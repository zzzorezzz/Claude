"""A stand-in Syncplay server for tests, following the real one's rules
(syncplay/server.py and protocols.py): rooms, users joining and leaving, files,
the room's position being the slowest watcher's, forced updates when someone
pauses or skips, and "ignoringOnTheFly" while changes are in flight. It
doesn't isolate rooms, like the public servers, and says no to encryption.
"""

import json
import socket
import threading
import time

STATE_INTERVAL = 1.0


class Watcher:
    def __init__(self, server, sock, name):
        self.server, self.sock, self.name = server, sock, name
        self.room = None
        self.file = None
        self.position = None
        self.updated = time.time()
        self.server_ignoring = 0
        self.client_ignoring = 0
        self.client_latency = 0
        self.lock = threading.Lock()
        self.alive = True

    def send(self, msg):
        try:
            with self.lock:
                self.sock.sendall(json.dumps(msg).encode() + b"\r\n")
        except OSError:
            self.alive = False

    def get_position(self):
        if self.position is None:
            return None
        return self.position + (time.time() - self.updated if self.room.playing else 0)

    def send_state(self, position, paused, do_seek, set_by, forced=False):
        state = {"ping": {"latencyCalculation": time.time(), "serverRtt": 0},
                 "playstate": {"position": position or 0, "paused": paused, "doSeek": do_seek,
                               "setBy": set_by.name if set_by else None}}
        if self.client_latency:
            state["ping"]["clientLatencyCalculation"] = self.client_latency
            self.client_latency = 0
        if forced:
            self.server_ignoring += 1
        if self.server_ignoring or self.client_ignoring:
            state["ignoringOnTheFly"] = {}
            if self.server_ignoring:
                state["ignoringOnTheFly"]["server"] = self.server_ignoring
            if self.client_ignoring:
                state["ignoringOnTheFly"]["client"] = self.client_ignoring
                self.client_ignoring = 0
        if self.server_ignoring == 0 or forced:
            self.send({"State": state})


class Room:
    def __init__(self, name):
        self.name = name
        self.watchers = {}
        self.playing = False
        self.set_by = None
        self.position = 0
        self.last = time.time()

    def get_position(self):
        age = time.time() - self.last
        with_files = [w for w in self.watchers.values() if w.file and w.position is not None]
        if self.watchers and age > 1:
            if with_files:
                slowest = min(with_files, key=lambda w: w.get_position())
                self.set_by, self.position = slowest, slowest.get_position()
            self.last = time.time()
            return self.position
        return self.position + (age if self.playing else 0)


class FakeSyncplayServer:
    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.port = self.sock.getsockname()[1]
        self.rooms = {}
        self.lock = threading.RLock()
        self.log = []
        self.running = True
        threading.Thread(target=self._accept, daemon=True).start()
        threading.Thread(target=self._timer, daemon=True).start()

    @property
    def address(self):
        return f"127.0.0.1:{self.port}"

    def stop(self):
        self.running = False
        self.sock.close()

    def watchers(self):
        return [w for r in self.rooms.values() for w in r.watchers.values()]

    def _accept(self):
        while self.running:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._client, args=(conn,), daemon=True).start()

    def _timer(self):
        while self.running:
            time.sleep(STATE_INTERVAL)
            with self.lock:
                for w in self.watchers():
                    w.send_state(w.room.get_position(), not w.room.playing, False, w.room.set_by)

    def _broadcast(self, what):
        for w in self.watchers():
            what(w)

    def _client(self, conn):
        buf, watcher = b"", None
        try:
            while self.running:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while b"\r\n" in buf:
                    line, buf = buf.split(b"\r\n", 1)
                    msg = json.loads(line)
                    self.log.append(msg)
                    with self.lock:
                        watcher = self._handle(conn, watcher, msg) or watcher
        except (OSError, ValueError):
            pass
        with self.lock:
            if watcher and watcher.room:
                room = watcher.room
                room.watchers.pop(watcher.name, None)
                self._broadcast(lambda w: w.send({"Set": {"user": {watcher.name: {
                    "room": {"name": room.name}, "event": {"left": True}}}}}))
        conn.close()

    def _handle(self, conn, watcher, msg):
        for command, value in msg.items():
            if command == "TLS":
                Watcher(self, conn, "").send({"TLS": {"startTLS": "false"}})
            elif command == "Hello":
                name = value["username"].strip()
                while any(w.name == name for w in self.watchers()):
                    name += "_"
                watcher = Watcher(self, conn, name)
                room = self.rooms.setdefault(value["room"]["name"], Room(value["room"]["name"]))
                if room.watchers:
                    watcher.position = room.get_position()
                room.watchers[name] = watcher
                watcher.room = room
                self._broadcast(lambda w: w.send({"Set": {"user": {name: {
                    "room": {"name": room.name}, "event": {"joined": True}}}}}) if w is not watcher else None)
                watcher.send({"Hello": {"username": name, "room": {"name": room.name},
                                        "version": value.get("version"), "realversion": "1.7.7",
                                        "features": {"isolateRooms": False, "chat": True}, "motd": ""}})
                watcher.send({"Set": {"playlistChange": {"user": None, "files": []}}})
                watcher.send_state(room.get_position(), not room.playing, True, room.set_by, True)
            elif command == "Set" and watcher:
                if "file" in value:
                    watcher.file = value["file"]
                    self._broadcast(lambda w: w.send({"Set": {"user": {watcher.name: {
                        "room": {"name": watcher.room.name}, "file": watcher.file}}}}))
            elif command == "List" and watcher:
                users = {}
                for w in self.watchers():
                    users.setdefault(w.room.name, {})[w.name] = {
                        "position": 0, "file": w.file or {}, "controller": False, "isReady": None, "features": {}}
                watcher.send({"List": users})
            elif command == "Chat" and watcher:
                for w in watcher.room.watchers.values():
                    w.send({"Chat": {"username": watcher.name, "message": value}})
            elif command == "State" and watcher:
                self._state(watcher, value)
        return watcher

    def _state(self, watcher, state):
        ignore = state.get("ignoringOnTheFly") or {}
        if "server" in ignore and watcher.server_ignoring == ignore["server"]:
            watcher.server_ignoring = 0
        if "client" in ignore:
            watcher.client_ignoring = ignore["client"]
        ping = state.get("ping") or {}
        watcher.client_latency = ping.get("clientLatencyCalculation", 0)
        playstate = state.get("playstate")
        if watcher.server_ignoring:
            return
        watcher.updated = time.time()
        if not playstate:
            return
        room = watcher.room
        paused = playstate.get("paused")
        pause_changed = paused is not None and room.playing == bool(paused)
        if pause_changed:
            room.playing, room.set_by = not paused, watcher
        if playstate.get("position") is not None:
            watcher.position = playstate["position"]
        if playstate.get("doSeek") or pause_changed:
            position = watcher.get_position()
            room.position, room.set_by, room.last = position, watcher, time.time()
            for w in room.watchers.values():
                w.position = position
                w.updated = time.time()
            for w in list(room.watchers.values()):
                w.send_state(position, not room.playing, bool(playstate.get("doSeek")), watcher, True)
