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
SUBTITLE = re.compile(r"\.(vtt|srt|ass)(?:[?#]|$)", re.I)    # (a player's captions; not its preview thumbnails)
# Captions asked for without a file ending (/subs?tmdbId=..., ...?format=srt): what they are
# is checked by reading them (scrape.pick_subtitle).
SUBTITLE_ASK = re.compile(r"(?:[/?&_.-])(?:subs?|subtitles?|captions?)(?:[/?&=_.-]|$)|[?&]format=(?:srt|vtt|ass)\b", re.I)

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
        + ' .fp-play, .play-button, .btn-play, .playbutton, [aria-label="Play"], [title="Play"], #play, .play-btn, .vds-play-button, #player > .play');
      if (b) { try { b.click(); } catch (e) {} }
    }
    if (n < 25) setTimeout(kick, 1000);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => setTimeout(kick, 700));
  else setTimeout(kick, 700);
})();"""

EPISODES_TAB_JS = r"""(() => {
  const el = [...document.querySelectorAll('button, [role="tab"], [role="button"], li, span, div')]
    .find((e) => /^episodes?\b/i.test((e.textContent || '').trim()) && (e.textContent || '').trim().length < 24
      && !e.closest('a[href]:not([href^="#"])') && !e.querySelector('button, [role="tab"]'));
  if (el) { try { el.click(); } catch (e) {} }
})();"""

# Choose one of an episode's streams (a server / sub / dub button) in the page, by its
# name: the hidden window then tells the app it clicked (a request to CLICKED).
CLICKED = "https://watchanything.invalid/clicked"
# ...or that the page has nothing to click for it (what it plays by itself is taken at once).
NO_CLICK = "https://watchanything.invalid/nothing-to-click"
CLICK_JS = r"""(() => {
  if (window.top !== window || window.__waClick) return; window.__waClick = true;
  const want = __WANT__;
  const norm = (t) => (t || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const kindOk = (el) => {
    if (!want.kind) return true;
    for (let n = el, i = 0; n && i < 6; n = n.parentElement, i += 1) {
      const own = i || norm(n.textContent).length > 40 ? '' : n.textContent;
      const t = norm([n.getAttribute && n.getAttribute('data-type'), n.className, n.id, own].join(' '));
      if (t.includes(want.kind)) return true;
    }
    return false;
  };
  let tries = 0;
  const opened = new Set();
  const done = () => fetch('__CLICKED__').catch(() => {});
  // Sub / dub (or a server) kept in a dropdown under the player: a <select> gets the
  // option chosen; a dropdown button ("Sub ▾") is opened, so its options can be clicked.
  const dropdowns = () => {
    const label = norm(want.label);
    for (const sel of document.querySelectorAll('select')) {
      const opt = [...sel.options].find((o) => norm(o.textContent) === label) || [...sel.options].find((o) => norm(o.textContent).includes(label));
      if (opt && sel.value !== opt.value) {
        sel.value = opt.value;
        sel.dispatchEvent(new Event('input', { bubbles: true }));
        sel.dispatchEvent(new Event('change', { bubbles: true }));
        return 'chosen';
      }
    }
    const triggers = [...document.querySelectorAll('[role="combobox"], [aria-haspopup="listbox"], [aria-haspopup="menu"], [aria-haspopup="true"]')]
      .filter((el) => !opened.has(el) && /^(sub|dub|raw|soft ?sub|hard ?sub|japanese|english|audio|language|server)/i.test((el.textContent || '').trim()));
    if (triggers.length) { opened.add(triggers[0]); triggers[0].click(); return 'opened'; }
    return '';
  };
  const pick = () => {
    tries += 1;
    const els = [...document.querySelectorAll('[role="option"], [role="menuitem"], [data-server-id], [data-server], [data-type] a, [data-type] li, [data-type] button,'
      + ' [data-link], [data-embed], [class*="server"] a, [class*="server"] li, [class*="server"] button, [class*="server-item"], [class*="episode"], a, button, li, span, div')]
      .filter((el) => !el.matches('[role="combobox"], [aria-haspopup]'));
    const label = norm(want.label);
    // The button itself, not a box around it: its name exactly, else the smallest match.
    const found = els.filter((el) => {
      const text = norm(el.textContent);
      const title = norm(el.getAttribute('title') || '');
      return (text.includes(label) || title.includes(label)) && kindOk(el);
    });
    const exact = found.filter((el) => norm(el.textContent) === label);
    const named = want.exact ? found.filter((el) => new RegExp(`^${label}(bed|s)?$`).test(norm(el.textContent))) : [];
    const hit = (want.exact ? named : exact.length ? exact : found).sort((a, b) => norm(a.textContent).length - norm(b.textContent).length)[0];
    if (hit) {
      const clickable = hit.closest('[role="option"], [role="menuitem"], a, button, li, [role="button"]') || hit;
      clickable.click();
      done();
      return;
    }
    const d = dropdowns();
    if (d === 'chosen') { done(); return; }
    if (want.exact && !d && !opened.size && tries >= 6) { fetch('__NO_CLICK__').catch(() => {}); return; }   // (no such switch here)
    if (tries < 25) setTimeout(pick, d === 'opened' ? 350 : 600);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => setTimeout(pick, 400));
  else setTimeout(pick, 400);
})();""".replace("__CLICKED__", CLICKED).replace("__NO_CLICK__", NO_CLICK)

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
    try:
        # Every frame: a site's player is usually another site's iframe, whose
        # requests the two-argument filter never reports.
        from Microsoft.Web.WebView2.Core import CoreWebView2WebResourceRequestSourceKinds as Kinds
        core.AddWebResourceRequestedFilter("*", CoreWebView2WebResourceContext.All, Kinds.All)
    except Exception:  # noqa: BLE001 - an older Edge engine: the page's own requests only
        core.AddWebResourceRequestedFilter("*", CoreWebView2WebResourceContext.All)

    def header(headers, name):
        try:
            return str(headers.GetHeader(name)) if headers.Contains(name) else ""
        except Exception:  # noqa: BLE001
            return ""

    def on_request(sender, args):
        url = str(args.Request.Uri)
        if url.startswith(CLICKED):
            opts["clicked_at"] = time.time()
            args.Response = core.Environment.CreateWebResourceResponse(None, 204, "OK", "")
            return
        if url.startswith(NO_CLICK):
            opts["no_click"] = True
            args.Response = core.Environment.CreateWebResourceResponse(None, 204, "OK", "")
            return
        if opts.get("adblock") and blocked(url):
            args.Response = core.Environment.CreateWebResourceResponse(None, 403, "Blocked", "")
            return
        if catch and (VIDEO.search(urllib.parse.urlsplit(url).path)
                      or args.ResourceContext == CoreWebView2WebResourceContext.Media):
            h = args.Request.Headers
            catch(url, {"Referer": header(h, "Referer"), "Origin": header(h, "Origin"),
                        "User-Agent": header(h, "User-Agent"), "Cookie": header(h, "Cookie")})
        elif catch and (SUBTITLE.search(urllib.parse.urlsplit(url).path) or SUBTITLE_ASK.search(url)) \
                and not re.search(r"thumbnail|\.(?:js|css|png|jpe?g|webp|svg|woff2?|gif)(?:\?|$)", url, re.I):
            subs = opts.setdefault("subs", [])
            if url not in subs:
                subs.append(url)

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
                                              "User-Agent": header(h, "User-Agent"), "Cookie": header(h, "Cookie")})
            elif "text/vtt" in kind or "subrip" in kind or "x-ass" in kind or "x-ssa" in kind:
                # Captions at an address that doesn't say so: kept as subtitles, never the video.
                url = str(args.Request.Uri)
                subs = opts.setdefault("subs", [])
                if url not in subs:
                    subs.append(url)
        core.WebResourceResponseReceived += on_response
        try:
            core.IsMuted = True
        except Exception:  # noqa: BLE001 - (an older Edge engine)
            pass
    scripts = []
    if catch:
        scripts.append(AUTOPLAY_JS)
        if opts.get("click"):
            import json
            scripts.append(CLICK_JS.replace("__WANT__", json.dumps(opts["click"])))
    if opts.get("adblock"):
        scripts.append(ADBLOCK_JS)
    for script in scripts:
        core.AddScriptToExecuteOnDocumentCreatedAsync(script)

    # Those are added while the first page is already loading, so it would miss
    # them: they're run in each page once it's read too (each runs only once).
    def on_loaded(sender, args):
        for script in scripts:
            try:
                core.ExecuteScriptAsync(script)
            except Exception:  # noqa: BLE001
                pass
    if scripts:
        core.DOMContentLoaded += on_loaded


def page_script(window):
    """Elsewhere than Windows: the ad-blocking script, run in each page once it has loaded."""
    if sys.platform == "win32" or window is None:
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
        # Off the screen rather than hidden: a hidden page counts as not being looked
        # at, and many players then never start (nor ask for their video).
        return wv.create_window(title, url, width=1280, height=720, x=-10000, y=-10000, focus=False)
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
            try:
                # An episode list kept behind a tab ("Episodes 500"): opened, without leaving the page.
                win.evaluate_js(EPISODES_TAB_JS)
            except Exception:  # noqa: BLE001
                pass
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


def catch_video(url, timeout=30, click=None):
    """{"url", "headers", "subs"} for the video a page's player asks for, or None. With
    `click` ({"label", "kind"}), that stream's button is clicked first, and only
    what the player asks for after it counts."""
    if not can_catch():
        return None
    found, got = [], threading.Event()
    opts = {"adblock": True, "click": click}

    def catch(video, headers):
        u = video.lower()
        if blocked(video) or u.startswith(("blob:", "data:")) or re.search(r"\.(ts|m4s|aac|key|vtt|srt|mp3|m4a|ogg|oga|opus|wav|flac)(\?|$)", u) \
                or re.search(r"imdb-video|media-imdb|youtube|googlevideo|ytimg|trailer", u):
            return
        if not any(v == video for v, _, _ in found):
            found.append((video, {k: v for k, v in headers.items() if v}, time.time()))
            if not click or opts.get("clicked_at"):
                got.set()
    opts["catch"] = catch

    with _lock:
        win = _hidden_window(url, opts, "Finding the video…")
        if win is None:
            return None
        try:
            deadline = time.time() + timeout

            def videos():
                # (what turned out to be captions isn't the video)
                return [f for f in found if f[0] not in (opts.get("subs") or [])]
            while time.time() < deadline:
                got.wait(0.5)
                if got.is_set():
                    time.sleep(1.2)         # a moment more: the playlist listing every quality often comes right after
                    if videos():
                        break
                    got.clear()
                if click and not opts.get("clicked_at") and videos() and (opts.get("no_click") or time.time() > deadline - timeout + 15):
                    break                   # the button wasn't found: what the page plays by itself
            found[:] = videos()
            if not found:
                return None
            after = [f for f in found if f[2] >= opts.get("clicked_at", 0)] or found
            best = min(after, key=lambda f: (0 if ".m3u8" in f[0].lower() or ".mpd" in f[0].lower() else 1,
                                             after.index(f)))
            return {"url": best[0], "headers": best[1], "subs": list(opts.get("subs") or [])}
        finally:
            try:
                win.destroy()
            except Exception:  # noqa: BLE001
                pass
