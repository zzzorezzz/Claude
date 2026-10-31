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

import json
import socket
import threading
import time
import urllib.parse
import urllib.request
import uuid

STATE_EVERY = 1.0


class LineSocket:
    """JSON lines over a socket (a guest straight to the host, or the host's own app)."""

    def __init__(self, sock):
        self.sock, self._buf, self._lock = sock, b"", threading.Lock()
        self.tls = False

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
                try:
                    return json.loads(line.decode("utf-8"))
                except ValueError:
                    continue
            chunk = self.sock.recv(65536)
            if not chunk:
                return None
            self._buf += chunk

    def close(self):
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

    def send(self, message):
        if not self.alive:
            return
        try:
            self.conn.send(message)
        except OSError:
            self.alive = False


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
            self.serve(LineSocket(sock))

    def serve(self, conn):
        """A new client (its messages read in a thread of its own)."""
        client = _Client(conn)
        with self.lock:
            self.clients.append(client)
        threading.Thread(target=self._read, args=(client,), daemon=True).start()
        return client

    def stop(self):
        self.running = False
        if self.listener:
            try:
                self.listener.close()
            except OSError:
                pass
        with self.lock:
            for c in list(self.clients):
                c.alive = False
                try:
                    c.conn.close()
                except OSError:
                    pass
            self.clients.clear()

    def _read(self, client):
        try:
            while self.running and client.alive:
                msg = client.conn.read()
                if msg is None:
                    break
                for command, value in msg.items():
                    with self.lock:
                        self._handle(client, command, value or {})
        except (OSError, ValueError):
            pass
        with self.lock:
            client.alive = False
            if client in self.clients:
                self.clients.remove(client)
            if client.name:
                self._others(client, {"Set": {"user": {client.name: {"room": {"name": client.room}, "event": {"left": True}}}}})

    def _others(self, client, message, room=None):
        room = room or client.room
        for c in self.clients:
            if c is not client and c.room == room and c.name:
                c.send(message)

    # ---- Syncplay's messages

    def _handle(self, client, command, value):
        if command == "TLS":
            client.send({"TLS": {"startTLS": "false"}})        # (encrypted by the relay, or on your network)
        elif command == "Hello":
            name = str(value.get("username") or "Watcher")[:40]
            room = str((value.get("room") or {}).get("name") or "party")[:80]
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
                client.file = value["file"]
                self._others(client, {"Set": {"user": {client.name: {"room": {"name": client.room},
                                                                      "file": client.file}}}})
        elif command == "Chat":
            text = value if isinstance(value, str) else str(value.get("message") or "")
            for c in self.clients:
                if c.room == client.room and c.name:
                    c.send({"Chat": {"username": client.name, "message": text}})
        elif command == "State":
            self._client_state(client, value)

    def _client_state(self, client, state):
        ping = state.get("ping") or {}
        if "clientLatencyCalculation" in ping:
            client.client_latency = (ping["clientLatencyCalculation"], time.time())
        if ping.get("latencyCalculation"):
            client.rtt = max(0.0, time.time() - float(ping["latencyCalculation"]))
        ignore = state.get("ignoringOnTheFly") or {}
        if "server" in ignore and ignore["server"] == client.server_ignoring:
            client.server_ignoring = 0             # (it has the last change we sent it)
        playstate = state.get("playstate")
        if "client" in ignore and playstate is not None:
            # A change it made (pause, play, seek): the room's, and everyone else's now.
            room = self.rooms.setdefault(client.room, _Room())
            room.position = float(playstate.get("position") or 0.0) + client.rtt / 2
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
            with self.lock:
                for c in list(self.clients):
                    if c.name:
                        self._state(c)


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


def open_port(port, timeout=4):
    """Ask the router to send connections on `port` to this computer (UPnP). True if it did."""
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
        return False
    finally:
        s.close()
    me = local_address()
    for loc in locations:
        try:
            with urllib.request.urlopen(loc, timeout=timeout) as r:
                desc = r.read().decode("utf-8", "replace")
            for service in ("WANIPConnection:1", "WANIPConnection:2", "WANPPPConnection:1"):
                m = re.search(r"<serviceType>urn:schemas-upnp-org:service:" + re.escape(service)
                              + r"</serviceType>.*?<controlURL>([^<]+)</controlURL>", desc, re.S)
                if not m:
                    continue
                control = urllib.parse.urljoin(loc, m.group(1))
                kind = "urn:schemas-upnp-org:service:" + service
                body = (f'<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
                        f's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
                        f'<u:AddPortMapping xmlns:u="{kind}"><NewRemoteHost></NewRemoteHost>'
                        f'<NewExternalPort>{port}</NewExternalPort><NewProtocol>TCP</NewProtocol>'
                        f'<NewInternalPort>{port}</NewInternalPort><NewInternalClient>{me}</NewInternalClient>'
                        f'<NewEnabled>1</NewEnabled><NewPortMappingDescription>Watch Anything party</NewPortMappingDescription>'
                        f'<NewLeaseDuration>0</NewLeaseDuration></u:AddPortMapping></s:Body></s:Envelope>').encode()
                req = urllib.request.Request(control, data=body, headers={
                    "Content-Type": 'text/xml; charset="utf-8"', "SOAPAction": f'"{kind}#AddPortMapping"'})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    if r.status == 200:
                        return True
        except Exception:  # noqa: BLE001 - another router, or it said no
            continue
    return False


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

