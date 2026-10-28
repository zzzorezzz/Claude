"""Find Media: titles from the internet, beyond your list.

- Top titles of a year, most popular or highest rated: TMDB for TV shows and
  films, MyAnimeList's seasonal lists for anime.
- Search and browse: any mix of TV shows, films and anime, by keywords, any of
  their genres, year and format, sorted by popularity, rating, date or box
  office, 50 at a time. TMDB for TV shows and films. Anime come only from
  MyAnimeList's official API, with your Client ID. It has no genre search, so
  the app keeps its own index of every anime (from MyAnimeList's ranking by
  popularity, with each one's genres) and filters and sorts that, like
  MyAnimeList's own genre pages.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import metadata
from .config import read_json, write_json
from .metadata import MAL, MAL_SEARCH_FIELDS, TMDB, MetadataError

TMDB_ANIMATION = 16
_list_cache = metadata._Cache(3600)


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

PAGE = 50
KINDS = ("tv", "anime", "movie")

MOVIE_GENRES = [(28, "Action"), (12, "Adventure"), (16, "Animation"), (35, "Comedy"), (80, "Crime"),
                (99, "Documentary"), (18, "Drama"), (10751, "Family"), (14, "Fantasy"), (36, "History"),
                (27, "Horror"), (10402, "Music"), (9648, "Mystery"), (10749, "Romance"),
                (878, "Science Fiction"), (10770, "TV Movie"), (53, "Thriller"), (10752, "War"), (37, "Western")]
TV_GENRES = [(10759, "Action & Adventure"), (16, "Animation"), (35, "Comedy"), (80, "Crime"),
             (99, "Documentary"), (18, "Drama"), (10751, "Family"), (10762, "Kids"), (9648, "Mystery"),
             (10763, "News"), (10764, "Reality"), (10765, "Sci-Fi & Fantasy"), (10766, "Soap"),
             (10767, "Talk"), (10768, "War & Politics"), (37, "Western")]

# MyAnimeList's genres, shown until the anime index has its own (MyAnimeList's
# API has no list of genres; the index collects them from the titles).
MAL_GENRES = [
    (1, "Action"), (2, "Adventure"), (5, "Avant Garde"), (46, "Award Winning"), (28, "Boys Love"),
    (4, "Comedy"), (8, "Drama"), (10, "Fantasy"), (26, "Girls Love"), (47, "Gourmet"), (14, "Horror"),
    (7, "Mystery"), (22, "Romance"), (24, "Sci-Fi"), (36, "Slice of Life"), (30, "Sports"),
    (37, "Supernatural"), (41, "Suspense"), (9, "Ecchi"), (49, "Erotica"), (12, "Hentai"),
    (50, "Adult Cast"), (51, "Anthropomorphic"), (52, "CGDCT"), (53, "Childcare"), (54, "Combat Sports"),
    (81, "Crossdressing"), (39, "Detective"), (56, "Educational"), (57, "Gag Humor"), (58, "Gore"),
    (35, "Harem"), (59, "High Stakes Game"), (13, "Historical"), (60, "Idols (Female)"),
    (61, "Idols (Male)"), (62, "Isekai"), (63, "Iyashikei"), (64, "Love Polygon"),
    (65, "Magical Sex Shift"), (66, "Mahou Shoujo"), (17, "Martial Arts"), (18, "Mecha"),
    (67, "Medical"), (38, "Military"), (19, "Music"), (6, "Mythology"), (68, "Organized Crime"),
    (69, "Otaku Culture"), (20, "Parody"), (70, "Performing Arts"), (71, "Pets"), (40, "Psychological"),
    (3, "Racing"), (72, "Reincarnation"), (73, "Reverse Harem"), (74, "Romantic Subtext"),
    (21, "Samurai"), (23, "School"), (75, "Showbiz"), (29, "Space"), (11, "Strategy Game"),
    (31, "Super Power"), (76, "Survival"), (77, "Team Sports"), (78, "Time Travel"), (32, "Vampire"),
    (79, "Video Game"), (80, "Visual Arts"), (48, "Workplace"),
    (43, "Josei"), (15, "Kids"), (42, "Seinen"), (25, "Shoujo"), (27, "Shounen")]
MAL_MAIN = {"Action", "Adventure", "Avant Garde", "Award Winning", "Boys Love", "Comedy", "Drama", "Fantasy",
            "Girls Love", "Gourmet", "Horror", "Mystery", "Romance", "Sci-Fi", "Slice of Life", "Sports",
            "Supernatural", "Suspense"}
MAL_AUDIENCE = {"Josei", "Kids", "Seinen", "Shoujo", "Shounen"}
MAL_ADULT = {"Ecchi", "Erotica", "Hentai"}

# Genres shared between the three, for searching more than one type at once:
# (key, name, movie genre, TV genre - or ("tag", word) for a TMDB keyword tag,
# since TMDB has no such TV genre - and the MyAnimeList genre, or ANY when
# every anime counts).
ANY = "any"
SHARED = [
    ("action", "Action", 28, 10759, "Action"),
    ("adventure", "Adventure", 12, 10759, "Adventure"),
    ("animation", "Animation", 16, 16, ANY),
    ("comedy", "Comedy", 35, 35, "Comedy"),
    ("crime", "Crime", 80, 80, "Detective"),
    ("documentary", "Documentary", 99, 99, None),
    ("drama", "Drama", 18, 18, "Drama"),
    ("family", "Family / Kids", 10751, 10751, "Kids"),
    ("fantasy", "Fantasy", 14, 10765, "Fantasy"),
    ("history", "History", 36, None, "Historical"),
    ("horror", "Horror", 27, ("tag", "horror"), "Horror"),
    ("music", "Music", 10402, None, "Music"),
    ("mystery", "Mystery", 9648, 9648, "Mystery"),
    ("romance", "Romance", 10749, ("tag", "romance"), "Romance"),
    ("scifi", "Sci-Fi", 878, 10765, "Sci-Fi"),
    ("thriller", "Thriller", 53, ("tag", "thriller"), "Suspense"),
    ("war", "War", 10752, 10768, "Military"),
    ("western", "Western", 37, 37, None),
]
SORT_NAMES = [("popular", "Most popular"), ("rated", "Highest rated"), ("newest", "Newest"),
              ("oldest", "Oldest"), ("title", "Title A-Z"), ("grossing", "Box office (films)")]
ANIME_KINDS = [("", "Any format"), ("tv", "TV"), ("movie", "Film"), ("ova", "OVA"), ("ona", "ONA"),
               ("special", "Special"), ("tv_special", "TV special"), ("music", "Music video")]
MIN_VOTES = 10          # "Highest rated" leaves out titles hardly anyone has rated

_search_cache = metadata._Cache(1800)
_keyword_cache = metadata._Cache(7 * 24 * 3600)


class AnimeIndex:
    """Every anime on MyAnimeList, with its genres, from the official API's
    ranking by popularity (500 at a time). Kept in the data folder; the first
    build continues where it stopped, and it's rebuilt in the background once
    a week. Searches use what's there while it's being built."""

    FIELDS = ("id,title,alternative_titles,start_date,mean,media_type,num_episodes,num_list_users,"
              "genres,main_picture")
    MAX_PAGES = 80                 # 40,000 titles; MyAnimeList has about 30,000
    STALE = 7 * 24 * 3600

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        self._thread = None
        self.error = ""
        data = read_json(path, {})
        if not (isinstance(data, dict) and data.get("v") == 1):
            data = {"v": 1, "rows": [], "genres": {}, "next": 0, "complete": False, "built": 0}
        self._data = data

    def state(self):
        with self._lock:
            return {"count": len(self._data["rows"]), "complete": self._data["complete"],
                    "building": bool(self._thread and self._thread.is_alive()), "error": self.error}

    def rows(self):
        with self._lock:
            return self._data["rows"]

    def genre_names(self):
        with self._lock:
            return {int(k): v for k, v in self._data["genres"].items()}

    def ensure(self, settings):
        """Start (or continue, or refresh) the index if it needs it."""
        client_id = settings.get("mal_client_id")
        if not client_id:
            return
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            due = not self._data["complete"] or time.time() - self._data["built"] > self.STALE
            if not due:
                return
            self._thread = threading.Thread(target=self._build, args=(client_id,), daemon=True)
            self._thread.start()

    def _build(self, client_id):
        mal = MAL(client_id)
        with self._lock:
            fresh = self._data["complete"]          # a weekly refresh: the old index stays until it's done
            rows = [] if fresh else list(self._data["rows"])
            genres = dict(self._data["genres"])
            offset = 0 if fresh else self._data["next"]
        self.error = ""
        done = False
        try:
            for n in range(self.MAX_PAGES):
                data = mal._get("/anime/ranking", ranking_type="bypopularity", limit=500, offset=offset,
                                nsfw="true", fields=self.FIELDS)
                page = [row.get("node") or {} for row in data.get("data", [])]
                for node in page:
                    for g in node.get("genres") or []:
                        genres[str(g["id"])] = g["name"]
                rows += [_compact(n) for n in page if n.get("id")]
                offset += 500
                done = not page or not (data.get("paging") or {}).get("next")
                if not fresh or done:
                    with self._lock:
                        self._data = {"v": 1, "rows": rows if fresh else list(rows), "genres": dict(genres),
                                      "next": offset, "complete": done,
                                      "built": time.time() if done else self._data["built"]}
                        if done or n % 4 == 3:             # saved now and then, to carry on after a restart
                            write_json(self.path, self._data, compact=True)
                if done:
                    break
                time.sleep(0.3)
        except MetadataError as e:
            self.error = str(e)
            with self._lock:
                if not fresh:
                    write_json(self.path, self._data, compact=True)


def _compact(node):
    alt = node.get("alternative_titles") or {}
    return {"id": node["id"], "t": node.get("title") or "", "en": alt.get("en") or "",
            "syn": list(alt.get("synonyms") or [])[:3], "d": node.get("start_date") or "",
            "k": node.get("media_type") or "", "s": node.get("mean"), "m": node.get("num_list_users") or 0,
            "e": node.get("num_episodes") or None, "g": [g["id"] for g in node.get("genres") or []],
            "p": (node.get("main_picture") or {}).get("medium") or ""}


def _from_index(c):
    kind = c["k"]
    title = c["en"] or c["t"]
    return {"source": "mal", "source_id": str(c["id"]), "title": title,
            "original_title": c["t"] if c["en"] and c["en"] != c["t"] else "",
            "aliases": [n for n in (c["en"], c["t"], *c["syn"]) if n],
            "year": metadata._year(c["d"]), "date": c["d"], "overview": "", "poster": c["p"],
            "rating": c["s"], "votes": MIN_VOTES if c["s"] else 0,
            "format": "movie" if kind == "movie" else "series",
            "media_type": kind.upper().replace("_", " "), "media_kind": kind, "type": "anime",
            "members": c["m"], "episodes_total": c["e"], "genre_ids": c["g"]}


def _anime_genres(index):
    """[(id, name)]: MyAnimeList's genres, themes and audiences."""
    by_name = {n: i for i, n in MAL_GENRES}
    if index is not None:
        by_name.update({n: i for i, n in index.genre_names().items()})
    return sorted(((i, n) for n, i in by_name.items()), key=lambda x: x[1])


def _anime_group(name):
    return ("Genres" if name in MAL_MAIN else "Audience" if name in MAL_AUDIENCE
            else "Adult" if name in MAL_ADULT else "Themes")


def genres(index=None):
    """Every genre to filter by: for each type on its own ("movie", "tv",
    "anime") and for searching several types at once ("all")."""
    anime = _anime_genres(index)
    anime_id = {n: i for i, n in anime}
    out = {
        "movie": [{"key": f"movie:{i}", "name": n, "group": "Movie genres"} for i, n in MOVIE_GENRES],
        "tv": [{"key": f"tv:{i}", "name": n, "group": "TV genres"} for i, n in TV_GENRES],
        "anime": [{"key": f"anime:{i}", "name": n, "group": _anime_group(n)}
                  for i, n in sorted(anime, key=lambda x: x[1])],
    }
    shared, used = [], {"movie": set(), "tv": set(), "anime": set()}
    for key, name, movie, tv, an in SHARED:
        types = [t for t, v in (("movie", movie), ("tv", tv), ("anime", an)) if v]
        shared.append({"key": f"all:{key}", "name": name, "group": "All types", "types": types,
                       "tagged": ["tv"] if isinstance(tv, tuple) else []})
        used["movie"].add(movie)
        used["tv"].add(tv)
        used["anime"].add(anime_id.get(an))
    others = [dict(g, types=["movie"], group="Films only") for g, (i, _) in zip(out["movie"], MOVIE_GENRES)
              if i not in used["movie"]]
    others += [dict(g, types=["tv"], group="TV shows only") for g, (i, _) in zip(out["tv"], TV_GENRES)
               if i not in used["tv"]]
    others += [dict(g, types=["anime"], group=f"Anime only - {g['group'].lower()}") for g in out["anime"]
               if int(g["key"].split(":")[1]) not in used["anime"]]
    out["all"] = shared + others
    out["sorts"] = SORT_NAMES
    out["formats"] = ANIME_KINDS
    return out


def _resolve(keys, kind, index=None):
    """What the chosen genres mean for one type: (genre ids, TMDB keyword tags),
    or None when that type doesn't have one of them."""
    anime_id = {n: i for i, n in _anime_genres(index)}
    ids, tags = [], []
    for key in keys:
        space, _, value = key.partition(":")
        if space == "all":
            row = next((r for r in SHARED if r[0] == value), None)
            if not row:
                continue
            v = {"movie": row[2], "tv": row[3], "anime": row[4]}[kind]
            if not v:
                return None
            if v == ANY:
                continue
            if isinstance(v, tuple):
                tags.append(v[1])
            elif kind == "anime":
                if v not in anime_id:
                    return None
                ids.append(anime_id[v])
            else:
                ids.append(v)
        elif space == kind and value.isdigit():
            ids.append(int(value))
        elif space in KINDS:
            return None          # a genre only another type has
    return sorted(set(ids)), sorted(set(tags))


def _today():
    return time.strftime("%Y-%m-%d")


def _order(rows, sort):
    """Sort a list of results by rating, date or popularity."""
    if sort == "rated":
        return sorted(rows, key=lambda r: (r.get("votes", MIN_VOTES) < MIN_VOTES, -(r.get("rating") or 0)))
    if sort in ("newest", "oldest"):
        dated = [r for r in rows if r.get("date")]
        dated.sort(key=lambda r: r["date"], reverse=sort == "newest")
        return dated + [r for r in rows if not r.get("date")]
    if sort == "grossing":
        return sorted(rows, key=lambda r: -(r.get("revenue") or 0))
    if sort == "title":
        return sorted(rows, key=lambda r: (r.get("title") or "").casefold())
    return sorted(rows, key=lambda r: -(r.get("members") or r.get("popularity") or 0))


def search(settings, index, kinds=(), query="", keys=(), year=None, sort="popular", fmt="", page=1):
    """One page (50) of titles from TMDB and MyAnimeList, matching every filter:
    {"results", "more", "notes", "building"}. No types means all three; several
    are mixed. `index` is the AnimeIndex."""
    query = (query or "").strip()
    kinds = [k for k in KINDS if k in kinds] or list(KINDS)
    notes, active = [], []
    for kind in kinds:
        if kind != "anime" and not settings.get("tmdb_api_key"):
            notes.append("Add your TMDB token in Settings to include TV shows and films.")
            continue
        if kind == "anime" and not settings.get("mal_client_id"):
            notes.append("Add your MyAnimeList Client ID in Settings to include anime.")
            continue
        wanted = _resolve(keys, kind, index)
        if wanted is None:
            if len(kinds) > 1:
                notes.append(f"{TYPE_NAMES[kind]} left out: they don't have one of the chosen genres.")
            continue
        active.append((kind, wanted))
    if not active:
        if notes:
            raise MetadataError(notes[0] if len(kinds) == 1 else " ".join(notes))
        raise MetadataError(f"{TYPE_NAMES[kinds[0]]} don't have that genre")
    if sort == "grossing" and any(k != "movie" for k, _ in active):
        notes.append("Box office is only known for films; the rest are sorted by popularity.")
    if any(w[1] for k, w in active if k == "tv"):
        notes.append("TMDB has no Horror, Romance or Thriller genre for TV shows; TV shows tagged with it are shown."
                     if not query else "With keywords, TV shows can't be checked for Horror, Romance or Thriller "
                     "(TMDB has no such TV genre) - clear the keywords to filter them.")
    if sort == "rated":
        notes.append(f"Highest rated counts titles with at least {MIN_VOTES} votes.")

    sizes = [PAGE // len(active) + (1 if i < PAGE % len(active) else 0) for i in range(len(active))]

    def one(job):
        (kind, (ids, tags)), size = job
        kind_sort = sort if sort != "grossing" or kind == "movie" else "popular"
        start = (page - 1) * size
        if kind == "anime":
            return _anime_slice(settings, index, query, ids, year, kind_sort, fmt, start, size)
        return _tmdb_slice(settings, kind, query, ids, tags, year, kind_sort, start, size)

    with ThreadPoolExecutor(3) as pool:
        parts = list(pool.map(_safely, [(one, job) for job in zip(active, sizes)]))
    errors = [p for p in parts if isinstance(p, MetadataError)]
    if errors and len(errors) == len(parts):
        raise errors[0]
    notes += [str(e) for e in errors]
    lists = [p[0] for p in parts if not isinstance(p, MetadataError)]
    more = any(p[1] for p in parts if not isinstance(p, MetadataError))
    if sort in ("popular", "grossing") or len(lists) == 1:
        rows = [r for group in _zip_longest(lists) for r in group]   # each list keeps its own order
    else:
        rows = _order([r for rows in lists for r in rows], sort)
    building = False
    if any(k == "anime" for k, _ in active):
        st = index.state()
        if not st["complete"]:
            building = st["building"]
            notes.insert(0, f"Still collecting every anime from MyAnimeList ({st['count']:,} so far, about "
                            "30,000 in all) - anime results fill in as it goes."
                         if st["building"] else
                         f"MyAnimeList stopped answering while collecting its anime ({st['error'] or 'try again later'})"
                         f" - {st['count']:,} so far.")
    return {"results": rows, "more": more, "notes": list(dict.fromkeys(notes)), "building": building}


TYPE_NAMES = {"tv": "TV shows", "movie": "Films", "anime": "Anime"}


def _safely(job):
    fn, arg = job
    try:
        return fn(arg)
    except MetadataError as e:
        return e


def _zip_longest(lists):
    for i in range(max((len(x) for x in lists), default=0)):
        yield [x[i] for x in lists if i < len(x)]


# ---- TV shows and films (TMDB)

def _tmdb_keyword(tmdb, word):
    cached = _keyword_cache.get(word)
    if cached is not None:
        return cached
    rows = tmdb._get("/search/keyword", query=word).get("results", [])
    found = next((r["id"] for r in rows if (r.get("name") or "").lower() == word), None)
    _keyword_cache.put(word, found or 0)
    return found or 0


def _tmdb_slice(settings, kind, query, ids, tags, year, sort, start, count):
    tmdb = _tmdb(settings)
    media = "movie" if kind == "movie" else "tv"
    if query:
        rows = _tmdb_found(tmdb, media, query, year)
        rows = [r for r in rows if set(ids) <= set(r["genre_ids"])]
        rows = _order(rows, sort)
        return rows[start:start + count], start + count < len(rows)
    date = "primary_release_date" if media == "movie" else "first_air_date"
    params = {"include_adult": "true", "sort_by": {
        "popular": "popularity.desc", "rated": "vote_average.desc", "grossing": "revenue.desc",
        "newest": f"{date}.desc", "oldest": f"{date}.asc",
        "title": "title.asc" if media == "movie" else "name.asc"}[sort], f"{date}.lte": _today()}
    if ids:
        params["with_genres"] = ",".join(map(str, ids))
    if tags:
        found = [_tmdb_keyword(tmdb, t) for t in tags]
        if not all(found):
            return [], False
        params["with_keywords"] = ",".join(map(str, found))
    if year:
        params["primary_release_year" if media == "movie" else "first_air_date_year"] = int(year)
    if sort == "rated":
        params["vote_count.gte"] = MIN_VOTES if (year or ids or tags) else (300 if media == "movie" else 100)
    first, last = start // 20 + 1, (start + count - 1) // 20 + 1
    if first > 500:
        return [], False
    key = ("discover", media, tuple(sorted(params.items())))

    def get_page(n):
        hit = _search_cache.get(key + (n,))
        if hit is None:
            hit = tmdb._get(f"/discover/{media}", page=n, **params)
            _search_cache.put(key + (n,), hit)
        return hit

    pages = [get_page(first)]
    last = min(last, 500, pages[0].get("total_pages") or first)
    if last > first:
        with ThreadPoolExecutor(3) as pool:
            pages += list(pool.map(get_page, range(first + 1, last + 1)))
    rows = [dict(tmdb.result(r, media), type=media) for p in pages for r in p.get("results", [])]
    off = start - (first - 1) * 20
    total = min(pages[0].get("total_results") or 0, 10000)
    rows = rows[off:off + count]
    if media == "tv" and 16 not in ids:
        rows = [r for r in rows if not _is_anime(r)]     # anime are in Anime
    return rows, start + count < total


def _tmdb_found(tmdb, media, query, year):
    """Up to 100 search results for a keyword search, to filter and sort here."""
    key = ("search", media, query.lower(), year)
    hit = _search_cache.get(key)
    if hit is not None:
        return hit
    extra = {}
    if year:
        extra["primary_release_year" if media == "movie" else "first_air_date_year"] = int(year)

    def get_page(n):
        return tmdb._get(f"/search/{media}", query=query, include_adult="true", page=n, **extra)

    first = get_page(1)
    pages = [first]
    total = min(first.get("total_pages") or 1, 5)
    if total > 1:
        with ThreadPoolExecutor(4) as pool:
            pages += list(pool.map(get_page, range(2, total + 1)))
    rows = [dict(tmdb.result(r, media), type=media) for p in pages for r in p.get("results", [])]
    if media == "tv":
        rows = [r for r in rows if not _is_anime(r)]
    _search_cache.put(key, rows)
    return rows


# ---- anime (MyAnimeList)

def _anime_slice(settings, index, query, ids, year, sort, fmt, start, count):
    """Anime from the index (and, for keywords and recent years, straight from
    MyAnimeList too, so nothing new is missed), filtered and sorted here."""
    index.ensure(settings)
    want_ids, today = set(ids), _today()
    want = metadata.norm_title(query) if query else ""
    year_text = str(int(year)) if year else ""

    def keep(c):
        return ((not want_ids or want_ids <= set(c["g"])) and (not fmt or c["k"] == fmt)
                and (not year_text or c["d"][:4] == year_text) and (not c["d"] or c["d"] <= today))

    found = {}
    for c in index.rows():
        if keep(c) and (not want or any(want in metadata.norm_title(n) for n in (c["t"], c["en"], *c["syn"]) if n)):
            found[c["id"]] = _from_index(c)
    extra = []
    if want and len(query) >= 3:
        try:
            extra += _mal_search(settings, query)
        except MetadataError:
            pass
    if year and int(year) >= int(today[:4]) - 1:
        extra += _anime_of_year(settings, int(year))          # this season's newest aren't ranked yet
    for r in extra:
        c = {"g": r.get("genre_ids") or [], "k": r["media_kind"], "d": r.get("date") or ""}
        if int(r["source_id"]) not in found and keep(c):
            found[int(r["source_id"])] = dict(r, votes=MIN_VOTES if r.get("rating") else 0)
    rows = _order(list(found.values()), sort)
    return rows[start:start + count], start + count < len(rows)


def _mal_search(settings, query):
    key = ("mal-search", query.lower())
    hit = _search_cache.get(key)
    if hit is not None:
        return hit
    mal = MAL(settings["mal_client_id"])
    data = mal._get("/anime", q=query[:64], limit=100, nsfw="true", fields=MAL_SEARCH_FIELDS + ",genres")
    out = [dict(mal._summary(row["node"]), members=row["node"].get("num_list_users") or 0,
                genre_ids=[g["id"] for g in row["node"].get("genres") or []]) for row in data.get("data", [])]
    _search_cache.put(key, out)
    return out


def _anime_of_year(settings, year):
    """Every anime that started in a year, from MyAnimeList's seasons, with genres."""
    key = ("anime-year", year)
    hit = _search_cache.get(key)
    if hit is not None:
        return hit
    mal = MAL(settings["mal_client_id"])

    def season(name):
        rows, offset = [], 0
        while offset < 2000:
            try:
                data = mal._get(f"/anime/season/{year}/{name}", limit=500, offset=offset, nsfw="true",
                                fields=MAL_SEARCH_FIELDS + ",genres")
            except MetadataError as e:
                if "doesn't have" in str(e):
                    return rows
                raise
            rows += data.get("data", [])
            if not (data.get("paging") or {}).get("next"):
                return rows
            offset += 500
        return rows

    with ThreadPoolExecutor(4) as pool:
        seasons = list(pool.map(season, ("winter", "spring", "summer", "fall")))
    seen, out = set(), []
    for row in (r for rows in seasons for r in rows):
        node = row.get("node") or {}
        if node.get("id") in seen:
            continue
        seen.add(node.get("id"))
        r = mal._summary(node)
        if r["year"] not in (None, year):
            continue
        r.update(members=node.get("num_list_users") or 0, genre_ids=[g["id"] for g in node.get("genres") or []])
        out.append(r)
    _search_cache.put(key, out)
    return out


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
