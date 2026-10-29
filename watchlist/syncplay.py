"""Watch-party invites.

A watch party is a room on a Syncplay server (syncplay.pl's free public one by
default); party.py talks to it. An invite code carries the server, the room,
the host's name and what's being watched, so the other person's app can join
the room and find the same episode in its own downloads.
"""

import base64
import binascii
import getpass
import json
import re
import secrets

DEFAULT_SERVER = "syncplay.pl:8999"
PREFIX = "WLP1."


def default_name():
    try:
        name = getpass.getuser()
    except Exception:  # noqa: BLE001 - no user name is fine
        name = ""
    return re.sub(r"[^\w .-]", "", name)[:20] or "Watcher"


def new_room():
    return "watchanything-" + secrets.token_hex(5)


def make_invite(server, room, item=None, season=None, episode=None, host=None, now=None):
    """An invite to a room (and, with item, what it was started for). `now` is
    what the host is playing (with a torrent's magnet and exact file), so a guest's
    app starts getting it ready the moment it joins."""
    data = {"s": server, "r": room}
    if now:
        data["now"] = {k: now[k] for k in ("src", "id", "type", "title", "season", "episode", "key", "label",
                                            "magnet", "th", "ti", "peer") if now.get(k) not in (None, "")}
    if item:
        data.update({"t": item["title"], "y": item.get("year"), "k": item["type"],
                     "src": item["source"], "id": item["source_id"]})
    if host:
        data["h"] = host
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
    if not isinstance(data, dict) or not data.get("r"):
        raise ValueError("that watch-party code is incomplete - copy all of it")
    server = str(data.get("s") or DEFAULT_SERVER)
    if not re.fullmatch(r"[A-Za-z0-9.-]+(:\d{1,5})?", server):
        raise ValueError("that watch-party code has a strange server in it")
    room = str(data["r"])
    if not re.fullmatch(r"[\w.-]{1,64}", room):
        raise ValueError("that watch-party code has a strange room in it")
    out = {"server": server, "room": room, "title": str(data.get("t") or ""), "year": data.get("y"),
           "type": data.get("k") if data.get("k") in ("tv", "anime", "movie") else "",
           "source": str(data.get("src") or ""), "source_id": str(data.get("id") or ""),
           "host": str(data.get("h") or "") or None}
    if data.get("ep"):
        out["season"], out["episode"] = int(data.get("se") or 1), int(data["ep"])
    now = data.get("now")
    if isinstance(now, dict) and str(now.get("magnet") or "").startswith("magnet:?"):
        out["now"] = now
    return out
