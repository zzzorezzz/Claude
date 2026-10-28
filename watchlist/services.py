"""Where to watch online: your own list of streaming services and sites.

Each service is opened with a search for the title. One you subscribe to can
be marked "only when it has the title": it's then only used when TMDB says
the title streams on it in your country. Plain sites are always searched.
Watch now (online) goes down the list and opens the first that fits; you can
also pick any one yourself.
"""

import re
import urllib.parse
import uuid

TYPES = ("anime", "tv", "movie")
MAX_SERVICES = 30

# Ready-made entries for the Settings list ("check" are TMDB's names for it).
PRESETS = [
    {"name": "Netflix", "url": "https://www.netflix.com/search?q={title}", "check": ["Netflix"]},
    {"name": "HBO Max", "url": "https://play.hbomax.com/search?q={title}", "check": ["HBO Max", "Max"]},
    {"name": "Disney+", "url": "https://www.disneyplus.com/search?q={title}", "check": ["Disney Plus", "Disney+"]},
    {"name": "Prime Video", "url": "https://www.primevideo.com/search?phrase={title}",
     "check": ["Amazon Prime Video", "Prime Video"]},
    {"name": "Crunchyroll", "url": "https://www.crunchyroll.com/search?q={title}", "check": ["Crunchyroll"]},
    {"name": "Hulu", "url": "https://www.hulu.com/search?q={title}", "check": ["Hulu"]},
    {"name": "Apple TV+", "url": "https://tv.apple.com/search?term={title}", "check": ["Apple TV Plus", "Apple TV+"]},
]


def new_id():
    return uuid.uuid4().hex[:8]


def host_name(url):
    host = urllib.parse.urlsplit(url or "").hostname or ""
    return host.removeprefix("www.") or "Your site"


def from_old_settings(s):
    """The list, made from the settings older versions had (Netflix, HBO Max,
    then your own anime and movie & TV sites)."""
    out = [{"id": new_id(), "name": "Netflix", "url": s.get("netflix_url") or PRESETS[0]["url"],
            "check": ["Netflix"], "types": list(TYPES), "on": True},
           {"id": new_id(), "name": "HBO Max", "url": s.get("hbomax_url") or PRESETS[1]["url"],
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
        sid = str(x.get("id") or "") or new_id()
        if sid in seen:
            sid = new_id()
        seen.add(sid)
        out.append({"id": sid, "name": name or host_name(url), "url": url, "check": check, "types": types,
                    "on": bool(x.get("on", True))})
    return out


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
