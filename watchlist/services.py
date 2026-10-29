"""Where to watch online: your own list of streaming services and sites.

Each service is opened with a search for the title. Each one's availability
check ("check_mode") says whether it's used only when it has the title in your
country: "tmdb" (TMDB's where-to-watch data), "ai" (your AI helper's best
knowledge), or "off" (always searched, like your own sites). Watch now
(online) goes down the list and opens the first that fits; you can also pick
any one yourself. Nothing is opened that you didn't add.
"""

import re
import urllib.parse
import uuid

TYPES = ("anime", "tv", "movie")
MAX_SERVICES = 30

# Ready-made entries for the Settings list ("check" are TMDB's names for it).
PRESETS = [
    {"name": "Netflix", "url": "https://www.netflix.com/search?q={title}", "check": ["Netflix"]},
    {"name": "Prime Video", "url": "https://www.primevideo.com/search?phrase={title}",
     "check": ["Amazon Prime Video", "Prime Video"]},
    {"name": "Disney+", "url": "https://www.disneyplus.com/search?q={title}", "check": ["Disney Plus", "Disney+"]},
    {"name": "HBO Max", "url": "https://play.hbomax.com/search?q={title}", "check": ["HBO Max", "Max"]},
    {"name": "Hulu", "url": "https://www.hulu.com/search?q={title}", "check": ["Hulu"]},
    {"name": "Apple TV+", "url": "https://tv.apple.com/search?term={title}", "check": ["Apple TV Plus", "Apple TV+"]},
    {"name": "Crunchyroll", "url": "https://www.crunchyroll.com/search?q={title}", "check": ["Crunchyroll"],
     "types": ["anime"]},
    {"name": "Paramount+", "url": "https://www.paramountplus.com/search/?q={title}",
     "check": ["Paramount Plus", "Paramount+"]},
    {"name": "Peacock", "url": "https://www.peacocktv.com/watch/search?q={title}", "check": ["Peacock", "Peacock Premium"]},
    {"name": "HIDIVE", "url": "https://www.hidive.com/search?q={title}", "check": ["HIDIVE"], "types": ["anime"]},
    {"name": "Viki", "url": "https://www.viki.com/search?q={title}", "check": ["Rakuten Viki", "Viki"]},
    {"name": "YouTube", "url": "https://www.youtube.com/results?search_query={title}", "check": ["YouTube"]},
    {"name": "Tubi", "url": "https://tubitv.com/search/{title}", "check": ["Tubi TV", "Tubi"]},
    {"name": "Pluto TV", "url": "https://pluto.tv/search/details?query={title}", "check": ["Pluto TV"]},
    {"name": "Plex", "url": "https://watch.plex.tv/search?q={title}", "check": ["Plex"]},
    {"name": "JustWatch (where it streams)", "url": "https://www.justwatch.com/us/search?q={title}", "check": []},
]
CHECK_MODES = ("tmdb", "ai", "off")


def new_id():
    return uuid.uuid4().hex[:8]


def host_name(url):
    host = urllib.parse.urlsplit(url or "").hostname or ""
    return host.removeprefix("www.") or "Your site"


def from_old_settings(s):
    """The list, made from the settings older versions had (Netflix, HBO Max,
    then your own anime and movie & TV sites)."""
    preset = {p["name"]: p for p in PRESETS}
    out = [{"id": new_id(), "name": "Netflix", "url": s.get("netflix_url") or preset["Netflix"]["url"],
            "check": ["Netflix"], "types": list(TYPES), "on": True},
           {"id": new_id(), "name": "HBO Max", "url": s.get("hbomax_url") or preset["HBO Max"]["url"],
            "check": ["HBO Max", "Max"], "types": list(TYPES), "on": True}]
    if s.get("anime_site_url"):
        out.append({"id": new_id(), "name": host_name(s["anime_site_url"]), "url": s["anime_site_url"],
                    "check": [], "types": ["anime"], "on": True})
    if s.get("movie_tv_site_url"):
        out.append({"id": new_id(), "name": host_name(s["movie_tv_site_url"]), "url": s["movie_tv_site_url"],
                    "check": [], "types": ["tv", "movie"], "on": True})
    return out


def clean(services):
    """A list of services from Settings, checked. Raises ValueError."""
    if not isinstance(services, list):
        raise ValueError("services must be a list")
    out, seen = [], set()
    for x in services[:MAX_SERVICES]:
        if not isinstance(x, dict):
            continue
        name = str(x.get("name") or "").strip()[:40]
        url = str(x.get("url") or "").strip()
        if not name and not url:
            continue
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError(f'{name or "a service"}: its link must start with http:// or https://')
        types = [t for t in TYPES if t in (x.get("types") or [])] or list(TYPES)
        check = [str(c).strip()[:40] for c in (x.get("check") or []) if str(c).strip()][:6]
        mode = x.get("check_mode")
        if mode not in CHECK_MODES:
            mode = "tmdb" if check else "off"        # (lists from before there was a choice)
        if mode == "tmdb" and not check:
            check = [name or host_name(url)]
        sid = str(x.get("id") or "") or new_id()
        if sid in seen:
            sid = new_id()
        seen.add(sid)
        out.append({"id": sid, "name": name or host_name(url), "url": url, "check": check, "check_mode": mode,
                    "types": types, "on": bool(x.get("on", True))})
    return out


def mode_of(service):
    mode = service.get("check_mode")
    return mode if mode in CHECK_MODES else ("tmdb" if service.get("check") else "off")


def _norm(name):
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower().replace("+", "plus"))


def has_it(service, streaming_names):
    """Whether one of the names TMDB gave for where it streams is this service."""
    wanted = {_norm(c) for c in service.get("check") or []}
    for name in streaming_names:
        n = _norm(name)
        if n in wanted or any(n.startswith(w) and n[len(w):] in ("amazonchannel", "withads", "basicwithads", "standardwithads")
                              for w in wanted):
            return True
    return False


def fill(service, title):
    return service["url"].replace("{title}", urllib.parse.quote_plus(title))
