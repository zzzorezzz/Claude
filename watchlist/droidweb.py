"""The video finder on a phone: pages open in a hidden WebView of the phone app
(MainActivity's Pages), watched the way cdp.py watches its Chromium - every
request any frame of the page makes, with its headers - so cdp's catch_video
and render work on a phone unchanged.

The phone's WebView reports requests, not responses: a player that asks for its
playlist at an address that doesn't say so is caught by MANIFEST_JS instead,
which looks at what the page's own fetch/XHR got back."""

import json
import re
import threading
import time

from . import cdp, launcher

# What a page's scripts got back, when it is a video playlist: told to the app by
# asking for MANIFEST + the playlist's address (an address no site has).
MANIFEST = "https://watchlist.invalid/manifest?u="
MANIFEST_JS = r"""(() => {
  if (window.__wlManifest) return; window.__wlManifest = true;
  const tell = (u, text) => {
    try {
      if (typeof text === 'string' && /^\s*(#EXTM3U|<\?xml[^>]*>\s*<MPD|<MPD)/.test(text.slice(0, 200)))
        new Image().src = '__MANIFEST__' + encodeURIComponent(new URL(u, location.href).href);
    } catch (e) {}
  };
  const f = window.fetch;
  if (f) window.fetch = function (...a) {
    return f.apply(this, a).then(r => {
      try { const u = r.url || (a[0] && a[0].url) || String(a[0]); r.clone().text().then(t => tell(u, t), () => {}); } catch (e) {}
      return r;
    });
  };
  const open = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (m, u, ...rest) {
    this.__wlUrl = u;
    this.addEventListener('load', () => {
      try { if (!this.responseType || this.responseType === 'text') tell(this.responseURL || this.__wlUrl, this.responseText); } catch (e) {}
    });
    return open.call(this, m, u, ...rest);
  };
})();""".replace("__MANIFEST__", MANIFEST)


_STATIC = re.compile(r"\.(js|mjs|css|png|jpe?g|gif|webp|avif|svg|ico|woff2?|ttf|otf|eot|ts|m4s|aac|mp3|m4a|mp4|webm|vtt|srt|ass|"
                     r"html?|php|json|xml|txt|map|wasm|key)$", re.I)


def _might_be_playlist(url):
    """An address a playlist could be at: not a page's own parts (scripts, pictures, fonts),
    not a piece of a video, not an ad."""
    import urllib.parse
    from . import webtools
    if not url.startswith(("http://", "https://")) or webtools.blocked(url):
        return False
    path = urllib.parse.urlsplit(url).path
    if ".m3u8" in path.lower() or ".mpd" in path.lower():
        return False                 # (said already: caught as it is)
    return not _STATIC.search(path)


def available():
    bridge = launcher.android_bridge
    return launcher.ANDROID and bridge is not None and hasattr(bridge, "pageOpen")


class Page:
    """A page in the phone's hidden WebView, the same calls as cdp._Page."""

    def __init__(self, scripts, on_request=None, on_response=None, on_closed=None, adblock=None):
        from . import webtools
        self.bridge = launcher.android_bridge
        self.on_request, self.on_response, self.on_closed = on_request, on_response, on_closed
        self.loaded = threading.Event()
        self.seen = {}               # address -> its request's headers (for what MANIFEST_JS finds)
        self._peeks = 0
        blocked = sorted(webtools.AD_HOSTS) if (webtools.ADBLOCK if adblock is None else adblock) else []
        self.id = str(self.bridge.pageOpen(json.dumps([MANIFEST_JS, *scripts]), json.dumps(blocked)))
        self.ua = str(self.bridge.pageUserAgent())
        self._open = True
        threading.Thread(target=self._watch, daemon=True).start()

    def _watch(self):
        while self._open:
            try:
                events = json.loads(str(self.bridge.pageEvents(self.id)) or "[]")
            except Exception:  # noqa: BLE001 - the page is gone
                return
            for ev in events:
                self._event(ev)
            time.sleep(0.2)

    def _event(self, ev):
        if ev.get("t") == "load":
            self.loaded.set()
            self.page = ev.get("u") or ""
            return
        if ev.get("t") == "closed":
            if self.on_closed:
                self.on_closed()
            return
        u, headers = ev.get("u") or "", ev.get("h") or {}
        if u.startswith(MANIFEST):
            import urllib.parse
            video = urllib.parse.unquote(u[len(MANIFEST):])
            if self.on_response:
                self.on_response(video, "application/vnd.apple.mpegurl", self.seen.get(video) or headers)
            return
        if len(self.seen) < 3000:
            self.seen[u] = headers
        if self.on_request:
            self.on_request(u, headers, ev.get("k") or "")
        # The phone's browser tells requests, not what came back: an address that doesn't
        # say what it is (a playlist at ".../stream?id=...") is looked at, in case.
        if self.on_response and ev.get("k") != "Document" and self._peeks < 80 and _might_be_playlist(u):
            self._peeks += 1
            threading.Thread(target=self._peek, args=(u, headers), daemon=True).start()

    def _peek(self, u, headers):
        import urllib.request
        h = {k: v for k, v in headers.items() if k in ("User-Agent", "Referer", "Origin", "Cookie")}
        h.setdefault("User-Agent", self.ua)
        try:
            req = urllib.request.Request(u, headers=dict(h, Range="bytes=0-511"))
            with urllib.request.urlopen(req, timeout=10) as r:
                kind = (r.headers.get("Content-Type") or "").lower()
                head = r.read(512).lstrip()
        except Exception:  # noqa: BLE001
            return
        if head.startswith(b"#EXTM3U") or "mpegurl" in kind:
            self.on_response(u, "application/vnd.apple.mpegurl", headers)
        elif b"<MPD" in head[:400] or "dash+xml" in kind:
            self.on_response(u, "application/dash+xml", headers)

    def go(self, url):
        self.bridge.pageGo(self.id, url)

    def run(self, js):
        got = str(self.bridge.pageRun(self.id, js))
        try:
            return json.loads(got) if got else None
        except ValueError:
            return got

    def close(self):
        self._open = False
        try:
            self.bridge.pageClose(self.id)
        except Exception:  # noqa: BLE001
            pass


def watch(url, job):
    """The page shown for you to use (picker.py): every video it asks for goes to `job`.
    Returns what closes it."""
    import urllib.parse
    from . import picker, webtools

    def on_request(u, headers, kind):
        if u.startswith(picker.CLICKED_ON):
            job.clicked(urllib.parse.unquote(u[len(picker.CLICKED_ON):]))
        elif webtools.VIDEO.search(urllib.parse.urlsplit(u).path):
            job.video(u, headers, getattr(page, "page", ""))
        elif webtools.SUBTITLE.search(urllib.parse.urlsplit(u).path) or webtools.SUBTITLE_ASK.search(u):
            job.subtitle(u)

    def on_response(u, mime, headers):
        job.video(u, headers, getattr(page, "page", ""))

    def closed():
        job.closed()
        page.close()
    page = Page([picker.TRACK_JS], on_request, on_response, on_closed=closed)
    page.go(url)
    page.bridge.pageShow(page.id)
    return page.close


def catch_video(url, timeout=30, click=None):
    return cdp.catch_video(url, timeout + 10, click, open_page=Page)      # (a phone is slower)


def render(url, timeout=20, settle=2.5):
    return cdp.render(url, timeout, settle + 1, open_page=Page)
