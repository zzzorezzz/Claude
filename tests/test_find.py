"""Find Media: top titles of a year, search and browse, and "I'm in the mood for..."."""

import urllib.parse
from unittest import mock

from tests import fixtures
from tests import test_server
from watchlist import ai, discover, metadata


def tmdb_row(i, name, **extra):
    return dict({"id": i, "name": name, "first_air_date": "2023-04-01", "overview": "", "poster_path": "/p.jpg",
                 "vote_average": 8.0, "vote_count": 500, "popularity": 100 - i, "genre_ids": [18],
                 "original_language": "en"}, **extra)


def mal_node(i, title, members, mean, start="2023-04-07", kind="tv", genres=()):
    return {"node": {"id": i, "title": title, "start_date": start, "mean": mean, "media_type": kind,
                     "num_list_users": members, "main_picture": {}, "alternative_titles": {},
                     "genres": [{"id": g, "name": GENRE_NAMES[g]} for g in genres]}}


GENRE_NAMES = {14: "Horror", 4: "Comedy", 62: "Isekai"}


def ranked(i):
    """MyAnimeList's i-th most popular anime, made up: every 3rd is horror, every
    5th a comedy, every 7th a film; ten years of them."""
    genres = [g for g, every in ((14, 3), (4, 5)) if i % every == 0] + ([62] if i == 7 else [])
    return mal_node(i, f"Anime {i}", 100000 - i, round(5 + (i * 37 % 50) / 10, 2),
                    f"{2015 + i % 10}-{1 + i % 12:02d}-01", "movie" if i % 7 == 0 else "tv", genres)


class FindTest(test_server.ServerBase):
    def setUp(self):
        super().setUp()
        discover._list_cache._data.clear()
        discover._search_cache._data.clear()
        discover._keyword_cache._data.clear()
        self.urls = []
        patch = mock.patch.object(metadata, "_get_json", side_effect=self.fake)
        patch.start()
        self.addCleanup(patch.stop)
        sleep = mock.patch.object(discover.time, "sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def fake(self, url, headers=None, service="the server"):
        self.urls.append(url)
        parts = urllib.parse.urlsplit(url)
        if parts.netloc == "api.jikan.moe":
            self.assertRegex(parts.path, r"^/v4/anime/\d+$")             # only a title's trailer, never searches
        q = dict(urllib.parse.parse_qsl(parts.query))
        if parts.path in ("/3/discover/tv", "/3/discover/movie"):
            page = int(q["page"])
            if page > 3:
                return {"results": [], "total_pages": 3, "total_results": 60}
            name = "Show" if "tv" in parts.path else "Film"
            rows = [tmdb_row(page * 100 + n, f"{name} {page}-{n}") for n in range(20)]
            if "tv" in parts.path:
                rows[0] = tmdb_row(page * 100, f"Anime {page}", genre_ids=[16], original_language="ja")
            return {"results": rows, "total_pages": 3, "total_results": 60}
        if parts.path == "/3/search/keyword":
            return {"results": [{"id": 315058, "name": "horror"}, {"id": 1, "name": "horror movie"}]}
        if parts.path == "/v2/anime/ranking":
            offset = int(q["offset"])
            nodes = [ranked(i) for i in range(offset + 1, min(offset + 500, 520) + 1)]
            return {"data": nodes, "paging": {"next": "more"} if offset + 500 < 520 else {}}
        if parts.path.startswith("/v2/anime/season/2023/"):
            season = parts.path.rsplit("/", 1)[1]
            if season == "fall":
                return {"data": [mal_node(1, "Frieren", 900000, 9.3, "2023-09-29"),
                                 mal_node(2, "Earlier", 5000, 7.0, "2022-10-01")]}   # carried over from 2022
            if season == "summer":
                return {"data": [mal_node(3, "Popular", 1000000, 7.5), mal_node(4, "Tiny", 100, 9.9),
                                 mal_node(1, "Frieren", 900000, 9.3, "2023-09-29"),
                                 mal_node(5, "A song", 999999, 8, kind="music")]}
            raise metadata.MetadataError("MyAnimeList doesn't have that title")
        return fixtures.fake_get_json(url, headers, service)

    def build_index(self):
        status, data = self.call("GET", "/api/find/genres")
        self.assertEqual(status, 200, data)
        self.app.anime_index._thread.join(5)
        return data

    def find(self, **query):
        status, data = self.call("GET", "/api/find/search?" + urllib.parse.urlencode(query))
        self.assertEqual(status, 200, data)
        return data

    def test_top_tv_shows_of_a_year_leave_anime_out(self):
        status, data = self.call("GET", "/api/find/top?type=tv&year=2023&order=popular")
        self.assertEqual(status, 200, data)
        titles = [r["title"] for r in data["results"]]
        self.assertEqual(len(titles), 57)              # 3 pages of 20, less the 3 anime
        self.assertFalse(any(t.startswith("Anime") for t in titles))
        self.assertIn("first_air_date_year=2023", self.urls[0])
        self.assertIn("sort_by=popularity.desc", self.urls[0])

        self.call("GET", "/api/find/top?type=tv&year=2023&order=rated")
        self.assertIn("vote_average.desc", self.urls[-1])
        self.assertIn("vote_count.gte", self.urls[-1])

    def test_top_anime_of_a_year_from_the_seasons(self):
        status, data = self.call("GET", "/api/find/top?type=anime&year=2023&order=popular")
        self.assertEqual(status, 200, data)
        self.assertEqual([r["title"] for r in data["results"]], ["Popular", "Frieren", "Tiny"])
        status, data = self.call("GET", "/api/find/top?type=anime&year=2023&order=rated")
        # Hardly anyone has rated "Tiny", so it goes to the end.
        self.assertEqual([r["title"] for r in data["results"]], ["Frieren", "Popular"])

    def test_every_anime_is_collected_from_myanimelist_once(self):
        data = self.build_index()
        state = self.app.anime_index.state()
        self.assertEqual((state["count"], state["complete"]), (520, True))
        self.assertEqual(sum("/v2/anime/ranking" in u for u in self.urls), 2)
        # The genres come from MyAnimeList's titles, adult ones included.
        names = {g["name"] for g in data["anime"]}
        self.assertTrue({"Horror", "Isekai", "Hentai", "Ecchi", "Shounen"} <= names)
        self.assertEqual({g["group"] for g in data["anime"]}, {"Genres", "Themes", "Audience", "Adult"})
        horror = next(g for g in data["anime"] if g["name"] == "Horror")
        self.assertEqual(horror["key"], "anime:14")
        # Kept: after a restart, nothing is fetched again.
        again = discover.AnimeIndex(self.app.anime_index.path)
        self.assertEqual(again.state()["count"], 520)
        before = len(self.urls)
        again.ensure(self.app.settings.get())
        self.assertIsNone(again._thread)
        self.assertEqual(len(self.urls), before)

    def test_one_genre_every_anime_sorted_like_myanimelist(self):
        self.build_index()
        data = self.find(types="anime", genres="anime:14", sort="popular")
        self.assertEqual(len(data["results"]), 50)
        self.assertTrue(data["more"])
        self.assertTrue(all(14 in r["genre_ids"] for r in data["results"]))
        self.assertEqual(data["results"][0]["title"], "Anime 3")          # the most members
        page4 = self.find(types="anime", genres="anime:14", sort="popular", page=4)
        self.assertEqual(len(page4["results"]), 173 - 150)                # 173 horror anime in all
        self.assertFalse(page4["more"])

        rated = self.find(types="anime", genres="anime:14", sort="rated")["results"]
        self.assertEqual([r["rating"] for r in rated], sorted((r["rating"] for r in rated), reverse=True))
        newest = self.find(types="anime", genres="anime:14", sort="newest")["results"]
        self.assertEqual([r["date"] for r in newest], sorted((r["date"] for r in newest), reverse=True))
        titles = [r["title"] for r in self.find(types="anime", sort="title")["results"]]
        self.assertEqual(titles, sorted(titles, key=str.casefold))

    def test_genres_year_and_format_together(self):
        self.build_index()
        data = self.find(types="anime", genres="anime:14,anime:4", year=2020, format="tv")
        self.assertTrue(data["results"])
        for r in data["results"]:
            self.assertTrue({14, 4} <= set(r["genre_ids"]))
            self.assertEqual((r["year"], r["media_kind"]), (2020, "tv"))
        films = self.find(types="anime", format="movie")["results"]
        self.assertTrue(films and all(r["media_kind"] == "movie" for r in films))

    def test_all_types_at_once(self):
        self.build_index()
        data = self.find(genres="all:horror", sort="popular")
        kinds = {r["type"] for r in data["results"]}
        self.assertEqual(kinds, {"anime", "movie", "tv"})
        self.assertGreaterEqual(len(data["results"]), 48)    # 50, less any anime TMDB lists as TV shows
        movie_url = next(u for u in self.urls if "/discover/movie" in u)
        tv_url = next(u for u in self.urls if "/discover/tv" in u)
        self.assertIn("with_genres=27", movie_url)
        self.assertIn("with_keywords=315058", tv_url)                     # TMDB has no horror TV genre
        self.assertTrue(any("tagged" in n for n in data["notes"]))
        anime = [r for r in data["results"] if r["type"] == "anime"]
        self.assertTrue(all(14 in r["genre_ids"] for r in anime))
        # Mixed and sorted by rating: one list, highest first.
        rated = self.find(genres="all:horror", sort="rated")["results"]
        self.assertEqual([r["rating"] for r in rated], sorted((r["rating"] for r in rated), reverse=True))

    def test_a_genre_only_one_type_has(self):
        self.build_index()
        data = self.find(genres="anime:62")
        self.assertEqual([r["title"] for r in data["results"]], ["Anime 7"])
        self.assertTrue(any("left out" in n for n in data["notes"]))
        self.assertFalse(any("/discover/" in u for u in self.urls))

    def test_fifty_films_at_a_time(self):
        data = self.find(types="movie", sort="grossing", page=2, year=2024, genres="movie:27")
        self.assertEqual(len(data["results"]), 10)                        # 60 in all: 50 + 10
        self.assertFalse(data["more"])
        urls = [u for u in self.urls if "/discover/movie" in u]
        self.assertEqual(sorted(dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(u).query))["page"] for u in urls),
                         ["3"])
        self.assertIn("revenue.desc", urls[0])
        self.assertIn("primary_release_year=2024", urls[0])

    def test_keywords_search_myanimelist_and_mark_your_list(self):
        frieren = self.add("anime", "mal", "52991")
        self.build_index()
        data = self.find(types="anime", q="frieren")
        self.assertEqual(data["results"][0]["in_list"], frieren["id"])
        self.assertTrue(any("/v2/anime?" in u and "q=frieren" in u for u in self.urls))

    def test_needs_the_right_key(self):
        self.app.settings.update({"tmdb_api_key": ""})
        status, data = self.call("GET", "/api/find/top?type=movie&year=2023")
        self.assertEqual(status, 400)
        self.assertIn("TMDB", data["error"])
        status, data = self.call("GET", "/api/find/search?types=movie")
        self.assertEqual(status, 400)
        self.assertIn("TMDB", data["error"])


class MoodTest(test_server.ServerBase):
    def setUp(self):
        super().setUp()
        self.app.settings.update({"gemini_api_key": "key"})
        self.prompts = []

    def talk(self, replies, messages):
        def fake_ask(settings, prompt, **kw):
            self.prompts.append(prompt)
            return replies.pop(0), "gemini"
        with mock.patch.object(ai, "ask", side_effect=fake_ask):
            return self.call("POST", "/api/find/mood", {"messages": messages})

    def test_asks_then_recommends_from_your_list_and_new(self):
        bb = self.add("tv", "tmdb", "tv/1396")
        status, data = self.talk([{"question": "Funny or serious?"}], [{"role": "you", "text": "something good"}])
        self.assertEqual(status, 200, data)
        self.assertEqual(data["question"], "Funny or serious?")
        self.assertIn("Breaking Bad", self.prompts[0])

        picks = {"intro": "Here you go", "from_list": [{"id": bb["id"], "why": "you started it"}, {"id": "gone"}],
                 "new": [{"title": "Frieren", "year": 2023, "type": "anime", "why": "calm"},
                         {"title": "Made Up Show", "year": 2020, "type": "tv", "why": "?"}]}
        messages = [{"role": "you", "text": "something good"}, {"role": "ai", "text": "Funny or serious?"},
                    {"role": "you", "text": "serious"}]
        status, data = self.talk([picks], messages)
        self.assertEqual(status, 200, data)
        self.assertEqual([r["title"] for r in data["from_list"]], ["Breaking Bad"])
        self.assertEqual(data["from_list"][0]["why"], "you started it")
        # Only titles really found on MyAnimeList / TMDB are shown.
        self.assertEqual([r["source"] for r in data["new"]], ["mal"])
        self.assertEqual(data["new"][0]["why"], "calm")

    def test_no_more_than_two_questions(self):
        messages = [{"role": "you", "text": "x"}, {"role": "ai", "text": "a?"},
                    {"role": "you", "text": "y"}, {"role": "ai", "text": "b?"}, {"role": "you", "text": "z"}]
        status, data = self.talk([{"question": "c?"}], messages)
        self.assertEqual(status, 200, data)
        self.assertNotIn("question", data)
        self.assertIn("don't ask anything more", self.prompts[0])

    def test_needs_an_ai_key_and_something_to_go_on(self):
        status, _ = self.talk([], [])
        self.assertEqual(status, 400)
        self.app.settings.update({"gemini_api_key": ""})
        status, data = self.talk([], [{"role": "you", "text": "x"}])
        self.assertEqual(status, 400)
        self.assertIn("AI", data["error"])
