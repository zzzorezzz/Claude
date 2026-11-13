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

import contextlib
import os
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
        + ' .fp-play, .play-button, .btn-play, .playbutton, [aria-label="Play"], [title="Play"], #play, .play-btn, .vds-play-button, #player > .play,'
        // (any player's own big play button, by the words in its class: "video-play-button", "big-play-btn"...)
        + ' [class*="play-button" i], [class*="play-btn" i], [class*="playbutton" i], [class*="btn-play" i], [class*="big-play" i],'
        + ' [class*="play-icon" i], [class*="playicon" i], [id*="play-button" i], [id*="playbtn" i]');
      if (b) { try { b.click(); } catch (e) {} }
    }
    // Nothing playing after a few seconds: the page's own "Play now" / "Watch now" (its own
    // button, in the page itself - not an ad's frame, not a link away).
    if ((n === 4 || n === 9) && window.top === window && ![...document.querySelectorAll('video')].some((v) => !v.paused)) {
      const own = [...document.querySelectorAll('button, [role="button"], a[href^="#"], a:not([href])')].find((el) => {
        const t = (el.textContent || '').replace(/\s+/g, ' ').trim();
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && /^(▶\s*)?(play|play now|watch|watch now|start watching|play (episode|movie|film)|watch (episode|movie|film))$/i.test(t);
      });
      if (own) { try { own.click(); } catch (e) {} }
    }
    if (n < 25) setTimeout(kick, 1000);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => setTimeout(kick, 700));
  else setTimeout(kick, 700);
})();"""

# Search results that are cards without a link (a picture and a name; a press of it takes you to the
# title by the page's own script - MeguAnimes): each pressed with the page's address changes held
# back, and given a link to where it would have gone. Only while that's how the page moves on (the
# first press that goes nowhere stops it).
CARD_LINKS_JS = r"""(() => {
  const named = (e) => (e.getAttribute('aria-label') || e.querySelector('img[alt]')?.alt || e.getAttribute('title') || '').trim();
  const cards = [...document.querySelectorAll('[role="button"], [tabindex], [onclick], [data-id], [data-href], [data-url], [data-link]')]
    .filter((e) => !e.closest('a[href]') && !e.querySelector('a[href]') && e.querySelector('img') && named(e)
      && e.getBoundingClientRect().width > 50 && e.getBoundingClientRect().height > 50
      && !e.parentElement.closest('[role="button"], [data-id], [data-href], [data-url], [onclick]'));
  const alike = cards.filter((e) => cards.some((o) => o !== e && o.className === e.className));
  if (alike.length < 2) return 0;
  // (kept apart from the page: its own script may draw the results again as each is pressed)
  const box = document.createElement('div');
  box.hidden = true;
  box.setAttribute('data-wa-cards', '1');
  document.documentElement.appendChild(box);
  const link = (e, url) => {
    const a = document.createElement('a');
    a.href = new URL(url, location.href).href;
    a.textContent = named(e);
    a.setAttribute('data-wa-card', '1');
    box.appendChild(a);
  };
  const push = history.pushState, replace = history.replaceState, open = window.open;
  let went = null;
  history.pushState = function (s, t, u) { went = u; };
  history.replaceState = function (s, t, u) { if (u && String(u) !== location.href) went = u; };
  window.open = function (u) { went = u; return null; };
  const done = () => { history.pushState = push; history.replaceState = replace; window.open = open; };
  let n = 0;
  (async () => {
    try {
      // (each kind of card on its own: one whose first press goes nowhere - a slide, a menu's - is let be)
      const kinds = [...new Set(alike.map((e) => e.className))];
      for (const kind of kinds) {
        // (the cards as they are now: pressing one may draw them again)
        for (const e of alike.filter((x) => x.className === kind).slice(0, 40)) {
          const given = e.dataset.href || e.dataset.url || e.dataset.link;
          if (given) { link(e, given); n += 1; continue; }
          went = null;
          try { e.click(); } catch (_) { /* fine */ }
          for (let w = 0; w < 10 && went === null; w += 1) await new Promise((r) => setTimeout(r, 30));
          if (went === null) break;
          link(e, went);
          n += 1;
        }
      }
    } finally { done(); }
  })();
  return alike.length;
})()"""

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
# A menu button named by words (a "Server" dropdown): by its own few words or a short label beside
# it - never an episode's button (Miruro's hold the episode's summary, where "source" may be a word).
MENU_NAMED_JS = r"""const EPISODE = /\b(ep|episode|eps|chapter)\.?\s*\d|^\d{1,4}$/i;
  const menuNamed = (el, words) => {
    const own = (el.textContent || '').replace(/\s+/g, ' ').trim();
    const aria = `${el.getAttribute('aria-label') || ''} ${el.getAttribute('title') || ''}`;
    if (own.length > 40 || EPISODE.test(own) || EPISODE.test(aria)) return false;
    const beside = ((el.parentElement || {}).textContent || '').replace(/\s+/g, ' ').trim();
    return words.test(`${own} ${aria} ${beside.length <= 40 ? beside : ''}`);
  };"""
CLICK_JS = r"""(() => {
  const want = __WANT__;
  if (want.frame ? location.host !== want.frame : window.top !== window) return;
  if (window.__waClick) return; window.__waClick = true;
  const norm = (t) => (t || '').replace(/\s+/g, ' ').trim().toLowerCase();
  __MENU_NAMED__
  const kindOk = (el) => {
    if (!want.kind || (langNow() === want.kind && langTrigger())) return true;
    for (let n = el, i = 0; n && i < 6; n = n.parentElement, i += 1) {
      const own = i || norm(n.textContent).length > 40 ? '' : n.textContent;
      const t = norm([n.getAttribute && n.getAttribute('data-type'), n.className, n.id, own].join(' '));
      if (t.includes(want.kind)) return true;
    }
    return false;
  };
  // A press as a real click makes it (menus built with React open on the pointer going down,
  // not on a bare click()).
  const press = (el) => {
    const r = el.getBoundingClientRect();
    const o = { bubbles: true, cancelable: true, composed: true, button: 0, view: window, clientX: r.x + r.width / 2, clientY: r.y + r.height / 2 };
    const p = Object.assign({ pointerId: 1, pointerType: 'mouse', isPrimary: true }, o);
    try { el.dispatchEvent(new PointerEvent('pointerdown', p)); } catch (e) { /* no pointer events here */ }
    el.dispatchEvent(new MouseEvent('mousedown', o));
    try { el.dispatchEvent(new PointerEvent('pointerup', p)); } catch (e) { /* no pointer events here */ }
    el.dispatchEvent(new MouseEvent('mouseup', o));
    el.dispatchEvent(new MouseEvent('click', o));
  };
  const POPUP = '[role=listbox], [role=menu], [data-radix-popper-content-wrapper], [data-floating-ui-portal], [class*=popover], [class*=Popover], [class*=dropdown-menu], [class*=Dropdown], [class*=popup]';
  const shown = (p) => { const r = p.getBoundingClientRect(); return r.height > 0 && r.width > 0; };
  const kindWord = (t) => (/\bdub/i.test(t) ? 'dub' : /(\b|-)sub(bed|s)?\b/i.test(t) ? 'sub' : /\braw\b/i.test(t) ? 'raw' : '');
  // A Sub / Dub menu by the player (Miruro's "Language"): what it's set to is the servers' kind.
  const langTrigger = () => [...document.querySelectorAll('[role=combobox], [aria-haspopup]')].find((el) => el.getBoundingClientRect().width > 0 && !el.closest(POPUP)
    && (/^(language|audio|audio language|version|type|sub ?\/ ?dub|track)$/i.test((el.getAttribute('aria-label') || '').trim())
      || /^(sub|dub|raw|soft ?sub|hard ?sub|subbed|dubbed)$/i.test((el.textContent || '').replace(/\s+/g, ' ').trim())));
  // A menu opened by a press: its pop-up (only one that wasn't showing before), or null.
  const openMenu = (el, then) => {
    const before = new Map([...document.querySelectorAll(POPUP)].map((p) => [p, shown(p)]));
    try { press(el); } catch (e) { /* fine */ }
    setTimeout(() => then([...document.querySelectorAll(POPUP)].filter((p) => shown(p) && before.get(p) !== true)), 700);
  };
  const closeMenu = (el) => {
    try { (document.activeElement || document.body).dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); } catch (e) { /* fine */ }
    setTimeout(() => { if (el && el.getAttribute('aria-expanded') === 'true') { try { press(el); } catch (e) { /* fine */ } } }, 150);
  };
  let tries = 0, langDone = false, langTries = 0;
  const opened = new Set();
  const done = () => fetch('__CLICKED__').catch(() => {});
  // A server of the dub (say) where the page has a Sub / Dub menu: Dub chosen in it first.
  const langNow = () => { const lt = langTrigger(); return lt ? kindWord((lt.textContent || '').trim()) : ''; };
  const chooseLang = (then) => {
    const lt = langTrigger();
    langTries += 1;
    if (!want.kind || !lt || langNow() === want.kind) { langDone = true; then(false); return; }
    openMenu(lt, (popups) => {
      const opt = popups.flatMap((p) => [...p.querySelectorAll('[role=option], [role=menuitem], [role=menuitemradio], button, li')])
        .find((o) => kindWord(norm(o.textContent)) === want.kind && norm(o.textContent).length < 20);
      // (a menu shown before the page's script is ready doesn't open yet: pressed again, a few times)
      if (!opt) { closeMenu(lt); langDone = langTries >= 4; setTimeout(() => then(false), langDone ? 0 : 1200); return; }
      langDone = true;
      press(opt);
      then(true);
    });
  };
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
    const triggers = [...document.querySelectorAll('[role="combobox"], [aria-haspopup="listbox"], [aria-haspopup="menu"], [aria-haspopup="true"], [aria-haspopup="dialog"]')]
      .filter((el) => !opened.has(el) && !(langDone && el === langTrigger()) && (/^(sub|dub|raw|soft ?sub|hard ?sub|japanese|english|audio|language|server)/i.test((el.textContent || '').trim())
        // (a "Server" dropdown named by its label or the words beside it: only when a server is
        // wanted - looking for Sub / Dub, a server list is no place to open)
        || (!/^(sub|dub|raw)$/i.test(want.label) && menuNamed(el, /\b(servers?|sources?|mirrors?)\b/i))));
    if (triggers.length) { opened.add(triggers[0]); press(triggers[0]); return 'opened'; }
    return '';
  };
  const pick = () => {
    tries += 1;
    if (!langDone && want.kind && langTrigger() && langNow() !== want.kind) {
      // (the language asked for chosen in its menu first - once the page has drawn it; Sub / Dub
      // itself is then all there is to do)
      chooseLang((chosen) => {
        if (chosen && /^(sub|dub|raw)$/i.test(want.label || '')) { done(); return; }
        setTimeout(pick, chosen ? 2500 : 0);
      });
      return;
    }
    const els = [...document.querySelectorAll('[role="option"], [role="menuitem"], [data-server-id], [data-server], [data-type] a, [data-type] li, [data-type] button,'
      + ' [data-link], [data-embed], [class*="server"] a, [class*="server"] li, [class*="server"] button, [class*="server-item"], [class*="episode"], a, button, li, span, div')]
      .filter((el) => !el.matches('[role="combobox"], [aria-haspopup]'));
    const label = norm(want.label);
    // (a name made of several pieces - "sun" "DL" "H-SUB" - reads with or without the spaces between)
    const squash = (t) => norm(t).replace(/\s+/g, '');
    const said = (el) => [norm(el.innerText || el.textContent), squash(el.textContent)];
    const bare = squash(want.label);
    // The button itself, not a box around it: its name exactly, else the smallest match.
    const found = els.filter((el) => {
      const [text, packed] = said(el);
      const title = norm(el.getAttribute('title') || '');
      return (text.includes(label) || packed.includes(bare) || title.includes(label)) && kindOk(el);
    });
    const exact = found.filter((el) => { const [text, packed] = said(el); return text === label || packed === bare; });
    const named = want.exact ? found.filter((el) => { const [text, packed] = said(el);
      return new RegExp(`^${label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(bed|s)?$`).test(text) || packed === bare; }) : [];
    const hit = (want.exact ? named : exact.length ? exact : found).sort((a, b) => norm(a.textContent).length - norm(b.textContent).length)[0];
    if (hit) {
      const clickable = hit.closest('[role="option"], [role="menuitem"], a, button, li, [role="button"]') || hit;
      // (an option of a pop-up menu as a real click picks it; anything else as before)
      if (clickable.matches('[role="option"], [role="menuitem"], [role="menuitemradio"]') || clickable.closest(POPUP)) press(clickable);
      else clickable.click();
      done();
      return;
    }
    const d = dropdowns();
    if (d === 'chosen') { done(); return; }
    if (want.exact && !d && !opened.size && tries >= 12 && document.readyState === 'complete') { fetch('__NO_CLICK__').catch(() => {}); return; }   // (no such switch here)
    if (tries < 25) setTimeout(pick, d === 'opened' ? 350 : 600);
  };
  // A button to press before it (an episode's season, whose episodes only show once it's chosen).
  const first = (n) => {
    if (!want.first) { pick(); return; }
    const name = norm(want.first);
    const el = [...document.querySelectorAll('button, a, li, [role="tab"], [role="button"], span, div')]
      .filter((e) => norm(e.textContent) === name).sort((a, b) => a.querySelectorAll('*').length - b.querySelectorAll('*').length)[0];
    if (el) { try { el.click(); } catch (e) { /* fine */ } setTimeout(pick, 1500); return; }
    if (n < 15) setTimeout(() => first(n + 1), 600); else pick();
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => setTimeout(() => first(0), 400));
  else setTimeout(() => first(0), 400);
})();""".replace("__CLICKED__", CLICKED).replace("__MENU_NAMED__", MENU_NAMED_JS).replace("__NO_CLICK__", NO_CLICK)

# The servers (sources, mirrors) an episode's page offers, read in every frame of it - the
# player is often another site's frame with its own list. Any page's: a box named for servers
# ("server", "source", "mirror", "provider", "embed"), or the list under a "Server(s)" heading,
# or a <select> of them; each option's name, its sub / dub, and whether it's the one playing.
# Told to the app as a request to SERVERS (in every way a page can send one).
SERVERS = "https://watchanything.invalid/servers?d="
SERVERS_JS = r"""(() => {
  if (window.__waServers) return; window.__waServers = true;
  const norm = (t) => (t || '').replace(/\s+/g, ' ').trim();
  __MENU_NAMED__
  const NAMED = /server|mirror|provider|embed|sources?(?:[-_ ]?(?:list|box|item|btn|button|wrap|tab))?$|^sources?\b|\bsources?\b/i;
  const HEAD = /^(servers?|video servers?|streaming servers?|sources?|video sources?|mirrors?|providers?|(choose|select|switch)( a| the)? (server|source|mirror))\s*:?$/i;
  const NOT = /^(home|log ?in|sign ?(in|up)|register|menu|search|share|report|download|trailer|comments?|next|prev(ious)?|episodes?|seasons?|settings|close|cancel|ok|auto|quality|speed|subtitles?|audio|captions?|\d{3,4}p|[\d.]+x|on|off|more|less|all|watch( now)?|play|pause|embed|direct|server|servers|source|sources|sub|dub|language|lang)$/i;
  const CLICK = 'button, a, li, [role=option], [role=menuitem], [role=menuitemradio], [role=radio], [role=tab], [role=button], [data-server], [data-server-id], [data-link], [data-embed], [data-src], [data-id], option';
  const INNER = CLICK.replace(', option', '');
  const QUALITY = /\b\d{3,4}p\b|\bup to\b|^auto\b/i;            // (a quality, in the same box as the servers)
  const BASE = location.hostname.replace(/^www\./, '').split('.')[0].toLowerCase();
  const cls = (n) => (typeof n.className === 'string' ? n.className : (n.className && n.className.baseVal) || '');
  const kindOf = (el, box) => {
    for (let n = el, i = 0; n && i < 10; n = n.parentElement, i += 1) {
      const t = [n.getAttribute && n.getAttribute('data-type'), cls(n), n.id].join(' ');
      if (/\bdub|[-_]dub\b/i.test(t)) return 'dub';
      if (/\b(sub|softsub|hardsub)\b|[-_]sub\b|\bsub[-_]/i.test(t)) return 'sub';
      if (/\braw\b/i.test(t)) return 'raw';
      if (n === box) {
        const head = norm((box.previousElementSibling || {}).textContent || '');
        if (head.length < 30) { if (/\bdub/i.test(head)) return 'dub'; if (/\bsub/i.test(head)) return 'sub'; if (/\braw\b/i.test(head)) return 'raw'; }
      }
    }
    return '';
  };
  const activeOf = (el) => {
    for (let n = el, i = 0; n && i < 3; n = n.parentElement, i += 1) {
      if (!n.getAttribute) break;
      if (['aria-selected', 'aria-checked', 'aria-current', 'aria-pressed'].some((a) => n.getAttribute(a) === 'true')) return true;
      if (/(^|[\s_-])(active|selected|current|checked|is-active|is-selected|on)($|[\s_-])/i.test(cls(n))) return true;
      if (n.tagName === 'OPTION' && n.selected) return true;
    }
    return false;
  };
  const boxes = () => {
    const out = new Set();
    for (const el of document.querySelectorAll('[class], [id], [aria-label], [title], select')) {
      if (/^(SCRIPT|STYLE|LINK|META|IFRAME|VIDEO|SOURCE|BODY|HTML|IMG)$/.test(el.tagName)) continue;
      const name = [cls(el), el.id, el.getAttribute('aria-label') || '', el.getAttribute('title') || '', el.getAttribute('name') || ''].join(' ');
      if (NAMED.test(name)) out.add(el);
    }
    for (const el of document.querySelectorAll('h1, h2, h3, h4, h5, h6, div, span, p, label, strong, b, dt, legend, header')) {
      if (el.children.length > 2) continue;
      const t = norm(el.textContent);
      if (t.length < 40 && HEAD.test(t)) {
        if (el.nextElementSibling) out.add(el.nextElementSibling);
        if (el.parentElement) out.add(el.parentElement);
      }
    }
    return [...out].sort((a, b) => a.getElementsByTagName('*').length - b.getElementsByTagName('*').length);
  };
  const options = (box) => {
    const out = [];
    const els = box.matches(CLICK) && !box.querySelector(INNER) ? [box] : box.querySelectorAll(CLICK);
    for (const el of els) {
      if (el.tagName !== 'OPTION' && el.querySelector(INNER)) continue;          // (a box of options, not one)
      const label = norm(el.tagName === 'OPTION' ? el.textContent : (el.innerText || el.textContent || el.getAttribute('title') || el.getAttribute('aria-label')));
      if (!label || label.length > 32 || NOT.test(label) || QUALITY.test(label)) continue;
      if (/^[\w-]+(\.[\w-]+)*\.[a-z]{2,}$/i.test(label) && label.split('.')[0].toLowerCase() === BASE) continue;   // (the site's own other addresses)
      out.push({ el, label });
    }
    return out;
  };
  // A press as a real click makes it (menus built with React open on the pointer going down,
  // not on a bare click()).
  const press = (el) => {
    const r = el.getBoundingClientRect();
    const o = { bubbles: true, cancelable: true, composed: true, button: 0, view: window, clientX: r.x + r.width / 2, clientY: r.y + r.height / 2 };
    const p = Object.assign({ pointerId: 1, pointerType: 'mouse', isPrimary: true }, o);
    try { el.dispatchEvent(new PointerEvent('pointerdown', p)); } catch (e) { /* no pointer events here */ }
    el.dispatchEvent(new MouseEvent('mousedown', o));
    try { el.dispatchEvent(new PointerEvent('pointerup', p)); } catch (e) { /* no pointer events here */ }
    el.dispatchEvent(new MouseEvent('mouseup', o));
    el.dispatchEvent(new MouseEvent('click', o));
  };
  const POPUP = '[role=listbox], [role=menu], [data-radix-popper-content-wrapper], [data-floating-ui-portal], [class*=popover], [class*=Popover], [class*=dropdown-menu], [class*=Dropdown], [class*=popup]';
  const shown = (p) => { const r = p.getBoundingClientRect(); return r.height > 0 && r.width > 0; };
  const kindWord = (t) => (/\bdub/i.test(t) ? 'dub' : /(\b|-)sub(bed|s)?\b/i.test(t) ? 'sub' : /\braw\b/i.test(t) ? 'raw' : '');
  // A Sub / Dub menu by the player (Miruro's "Language"): what it's set to is the servers' kind.
  const langTrigger = () => [...document.querySelectorAll('[role=combobox], [aria-haspopup]')].find((el) => el.getBoundingClientRect().width > 0 && !el.closest(POPUP)
    && (/^(language|audio|audio language|version|type|sub ?\/ ?dub|track)$/i.test((el.getAttribute('aria-label') || '').trim())
      || /^(sub|dub|raw|soft ?sub|hard ?sub|subbed|dubbed)$/i.test((el.textContent || '').replace(/\s+/g, ' ').trim())));
  // A menu opened by a press: its pop-up (only one that wasn't showing before), or null.
  const openMenu = (el, then) => {
    const before = new Map([...document.querySelectorAll(POPUP)].map((p) => [p, shown(p)]));
    try { press(el); } catch (e) { /* fine */ }
    setTimeout(() => then([...document.querySelectorAll(POPUP)].filter((p) => shown(p) && before.get(p) !== true)), 700);
  };
  const closeMenu = (el) => {
    try { (document.activeElement || document.body).dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); } catch (e) { /* fine */ }
    setTimeout(() => { if (el && el.getAttribute('aria-expanded') === 'true') { try { press(el); } catch (e) { /* fine */ } } }, 150);
  };
  // A "Server" dropdown whose options only exist while it's open (drawn in a pop-up elsewhere in
  // the page): opened, its pop-up read with the rest, then closed again.
  // (named so by its own few words, or a short label beside it - not a word somewhere in an
  // episode's summary: Miruro's episode buttons hold theirs, and pressing one changes episode)
  const openers = () => [...document.querySelectorAll('button, [role=button], [role=combobox], [aria-haspopup]')]
    .filter((el) => menuNamed(el, /\b(servers?|sources?|mirrors?|providers?)\b/i)
      && !el.closest(POPUP) && el.getBoundingClientRect().width > 0 && el !== langTrigger());
  let opened = 0;
  const peek = (then, again = false) => {
    const el = openers()[0];
    if (!el || (opened >= 2 && !again)) { then([]); return; }
    opened += 1;
    // (only what the press opened - not a menu that was open already)
    openMenu(el, (popups) => { then(popups); closeMenu(el); });
  };
  // Listing every server (the app's Servers list, not while finding a video): each other
  // language chosen in turn, its servers read too.
  let others = [], more = false, cycled = false;
  const kept = new Map();
  const cycle = () => {
    const lt = langTrigger();
    cycled = true;
    if (!lt) { more = false; report(); return; }
    const start = kindWord(norm(lt.textContent));
    openMenu(lt, (popups) => {
      const kinds = [...new Set(popups.flatMap((p) => [...p.querySelectorAll('[role=option], [role=menuitem], [role=menuitemradio], button, li')])
        .map((o) => kindWord(norm(o.textContent))).filter((k) => k && k !== start))];
      closeMenu(lt);
      if (!kinds.length) { more = false; report(); return; }
      more = true;
      const next = (i) => {
        if (i >= kinds.length) { more = false; report(); return; }
        setTimeout(() => openMenu(langTrigger() || lt, (menu) => {
          const opt = menu.flatMap((p) => [...p.querySelectorAll('[role=option], [role=menuitem], [role=menuitemradio], button, li')])
            .find((o) => kindWord(norm(o.textContent)) === kinds[i] && norm(o.textContent).length < 20);
          if (!opt) { closeMenu(lt); next(i + 1); return; }
          press(opt);
          // (its servers once the page has shown them)
          setTimeout(() => peek((list) => {
            for (const box of list) for (const o of options(box)) others.push({ l: o.label, k: kindWord(o.label) || kinds[i], a: false });
            next(i + 1);
          }, true), 2500);
        }), 400);
      };
      next(0);
    });
  };
  let last = '';
  const tell = (url) => {
    try { fetch(url, { mode: 'no-cors' }).catch(() => {}); } catch (e) { /* not allowed here */ }
    try { new Image().src = url; } catch (e) { /* not allowed here */ }
  };
  const report = (extra = []) => {
    const items = [], taken = new Set(), names = new Set();
    const lt = langTrigger();
    const now = lt ? kindWord(norm(lt.textContent)) : '';
    for (const box of [...extra, ...boxes()]) {
      const opts = options(box);
      const distinct = new Set(opts.map((o) => o.label.toLowerCase()));
      if (distinct.size < 2 || distinct.size > 40) continue;
      if ([...distinct].every((l) => /^\d+$/.test(l)) && distinct.size > 8) continue;      // (an episode list)
      for (const o of opts) {
        if (taken.has(o.el)) continue;
        taken.add(o.el);
        const said = /\bdub(bed)?\b/i.test(o.label) ? 'dub' : /(\b|-)sub(bed|s)?\b/i.test(o.label) ? 'sub' : /\braw\b/i.test(o.label) ? 'raw' : '';
        const kind = said || kindOf(o.el, box) || now;
        const key = `${o.label.toLowerCase()}|${kind}`;
        if (names.has(key)) continue;
        names.add(key);
        items.push({ l: o.label, k: kind, a: activeOf(o.el) });
      }
    }
    if (!items.length && !extra.length && !kept.size && openers().length) { peek((popups) => { if (popups.length) report(popups); }); return; }
    for (const o of [...items, ...others]) {
      const key = `${o.l.toLowerCase()}|${o.k}`;
      if (!kept.has(key) || items.includes(o)) kept.set(key, o);
    }
    items.splice(0, items.length, ...kept.values());
    if (items.length && window.__waListAll && !cycled && langTrigger()) { more = true; cycle(); }
    const s = JSON.stringify([items.slice(0, 40), more]);
    if (s === last || !items.length) return;
    last = s;
    tell('__SERVERS__' + encodeURIComponent(JSON.stringify({ f: location.host, i: items.slice(0, 40), m: more ? 1 : 0 })));
  };
  for (const ms of [1200, 3000, 6000, 10000, 16000]) setTimeout(report, ms);
})();""".replace("__SERVERS__", SERVERS).replace("__MENU_NAMED__", MENU_NAMED_JS)


LIST_ALL_JS = "window.__waListAll = true;"     # (SERVERS_JS: every language's servers - the Servers list)


def servers_signal(url, opts):
    """A page telling its servers (SERVERS_JS) into opts["servers"] (by frame): whether it was that."""
    if not url.startswith(SERVERS):
        return False
    import json
    try:
        got = json.loads(urllib.parse.unquote(url[len(SERVERS):]))
    except ValueError:
        return True
    items = [{"label": str(x.get("l"))[:40], "kind": str(x.get("k") or ""), "on": bool(x.get("a"))}
             for x in got.get("i") or [] if isinstance(x, dict) and x.get("l")][:40]
    if items:
        opts.setdefault("servers", {})[str(got.get("f") or "")] = items
        opts["servers_at"] = time.time()
        opts["servers_more"] = bool(got.get("m"))      # (more coming: its other languages' servers)
    return True


def servers_of(opts, page_url):
    """Every server the page's frames told, the page's own first: [{"label", "kind", "on", "frame"}]
    ("frame": the frame's server when it isn't the page's)."""
    top = urllib.parse.urlsplit(page_url or "").netloc
    out, seen = [], set()
    for frame, items in sorted((opts.get("servers") or {}).items(), key=lambda kv: kv[0] != top):
        for x in items:
            key = (x["label"].lower(), x["kind"])
            if key not in seen:
                seen.add(key)
                out.append(dict(x, frame="" if frame == top else frame))
    return out


ADBLOCK = True                 # Settings → Browser → Block ads and pop-ups (kept current by the app)
_pending = {}                  # address -> options, for a window about to open (see _options)
_by_window = {}                # window uid -> options
_patched = False
_lock = threading.BoundedSemaphore(3)       # up to three hidden windows at once (several sites searched together)
_background = threading.BoundedSemaphore(2)  # ...of which the app's own look-ups in the background take two at most
_here = threading.local()


def unwanted(skip):
    """What this thread's next video catches leave out: skip(video, referer) True for a video that
    isn't the one wanted (another episode's player) - waited past, not taken. None: nothing."""
    _here.skip = skip


def _skip():
    return getattr(_here, "skip", None) or (lambda video, referer: False)


def background():
    """This thread looks things up in the background (every server checked, the season's episodes
    matched): it leaves a hidden window free for what you ask for."""
    _here.background = True


def carried(fn):
    """`fn` for another thread (a pool's) that looks things up as this one does: in the background,
    when this one is."""
    behind = getattr(_here, "background", False)

    def run(*args, **kwargs):
        if behind:
            _here.background = True
        return fn(*args, **kwargs)
    return run


@contextlib.contextmanager
def slot(lock=None):
    """One of the hidden windows (or pages) - a background look-up only when one is left over."""
    lock = lock or _lock
    behind = getattr(_here, "background", False)
    if behind:
        _background.acquire()
    try:
        with lock:
            yield
    finally:
        if behind:
            _background.release()


_TRAILER_HOSTS = re.compile(r"(?:^|\.)(?:youtube\.com|youtube-nocookie\.com|youtu\.be|googlevideo\.com|ytimg\.com|"
                            r"media-imdb\.com|imdb-video\.media-imdb\.com|imdb\.com|vimeocdn\.com)$")


def trailer(url):
    """A trailer, not the episode: YouTube's / IMDb's own servers, or "trailer" in its path (a
    server merely named like them - "xyz-youtube.cfd" - isn't one)."""
    parts = urllib.parse.urlsplit(url)
    return bool(_TRAILER_HOSTS.search((parts.hostname or "").lower()) or re.search(r"trailer", parts.path, re.I))


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


def _chromium():
    """Pages are opened in a hidden Chromium (cdp.py) instead of Edge windows: on a Mac and
    Linux (and on Windows when asked to, to test it)."""
    from . import launcher
    return not launcher.ANDROID and (sys.platform != "win32" or os.environ.get("WATCHLIST_CDP") == "1")


def _phone():
    """On a phone: pages open in the phone app's hidden WebView (droidweb.py)."""
    from . import droidweb
    return droidweb.available()

# A site's own search box used as you would: the title typed in and Enter pressed (a search
# only its scripts run - no form, no link with the words in it). A search button that only
# opens the box is pressed first. render(..., then=...) runs it, and the address it goes to is
# the site's search link.
TYPE_SEARCH_JS = r"""(() => {
  const q = __Q__;
  const shown = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden'; };
  const SEARCHY = /search|find|query|keyword|搜索/i;
  const boxes = () => [...document.querySelectorAll('input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=submit]):not([type=button]), [contenteditable=true][role=searchbox]')]
    .filter((el) => el.type === 'search' || SEARCHY.test([el.name, el.id, el.placeholder, el.getAttribute('aria-label'), el.className, el.getAttribute('role')].join(' ')));
  const fire = (el, type, init) => el.dispatchEvent(new (type.startsWith('key') ? KeyboardEvent : Event)(type, Object.assign({ bubbles: true, cancelable: true }, init || {})));
  const named = () => [...document.querySelectorAll('a[href]')].filter((a) => shown(a)
    && (a.textContent + ' ' + a.href).toLowerCase().includes(q.toLowerCase())).length;
  const type = (box) => {
    const before = named();
    box.focus();
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    try { setter.call(box, q); } catch (e) { box.value = q; }
    fire(box, 'input'); fire(box, 'change');
    setTimeout(() => {
      // (results shown as it's typed, and no search form to go to: left as they are - Enter can close them)
      if (named() > before && !box.closest('form[action]')) return;
      const enter = { key: 'Enter', code: 'Enter', keyCode: 13, which: 13 };
      fire(box, 'keydown', enter); fire(box, 'keypress', enter); fire(box, 'keyup', enter);
      setTimeout(() => {
        const form = box.closest('form');
        if (form && location.href === start) { try { form.requestSubmit ? form.requestSubmit() : form.submit(); } catch (e) { /* fine */ } }
      }, 600);
    }, 1500);
  };
  const start = location.href;
  // (a magnifier button that opens the box - or only its icon says so: "lucide-search")
  const open = () => {
    const opener = [...document.querySelectorAll('button, a, [role=button], label, i, span, div, svg')].find((el) => shown(el)
      && el.children.length <= 2 && SEARCHY.test([el.getAttribute('aria-label'), el.getAttribute('title'), el.getAttribute('class'),
        el.id, el.tagName === 'svg' ? '' : (el.textContent || '').trim().slice(0, 12)].join(' ')));
    const press = opener && (opener.closest('button, a, [role=button], label') || opener);
    if (press) { try { press.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window })); } catch (e) { /* fine */ } }
    return !!press;
  };
  // (a page its scripts are still drawing: looked at again, for a few seconds)
  let tries = 0;
  const attempt = () => {
    const box = boxes().find(shown);
    if (box) { type(box); return; }
    if (tries % 3 === 0) open();          // (pressed again: a page not ready yet doesn't answer the first)
    tries += 1;
    if (tries < 12) { setTimeout(attempt, 500); return; }
    const any = boxes()[0];
    if (any) type(any);
  };
  attempt();
})();"""


def type_search(url, query, timeout=20):
    """(the page, its address) once `query` is typed into a site's own search box - the address
    it went to, or the page showing its results itself - or None."""
    import json
    return render(url, timeout, then=TYPE_SEARCH_JS.replace("__Q__", json.dumps(query)), cards=True)


def can_catch():
    """Whether videos can be caught from pages: in the app's own Edge windows on Windows,
    in a hidden Chromium on a computer elsewhere, in a hidden WebView on a phone."""
    if _chromium() or _phone():
        return True
    return _webview() is not None and sys.platform == "win32"


def can_render():
    return _chromium() or _phone() or _webview() is not None


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
        try:
            if args.IsSuccess:
                _recover(sender.CoreWebView2)
        except Exception:  # noqa: BLE001
            pass

    def on_new_window_request(self, sender, args):
        opts = _options(self.pywebview_window)
        if opts.get("adblock") or opts.get("catch"):
            args.set_Handled(True)                # a pop-up or pop-under: not opened
            return
        new_window(self, sender, args)

    edge.on_webview_ready = on_webview_ready
    edge.on_new_window_request = on_new_window_request


def _recover(core):
    """The app's own window: if Edge's engine behind it crashes or stops answering (the window
    goes blank), its page is loaded again - the app itself carries on."""
    def failed(sender, args):
        try:
            source = str(sender.Source or "")
            if source.startswith(("http://127.0.0.1", "http://localhost")):
                sender.Reload()
        except Exception:  # noqa: BLE001
            pass
    core.ProcessFailed += failed


def _hook(opts, core):
    """Ad blocking and video catching, in the browser engine of one window (UI thread)."""
    from . import droidweb
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

    watch = opts.get("watch")

    def on_request(sender, args):
        url = str(args.Request.Uri)
        if watch and url.startswith(PRESSED):
            watch.clicked(urllib.parse.unquote(url[len(PRESSED):]))
            args.Response = core.Environment.CreateWebResourceResponse(None, 204, "OK", "")
            return
        if url.startswith(CLICKED):
            opts["clicked_at"] = time.time()
            args.Response = core.Environment.CreateWebResourceResponse(None, 204, "OK", "")
            return
        if url.startswith(NO_CLICK):
            opts["no_click"] = True
            args.Response = core.Environment.CreateWebResourceResponse(None, 204, "OK", "")
            return
        if servers_signal(url, opts):
            args.Response = core.Environment.CreateWebResourceResponse(None, 204, "OK", "")
            return
        if catch and url.startswith(droidweb.MANIFEST):
            # (a playlist the page's scripts got from an address that doesn't say so)
            video = urllib.parse.unquote(url[len(droidweb.MANIFEST):])
            catch(video, (opts.get("seen") or {}).get(video) or {})
            args.Response = core.Environment.CreateWebResourceResponse(None, 204, "OK", "")
            return
        if catch and droidweb._might_be_playlist(url) and len(opts.setdefault("seen", {})) < 3000:
            h = args.Request.Headers
            opts["seen"][url] = {"Referer": header(h, "Referer"), "Origin": header(h, "Origin"),
                                 "User-Agent": header(h, "User-Agent"), "Cookie": header(h, "Cookie")}
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
            if "mpegurl" in kind or "dash+xml" in kind or (kind.startswith("video/") and kind not in ("video/mp2t", "video/iso.segment")):  # (a playlist's piece isn't the video)
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
        if not watch:
            try:
                core.IsMuted = True
            except Exception:  # noqa: BLE001 - (an older Edge engine)
                pass
    scripts = []
    if watch:
        from .picker import TRACK_JS
        scripts.append(TRACK_JS)
    elif catch:
        scripts.append(AUTOPLAY_JS)
        if opts.get("list_all"):
            scripts.append(LIST_ALL_JS)
        scripts.append(SERVERS_JS)
        scripts.append(droidweb.MANIFEST_JS)
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


# What you showed the app on each site (picker.py): {site's server: {"frame", "video", "label"}}.
LEARNED = {}
PRESSED = "https://watchanything.invalid/pressed?label="


def _learned(page_url):
    host = urllib.parse.urlsplit(page_url or "").netloc.lower()
    while host:
        if host in LEARNED:
            return LEARNED[host]
        host = host.partition(".")[2] if host.count(".") > 1 else ""
    return None


def learned_rank(page_url, video, headers):
    """0 for a video like the one you picked on this site before, 1 otherwise."""
    got = _learned(page_url)
    if not got:
        return 1
    ref = urllib.parse.urlsplit((headers or {}).get("Referer") or "").netloc.lower()
    host = urllib.parse.urlsplit(video).netloc.lower()
    return 0 if (got.get("frame") and ref == got["frame"]) or (got.get("video") and host == got["video"]) else 1


def learned_found(page_url, videos):
    """Whether what's been caught has the kind you picked (always, for a site you haven't)."""
    if not _learned(page_url):
        return True
    return any(learned_rank(page_url, v[0], v[1]) == 0 for v in videos)


def learned_click(page_url, click):
    """The button to press on a site's page: the one asked for, else the one you pressed when you picked."""
    if click:
        return click
    got = _learned(page_url)
    return {"label": got["label"], "kind": "", "exact": False} if got and got.get("label") else None


def watch_window(url, job, at=None):
    """Windows: `url` in a window of the app's own whose every video goes to `job` (picker.py).
    Returns what closes it."""
    from . import launcher
    wv = _webview()
    patch()
    opts = {"adblock": ADBLOCK, "watch": job, "subs": job.subs,
            "catch": lambda video, headers: job.video(video, headers)}
    _pending[url] = opts
    x, y, w, h = at or (None, None, 1200, 820)
    win = wv.create_window("Find the video - Watch Anything", url, width=w, height=h, x=x, y=y, background_color="#0d0f1a")
    try:
        win.events.closed += job.closed
        win.events.loaded += lambda: setattr(job, "page", win.get_current_url() or job.page)
    except Exception:  # noqa: BLE001
        pass

    def follow():
        # (a page that changes its address with its scripts - a search, many sites' - isn't loaded again)
        while job.open:
            time.sleep(1)
            try:
                job.page = win.get_current_url() or job.page
            except Exception:  # noqa: BLE001 - closed, or between pages
                pass
    threading.Thread(target=follow, daemon=True).start()
    return win.destroy


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


def render(url, timeout=20, settle=2.5, then=None, cards=False):
    """(page's HTML after its JavaScript has run, its address), or None. `then`: a script run once
    it has loaded (one that may take it to another address, which is waited for). `cards`: results
    that are cards without a link given one (CARD_LINKS_JS) - for a search's results."""
    if _chromium():
        from . import cdp
        return cdp.render(url, timeout, settle, then=then, cards=cards)
    if _phone():
        from . import droidweb
        return droidweb.render(url, timeout, settle, then=then)
    if not can_render():
        return None
    with slot():
        win = _hidden_window(url, {"adblock": True})
        if win is None:
            return None
        try:
            if not win.events.loaded.wait(timeout):
                return None
            if then:
                start = win.get_current_url() or url
                try:
                    win.evaluate_js(then)
                except Exception:  # noqa: BLE001 - it may have left the page already
                    pass
                wait = time.time() + 10
                while time.time() < wait:
                    time.sleep(0.5)
                    try:
                        if (win.get_current_url() or start) != start:
                            time.sleep(settle)
                            break
                    except Exception:  # noqa: BLE001 - between pages
                        pass
            # Wait for what scripts add (an episode list, search results) to settle.
            last, deadline = -1, time.time() + max(settle, 1) * 3
            try:
                # An episode list kept behind a tab ("Episodes 500"): opened, without leaving the page.
                win.evaluate_js(EPISODES_TAB_JS)
            except Exception:  # noqa: BLE001
                pass
            for again in (False, True):
                while time.time() < deadline:
                    time.sleep(settle / 3)
                    count = win.evaluate_js("document.getElementsByTagName('a').length + document.getElementsByTagName('iframe').length")
                    if count == last:
                        break
                    last = count
                # (results that are cards without links: given them, once they're all there)
                if again or not cards or not win.evaluate_js(CARD_LINKS_JS):
                    break
                last, deadline = -1, time.time() + max(settle, 1) * 3
            page = win.evaluate_js("document.documentElement.outerHTML")
            if not isinstance(page, str):
                # (it was moving to another address just then - ShuttleTV's .su to .pk: once more)
                time.sleep(settle)
                page = win.evaluate_js("document.documentElement.outerHTML")
            return (page, win.get_current_url() or url) if isinstance(page, str) else None
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
    if _chromium():
        from . import cdp
        return cdp.catch_video(url, timeout, click)
    if _phone():
        from . import droidweb
        return droidweb.catch_video(url, timeout, click)
    if not can_catch():
        return None
    found, got = [], threading.Event()
    click = learned_click(url, click)
    if click and click.get("quick"):
        timeout = min(timeout, 15)
    opts = {"adblock": True, "click": click}
    skip = _skip()

    def catch(video, headers):
        u = video.lower()
        if skip(video, headers.get("Referer") or headers.get("referer") or ""):
            return
        if blocked(video) or not u.startswith(("http://", "https://")) or re.search(r"\.(ts|m4s|aac|key|vtt|srt|mp3|m4a|ogg|oga|opus|wav|flac)(\?|$)", u) \
                or re.search(r"/(?:tiles?|thumbs?|thumbnails?|sprites?|storyboards?|previews?|trickplay)(?:/|[^/?]*\.m3u8)", u) \
                or trailer(video):
            return
        if not any(v == video for v, _, _ in found):
            found.append((video, {k: v for k, v in headers.items() if v}, time.time()))
            if not click or opts.get("clicked_at"):
                got.set()
    opts["catch"] = catch

    with slot():
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
                    if videos() and (learned_found(url, videos()) or pressed(click, opts, videos())
                                     or time.time() > deadline - timeout + 20):
                        break
                    got.clear()
                if click and not opts.get("clicked_at") and videos() and (opts.get("no_click") or time.time() > deadline - timeout + 15):
                    break                   # the button wasn't found: what the page plays by itself
                if not click and not videos() and opts.get("servers") and time.time() > deadline - timeout + 15:
                    break                   # (nothing in 15 s, and the page has other servers to try)
            found[:] = videos()
            if click and click.get("exact") and not click.get("quick") and not opts.get("clicked_at"):
                # (a server or an episode asked for by name that was never there to press: not
                # what played by itself instead - that would look like a switch that worked)
                return {"url": "", "headers": {}, "subs": [], "servers": servers_of(opts, url)}
            if not found:
                servers = servers_of(opts, url)
                return {"url": "", "headers": {}, "subs": [], "servers": servers} if servers else None
            after = [f for f in found if f[2] >= opts.get("clicked_at", 0)] or found
            best = min(after, key=lambda f: (learned_rank(url, f[0], f[1]),
                                             0 if ".m3u8" in f[0].lower() or ".mpd" in f[0].lower() else 1,
                                             after.index(f)))
            return {"url": best[0], "headers": best[1], "subs": list(opts.get("subs") or []), "videos": [f[0] for f in found],
                    "servers": servers_of(opts, url)}
        finally:
            try:
                win.destroy()
            except Exception:  # noqa: BLE001
                pass


def pressed(click, opts, found):
    """A video asked for by a press (a server, Sub / Dub) that came after it: that's the one."""
    return bool(click and opts.get("clicked_at") and any(f[2] >= opts["clicked_at"] for f in found))


def list_servers(url, timeout=24):
    """The servers an episode's page offers (its player started, as for catch_video):
    [{"label", "kind", "on", "frame"}], [] when it shows none."""
    if _chromium():
        from . import cdp
        return cdp.list_servers(url, timeout)
    if _phone():
        from . import droidweb
        return droidweb.list_servers(url, timeout)
    if not can_catch():
        return []
    opts = {"adblock": True, "catch": lambda video, headers: None, "list_all": True}
    with slot():
        win = _hidden_window(url, opts, "Finding the video…")
        if win is None:
            return []
        try:
            return wait_for_servers(opts, url, timeout)
        finally:
            try:
                win.destroy()
            except Exception:  # noqa: BLE001
                pass


def wait_for_servers(opts, url, timeout):
    """Until the page's list hasn't changed for 3 seconds (once it has one), or `timeout`."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.4)
        if opts.get("servers_at") and time.time() - opts["servers_at"] > 3 and not opts.get("servers_more"):
            break
    return servers_of(opts, url)
