"""Videos whose pieces are dressed up as pictures.

Some sites keep each piece (chunk) of a video on an image server, with a tiny picture's
bytes put in front of the video's own. A browser's player skips them; newer FFmpeg (the
Mac's mpv) takes the piece for a picture and gives up. Such a video is played through the
app itself: its playlists pointed at the app, each piece passed on without the picture.
"""

import re
import threading
import time
import urllib.parse
import urllib.request
import uuid

PICTURES = (b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"BM", b"RIFF")
_lock = threading.Lock()
_streams = {}            # id -> {"headers", "at"}
_hosts = {}              # a playlist's server -> (whether its pieces need it, when that was found)


def _headers(found_headers, referer):
    h = {k: v for k, v in (found_headers or {}).items() if k in ("User-Agent", "Origin", "Cookie")}
    if referer:
        h["Referer"] = referer
    h.setdefault("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/130.0 Safari/537.36")
    return h


def _get(url, headers, limit=None, timeout=20):
    req = urllib.request.Request(url, headers=dict(headers, **({"Range": f"bytes=0-{limit - 1}"} if limit else {})))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(limit) if limit else r.read()


def _uris(text):
    """The addresses a playlist points to (its lines, and URI="..." in its tags)."""
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            yield line
        for m in re.finditer(r'URI="([^"]+)"', line):
            yield m.group(1)


def _first_piece(url, headers):
    """The first piece of a video's playlist (the first variant's, for a master playlist)."""
    for _ in range(3):
        text = _get(url, headers).decode("utf-8", "replace")
        if not text.lstrip().startswith("#EXTM3U"):
            return None
        lines = [x.strip() for x in text.splitlines() if x.strip() and not x.startswith("#")]
        if not lines:
            return None
        nxt = urllib.parse.urljoin(url, lines[0])
        if "#EXT-X-STREAM-INF" in text:
            url = nxt
            continue
        return nxt
    return None


def needs_fix(url, found_headers, referer):
    """Whether this video's pieces are dressed up as pictures (looked at once per server)."""
    if ".m3u8" not in url.lower():
        return False
    host = urllib.parse.urlsplit(url).netloc
    with _lock:
        known = _hosts.get(host)
    if known and time.time() - known[1] < 3600:
        return known[0]
    headers = _headers(found_headers, referer)
    try:
        piece = _first_piece(url, headers)
        head = _get(piece, headers, limit=2048) if piece else b""
    except Exception:  # noqa: BLE001 - can't tell: played as it is
        return False
    fix = head.startswith(PICTURES) and _sync(head) is not None
    with _lock:
        _hosts[host] = (fix, time.time())
    return fix


def local_url(base, url, found_headers, referer):
    """The address to give the player: the app's own, for `url` (base: http://127.0.0.1:port/hls/token)."""
    sid = uuid.uuid4().hex[:12]
    with _lock:
        for old in [k for k, v in _streams.items() if time.time() - v["at"] > 12 * 3600]:
            del _streams[old]
        _streams[sid] = {"headers": _headers(found_headers, referer), "at": time.time()}
    return _local(base, sid, url)


def _local(base, sid, url):
    name = urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1] or "x"
    ext = ".m3u8" if name.lower().endswith(".m3u8") else ".ts"
    # (the end says what it is: the player picks its reader by it)
    return f"{base}/{sid}/v{ext}?u={urllib.parse.quote(url, safe='')}"


def _sync(data):
    """Where the video's own bytes (MPEG-TS) start, after a picture in front of them."""
    for i in range(min(len(data) - 376, 65536)):
        if data[i] == 0x47 and data[i + 188] == 0x47 and data[i + 376] == 0x47:
            return i
    return None


def serve(sid, url, base):
    """(content type, body) for a playlist or piece asked for through the app, or None."""
    with _lock:
        s = _streams.get(sid)
    if not s:
        return None
    data = _get(url, s["headers"], timeout=60)
    if data.lstrip()[:7] == b"#EXTM3U":
        text = data.decode("utf-8", "replace")

        def point(u):
            return _local(base, sid, urllib.parse.urljoin(url, u))
        out = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                line = point(stripped)
            else:
                line = re.sub(r'URI="([^"]+)"', lambda m: f'URI="{point(m.group(1))}"', line)
            out.append(line)
        return "application/vnd.apple.mpegurl", ("\n".join(out) + "\n").encode("utf-8")
    if data.startswith(PICTURES):
        start = _sync(data)
        if start is not None:
            data = data[start:]
    return "video/mp2t", data


def playable(found, base):
    """found["url"], or the app's address for it (base as for local_url)."""
    if needs_fix(found["url"], found.get("headers"), found.get("referer")):
        return local_url(base, found["url"], found.get("headers"), found.get("referer"))
    return found["url"]


def test_server():
    """For the live checks (no app running): a server of its own for these addresses; its base."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            m = re.fullmatch(r"/hls/t/(\w+)/v\.(?:m3u8|ts)", url.path)
            target = urllib.parse.parse_qs(url.query).get("u", [""])[-1]
            try:
                got = serve(m.group(1), target, base) if m else None
            except Exception:  # noqa: BLE001
                got = None
            if not got:
                self.send_response(404)
                self.end_headers()
                return
            try:
                self.send_response(200)
                self.send_header("Content-Type", got[0])
                self.send_header("Content-Length", str(len(got[1])))
                self.end_headers()
                self.wfile.write(got[1])
            except OSError:
                pass                     # (the player stopped reading: nothing more to send)

        def log_message(self, *a):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/hls/t"
    return base
