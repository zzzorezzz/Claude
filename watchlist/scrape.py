"""Playing from your own video sites: "Play online (my sites)".

You add your site in Settings (Your video sites) with its search link, the
same way as a torrent site: search for any title on it, copy the results
page's address and put {title} where the name was. Nothing is built in.

To play an episode:

1. The site is searched for the title (its name, its other names, and for a
   TV show's later season "<name> season N" too), all at once.
2. Every result is scored against the title: the name, then the season, the
   kind (a film, an OVA or a special isn't the TV series) and words the
   title doesn't have ("To the Top", "Shippuden"). The best one is opened;
   when nothing fits exactly, the closest one is.
3. On its page, the links to its episodes are read ("Episode 5", ".../ep-5",
   "S02E05"). When the page has none, its "Watch" / "Episodes" link is
   followed first.
4. The episode's page is read for the video itself: <video> and <source>
   tags, the page's player settings and scripts (.m3u8 / .mp4 links), and
   the players it embeds (iframes), a couple of levels deep.
5. The video's address goes to the player, which streams it: nothing is
   downloaded first, it plays in a few seconds and seeking works. When the
   page has no video the app can read (a player that only works in a
   browser), the episode's page opens in the browser instead.

What was found (the title's page and its episodes' pages) is remembered, so
the next episode starts straight away.
"""

import difflib
import html
import html.parser
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import library, metadata, scanner
from .config import read_json, write_json

MAX_SITES = 10
TYPES = ("anime", "tv", "movie")
MAX_PAGE = 3 * 1024 * 1024
TIMEOUT = 12
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0 Safari/537.36")
VIDEO_EXTS = ("m3u8", "mp4", "webm", "mkv", "mov", "m4v", "mpd")
ENTRY_DAYS = 30          # a title's page on your site is remembered this long
EPISODES_FOR = 3600      # its list of episodes is read again after this, when an episode is missing
VIDEO_FOR = 600          # a video's address (often signed for a while) is reused this long


class ScrapeError(Exception):
    """Nothing playable was found. `page`: the page to open in a browser instead, if any."""

    def __init__(self, message, page=""):
        super().__init__(message)
        self.page = page


# ---- Settings

def new_id():
    return uuid.uuid4().hex[:8]


def clean(sites):
    """Your video sites from Settings, checked. Raises ValueError."""
    if not isinstance(sites, list):
        raise ValueError("video sites must be a list")
    out, seen = [], set()
    for x in sites[:MAX_SITES]:
        if not isinstance(x, dict):
            continue
        name = str(x.get("name") or "").strip()[:40]
        url = str(x.get("url") or "").strip()
        if not name and not url:
            continue
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError(f'{name or "a video site"}: its search link must start with http:// or https://')
        if "{title}" not in url and "{query}" not in url:
            raise ValueError(f'{name or "a video site"}: put {{title}} in its search link where the name goes, '
                             "like https://yoursite/search?q={title}")
        sid = re.sub(r"[^\w-]", "", str(x.get("id") or ""))[:16] or new_id()
        if sid in seen:
            sid = new_id()
        seen.add(sid)
        types = [t for t in TYPES if t in (x.get("types") or [])] or list(TYPES)
        out.append({"id": sid, "name": name or urllib.parse.urlsplit(url).hostname or "My site", "url": url,
                    "types": types, "on": bool(x.get("on", True)),
                    "open": "browser" if x.get("open") == "browser" else "player"})
    return out


def sites_for(settings, item_type):
    return [s for s in settings.get("video_sites") or [] if s["on"] and item_type in s["types"]]


def search_url(site, query):
    q = urllib.parse.quote_plus(query)
    return site["url"].replace("{title}", q).replace("{query}", q)


# ---- reading pages

def fetch(url, referer="", limit=MAX_PAGE, timeout=TIMEOUT):
    """(text, final url). Raises ScrapeError."""
    headers = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
               "Accept-Language": "en-US,en;q=0.9"}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=metadata.ssl_context()) as r:
            data = r.read(limit)
            charset = r.headers.get_content_charset() or "utf-8"
            return data.decode(charset, "replace"), r.geturl()
    except urllib.error.HTTPError as e:
        raise ScrapeError(f"the site answered {e.code} {e.reason}") from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise ScrapeError(f"couldn't reach the site ({getattr(e, 'reason', e)})") from e


_VOID = {"img", "br", "input", "meta", "link", "source", "hr", "wbr", "area", "base", "col", "track", "embed", "param"}
# Words that mark a link or button as one of an episode's streams (a server, sub or dub,
# a quality, a download), not an episode.
SERVER_WORDS = re.compile(r"\b(?:servers?|mirrors?|sources?|providers?|hosts?|sub(?:bed|s)?|soft-?sub|hard-?sub|dub(?:bed)?|raw|"
                          r"english|japanese|audio|quality|\d{3,4}p|hd-?\d|sd|fhd|download|vidstream\w*|stream\s*\d|player\s*\d|option\s*\d)\b",
                          re.I)
_KIND_OF_STREAM = (("dub", re.compile(r"\bdub|\benglish\b|\beng\b", re.I)), ("raw", re.compile(r"\braw\b", re.I)),
                   ("sub", re.compile(r"\bsub|\bsoft-?sub|\bhard-?sub|\bjapanese\b", re.I)))


def stream_kind(*texts):
    """"sub", "dub", "raw" or "" from what a stream's button and its surroundings say."""
    joined = " ".join(t for t in texts if t)
    for kind, pattern in _KIND_OF_STREAM:
        if pattern.search(joined):
            return kind
    return ""


class _Page(html.parser.HTMLParser):
    """The parts of a page that matter here: links (with their text, title,
    pictures' alt text and what they sit in), embedded players, video tags,
    meta tags, and the buttons that switch an episode's stream (servers)."""

    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.links = []            # {"href", "text", "title", "num", "attrs", "ctx"}
        self.servers = []          # {"label", "url", "attrs", "ctx"}: an episode's streams
        self._stack = []           # (tag, its class/id/data words) of the elements we're inside
        self._caps = []            # [depth, server] being read
        self.frames = []
        self.videos = []
        self.metas = {}
        self.title = ""
        self._a = None
        self._in_title = False
        self._skip = 0             # inside <script> / <style>
        self.scripts = []

    def _url(self, value):
        value = (value or "").strip()
        if not value or value.startswith(("javascript:", "data:", "blob:", "mailto:", "#")):
            return ""
        return urllib.parse.urljoin(self.base, html.unescape(value))

    def _ctx(self):
        return " ".join(c for _, c in self._stack[-5:] if c)

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        own = " ".join(v for k, v in a.items() if k in ("class", "id") or (k.startswith("data-") and len(v) < 40))
        own = " ".join([own] + [k for k in a if k.startswith("data-")])
        # A stream / server button: data saying so, or a class or id with "server" in it.
        is_server = bool(re.search(r"server|mirror|source|provider|stream|embed|player-?btn|episode-?link|data-type", own, re.I)
                         and (a.get("data-type") or any(k.startswith(("data-server", "data-link", "data-embed", "data-src",
                                                                       "data-video", "data-url", "data-id", "data-hash"))
                                                        for k in a)
                              or re.search(r"\bserver", own, re.I)))
        if is_server and tag not in ("iframe", "video", "source", "script", "style", "img", "ul", "div") or (
                is_server and tag == "div" and any(k.startswith(("data-server", "data-link", "data-embed", "data-video")) for k in a)):
            url = ""
            for key in ("data-link", "data-embed", "data-src", "data-video", "data-url", "data-iframe", "href"):
                v = a.get(key) or ""
                if v.startswith(("http", "//", "/")) and not v.startswith("/#"):
                    url = self._url(v)
                    break
            server = {"label": "", "url": url, "attrs": own, "ctx": self._ctx(), "tag": tag}
            self.servers.append(server)
            if tag not in _VOID:
                self._caps.append([len(self._stack) + 1, server])
        if tag not in _VOID:
            self._stack.append((tag, own))
        if tag == "a":
            href = self._url(a.get("href"))
            self._a = {"href": href, "text": "", "title": a.get("title") or a.get("data-title") or "",
                       "num": a.get("data-number") or a.get("data-ep") or a.get("data-episode") or "",
                       "attrs": own, "ctx": self._ctx()}
            if not href:
                self._a = None
        elif tag == "img" and self._a is not None:
            alt = a.get("alt") or a.get("title") or ""
            if alt and not self._a["title"]:
                self._a["title"] = alt
        elif tag in ("iframe", "embed", "frame"):
            src = self._url(a.get("src") or a.get("data-src") or a.get("data-lazy-src"))
            if src:
                self.frames.append(src)
        elif tag in ("video", "source"):
            for key in ("src", "data-src", "data-hls", "data-video"):
                src = self._url(a.get(key))
                if src:
                    self.videos.append(src)
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key and a.get("content"):
                self.metas[key] = a["content"]
        elif tag == "title":
            self._in_title = True
        elif tag in ("script", "style"):
            self._skip += 1
        # Players often keep their video in data attributes of a plain element.
        for key in ("data-src", "data-video", "data-file", "data-url", "data-hls", "data-stream"):
            v = a.get(key) or ""
            if tag not in ("a", "iframe", "video", "source", "img") and _VIDEO_URL.search(v):
                self.videos.append(self._url(v))

    def handle_endtag(self, tag):
        if tag not in _VOID:
            for i in range(len(self._stack) - 1, max(-1, len(self._stack) - 8), -1):
                if self._stack[i][0] == tag:
                    depth = i + 1
                    while self._caps and self._caps[-1][0] >= depth:
                        cap = self._caps.pop()
                        cap[1]["label"] = " ".join(cap[1]["label"].split())[:60]
                    del self._stack[i:]
                    break
        if tag == "a" and self._a is not None:
            self._a["text"] = " ".join(self._a["text"].split())
            self.links.append(self._a)
            self._a = None
        elif tag == "title":
            self._in_title = False
        elif tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data):
        if self._skip:
            self.scripts.append(data)
            return
        if self._a is not None:
            self._a["text"] += " " + data
        for _, server in self._caps:
            server["label"] += " " + data
        if self._in_title:
            self.title += data


def parse(text, base):
    p = _Page(base)
    try:
        p.feed(text)
        p.close()
    except Exception:  # noqa: BLE001 - broken HTML: keep what was read
        pass
    return p


# ---- step 2: which result is the title

_KIND = {"movie": "movie", "movies": "movie", "film": "movie", "gekijouban": "movie", "ova": "ova", "ovas": "ova",
         "oad": "ova", "oads": "ova", "ona": "ona", "special": "special", "specials": "special", "recap": "special",
         "picture drama": "special"}
_ROMAN = {"ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6}
_PART = re.compile(r"\b(?:part|cour)\s*(\d{1,2})\b")
_FILLER = {"watch", "online", "free", "english", "eng", "sub", "subbed", "dub", "dubbed", "hd", "full", "episodes",
           "episode", "ep", "eps", "the", "a", "an", "of", "and", "season", "series", "tv", "anime", "stream",
           "streaming", "uncensored", "complete", "new", "latest", "s", "x"}


def _season_of_text(text):
    """The season a name says ("Season 2", "2nd Season", "Second Season", "S2", "II"), else None."""
    n = library.norm(text)
    s = scanner.season_of(n)
    if s:
        return s
    m = re.search(r"\b(ii|iii|iv|vi?)\b$", n)
    if m:
        return _ROMAN[m.group(1)]
    return None


def _kind_of_text(text):
    n = library.norm(text)
    for words, kind in _KIND.items():
        if re.search(rf"\b{words}\b", n):
            return kind
    return ""


def _base_words(text):
    """The words of a name without seasons, kinds, numbers and filler."""
    n = library.norm(text)
    n = re.sub(r"\b(?:season|part|cour)\s*\d+\b|\b\d+(?:st|nd|rd|th)\s+season\b", " ", n)
    n = re.sub(r"\b(?:" + "|".join(scanner._SEASON_WORDS) + r")\s+season\b", " ", n)
    return [w for w in n.split() if w not in _FILLER and w not in _KIND and not w.isdigit()
            and w not in _ROMAN and not re.fullmatch(r"s\d{1,2}", w)]


def wanted_of(item, season=None):
    """What's searched for: {"names", "season", "kind", "year", "part"}. For a TV
    show from TMDB the season is the episode's; for an anime (MyAnimeList keeps
    every season as its own title) it's the title's own."""
    names = [n for n in [item.get("title"), item.get("original_title"), *(item.get("aliases") or [])] if n]
    own = None
    for n in names:
        own = own or _season_of_text(n)
    kind = library.item_kind(item)
    part = None
    for n in names:
        m = _PART.search(library.norm(n))
        if m:
            part = int(m.group(1))
            break
    if item.get("source") == "tmdb" and item.get("format") == "series":
        want = season or 1
    else:
        want = own or 1
    return {"names": names[:6], "season": want, "kind": kind, "year": item.get("year"), "part": part}


def score(text, wanted):
    """0-100: how well a result's name fits the title."""
    if not text:
        return 0
    mine = _base_words(text)
    if not mine:
        return 0
    best = 0.0
    for name in wanted["names"]:
        theirs = _base_words(name)
        if not theirs:
            continue
        a, b = " ".join(mine), " ".join(theirs)
        ratio = difflib.SequenceMatcher(None, a, b).ratio()
        overlap = len(set(mine) & set(theirs)) / len(set(theirs))
        extra = set(mine) - set(theirs)
        s = 100 * max(ratio, overlap * 0.92)
        if a == b:
            s = 100
        # Words the title doesn't have ("to the top", "shippuden", "kai"): another title of the series.
        s -= min(40, 14 * len(extra))
        best = max(best, s)
    if best <= 0:
        return 0
    season = _season_of_text(text)
    part = _PART.search(library.norm(text))
    if (season or 1) != wanted["season"]:
        best -= 45 if season and wanted["season"] == 1 else 35
    if part and wanted.get("part") and int(part.group(1)) != wanted["part"]:
        best -= 30
    elif part and not wanted.get("part") and int(part.group(1)) > 1:
        best -= 20
    elif not part and (wanted.get("part") or 1) > 1:
        best -= 25                    # part 2 wanted, and this says no part (it's part 1)
    kind = _kind_of_text(text)
    have = wanted["kind"]
    if kind and kind != have and not (kind == "ona" and have == "tv"):
        best -= 40
    elif not kind and have in ("movie", "ova", "special") and wanted.get("year") is None:
        best -= 5
    if wanted.get("year") and re.search(rf"\b{wanted['year']}\b", text):
        best += 4
    if re.search(r"\bdub(bed)?\b", text, re.I):
        best -= 3                     # the subbed version first, when both are there
    return max(0, min(100, round(best)))


def _host(url):
    h = (urllib.parse.urlsplit(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def _same_site(a, b):
    ha, hb = _host(a), _host(b)
    return bool(ha) and (ha == hb or ha.endswith("." + hb) or hb.endswith("." + ha))


_NAV = re.compile(r"^(home|login|log in|sign in|sign up|register|contact|dmca|faq|privacy|terms|next|prev|previous|"
                  r"more|menu|search|genres?|a-z list|az list|top|popular|trending|random|schedule|news|filter|"
                  r"\d{1,3}|»|«|>|<|›|‹)$", re.I)


def candidates(page, search_page):
    """The links on a results page that could be titles: [{"url", "name"}], one per address."""
    by = {}
    for link in page.links:
        href = link["href"].split("#")[0]
        if not href or not _same_site(href, search_page) or href.rstrip("/") == search_page.rstrip("/"):
            continue
        path = urllib.parse.urlsplit(href).path
        if path in ("", "/") or re.search(r"/(search|genre|genres|login|register|tag|tags|category|page)/?", path, re.I):
            continue
        texts = [t for t in (link["title"], link["text"]) if t and not _NAV.match(t.strip())]
        if not texts:
            continue
        entry = by.setdefault(href, {"url": href, "names": []})
        entry["names"] += [t[:160] for t in texts]
    out = []
    for e in by.values():
        slug = urllib.parse.unquote(urllib.parse.urlsplit(e["url"]).path.rstrip("/").split("/")[-1])
        slug = re.sub(r"[-_]+", " ", re.sub(r"\.\w{2,5}$", "", slug))
        slug = re.sub(r"\b[0-9a-f]{6,}\b|\b\d{4,}\b", " ", slug)      # ids in the address
        names = list(dict.fromkeys(e["names"] + ([slug] if slug.strip() else [])))
        out.append({"url": e["url"], "name": max(e["names"], key=len), "names": names})
    return out


def best_entry(found, wanted):
    """The results scored, best first: [{"url", "name", "score"}]."""
    scored = []
    for c in found:
        s = max(score(n, wanted) for n in c["names"])
        # A link saying it's an episode is an episode's page, not the title's.
        if re.search(r"\b(?:episode|ep)\s*\d", c["name"], re.I) and not re.search(r"\bepisodes\b", c["name"], re.I):
            s -= 8
        scored.append(dict(c, score=s))
    scored.sort(key=lambda c: (-c["score"], len(c["name"])))
    return scored


# ---- step 3: the episodes on a title's page

_EP_TEXT = [re.compile(r"\b(?:episode|ep|eps|e)\.?\s*[-#:]?\s*(\d{1,4})\b", re.I),
            re.compile(r"^\s*(\d{1,4})\s*$"),
            re.compile(r"第(\d{1,4})[話话集]")]
_EP_HREF = [re.compile(r"(?:episode|ep|eps)[-_/=]?(\d{1,4})(?:\b|_|$)", re.I),
            re.compile(r"[?&](?:ep|episode|e)=(\d{1,4})\b", re.I)]
_SE = [re.compile(r"\bs(\d{1,2})[ ._-]*e(\d{1,4})\b", re.I),
       re.compile(r"\bseason[ ._-]*(\d{1,2})[ ._/-]*(?:episode|ep)[ ._-]*(\d{1,4})\b", re.I),
       re.compile(r"\b(\d{1,2})x(\d{1,3})\b")]
_WATCH = re.compile(r"\b(watch( now| online| episodes?)?|episodes|play( now)?|all episodes|stream)\b", re.I)


def _shape(url):
    """An address with its numbers taken out: links to a title's episodes share one."""
    parts = urllib.parse.urlsplit(url)
    return re.sub(r"\d+", "#", parts.path + ("?" + parts.query if parts.query else ""))


def episode_links(page):
    """{(season or None, episode): url} from a title's page. A link counts when its
    text or address says it's an episode ("Episode 5", ".../ep-5", "S02E05"); a
    bare number ("5") only as one of a run of them that look alike. Buttons for
    an episode's streams (servers, sub / dub, qualities) are never episodes."""
    strong, weak = {}, []
    for link in page.links:
        href = link["href"].split("#")[0]
        text = " ".join(x for x in (link["text"], link["title"]) if x)
        if SERVER_WORDS.search(" ".join((text, link.get("attrs", "")))) or (
                SERVER_WORDS.search(link.get("ctx", "")) and not re.search(r"episode|\bep\b|\beps\b", link.get("ctx", ""), re.I)):
            continue
        found = None
        for p in _SE:
            m = p.search(text) or p.search(urllib.parse.unquote(href))
            if m:
                found = (int(m.group(1)), int(m.group(2)))
                break
        bare = False
        if not found:
            m = _EP_TEXT[0].search(text) or _EP_TEXT[2].search(text)
            if m:
                found = (None, int(m.group(1)))
        if not found:
            path = urllib.parse.unquote(urllib.parse.urlsplit(href).path + "?" + urllib.parse.urlsplit(href).query)
            for p in _EP_HREF:
                m = p.search(path)
                if m:
                    found = (None, int(m.group(1)))
                    break
        if not found and link.get("num", "").isdigit():
            found = (None, int(link["num"]))              # data-number="5": the site says so
        if not found and _EP_TEXT[1].search(text):
            found, bare = (None, int(_EP_TEXT[1].search(text).group(1))), True
        if not found or found[1] <= 0 or re.search(r"\b(next|prev|previous)\b", link["text"], re.I):
            continue
        if bare:
            weak.append((found, href, link))
        else:
            strong.setdefault(found, href)
    if weak:
        # Bare numbers: the biggest group of links that look alike (same address but
        # for the number, or in a list called "episodes"), and only a real run of them.
        groups = {}
        for found, href, link in weak:
            named = bool(re.search(r"episode|\bep\b|\beps\b", link.get("ctx", "") + " " + link.get("attrs", ""), re.I))
            groups.setdefault(("named",) if named else ("shape", _shape(href)), []).append((found, href))
        best = max(groups.values(), key=len)
        numbers = {f[1] for f, _ in best}
        if len(numbers) >= 3 or (("named",) in groups and groups[("named",)] is best and len(numbers) >= 1):
            for found, href in best:
                strong.setdefault(found, href)
    return strong


def pick_episode(eps, season, episode, absolute=None):
    """The page of an episode, from {(season or None, episode): url}."""
    if (season, episode) in eps:
        return eps[(season, episode)]
    seasons = {s for s, _ in eps if s is not None}
    if (None, episode) in eps and (not seasons or seasons == {season}):
        return eps[(None, episode)]
    if absolute and (None, absolute) in eps:
        return eps[(None, absolute)]
    if not seasons and (None, episode) in eps:
        return eps[(None, episode)]
    return None


def watch_links(page, base):
    """The page's "Watch" / "Episodes" links (followed when a title's page lists no episodes)."""
    out = []
    for link in page.links:
        if _WATCH.search(link["text"] or "") and _same_site(link["href"], base) and link["href"] not in out:
            out.append(link["href"])
    return out[:2]


# ---- step 4: the video on an episode's page

_VIDEO_URL = re.compile(r"https?:(?:\\?/){2}[^\s\"'<>()\\]+?\.(?:m3u8|mp4|webm|mkv|mov|m4v|mpd)(?:\?[^\s\"'<>\\]*)?",
                        re.I)
_JSON_SRC = re.compile(r"[\"']?(?:file|src|source|url|hls|video_url|videoUrl|stream|playlist)[\"']?\s*[:=]\s*"
                       r"[\"'](https?:[^\"']+)[\"']", re.I)
_JUNK = re.compile(r"(?:preview|trailer|thumb|sprite|poster|banner|/ads?/|advert|vast|promo|sample|intro\.mp4)", re.I)


def _unescape_js(text):
    return (text.replace("\\/", "/").replace("\\u002F", "/").replace("\\u002f", "/")
            .replace("\\u0026", "&").replace("&amp;", "&"))


_PACKED = re.compile(r"eval\(function\(p,a,c,k,e,[rd]\)\{.*?\}\(\s*'(?P<p>(?:[^'\\]|\\.)*)'\s*,\s*(?P<a>\d+)\s*,"
                     r"\s*(?P<c>\d+)\s*,\s*'(?P<k>(?:[^'\\]|\\.)*)'\.split\('\|'\)", re.S)
_B64 = re.compile(r"[\"']([A-Za-z0-9+/]{40,}={0,2})[\"']")


def _base_n(number, base):
    digits = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    out = ""
    while True:
        number, r = divmod(number, base)
        out = digits[r] + out
        if not number:
            return out


def unpack(text):
    """Scripts packed with the common "eval(function(p,a,c,k,e,d)" packer, unpacked
    (players hide their video's address that way); "" when there are none."""
    out = []
    for m in _PACKED.finditer(text):
        try:
            payload, base, count = m.group("p"), int(m.group("a")), int(m.group("c"))
            words = m.group("k").split("|")
            if base > 62 or count > 100000:
                continue
            table = {_base_n(i, base): (words[i] if i < len(words) and words[i] else _base_n(i, base)) for i in range(count)}
            out.append(re.sub(r"\b\w+\b", lambda w: table.get(w.group(0), w.group(0)), payload.replace("\\'", "'")))
        except (ValueError, IndexError):
            continue
    return "\n".join(out)


def decoded_strings(text):
    """Text hidden in base64 strings (atob("aHR0cHM6Ly...")) that holds a web address."""
    import base64
    out = []
    for m in _B64.finditer(text):
        try:
            plain = base64.b64decode(m.group(1) + "=" * (-len(m.group(1)) % 4)).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            continue
        if "http" in plain:
            out.append(plain)
    return "\n".join(out[:50])


def videos_in(page, text):
    """Every video address on a page, best first (also inside packed scripts and base64)."""
    text = "\n".join(x for x in (text, unpack(text), decoded_strings(text)) if x)
    found = list(page.videos)
    for key in ("og:video", "og:video:url", "og:video:secure_url", "twitter:player:stream"):
        if page.metas.get(key):
            found.append(urllib.parse.urljoin(page.base, page.metas[key]))
    plain = _unescape_js(text)
    found += _VIDEO_URL.findall(plain)
    for m in _JSON_SRC.finditer(plain):
        if re.search(r"\.(?:" + "|".join(VIDEO_EXTS) + r")\b|/hls/|/manifest|playlist", m.group(1), re.I):
            found.append(m.group(1))
    for link in page.links:
        if re.search(r"\.(?:mp4|mkv|webm|m3u8)(?:\?|$)", link["href"], re.I):
            found.append(link["href"])
    seen, out = set(), []
    for u in found:
        u = _unescape_js(u).strip()
        if u.startswith("//"):
            u = "https:" + u
        if not u.lower().startswith(("http://", "https://")) or u in seen or _JUNK.search(u):
            continue
        seen.add(u)
        out.append(u)
    return sorted(out, key=_video_rank)


def _video_rank(url):
    u = url.lower()
    kind = 0 if ".m3u8" in u else 1 if ".mp4" in u else 2 if ".webm" in u else 3 if ".mkv" in u else 4
    m = re.search(r"(2160|1440|1080|720|480|360)p?", u)
    quality = -int(m.group(1)) if m else 0
    master = 0 if re.search(r"master|playlist|index", u) else 1
    return (kind, master, quality)


def _check(url, referer):
    """Whether an address really gives a video (a quick look at its first bytes)."""
    headers = {"User-Agent": UA, "Range": "bytes=0-2047"}
    if referer:
        headers["Referer"] = referer
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=6, context=metadata.ssl_context()) as r:
            kind = (r.headers.get("Content-Type") or "").lower()
            head = r.read(2048)
    except (urllib.error.URLError, OSError, ValueError):
        return False
    if head.lstrip().startswith(b"#EXTM3U"):
        return True
    if "text/html" in kind or head.lstrip()[:1] == b"<" and b"<MPD" not in head[:200]:
        return False
    return True


def find_video(page_url, referer="", depth=2, log=None):
    """{"url", "referer"} for the video on an episode's page. Raises ScrapeError."""
    log = log if log is not None else []
    text, final = fetch(page_url, referer=referer)
    page = parse(text, final)
    found = videos_in(page, text)
    log.append(f"{'  ' * (2 - depth)}Read {final}: {len(found)} video address{'es' if len(found) != 1 else ''}, "
               f"{len(page.frames)} embedded player{'s' if len(page.frames) != 1 else ''}")
    if found:
        with ThreadPoolExecutor(3) as pool:
            ok = list(pool.map(lambda u: _check(u, final), found[:3]))
        for u, good in zip(found[:3], ok):
            if good:
                return {"url": u, "referer": final}
            log.append(f"{'  ' * (2 - depth)}  {u} didn't answer with a video")
    if depth > 0 and page.frames:
        frames = [f for f in dict.fromkeys(page.frames) if not _JUNK.search(f)
                  and not re.search(r"(facebook|twitter|disqus|google|recaptcha|youtube\.com/embed/videoseries)", f)][:4]

        def inside(f):
            try:
                return find_video(f, referer=final, depth=depth - 1, log=log)
            except ScrapeError as e:
                log.append(f"{'  ' * (3 - depth)}{f}: {e}")
                return None
        with ThreadPoolExecutor(4) as pool:
            for got in pool.map(inside, frames):
                if got:
                    return got
    raise ScrapeError("no video the player can use was found on the episode's page", page=final)


# ---- an episode's streams (servers, sub / dub) and a stream's qualities

def streams(page):
    """The ways an episode's page offers it: [{"label", "kind", "url", "how"}].
    "how": "page" (another page of the site with that stream), "embed" (a player
    from another site) or "click" (a button its scripts handle: clicked in the
    hidden browser). Its embedded players count too when nothing names them."""
    out, seen = [], set()
    for sv in page.servers:
        label = sv["label"] or re.sub(r".*?(server|mirror)[-_ ]?", "", sv["attrs"], flags=re.I)[:20] or "Stream"
        kind = stream_kind(sv["label"], sv["attrs"], sv["ctx"])
        url = sv["url"]
        how = "click" if not url else "page" if _same_site(url, page.base) else "embed"
        key = (label.lower(), kind, url)
        if key in seen or (how == "page" and url.split("#")[0] == page.base.split("#")[0] and not sv["label"]):
            continue
        seen.add(key)
        out.append({"label": label, "kind": kind, "url": url, "how": how})
    named = {x["url"] for x in out}
    for f in dict.fromkeys(page.frames):
        if f not in named and not _JUNK.search(f) and not re.search(r"facebook|twitter|disqus|google|recaptcha", f):
            out.append({"label": _host(f) or "Player", "kind": "", "url": f, "how": "embed"})
    for i, x in enumerate(out):
        x["id"] = f"{i}:{x['kind']}:{x['label']}"[:80]
    return out


def order_streams(found, prefs):
    """Streams in the order they're tried: the one you chose last time on this site,
    then others of the same kind (sub, else what you prefer), raw last."""
    want_kind = prefs.get("kind") or "sub"
    want_label = (prefs.get("label") or "").lower()

    def rank(x):
        return (0 if x["label"].lower() == want_label and x["kind"] == want_kind else 1,
                0 if x["kind"] == want_kind else 1 if not x["kind"] else 2 if x["kind"] != "raw" else 3,
                found.index(x))
    return sorted(found, key=rank)


def qualities(url, headers=None):
    """The qualities an HLS playlist offers: [{"label", "height", "url"}], best first
    ([] for a single-quality stream or anything else)."""
    if ".m3u8" not in url.lower():
        return []
    req = urllib.request.Request(url, headers={"User-Agent": (headers or {}).get("User-Agent") or UA,
                                               **{k: v for k, v in (headers or {}).items() if k in ("Referer", "Origin") and v}})
    try:
        with urllib.request.urlopen(req, timeout=8, context=metadata.ssl_context()) as r:
            text = r.read(512 * 1024).decode("utf-8", "replace")
            base = r.geturl()
    except (urllib.error.URLError, OSError, ValueError):
        return []
    out, lines = [], text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF"):
            m = re.search(r"RESOLUTION=\d+x(\d+)", line)
            nxt = next((x.strip() for x in lines[i + 1:i + 3] if x.strip() and not x.startswith("#")), "")
            if nxt:
                height = int(m.group(1)) if m else 0
                out.append({"label": f"{height}p" if height else f"Stream {len(out) + 1}", "height": height,
                            "url": urllib.parse.urljoin(base, nxt)})
    seen, unique = set(), []
    for q in sorted(out, key=lambda q: -q["height"]):
        if q["label"] not in seen:
            seen.add(q["label"])
            unique.append(q)
    return unique if len(unique) > 1 else []


def pick_quality(found, wanted):
    """The quality to play: the one asked for, else the nearest below it, else the best."""
    if not found or not wanted or wanted == "auto":
        return None
    height = int(re.sub(r"\D", "", str(wanted)) or 0)
    return next((q for q in found if q["height"] and q["height"] <= height), found[-1]) if height else None


# ---- yt-dlp: the players of hundreds of video hosts, and plain pages too

class _Quiet:
    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


def has_ytdlp():
    try:
        import yt_dlp  # noqa: F401
        return True
    except ImportError:
        return False


def ytdlp_video(url, referer=""):
    """{"url", "referer", "headers"} for the video yt-dlp finds at an address (an
    episode's page or the player it embeds), with the exact headers the stream
    wants (so the player isn't turned away). None when yt-dlp isn't there.
    Raises ScrapeError when it finds nothing."""
    try:
        import yt_dlp
    except ImportError:
        return None
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True, "socket_timeout": 12,
            "extractor_retries": 1, "logger": _Quiet(), "cachedir": False,
            "http_headers": {"User-Agent": UA, **({"Referer": referer} if referer else {})}}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:  # noqa: BLE001 - yt-dlp's own errors: nothing usable there
        raise ScrapeError("yt-dlp found no video there (" + re.sub(r"^ERROR:\s*|\x1b\[[0-9;]*m", "", str(e))[:120] + ")") from e
    while info and info.get("_type") in ("playlist", "multi_video") and info.get("entries"):
        info = next((x for x in info["entries"] if x), None)
    if not info:
        raise ScrapeError("yt-dlp found no video there")
    formats = [f for f in info.get("formats") or [] if f.get("url")]
    hls = [f for f in formats if str(f.get("protocol", "")).startswith("m3u8") and f.get("manifest_url")]
    both = [f for f in formats if f.get("vcodec") != "none" and f.get("acodec") != "none"]
    chosen = hls[-1] if hls else both[-1] if both else (formats[-1] if formats else info)
    video = chosen.get("manifest_url") if hls else chosen.get("url") or info.get("url")
    if not video:
        raise ScrapeError("yt-dlp found no video there")
    headers = dict(chosen.get("http_headers") or info.get("http_headers") or {})
    if chosen.get("cookies") or info.get("cookies"):
        headers["Cookie"] = chosen.get("cookies") or info.get("cookies")
    headers = {k: str(v) for k, v in headers.items() if k in ("Referer", "Origin", "User-Agent", "Cookie") and v}
    return {"url": video, "referer": headers.pop("Referer", "") or referer or url, "headers": headers}


# ---- putting it together

class Scraper:
    """Finds and remembers where each title's episodes are on your sites."""

    def __init__(self, data_dir):
        self.path = Path(data_dir) / "scraped.json"
        self.lock = threading.Lock()
        self.memory = read_json(self.path, {})
        if not isinstance(self.memory, dict):
            self.memory = {}
        # Set by the app: a page read after its JavaScript has run ((html, address) or None),
        # and a video caught from a page's player ({"url", "headers"} or None).
        self.render = None
        self.catch = None
        self.can_render = lambda: True
        self.can_catch = lambda: True
        self.ytdlp = ytdlp_video       # (a stand-in in tests)
        self.log = []                  # what the last search did, step by step (the site's Test)

    def _renderer(self):
        return self.render if self.render and self.can_render() else None

    def _catcher(self):
        return self.catch if self.catch and self.can_catch() else None

    def _note(self, text):
        self.log.append(text)

    def _read(self, url, enough, referer=""):
        """A page as a ScrapeError-free (text, address): read as it is, and when that
        isn't `enough` (nothing found that's needed), after its JavaScript has run."""
        try:
            text, final = fetch(url, referer=referer)
        except ScrapeError as e:
            text, final = "", url
            self._note(f"Couldn't read {url} ({e})")
        if enough(parse(text, final)) or not self._renderer():
            return text, final
        self._note(f"Nothing usable in {final} as it is - reading it after its scripts have run")
        got = self.render(url)
        if got:
            return got[0], got[1]
        self._note("The page couldn't be opened in the hidden browser")
        return text, final

    def _save(self):
        with self.lock:
            write_json(self.path, self.memory, compact=True)

    def remembered(self, item_id, site):
        """What was found for a title on a site. Nothing once the site's link has
        changed in Settings (it's then another site, even with the same name)."""
        mem = (self.memory.get(item_id) or {}).get(site["id"])
        if mem and mem.get("link") != site["url"]:
            return None
        return mem

    def forget(self, item_id):
        if self.memory.pop(item_id, None) is not None:
            self._save()

    def search(self, site, item, season=None):
        """The site's results for a title, scored: [{"url", "name", "score"}]."""
        wanted = wanted_of(item, season)
        queries = list(dict.fromkeys(n for n in wanted["names"][:3]))
        if wanted["season"] > 1 and item.get("source") == "tmdb":
            queries.insert(1, f"{wanted['names'][0]} season {wanted['season']}")

        errors = []

        def one(q):
            url = search_url(site, q)
            try:
                text, final = fetch(url)
            except ScrapeError as e:
                errors.append(str(e))
                text, final = "", url
            found = candidates(parse(text, final), final)
            if not found and self._renderer():
                got = self.render(url)                 # results the site only shows with JavaScript
                if got:
                    found = candidates(parse(got[0], got[1]), got[1])
                    if found and errors:
                        errors.pop()
            return found

        found, seen = [], set()
        with ThreadPoolExecutor(4) as pool:
            for rows in pool.map(one, queries[:4]):
                for r in rows:
                    if r["url"] not in seen:
                        seen.add(r["url"])
                        found.append(r)
        if not found and errors and len(errors) == len(queries[:4]):
            raise ScrapeError(f"{site['name']}: {errors[0]}")
        ranked = best_entry(found, wanted)
        self._note(f"Searched {site['name']} for {', '.join(queries[:4])}: {len(ranked)} results"
                   + (" - best: " + "; ".join(f"{c['name']} ({c['score']}%)" for c in ranked[:3]) if ranked else ""))
        return ranked

    def _entry(self, site, item, season, pin=""):
        """(entry url, entry name, other results) for a title, remembered."""
        mem = self.remembered(item["id"], site) or {}
        key = str(season or "")
        entries = mem.get("entries") or {}
        if pin:
            got = {"url": pin, "name": (next((c["name"] for c in mem.get("others") or [] if c["url"] == pin), pin)),
                   "at": time.time(), "pinned": True}
            entries[key] = got
            self._remember(item["id"], site, entries=entries, episodes={})
            return got, mem.get("others") or []
        got = entries.get(key)
        if got and (got.get("pinned") or time.time() - got.get("at", 0) < ENTRY_DAYS * 86400):
            return got, mem.get("others") or []
        ranked = self.search(site, item, season)
        if not ranked or ranked[0]["score"] < 25:
            raise ScrapeError(f"{site['name']} has nothing that looks like {item['title']}"
                              + (f" (closest: {ranked[0]['name']})" if ranked else ""))
        best = ranked[0]
        got = {"url": best["url"], "name": best["name"], "score": best["score"], "at": time.time()}
        self._note(f"Opened {best['name']} ({best['score']}% match): {best['url']}")
        entries[key] = got
        others = [{"url": c["url"], "name": c["name"], "score": c["score"]} for c in ranked[:8]]
        self._remember(item["id"], site, entries=entries, others=others, episodes={})
        return got, others

    def _remember(self, item_id, site, **patch):
        with self.lock:
            sites = self.memory.setdefault(item_id, {})
            mem = sites.get(site["id"])
            if not mem or mem.get("link") != site["url"]:
                mem = sites[site["id"]] = {"link": site["url"]}      # a new link: start afresh
            mem.update(patch)
        self._save()

    def _episodes(self, entry_url, fresh=False):
        """{(season, episode): url} on a title's page (following its Watch link if needed)."""
        text, final = self._read(entry_url, lambda p: bool(episode_links(p) or watch_links(p, entry_url)))
        page = parse(text, final)
        eps = episode_links(page)
        if not eps:
            for link in watch_links(page, final):
                t2, f2 = self._read(link, lambda p: bool(episode_links(p)), referer=final)
                eps = episode_links(parse(t2, f2))
                if eps:
                    self._note(f"Followed its link {link}")
                    break
        self._note(f"{final}: {len(eps)} episode link{'s' if len(eps) != 1 else ''}")
        return eps, final, page, text

    def episode_page(self, site, item, season, episode, pin=""):
        """(page url of the episode or film, entry, others)."""
        movie = item.get("format") == "movie" or library.item_kind(item) == "movie"
        tv_season = season if item.get("source") == "tmdb" and item.get("format") == "series" else None
        entry, others = self._entry(site, item, tv_season, pin)
        if movie:
            return entry["url"], entry, others
        mem = self.remembered(item["id"], site) or {}
        cached = (mem.get("episodes") or {})
        key = f"{tv_season or ''}:{season}:{episode}"
        if key in cached:
            return cached[key], entry, others
        eps, final, page, text = self._episodes(entry["url"])
        absolute = None
        if item.get("seasons") and season and season > 1:
            absolute = sum(s.get("episodes") or 0 for s in item["seasons"] if 0 < s["number"] < season) + episode
        want_season = season if tv_season else None
        url = pick_episode(eps, want_season, episode, absolute) or pick_episode(eps, None, episode, absolute)
        if not url:
            if not eps and videos_in(page, text):
                url = final            # the title's page plays it itself
            elif episode == 1 and not eps:
                url = final
            else:
                raise ScrapeError(f"episode {episode} wasn't found on {site['name']}'s page for {entry['name']}"
                                  + (f" (it lists {len(eps)} episodes)" if eps else ""), page=final)
        cached = dict(cached)
        cached[key] = url
        self._remember(item["id"], site, episodes=cached)
        return url, entry, others

    def resolve(self, site, item, season=None, episode=None, pin="", stream="", quality="", skip=()):
        """Everything needed to play: {"url" (the video, or "" when only the page
        works), "page", "referer", "headers", "entry", "others", "site",
        "streams" (the episode's sub / dub / server choices), "stream" (the one
        playing), "qualities", "quality"}. The episode's streams are tried in
        order (the one you chose before first) until one gives a video; `skip`
        leaves out streams that didn't play. Raises ScrapeError."""
        self.log = []
        page_url, entry, others = self.episode_page(site, item, season, episode, pin)
        out = {"page": page_url, "entry": entry, "others": others, "site": site["name"], "url": "", "referer": "",
               "headers": {}, "streams": [], "stream": None, "qualities": [], "quality": ""}
        self._note(f"The episode's page: {page_url}")
        if site.get("open") == "browser":
            return out
        prefs = self.memory.setdefault("_sites", {}).setdefault(site["url"], {})
        if stream:
            prefs["kind"], prefs["label"] = (stream.split(":", 2) + ["", ""])[1:3]
        if quality:
            prefs["quality"] = quality
        mem = self.remembered(item["id"], site) or {}
        cache_key = f"{page_url}|{prefs.get('kind', '')}|{prefs.get('label', '')}"
        hit = (mem.get("videos") or {}).get(cache_key)
        if hit and not skip and time.time() - hit.get("at", 0) < VIDEO_FOR:
            out.update({k: hit.get(k) for k in ("url", "referer", "headers", "streams", "stream", "qualities")})
            self._note(f"Used the video found a few minutes ago: {hit['url']}")
            return self._with_quality(out, prefs)

        text, final = self._read(page_url, lambda p: bool(streams(p) or videos_in(p, "")))
        page = parse(text, final)
        found = order_streams(streams(page), prefs)
        out["streams"] = [{k: x[k] for k in ("id", "label", "kind")} for x in found]
        if found:
            names = ", ".join(x["label"] + (f" ({x['kind']})" if x["kind"] else "") for x in found[:8])
            self._note(f"{len(found)} stream{'s' if len(found) != 1 else ''}: {names}")
        tries = [x for x in found if x["id"] not in skip][:4] or ([] if found else [None])
        errors = []
        for x in tries:
            try:
                got = self._stream_video(page_url, entry, prefs, x, text, final)
            except ScrapeError as e:
                errors.append(f"{x['label'] if x else 'the page'}: {e}")
                self._note(f"  {errors[-1]}")
                continue
            out.update(url=got["url"], referer=got["referer"], headers=got.get("headers") or {},
                       stream=({k: x[k] for k in ("id", "label", "kind")} if x else None))
            out["qualities"] = [{k: q[k] for k in ("label", "height", "url")} for q in qualities(got["url"], out["headers"] | {"Referer": out["referer"]})]
            videos = {k: v for k, v in (mem.get("videos") or {}).items() if time.time() - v.get("at", 0) < VIDEO_FOR}
            videos[cache_key] = {k: out[k] for k in ("url", "referer", "headers", "streams", "stream", "qualities")} | {"at": time.time()}
            self._remember(item["id"], site, videos=videos)
            return self._with_quality(out, prefs)
        raise ScrapeError("; ".join(errors) if errors else "no video the player can use was found on the episode's page",
                          page=page_url)

    def _with_quality(self, out, prefs):
        q = pick_quality(out.get("qualities") or [], prefs.get("quality"))
        out["master"] = out["url"]
        if q:
            out["url"], out["quality"] = q["url"], q["label"]
        else:
            out["quality"] = "auto" if out.get("qualities") else ""
        return out

    def _stream_video(self, page_url, entry, prefs, x, text, final):
        """{"url", "referer", "headers"} for one of an episode's streams (None: the page as it is)."""
        if x is None or x["how"] in ("embed", "page"):
            target, ref = (page_url, entry["url"]) if x is None else (x["url"], page_url)
            of = f" of {x['label']}" if x else ""
            way = prefs.get("way") or ("browser" if prefs.get("browser") else "")
            if way not in ("ytdlp", "browser"):
                try:
                    got = find_video(target, referer=ref, log=self.log)
                    self._note(f"Found the video{of}: {got['url']}")
                    return got
                except ScrapeError as e:
                    self._note(f"Not found by reading {target} ({e})")
            if way != "browser":
                try:
                    got = self.ytdlp(target, referer=ref)
                    if got:
                        self._note(f"yt-dlp found the video{of}: {got['url']}")
                        prefs["way"] = "ytdlp"
                        return got
                    self._note("(yt-dlp isn't in this copy of the app)")
                except ScrapeError as e:
                    self._note(f"  {e}")
            if not self._catcher():
                raise ScrapeError("its video was found neither by reading the page nor by yt-dlp")
        if not self._catcher():
            raise ScrapeError("this stream's button only works in a browser")
        choosing = f" and choosing {x['label']}" if x else ""
        self._note(f"Opening the episode in a hidden browser window{choosing}…")
        target = x["url"] if x and x["how"] == "page" else page_url
        caught = self.catch(target, click=({"label": x["label"], "kind": x["kind"]} if x and x["how"] != "page" else None))
        if not caught:
            raise ScrapeError("its player didn't ask for a video within half a minute")
        headers = caught.get("headers") or {}
        self._note(f"Caught the video its player asked for: {caught['url']}")
        prefs["browser"], prefs["way"] = True, "browser"
        return {"url": caught["url"], "referer": headers.get("Referer") or page_url,
                "headers": {k: v for k, v in headers.items() if k != "Referer"}}

    def diagnose(self, site, item, season=None, episode=None):
        """The site's Test: everything again from scratch (nothing remembered is
        used), each step written down. {"ok", "steps", "url", "error"}."""
        mem = self.memory.get(item["id"], {}).pop(site["id"], None) or {}
        pinned = {k: v for k, v in (mem.get("entries") or {}).items() if v.get("pinned")}
        if pinned and mem.get("link") == site["url"]:
            self._remember(item["id"], site, entries=pinned)    # (a title you picked yourself is kept)
        self.memory.get("_sites", {}).pop(site["url"], None)
        try:
            got = self.resolve(site, item, season, episode)
            return {"ok": True, "steps": list(self.log), "url": got["url"], "page": got["page"], "error": ""}
        except ScrapeError as e:
            return {"ok": False, "steps": list(self.log), "url": "", "page": e.page, "error": str(e)}


def test_site(site, query):
    """Searches a site and shows what's read: {"count", "examples", "url"}."""
    url = search_url(site, query)
    text, final = fetch(url)
    found = best_entry(candidates(parse(text, final), final), {"names": [query], "season": 1, "kind": "tv",
                                                                "year": None, "part": None})
    return {"url": url, "query": query, "count": len(found),
            "examples": [{"name": c["name"], "score": c["score"]} for c in found[:5]]}

