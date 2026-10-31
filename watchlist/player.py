"""The built-in player: mpv, started and followed by the app.

mpv plays in its own window. The app talks to it over mpv's JSON IPC
connection (a named pipe on Windows, a Unix socket on a Mac) to learn which
file is playing and how far in you are, so it can save your place, resume
later and mark an episode watched when you reach its end.
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

SAVE_EVERY = 15        # seconds between saving your place while watching
RESUME_FROM = 30       # don't bother resuming in the first 30 seconds


def find_mpv():
    """The mpv bundled with the app, or one that's installed."""
    candidates = []
    if os.environ.get("WATCHLIST_MPV"):
        candidates.append(Path(os.environ["WATCHLIST_MPV"]))
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        candidates += [base / "mpv" / "mpv.exe", base / "mpv" / "mpv"]
        # Watchlist.app/Contents/MacOS/Watchlist -> Contents/Resources/mpv.app
        contents = Path(sys.executable).resolve().parent.parent
        candidates.append(contents / "Resources" / "mpv.app" / "Contents" / "MacOS" / "mpv")
    if sys.platform == "win32":
        for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            if os.environ.get(env):
                candidates.append(Path(os.environ[env]) / "mpv" / "mpv.exe")
    elif sys.platform == "darwin":
        candidates += [Path("/Applications/mpv.app/Contents/MacOS/mpv"),
                       Path("/opt/homebrew/bin/mpv"), Path("/usr/local/bin/mpv")]
    for c in candidates:
        if c.is_file():
            return str(c)
    return shutil.which("mpv") or ""


SKIP_SCRIPT = Path(__file__).resolve().parent / "mpv_scripts" / "watchlist_skip.lua"


# The player's look: modern controls (uosc) with the app's colours, kept in a
# config folder of the app's own (scripts/, fonts/ and script-opts/ inside it).
UI_DIR = Path(__file__).resolve().parent / "mpv_config"


def _config_dir():
    """The player's settings folder. mpv keeps small files of its own next to its settings
    (its caches); inside the Mac app those would be changes to the app itself, which macOS
    blocks ("prevented from modifying apps"). So the packaged app's player uses a copy kept
    with your data, refreshed when the app is updated."""
    if not (getattr(sys, "frozen", False) and sys.platform != "win32"):
        return UI_DIR
    from . import __version__
    from .config import default_data_dir
    target = default_data_dir() / "player"
    try:
        stamp = target / ".version"
        if not stamp.is_file() or stamp.read_text().strip() != __version__:
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            shutil.copytree(UI_DIR, target)
            stamp.write_text(__version__)
        return target
    except OSError:
        return UI_DIR


def ui_args(theme="violet"):
    """Modern controls when they're bundled (in the app's colour theme); otherwise mpv's own, tidied up."""
    if (UI_DIR / "scripts" / "uosc" / "main.lua").is_file():
        from .config import THEMES
        accent, second, bg = THEMES.get(theme) or THEMES["violet"]
        text = "111111" if theme == "mono" else "ffffff"
        colors = (f"foreground={accent},foreground_text={text},background={bg},background_text=e6e9f2,"
                  f"curtain={bg},success=4ade80,error=ff6b81,match={second},heatmap={second}")
        # The controls hide after 5 seconds without the mouse moving or a key pressed (even
        # with the mouse resting over them), and come back when it moves or a key is used.
        return [f"--config-dir={_config_dir()}", "--osc=no", "--osd-bar=no", "--border=yes", "--cursor-autohide=5000",
                "--osd-font-size=30", "--osd-border-size=1.5", "--osd-color=#FFE6E9F2", "--osd-shadow-offset=1",
                f"--script-opts-append=uosc-color={colors}",
                f"--script-opts-append=uosc-chapter_ranges=openings:{second}55,endings:{second}55,ads:ff6b8166"]
    return ["--script-opts-append=osc-layout=bottombar", "--script-opts-append=osc-seekbarstyle=bar",
            "--script-opts-append=osc-hidetimeout=900", "--script-opts-append=osc-fadeduration=150",
            "--osd-font-size=30", "--osd-border-size=1.5"]


def script_args(skip_intro, theme="violet"):
    """Loads the opening/ending skipper (Shift+S toggles it while watching) and the player's look."""
    args = ui_args(theme)
    if SKIP_SCRIPT.is_file():
        args += [f"--script={SKIP_SCRIPT}", f"--script-opts-append=watchlist-skip={'yes' if skip_intro else 'no'}"]
    return args


_started = []    # every mpv this app started (so quitting the app closes them all)


def kill_children():
    """End every player window this app started that is still open."""
    for proc in list(_started):
        try:
            if proc.poll() is None:
                proc.kill()
        except OSError:
            pass


def web_log():
    """Where the player writes what happened, for videos from your own sites (so a
    stream that won't play can say why)."""
    path = Path(tempfile.gettempdir()) / "watchanything-mpv.log"
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    return str(path)


def why_it_stopped(session):
    """A short reason from the player's log when it closed without playing ("" if unknown)."""
    try:
        lines = (Path(tempfile.gettempdir()) / "watchanything-mpv.log").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for line in reversed(lines[-200:]):
        m = re.search(r"HTTP error (\d{3})|(\d{3}) (?:Forbidden|Not Found|Unauthorized)", line)
        if m:
            code = m.group(1) or m.group(2)
            return {"403": "the site refused it (403 - it may only play on its own page)", "404": "it's gone (404)",
                    "401": "it needs a sign-in (401)"}.get(code, f"the site answered {code}")
    for line in reversed(lines[-200:]):
        if re.search(r"Failed to open|Failed to recognize file format|No video or audio streams|Errors when loading file", line):
            return line.split("]", 1)[-1].strip()[:140]
    return ""


def finished(pos, dur):
    """Seen enough of it to count as watched: 90% in, or into the last two minutes."""
    return bool(dur) and dur > 0 and pos is not None and (pos / dur >= 0.9 or dur - pos <= 120)


def resume_point(saved):
    """Where to start a file again, from its saved {"pos", "dur"} (0 = the start)."""
    if not saved:
        return 0
    pos, dur = saved.get("pos") or 0, saved.get("dur") or 0
    if pos < RESUME_FROM or finished(pos, dur):
        return 0
    return max(0, pos - 5)


class _Connection:
    """mpv's JSON IPC: a named pipe on Windows, a Unix socket elsewhere."""

    def __init__(self, address, timeout=20):
        deadline = time.monotonic() + timeout
        while True:
            try:
                if sys.platform == "win32":
                    self._file = open(address, "r+b", buffering=0)  # noqa: SIM115
                    self._sock = None
                else:
                    self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    self._sock.connect(address)
                    self._file = self._sock.makefile("rb")
                return
            except OSError:
                if self._sock_open():
                    self._sock.close()
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.1)

    def _sock_open(self):
        return getattr(self, "_sock", None) is not None

    def send(self, *commands):
        # Everything is sent up front: on Windows, writing to the pipe while a
        # read is waiting on it can block, so after this we only read.
        data = b"".join(json.dumps({"command": c}).encode() + b"\n" for c in commands)
        if self._sock:
            self._sock.sendall(data)
        else:
            self._file.write(data)

    def messages(self):
        while True:
            try:
                line = self._file.readline()
            except (OSError, ValueError):
                return
            if not line:
                return
            try:
                yield json.loads(line)
            except ValueError:
                continue

    def close(self):
        for thing in (self._file, self._sock):
            try:
                if thing:
                    thing.close()
            except OSError:
                pass


class Session:
    """One run of mpv: a playlist of entries, followed until mpv closes.

    entries: [{"path", "sub", "key", "label"}] where key identifies what the
    file is ("1:3" for season 1 episode 3, "movie" for a film).
    """

    def __init__(self, mpv, item_id, entries, start=0, extra_args=(), on_progress=None,
                 on_finished=None, on_exit=None, audio_langs="", first=0, playlist=False):
        """`playlist`: every entry goes to mpv as one playlist (a whole season, each found
        when it opens), starting at entries[first]; titles and subtitles are set as each opens."""
        self.mpv, self.item_id, self.entries = mpv, item_id, entries
        self.playlist, self._seek_to = playlist, (start if playlist and start else 0)
        self.start_at, self.extra_args, self.audio_langs = start, list(extra_args), audio_langs
        self.on_progress = on_progress or (lambda *a: None)
        self.on_finished = on_finished or (lambda *a: None)
        self.on_exit = on_exit or (lambda *a: None)
        self.index, self.pos, self.dur, self.paused = (first if playlist else 0), None, None, False
        self.pos_at = 0.0            # when pos was last reported (time.monotonic())
        self.listeners = []          # called with every message from mpv (watch parties)
        self._pending_sub = None
        self.loaded = False          # the current entry's file is open (its extra tracks can be added)
        self.connected, self.error, self.ended = False, "", False
        self.done = set()
        self.proc = None
        self._last_save = 0.0
        tag = uuid.uuid4().hex[:10]
        self.address = (rf"\\.\pipe\watchlist-mpv-{tag}" if sys.platform == "win32"
                        else os.path.join(tempfile.gettempdir(), f"watchlist-mpv-{tag}.sock"))

    def command(self):
        base = list(self.mpv) if isinstance(self.mpv, (list, tuple)) else [self.mpv]
        cmd = base + [f"--input-ipc-server={self.address}", "--force-window=immediate",
               "--keep-open=no", "--idle=no", "--save-position-on-quit=no", "--terminal=no",
               "--sub-auto=fuzzy", "--slang=en,eng,english", "--autofit-larger=90%x90%",
               "--title=${media-title} - Watch Anything",
               # Video sites' chunks can be named like pictures (.image, .jpg...): newer FFmpeg
               # (the Mac's) turns those down unless told not to.
               "--demuxer-lavf-o-append=extension_picky=0"]
        if self.audio_langs:
            cmd.append(f"--alang={self.audio_langs}")
        cmd += self.extra_args
        if self.playlist:
            listing = Path(tempfile.gettempdir()) / f"watchanything-{self.address[-10:]}.m3u"
            lines = ["#EXTM3U"]
            for entry in self.entries:
                lines += [f"#EXTINF:-1,{(entry.get('label') or '').replace(chr(10), ' ')}", entry["path"]]
            listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return cmd + [f"--playlist={listing}", f"--playlist-start={self.index}"]
        for i, entry in enumerate(self.entries):
            cmd.append("--{")
            if entry.get("label"):
                cmd.append(f"--force-media-title={entry['label']}")
            if entry.get("sub"):
                cmd.append(f"--sub-file={entry['sub']}")
            if i == 0 and self.start_at:
                cmd.append(f"--start={int(self.start_at)}")
            cmd += [entry["path"], "--}"]
        return cmd

    def start(self):
        kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                  "stderr": subprocess.DEVNULL}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        self.proc = subprocess.Popen(self.command(), **kwargs)
        _started.append(self.proc)
        threading.Thread(target=self._follow, daemon=True).start()
        return self

    # ---- following playback

    def current(self):
        return self.entries[self.index] if 0 <= self.index < len(self.entries) else None

    def _save(self, force=False):
        entry = self.current()
        if entry and self.pos is not None and (force or time.monotonic() - self._last_save >= SAVE_EVERY):
            self._last_save = time.monotonic()
            self.on_progress(self.item_id, entry, self.pos, self.dur)

    def _wrap_up_current(self, reached_end=False):
        entry = self.current()
        if not entry or entry["key"] in self.done:
            return
        if reached_end or finished(self.pos, self.dur):
            self.done.add(entry["key"])
            self.on_finished(self.item_id, entry)
        else:
            self._save(force=True)

    def _follow(self):
        conn = None
        try:
            conn = _Connection(self.address)
            self.connected = True
            conn.send(["observe_property", 1, "time-pos"], ["observe_property", 2, "duration"],
                      ["observe_property", 3, "playlist-pos"], ["observe_property", 4, "pause"])
            for msg in conn.messages():
                event = msg.get("event")
                if event == "property-change":
                    name, data = msg.get("name"), msg.get("data")
                    if name == "time-pos" and data is not None:
                        self.pos, self.pos_at = float(data), time.monotonic()
                        self._save()
                    elif name == "duration" and data is not None:
                        self.dur = float(data)
                    elif name == "pause":
                        if self.pos is not None:
                            # Estimates of where playback is restart from here.
                            self.pos, self.pos_at = self.position(), time.monotonic()
                        self.paused = bool(data)
                        if self.paused:
                            self._save(force=True)
                    elif name == "playlist-pos" and getattr(self, "_swapping", None) is not None and isinstance(data, int):
                        # (a file of the same episode coming in: still that episode)
                        if data == self._swapping:
                            self._swapping = None
                        self.pos, self.dur = None, None
                    elif name == "playlist-pos" and isinstance(data, int) and data >= 0 and data != self.index:
                        self._wrap_up_current()
                        self.index, self.pos, self.dur, self.loaded = data, None, None, False
                elif event == "end-file" and msg.get("reason") == "eof":
                    self._wrap_up_current(reached_end=True)
                elif event == "file-loaded":
                    self._loaded()
                for listener in list(self.listeners):
                    try:
                        listener(self, msg)
                    except Exception:  # noqa: BLE001 - a listener mustn't stop playback being followed
                        pass
        except OSError as e:
            self.error = f"couldn't connect to the player ({e})"
        finally:
            if conn:
                conn.close()
            if self.proc:
                try:
                    self.proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
            self._wrap_up_current()
            self.ended = True
            if sys.platform != "win32":
                try:
                    os.unlink(self.address)
                except OSError:
                    pass
            self.on_exit(self)

    def position(self):
        """Where playback is now (moving on from the last report while playing)."""
        if self.pos is None:
            return None
        if self.paused:
            return self.pos
        return self.pos + max(0.0, time.monotonic() - self.pos_at)

    def send(self, *commands):
        """Send commands to mpv on a connection of their own. (On Windows the
        following connection only reads, so commands can't go through it.)"""
        if self.ended or not self.connected:
            return False
        try:
            conn = _Connection(self.address, timeout=3)
        except OSError:
            return False
        try:
            conn.send(*commands)
            if sys.platform != "win32":
                time.sleep(0.05)   # let mpv read them before the socket closes
            return True
        except OSError:
            return False
        finally:
            conn.close()

    def _loaded(self):
        """A file opened: its subtitles and other versions (added later) go in."""
        entry, cmds = self.current(), []
        if self._pending_sub:
            cmds.append(["sub-add", self._pending_sub, "select"])
            self._pending_sub = None
        if self._seek_to:
            cmds.append(["seek", int(self._seek_to), "absolute"])        # (where you stopped, the first one only)
            self._seek_to = 0
        if entry and (entry.get("appended") or self.playlist):
            if entry.get("label"):
                cmds.append(["set_property", "force-media-title", entry["label"]])
            if entry.get("sub"):
                cmds.append(["sub-add", entry["sub"], "select"])
        for track in (entry or {}).get("extras") or []:
            cmds.append(_track_command(track))
        self.loaded = True
        if cmds:
            self.send(*cmds)

    def add_tracks(self, entry, tracks):
        """Other versions of an entry: [{"kind": "audio" / "sub", "url", "title", "lang"}].
        Added to the open file now (when it's the one playing), and whenever it opens."""
        entry.setdefault("extras", []).extend(tracks)
        if entry is self.current() and self.loaded:
            return self.send(*[_track_command(t) for t in tracks])
        return True

    def append(self, entry):
        """One more entry at the end of the playlist (the next episode, found meanwhile)."""
        entry = dict(entry, appended=True)
        self.entries.append(entry)
        if not self.send(["loadfile", entry["path"], "append"]):
            self.entries.pop()
            return None
        return entry

    def swap(self, entry, at=0):
        """The episode playing, swapped for another file of it (its dub, say) from where it is."""
        i = self.index
        entry = dict(entry, appended=True)
        self._swapping = i
        self.entries[i] = entry
        self.loaded = False
        return self.send(["loadfile", entry["path"], "insert-next-play", -1, f"start={max(0, int(at))}"],
                         ["playlist-remove", i])

    def replace(self, entry):
        """Play a different file in the same window (a watch party moving on)."""
        self._wrap_up_current()
        self.entries, self.index, self.pos, self.dur = [entry], 0, None, None
        self._pending_sub = entry.get("sub")
        cmds = [["set_property", "force-media-title", entry.get("label") or Path(entry["path"]).name],
                ["loadfile", entry["path"], "replace"]]
        return self.send(*cmds)

    def stop(self):
        """Close the player window (saving your place first)."""
        self._save(force=True)
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def status(self):
        entry = self.current()
        return {"active": not self.ended, "item_id": self.item_id,
                "label": entry.get("label") if entry else "", "key": entry["key"] if entry else "",
                "file": Path(entry["path"]).name if entry else "", "pos": self.pos, "dur": self.dur,
                "paused": self.paused, "connected": self.connected, "error": self.error}


def _track_command(track):
    """mpv's command adding an audio or subtitle track from another file, not chosen
    (you switch to it in the player)."""
    return ["audio-add" if track["kind"] == "audio" else "sub-add", track["url"],
            "select" if track.get("select") else "auto", track.get("title") or "", track.get("lang") or ""]


class Player:
    """At most one built-in player window at a time."""

    def __init__(self):
        self._lock = threading.Lock()
        self.session = None

    def play(self, session):
        with self._lock:
            if self.session and not self.session.ended:
                self.session.stop()
            self.session = session.start()
            return self.session

    def take(self):
        """Stop the current session and hand back where it was (for switching to VLC)."""
        with self._lock:
            session = self.session
            if not session or session.ended:
                return None
            session.stop()
            return session

    def status(self):
        with self._lock:
            return self.session.status() if self.session else {"active": False}
