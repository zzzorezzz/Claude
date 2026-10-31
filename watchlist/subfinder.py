"""Subtitles found online for a title's episodes (OpenSubtitles' open search, no key
needed): searched, ranked (by your AI helper when one is set up, else by the names),
downloaded, and - once you say one is in sync - the same release for every episode."""
import difflib
import gzip
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from . import ai, library, metadata

SEARCH = "https://rest.opensubtitles.org/search"
# OpenSubtitles' open search answers without a key when it's asked like this.
HEADERS = {"User-Agent": "Mozilla/5.0", "X-User-Agent": "TemporaryUserAgent"}


class SubError(Exception):
    pass


def _get(url, timeout=20, limit=5_000_000):
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=metadata.ssl_context()) as r:
            return r.read(limit)
    except urllib.error.HTTPError as e:
        raise SubError(f"the subtitles site answered {e.code}") from e
    except (urllib.error.URLError, OSError) as e:
        raise SubError(f"couldn't reach the subtitles site ({getattr(e, 'reason', e)})") from e


def imdb_of(item):
    """The title's IMDb id ("tt0388629"), when its info has one."""
    m = re.search(r"tt\d{5,}", json.dumps(item.get("links") or {}))
    return m.group(0) if m else ""


def search(item, season=None, episode=None, lang="eng"):
    """Every English subtitle found for an episode (or a film): [{"id", "name", "release",
    "season", "episode", "downloads", "format", "url", "uploader"}]."""
    series = item.get("format") == "series"
    paths = []
    imdb = imdb_of(item)
    if imdb:
        p = f"imdbid-{imdb[2:]}"
        paths.append(f"{SEARCH}/episode-{episode}/{p}/season-{season or 1}/sublanguageid-{lang}" if series
                     else f"{SEARCH}/{p}/sublanguageid-{lang}")
    for name in [n for n in [item.get("title"), item.get("original_title"), *(item.get("aliases") or [])] if n][:3]:
        q = urllib.parse.quote(library.norm(name) or name)
        paths.append(f"{SEARCH}/episode-{episode}/query-{q}/season-{season or 1}/sublanguageid-{lang}" if series
                     else f"{SEARCH}/query-{q}/sublanguageid-{lang}")
        if series and item.get("type") == "anime":
            paths.append(f"{SEARCH}/query-{q}%20{episode:02d}/sublanguageid-{lang}")   # (anime often numbered on their own)
    out, seen = [], set()
    errors = []
    for url in paths:
        try:
            rows = json.loads(_get(url) or b"[]")
        except (SubError, ValueError) as e:
            errors.append(str(e))
            continue
        for r in rows if isinstance(rows, list) else []:
            sid = str(r.get("IDSubtitleFile") or "")
            if not sid or sid in seen or (r.get("SubFormat") or "srt").lower() not in ("srt", "ass", "ssa", "vtt"):
                continue
            seen.add(sid)
            out.append({"id": sid, "name": str(r.get("MovieName") or ""), "release": str(r.get("MovieReleaseName") or "").strip(),
                        "season": int(r.get("SeriesSeason") or 0), "episode": int(r.get("SeriesEpisode") or 0),
                        "downloads": int(r.get("SubDownloadsCnt") or 0), "format": (r.get("SubFormat") or "srt").lower(),
                        "url": str(r.get("SubDownloadLink") or ""), "uploader": str(r.get("UserNickName") or ""),
                        "fps": str(r.get("MovieFPS") or ""), "year": int(r.get("MovieYear") or 0)})
    if not out and errors:
        raise SubError(errors[0])
    return out


def _fits(c, item, season, episode):
    """0-100: how well a subtitle's names fit the title and episode."""
    from . import scrape
    wanted = scrape.wanted_of(item, season)
    s = max(scrape.score(c["name"], wanted), scrape.score(re.sub(r"[._]", " ", c["release"]), wanted))
    years = [int(y) for y in re.findall(r"\b(19[2-9]\d|20[0-4]\d)\b", f"{c['release']} {c['name']}")] + ([c["year"]] if c.get("year") else [])
    if item.get("year") and years:
        # Another year is another title (Dune 1984, the live-action Death Note of 2015).
        s += 10 if any(abs(y - int(item["year"])) <= 1 for y in years) else -50
    if item.get("format") == "series":
        nums = {int(x) for x in re.findall(r"(?:e|ep|episode|-|\s)(\d{1,4})(?!\d|p\b)", c["release"].lower())}
        if c["episode"] == episode or episode in nums:
            s += 25
        elif c["episode"] or nums:
            s -= 40
    return s


def rank(item, season, episode, found, source=""):
    """The candidates best first: by the names (title and episode), then how often they were
    downloaded; your AI helper, when there is one, picks the order of the best few."""
    scored = sorted(found, key=lambda c: (-_fits(c, item, season, episode), -c["downloads"]))
    scored = [c for c in scored if _fits(c, item, season, episode) >= 40] or scored[:5]
    return scored


def ai_order(settings, item, season, episode, candidates, source=""):
    """Your AI helper's order for the best few (the most likely to be in sync with the video
    first); the order as it is when there's no helper or it doesn't answer."""
    top = candidates[:12]
    if len(top) < 2 or not ai.available(settings):
        return candidates
    what = f"{item['title']}" + (f" season {season} episode {episode}" if item.get("format") == "series" else f" ({item.get('year') or ''})")
    lines = "\n".join(f"{i}. {c['release'] or c['name']} (title: {c['name']}, S{c['season']}E{c['episode']}, "
                      f"{c['downloads']} downloads, {c['fps']} fps)" for i, c in enumerate(top))
    prompt = (f"I'm watching {what}" + (f", streamed from {source}" if source else "") + ".\n"
              f"Which of these English subtitle files is most likely the right episode and in sync with it? "
              f"Prefer ones made for web streams (WEB, WEBRip, CR, Crunchyroll, NF) for an online stream.\n{lines}\n"
              'Answer only JSON: {"order": [the numbers, best first, leaving out wrong titles or episodes]}')
    try:
        reply, _ = ai.ask(settings, prompt, timeout=30)
        order = [int(i) for i in ai.parse_json(reply).get("order", []) if str(i).isdigit() and int(i) < len(top)]
    except (ai.AIError, ValueError, AttributeError, TypeError):
        return candidates
    picked = [top[i] for i in dict.fromkeys(order)]
    return picked + [c for c in candidates if c not in picked]


def download(c):
    """A subtitle file's text (bytes), unpacked."""
    data = _get(c["url"])
    if data[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(data)
        except OSError as e:
            raise SubError("the subtitle file was damaged") from e
    if not re.search(rb"\d{1,2}:\d{2}:\d{2}[,.]\d{3}\s*-->|Dialogue:", data[:20000]):
        raise SubError("that wasn't a subtitle file")
    return data


def _family(release):
    """A release's name without its episode numbers (one release group's files share it)."""
    n = re.sub(r"[._\-\s]+", " ", release.lower())
    n = re.sub(r"\b(?:s\d{1,2}e\d{1,4}|e?\d{1,4}v?\d?|\d{1,2}x\d{1,3})\b", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def same_source(chosen, candidates):
    """The candidate from the same release (group) as the subtitle you said is in sync, else
    the best one."""
    fam = _family(chosen["release"] or chosen["name"])
    best = max(candidates, key=lambda c: (difflib.SequenceMatcher(None, fam, _family(c["release"] or c["name"])).ratio()
                                           + (0.2 if chosen["uploader"] and c["uploader"] == chosen["uploader"] else 0)
                                           + (0.1 if c["fps"] == chosen["fps"] else 0)), default=None)
    return best
