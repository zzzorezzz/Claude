"""A watch-party server inside the host's app, for when none of Syncplay's servers works.

It speaks Syncplay's protocol (as party.py does, the client side), so the party works the
same whichever server it's on: who's in the room, what each is playing, chat, and keeping
everyone at the same place (a pause or seek by one goes to the others; the room's position
is sent to each about every second, with the timing each needs to allow for its lag).

Guests reach it one of two ways:
- through a public relay (relay.py), encrypted - nothing to set up, works from anywhere;
- straight over the internet to the host's computer (the host's app as the server): a port
  is opened on the host's router when the router allows it (UPnP), else it only works for
  people on the same network.
"""

import atexit
import json
import math
import queue
import socket
import threading
import time
import urllib.parse
import urllib.request
import uuid

STATE_EVERY = 1.0
# Anyone can reach a direct party's port, so what comes in is checked and kept small.
MAX_LINE = 64 * 1024             # the longest message taken from a guest (a picture is 12 KB)
MAX_CLIENTS = 32                 # connections at once (more are turned away)
MAX_QUEUED = 200                 # messages waiting for one guest before it's dropped (it isn't taking them)
SEND_TIMEOUT = 10.0              # a guest straight to the host that takes nothing this long is dropped
DROP_AFTER = 45.0                # a guest not heard from this long has gone (apps answer every second)
MAX_POSITION = 1e7               # seconds: further into a video than this is a made-up place


def _number(x):
    """`x` as a real, finite number, or None (what a message says isn't trusted to be one)."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    x = float(x)
    return x if math.isfinite(x) else None


def _count(x):
    """`x` as a small whole number (Syncplay's counts of changes in flight), or None."""
    return x if isinstance(x, int) and not isinstance(x, bool) and 0 <= x < 1 << 31 else None


class LineSocket:
    """JSON lines over a socket (a guest straight to the host, or the host's own app). A
    line that's too long or isn't a message is ValueError: that connection is dropped."""

    def __init__(self, sock):
        self.sock, self._buf, self._lock = sock, b"", threading.Lock()
        self.tls = False
        self.strict = True               # (anyone could be at the other end: a bad message cuts it off)
        self.closed = False
        # (a send that waits this long is a guest that's stopped taking them; reading just tries again)
        sock.settimeout(SEND_TIMEOUT)

    def send(self, message):
        data = json.dumps(message).encode("utf-8") + b"\r\n"
        with self._lock:
            self.sock.sendall(data)

    def read(self):
        while True:
            if b"\n" in self._buf:
                line, self._buf = self._buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                if len(line) > MAX_LINE:
                    raise ValueError("a message too long")
                msg = json.loads(line.decode("utf-8"))
                if not isinstance(msg, dict):
                    raise ValueError("not a message")
                return msg
            if len(self._buf) > MAX_LINE:
                raise ValueError("a message too long")
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                if self.closed:
                    return None
                continue                 # (quiet for a while: whether it's gone is the server's to say)
            if not chunk:
                return None
            self._buf += chunk

    def close(self):
        self.closed = True
        for step in (lambda: self.sock.shutdown(socket.SHUT_RDWR), self.sock.close):
            try:
                step()
            except OSError:
                pass


class _Client:
    def __init__(self, conn):
        self.conn, self.name, self.room = conn, "", ""
        self.file = None
        self.server_ignoring = 0       # changes sent it that it hasn't acknowledged yet
        self.client_latency = None     # (its own clock, when it came) - echoed back so it can tell its lag
        self.rtt = 0.0
        self.alive = True
        self.came = self.heard = time.time()
        # What's sent to it waits here and goes in a thread of its own: a guest whose connection
        # stalls holds up only itself (and is dropped once too much waits), never the room.
        self._outbox = queue.Queue()
        threading.Thread(target=self._write, daemon=True).start()

    def send(self, message):
        if not self.alive:
            return
        if self._outbox.qsize() >= MAX_QUEUED:
            self.close()
            return
        self._outbox.put(message)

    def _write(self):
        while True:
            message = self._outbox.get()
            if message is None or not self.alive:
                return
            try:
                self.conn.send(message)
            except (OSError, ValueError, TypeError):
                self.close()
                return

    def close(self):
        """Its connection closed (its reader then takes it out of the room)."""
        self.alive = False
        self._outbox.put(None)
        try:
            self.conn.close()
        except OSError:
            pass


class _Room:
    def __init__(self):
        self.position, self.paused, self.set_by, self.at = 0.0, True, "", time.time()

    def now(self):
        return self.position + (0 if self.paused else time.time() - self.at)


class PartyServer:
    """Rooms of clients, each a connection with send(dict) / read() / close()."""

    def __init__(self):
        self.clients, self.rooms = [], {}
        self.lock = threading.RLock()
        self.running = True
        self.listener = None
        self.port = 0
        threading.Thread(target=self._tick, daemon=True).start()

    # ---- connections

    def listen(self, port=0, host="0.0.0.0"):
        """Take connections on a port (the host's app, and guests straight to it). The port."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            sock.bind((host, 0))               # (that port is taken: any other)
        sock.listen(16)
        self.listener, self.port = sock, sock.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()
        return self.port

    def _accept(self):
        while self.running:
            try:
                sock, _ = self.listener.accept()
            except OSError:
                return
            if len(self.clients) >= MAX_CLIENTS:
                # (more connections than any party has: someone knocking, not a guest)
                LineSocket(sock).close()
                continue
            try:
                self.serve(LineSocket(sock))
            except OSError:
                pass

    def serve(self, conn):
        """A new client (its messages read in a thread of its own)."""
        client = _Client(conn)
        with self.lock:
            self.clients.append(client)
        threading.Thread(target=self._read, args=(client,), daemon=True).start()
        return client

    def stop(self):
        was_running, self.running = self.running, False
        if self.listener:
            try:
                self.listener.close()
            except OSError:
                pass
        with self.lock:
            clients = list(self.clients)
            self.clients.clear()
        for c in clients:
            c.close()
        if was_running and self.port:
            close_port(self.port)            # (the router's port, if it was opened for this party)

    def _read(self, client):
        strict = getattr(client.conn, "strict", False)
        try:
            while self.running and client.alive:
                msg = client.conn.read()
                if msg is None:
                    break
                client.heard = time.time()
                try:
                    if not isinstance(msg, dict):
                        raise ValueError("not a message")
                    for command, value in msg.items():
                        with self.lock:
                            self._handle(client, command, value)
                except (ValueError, TypeError, AttributeError, KeyError):
                    # A message that isn't one: dropped. Straight to the host (anyone could be
                    # there) the connection goes with it; through the relay only the invited are.
                    if strict:
                        break
        except (OSError, ValueError, RecursionError):
            pass
        self._drop(client)

    def _drop(self, client):
        """A client gone: out of the room, the others told."""
        with self.lock:
            if client in self.clients:
                self.clients.remove(client)
            client.alive = False
            if client.name:
                self._others(client, {"Set": {"user": {client.name: {"room": {"name": client.room}, "event": {"left": True}}}}})
                client.name = ""             # (told once)
                if not any(c.room == client.room and c.name for c in self.clients):
                    self.rooms.pop(client.room, None)    # (nobody's in it any more)
        client.close()

    def _others(self, client, message, room=None):
        room = room or client.room
        for c in self.clients:
            if c is not client and c.room == room and c.name:
                c.send(message)

    # ---- Syncplay's messages

    def _handle(self, client, command, value):
        """One of Syncplay's messages from `client`. ValueError when it isn't what that message
        is (dropped); anything else not known is passed over, as Syncplay's servers do."""
        if command == "TLS":
            client.send({"TLS": {"startTLS": "false"}})        # (encrypted by the relay, or on your network)
            return
        if command not in ("Hello", "List", "Set", "Chat", "State"):
            return
        if command == "Hello" and client.name:
            return                                             # (once: a second one doesn't move it)
        if command != "Hello" and not client.name:
            return                                             # (not in a room yet)
        if command in ("Hello", "Set", "State") and not isinstance(value, dict):
            raise ValueError(f"a strange {command}")
        if command == "Hello":
            room = value.get("room") or {}
            if not isinstance(room, dict):
                raise ValueError("a strange room")
            name = str(value.get("username") or "Watcher")[:40]
            room = str(room.get("name") or "party")[:80]
            taken = {c.name for c in self.clients if c.room == room and c is not client}
            base, n = name, 2
            while name in taken:
                name, n = f"{base}_{n}", n + 1
            client.name, client.room = name, room
            self.rooms.setdefault(room, _Room())
            client.send({"Hello": {"username": name, "room": {"name": room}, "version": "1.2.255",
                                   "realversion": "1.7.7", "motd": "",
                                   "features": {"chat": True, "readiness": True, "managedRooms": False,
                                                "isolateRooms": True}}})
            self._others(client, {"Set": {"user": {name: {"room": {"name": room}, "event": {"joined": True}}}}})
            self._state(client)
        elif command == "List":
            people = {c.name: {"position": 0, "file": c.file or {}, "controller": False, "isReady": True, "features": {}}
                      for c in self.clients if c.room == client.room and c.name}
            client.send({"List": {client.room: people}})
        elif command == "Set":
            if "file" in value:
                file_ = value["file"]
                if file_ is not None and not isinstance(file_, dict):
                    raise ValueError("a strange file")
                if file_ is not None and file_.get("watchlist") is not None and not isinstance(file_["watchlist"], dict):
                    raise ValueError("a strange file")
                client.file = file_
                self._others(client, {"Set": {"user": {client.name: {"room": {"name": client.room},
                                                                      "file": client.file}}}})
        elif command == "Chat":
            text = value if isinstance(value, str) else (value.get("message") if isinstance(value, dict) else None)
            if not isinstance(text, str):
                raise ValueError("a strange chat message")
            for c in self.clients:
                if c.room == client.room and c.name:
                    c.send({"Chat": {"username": client.name, "message": text[:2000]}})
        elif command == "State":
            self._client_state(client, value)

    def _client_state(self, client, state):
        ping = state.get("ping") or {}
        ignore = state.get("ignoringOnTheFly") or {}
        playstate = state.get("playstate")
        if not isinstance(ping, dict) or not isinstance(ignore, dict) or not isinstance(playstate, (dict, type(None))):
            raise ValueError("a strange state")
        for key in ("server", "client"):
            if key in ignore and _count(ignore[key]) is None:
                raise ValueError("a strange state")
        position = None
        if playstate is not None:
            position = _number(playstate.get("position") or 0.0)
            if position is None or not 0 <= position < MAX_POSITION \
                    or not isinstance(playstate.get("paused"), (bool, type(None))):
                raise ValueError("a strange place in the video")
        if "clientLatencyCalculation" in ping:
            sent = _number(ping["clientLatencyCalculation"])
            client.client_latency = (sent, time.time()) if sent is not None else None
        sent = _number(ping.get("latencyCalculation"))
        if sent:
            client.rtt = min(max(0.0, time.time() - sent), 60.0)
        if "server" in ignore and ignore["server"] == client.server_ignoring:
            client.server_ignoring = 0             # (it has the last change we sent it)
        if "client" in ignore and playstate is None:
            # (a change it's still waiting to hear back about, without its place: heard, as
            # Syncplay's servers answer - else it would wait for ever)
            client.send({"State": {"ignoringOnTheFly": {"client": ignore["client"]}, "ping": self._ping(client)}})
        if "client" in ignore and playstate is not None:
            # A change it made (pause, play, seek): the room's, and everyone else's now.
            room = self.rooms.setdefault(client.room, _Room())
            room.position = position + client.rtt / 2
            room.paused = bool(playstate.get("paused"))
            room.set_by, room.at = client.name, time.time()
            seek = bool(playstate.get("doSeek"))
            client.send({"State": {"ignoringOnTheFly": {"client": ignore["client"]},
                                   "ping": self._ping(client)}})
            for c in self.clients:
                if c is not client and c.room == client.room and c.name:
                    c.server_ignoring += 1
                    c.send({"State": {"playstate": {"position": room.now(), "paused": room.paused,
                                                    "doSeek": seek, "setBy": client.name},
                                      "ping": self._ping(c),
                                      "ignoringOnTheFly": {"server": c.server_ignoring}}})

    def _ping(self, client):
        ping = {"latencyCalculation": time.time(), "serverRtt": client.rtt}
        if client.client_latency is not None:
            # Its clock, moved on by as long as it waited here: its round trip is the network's only.
            sent, got = client.client_latency
            ping["clientLatencyCalculation"] = sent + (time.time() - got)
        return ping

    def _state(self, client):
        room = self.rooms.get(client.room) or _Room()
        state = {"ping": self._ping(client)}
        if not client.server_ignoring:
            state["playstate"] = {"position": room.now(), "paused": room.paused, "doSeek": False, "setBy": room.set_by}
        client.send({"State": state})

    def _tick(self):
        while self.running:
            time.sleep(STATE_EVERY)
            gone = []
            with self.lock:
                now = time.time()
                for c in list(self.clients):
                    # Not heard from (its app closed without a word, its network gone), or never
                    # joined a room: it's taken out, so nobody waits for it.
                    if now - c.heard > DROP_AFTER or (not c.name and now - c.came > DROP_AFTER):
                        gone.append(c)
                        continue
                    if c.name:
                        try:
                            self._state(c)
                        except Exception:  # noqa: BLE001 - one client isn't the room's end
                            gone.append(c)
            for c in gone:
                self._drop(c)


# ---- the host's computer, reachable from the internet (the host's app as the server)

def local_address():
    """This computer's address on its network ("192.168.1.20")."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def public_address():
    """This network's address on the internet, or ""."""
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip", "https://icanhazip.com"):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "curl/8"}), timeout=6) as r:
                ip = r.read(64).decode().strip()
            if ip.count(".") == 3:
                return ip
        except Exception:  # noqa: BLE001
            continue
    return ""


_opened = {}                     # port -> (control URL, service) of the router that opened it
_opened_lock = threading.Lock()


def _gateways(timeout):
    """The routers on this network that can open ports (UPnP): [(control URL, service)]."""
    import re
    msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\nMX: 2\r\n"
           "ST: urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n\r\n").encode()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    locations = []
    try:
        s.sendto(msg, ("239.255.255.250", 1900))
        end = time.time() + timeout
        while time.time() < end:
            try:
                data, _ = s.recvfrom(4096)
            except socket.timeout:
                break
            m = re.search(rb"(?im)^location:\s*(\S+)", data)
            if m and m.group(1).decode() not in locations:
                locations.append(m.group(1).decode())
    except OSError:
        return []
    finally:
        s.close()
    found = []
    for loc in locations:
        try:
            with urllib.request.urlopen(loc, timeout=timeout) as r:
                desc = r.read(1 << 20).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - another device, or it didn't answer
            continue
        for service in ("WANIPConnection:1", "WANIPConnection:2", "WANPPPConnection:1"):
            m = re.search(r"<serviceType>urn:schemas-upnp-org:service:" + re.escape(service)
                          + r"</serviceType>.*?<controlURL>([^<]+)</controlURL>", desc, re.S)
            if m:
                found.append((urllib.parse.urljoin(loc, m.group(1)), "urn:schemas-upnp-org:service:" + service))
    return found


def _soap(control, kind, action, args, timeout):
    """Ask a router to do `action` ([(name, value)]). True if it did; it saying no is an error."""
    fields = "".join(f"<{k}>{v}</{k}>" for k, v in args)
    body = (f'<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            f's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            f'<u:{action} xmlns:u="{kind}">{fields}</u:{action}></s:Body></s:Envelope>').encode()
    req = urllib.request.Request(control, data=body, headers={
        "Content-Type": 'text/xml; charset="utf-8"', "SOAPAction": f'"{kind}#{action}"'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status == 200


def open_port(port, timeout=4):
    """Ask the router to send connections on `port` to this computer (UPnP). True if it did.
    It's closed again by close_port() - the party's server does that when it stops, and
    whatever's still open is closed as the app quits."""
    me = local_address()
    for control, kind in _gateways(timeout):
        try:
            if _soap(control, kind, "AddPortMapping", [
                    ("NewRemoteHost", ""), ("NewExternalPort", port), ("NewProtocol", "TCP"),
                    ("NewInternalPort", port), ("NewInternalClient", me), ("NewEnabled", 1),
                    ("NewPortMappingDescription", "Watch Anything party"), ("NewLeaseDuration", 0)], timeout):
                with _opened_lock:
                    _opened[port] = (control, kind)
                return True
        except Exception:  # noqa: BLE001 - another router, or it said no
            continue
    return False


def close_port(port, timeout=3):
    """Ask the router to close a port open_port() opened. True if it did (False for a port
    it didn't open)."""
    with _opened_lock:
        where = _opened.pop(port, None)
    if not where:
        return False
    try:
        return _soap(*where, "DeletePortMapping", [("NewRemoteHost", ""), ("NewExternalPort", port),
                                                   ("NewProtocol", "TCP")], timeout)
    except Exception:  # noqa: BLE001 - the router's gone, or it closed it itself
        return False


@atexit.register
def close_ports():
    """Every port this app opened on the router, closed (as the app quits)."""
    for port in list(_opened):
        close_port(port, timeout=2)


def reachable_from_outside(address, port, timeout=5):
    """Whether the port answers at the internet address (as a guest would reach it). Many
    routers can't connect to their own outside address, so False isn't always the answer
    for others - but True is."""
    try:
        with socket.create_connection((address, port), timeout=timeout):
            return True
    except OSError:
        return False


def new_id():
    return uuid.uuid4().hex[:8]

