"""Watch together with Syncplay (syncplay.pl, free).

Syncplay keeps two (or more) players in step over the internet: when one of
you pauses, seeks or starts, so does everyone else. Each of you plays your own
copy of the file. The host starts a private room and gets an invite code; the
other person pastes the code into their app, which finds the same title and
episode in their downloads and joins the room.
"""

import base64
import binascii
import getpass
import json
import os
import re
import secrets
import shutil
import sys
from pathlib import Path

DEFAULT_SERVER = "syncplay.pl:8999"
PREFIX = "WLP1."


def find_syncplay(configured=""):
    """Syncplay's program, or ""."""
    candidates = []
    if configured:
        p = Path(configured)
        if p.suffix == ".app":
            p = p / "Contents" / "MacOS" / "Syncplay"
        candidates.append(p)
    if sys.platform == "win32":
        for env in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
            if os.environ.get(env):
                base = Path(os.environ[env])
                candidates += [base / "Syncplay" / "Syncplay.exe", base / "Programs" / "Syncplay" / "Syncplay.exe"]
    elif sys.platform == "darwin":
        candidates += [Path("/Applications/Syncplay.app/Contents/MacOS/Syncplay"),
                       Path.home() / "Applications" / "Syncplay.app" / "Contents" / "MacOS" / "Syncplay"]
    for c in candidates:
        if c.is_file():
            return str(c)
    return shutil.which("syncplay") or ""


def default_name():
    try:
        name = getpass.getuser()
    except Exception:  # noqa: BLE001 - no user name is fine
        name = ""
    return re.sub(r"[^\w .-]", "", name)[:20] or "Watchlist"


def new_room():
    return "watchlist-" + secrets.token_hex(5)


def make_invite(server, room, item, season=None, episode=None):
    data = {"s": server, "r": room, "t": item["title"], "y": item.get("year"), "k": item["type"],
            "src": item["source"], "id": item["source_id"]}
    if episode:
        data["se"], data["ep"] = season, episode
    raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return PREFIX + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def read_invite(code):
    """The invite's contents, or ValueError."""
    code = re.sub(r"\s+", "", str(code or ""))
    m = re.search(r"WLP1\.([A-Za-z0-9_-]+)", code)
    if not m:
        raise ValueError("that isn't a watch-party code - it starts with WLP1.")
    body = m.group(1)
    try:
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode("utf-8"))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        raise ValueError("that watch-party code is incomplete - copy all of it") from None
    if not isinstance(data, dict) or not data.get("r") or not data.get("t"):
        raise ValueError("that watch-party code is incomplete - copy all of it")
    server = str(data.get("s") or DEFAULT_SERVER)
    if not re.fullmatch(r"[A-Za-z0-9.-]+(:\d{1,5})?", server):
        raise ValueError("that watch-party code has a strange server in it")
    room = str(data["r"])
    if not re.fullmatch(r"[\w.-]{1,64}", room):
        raise ValueError("that watch-party code has a strange room in it")
    out = {"server": server, "room": room, "title": str(data["t"]), "year": data.get("y"),
           "type": data.get("k") if data.get("k") in ("tv", "anime", "movie") else "",
           "source": str(data.get("src") or ""), "source_id": str(data.get("id") or "")}
    if data.get("ep"):
        out["season"], out["episode"] = int(data.get("se") or 1), int(data["ep"])
    return out


def command(syncplay, server, name, room, player_path, video, subtitle=""):
    """The command that opens Syncplay with everything filled in."""
    cmd = [syncplay, "-a", server, "-n", name or default_name(), "-r", room]
    if player_path:
        cmd += ["--player-path", player_path]
    cmd.append(video)
    if subtitle:
        cmd += ["--", f"--sub-file={subtitle}"]
    return cmd
