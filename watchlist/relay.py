"""The backup way to watch together: a public message relay, end-to-end encrypted.

When none of Syncplay's servers works, the host's app runs the party itself (partyserver.py)
and the guests reach it through one of a few free public MQTT brokers (HiveMQ, EMQX,
Mosquitto). Everything goes through in a private channel, encrypted with a key that only
exists in the invite: the relay passes on bytes it can't read (what you watch, magnets,
names), and nobody can send into the party without the invite.

Encryption is HMAC-SHA256 used as a stream cipher (a keystream from the key, a random
nonce and a counter) with an HMAC-SHA256 tag over all of it - nothing outside Python's own
library is needed, on a phone either.

Anyone watching the relay sees the sealed messages go by and could send them again, so
inside the seal each one says who sent it, who it's for, when, and its number from that
sender: one numbered at or below the last heard from that sender (or from long ago, or for
someone else) is turned away. A message without these (from an older app) is turned away
too - it could be anyone's, sent again.

Everyone in a party has its invite, so the invite's key alone would let one guest read
another's messages or speak as them. So each guest also agrees a key of its own with the host
(Diffie-Hellman, RFC 3526's 2048-bit group: only Python's own big numbers): the host's half
is checked against a fingerprint in the invite, the guest's is new every time. What a guest
and the host say to each other is sealed with that key too, inside the party's.
"""

import base64
import hashlib
import hmac
import json
import os
import queue
import secrets
import socket
import ssl
import struct
import threading
import time

BROKERS = [("broker.hivemq.com", 8883), ("broker.emqx.io", 8883), ("test.mosquitto.org", 8886)]
ROOM = "relay"                 # the room on the host's own server
MAX_PACKET = 2 * 1024 * 1024   # the biggest packet taken from the relay (bigger ones are passed over)
MAX_MESSAGE = 96 * 1024        # the biggest message taken from a guest
MAX_GUESTS = 32                # guests at once through the relay
REPLAY_WINDOW = 3600.0         # seconds: a message sent longer ago than this (or this far ahead) is old
IDLE = 60.0                    # a guest hearing nothing from the party this long has lost it


# RFC 3526, group 14 (2048 bits), generator 2.
_P = int("FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A08798E3404DD"
         "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7EDEE"
         "386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F83655D"
         "23DCA3AD961C62F356208552BB9ED529077096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3BE39E772C18"
         "0E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF6955817183995497CEA956AE515D2261898FA051015728E5A8AACAA"
         "68FFFFFFFFFFFFFFFF", 16)
_G = 2


def _half():
    """(private, public) for one side of a key agreement."""
    private = secrets.randbits(256) | (1 << 255)
    return private, pow(_G, private, _P)


def _agreed(private, theirs):
    """The key both sides get from one's private half and the other's public one (None for a
    public half that isn't one)."""
    if not isinstance(theirs, int) or not 1 < theirs < _P - 1:
        return None
    return hashlib.sha256(pow(theirs, private, _P).to_bytes(256, "big")).digest()


def fingerprint(public):
    """The host's public half, shortly (in the invite: a guest checks the host's is that one)."""
    return hashlib.sha256(public.to_bytes(256, "big")).digest()[:10]


def _inner(box, blob):
    """A message sealed again with a guest's own key (inside the party's), or None."""
    try:
        data = box.open(base64.b64decode(blob))
    except (ValueError, TypeError):
        return None
    return _envelope(data) if data is not None else None


def _envelope(data):
    """A message as sealed (a dict, with its number and time), or None if it isn't one."""
    try:
        env = json.loads(data.decode("utf-8"))
    except (ValueError, RecursionError):
        return None
    if not isinstance(env, dict):
        return None
    n, t = env.get("n"), env.get("t")
    if isinstance(n, bool) or not isinstance(n, int) or isinstance(t, bool) or not isinstance(t, (int, float)):
        return None                    # (an older app's: it could be anyone's, sent again)
    if abs(time.time() - t) > REPLAY_WINDOW:
        return None
    return env


class _Numbered:
    """Each sender's messages numbered: one at or below the last heard from it is a replay."""

    def __init__(self):
        self.last, self.lock = {}, threading.Lock()

    def fresh(self, sender, n):
        with self.lock:
            if n <= self.last.get(sender, 0):
                return False
            self.last[sender] = n
            return True


# ---- encryption

class Box:
    def __init__(self, secret: bytes):
        self.enc = hmac.new(secret, b"watchanything enc", hashlib.sha256).digest()
        self.mac = hmac.new(secret, b"watchanything mac", hashlib.sha256).digest()
        self.topic = "watchanything/" + hmac.new(secret, b"watchanything topic", hashlib.sha256).hexdigest()[:32]

    def _stream(self, nonce, n):
        out, counter = bytearray(), 0
        while len(out) < n:
            out += hmac.new(self.enc, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
            counter += 1
        return bytes(out[:n])

    def seal(self, data: bytes) -> bytes:
        nonce = os.urandom(16)
        body = bytes(a ^ b for a, b in zip(data, self._stream(nonce, len(data))))
        tag = hmac.new(self.mac, nonce + body, hashlib.sha256).digest()[:16]
        return nonce + tag + body

    def open(self, blob: bytes):
        """The message, or None when it isn't one of this party's (or was changed)."""
        if len(blob) < 32:
            return None
        nonce, tag, body = blob[:16], blob[16:32], blob[32:]
        if not hmac.compare_digest(tag, hmac.new(self.mac, nonce + body, hashlib.sha256).digest()[:16]):
            return None
        return bytes(a ^ b for a, b in zip(body, self._stream(nonce, len(body))))


# ---- MQTT 3.1.1 (just what's needed: connect, subscribe, publish, keep alive)

def _varint(n):
    out = bytearray()
    while True:
        byte, n = n % 128, n // 128
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _string(s):
    data = s.encode("utf-8") if isinstance(s, str) else s
    return struct.pack(">H", len(data)) + data


class Mqtt:
    """A connection to a broker; on_message(topic, payload) for what arrives on `topics`."""

    def __init__(self, broker, topics, on_message, on_lost=None, timeout=10):
        self.broker, self.topics, self.on_message, self.on_lost = broker, list(topics), on_message, on_lost
        self.timeout = timeout
        self.sock, self.lock, self.alive, self._pid = None, threading.Lock(), True, 0
        self.acked = threading.Event()      # (the relay took a message: PUBACK)
        self._connect()
        threading.Thread(target=self._read_loop, daemon=True).start()
        threading.Thread(target=self._keepalive, daemon=True).start()

    def _connect(self):
        from .metadata import ssl_context
        host, port = self.broker
        raw = socket.create_connection((host, port), timeout=self.timeout)
        try:
            context = ssl_context()
        except Exception:  # noqa: BLE001
            context = ssl.create_default_context()
        sock = context.wrap_socket(raw, server_hostname=host)
        sock.settimeout(self.timeout)
        client_id = "wa-" + secrets.token_hex(8)
        body = _string("MQTT") + bytes([4, 0x02]) + struct.pack(">H", 30) + _string(client_id)
        sock.sendall(bytes([0x10]) + _varint(len(body)) + body)
        kind, payload = self._packet(sock)
        if kind != 0x20 or len(payload) < 2 or payload[1] != 0:
            raise OSError(f"the relay {host} refused the connection")
        self.sock = sock
        for topic in self.topics:
            self._subscribe(topic)
        sock.settimeout(None)

    @staticmethod
    def _recv(sock, n):
        data = b""
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk:
                raise OSError("the relay closed the connection")
            data += chunk
        return data

    def _packet(self, sock=None):
        sock = sock or self.sock
        first = self._recv(sock, 1)[0]
        length, mult = 0, 1
        for _ in range(4):
            byte = self._recv(sock, 1)[0]
            length += (byte & 0x7F) * mult
            if not byte & 0x80:
                break
            mult *= 128
        else:
            raise OSError("the relay sent something that isn't MQTT")
        if length > MAX_PACKET:
            # (too big for anything the party sends: read past, not kept)
            while length:
                length -= len(self._recv(sock, min(length, 65536)))
            return None, b""
        return first & 0xF0, (first, self._recv(sock, length)) if first & 0xF0 == 0x30 else self._recv(sock, length)

    def _send(self, data):
        with self.lock:
            self.sock.sendall(data)

    def _next_pid(self):
        self._pid = self._pid % 65535 + 1
        return self._pid

    def _subscribe(self, topic):
        body = struct.pack(">H", self._next_pid()) + _string(topic) + bytes([1])
        self.sock.sendall(bytes([0x82]) + _varint(len(body)) + body)

    def publish(self, topic, payload: bytes, retain=False):
        """`retain`: the relay keeps it and hands it to whoever asks later (the latest one)."""
        body = _string(topic) + struct.pack(">H", self._next_pid()) + payload
        self._send(bytes([0x32 | (1 if retain else 0)]) + _varint(len(body)) + body)   # (QoS 1: the relay keeps it till it's taken)

    def _read_loop(self):
        while self.alive:
            try:
                kind, payload = self._packet()
            except (OSError, ssl.SSLError):
                if not self.alive:
                    return
                if not self._reconnect():
                    self.alive = False
                    if self.on_lost:
                        self.on_lost()
                    return
                continue
            if kind == 0x40:
                self.acked.set()
            if kind == 0x30:
                first, data = payload
                if len(data) < 2:
                    continue
                tlen = struct.unpack(">H", data[:2])[0]
                topic = data[2:2 + tlen].decode("utf-8", "replace")
                rest = data[2 + tlen:]
                qos = (first >> 1) & 3
                if qos:
                    pid, rest = rest[:2], rest[2:]
                    try:
                        self._send(bytes([0x40, 0x02]) + pid)
                    except OSError:
                        pass
                try:
                    self.on_message(topic, rest)
                except Exception:  # noqa: BLE001 - one bad message isn't the connection's end
                    pass

    def _reconnect(self):
        for wait in (1, 2, 4, 8, 15):
            time.sleep(wait)
            if not self.alive:
                return False
            try:
                self._connect()
                return True
            except (OSError, ssl.SSLError):
                continue
        return False

    def _keepalive(self):
        while self.alive:
            time.sleep(20)
            try:
                self._send(bytes([0xC0, 0x00]))
            except (OSError, AttributeError):
                pass

    def close(self):
        self.alive = False
        try:
            self._send(bytes([0xE0, 0x00]))
            self.sock.close()
        except (OSError, AttributeError):
            pass


# ---- the host: guests arriving through the relay, each a client of its own server

class _Through:
    """One guest's connection, as the host's server sees it."""

    def __init__(self, hub, sid):
        self.hub, self.sid, self.inbox = hub, sid, queue.Queue()

    def send(self, message):
        self.hub.to_guest(self.sid, message)

    def read(self):
        return self.inbox.get()

    def close(self):
        self.inbox.put(None)
        if self.hub.guests.get(self.sid) is self:
            self.hub.guests.pop(self.sid, None)      # (gone from the server: forgotten here too)


class Hub:
    """The host's end of the relay: messages from guests go to its party server."""

    def __init__(self, secret, server, broker_index):
        self.box, self.server, self.guests = Box(secret), server, {}
        self.heard = _Numbered()         # each guest's last message (kept after it leaves: no replays)
        self._sent, self._send_lock = {}, threading.Lock()
        self._private, self.public = _half()
        self.fingerprint = fingerprint(self.public)
        self.keys = {}                   # guest -> (its public half, the box of the key agreed with it)
        self.mqtt = Mqtt(BROKERS[broker_index], [self.box.topic + "/h"], self._arrived)

    def _arrived(self, topic, blob):
        if len(blob) > MAX_MESSAGE:
            return
        data = self.box.open(blob)
        if data is None:
            return
        try:
            env = json.loads(data.decode("utf-8"))
        except (ValueError, RecursionError):
            return
        if not isinstance(env, dict) or "g" in env:
            return                       # (not a guest's: one of ours, sent back in)
        sid = env.get("s")
        if not isinstance(sid, str) or not sid or len(sid) > 20:
            return
        if "hi" in env:
            self._hello(sid, env)
            return
        # (everything else sealed again with the key agreed with that guest: no one else can say it)
        got = self.keys.get(sid)
        env = _inner(got[1], env.get("x")) if got and isinstance(env.get("x"), str) else None
        if env is None or not self.heard.fresh(sid, env["n"]):
            return
        conn = self.guests.get(sid)
        if env.get("bye"):
            if conn:
                conn.close()
                self.guests.pop(sid, None)
            return
        if not isinstance(env.get("m"), dict):
            return
        if conn is None:
            if len(self.guests) >= MAX_GUESTS:
                return
            conn = self.guests[sid] = _Through(self, sid)
            self.server.serve(conn)
        conn.inbox.put(env["m"])

    def _hello(self, sid, env):
        """A guest arriving: its half of a key of its own; the host's half back (to the guest only)."""
        if _envelope(json.dumps(env).encode("utf-8")) is None or not isinstance(env.get("hi"), str):
            return
        try:
            theirs = int(env["hi"], 16)
        except ValueError:
            return
        known = self.keys.get(sid)
        if known and known[0] != theirs:
            return                       # (that guest has a key already: not another's to take over)
        if not known:
            if len(self.keys) >= MAX_GUESTS * 4:
                return
            key = _agreed(self._private, theirs)
            if key is None:
                return
            self.keys[sid] = (theirs, Box(key))
        with self._send_lock:
            n = self._sent[sid] = self._sent.get(sid, 0) + 1
            blob = self.box.seal(json.dumps({"g": sid, "n": n, "t": round(time.time(), 3),
                                             "key": format(self.public, "x")}).encode("utf-8"))
            self.mqtt.publish(f"{self.box.topic}/g/{sid}", blob)

    def to_guest(self, sid, message):
        got = self.keys.get(sid)
        if not got:
            return
        with self._send_lock:            # (numbered in the order they go)
            n = self._sent[sid] = self._sent.get(sid, 0) + 1
            inner = got[1].seal(json.dumps({"n": n, "t": round(time.time(), 3), "m": message}).encode("utf-8"))
            blob = self.box.seal(json.dumps({"g": sid, "x": base64.b64encode(inner).decode("ascii")}).encode("utf-8"))
            self.mqtt.publish(f"{self.box.topic}/g/{sid}", blob)

    def close(self):
        for conn in list(self.guests.values()):
            conn.close()
        self.mqtt.close()


def host(server, secret=None):
    """The relay for a party on `server` (a partyserver.PartyServer): (hub, secret, broker
    index) on the first broker that works. Raises OSError if none does."""
    secret = secret or secrets.token_bytes(10)
    problems = []
    for index in range(len(BROKERS)):
        try:
            return Hub(secret, server, index), secret, index
        except (OSError, ssl.SSLError) as e:
            problems.append(f"{BROKERS[index][0]}: {e}")
    raise OSError("none of the backup relays could be reached (" + "; ".join(problems) + ")")


# ---- a guest: the relay as its connection to the party (what party.Connection is otherwise)

class _NoSocket:
    def settimeout(self, _):
        pass


class Connection:
    """A guest's way into a party through the relay: send(dict), read() -> dict or None."""

    def __init__(self, secret, broker_index, host_print=b"", timeout=10):
        """`host_print`: the host's fingerprint from the invite (its key is checked against it)."""
        if not host_print:
            raise OSError("that invite is from an older Watch Anything - ask for a new one")
        self.box, self.sid = Box(secret), secrets.token_hex(6)
        self.inbox, self.tls, self.sock = queue.Queue(), True, _NoSocket()
        self.heard = _Numbered()
        self._n, self._send_lock = 0, threading.Lock()
        self._print, self.own = bytes(host_print), None
        self._private, self._public = _half()
        self._keyed = threading.Event()
        self.mqtt = Mqtt(BROKERS[broker_index], [f"{self.box.topic}/g/{self.sid}"], self._arrived,
                         on_lost=lambda: self.inbox.put(None), timeout=timeout)
        # A key of its own with the host first (asked again now and then, until the host answers).
        end = time.time() + timeout
        while not self._keyed.is_set() and time.time() < end:
            with self._send_lock:
                self._n += 1
                self.mqtt.publish(self.box.topic + "/h", self.box.seal(json.dumps(
                    {"s": self.sid, "n": self._n, "t": round(time.time(), 3), "hi": format(self._public, "x")}).encode("utf-8")))
            self._keyed.wait(min(2.0, max(0.1, end - time.time())))
        if not self._keyed.is_set():
            self.mqtt.close()
            raise OSError("the party's host didn't answer through the relay")

    def _arrived(self, topic, blob):
        if len(blob) > MAX_PACKET:
            return
        data = self.box.open(blob)
        if data is None:
            return
        try:
            env = json.loads(data.decode("utf-8"))
        except (ValueError, RecursionError):
            return
        if not isinstance(env, dict) or env.get("g") != self.sid:
            return                       # (another guest's)
        if "key" in env:
            # (the host's half: only the one the invite's fingerprint is of)
            if self.own is None and _envelope(data) is not None and isinstance(env["key"], str):
                try:
                    theirs = int(env["key"], 16)
                except ValueError:
                    return
                if 1 < theirs < _P - 1 and hmac.compare_digest(fingerprint(theirs), self._print):
                    key = _agreed(self._private, theirs)
                    if key:
                        self.own = Box(key)
                        self._keyed.set()
            return
        # (the host's, sealed with this guest's own key - not an old one sent again)
        env = _inner(self.own, env.get("x")) if self.own and isinstance(env.get("x"), str) else None
        if env is None or not self.heard.fresh("host", env["n"]):
            return
        if isinstance(env.get("m"), dict):
            self.inbox.put(env["m"])

    def _seal(self, **what):
        self._n += 1
        inner = self.own.seal(json.dumps(dict(what, n=self._n, t=round(time.time(), 3))).encode("utf-8"))
        return self.box.seal(json.dumps({"s": self.sid, "x": base64.b64encode(inner).decode("ascii")}).encode("utf-8"))

    def send(self, message):
        with self._send_lock:            # (numbered in the order they go)
            self.mqtt.publish(self.box.topic + "/h", self._seal(m=message))

    def read(self):
        """The next message; None when the relay's lost; OSError when the party's said nothing
        for a minute (the host's server talks every second: it's gone)."""
        try:
            return self.inbox.get(timeout=IDLE)
        except queue.Empty:
            raise socket.timeout("nothing heard from the watch party") from None

    def close(self):
        try:
            with self._send_lock:
                self.mqtt.publish(self.box.topic + "/h", self._seal(bye=True))
        except OSError:
            pass
        self.mqtt.close()
        self.inbox.put(None)


def server_name(secret: bytes, broker_index: int, host_print: bytes = b""):
    """How a relay party is named where a server's address would be ("relay:1:<key>:<host's fingerprint>")."""
    return f"relay:{broker_index}:{secret.hex()}:{bytes(host_print).hex()}"


def parse(server):
    """(secret, broker index, host's fingerprint) of a relay party's name, or None."""
    if not str(server).startswith("relay:"):
        return None
    try:
        _, index, key, *rest = server.split(":")
        index = int(index)
        if not 0 <= index < len(BROKERS):
            return None
        return bytes.fromhex(key), index, bytes.fromhex(rest[0] if rest else "")
    except ValueError:
        return None
