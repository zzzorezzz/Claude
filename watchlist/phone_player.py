"""The phone's video player as the rest of the app sees a player: the same
calls as player.Session (current, position, send, replace, stop, listeners...),
so a watch party drives the phone's player exactly as it drives mpv.

The player itself is the phone app's (PlayerActivity, ExoPlayer). It tells this
side what it's doing many times a second (event()), and this side tells it
what to do (pause, seek, speed, show a message, play another file, close)
through the phone app's bridge.
"""

import json
import threading
import time
import uuid

_sessions = {}
_lock = threading.Lock()
_bridge = None          # the phone app's Java side (MainActivity.Bridge); None in tests


def set_bridge(bridge):
    global _bridge
    _bridge = bridge


class PhoneSession:
    def __init__(self, item_id, entries, start=0.0, paused=False, keep_open=False, anime=False):
        self.id = uuid.uuid4().hex[:12]
        self.item_id, self.entries = item_id, list(entries)
        self.start_at, self.start_paused, self.keep_open, self.anime = start, paused, keep_open, anime
        self.index, self.pos, self.dur, self.paused = 0, None, None, paused
        self.pos_at = 0.0
        self.listeners = []
        self.connected, self.error, self.ended = False, "", False
        self.party = False           # in a watch party: leaving the player doesn't pause it
        with _lock:
            older = list(_sessions.values())
            _sessions.clear()
            _sessions[self.id] = self
        for old in older:
            old.ended = True             # one player at a time, as on a computer

    def launch(self):
        """What the phone app needs to open the player for this."""
        return {"session": self.id, "item_id": self.item_id, "start": self.start_at, "paused": self.start_paused,
                "keep_open": self.keep_open, "anime": self.anime, "party": self.party,
                "entries": [{k: e.get(k) for k in ("path", "sub", "key", "label", "referer")} for e in self.entries]}

    def open(self):
        """Open the player from here (a guest following the host)."""
        if _bridge is not None:
            _bridge.openPlayer(json.dumps(self.launch()))
        return self

    # ---- what the player tells us

    def event(self, ev):
        if self.ended:
            return
        index = int(ev.get("index", self.index) or 0)
        changed = []
        if index != self.index:
            self.index, self.pos, self.dur = index, None, None
            changed.append(("playlist-pos", index))
        if ev.get("dur") and ev["dur"] > 0:
            if self.dur is None:
                changed.append(("duration", ev["dur"]))
            self.dur = float(ev["dur"])
        if ev.get("pos") is not None and self.dur:
            self.pos, self.pos_at = float(ev["pos"]), time.monotonic()
        if "paused" in ev:
            self.paused = bool(ev["paused"])
        self.connected = True
        if ev.get("ended"):
            self._end()
        for name, data in changed:
            for listener in list(self.listeners):
                try:
                    listener(self, {"event": "property-change", "name": name, "data": data})
                except Exception:  # noqa: BLE001 - a listener's problem isn't the player's
                    pass

    def _end(self):
        self.ended = True
        with _lock:
            _sessions.pop(self.id, None)

    # ---- the same calls as player.Session

    def current(self):
        return self.entries[self.index] if 0 <= self.index < len(self.entries) else None

    def position(self):
        if self.pos is None:
            return None
        if self.paused:
            return self.pos
        return self.pos + max(0.0, time.monotonic() - self.pos_at)

    def send(self, *commands):
        """mpv's commands, as the party sends them: seek, pause, speed, show-text."""
        if self.ended or _bridge is None:
            return
        out = []
        for c in commands:
            if c[0] == "seek":
                out.append({"do": "seek", "to": float(c[1])})
                self.pos, self.pos_at = float(c[1]), time.monotonic()
            elif c[0] == "set_property" and c[1] == "pause":
                out.append({"do": "pause" if c[2] else "play"})
                self.paused = bool(c[2])
            elif c[0] == "set_property" and c[1] == "speed":
                out.append({"do": "speed", "to": float(c[2])})
            elif c[0] == "show-text":
                out.append({"do": "text", "text": str(c[1]), "ms": int(c[2]) if len(c) > 2 else 3000})
        if out:
            _bridge.playerCommand(self.id, json.dumps(out))

    def replace(self, entry):
        """Play another file in the open player (the host moved on)."""
        self.entries, self.index, self.pos, self.dur = [entry], 0, None, None
        if not self.ended and _bridge is not None:
            _bridge.playerCommand(self.id, json.dumps([{"do": "replace", "launch": self.launch()}]))

    def stop(self):
        if not self.ended and _bridge is not None:
            _bridge.playerCommand(self.id, json.dumps([{"do": "close"}]))
        self._end()

    def status(self):
        entry = self.current()
        return {"active": not self.ended, "item_id": self.item_id, "file": (entry or {}).get("label", ""),
                "pos": self.pos, "dur": self.dur, "paused": self.paused, "connected": self.connected,
                "error": self.error}


def event(session_id, data):
    """From the phone app: what its player is doing (JSON)."""
    with _lock:
        session = _sessions.get(session_id)
    if session is not None:
        session.event(json.loads(data) if isinstance(data, str) else data)


def current():
    with _lock:
        return next((s for s in _sessions.values() if not s.ended), None)
