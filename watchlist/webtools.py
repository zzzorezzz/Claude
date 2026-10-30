"""The built-in browser's extras.

1. Blocking ads (Settings → Browser → Block ads and pop-ups): in the app's own
   browser windows, requests to ad and tracker servers are refused before they
   load, pop-up and pop-under windows are stopped, and what's left of the ads
   (boxes, and the invisible layers some players put over the video that open
   an ad when clicked) is hidden. On Windows this happens in Edge's engine
   itself (WebView2); elsewhere a script in each page does what it can.

2. Looking at a page the way a browser does, for your video sites: a page whose
   episode list only appears with JavaScript is read after it has run
   (render), and the video of a page whose player builds it with JavaScript
   (an embedded player from another site, scrambled code, a Cloudflare check)
   is caught as the player asks for it (catch_video): the page is opened in a
   window you don't see, muted, its player is started, and the first video
   address it requests (.m3u8 / .mp4) is taken, with the headers it was sent
   with, for the app's player. That part needs Windows (Edge's engine shows the
   app every request, in every frame).
"""

import re
import sys
import threading
import time
import urllib.parse

# Ad, pop-up and tracker servers (a request to one of these, or to anywhere under
# one, is refused). The usual suspects on free streaming sites, plus the big ad networks.
AD_HOSTS = frozenset("""
doubleclick.net googlesyndication.com googleadservices.com adservice.google.com pagead2.googlesyndication.com
google-analytics.com googletagservices.com googletagmanager.com adnxs.com adsrvr.org amazon-adsystem.com
criteo.com criteo.net outbrain.com taboola.com revcontent.com mgid.com zedo.com adform.net rubiconproject.com
pubmatic.com openx.net casalemedia.com smartadserver.com yieldmo.com teads.tv media.net quantserve.com
scorecardresearch.com moatads.com serving-sys.com 33across.com sharethrough.com indexww.com contextweb.com
adcolony.com applovin.com unityads.unity3d.com inmobi.com vungle.com chartboost.com
popads.net popcash.net propellerads.com propellerclick.com onclickads.net onclickalgo.com onclkds.com
adsterra.com adsterratech.com highperformanceformat.com highperformancecpmgate.com profitabledisplaynetwork.com
profitablegatecpm.com effectivegatecpm.com effectiveratecpm.com displayvertising.com
exoclick.com exosrv.com exdynsrv.com juicyads.com trafficjunky.net trafficjunky.com trafficfactory.biz
trafficstars.com tsyndicate.com plugrush.com hilltopads.net hilltopads.com clickadu.com clickaine.com
adcash.com adskeeper.com adskeeper.co.uk admaven.com ad-maven.com admaventech.com bidvertiser.com
richpush.co pushground.com pushnami.com pushwhy.com evadav.com galaksion.com rollerads.com zeropark.com
a-ads.com coinzilla.com bitmedia.io adspyglass.com realsrv.com acscdn.com aclib.net adtng.com
dolohen.com tapioni.com ladsp.com glizauvo.net whitepark.co.uk mobiletracking.ru ofapes.com
syndication.realsrv.com magsrv.com mnaspm.com bebi.com ylx-1.com ylx-2.com ylx-3.com ylx-4.com
popmyads.com popunder.net popadscdn.net poptm.com yllix.com directrev.com shorte.st adf.ly linkvertise.com
ouo.io ouo.press bc.vc exe.io fc.lc shrinkme.io clk.sh cuty.io
onesignal.com cdn.onesignal.com pushcrew.com webpushr.com izooto.com
hotjar.com mouseflow.com crazyegg.com fullstory.com mixpanel.com segment.io amplitude.com
yandex.ru mc.yandex.ru adfox.ru an.yandex.ru begun.ru
ad.plus adplus.co adsco.re adscore.com adthrive.com ezoic.net ezojs.com monetag.com
ptwmjmp.com ptwmemd.com shaillal.com doubtfulrainstorm.com wbsads.com rtbuzz.net
vidoomy.com vdo.ai primis.tech aniview.com spotxchange.com springserve.com
""".split())
# Adresses that are ads wherever they're served from (players' video ads, pop-under scripts).
AD_PATH = re.compile(r"popunder|popads|/pop(?:up)?\.js|/vast(?:\.xml|/|\?)|vpaid|/ads?\.js\b|adsbygoogle|/prebid", re.I)
VIDEO = re.compile(r"\.(m3u8|mpd|mp4|webm|mkv)(?:[?#]|$)", re.I)

ADBLOCK_JS = r"""(() => {
  if (window.__waAdblock) return; window.__waAdblock = true;
  try { window.open = function () { return null; }; } catch (e) {}
  const css = '.adsbygoogle, ins.adsbygoogle, [id^="google_ads"], [id*="div-gpt-ad"], [id^="ad_"], [class^="ad_"],'
    + ' [id*="ad-banner"], [class*="ad-banner"], [class*="adsbox"], .ad-container, .ads-container, .ad-wrapper, .ad-slot,'
    + ' [data-ad], [data-ad-slot], [data-ad-unit], [id*="popunder"], [class*="popunder"], [class*="sticky-ad"],'
    + ' iframe[src*="doubleclick"], iframe[src*="googlesyndication"], iframe[src*="/ads/"], a[href*="/aff/"]'
    + ' { display: none !important; }';
  const add = () => { const s = document.createElement('style'); s.textContent = css; (document.head || document.documentElement).appendChild(s); };
  if (document.documentElement) add(); else document.addEventListener('DOMContentLoaded', add);
  // Invisible layers over the page (or the player) that open an ad when clicked.
  const sweep = () => {
    if (!document.body) return;
    for (const el of document.querySelectorAll('body > div, body > a, body > span, body > iframe')) {
      const st = getComputedStyle(el);
      if ((st.position !== 'fixed' && st.position !== 'absolute') || (parseInt(st.zIndex, 10) || 0) < 1000) continue;
      const r = el.getBoundingClientRect();
      const big = r.width >= innerWidth * 0.6 && r.height >= innerHeight * 0.6;
      const empty = !el.querySelector('video, img, iframe, input, button') && (el.innerText || '').trim().length < 3;
      const unseen = parseFloat(st.opacity) < 0.15 || st.backgroundColor === 'rgba(0, 0, 0, 0)' || st.backgroundColor === 'transparent';
      if (big && empty && unseen) el.remove();
    }
  };
  setInterval(sweep, 1500);
})();"""

# Start the player of the page (and of every embedded player): muted, so it's allowed to.
AUTOPLAY_JS = r"""(() => {
  if (window.__waPlay) return; window.__waPlay = true;
  let n = 0;
  const kick = () => {
    n += 1;
    for (const v of document.querySelectorAll('video')) {
      try { v.muted = true; if (v.paused) v.play().catch(() => {}); } catch (e) {}
    }
    if (n % 3 === 1) {
      const b = document.querySelector('.jw-icon-display, .jw-display-icon-container, .vjs-big-play-button, .plyr__control--overlaid,'
        + ' .fp-play, .play-button, .btn-play, .playbutton, [aria-label="Play"], [title="Play"], #play, .play-btn, .vds-play-button');
      if (b) { try { b.click(); } catch (e) {} }
    }
    if (n < 25) setTimeout(kick, 1000);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => setTimeout(kick, 700));
  else setTimeout(kick, 700);
})();"""

ADBLOCK = True                 # Settings → Browser → Block ads and pop-ups (kept current by the app)
_pending = {}                  # address -> options, for a window about to open (see _options)
_by_window = {}                # window uid -> options
_patched = False
_lock = threading.Lock()       # one hidden window at a time


def blocked(url):
    """Whether a request is an ad's (by its server, or its address)."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    parts = host.split(".")
    for i in range(len(parts) - 1):
        if ".".join(parts[i:]) in AD_HOSTS:
            return True
    return bool(AD_PATH.search(url))


def _webview():
    from . import launcher
    return launcher._webview


def can_catch():
    """Whether videos can be caught from pages (Windows, in the app's own window)."""
    return _webview() is not None and sys.platform == "win32"


def can_render():
    return _webview() is not None


def _options(window):
    """What a window gets: set when it was opened (a hidden window for a video
    site), else ad blocking for every page that isn't the app's own."""
    uid = getattr(window, "uid", None)
    if uid in _by_window:
        return _by_window[uid]
    url = getattr(window, "real_url", None) or ""
    opts = _pending.pop(url, None)
    if opts is None:
        own = url.startswith(("http://127.0.0.1", "http://localhost"))
        opts = {"adblock": ADBLOCK and not own}
    _by_window[uid] = opts
    return opts


def patch():
    """Hook into pywebview's Edge (WebView2) windows on Windows, once: every new
    window gets its options as its browser engine starts."""
    global _patched
    if _patched or sys.platform != "win32":
        return
    _patched = True
    try:
        from webview.platforms import edgechromium
    except Exception:  # noqa: BLE001 - another engine: the page script is used instead
        return
    edge = edgechromium.EdgeChrome
    ready, new_window = edge.on_webview_ready, edge.on_new_window_request

    def on_webview_ready(self, sender, args):
        ready(self, sender, args)
        try:
            if args.IsSuccess:
                _hook(_options(self.pywebview_window), sender.CoreWebView2)
        except Exception:  # noqa: BLE001 - the window works anyway, just without the extras
            pass

    def on_new_window_request(self, sender, args):
        opts = _options(self.pywebview_window)
        if opts.get("adblock") or opts.get("catch"):
            args.set_Handled(True)                # a pop-up or pop-under: not opened
            return
        new_window(self, sender, args)

    edge.on_webview_ready = on_webview_ready
    edge.on_new_window_request = on_new_window_request


def _hook(opts, core):
    """Ad blocking and video catching, in the browser engine of one window (UI thread)."""
    from Microsoft.Web.WebView2.Core import CoreWebView2WebResourceContext
    catch = opts.get("catch")
    if not (opts.get("adblock") or catch):
        return
    core.AddWebResourceRequestedFilter("*", CoreWebView2WebResourceContext.All)

    def header(headers, name):
        try:
            return str(headers.GetHeader(name)) if headers.Contains(name) else ""
        except Exception:  # noqa: BLE001
            return ""

    def on_request(sender, args):
        url = str(args.Request.Uri)
        if opts.get("adblock") and blocked(url):
            args.Response = core.Environment.CreateWebResourceResponse(None, 403, "Blocked", "")
            return
        if catch and (VIDEO.search(urllib.parse.urlsplit(url).path)
                      or args.ResourceContext == CoreWebView2WebResourceContext.Media):
            h = args.Request.Headers
            catch(url, {"Referer": header(h, "Referer"), "Origin": header(h, "Origin"),
                        "User-Agent": header(h, "User-Agent")})

    core.WebResourceRequested += on_request
    if catch:
        def on_response(sender, args):
            try:
                kind = header(args.Response.Headers, "Content-Type").lower()
            except Exception:  # noqa: BLE001
                return
            if "mpegurl" in kind or "dash+xml" in kind or kind.startswith("video/"):
                h = args.Request.Headers
                catch(str(args.Request.Uri), {"Referer": header(h, "Referer"), "Origin": header(h, "Origin"),
                                              "User-Agent": header(h, "User-Agent")})
        core.WebResourceResponseReceived += on_response
        try:
            core.IsMuted = True
        except Exception:  # noqa: BLE001 - (an older Edge engine)
            pass
        core.AddScriptToExecuteOnDocumentCreatedAsync(AUTOPLAY_JS)
    if opts.get("adblock"):
        core.AddScriptToExecuteOnDocumentCreatedAsync(ADBLOCK_JS)


def page_script(window):
    """Elsewhere than Windows: the ad-blocking script, run in each page once it has loaded."""
    if sys.platform == "win32":
        return

    def loaded():
        if _options(window).get("adblock"):
            try:
                window.evaluate_js(ADBLOCK_JS)
            except Exception:  # noqa: BLE001
                pass
    window.events.loaded += loaded


def _hidden_window(url, opts, title="Watch Anything"):
    wv = _webview()
    patch()
    _pending[url] = opts
    try:
        return wv.create_window(title, url, width=1280, height=720, hidden=True, focus=False)
    except Exception:  # noqa: BLE001
        _pending.pop(url, None)
        return None


def render(url, timeout=20, settle=2.5):
    """(page's HTML after its JavaScript has run, its address), or None."""
    if not can_render():
        return None
    with _lock:
        win = _hidden_window(url, {"adblock": True})
        if win is None:
            return None
        try:
            if not win.events.loaded.wait(timeout):
                return None
            # Wait for what scripts add (an episode list, search results) to settle.
            last, deadline = -1, time.time() + max(settle, 1) * 3
            while time.time() < deadline:
                time.sleep(settle / 3)
                count = win.evaluate_js("document.getElementsByTagName('a').length + document.getElementsByTagName('iframe').length")
                if count == last:
                    break
                last = count
            return win.evaluate_js("document.documentElement.outerHTML"), win.get_current_url() or url
        except Exception:  # noqa: BLE001 - the page or the window went away
            return None
        finally:
            try:
                win.destroy()
            except Exception:  # noqa: BLE001
                pass


def catch_video(url, timeout=30):
    """{"url", "headers"} for the video a page's player asks for, or None."""
    if not can_catch():
        return None
    found = []
    got = threading.Event()

    def catch(video, headers):
        u = video.lower()
        if blocked(video) or u.startswith(("blob:", "data:")) or re.search(r"\.(ts|m4s|aac|key)(\?|$)", u):
            return
        if not any(v == video for v, _ in found):
            found.append((video, {k: v for k, v in headers.items() if v}))
            got.set()

    with _lock:
        win = _hidden_window(url, {"adblock": True, "catch": catch}, "Finding the video…")
        if win is None:
            return None
        try:
            if not got.wait(timeout):
                return None
            # A moment more: the playlist that lists every quality often comes right after.
            time.sleep(1.2)
            best = min(found, key=lambda f: (0 if ".m3u8" in f[0].lower() or ".mpd" in f[0].lower() else 1,
                                             found.index(f)))
            return {"url": best[0], "headers": best[1]}
        finally:
            try:
                win.destroy()
            except Exception:  # noqa: BLE001
                pass
