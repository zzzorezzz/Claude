"""The backup way to watch together: a public message relay, end-to-end encrypted.

When none of Syncplay's servers works, the host's app runs the party itself (partyserver.py)
and the guests reach it through one of a few free public MQTT brokers (HiveMQ, EMQX,
Mosquitto). Everything goes through in a private channel, encrypted with a key that only
exists in the invite: the relay passes on bytes it can't read (what you watch, magnets,
names), and nobody can send into the party without the invite.

Encryption is HMAC-SHA256 used as a stream cipher (a keystream from the key, a random
nonce and a counter) with an HMAC-SHA256 tag over all of it - nothing outside Python's own
library is needed, on a phone either.
"""

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
        while True:
            byte = self._recv(sock, 1)[0]
            length += (byte & 0x7F) * mult
            if not byte & 0x80:
                break
            mult *= 128
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

    def publish(self, topic, payload: bytes):
        body = _string(topic) + struct.pack(">H", self._next_pid()) + payload
        self._send(bytes([0x32]) + _varint(len(body)) + body)          # (QoS 1: the relay keeps it till it's taken)

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
            if kind == 0x30:
                first, data = payload
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


class Hub:
    """The host's end of the relay: messages from guests go to its party server."""

    def __init__(self, secret, server, broker_index):
        self.box, self.server, self.guests = Box(secret), server, {}
        self.mqtt = Mqtt(BROKERS[broker_index], [self.box.topic + "/h"], self._arrived)

    def _arrived(self, topic, blob):
        data = self.box.open(blob)
        if data is None:
            return
        env = json.loads(data.decode("utf-8"))
        sid = str(env.get("s") or "")[:20]
        if not sid:
            return
        conn = self.guests.get(sid)
        if env.get("bye"):
            if conn:
                conn.close()
                self.guests.pop(sid, None)
            return
        if conn is None:
            conn = self.guests[sid] = _Through(self, sid)
            self.server.serve(conn)
        if isinstance(env.get("m"), dict):
            conn.inbox.put(env["m"])

    def to_guest(self, sid, message):
        blob = self.box.seal(json.dumps({"m": message}).encode("utf-8"))
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

    def __init__(self, secret, broker_index, timeout=10):
        self.box, self.sid = Box(secret), secrets.token_hex(6)
        self.inbox, self.tls, self.sock = queue.Queue(), True, _NoSocket()
        self.mqtt = Mqtt(BROKERS[broker_index], [f"{self.box.topic}/g/{self.sid}"], self._arrived,
                         on_lost=lambda: self.inbox.put(None), timeout=timeout)

    def _arrived(self, topic, blob):
        data = self.box.open(blob)
        if data is None:
            return
        env = json.loads(data.decode("utf-8"))
        if isinstance(env.get("m"), dict):
            self.inbox.put(env["m"])

    def send(self, message):
        blob = self.box.seal(json.dumps({"s": self.sid, "m": message}).encode("utf-8"))
        self.mqtt.publish(self.box.topic + "/h", blob)

    def read(self):
        return self.inbox.get()

    def close(self):
        try:
            self.mqtt.publish(self.box.topic + "/h", self.box.seal(json.dumps({"s": self.sid, "bye": True}).encode()))
        except OSError:
            pass
        self.mqtt.close()
        self.inbox.put(None)


def server_name(secret: bytes, broker_index: int):
    """How a relay party is named where a server's address would be ("relay:1:<key>")."""
    return f"relay:{broker_index}:{secret.hex()}"


def parse(server):
    """(secret, broker index) of a relay party's name, or None."""
    if not str(server).startswith("relay:"):
        return None
    try:
        _, index, key = server.split(":", 2)
        index = int(index)
        if not 0 <= index < len(BROKERS):
            return None
        return bytes.fromhex(key), index
    except ValueError:
        return None
