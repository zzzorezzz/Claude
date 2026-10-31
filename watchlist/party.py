"""Watch together, built in.

The app talks to a Syncplay server itself (Syncplay's free public server by
default), so no other program is needed. Each of you plays your own copy in
the built-in player and the server keeps you in step: pausing, playing and
skipping on either side happens on both. It follows Syncplay's own rules
(protocols.py and client.py in the Syncplay source), so people using the
Syncplay app can join a room too.

On top of Syncplay, each player tells the room which title and episode it's
playing (and, for a torrent, its magnet). The party isn't tied to a player
window: whatever the host plays, on any title, the guests' apps follow,
finding their own copy (or streaming the host's torrent, getting it ready
first), and opening their player if it was closed. While a guest gets ready,
the room is told how far along it is, so the host sees "on the way 40%".
A guest leaving changes nothing for the host; the host ending it ends it
for everyone, and the host closing the player closes the guests' players
(the party stays on).

The host says what it's about to play before its own copy is ready ("on the
way"), with the torrent's exact file and the host's address, so everyone gets
ready at the same time - a guest can download straight from the host. A link
the host opens in the browser (Watch online) opens for the guests too.
"""

import json
import os
import socket
import ssl
import threading
import time

PROTOCOL_VERSION = "1.2.255"     # what Syncplay clients send, for old servers
SYNCPLAY_VERSION = "1.7.7"
FEATURES = {"sharedPlaylists": False, "chat": True, "uiMode": "CLI", "featureList": True,
            "readiness": True, "managedRooms": True, "persistentRooms": True, "setOthersReadiness": False}
SEEK_THRESHOLD = 1.0             # Syncplay's numbers (constants.py)
REWIND_THRESHOLD = 4.0
SLOWDOWN_THRESHOLD = 1.5
SLOWDOWN_RESET = 0.1
SLOWDOWN_RATE = 0.95
PING_WEIGHT = 0.85
TICK = 0.2                       # how often the player is checked
# What a guest is doing, as the room sees it (in the "file" each person sets).
WAITING_STATUSES = ("idle", "finding", "preparing", "needs-file")
# A chat message from the host's app asking the guests' apps to open a link.
OPEN_LINK = "[Watch Anything] open: "


class PartyError(Exception):
    pass


class _Ping:
    """Syncplay's round-trip and delay estimate (protocols.PingService)."""

    def __init__(self):
        self.rtt, self.fd, self.avg = 0.0, 0.0, 0.0

    def receive(self, timestamp, sender_rtt):
        if not timestamp:
            return
        self.rtt = time.time() - timestamp
        if self.rtt < 0 or sender_rtt < 0:
            return
        if not self.avg:
            self.avg = self.rtt
        self.avg = self.avg * PING_WEIGHT + self.rtt * (1 - PING_WEIGHT)
        self.fd = self.avg / 2 + (self.rtt - sender_rtt) if sender_rtt < self.rtt else self.avg / 2


class Connection:
    """JSON lines to and from a Syncplay server, encrypted when it allows."""

    def __init__(self, host, port, timeout=10, context=None):
        self.host, self.port, self.tls = host, port, False
        self._lock = threading.Lock()
        self._buf = b""
        try:
            self.sock = self._connect(timeout, context)
        except (OSError, ssl.SSLError):
            # A server without a proper certificate: connect without encryption.
            self.sock = self._connect(timeout, None, tls=False)

    def _connect(self, timeout, context, tls=True):
        sock = socket.create_connection((self.host, self.port), timeout=timeout)
        sock.settimeout(timeout)
        self.sock, self._buf = sock, b""
        if tls:
            self.send({"TLS": {"startTLS": "send"}})
            reply = self.read() or {}
            if "true" in str((reply.get("TLS") or {}).get("startTLS", "")):
                ctx = context or ssl.create_default_context()
                sock = ctx.wrap_socket(sock, server_hostname=self.host)
                self.tls = True
        self.sock = sock
        return sock

    def send(self, message):
        data = json.dumps(message).encode("utf-8") + b"\r\n"
        with self._lock:
            self.sock.sendall(data)

    def read(self):
        """The next message, or None when the server has closed the connection."""
        while True:
            for sep in (b"\r\n", b"\n"):
                if sep in self._buf:
                    line, self._buf = self._buf.split(sep, 1)
                    line = line.strip()
                    if not line:
                        break
                    try:
                        return json.loads(line.decode("utf-8"))
                    except ValueError:
                        break
            else:
                chunk = self.sock.recv(65536)
                if not chunk:
                    return None
                self._buf += chunk
                continue

    def close(self):
        # shutdown() first: it tells the server at once, and wakes the reading thread.
        for step in (lambda: self.sock.shutdown(socket.SHUT_RDWR), self.sock.close):
            try:
                step()
            except OSError:
                pass


class Party:
    """One watch party: a connection to the room and the built-in player.

    role "host" leads (its player moves on to the next episode by itself);
    "guest" follows `leader` (the host's name): it plays the same episode,
    found by `resolve(file)`, and leaves when the leader does."""

    def __init__(self, server, room, name, role, resolve=None, leader=None, context=None, on_end=None,
                 open_player=None, invite="", avatar="", open_link=None):
        host, _, port = server.partition(":")
        self.server_host, self.server_port = host, int(port or 8999)
        self.server, self.room, self.name, self.role = server, room, name, role
        self.resolve, self.leader, self.context = resolve, leader, context
        self.on_end = on_end or (lambda party, reason: None)
        # A guest's player opened for what the host plays (when there isn't one open).
        self.open_player = open_player
        self.invite = invite
        self.avatar = avatar             # a small picture of you ("data:image/jpeg;base64,..."), shown to the others
        self.open_link = open_link       # a guest: opens a link the host shared (Watch online)
        self._following = None           # a guest: the key of what the host plays that's being followed
        self.mine = {"status": "idle"}   # a guest getting ready: what it's doing, how far along
        self._mine_sent = 0.0
        self._switch_gen = 0
        self.me = name
        self.people = {}             # name -> file info
        self.session = None
        self.conn = None
        self.active = False
        self.ended = ""
        self.missing = ""            # what the leader plays that this computer doesn't have
        self.lock = threading.RLock()
        self._joined = threading.Event()
        self._listed = threading.Event()
        self._ping = _Ping()
        self._client_ignoring = 0
        self._server_ignoring = 0
        self._player_pos, self._player_paused, self._player_at = 0.0, True, None
        self._global_pos, self._global_paused, self._global_at = 0.0, True, None
        self._speed_changed = False
        self._quiet_until = 0.0      # just moved the player ourselves: don't read that as you
        self._file_key = None        # what's loaded, as announced
        self._announced = False
        self._init_pending = False   # a guest's new file waits for the room's position
        self._leader_file = None
        self._last_leader_key = None
        self._hold_until = 0.0       # the host waiting for everyone before playing

    # ---- connecting

    def connect(self, timeout=10):
        try:
            self.conn = Connection(self.server_host, self.server_port, timeout, self.context)
            self.conn.send({"Hello": {"username": self.name, "room": {"name": self.room},
                                      "version": PROTOCOL_VERSION, "realversion": SYNCPLAY_VERSION,
                                      "features": FEATURES}})
        except OSError as e:
            raise PartyError(f"couldn't connect to the watch-party server {self.server} ({e})") from e
        self.active = True
        threading.Thread(target=self._read_loop, daemon=True).start()
        if not self._joined.wait(timeout):
            self.leave("the watch-party server didn't answer")
            raise PartyError(f"the watch-party server {self.server} didn't answer")
        if self.ended:
            raise PartyError(self.ended)
        self.conn.send({"List": None})
        self._listed.wait(5)
        # Everyone sees you (and your picture) straight away, before anything plays.
        if self.people.get(self.me) is None:
            self._set_file({"name": "", "duration": 0, "size": 0, "watchlist": {"status": "idle"}})
        threading.Thread(target=self._tick_loop, daemon=True).start()
        return self

    def _set_file(self, file_):
        """Tell the room what you're doing (your picture goes along)."""
        meta = dict(file_.get("watchlist") or {})
        if self.avatar:
            meta["avatar"] = self.avatar
        if self.role == "host":
            meta["host"] = True
        file_ = dict(file_, watchlist=meta)
        self.people[self.me] = file_
        try:
            self.conn.send({"Set": {"file": file_}})
        except OSError:
            pass
        return file_

    def announce_upcoming(self, meta, label):
        """The host is getting something ready: everyone starts getting it ready
        now too (not once the host's copy is ready)."""
        if self.role != "host" or not self.active:
            return
        with self.lock:
            current = (self.people.get(self.me) or {}).get("watchlist") or {}
            if current.get("key") == meta.get("key") and current.get("status") in ("preparing", "watching"):
                return
            self._set_file({"name": label, "duration": 0, "size": 0,
                            "watchlist": dict(meta, label=label, status="preparing")})

    def share_link(self, url, label=""):
        """The host opened a link (Watch online): the guests' apps open it too."""
        if self.role != "host" or not self.active:
            return
        try:
            self.conn.send({"Chat": f"{OPEN_LINK}{url}" + (f" ({label})" if label else "")})
        except OSError:
            pass

    def attach(self, session, hold=False):
        """Follow this player window (until it's closed; the party carries on).
        `hold`: the host's player waits, paused, until everyone's ready (then plays for all)."""
        with self.lock:
            self.session = session
            self._hold_until = time.time() + 180 if hold else 0.0
            self._announced = False
            self._file_key = None
            self._player_at = None
            if self.role == "guest":
                self._init_pending = True
        session.listeners.append(self._player_message)

    def leave(self, reason="you left"):
        with self.lock:
            if not self.active and self.ended:
                return
            self.active = False
            self.ended = self.ended or reason
        if self.conn:
            self.conn.close()
        self.on_end(self, self.ended)

    # ---- what others see

    def leader_name(self):
        if self.role == "host":
            return self.me
        if self.leader and self.leader in self.people:
            return self.leader
        host = next((n for n, f in self.people.items()
                     if n != self.me and ((f or {}).get("watchlist") or {}).get("host")), None)
        return host or next((n for n, f in self.people.items() if n != self.me and f), self.leader)

    def leader_file(self):
        name = self.leader_name()
        return self.people.get(name) if name else None

    @staticmethod
    def person_state(file_):
        """("watching" | "finding" | "preparing" | "needs-file" | "idle", meta) from a file a person set."""
        meta = (file_ or {}).get("watchlist") or {}
        status = meta.get("status")
        if status in WAITING_STATUSES:
            return status, meta
        if file_ and file_.get("duration"):
            return "watching", meta
        return "idle", meta

    def status(self):
        with self.lock:
            entry = self.session.current() if self.session and not self.session.ended else None
            leader = self.leader_name()
            people = []
            for n, f in sorted(self.people.items(), key=lambda x: (x[0] != leader, x[0].lower())):
                state, meta = self.person_state(f)
                if n == self.me and self.role == "guest" and self.mine.get("status") in WAITING_STATUSES[1:]:
                    state, meta = self.mine["status"], self.mine
                pic = ((f or {}).get("watchlist") or {}).get("avatar") or (self.avatar if n == self.me else "")
                people.append({"name": n, "me": n == self.me, "leader": n == leader, "state": state,
                               "label": meta.get("label") or (f or {}).get("name", ""),
                               "progress": meta.get("progress"), "eta": meta.get("eta"),
                               "avatar": pic if isinstance(pic, str) and pic.startswith("data:image/") else ""})
            lead_state, lead_meta = self.person_state(self.leader_file())
            return {"active": self.active, "role": self.role, "room": self.room, "server": self.server,
                    "me": self.me, "leader": leader, "people": people, "invite": self.invite,
                    "host_playing": lead_meta.get("label") if lead_state == "watching" else "",
                    "playing": (entry or {}).get("label", ""), "player_open": bool(entry),
                    "missing": self.missing, "mine": dict(self.mine),
                    "ended": self.ended, "encrypted": bool(self.conn and self.conn.tls)}

    def report(self, status, progress=None, eta=None, label=None):
        """A guest getting ready: tell the room (so the host sees it next to your name)."""
        with self.lock:
            changed = status != self.mine.get("status")
            self.mine = {"status": status, "label": label if label is not None else self.mine.get("label", ""),
                         "progress": progress, "eta": eta}
            if self.role != "guest" or not self.conn or not self.active:
                return
            if not changed and time.time() - self._mine_sent < 1.0:
                return
            self._mine_sent = time.time()
            self._set_file({"name": self.mine["label"] or "", "duration": 0, "size": 0,
                            "watchlist": {k: v for k, v in self.mine.items() if v is not None}})

    def osd(self, text, seconds=4):
        if self.session:
            self.session.send(["show-text", text, int(seconds * 1000)])

    # ---- messages from the server

    def _read_loop(self):
        reason = "the connection to the watch party was lost"
        try:
            self.conn.sock.settimeout(30)
            while self.active:
                msg = self.conn.read()
                if msg is None:
                    break
                for command, value in msg.items():
                    self._handle(command, value)
        except (OSError, ValueError):
            pass
        if self.active:
            self.leave(reason)

    def _handle(self, command, value):
        with self.lock:
            if command == "Hello":
                self.me = value.get("username") or self.me
                self.people.setdefault(self.me, None)
                self._joined.set()
            elif command == "Error":
                self.ended = (value or {}).get("message") or "the watch-party server refused"
                self._joined.set()
                self.active = False
            elif command == "List":
                # Servers that don't keep rooms apart list everyone: only this room counts.
                self.people = {name: (info or {}).get("file") or None
                               for name, info in ((value or {}).get(self.room) or {}).items()}
                self.people.setdefault(self.me, None)
                self._listed.set()
                self._follow_leader()
            elif command == "Set":
                self._handle_set(value or {})
            elif command == "State":
                self._handle_state(value or {})
            elif command == "Chat":
                msg, who = str((value or {}).get("message") or ""), (value or {}).get("username")
                if msg.startswith(OPEN_LINK):
                    if self.role == "guest" and who == self.leader_name() and self.open_link:
                        url = msg[len(OPEN_LINK):].split(" ")[0]
                        if url.lower().startswith(("http://", "https://")):
                            threading.Thread(target=self.open_link, args=(url,), daemon=True).start()
                            self.osd(f"{who} opened a page - it's open on your computer too", 5)
                else:
                    self.osd(f'{who}: {msg}', 6)

    def _handle_set(self, settings):
        users = settings.get("user") or {}
        for name, info in users.items():
            info = info or {}
            if (info.get("room") or {}).get("name", self.room) != self.room:
                if name in self.people and name != self.me:
                    info = {"event": {"left": True}}   # moved to another room
                else:
                    continue
            event = info.get("event") or {}
            if event.get("left"):
                was_leader = self.role == "guest" and name != self.me and name == self.leader_name()
                self.people.pop(name, None)
                if was_leader:
                    self.osd(f"{name} left - the watch party is over", 5)
                    threading.Thread(target=self._end_after_leader_left, args=(name,), daemon=True).start()
                elif name != self.me:
                    self.osd(f"{name} left the watch party")
                continue
            if event.get("joined"):
                self.people.setdefault(name, None)
                if name != self.me:
                    self.osd(f"{name} joined the watch party"
                             + (" - press Space when you're both ready" if self.role == "host" else ""), 6)
            if "file" in info:
                before, _ = self.person_state(self.people.get(name))
                self.people[name] = info["file"] or None
                if name != self.me:
                    after, meta = self.person_state(self.people[name])
                    if self.role == "host" and after != before:
                        what = meta.get("label") or "the episode"
                        if after in ("finding", "preparing") and before not in ("finding", "preparing"):
                            self.osd(f"{name} is on the way - getting {what} ready", 5)
                        elif after == "needs-file":
                            self.osd(f"{name} doesn't have {what} yet", 5)
                        elif after == "watching" and before in ("finding", "preparing", "needs-file"):
                            self.osd(f"{name} is here and ready", 4)
                    self._follow_leader()

    def _end_after_leader_left(self, name):
        time.sleep(3)
        if self.session and not self.session.ended:
            self.session.stop()
        self.leave(f"{name} left")

    # ---- a guest following the host

    def _follow_leader(self):
        if self.role != "guest" or not self.resolve:
            return
        file_ = self.leader_file()
        state, meta = self.person_state(file_)
        if state == "idle" and self._following:
            # The host closed the player: yours closes too (the party stays on).
            self._following = self._last_leader_key = None
            self._switch_gen += 1
            session = self.session
            self.mine = {"status": "idle"}
            if session and not session.ended:
                self.osd(f"{self.leader_name()} closed the player", 2)
                threading.Thread(target=lambda: (time.sleep(1.0), session.stop()), daemon=True).start()
            return
        if state not in ("watching", "preparing"):
            return                     # the host isn't playing anything (yet)
        # The same thing the host is getting ready, then playing, is followed once.
        key = meta.get("key") or (file_ or {}).get("name") or ""
        if not key or key == self._last_leader_key:
            return
        self._last_leader_key = self._following = key
        self._switch_gen += 1
        threading.Thread(target=self._switch_to, args=(file_, self._switch_gen), daemon=True).start()

    def wanted_position(self):
        """Where the room is now: a guest getting ready gets that part first."""
        return self._global_position()

    def _switch_to(self, file_, gen):
        label = (file_.get("watchlist") or {}).get("label") or file_.get("name", "that episode")
        self.report("finding", label=label)
        try:
            entry = self.resolve(file_, report=lambda *a, **k: gen == self._switch_gen and self.report(*a, **k),
                                 at=self.wanted_position() / file_["duration"] if file_.get("duration") else 0)
        except Exception:  # noqa: BLE001 - shown as "needs the file"
            entry = None
        with self.lock:
            if gen != self._switch_gen or not self.active:
                return                 # the host has moved on again
            self.missing = "" if entry else label
        if not entry:
            self.report("needs-file", label=label)
            self.osd(f"You don't have {label} - choose it in Watch Anything to keep watching", 8)
            return
        self.use(entry)

    def use(self, entry):
        """Play `entry` for the party: in the open player, or a new one."""
        with self.lock:
            self.missing = ""
            self.mine = {"status": "idle", "label": entry.get("label") or ""}
            session = self.session
            jump = None
            if session and not session.ended:
                current = session.current()
                if current and (current.get("path") == entry["path"]
                                or (entry.get("key") and current.get("key") == entry.get("key"))):
                    return
                # (the season is in this player already: that episode of it)
                jump = next((i for i, e in enumerate(getattr(session, "entries", None) or [])
                             if entry.get("key") and e.get("key") == entry["key"]), None)
                self._init_pending = True
                self._announced = False
                self._file_key = None
        if session and not session.ended:
            if jump is not None and hasattr(session, "jump"):
                session.jump(jump)
            else:
                session.replace(entry)
            self.osd(f"Now playing {entry.get('label') or ''}".strip())
        elif self.open_player:
            self.open_player(self, entry)

    # ---- keeping the players together (Syncplay's client.py)

    def _player_message(self, session, msg):
        if msg.get("event") == "property-change" and msg.get("name") in ("duration", "playlist-pos"):
            with self.lock:
                if msg.get("name") == "playlist-pos":
                    self._announced = False
                    if self.role == "guest":
                        self._init_pending = True

    def _tick_loop(self):
        while self.active:
            time.sleep(TICK)
            session = self.session
            if not session:
                continue
            if session.ended:
                self._player_closed(session)
                continue
            try:
                with self.lock:
                    self._announce_file(session)
                    self._release_hold(session)
                    self._update_player(session)
            except OSError:
                pass

    def _release_hold(self, session):
        """The host's new episode starts for everyone when they all have it ready
        (or someone doesn't have it, or it's taking more than three minutes)."""
        if not self._hold_until or not self._ready(session):
            return
        if not session.paused:
            self._hold_until = 0.0           # you pressed play yourself
            return
        mine = ((self.people.get(self.me) or {}).get("watchlist") or {}).get("key")
        waiting = []
        for name, f in self.people.items():
            if name == self.me:
                continue
            state, meta = self.person_state(f)
            if state in ("finding", "preparing") or (state == "watching" and meta.get("key") != mine) \
                    or (state == "idle" and time.time() < self._hold_until - 170):
                waiting.append(name)
        if waiting and time.time() < self._hold_until:
            return
        self._hold_until = 0.0
        session.send(["set_property", "pause", False])
        self.osd("Everyone's ready - playing for all of you" if not waiting else "Playing (not everyone was ready)", 3)

    def _player_closed(self, session):
        """A player window was closed: the party carries on without it. The host
        pausing everyone and saying so; a guest just stops following along."""
        with self.lock:
            if self.session is not session:
                return
            self.session = None
            self._announced = False
            self._file_key = None
            self._player_at = None
            if self.role == "host":
                pos = self._global_position()
                self._global_pos, self._global_paused, self._global_at = pos, True, time.time()
                self._send_state(pos, True, False, None, True)
                self._set_file({"name": "", "duration": 0, "size": 0, "watchlist": {"status": "idle"}})

    def _announce_file(self, session):
        """Tell the room what's playing once its length is known."""
        entry = session.current()
        if self._announced or not entry or not session.dur or session.pos is None:
            return
        meta = dict(entry.get("party") or {})
        meta["label"] = entry.get("label") or ""
        meta["status"] = "watching"
        try:
            size = os.path.getsize(entry["path"])
        except OSError:
            size = 0
        self._set_file({"name": entry.get("name") or os.path.basename(entry["path"]), "duration": round(session.dur, 3),
                        "size": size, "watchlist": meta})
        self._announced = True
        self._file_key = entry["path"]
        if self.role == "host":
            # The host decides where a new episode starts (its own place in it).
            pos = session.position() or 0.0
            self._player_pos, self._player_paused, self._player_at = pos, session.paused, time.time()
            self._global_pos, self._global_paused, self._global_at = pos, session.paused, time.time()
            self._send_state(pos, session.paused, True, None, True)

    def _ready(self, session):
        return session.connected and session.dur is not None and session.pos is not None and self._announced

    def _player_position(self):
        if self._player_at is None:
            return self._global_position() if self._global_at else 0.0
        if self._player_paused:
            return self._player_pos
        return self._player_pos + (time.time() - self._player_at)

    def _global_position(self):
        if self._global_at is None:
            return 0.0
        if self._global_paused:
            return self._global_pos
        return self._global_pos + (time.time() - self._global_at)

    def _changes(self, paused, position):
        pause_change = (self._player_paused if self._player_at else True) != paused and \
            (self._global_paused if self._global_at else True) != paused
        seeked = abs(self._player_position() - position) > SEEK_THRESHOLD and \
            abs(self._global_position() - position) > SEEK_THRESHOLD
        return pause_change, seeked

    def _update_player(self, session):
        """You paused, played or skipped: tell the room."""
        if not self._ready(session) or self._init_pending:
            return
        if time.time() < self._quiet_until:
            return
        paused, position = session.paused, session.position()
        pause_change, seeked = self._changes(paused, position)
        first = self._player_at is None
        self._player_pos, self._player_paused, self._player_at = position, paused, time.time()
        if self._global_at and not first and (pause_change or seeked):
            self._send_state(position, paused, seeked, None, True)

    def _handle_state(self, state):
        latency = None
        age = 0.0
        ignore = state.get("ignoringOnTheFly") or {}
        if "server" in ignore:
            self._server_ignoring = ignore["server"]
            self._client_ignoring = 0
        elif "client" in ignore and ignore["client"] == self._client_ignoring:
            self._client_ignoring = 0
        playstate = state.get("playstate")
        ping = state.get("ping") or {}
        latency = ping.get("latencyCalculation")
        if "clientLatencyCalculation" in ping:
            self._ping.receive(ping["clientLatencyCalculation"], ping.get("serverRtt") or 0)
            age = self._ping.fd
        session = self.session
        ready = session is not None and not session.ended and self._ready(session)
        if playstate is not None and not self._client_ignoring and ready:
            position = playstate.get("position") or 0.0
            paused = playstate.get("paused")
            if paused is not None:
                if not paused:
                    position += age
                self._apply_room_state(session, position, paused, playstate.get("doSeek"), playstate.get("setBy"))
        if ready and not self._init_pending and self._player_at is not None and time.time() >= self._quiet_until:
            position = session.position()
            paused = session.paused
            pause_change, seeked = self._changes(paused, position)
            self._send_state(position, paused, seeked, latency, pause_change)
        else:
            self._send_state(None, None, False, latency, False)

    def _apply_room_state(self, session, position, paused, do_seek, set_by):
        if self.role == "guest" and self._init_pending:
            # Starting a file: go to where the room is, playing or paused like the room.
            leader = self.leader_file() or {}
            mine = self.people.get(self.me) or {}
            if leader.get("watchlist") and mine.get("watchlist") and \
                    leader["watchlist"].get("key") != mine["watchlist"].get("key"):
                return   # still on another episode; the switch is on its way
            self._quiet_until = time.time() + 1.5
            session.send(["seek", position, "absolute"], ["set_property", "pause", paused])
            self._player_pos, self._player_paused, self._player_at = position, paused, time.time()
            self._global_pos, self._global_paused, self._global_at = position, paused, time.time()
            self._init_pending = False
            return
        pause_changed = paused != self._global_paused or paused != self._player_paused
        diff = self._player_position() - position
        first = self._global_at is None
        self._global_pos, self._global_paused, self._global_at = position, paused, time.time()
        if first:
            return
        commands = []
        if do_seek and set_by != self.me:
            commands.append(["seek", position, "absolute"])
            self._player_pos, self._player_at = position, time.time()
            if set_by:
                self.osd(f"{set_by} skipped", 2)
        elif diff > REWIND_THRESHOLD and set_by != self.me:
            commands.append(["seek", position, "absolute"])
            self._player_pos, self._player_at = position, time.time()
        elif not do_seek and not paused:
            if diff > SLOWDOWN_THRESHOLD and not self._speed_changed and set_by != self.me:
                commands.append(["set_property", "speed", SLOWDOWN_RATE])
                self._speed_changed = True
            elif self._speed_changed and diff < SLOWDOWN_RESET:
                commands.append(["set_property", "speed", 1.0])
                self._speed_changed = False
        if pause_changed and paused != self._player_paused:
            if paused and set_by != self.me:
                commands.append(["seek", position, "absolute"])
            commands.append(["set_property", "pause", paused])
            self._player_paused = paused
            self._player_at = time.time()
            if set_by and set_by != self.me:
                self.osd(f"{set_by} {'paused' if paused else 'pressed play'}", 2)
        if commands:
            self._quiet_until = time.time() + 1.0
            session.send(*commands)

    def _send_state(self, position, paused, do_seek, latency, state_change):
        state = {}
        if (self._client_ignoring == 0 or self._server_ignoring != 0) and position is not None and paused is not None:
            state["playstate"] = {"position": position, "paused": paused}
            if do_seek:
                state["playstate"]["doSeek"] = True
        state["ping"] = {"clientLatencyCalculation": time.time(), "clientRtt": self._ping.rtt}
        if latency:
            state["ping"]["latencyCalculation"] = latency
        if state_change:
            self._client_ignoring += 1
        if self._server_ignoring or self._client_ignoring:
            state["ignoringOnTheFly"] = {}
            if self._server_ignoring:
                state["ignoringOnTheFly"]["server"] = self._server_ignoring
                self._server_ignoring = 0
            if self._client_ignoring:
                state["ignoringOnTheFly"]["client"] = self._client_ignoring
        try:
            self.conn.send({"State": state})
        except OSError:
            pass
