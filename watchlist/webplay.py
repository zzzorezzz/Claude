"""Playing on a phone or tablet over the network (Use on your phone): the phone's own <video>.

Nothing plays on the PC. The phone gets a player of its own (player.js in the page) and, for
each episode, an address under /stream/web/<ticket>/: a short-lived ticket, signed, for that
device only. What's behind it - a file on the PC, a torrent, a site's video with the headers
the site wants - stays on the PC; the phone's address never holds it. A ticket outlives its
last use by a while (AirPlay, Picture in Picture), then stops working.

A WebSession is the phone's player as the rest of the app sees a player - the same calls as
player.Session and phone_player.PhoneSession - so watch parties drive it the same way. The
phone tells it what it's doing every second, and takes what it's told to do in the answer.

Videos are made playable by transcode.py (one job per device at a time); the pieces it
writes are deleted when playback stops, or an hour after they were last used, and never
take more than PHONE_CACHE_CAP together.
"""

import base64
import hashlib
import hmac
import re
import secrets
import shutil
import threading
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from . import hlsfix, transcode

TICKET_LIFE = 8 * 3600          # a ticket never works longer than this
TICKET_IDLE = 2 * 3600          # ... nor this long after it was last used
JOB_IDLE = 120                  # a job nobody asks anything of is stopped after this
CACHE_AGE = 3600                # pieces left behind go after an hour
PHONE_CACHE_CAP = 5 * 1024 ** 3


# ---- tickets

class Tickets:
    def __init__(self):
        self.secret = secrets.token_bytes(32)
        self.items = {}
        self.lock = threading.Lock()

    def _sig(self, tid, exp, device):
        mac = hmac.new(self.secret, f"{tid}.{exp}.{device}".encode(), hashlib.sha256).digest()[:15]
        return base64.urlsafe_b64encode(mac).decode()

    def make(self, device, target, life=TICKET_LIFE):
        tid, exp = secrets.token_urlsafe(9), int(time.time() + life)
        with self.lock:
            for old in [k for k, v in self.items.items() if v["exp"] < time.time()]:
                del self.items[old]
            self.items[tid] = dict(target, device=device, exp=exp, used=time.time())
        return f"{tid}.{exp}.{self._sig(tid, exp, device)}"

    def check(self, ticket):
        """What a ticket is for (and it's been used now), or None."""
        parts = str(ticket or "").split(".")
        if len(parts) != 3 or not parts[1].isdigit():
            return None
        tid, exp, sig = parts
        with self.lock:
            t = self.items.get(tid)
            if not t or int(exp) != t["exp"] or t["exp"] < time.time() or time.time() - t["used"] > TICKET_IDLE:
                return None
            if not hmac.compare_digest(sig, self._sig(tid, exp, t["device"])):
                return None
            t["used"] = time.time()
            return t

    def drop_device(self, device):
        with self.lock:
            for k in [k for k, v in self.items.items() if v["device"] == device]:
                del self.items[k]


# ---- the phone's player, as the app sees a player

class WebSession:
    def __init__(self, device, item_id, entries, start=0.0, paused=False, index=0, anime=False, title=""):
        self.id = uuid.uuid4().hex[:12]
        self.device, self.item_id, self.entries = device, item_id, list(entries)
        self.start_at, self.start_paused, self.anime, self.title = float(start or 0), paused, anime, title
        self.index, self.pos, self.dur, self.paused = index, None, None, paused
        self.pos_at = 0.0
        self.listeners = []
        self.connected, self.error, self.ended = False, "", False
        self.party = False
        self.commands, self.seq = [], 0
        self.lock = threading.Lock()
        self.changed = threading.Condition(self.lock)
        self.seen = time.time()
        self.saved_at = 0.0
        self.on_end = None

    def launch(self):
        return {"session": self.id, "item_id": self.item_id, "title": self.title, "start": self.start_at,
                "index": self.index, "paused": self.start_paused, "anime": self.anime, "party": self.party,
                "entries": [{"key": e.get("key") or "", "label": e.get("label") or e.get("name") or "",
                             "web": bool(e.get("web") or e.get("online"))} for e in self.entries]}

    # ---- what the phone says

    def event(self, ev):
        if self.ended:
            return
        self.seen = time.time()
        changed = []
        try:
            index = int(ev.get("index", self.index))
        except (TypeError, ValueError):
            index = self.index
        if 0 <= index < len(self.entries) and index != self.index:
            self.index, self.pos, self.dur = index, None, None
            changed.append(("playlist-pos", index))
        if ev.get("dur") and float(ev["dur"]) > 0:
            if self.dur is None:
                changed.append(("duration", float(ev["dur"])))
            self.dur = float(ev["dur"])
        if ev.get("pos") is not None and self.dur:
            self.pos, self.pos_at = float(ev["pos"]), time.monotonic()
        if "paused" in ev:
            self.paused = bool(ev["paused"])
        self.connected = True
        for name, data in changed:
            for listener in list(self.listeners):
                try:
                    listener(self, {"event": "property-change", "name": name, "data": data, "moved": True})
                except Exception:  # noqa: BLE001 - a listener's problem isn't the player's
                    pass
        if ev.get("closed"):
            self._end()

    def take(self, after):
        """The commands the phone hasn't had yet: (newest number, [commands, each with its "n"])."""
        with self.lock:
            return self.seq, [dict(c, n=n) for n, c in self.commands if n > after]

    def wait(self, after, timeout):
        """take(), once there's something new (or `timeout` seconds went by)."""
        with self.changed:
            self.changed.wait_for(lambda: self.seq > after or self.ended, timeout)
            return self.seq, [dict(c, n=n) for n, c in self.commands if n > after]

    def _queue(self, *cmds):
        with self.changed:
            for c in cmds:
                self.seq += 1
                self.commands.append((self.seq, c))
            self.commands = self.commands[-60:]
            self.changed.notify_all()

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
        """mpv's commands, as the party and the app send them: seek, pause, speed, a message."""
        if self.ended:
            return False
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
            elif c[0] == "script-message-to" and len(c) > 3 and c[2] == "show-loading":
                out.append({"do": "text", "text": str(c[3]), "ms": 60000})
        if out:
            self._queue(*out)
        return True

    def replace(self, entry):
        self.entries, self.index, self.pos, self.dur = [entry], 0, None, None
        self.start_at = 0.0
        self._queue({"do": "replace", "launch": self.launch()})
        return True

    def jump(self, index):
        self._queue({"do": "index", "to": int(index)})

    def swap(self, entry, at=0.0):
        """The episode playing, from somewhere else (another source, the dub), from where it is."""
        if 0 <= self.index < len(self.entries):
            self.entries[self.index] = dict(entry)
        self._queue({"do": "reload", "at": float(at or 0)})
        return True

    def append(self, entry):
        self.entries.append(dict(entry))
        self._queue({"do": "entries", "entries": self.launch()["entries"]})
        return entry

    def add_tracks(self, entry, tracks):
        entry.setdefault("extras", []).extend(tracks)
        return True

    def stop(self):
        self._queue({"do": "close"})
        self._end()

    def _end(self):
        if self.ended:
            return
        with self.changed:
            self.ended = True
            self.changed.notify_all()
        if self.on_end:
            try:
                self.on_end(self)
            except Exception:  # noqa: BLE001
                pass

    def status(self):
        entry = self.current()
        return {"active": not self.ended, "item_id": self.item_id, "label": (entry or {}).get("label", ""),
                "key": (entry or {}).get("key", ""), "file": (entry or {}).get("label", ""), "pos": self.pos,
                "dur": self.dur, "paused": self.paused, "connected": self.connected, "error": self.error}


# ---- a site's video, through the app (its playlists pointed at the ticket, nothing else)

KINDS = {".m3u8": "m3u8", ".ts": "ts", ".m4s": "m4s", ".mp4": "mp4", ".aac": "aac", ".vtt": "vtt", ".key": "key",
         ".webvtt": "vtt", ".m4a": "m4a", ".mp3": "mp3"}


def rewrite(text, base_url, ticket, remember):
    """A site's HLS playlist with every address it holds - variants, pieces, keys, init pieces,
    other renditions - pointed at /stream/web/<ticket>/r/<n>.<kind> (`remember(url)` -> n)."""
    out = []
    for line in text.splitlines():
        s = line.strip()

        def point(uri, hint=""):
            url = urllib.parse.urljoin(base_url, uri)
            ext = Path(urllib.parse.urlsplit(url).path).suffix.lower()
            kind = hint or KINDS.get(ext, "ts")
            return f"/stream/web/{ticket}/r/{remember(url)}.{kind}"
        if s.startswith("#"):
            tag = s.split(":", 1)[0]
            hint = {"#EXT-X-KEY": "key", "#EXT-X-SESSION-KEY": "key", "#EXT-X-MAP": "mp4", "#EXT-X-MEDIA": "m3u8",
                    "#EXT-X-I-FRAME-STREAM-INF": "m3u8"}.get(tag, "")
            line = re.sub(r'URI="([^"]+)"', lambda m: f'URI="{point(m.group(1), hint)}"', line)
            out.append(line)
        elif s:
            previous = next((x.strip() for x in reversed(out) if x.strip()), "")
            out.append(point(s, "m3u8" if previous.startswith("#EXT-X-STREAM-INF") else ""))
        else:
            out.append(line)
    return "\n".join(out) + "\n"


def open_remote(url, headers, range_header=None, timeout=30):
    h = dict(headers or {})
    if range_header:
        h["Range"] = range_header
    return urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout)


def strip_picture(head):
    """A piece's bytes without a picture in front of them (hlsfix.py), or as they are."""
    if head.startswith(hlsfix.PICTURES):
        at = hlsfix._sync(head)
        if at is not None:
            return head[at:]
    return head


# ---- jobs: one per device

class Jobs:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.lock = threading.Lock()
        self.by_device = {}             # device -> Job
        self.started = False

    def start(self, device, source, info, how, at=0.0, headers=None, subs=None):
        with self.lock:
            old = self.by_device.pop(device, None)
        if old:
            old.stop()
        job = transcode.Job(source, info, how, self.folder / uuid.uuid4().hex[:12], start=at, headers=headers, subs=subs)
        job.device = device
        job.run()
        with self.lock:
            self.by_device[device] = job
        if not self.started:
            self.started = True
            threading.Thread(target=self._tidy_loop, daemon=True).start()
        return job

    def current(self, device):
        with self.lock:
            return self.by_device.get(device)

    def stop(self, device):
        with self.lock:
            job = self.by_device.pop(device, None)
        if job:
            job.stop()

    def _tidy_loop(self):
        while True:
            time.sleep(30)
            try:
                self.tidy()
            except Exception:  # noqa: BLE001 - tried again in a moment
                pass

    def tidy(self):
        """Idle jobs stopped (their pieces deleted); pieces left behind deleted after an hour;
        never more than PHONE_CACHE_CAP of them."""
        with self.lock:
            idle = [d for d, j in self.by_device.items() if time.time() - j.used > JOB_IDLE]
            jobs = {d: self.by_device.pop(d) for d in idle}
            live = {j.folder.resolve() for j in self.by_device.values()}
        for job in jobs.values():
            job.stop()
        if not self.folder.is_dir():
            return
        dirs = []
        for d in self.folder.iterdir():
            if not d.is_dir() or d.resolve() in live:
                continue
            try:
                when = max((f.stat().st_mtime for f in d.rglob("*")), default=d.stat().st_mtime)
            except OSError:
                when = 0
            if time.time() - when > CACHE_AGE:
                shutil.rmtree(d, ignore_errors=True)
            else:
                dirs.append((when, d))
        total = sum(f.stat().st_size for f in self.folder.rglob("*") if f.is_file())
        for _, d in sorted(dirs):
            if total <= PHONE_CACHE_CAP:
                break
            size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            shutil.rmtree(d, ignore_errors=True)
            total -= size
        if total > PHONE_CACHE_CAP:
            with self.lock:
                oldest = sorted(self.by_device.items(), key=lambda x: x[1].used)
            for device, _ in oldest[:1]:
                self.stop(device)

    def clear(self):
        """At start: whatever an earlier run left."""
        shutil.rmtree(self.folder, ignore_errors=True)
