"""The built-in player: mpv, started and followed by the app.

mpv plays in its own window. The app talks to it over mpv's JSON IPC
connection (a named pipe on Windows, a Unix socket on a Mac) to learn which
file is playing and how far in you are, so it can save your place, resume
later and mark an episode watched when you reach its end.
"""

import json
import os
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


def script_args(skip_intro):
    """Loads the opening/ending skipper (Shift+S toggles it while watching)."""
    if not SKIP_SCRIPT.is_file():
        return []
    return [f"--script={SKIP_SCRIPT}", f"--script-opts=watchlist-skip={'yes' if skip_intro else 'no'}"]


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
                 on_finished=None, on_exit=None, audio_langs=""):
        self.mpv, self.item_id, self.entries = mpv, item_id, entries
        self.start_at, self.extra_args, self.audio_langs = start, list(extra_args), audio_langs
        self.on_progress = on_progress or (lambda *a: None)
        self.on_finished = on_finished or (lambda *a: None)
        self.on_exit = on_exit or (lambda *a: None)
        self.index, self.pos, self.dur, self.paused = 0, None, None, False
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
               "--title=${media-title} - Watchlist"]
        if self.audio_langs:
            cmd.append(f"--alang={self.audio_langs}")
        cmd += self.extra_args
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
                        self.pos = float(data)
                        self._save()
                    elif name == "duration" and data is not None:
                        self.dur = float(data)
                    elif name == "pause":
                        self.paused = bool(data)
                        if self.paused:
                            self._save(force=True)
                    elif name == "playlist-pos" and isinstance(data, int) and data >= 0 and data != self.index:
                        self._wrap_up_current()
                        self.index, self.pos, self.dur = data, None, None
                elif event == "end-file" and msg.get("reason") == "eof":
                    self._wrap_up_current(reached_end=True)
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
