"""Your own torrent site: "Get magnet" on an anime's page finds its torrent there.

You add your site in Settings with its search link ({title} is replaced with
what's searched). Nothing else is built in: no site comes with the app.

1. The site is searched for the anime (its name on MyAnimeList, its English
   name, and the series' name, which finds packs of every season).
2. Its results page is read: each result's name, size and seeders (from the
   column whose heading says so), and its own page.
3. The results that are about this anime are opened, and the magnet link is
   taken from the page, or made from the info hash the page shows, or from
   the page's .torrent file.
4. The best few are checked for real: their list of files is fetched from
   the people sharing them, and worked out like any torrent (packs.py), so
   what's counted is how many of this title's episodes each one really has.
   Quality (1080p, Blu-ray) and seeders then decide; with an AI key, the AI
   compares them too.
5. One that clearly wins is added; when several are as good, you choose,
   seeing each one's files.
"""

import base64
import binascii
import html.parser
import math
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import ai, library, metadata, packs, scanner

MAX_SITES = 10
MAX_PAGE = 4 * 1024 * 1024
MAX_TORRENT_FILE = 8 * 1024 * 1024
OPEN_PAGES = 10          # result pages opened to find a magnet
CHECK = 6                # torrents whose list of files is fetched
PEEK_WAIT = 45           # seconds to wait for those lists
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0 Safari/537.36 Watchlist")


class SiteError(Exception):
    pass


# ---- Settings

def new_id():
    return uuid.uuid4().hex[:8]


def clean(sites):
    """Your sites from Settings, checked. Raises ValueError."""
    if not isinstance(sites, list):
        raise ValueError("torrent sites must be a list")
    out, seen = [], set()
    for x in sites[:MAX_SITES]:
        if not isinstance(x, dict):
            continue
        name = str(x.get("name") or "").strip()[:40]
        url = str(x.get("url") or "").strip()
        if not name and not url:
            continue
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError(f'{name or "a torrent site"}: its search link must start with http:// or https://')
        if "{title}" not in url and "{query}" not in url:
            raise ValueError(f'{name or "a torrent site"}: put {{title}} in its search link where the name goes, '
                             "like https://yoursite/search?q={title}")
        sid = str(x.get("id") or "") or new_id()
        if sid in seen:
            sid = new_id()
        seen.add(sid)
        out.append({"id": sid, "name": name or urllib.parse.urlsplit(url).hostname or "My site", "url": url,
                    "on": bool(x.get("on", True))})
    return out


def search_url(site, query):
    q = urllib.parse.quote_plus(query)
    return site["url"].replace("{title}", q).replace("{query}", q)


# ---- reading pages

def fetch(url, limit=MAX_PAGE, timeout=20):
    """(bytes, final url, content type). Raises SiteError."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=metadata.ssl_context()) as r:
            data = r.read(limit + 1)
            if len(data) > limit:
                raise SiteError("the page is too big")
            return data, r.geturl(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        raise SiteError(f"the site answered {e.code} {e.reason}") from e
    except (urllib.error.URLError, OSError) as e:
        raise SiteError(f"couldn't reach the site ({getattr(e, 'reason', e)})") from e


def _text(data):
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


class _Page(html.parser.HTMLParser):
    """A page as what matters here: its tables' rows (each cell's text and
    links), every link, and its text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.links, self.text, self.title = [], [], [], ""
        self._row = None
        self._cell = None
        self._link = None
        self._in_title = False
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "tr":
            self._row = {"cells": [], "head": False}
        elif tag in ("td", "th") and self._row is not None:
            self._cell = {"text": [], "links": [], "head": tag == "th",
                          "hint": " ".join(filter(None, [a.get("title"), a.get("class"), a.get("data-title")]))}
            self._row["cells"].append(self._cell)
        elif tag == "a":
            self._link = {"href": a.get("href") or "", "text": [], "title": a.get("title") or ""}
            self.links.append(self._link)
            if self._cell is not None:
                self._cell["links"].append(self._link)
        elif tag in ("i", "span", "img") and self._cell is not None:
            hint = " ".join(filter(None, [a.get("title"), a.get("class"), a.get("alt")]))
            if hint:
                self._cell["hint"] += " " + hint
        if tag in ("br", "p", "div", "li", "tr", "h1", "h2", "h3", "dt", "dd"):
            self.text.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self._skip = max(0, self._skip - 1)
        elif tag == "title":
            self._in_title = False
        elif tag == "a":
            self._link = None
        elif tag in ("td", "th"):
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row["cells"]:
                self.rows.append(self._row)
            self._row, self._cell = None, None

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self.title += data
        self.text.append(data)
        if self._link is not None:
            self._link["text"].append(data)
        if self._cell is not None:
            self._cell["text"].append(data)


def parse_page(data):
    p = _Page()
    try:
        p.feed(_text(data))
        p.close()
    except Exception:  # noqa: BLE001 - a broken page: what was read so far
        pass
    for link in p.links:
        link["text"] = " ".join("".join(link["text"]).split())
    for row in p.rows:
        for c in row["cells"]:
            c["text"] = " ".join("".join(c["text"]).split())
    p.text = "".join(p.text)
    return p


_HEX = re.compile(r"(?<![0-9a-fA-F])([0-9a-fA-F]{40})(?![0-9a-fA-F])")
_B32 = re.compile(r"(?<![A-Z2-7])([A-Z2-7]{32})(?![A-Z2-7])")
_BTIH = re.compile(r"urn:btih:([0-9a-fA-F]{40}|[A-Za-z2-7]{32})")
_SIZE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(TiB|GiB|MiB|KiB|TB|GB|MB|KB)\b", re.I)
_UNITS = {"k": 1024, "m": 1024 ** 2, "g": 1024 ** 3, "t": 1024 ** 4}
_TRACKER = re.compile(r"\b((?:udp|https?|wss?)://[^\s\"'<>]+/announce[^\s\"'<>]*)", re.I)


def norm_hash(h):
    """A 40-character lower-case hex info hash, from hex or base32."""
    h = (h or "").strip()
    if re.fullmatch(r"[0-9a-fA-F]{40}", h):
        return h.lower()
    if re.fullmatch(r"[A-Za-z2-7]{32}", h):
        try:
            return binascii.hexlify(base64.b32decode(h.upper())).decode()
        except (binascii.Error, ValueError):
            return ""
    return ""


def size_of(text):
    m = _SIZE.search(text or "")
    if not m:
        return 0
    return int(float(m.group(1).replace(",", ".")) * _UNITS[m.group(2)[0].lower()])


def make_magnet(info_hash, name="", trackers=()):
    out = f"magnet:?xt=urn:btih:{info_hash}"
    if name:
        out += "&dn=" + urllib.parse.quote(name)
    for t in list(dict.fromkeys(trackers))[:12]:
        out += "&tr=" + urllib.parse.quote(t, safe="")
    return out


def _trackers_of_magnet(magnet):
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(magnet).query)
    return q.get("tr", [])


def _column(headers, *words):
    for i, h in enumerate(headers):
        if any(w in h for w in words):
            return i
    return None


def _number(text):
    m = re.search(r"\d[\d,]*", text or "")
    return int(m.group().replace(",", "")) if m else None


def results(data, base_url):
    """The results on a search page: [{"name", "page", "magnet", "hash",
    "torrent", "size", "seeders", "leechers"}]."""
    page = parse_page(data)
    out = []
    headers = []
    for row in page.rows:
        cells = row["cells"]
        if all(c["head"] for c in cells) or (not out and not any(c["links"] for c in cells) and len(cells) > 2):
            headers = [f'{c["text"]} {c["hint"]}'.lower() for c in cells]
            continue
        found = _from_row(cells, headers, base_url)
        if found:
            out.append(found)
    if not out:
        # Not a table: every link that looks like a result, on its own.
        for link in page.links:
            href, name = link["href"], link["text"] or link["title"]
            if href.startswith("magnet:"):
                h = _BTIH.search(href)
                out.append({"name": name or urllib.parse.parse_qs(urllib.parse.urlsplit(href).query).get("dn", [""])[0],
                            "page": "", "magnet": href, "hash": norm_hash(h.group(1)) if h else "", "torrent": "",
                            "size": 0, "seeders": None, "leechers": None})
            elif len(name) >= 12 and not href.startswith(("#", "javascript:", "mailto:")):
                out.append({"name": name, "page": urllib.parse.urljoin(base_url, href), "magnet": "", "hash": "",
                            "torrent": urllib.parse.urljoin(base_url, href) if _is_torrent_link(href) else "",
                            "size": 0, "seeders": None, "leechers": None})
    seen, unique = set(), []
    for r in out:
        key = r["hash"] or r["page"] or r["name"]
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def _is_torrent_link(href):
    return urllib.parse.urlsplit(href).path.lower().endswith(".torrent")


def _from_row(cells, headers, base_url):
    links = [link for c in cells for link in c["links"]]
    magnet = next((link["href"] for link in links if link["href"].startswith("magnet:")), "")
    torrent = next((urllib.parse.urljoin(base_url, link["href"]) for link in links if _is_torrent_link(link["href"])), "")
    named = [link for link in links if not link["href"].startswith(("magnet:", "#", "javascript:", "mailto:"))
             and not _is_torrent_link(link["href"]) and "comment" not in link["href"].lower()]
    best = max(named, key=lambda link: len(link["text"] or link["title"]), default=None)
    name = (best["text"] or best["title"]) if best else ""
    if magnet and len(name) < 8:
        name = urllib.parse.parse_qs(urllib.parse.urlsplit(magnet).query).get("dn", [name])[0]
    if len(name) < 8:
        return None
    row_text = " ".join(c["text"] for c in cells)
    h = _BTIH.search(magnet) if magnet else None
    info_hash = norm_hash(h.group(1)) if h else ""
    if not info_hash:
        m = _HEX.search(row_text)
        info_hash = m.group(1).lower() if m else ""
    if not (magnet or info_hash or torrent or best):
        return None
    seeders = leechers = None
    size = 0
    if headers and len(headers) == len(cells):
        i = _column(headers, "seed", "seeder")
        seeders = _number(cells[i]["text"]) if i is not None else None
        i = _column(headers, "leech", "peer")
        leechers = _number(cells[i]["text"]) if i is not None else None
        i = _column(headers, "size")
        size = size_of(cells[i]["text"]) if i is not None else 0
    if seeders is None:
        # No headings: a cell marked as seeders (its class or icon), else nothing.
        for c in cells:
            if "seed" in c["hint"].lower() and _number(c["text"]) is not None:
                seeders = _number(c["text"])
                break
    return {"name": name, "page": urllib.parse.urljoin(base_url, best["href"]) if best else "",
            "magnet": magnet, "hash": info_hash, "torrent": torrent,
            "size": size or size_of(row_text), "seeders": seeders, "leechers": leechers}


def details(data, base_url):
    """What a result's own page gives: {"magnet", "hash", "torrent", "trackers", "name", "size"}."""
    page = parse_page(data)
    magnet = next((link["href"] for link in page.links if link["href"].startswith("magnet:")), "")
    torrent = next((urllib.parse.urljoin(base_url, link["href"]) for link in page.links
                    if _is_torrent_link(link["href"])), "")
    info_hash = ""
    if magnet:
        m = _BTIH.search(magnet)
        info_hash = norm_hash(m.group(1)) if m else ""
    if not info_hash:
        # The info hash shown as text: next to its label, else the only one on the page.
        text = page.text
        label = re.search(r"info\s*-?\s*hash|hash", text, re.I)
        if label:
            near = text[label.end():label.end() + 200]
            m = _HEX.search(near) or _B32.search(near)
            if m:
                info_hash = norm_hash(m.group(1))
        if not info_hash:
            found = {h.lower() for h in _HEX.findall(text)}
            if len(found) == 1:
                info_hash = found.pop()
    trackers = _trackers_of_magnet(magnet) if magnet else []
    trackers += [t for t in _TRACKER.findall(page.text) if t not in trackers]
    heading = " ".join(page.title.split())
    return {"magnet": magnet, "hash": info_hash, "torrent": torrent, "trackers": trackers[:12],
            "name": heading, "size": size_of(page.text)}


def torrent_file(data):
    """{"hash", "name", "files", "trackers"} of a .torrent file, or None."""
    from .torrents import lt
    if lt is None:
        return None
    try:
        ti = lt.torrent_info(lt.bdecode(data))
    except Exception:  # noqa: BLE001 - not a torrent file
        return None
    from .torrents import _hash_of, _is_pad
    fs = ti.files()
    files = [{"index": i, "path": fs.file_path(i), "size": fs.file_size(i)}
             for i in range(fs.num_files()) if not _is_pad(fs, i)]
    try:
        info_hash = str(ti.info_hashes().v1)
    except AttributeError:
        info_hash = _hash_of(ti)
    return {"hash": norm_hash(info_hash), "name": ti.name(), "files": files,
            "trackers": [t.url for t in ti.trackers()]}


# ---- what a release is

_RANGE = re.compile(r"(?<![\dx])(\d{1,4})\s*(?:-|~|to)\s*(\d{1,4})(?![\dp])", re.I)
_QUALITY = [(re.compile(r"\b2160p\b|\b4k\b|\buhd\b", re.I), 2160), (re.compile(r"\b1080p?\b", re.I), 1080),
            (re.compile(r"\b720p?\b", re.I), 720), (re.compile(r"\b(?:480|576)p?\b", re.I), 480)]
_BD = re.compile(r"\b(?:bd|bdrip|blu-?ray|bdremux|remux)\b", re.I)
_BAD = re.compile(r"\b(?:cam|ts|hdcam|telesync|raw)\b", re.I)


def quality(name):
    for pattern, res in _QUALITY:
        if pattern.search(name or ""):
            return res
    return 0


def describe_release(name):
    """What a release's name says: {"pack", "range", "episode", "resolution", "bd"}."""
    text = name or ""
    pack = bool(scanner._MANY_SEASONS.search(text)) or bool(re.search(
        r"\b(?:movies?|ovas?|oads?|specials?)\b.*\+|\+.*\b(?:movies?|ovas?|oads?)\b", text, re.I))
    clean = re.sub(r"\[[^\]]*\]|\((?:[^)]*\b(?:1080p|720p|x26[45]|hevc|bd|aac|flac)\b[^)]*)\)", " ", text, flags=re.I)
    rng = None
    for m in _RANGE.finditer(clean):
        a, b = int(m.group(1)), int(m.group(2))
        if a < b <= 2000 and not (1900 <= a <= 2100):
            rng = (a, b)
    episode = None
    if not rng and not pack:
        _, eps = scanner.parse_episode([" ".join(clean.split()) + ".mkv"])
        if len(eps) == 1 and not re.search(r"\b(?:batch|complete|collection)\b", text, re.I):
            episode = eps[0]
    return {"pack": pack, "range": rng, "episode": episode, "resolution": quality(text), "bd": bool(_BD.search(text)),
            "bad": bool(_BAD.search(clean))}


def _words(text):
    return {w for w in library.norm(text).split() if w not in library._STOP}


def about(name, item_names, franchise_words):
    """0-100: how much a release's name is about this anime (its own name),
    or about the whole series (its words, for packs)."""
    words = _words(name)
    if not words:
        return 0
    best = 0
    for n in item_names:
        mine = _words(n)
        if mine and mine <= words:
            best = max(best, 100)
        elif mine:
            best = max(best, round(100 * len(mine & words) / len(mine)))
    if franchise_words and franchise_words <= words:
        best = max(best, 70)
    return best


def _entry_fit(name, entry, common):
    """0-100: how many of what makes an entry's name its own ("second", "top")
    a release's name has."""
    words = packs._words(name, common)
    best = 0
    for n in entry.get("names") or [entry.get("title") or ""]:
        theirs = packs._words(n, common)
        if theirs:
            best = max(best, round(100 * len(theirs & words) / len(theirs)))
    return best


# ---- finding the torrent

class Finder:
    """One "Get magnet" run for a title, in the background, followed by the page."""

    def __init__(self, app, item):
        self.app, self.item = app, item
        self.state = {"running": True, "stage": "Starting…", "error": "", "choices": [], "added": None,
                      "searched": [], "log": [], "at": time.time()}

    def stage(self, text):
        self.state["stage"] = text

    def log(self, text):
        self.state["log"].append(text)

    def run(self):
        try:
            self._run()
        except SiteError as e:
            self.state["error"] = str(e)
        except metadata.MetadataError as e:
            self.state["error"] = f"MyAnimeList: {e}"
        except Exception as e:  # noqa: BLE001 - shown on the page rather than lost
            self.state["error"] = f"something went wrong: {e}"
        finally:
            self.state["running"] = False
            self.state["stage"] = ""

    def _run(self):
        settings = self.app.settings.get()
        sites = [s for s in settings.get("torrent_sites") or [] if s["on"]]
        item = self.item
        if not sites:
            raise SiteError("add your torrent site in Settings first")
        if item.get("source") != "mal" or not settings.get("mal_client_id"):
            raise SiteError("this works for anime from MyAnimeList (with a MyAnimeList Client ID in Settings)")
        self.stage("Getting the series from MyAnimeList…")
        ents = packs.entries(settings, item["source_id"])
        me = next((e for e in ents if e["source_id"] == item["source_id"]), None)
        item_names = list(dict.fromkeys(n for n in [item["title"], item.get("original_title"), *(item.get("aliases") or [])]
                                        if n))[:6]
        common = packs._common_words(ents) if len(ents) > 1 else set()
        queries = self._queries(item, item_names, common)
        # 1. Searching.
        found = []
        for site in sites:
            for q in queries:
                self.stage(f'Searching {site["name"]} for "{q}"…')
                url = search_url(site, q)
                self.state["searched"].append(url)
                try:
                    data, final, _ = fetch(url)
                except SiteError as e:
                    self.log(f'{site["name"]}: {e}')
                    continue
                rows = results(data, final)
                self.log(f'{site["name"]} "{q}": {len(rows)} results')
                for r in rows:
                    r["site"] = site["name"]
                    r["site_id"] = site["id"]
                found += rows
        found = self._unique(found)
        # 2. The ones about this anime, best first by what their names say.
        others = [e for e in ents if e["source_id"] != item["source_id"]]
        for r in found:
            r["about"] = about(r["name"], item_names, common)
            r["release"] = describe_release(r["name"])
            # Named after another title of the series ("To the Top" when this is Second Season).
            mine = _entry_fit(r["name"], me, common) if me else 0
            elsewhere = max((_entry_fit(r["name"], e, common) for e in others), default=0)
            r["elsewhere"] = elsewhere >= 60 and elsewhere > mine and not r["release"]["pack"]
        wanted = me["episodes"] if me and me.get("episodes") else None
        found = [r for r in found if r["about"] >= 60 and not r["release"]["bad"]]
        film = item.get("format") == "movie" or (wanted == 1)
        if not film:
            found = [r for r in found if r["release"]["episode"] is None]      # one episode isn't what's needed
        if not found:
            raise SiteError("nothing on your site matched this anime" + (
                f' (searched for {", ".join(queries)})' if queries else ""))
        for r in found:
            r["score"] = self._name_score(r, wanted)
        found.sort(key=lambda r: -r["score"])
        # 3. Their magnets: from the result, its page, or its .torrent file.
        self.stage("Reading the results' pages for their magnet links…")
        top = found[:OPEN_PAGES]
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(self._complete, top))
        ready = [r for r in top if r.get("hash")]
        if not ready:
            raise SiteError("found results, but no magnet link or info hash on their pages")
        # 4. Their real files.
        check = ready[:CHECK]
        self.stage(f"Getting the list of files of the best {len(check)} from the people sharing them…")
        self._peek(check)
        for r in check:
            self._cover(settings, r, ents)
        # 5. Deciding.
        checked = [r for r in check if r.get("files")]
        for r in ready:
            r["final"] = self._final_score(r, wanted)
        ranked = sorted(ready, key=lambda r: -r["final"])
        if ai.available(settings) and len(ranked) > 1:
            self.stage("Asking your AI helper to compare them…")
            self._ask_ai(settings, ranked[:6], me or {"title": item["title"], "episodes": wanted})
            ranked.sort(key=lambda r: -r["final"])
        self.state["choices"] = [self._view(r) for r in ranked[:6]]
        best = ranked[0]
        full = [r for r in checked if r.get("complete")]
        clear = (best in full and (len(full) == 1 or best["final"] - max(
            (r["final"] for r in full if r is not best), default=0) >= 15))
        if clear:
            self.stage("Adding it…")
            self.state["added"] = self.use(best)
        elif not checked:
            self.log("none of them could be checked: nobody sharing them answered in time")

    @staticmethod
    def _queries(item, names, common):
        out = []
        romaji = item.get("original_title") or ""
        for q in [romaji, item["title"], *(item.get("aliases") or [])[:2]]:
            q = " ".join(re.sub(r"[^\w\s'-]", " ", q or "").split())
            if q and q.lower() not in (x.lower() for x in out):
                out.append(q)
        # The series' name, for packs of every season ("Haikyuu" for Haikyuu!! Second Season).
        base = packs._base(library.norm(romaji or item["title"]))
        if common:
            base = " ".join(w for w in library.norm(romaji or item["title"]).split() if w in common) or base
        if base and base.lower() not in (x.lower() for x in out):
            out.append(base)
        return out[:3]

    @staticmethod
    def _unique(rows):
        seen, out = set(), []
        for r in rows:
            key = r["hash"] or r["page"] or r["name"]
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
        return out

    @staticmethod
    def _name_score(r, wanted):
        rel = r["release"]
        score = r["about"] * 0.6 - (35 if r.get("elsewhere") else 0)
        if rel["range"] and wanted:
            a, b = rel["range"]
            score += 25 if a <= 1 and b >= wanted else 8
        if rel["pack"]:
            score += 15
        score += {2160: 18, 1080: 20, 720: 8, 480: -10}.get(rel["resolution"], 0) + (8 if rel["bd"] else 0)
        if r.get("seeders") is not None:
            score += min(25, 9 * math.log10(1 + r["seeders"]))
            if r["seeders"] == 0:
                score -= 30
        return score

    def _complete(self, r):
        """Its info hash (and trackers, and maybe its files) from its page or .torrent file."""
        try:
            if not r["hash"] and r["page"]:
                data, final, ctype = fetch(r["page"])
                if "bittorrent" in ctype or data[:11] == b"d8:announce" or data[:1] == b"d" and b"4:info" in data[:4000]:
                    r.update(self._from_torrent(data) or {})
                else:
                    d = details(data, final)
                    r["hash"] = d["hash"]
                    r["trackers"] = d["trackers"]
                    r["torrent"] = r["torrent"] or d["torrent"]
                    if d["magnet"]:
                        r["magnet"] = d["magnet"]
                    if not r.get("size"):
                        r["size"] = d["size"]
            if not r["hash"] and r["torrent"]:
                data, _, _ = fetch(r["torrent"], limit=MAX_TORRENT_FILE)
                r.update(self._from_torrent(data) or {})
        except SiteError as e:
            self.log(f'{r["name"]}: {e}')
        if r["hash"]:
            trackers = list(dict.fromkeys((r.get("trackers") or []) + (
                _trackers_of_magnet(r["magnet"]) if r.get("magnet") else [])))
            r["trackers"] = trackers
            r["magnet"] = r.get("magnet") or make_magnet(r["hash"], r["name"], trackers)

    @staticmethod
    def _from_torrent(data):
        t = torrent_file(data)
        if not t or not t["hash"]:
            return None
        return {"hash": t["hash"], "files": t["files"], "trackers": t["trackers"],
                "size": sum(f["size"] for f in t["files"])}

    def _peek(self, rows):
        torrents = getattr(self.app, "torrents", None)
        todo = [r for r in rows if not r.get("files")]
        if not todo or torrents is None:
            return
        got = torrents.peek_many([r["magnet"] for r in todo], wait=PEEK_WAIT)
        for r in todo:
            info = got.get(r["hash"]) or {}
            if info.get("files"):
                r["files"] = info["files"]
                r["size"] = sum(f["size"] for f in info["files"])
            if info.get("seeds") is not None and info["seeds"] >= 0:
                r["live_seeders"] = info["seeds"]

    def _cover(self, settings, r, ents):
        """How much of this title a torrent really has, from its list of files."""
        files = [{"key": f["index"], "rel": f["path"], "size": f["size"]} for f in r.get("files") or []
                 if Path(f["path"]).suffix.lower() in scanner.VIDEO_EXTS]
        if not files:
            return
        result = packs.plan(settings, self.item["source_id"], files, use_ai=False, ents=ents)
        sid = self.item["source_id"]
        mine, others, best_res = set(), set(), 0
        count = 0
        for g in result["groups"]:
            for key, (target, ep) in g["parts"].items():
                if target == sid:
                    count += 1
                    mine.add(ep or 1)
                elif target:
                    others.add(target)
        for f in files:
            best_res = max(best_res, quality(f["rel"]))
        me = next((e for e in ents if e["source_id"] == sid), {})
        wanted = me.get("episodes") or self.item.get("episodes_total") or 0
        film = self.item.get("format") == "movie"
        r["has"] = len(mine) if not film else (1 if count else 0)
        r["wanted"] = wanted if not film else 1
        r["complete"] = bool(r["has"]) and (r["has"] >= (r["wanted"] or r["has"]))
        r["others"] = len(others)
        r["file_resolution"] = best_res

    def _final_score(self, r, wanted):
        score = r["score"]
        if r.get("files") is not None and "has" in r:
            share = r["has"] / max(1, r["wanted"] or r["has"])
            score += 60 * share + (25 if r["complete"] else 0) - (40 if not r["has"] else 0)
        else:
            score -= 20                       # couldn't be checked: only its name to go on
        res = r.get("file_resolution") or 0
        if res and res != r["release"]["resolution"]:
            score += {2160: 18, 1080: 20, 720: 8, 480: -10}.get(res, 0) - {2160: 18, 1080: 20, 720: 8, 480: -10}.get(
                r["release"]["resolution"], 0)
        seeds = r.get("live_seeders")
        if seeds is not None and r.get("seeders") is None:
            score += min(25, 9 * math.log10(1 + seeds))
        return round(score, 1)

    def _ask_ai(self, settings, rows, entry):
        import json
        listing = [{"n": i, "name": r["name"], "size_gb": round((r.get("size") or 0) / 1024 ** 3, 1),
                    "seeders": r.get("live_seeders", r.get("seeders")),
                    "checked_files": ({"has_episodes": r["has"], "of": r["wanted"], "other_titles": r["others"]}
                                      if "has" in r else "not checked")} for i, r in enumerate(rows)]
        prompt = (
            "Pick the best torrent for an anime from this list. Wanted: "
            f'{json.dumps({"title": entry.get("title"), "episodes": entry.get("episodes")}, ensure_ascii=False)}. '
            "It must have every episode of that title (a pack with other seasons, films or OVAs of the series is "
            "fine: only what's needed downloads). Prefer good quality (1080p, Blu-ray, a known release group) and "
            "more seeders; avoid ones with missing episodes, wrong seasons, hardcoded-sub raws or tiny low-quality "
            "encodes. checked_files is what the app found in each torrent's real list of files.\n"
            f"{json.dumps(listing, ensure_ascii=False)}\n"
            'Reply as JSON: {"order": [n, ...best first], "why": {"n": "short reason"}}')
        try:
            reply, provider = ai.ask(settings, prompt, timeout=40, budget=60)
        except ai.AIError as e:
            self.log(f"AI: {e}")
            return
        if not isinstance(reply, dict):
            return
        order = [n for n in reply.get("order") or [] if isinstance(n, int) and 0 <= n < len(rows)]
        why = reply.get("why") or {}
        for rank, n in enumerate(dict.fromkeys(order)):
            r = rows[n]
            best_cover = max((x.get("has", 0) for x in rows), default=0)
            if r.get("has", best_cover) >= best_cover:       # never over one that has more of it
                r["final"] += max(0, 12 - 4 * rank)
            r["why"] = str(why.get(str(n)) or why.get(n) or "")[:200]
        self.state["ai"] = ai.NAMES[provider]

    def _view(self, r):
        rel = r["release"]
        return {"key": r["hash"], "name": r["name"], "site": r["site"], "size": r.get("size") or 0,
                "seeders": r.get("live_seeders", r.get("seeders")), "resolution": r.get("file_resolution") or rel["resolution"],
                "bd": rel["bd"], "pack": rel["pack"], "has": r.get("has"), "wanted": r.get("wanted"),
                "complete": r.get("complete"), "others": r.get("others", 0), "checked": "has" in r,
                "why": r.get("why", ""), "score": r.get("final"), "magnet": r["magnet"],
                "files": [{"path": f["path"], "size": f["size"]} for f in (r.get("files") or [])][:400]}

    def use(self, r):
        view = r if "key" in r else self._view(r)
        t = self.app.torrents.add(self.item["id"], view["magnet"])
        return {"hash": t["hash"], "name": view["name"]}


# ---- the jobs the pages follow

class Jobs:
    def __init__(self, app):
        self.app = app
        self.jobs = {}
        self.lock = threading.Lock()

    def start(self, item):
        with self.lock:
            job = self.jobs.get(item["id"])
            if job and job.state["running"]:
                return job.state
            job = Finder(self.app, item)
            self.jobs[item["id"]] = job
        threading.Thread(target=job.run, daemon=True).start()
        return job.state

    def get(self, item_id):
        job = self.jobs.get(item_id)
        return job.state if job else None

    def use(self, item_id, key):
        job = self.jobs.get(item_id)
        if not job:
            raise SiteError("search again: that list is gone")
        choice = next((c for c in job.state["choices"] if c["key"] == key), None)
        if not choice:
            raise SiteError("that torrent isn't in the list any more")
        job.state["added"] = job.use(choice)
        return job.state


def test_site(site, query):
    """What the app reads from your site's search page (Settings → Test)."""
    url = search_url(site, query)
    data, final, _ = fetch(url)
    rows = results(data, final)
    return {"url": url, "count": len(rows),
            "with_magnet": sum(1 for r in rows if r["magnet"] or r["hash"]),
            "with_page": sum(1 for r in rows if r["page"]),
            "with_seeders": sum(1 for r in rows if r["seeders"] is not None),
            "examples": [{"name": r["name"], "size": r["size"], "seeders": r["seeders"],
                          "magnet": bool(r["magnet"] or r["hash"])} for r in rows[:8]]}
