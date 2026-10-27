"""Online info: TMDB for movies and TV, MyAnimeList for anime.

TMDB also answers "where can I watch this?" per country, which is how the app
knows whether a title is on Netflix or HBO Max in Pakistan.
"""

import html
import json
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "Watchlist/1.0 (personal desktop app)"
TIMEOUT = 15

TMDB_API = "https://api.themoviedb.org/3"
TMDB_IMG = "https://image.tmdb.org/t/p/"
MAL_API = "https://api.myanimelist.net/v2"
JIKAN_API = "https://api.jikan.moe/v4"

TMDB_ANIMATION = 16

# TMDB watch-provider IDs (names are matched too, in case IDs change).
NETFLIX_IDS = {8, 1796}
HBOMAX_IDS = {384, 1899, 1825}


class MetadataError(Exception):
    pass


_ssl_context = None


def ssl_context():
    """Trusted certificates for HTTPS. Windows provides its own; the Python
    inside a packaged Mac app finds none, so use the bundled certifi list."""
    global _ssl_context
    if _ssl_context is None:
        ctx = ssl.create_default_context()
        if not ctx.cert_store_stats().get("x509_ca"):
            try:
                import certifi
                ctx = ssl.create_default_context(cafile=certifi.where())
            except (ImportError, OSError):
                pass
        _ssl_context = ctx
    return _ssl_context


def _get_json(url, headers=None, service="the server"):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json",
                                               **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ssl_context()) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise MetadataError(f"{service} rejected your key - check it in Settings") from e
        if e.code == 404:
            raise MetadataError(f"{service} doesn't have that title") from e
        if e.code == 429:
            raise MetadataError(f"{service} is rate limiting - try again in a few seconds") from e
        raise MetadataError(f"{service} returned an error (HTTP {e.code})") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, ssl.SSLError):
            raise MetadataError(f"couldn't make a secure connection to {service} ({reason})") from e
        raise MetadataError(f"couldn't reach {service} ({reason}) - are you online?") from e
    except ValueError as e:
        raise MetadataError(f"{service} sent an unreadable reply") from e


class _Cache:
    def __init__(self, ttl):
        self.ttl = ttl
        self._data = {}
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            hit = self._data.get(key)
            if hit and time.time() - hit[0] < self.ttl:
                return hit[1]
        return None

    def put(self, key, value):
        with self._lock:
            self._data[key] = (time.time(), value)


_season_cache = _Cache(6 * 3600)
_provider_cache = _Cache(6 * 3600)
_related_cache = _Cache(24 * 3600)


def _year(date):
    date = str(date or "")
    return int(date[:4]) if date[:4].isdigit() else None


def _clean_text(text):
    text = html.unescape(re.sub(r"<[^>]+>", "", text or ""))
    text = re.sub(r"\s*\[Written by MAL Rewrite\]\s*$", "", text)
    text = re.sub(r"\s*\(Source:[^)]*\)\s*$", "", text)
    return text.strip()


def _unique(values):
    seen, out = set(), []
    for v in values:
        v = (v or "").strip()
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)
    return out


def norm_title(text):
    text = re.sub(r"[^a-z0-9]+", " ", (text or "").lower().replace("&", " and "))
    return " ".join(text.split())


# ---------------------------------------------------------------- TMDB

class TMDB:
    service = "TMDB"

    def __init__(self, key):
        self.key = (key or "").strip()
        if not self.key:
            raise MetadataError("add your TMDB token in Settings first")

    def _get(self, path, **params):
        headers = {}
        if self.key.startswith("eyJ") and "." in self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        else:
            params["api_key"] = self.key
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        return _get_json(f"{TMDB_API}{path}?{query}", headers, self.service)

    @staticmethod
    def image(path, size="w500"):
        return f"{TMDB_IMG}{size}{path}" if path else ""

    def search(self, query, media):
        data = self._get(f"/search/{media}", query=query, include_adult="false")
        out = []
        for r in data.get("results", [])[:20]:
            title = r.get("title") or r.get("name") or ""
            out.append({
                "source": "tmdb", "source_id": f"{media}/{r['id']}",
                "title": title,
                "original_title": r.get("original_title") or r.get("original_name") or "",
                "year": _year(r.get("release_date") or r.get("first_air_date")),
                "overview": r.get("overview") or "",
                "poster": self.image(r.get("poster_path"), "w342"),
                "rating": round(r["vote_average"], 1) if r.get("vote_count") else None,
                "format": "movie" if media == "movie" else "series",
            })
        return out

    @staticmethod
    def pick_trailer(videos):
        vids = [v for v in (videos or {}).get("results", []) if v.get("site") == "YouTube"]
        for want in ("Trailer", "Teaser"):
            matches = [v for v in vids if v.get("type") == want]
            matches.sort(key=lambda v: (not v.get("official"), v.get("iso_639_1") != "en"))
            if matches:
                return matches[0]["key"]
        return ""

    def details(self, source_id):
        media, _, tid = source_id.partition("/")
        if media not in ("movie", "tv") or not tid.isdigit():
            raise MetadataError("unknown TMDB title")
        d = self._get(f"/{media}/{tid}", append_to_response="credits,videos,external_ids",
                      include_video_language="en,null")
        title = d.get("title") or d.get("name") or ""
        original = d.get("original_title") or d.get("original_name") or ""
        imdb = d.get("imdb_id") or (d.get("external_ids") or {}).get("imdb_id")
        links = {"tmdb": f"https://www.themoviedb.org/{media}/{tid}"}
        if imdb:
            links["imdb"] = f"https://www.imdb.com/title/{imdb}/"
        item = {
            "source": "tmdb", "source_id": source_id, "tmdb_ref": source_id,
            "title": title, "original_title": original if original != title else "",
            "aliases": _unique([title, original]),
            "year": _year(d.get("release_date") or d.get("first_air_date")),
            "overview": d.get("overview") or "", "tagline": d.get("tagline") or "",
            "poster": self.image(d.get("poster_path")),
            "backdrop": self.image(d.get("backdrop_path"), "w1280"),
            "genres": [g["name"] for g in d.get("genres", [])],
            "rating": round(d["vote_average"], 1) if d.get("vote_count") else None,
            "rating_source": "TMDB", "votes": d.get("vote_count"),
            "popularity": d.get("popularity") or 0,
            "cast": [c["name"] for c in (d.get("credits") or {}).get("cast", [])[:10]],
            "trailer": self.pick_trailer(d.get("videos")),
            "links": links,
        }
        if media == "movie":
            collection = d.get("belongs_to_collection") or {}
            item.update(format="movie", media_type="movie", runtime=d.get("runtime") or None,
                        revenue=d.get("revenue") or None,
                        studios=[c["name"] for c in d.get("production_companies", [])[:3]],
                        airing_status=d.get("status") or "",
                        tmdb_collection=collection.get("id"))
        else:
            # Season 0 is "Specials" on TMDB; it's kept when it has episodes.
            seasons = [{"number": s["season_number"], "episodes": s.get("episode_count") or 0}
                       for s in d.get("seasons", [])
                       if (s.get("season_number") or 0) > 0 or s.get("episode_count")]
            item["media_type"] = "tv"
            runtimes = d.get("episode_run_time") or []
            item.update(format="series", seasons=seasons,
                        episodes_total=d.get("number_of_episodes"),
                        runtime=runtimes[0] if runtimes else None,
                        studios=[n["name"] for n in d.get("networks", [])[:3]],
                        airing_status=d.get("status") or "")
        return item

    def collection(self, collection_id):
        """The other films in a movie series (TMDB "collection")."""
        d = self._get(f"/collection/{int(collection_id)}")
        return [{
            "source": "tmdb", "source_id": f"movie/{p['id']}", "title": p.get("title") or "",
            "original_title": p.get("original_title") or "", "year": _year(p.get("release_date")),
            "overview": p.get("overview") or "", "poster": self.image(p.get("poster_path"), "w342"),
            "format": "movie", "media_type": "MOVIE", "media_kind": "movie", "type": "movie",
            "relation": "Same film series",
        } for p in sorted(d.get("parts", []), key=lambda p: p.get("release_date") or "9999")]

    def search_titles(self, query, media):
        """Search results with the extra fields the automatic sorter needs."""
        rows = self.search(query, media)
        for r in rows:
            r["media_kind"] = "movie" if media == "movie" else "tv"
            r["type"] = "movie" if media == "movie" else "tv"
        return rows

    def season(self, tv_id, number):
        key = (tv_id, number)
        cached = _season_cache.get(key)
        if cached is not None:
            return cached
        d = self._get(f"/tv/{tv_id}/season/{number}")
        episodes = [{
            "number": e.get("episode_number"),
            "name": e.get("name") or "",
            "overview": e.get("overview") or "",
            "air_date": e.get("air_date") or "",
            "runtime": e.get("runtime"),
            "still": self.image(e.get("still_path"), "w300"),
        } for e in d.get("episodes", []) if e.get("episode_number")]
        _season_cache.put(key, episodes)
        return episodes

    def providers(self, ref, region):
        """Streaming services for a title in one country."""
        key = (ref, region)
        cached = _provider_cache.get(key)
        if cached is not None:
            return cached
        media, _, tid = ref.partition("/")
        d = self._get(f"/{media}/{tid}/watch/providers")
        country = (d.get("results") or {}).get(region) or {}
        result = {"link": country.get("link", ""), "services": []}
        seen = set()
        for kind in ("flatrate", "free", "ads", "rent", "buy"):
            for p in country.get(kind, []):
                if p.get("provider_id") in seen:
                    continue
                seen.add(p.get("provider_id"))
                result["services"].append({"id": p.get("provider_id"),
                                           "name": p.get("provider_name", ""),
                                           "kind": kind,
                                           "logo": self.image(p.get("logo_path"), "w92")})
        _provider_cache.put(key, result)
        return result

    def find_anime(self, titles, year, fmt):
        """Best-effort match of a MyAnimeList entry to its TMDB entry."""
        media = "movie" if fmt == "movie" else "tv"
        tried = set()
        candidates = []
        for t in titles:
            for variant in (t, _strip_season(t)):
                if variant and variant.lower() not in tried:
                    tried.add(variant.lower())
                    candidates.append(variant)
        for strict in (True, False):
            for query in candidates[:4]:
                try:
                    data = self._get(f"/search/{media}", query=query, include_adult="false")
                except MetadataError:
                    continue
                for r in data.get("results", [])[:8]:
                    if TMDB_ANIMATION not in r.get("genre_ids", []):
                        continue
                    if r.get("original_language") not in ("ja", "zh", "ko"):
                        continue
                    ryear = _year(r.get("release_date") or r.get("first_air_date"))
                    if strict and year and ryear and abs(ryear - year) > 1:
                        continue
                    return f"{media}/{r['id']}"
        return ""


def _strip_season(title):
    t = re.sub(r"\b(season\s*\d+|\d+(st|nd|rd|th)\s+season|part\s*\d+|cour\s*\d+|"
               r"final season|the final season)\b.*$", "", title or "", flags=re.I)
    return t.strip(" :-")


def service_of(provider):
    """'netflix', 'hbomax' or None for a provider from TMDB.providers()."""
    name = provider.get("name", "").lower()
    if provider.get("id") in NETFLIX_IDS or name.startswith("netflix"):
        return "netflix"
    if provider.get("id") in HBOMAX_IDS or name in ("max", "hbo max", "max amazon channel",
                                                    "hbo max amazon channel"):
        return "hbomax"
    return None


# ---------------------------------------------------------- MyAnimeList

MAL_SEARCH_FIELDS = ("id,title,main_picture,alternative_titles,start_date,synopsis,mean,"
                     "media_type,num_episodes,num_list_users")
MAL_DETAIL_FIELDS = ("id,title,main_picture,alternative_titles,start_date,end_date,synopsis,"
                     "mean,rank,popularity,num_list_users,num_scoring_users,media_type,status,"
                     "genres,num_episodes,average_episode_duration,studios,rating")

MAL_STATUS = {"finished_airing": "Finished airing", "currently_airing": "Currently airing",
              "not_yet_aired": "Not yet aired"}


class MAL:
    service = "MyAnimeList"

    def __init__(self, client_id):
        self.client_id = (client_id or "").strip()
        if not self.client_id:
            raise MetadataError("add your MyAnimeList Client ID in Settings first")

    def _get(self, path, **params):
        query = urllib.parse.urlencode(params)
        return _get_json(f"{MAL_API}{path}?{query}", {"X-MAL-CLIENT-ID": self.client_id},
                         self.service)

    @staticmethod
    def _titles(a):
        alt = a.get("alternative_titles") or {}
        english = alt.get("en") or ""
        romaji = a.get("title") or ""
        return (english or romaji), romaji, _unique([english, romaji, alt.get("ja")]
                                                  + list(alt.get("synonyms") or []))

    @staticmethod
    def _picture(a):
        pic = a.get("main_picture") or {}
        return pic.get("large") or pic.get("medium") or ""

    def _summary(self, a):
        title, romaji, _ = self._titles(a)
        return {
            "source": "mal", "source_id": str(a["id"]), "title": title,
            "original_title": romaji if romaji != title else "",
            "year": _year(a.get("start_date")),
            "overview": _clean_text(a.get("synopsis")),
            "poster": self._picture(a), "rating": a.get("mean"),
            "format": "movie" if a.get("media_type") == "movie" else "series",
            "media_type": (a.get("media_type") or "").upper().replace("_", " "),
            "media_kind": a.get("media_type") or "",
            "type": "anime",
            "episodes_total": a.get("num_episodes") or None,
            "aliases": self._titles(a)[2],
        }

    def related(self, mal_id):
        """Sequels, prequels, side stories, spin-offs, OVAs, specials, films...
        directly related to an anime on MyAnimeList."""
        cached = _related_cache.get(("mal", str(mal_id)))
        if cached is not None:
            return cached
        rows = self._get(f"/anime/{int(mal_id)}", fields="related_anime").get("related_anime") or []
        out = []
        for row in rows[:30]:
            node = row.get("node") or {}
            if not node.get("id"):
                continue
            try:
                entry = self._summary(self._get(f"/anime/{node['id']}", fields=MAL_SEARCH_FIELDS))
            except MetadataError:
                entry = {"source": "mal", "source_id": str(node["id"]), "title": node.get("title", ""),
                         "original_title": "", "year": None, "overview": "", "rating": None,
                         "poster": self._picture(node), "format": "series", "media_type": "",
                         "media_kind": "", "type": "anime", "episodes_total": None, "aliases": []}
            entry["relation"] = row.get("relation_type_formatted") or row.get("relation_type", "")
            out.append(entry)
        _related_cache.put(("mal", str(mal_id)), out)
        return out

    def user_list(self, user):
        """A public MyAnimeList list: [{"mal_id", "title", "status", "watched"}]."""
        user = (user or "").strip()
        if not re.fullmatch(r"[\w-]{2,32}", user):
            raise MetadataError("that doesn't look like a MyAnimeList user name")
        url = (f"{MAL_API}/users/{urllib.parse.quote(user)}/animelist?"
               + urllib.parse.urlencode({"fields": "list_status", "limit": 1000, "nsfw": "true"}))
        out = []
        for _ in range(20):
            data = _get_json(url, {"X-MAL-CLIENT-ID": self.client_id}, self.service)
            for row in data.get("data", []):
                node, st = row.get("node") or {}, row.get("list_status") or {}
                if node.get("id"):
                    out.append({"mal_id": str(node["id"]), "title": node.get("title", ""),
                                "status": st.get("status", ""), "watched": st.get("num_episodes_watched") or 0})
            url = (data.get("paging") or {}).get("next")
            if not url or not url.startswith(MAL_API):
                break
        return out

    def search(self, query):
        query = query.strip()
        # A pasted MyAnimeList link always finds exactly that show.
        link = re.search(r"myanimelist\.net/anime/(\d+)", query)
        if link:
            return [self._summary(self._get(f"/anime/{link.group(1)}", fields=MAL_SEARCH_FIELDS))]
        if len(query) < 3:
            raise MetadataError("type at least 3 letters to search MyAnimeList")
        # Without nsfw=true, MAL's API silently drops every show it marks as
        # NSFW (ecchi, hentai and more) even though the website lists them.
        data = self._get("/anime", q=query[:64], limit=50, nsfw="true", fields=MAL_SEARCH_FIELDS)
        return [self._summary(row["node"]) for row in data.get("data", [])]

    def details(self, mal_id):
        if not str(mal_id).isdigit():
            raise MetadataError("unknown MyAnimeList title")
        a = self._get(f"/anime/{mal_id}", fields=MAL_DETAIL_FIELDS)
        title, romaji, aliases = self._titles(a)
        fmt = "movie" if a.get("media_type") == "movie" else "series"
        episodes = a.get("num_episodes") or 0
        duration = a.get("average_episode_duration")
        return {
            "source": "mal", "source_id": str(a["id"]), "format": fmt,
            "media_type": a.get("media_type") or "",
            "title": title, "original_title": romaji if romaji != title else "",
            "aliases": aliases, "year": _year(a.get("start_date")),
            "overview": _clean_text(a.get("synopsis")),
            "poster": self._picture(a),
            "genres": [g["name"] for g in a.get("genres", [])],
            "rating": a.get("mean"), "rating_source": "MyAnimeList",
            "votes": a.get("num_scoring_users"),
            "mal_members": a.get("num_list_users"), "mal_rank": a.get("rank"),
            "popularity": a.get("num_list_users") or 0,
            "runtime": round(duration / 60) if duration else None,
            "studios": [s["name"] for s in a.get("studios", [])],
            "seasons": [] if fmt == "movie" else [{"number": 1, "episodes": episodes}],
            "episodes_total": episodes or None,
            "airing_status": MAL_STATUS.get(a.get("status"), ""),
            "links": {"mal": f"https://myanimelist.net/anime/{a['id']}"},
        }


def jikan_trailer(mal_id):
    """MyAnimeList's trailer for an anime, via the free Jikan API."""
    try:
        data = _get_json(f"{JIKAN_API}/anime/{int(mal_id)}", service="Jikan").get("data") or {}
    except (MetadataError, ValueError):
        return ""
    trailer = data.get("trailer") or {}
    if trailer.get("youtube_id"):
        return trailer["youtube_id"]
    m = re.search(r"/embed/([\w-]{11})", trailer.get("embed_url") or "")
    return m.group(1) if m else ""


# ------------------------------------------------------------- facade

def search(settings, item_type, query):
    query = (query or "").strip()
    if not query:
        return []
    if item_type == "anime":
        return MAL(settings["mal_client_id"]).search(query)
    media = "movie" if item_type == "movie" else "tv"
    return TMDB(settings["tmdb_api_key"]).search(query, media)


def details(settings, item_type, source, source_id):
    """Full info for a search result, ready to store."""
    if source == "mal":
        item = MAL(settings["mal_client_id"]).details(source_id)
        _enrich_anime(settings, item)
        return item
    if source == "tmdb":
        return TMDB(settings["tmdb_api_key"]).details(source_id)
    raise MetadataError("unknown source")


def related(settings, item):
    """Titles related to one in your list: for anime, MyAnimeList's relations; for
    films, the rest of their series on TMDB. (TV spin-offs come from Gemini.)"""
    if item.get("source") == "mal" and settings.get("mal_client_id"):
        return MAL(settings["mal_client_id"]).related(item["source_id"])
    if item.get("format") == "movie" and item.get("tmdb_collection") and settings.get("tmdb_api_key"):
        own = item.get("source_id")
        return [r for r in TMDB(settings["tmdb_api_key"]).collection(item["tmdb_collection"])
                if r["source_id"] != own]
    return []


def _enrich_anime(settings, item):
    """Fill in what MAL's API lacks: a trailer, a wide backdrop and a TMDB match
    (TMDB is needed for the Netflix / HBO Max check)."""
    item["trailer"] = jikan_trailer(item["source_id"])
    if not settings.get("tmdb_api_key"):
        return
    try:
        tmdb = TMDB(settings["tmdb_api_key"])
        ref = tmdb.find_anime(item["aliases"], item["year"], item["format"])
        if not ref:
            return
        item["tmdb_ref"] = ref
        extra = tmdb.details(ref)
        item["backdrop"] = extra.get("backdrop", "")
        item["cast"] = extra.get("cast", [])
        if not item["trailer"]:
            item["trailer"] = extra.get("trailer", "")
        item["links"]["tmdb"] = extra["links"]["tmdb"]
    except MetadataError:
        pass


def test_keys(settings):
    """Check both keys; returns {"tmdb": msg-or-None, "mal": msg-or-None}."""
    out = {}
    try:
        TMDB(settings["tmdb_api_key"])._get("/configuration")
        out["tmdb"] = None
    except MetadataError as e:
        out["tmdb"] = str(e)
    try:
        MAL(settings["mal_client_id"])._get("/anime", q="naruto", limit=1)
        out["mal"] = None
    except MetadataError as e:
        out["mal"] = str(e)
    return out
