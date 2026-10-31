"""Finding a video yourself: a site's page opened in a browser window the app watches.

When the app can't find an episode's video (or finds the wrong one), you open its page
in the app's browser and play it there the way you would anyway - choosing the server,
clicking play. Every video the page asks for is kept (not just the first), each with
what tells them apart: where it came from (the player's site), how long it is and how
good, and the button pressed just before it. You pick the right one and it plays in the
app's player like any other.

The app learns from your pick, for that site: the player's site the video came from,
the server button you pressed, the video's own server. From then on the video finder
looks for that first (webtools.LEARNED), so next time it finds the right one itself.

The window: an Edge window on Windows (webtools.watch_window), an ordinary Chrome window
elsewhere on a computer (cdp.watch), the phone app's own page on a phone (droidweb.watch).
"""

import re
import threading
import time
import urllib.parse
import urllib.request
import uuid

_jobs = {}
_lock = threading.Lock()

# What the page's own player asks for that isn't the video (its pieces, sounds, captions, ads).
NOT_VIDEO = re.compile(r"\.(ts|m4s|aac|key|vtt|srt|mp3|m4a|ogg|oga|opus|wav|flac|jpg|jpeg|png|gif|webp|js|css)(\?|$)", re.I)

# The click tracker: each click on a button or link in the page is told to the app (an
# address no site has), so the app knows what was pressed before a video came.
from .webtools import PRESSED as CLICKED_ON  # noqa: E402
TRACK_JS = r"""(() => {
  if (window.__wlTrack) return; window.__wlTrack = true;
  document.addEventListener('click', (e) => {
    try {
      const el = e.target.closest('button, a, [role=button], [role=tab], [role=option], li, [data-id], [data-server], [onclick]');
      if (!el) return;
      const label = (el.innerText || el.getAttribute('title') || el.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ').slice(0, 40);
      if (label) new Image().src = '__CLICKED_ON__' + encodeURIComponent(label);
    } catch (err) {}
  }, true);
})();""".replace("__CLICKED_ON__", CLICKED_ON)


def _host(url):
    return urllib.parse.urlsplit(url or "").netloc.lower()


class Job:
    def __init__(self, item_id, season, episode, site_id, url):
        self.id = uuid.uuid4().hex[:10]
        self.item_id, self.season, self.episode, self.site_id, self.url = item_id, season, episode, site_id, url
        self.videos, self.subs, self.clicks = [], [], []
        self.page = url                 # the page shown now (as far as the app saw it)
        self.open, self.error, self.started = True, "", time.time()
        self.closer = None

    # what the window tells the job

    def video(self, url, headers, page=""):
        low = url.lower()
        if not url.startswith("http") or NOT_VIDEO.search(urllib.parse.urlsplit(url).path) \
                or re.search(r"imdb-video|media-imdb|youtube|googlevideo|ytimg", low):
            return
        with _lock:
            if any(v["url"] == url for v in self.videos):
                return
            ref = headers.get("Referer") or ""
            entry = {"n": len(self.videos) + 1, "url": url, "headers": {k: v for k, v in headers.items() if v},
                     "host": _host(url), "frame": _host(ref) or _host(page or self.page), "page": page or self.page,
                     "after": self.clicks[-1][1] if self.clicks and time.time() - self.clicks[-1][0] < 60 else "",
                     "at": time.time(), "kind": "playlist" if ".m3u8" in low or ".mpd" in low else "file",
                     "length": None, "quality": ""}
            self.videos.append(entry)
        threading.Thread(target=_describe, args=(entry,), daemon=True).start()

    def subtitle(self, url):
        with _lock:
            if url not in self.subs:
                self.subs.append(url)

    def clicked(self, label):
        with _lock:
            self.clicks.append((time.time(), label))

    def closed(self):
        self.open = False

    def view(self):
        return {"id": self.id, "open": self.open, "error": self.error, "page": self.page, "url": self.url,
                "videos": [{k: v[k] for k in ("n", "host", "frame", "after", "kind", "length", "quality", "at")}
                           for v in self.videos]}

    def close(self):
        self.open = False
        if self.closer:
            try:
                self.closer()
            except Exception:  # noqa: BLE001 - closed already
                pass


def _describe(entry):
    """How long and how good a video is (an ad is short, the episode isn't), looked at
    inside: a playlist's length and best quality, a file's size."""
    headers = {k: v for k, v in entry["headers"].items() if k in ("Referer", "Origin", "User-Agent", "Cookie")}
    headers.setdefault("User-Agent", "Mozilla/5.0")

    def get(u, limit=2_000_000):
        req = urllib.request.Request(u, headers=dict(headers, Range=f"bytes=0-{limit - 1}"))
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read(limit), r.headers
    try:
        body, info = get(entry["url"])
        if not body.lstrip()[:7] == b"#EXTM3U":
            size = re.search(r"/(\d+)$", info.get("Content-Range") or "") or None
            total = int(size.group(1)) if size else int(info.get("Content-Length") or 0)
            if total:
                entry["quality"] = f"{total / 1e9:.1f} GB" if total >= 1e9 else f"{total / 1e6:.0f} MB"
            return
        entry["kind"] = "playlist"
        text = body.decode("utf-8", "replace")
        heights = [int(h) for h in re.findall(r"RESOLUTION=\d+x(\d+)", text)]
        if heights:
            entry["quality"] = f"{max(heights)}p"
            first = next((x.strip() for x in text.splitlines() if x.strip() and not x.startswith("#")), "")
            text = get(urllib.parse.urljoin(entry["url"], first))[0].decode("utf-8", "replace") if first else ""
        total = sum(float(x) for x in re.findall(r"#EXTINF:([\d.]+)", text))
        if total:
            entry["length"] = round(total)
    except Exception:  # noqa: BLE001 - shown without it
        pass


def start(item_id, season, episode, site_id, url):
    """Open `url` in a watched browser window. The job, or an error message."""
    from . import cdp, droidweb, launcher, webtools
    job = Job(item_id, season, episode, site_id, url)
    try:
        if launcher.ANDROID and droidweb.available():
            job.closer = droidweb.watch(url, job)
        elif webtools._chromium():
            job.closer = cdp.watch(url, job)
        elif launcher.can_open_windows():
            job.closer = webtools.watch_window(url, job)
        else:
            raise RuntimeError("this needs the app's own window (it's running in a browser)")
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"couldn't open the browser: {e}") from e
    with _lock:
        for old in [k for k, j in _jobs.items() if not j.open and time.time() - j.started > 3600]:
            del _jobs[old]
        _jobs[job.id] = job
    return job


def get(job_id):
    return _jobs.get(job_id)


def recipe(job, entry):
    """What to remember about the site from your pick."""
    return {"frame": entry["frame"], "video": entry["host"], "label": entry["after"], "at": time.time()}
