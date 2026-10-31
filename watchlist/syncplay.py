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
# Syncplay's free public servers (some ports are sometimes down: the next one is used).
PUBLIC_SERVERS = ("syncplay.pl:8999", "syncplay.pl:8998", "syncplay.pl:8996", "syncplay.pl:8995", "syncplay.pl:8997")


def default_name():
    try:
        name = getpass.getuser()
    except Exception:  # noqa: BLE001 - no user name is fine
        name = ""
    if re.fullmatch(r"u\d+_a\d+", name or ""):
        # A phone: its user name is an internal one ("u0_a130"); your name is set in Settings.
        name = "Phone"
    return re.sub(r"[^\w .-]", "", name)[:20] or "Watcher"


def new_room():
    return "watchanything-" + secrets.token_hex(5)


def answers(server, timeout=6, stay=5.0):
    """Whether a watch-party server really works now: it's asked for an encrypted connection,
    a made-up empty room is joined as a Syncplay app joins one, and the connection is kept
    `stay` seconds, the server having to keep talking and not cut it off (syncplay.pl:8999
    sometimes takes a connection and drops it at once)."""
    import socket
    import ssl
    import time
    host, _, port = server.partition(":")
    try:
        sock = socket.create_connection((host, int(port or 8999)), timeout=timeout)
    except (OSError, ValueError):
        return False
    try:
        sock.settimeout(timeout)
        buf = b""

        def read():
            nonlocal buf
            while b"\n" not in buf:
                chunk = sock.recv(4096)
                if not chunk:
                    raise OSError("closed")
                buf += chunk
            line, buf = buf.split(b"\n", 1)
            return json.loads(line.decode("utf-8") or "{}")

        def send(msg):
            sock.sendall(json.dumps(msg).encode() + b"\r\n")
        send({"TLS": {"startTLS": "send"}})
        reply = read()
        if "true" in str((reply.get("TLS") or {}).get("startTLS", "")):
            try:
                from .metadata import ssl_context
                context = ssl_context()
            except Exception:  # noqa: BLE001
                context = ssl.create_default_context()
            sock = context.wrap_socket(sock, server_hostname=host)
            sock.settimeout(timeout)
            buf = b""
        send({"Hello": {"username": "wa-check-" + secrets.token_hex(3), "room": {"name": "wa-check-" + secrets.token_hex(5)},
                        "version": "1.2.255", "realversion": "1.7.7", "features": {}}})
        deadline = time.time() + timeout
        while "Hello" not in reply:
            if time.time() > deadline or "Error" in reply:
                return False
            reply = read()
        # Joined: stays on `stay` seconds, the server still answering.
        until = time.time() + stay
        heard = 0
        sock.settimeout(1.5)
        while time.time() < until:
            try:
                msg = read()
            except socket.timeout:
                send({"State": {"ping": {"clientLatencyCalculation": time.time(), "clientRtt": 0}}})
                continue
            heard += 1
            if "Error" in msg:
                return False
            if "State" in msg:
                send({"State": {"ping": {"clientLatencyCalculation": time.time(), "clientRtt": 0,
                                         "latencyCalculation": (msg["State"].get("ping") or {}).get("latencyCalculation")}}})
        return heard > 0
    except (OSError, ValueError, ssl.SSLError):
        return False
    finally:
        try:
            sock.close()
        except OSError:
            pass


def working_server(servers=None, stay=5.0):
    """The first of `servers` (in order) that really works now, all checked at once; "" if none."""
    import threading
    servers = tuple(servers or PUBLIC_SERVERS)
    results, done = {}, threading.Event()

    def check(server):
        results[server] = answers(server, stay=stay)
        if all(results.get(x) is not None for x in servers) or \
                any(results.get(x) for x in servers[:servers.index(server) + 1]):
            done.set()
    for server in servers:
        threading.Thread(target=check, args=(server,), daemon=True).start()
    done.wait(20)
    for server in servers:
        if results.get(server) is None:
            continue
        if results[server]:
            # (the first in order that works - earlier ones having been checked)
            if all(results.get(x) is not None for x in servers[:servers.index(server)]):
                return server
    # Still being checked: the first working one so far.
    return next((x for x in servers if results.get(x)), "")


# A party's invite: "WA-" and the room's own 8 letters ("WA-K7QD-2M9X"), plus "@server" for a
# server of your own. It stays the same for the whole party: what's playing (a file, a magnet,
# your site) reaches whoever joins from the room itself, so anyone can join with it at any time.
SHORT = re.compile(r"\bWA-([A-Z2-7]{4})-?([A-Z2-7]{4})(?:-([5-8])\b)?(?:@([A-Za-z0-9.-]+(?::\d{1,5})?))?", re.I)


# A party through the backup relay: its key in the invite (only who has the invite can read
# or join it): "WA-" + 16 letters + "-R" and which relay ("WA-ABCD-EFGH-IJKL-MNOP-R1").
RELAY = re.compile(r"\bWA-([A-Z2-7]{4})-?([A-Z2-7]{4})-?([A-Z2-7]{4})-?([A-Z2-7]{4})-R(\d)\b", re.I)


def relay_invite(secret, index):
    letters = base64.b32encode(secret).decode("ascii")
    return "WA-" + "-".join(letters[i:i + 4] for i in range(0, 16, 4)) + f"-R{index}"


def short_invite(server, room):
    """The short invite for a room made by new_room() ("" for any other room)."""
    m = re.fullmatch(r"watchanything-([0-9a-f]{10})", room or "")
    if not m:
        return ""
    letters = base64.b32encode(bytes.fromhex(m.group(1))).decode("ascii")
    code = f"WA-{letters[:4]}-{letters[4:]}"
    m = re.fullmatch(r"syncplay\.pl:899([5-8])", server)
    if m:
        return f"{code}-{m.group(1)}"          # (another public port: one more digit)
    return code if server == DEFAULT_SERVER else f"{code}@{server}"


def make_invite(server, room, item=None, season=None, episode=None, host=None, now=None):
    """An invite to a room (and, with item, what it was started for). `now` is
    what the host is playing (a torrent's magnet and exact file, or the video site
    and its title there), so a guest's app starts getting it ready the moment it joins."""
    data = {"s": server, "r": room}
    if now:
        data["now"] = {k: now[k] for k in ("src", "id", "type", "title", "season", "episode", "key", "label",
                                            "magnet", "th", "ti", "peer", "url", "referer", "sub", "track",
                                            "site", "entry") if now.get(k) not in (None, "")}
        if now.get("headers"):
            # (never your cookies: only what says where the video is played from)
            data["now"]["headers"] = {k: v for k, v in now["headers"].items() if k in ("Origin", "User-Agent")}
    if item:
        data.update({"t": item["title"], "y": item.get("year"), "k": item["type"],
                     "src": item["source"], "id": item["source_id"]})
    if host:
        data["h"] = host
    if episode:
        data["se"], data["ep"] = season, episode
    raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return PREFIX + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def online_source(meta):
    """The video site of what the host plays from one ({"name", "url"}), or None."""
    site = (meta or {}).get("site")
    if isinstance(site, dict) and str(site.get("url") or "").lower().startswith(("http://", "https://")):
        return {"name": str(site.get("name") or "")[:40], "url": str(site["url"])}
    return None


def read_invite(code):
    """The invite's contents, or ValueError."""
    code = re.sub(r"\s+", "", str(code or ""))
    relayed = RELAY.search(code)
    if relayed:
        from . import relay
        try:
            secret = base64.b32decode("".join(relayed.group(i) for i in range(1, 5)).upper())
        except (binascii.Error, ValueError):
            raise ValueError("that watch-party code isn't right - check it letter by letter") from None
        index = int(relayed.group(5))
        if index >= len(relay.BROKERS):
            raise ValueError("that watch-party code is from a newer Watch Anything - update the app")
        return {"server": relay.server_name(secret, index), "room": relay.ROOM, "title": "", "year": None, "type": "",
                "source": "", "source_id": "", "host": None}
    short = SHORT.search(code)
    if short:
        try:
            room = "watchanything-" + base64.b32decode((short.group(1) + short.group(2)).upper()).hex()
        except (binascii.Error, ValueError):
            raise ValueError("that watch-party code isn't right - check it letter by letter") from None
        server = short.group(4) or (f"syncplay.pl:899{short.group(3)}" if short.group(3) else DEFAULT_SERVER)
        return {"server": server, "room": room, "title": "", "year": None, "type": "",
                "source": "", "source_id": "", "host": None}
    m = re.search(r"WLP1\.([A-Za-z0-9_-]+)", code)
    if not m:
        raise ValueError("that isn't a watch-party code - it starts with WA-")
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
    if isinstance(now, dict) and (str(now.get("magnet") or "").startswith("magnet:?") or online_source(now)):
        out["now"] = now
    return out
