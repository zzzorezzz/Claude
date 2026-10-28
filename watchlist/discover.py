"""Find Media: titles from the internet, beyond your list.

- Top titles of a year, most popular or highest rated: TMDB for TV shows and
  films, MyAnimeList's seasonal lists for anime.
- Search and browse by keywords, genre and type, sorted several ways: TMDB for
  TV shows and films, Jikan (MyAnimeList's public data, no key needed) for anime.
"""

import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from . import metadata
from .metadata import JIKAN_API, MAL, MAL_SEARCH_FIELDS, TMDB, MetadataError

TMDB_ANIMATION = 16
_genre_cache = metadata._Cache(24 * 3600)
_list_cache = metadata._Cache(3600)

SORTS = {
    "movie": [("popular", "Most popular"), ("rated", "Highest rated"), ("grossing", "Box office"),
              ("newest", "Newest"), ("oldest", "Oldest")],
    "tv": [("popular", "Most popular"), ("rated", "Highest rated"), ("newest", "Newest"), ("oldest", "Oldest")],
    "anime": [("popular", "Most popular"), ("rated", "Highest rated"), ("newest", "Newest"), ("oldest", "Oldest")],
}
ANIME_KINDS = [("", "Any type"), ("tv", "TV"), ("movie", "Film"), ("ova", "OVA"), ("ona", "ONA"),
               ("special", "Special")]


def _tmdb(settings):
    return TMDB(settings["tmdb_api_key"])


def _is_anime(r):
    return TMDB_ANIMATION in (r.get("genre_ids") or []) and r.get("language") == "ja"


# ------------------------------------------------------------- top of a year

def top_by_year(settings, kind, year, order="popular", count=100):
    """The `count` most popular (or highest rated) titles of a year."""
    key = ("top", kind, year, order)
    cached = _list_cache.get(key)
    if cached is not None:
        return cached
    if kind == "anime":
        out = _anime_year(settings, year, order, count)
    else:
        out = _tmdb_year(settings, kind, year, order, count)
    _list_cache.put(key, out)
    return out


def _tmdb_year(settings, kind, year, order, count):
    tmdb = _tmdb(settings)
    media = "movie" if kind == "movie" else "tv"
    params = {"sort_by": "popularity.desc" if order == "popular" else "vote_average.desc",
              "include_adult": "false"}
    params["primary_release_year" if media == "movie" else "first_air_date_year"] = int(year)
    if order == "rated":
        params["vote_count.gte"] = 200 if media == "movie" else 80
    out, page = [], 1
    while len(out) < count and page <= 10:
        data = tmdb._get(f"/discover/{media}", page=page, **params)
        rows = [tmdb.result(r, media) for r in data.get("results", [])]
        # Japanese animation is in the Anime section, not TV shows.
        out += [r for r in rows if not (media == "tv" and _is_anime(r))]
        if page >= (data.get("total_pages") or 1):
            break
        page += 1
    return out[:count]


def _anime_year(settings, year, order, count):
    mal = MAL(settings["mal_client_id"])
    seen, rows = set(), []
    for season in ("winter", "spring", "summer", "fall"):
        try:
            data = mal._get(f"/anime/season/{int(year)}/{season}", limit=500, nsfw="true",
                            fields=MAL_SEARCH_FIELDS)
        except MetadataError as e:
            if "doesn't have" in str(e):   # a season that hasn't been listed yet
                continue
            raise
        for row in data.get("data", []):
            node = row.get("node") or {}
            if node.get("id") in seen or node.get("media_type") in ("music", "pv", "cm"):
                continue
            seen.add(node.get("id"))
            summary = mal._summary(node)
            summary["members"] = node.get("num_list_users") or 0
            if summary["year"] in (None, int(year)):
                rows.append(summary)
    if order == "rated":
        rows = [r for r in rows if r.get("rating") and r["members"] >= 2000] or rows
        rows.sort(key=lambda r: -(r.get("rating") or 0))
    else:
        rows.sort(key=lambda r: -r["members"])
    return rows[:count]


# ------------------------------------------------------------- search and browse

def genres(settings, kind):
    """[{"id", "name"}] to browse by."""
    cached = _genre_cache.get(kind)
    if cached is not None:
        return cached
    if kind == "anime":
        data = metadata._get_json(f"{JIKAN_API}/genres/anime", service="Jikan")
        names = {}
        for g in data.get("data", []):
            names.setdefault(g.get("name"), g.get("mal_id"))
        out = [{"id": i, "name": n} for n, i in sorted(names.items()) if n and i]
    else:
        data = _tmdb(settings)._get(f"/genre/{'movie' if kind == 'movie' else 'tv'}/list")
        out = [{"id": g["id"], "name": g["name"]} for g in data.get("genres", [])]
    _genre_cache.put(kind, out)
    return out


def browse(settings, kind, query="", genre="", sort="popular", sub_kind="", page=1):
    """One page of titles: {"results": [...], "more": bool}."""
    if kind == "anime":
        return _browse_anime(query, genre, sort, sub_kind, page)
    tmdb = _tmdb(settings)
    media = "movie" if kind == "movie" else "tv"
    if query.strip():
        data = tmdb._get(f"/search/{media}", query=query.strip(), include_adult="false", page=page)
        rows = [tmdb.result(r, media) for r in data.get("results", [])]
        if genre:
            rows = [r for r in rows if int(genre) in r["genre_ids"]]
        rows = _sort(rows, sort)
    else:
        sort_by = {"popular": "popularity.desc", "rated": "vote_average.desc", "grossing": "revenue.desc",
                   "newest": "primary_release_date.desc" if media == "movie" else "first_air_date.desc",
                   "oldest": "primary_release_date.asc" if media == "movie" else "first_air_date.asc"}[sort]
        params = {"sort_by": sort_by, "include_adult": "false", "page": page}
        if genre:
            params["with_genres"] = int(genre)
        if sort == "rated":
            params["vote_count.gte"] = 300 if media == "movie" else 100
        if sort in ("newest", "oldest"):
            params["vote_count.gte"] = 10
            today = time.strftime("%Y-%m-%d")
            params["primary_release_date.lte" if media == "movie" else "first_air_date.lte"] = today
        data = tmdb._get(f"/discover/{media}", **params)
        rows = [tmdb.result(r, media) for r in data.get("results", [])]
    if media == "tv" and not genre == str(TMDB_ANIMATION):
        rows = [r for r in rows if not _is_anime(r)]
    return {"results": rows, "more": page < min(data.get("total_pages") or 1, 500)}


def _sort(rows, sort):
    if sort == "rated":
        return sorted(rows, key=lambda r: -(r.get("rating") or 0) if r.get("votes", 0) >= 20 else 0)
    if sort == "newest":
        return sorted(rows, key=lambda r: -(r.get("year") or 0))
    if sort == "oldest":
        return sorted(rows, key=lambda r: r.get("year") or 9999)
    return sorted(rows, key=lambda r: -(r.get("popularity") or 0))


def _browse_anime(query, genre, sort, sub_kind, page):
    params = {"page": page, "limit": 25}
    params["order_by"], params["sort"] = {"popular": ("members", "desc"), "rated": ("score", "desc"),
                                          "newest": ("start_date", "desc"), "oldest": ("start_date", "asc"),
                                          "grossing": ("members", "desc")}[sort]
    if query.strip():
        params["q"] = query.strip()
    if genre:
        params["genres"] = int(genre)
    if sub_kind:
        params["type"] = sub_kind
    if sort == "rated":
        params["min_score"] = 1
    data = metadata._get_json(f"{JIKAN_API}/anime?{urllib.parse.urlencode(params)}", service="Jikan")
    return {"results": [_jikan(a) for a in data.get("data", [])],
            "more": bool((data.get("pagination") or {}).get("has_next_page"))}


def _jikan(a):
    images = (a.get("images") or {}).get("jpg") or {}
    kind = (a.get("type") or "").lower().replace(" ", "_")
    english = a.get("title_english") or ""
    year = a.get("year") or metadata._year(((a.get("aired") or {}).get("from") or "")[:10])
    return {"source": "mal", "source_id": str(a.get("mal_id")), "title": english or a.get("title") or "",
            "original_title": a.get("title") if english and english != a.get("title") else "",
            "year": year, "overview": metadata._clean_text(a.get("synopsis")),
            "poster": images.get("large_image_url") or images.get("image_url") or "",
            "rating": a.get("score"), "format": "movie" if kind == "movie" else "series",
            "media_type": (a.get("type") or "").upper(), "media_kind": kind, "type": "anime",
            "members": a.get("members") or 0, "episodes_total": a.get("episodes")}


# ------------------------------------------------------------- AI picks, checked

def verify(settings, picks):
    """Titles the AI named, found on MyAnimeList (anime) or TMDB; the rest are dropped."""
    def one(p):
        kind = p.get("type")
        want = metadata.norm_title(p.get("title") or "")
        if not want:
            return None
        try:
            if kind == "anime" and settings.get("mal_client_id"):
                rows = metadata.MAL(settings["mal_client_id"]).search(p["title"]) if len(p["title"]) >= 3 else []
            elif kind in ("tv", "movie") and settings.get("tmdb_api_key"):
                rows = _tmdb(settings).search(p["title"], "movie" if kind == "movie" else "tv")
            else:
                return None
        except MetadataError:
            return None
        year = p.get("year") if isinstance(p.get("year"), int) else None
        near = lambda r: not year or not r.get("year") or abs(r["year"] - year) <= 1
        names = lambda r: {metadata.norm_title(n) for n in (r.get("title"), r.get("original_title"),
                                                             *(r.get("aliases") or [])) if n}
        # The same name; or a short name for one of the first few results
        # ("Frieren" for "Frieren: Beyond Journey's End") from the same year.
        found = next((r for r in rows[:10] if want in names(r) and near(r)), None)
        found = found or next((r for r in rows[:3] if near(r) and (year or len(want) >= 6)
                               and any(n.startswith(want + " ") for n in names(r))), None)
        return dict(found, type=kind, why=p.get("why", "")) if found else None

    with ThreadPoolExecutor(4) as pool:
        return [r for r in pool.map(one, picks) if r]
