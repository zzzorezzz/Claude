import unittest
from unittest import mock

from tests import fixtures
from watchlist import metadata

SETTINGS = {"tmdb_api_key": "eyJhbGciOiJIUzI1NiJ9.test.token", "mal_client_id": "client123"}


class MetadataTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(metadata, "_get_json", side_effect=fixtures.fake_get_json)
        self.get = patcher.start()
        self.addCleanup(patcher.stop)
        metadata._provider_cache._data.clear()
        metadata._season_cache._data.clear()

    def test_tmdb_token_uses_bearer_header_and_key_uses_param(self):
        metadata.TMDB(SETTINGS["tmdb_api_key"]).search("breaking", "tv")
        url, headers = self.get.call_args[0][:2]
        self.assertEqual(headers["Authorization"], f"Bearer {SETTINGS['tmdb_api_key']}")
        self.assertNotIn("api_key", url)
        metadata.TMDB("e88a65abc").search("breaking", "tv")
        url, headers = self.get.call_args[0][:2]
        self.assertIn("api_key=e88a65abc", url)
        self.assertNotIn("Authorization", headers)

    def test_tv_details(self):
        item = metadata.details(SETTINGS, "tv", "tmdb", "tv/1396")
        self.assertEqual(item["title"], "Breaking Bad")
        self.assertEqual(item["format"], "series")
        self.assertEqual(item["seasons"], [{"number": 1, "episodes": 7}, {"number": 2, "episodes": 13}])
        self.assertEqual(item["trailer"], "HhesaQXLuRY", "official trailer wins over fan uploads and featurettes")
        self.assertEqual(item["rating"], 8.9)
        self.assertEqual(item["links"]["imdb"], "https://www.imdb.com/title/tt0903747/")
        self.assertTrue(item["backdrop"].endswith("/w1280/bb-back.jpg"))

    def test_movie_details_have_box_office(self):
        item = metadata.details(SETTINGS, "movie", "tmdb", "movie/27205")
        self.assertEqual(item["revenue"], 839030630)
        self.assertEqual(item["runtime"], 148)
        self.assertEqual(item["format"], "movie")

    def test_anime_from_mal_is_enriched(self):
        results = metadata.search(SETTINGS, "anime", "frieren")
        self.assertEqual(results[0]["title"], "Frieren: Beyond Journey's End")
        self.assertEqual(results[0]["original_title"], "Sousou no Frieren")
        item = metadata.details(SETTINGS, "anime", "mal", "52991")
        self.assertEqual(item["rating_source"], "MyAnimeList")
        self.assertEqual(item["popularity"], 1100000)
        self.assertEqual(item["seasons"], [{"number": 1, "episodes": 28}])
        self.assertEqual(item["runtime"], 24)
        self.assertNotIn("MAL Rewrite", item["overview"])
        self.assertEqual(item["trailer"], "qgQQQ7ZRrm4")
        self.assertEqual(item["tmdb_ref"], "tv/209867")
        self.assertTrue(item["backdrop"].endswith("/frieren-back.jpg"))
        self.assertEqual(item["studios"], ["Madhouse"])

    def test_mal_header(self):
        metadata.MAL("client123").search("frieren")
        self.assertEqual(self.get.call_args[0][1], {"X-MAL-CLIENT-ID": "client123"})

    def test_short_mal_search_is_refused_without_a_request(self):
        with self.assertRaises(metadata.MetadataError):
            metadata.MAL("client123").search("ab")
        self.get.assert_not_called()

    def test_missing_keys(self):
        with self.assertRaisesRegex(metadata.MetadataError, "TMDB"):
            metadata.search({"tmdb_api_key": "", "mal_client_id": ""}, "tv", "x")
        with self.assertRaisesRegex(metadata.MetadataError, "MyAnimeList"):
            metadata.search({"tmdb_api_key": "", "mal_client_id": ""}, "anime", "xyz")

    def test_providers_and_services(self):
        tmdb = metadata.TMDB("key")
        pk = tmdb.providers("tv/1396", "PK")
        self.assertEqual([metadata.service_of(p) for p in pk["services"]], ["netflix"])
        us = tmdb.providers("movie/27205", "US")
        self.assertEqual(metadata.service_of(us["services"][0]), "hbomax")
        self.assertEqual(tmdb.providers("movie/27205", "PK")["services"][0]["kind"], "rent")

    def test_strip_season(self):
        self.assertEqual(metadata._strip_season("Shingeki no Kyojin Season 3 Part 2"), "Shingeki no Kyojin")
        self.assertEqual(metadata._strip_season("Kimetsu no Yaiba: 2nd Season"), "Kimetsu no Yaiba")


if __name__ == "__main__":
    unittest.main()
