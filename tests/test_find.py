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


def mal_node(i, title, members, mean, start="2023-04-07", kind="tv"):
    return {"node": {"id": i, "title": title, "start_date": start, "mean": mean, "media_type": kind,
                     "num_list_users": members, "main_picture": {}, "alternative_titles": {}}}


class FindTest(test_server.ServerBase):
    def setUp(self):
        super().setUp()
        discover._list_cache._data.clear()
        discover._genre_cache._data.clear()
        self.urls = []
        patch = mock.patch.object(metadata, "_get_json", side_effect=self.fake)
        patch.start()
        self.addCleanup(patch.stop)

    def fake(self, url, headers=None, service="the server"):
        self.urls.append(url)
        parts = urllib.parse.urlsplit(url)
        q = dict(urllib.parse.parse_qsl(parts.query))
        if parts.path == "/3/discover/tv":
            page = int(q["page"])
            rows = [tmdb_row(page * 100 + n, f"Show {page}-{n}") for n in range(20)]
            rows[0] = tmdb_row(page * 100, f"Anime {page}", genre_ids=[16], original_language="ja")
            return {"results": rows, "total_pages": 3}
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
        if parts.path in ("/3/genre/tv/list", "/3/genre/movie/list"):
            return {"genres": [{"id": 18, "name": "Drama"}, {"id": 16, "name": "Animation"}]}
        if parts.path == "/v4/genres/anime":
            return {"data": [{"mal_id": 1, "name": "Action"}, {"mal_id": 1, "name": "Action"},
                             {"mal_id": 4, "name": "Comedy"}]}
        if parts.path == "/v4/anime":
            return {"data": [{"mal_id": 52991, "title": "Sousou no Frieren", "title_english": "Frieren",
                              "type": "TV", "year": 2023, "score": 9.3, "members": 900000,
                              "images": {"jpg": {"large_image_url": "https://img/f.jpg"}}}],
                    "pagination": {"has_next_page": True}}
        return fixtures.fake_get_json(url, headers, service)

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

    def test_whats_already_in_your_list_is_marked(self):
        frieren = self.add("anime", "mal", "52991")
        status, data = self.call("GET", "/api/find/search?type=anime&q=frieren&sort=rated")
        self.assertEqual(status, 200, data)
        self.assertEqual(data["results"][0]["in_list"], frieren["id"])
        self.assertTrue(data["more"])
        url = self.urls[-1]
        self.assertIn("order_by=score", url)
        self.assertIn("q=frieren", url)

    def test_genres_and_sorts(self):
        status, data = self.call("GET", "/api/find/genres?type=anime")
        self.assertEqual([g["name"] for g in data["genres"]], ["Action", "Comedy"])
        self.assertTrue(data["kinds"])
        status, data = self.call("GET", "/api/find/genres?type=movie")
        self.assertIn("grossing", [k for k, _ in data["sorts"]])
        status, data = self.call("GET", "/api/find/genres?type=films")
        self.assertEqual(status, 400)

    def test_browse_by_genre_and_box_office(self):
        status, data = self.call("GET", "/api/find/search?type=tv&genre=18&sort=popular")
        self.assertEqual(status, 200, data)
        self.assertIn("with_genres=18", self.urls[-1])
        self.assertFalse(any(r["title"].startswith("Anime") for r in data["results"]))
        # An unknown sort falls back to most popular.
        self.call("GET", "/api/find/search?type=tv&sort=grossing")
        self.assertIn("popularity.desc", self.urls[-1])

    def test_needs_the_right_key(self):
        self.app.settings.update({"tmdb_api_key": ""})
        status, data = self.call("GET", "/api/find/top?type=movie&year=2023")
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
