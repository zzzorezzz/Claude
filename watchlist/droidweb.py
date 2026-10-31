"""The video finder on a phone: pages open in a hidden WebView of the phone app
(MainActivity's Pages), watched the way cdp.py watches its Chromium - every
request any frame of the page makes, with its headers - so cdp's catch_video
and render work on a phone unchanged.

The phone's WebView reports requests, not responses: a player that asks for its
playlist at an address that doesn't say so is caught by MANIFEST_JS instead,
which looks at what the page's own fetch/XHR got back."""

import json
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


def available():
    bridge = launcher.android_bridge
    return launcher.ANDROID and bridge is not None and hasattr(bridge, "pageOpen")


class Page:
    """A page in the phone's hidden WebView, the same calls as cdp._Page."""

    def __init__(self, scripts, on_request=None, on_response=None):
        from . import webtools
        self.bridge = launcher.android_bridge
        self.on_request, self.on_response = on_request, on_response
        self.loaded = threading.Event()
        self.seen = {}               # address -> its request's headers (for what MANIFEST_JS finds)
        blocked = sorted(webtools.AD_HOSTS) if webtools.ADBLOCK else []
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


def catch_video(url, timeout=30, click=None):
    return cdp.catch_video(url, timeout + 10, click, open_page=Page)      # (a phone is slower)


def render(url, timeout=20, settle=2.5):
    return cdp.render(url, timeout, settle + 1, open_page=Page)
