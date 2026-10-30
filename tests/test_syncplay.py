import unittest

from watchlist import syncplay


class InviteTest(unittest.TestCase):
    def test_round_trip(self):
        item = {"title": "Frieren: Beyond Journey's End", "year": 2023, "type": "anime",
                "source": "mal", "source_id": "52991"}
        code = syncplay.make_invite("syncplay.pl:8999", "watchlist-abc", item, 1, 5)
        self.assertTrue(code.startswith("WLP1."))
        got = syncplay.read_invite(f"  here you go: {code[:20]}\n{code[20:]} ")
        self.assertEqual((got["room"], got["server"], got["source_id"], got["season"], got["episode"]),
                         ("watchlist-abc", "syncplay.pl:8999", "52991", 1, 5))

    def test_an_online_episode_goes_with_the_invite_without_cookies(self):
        now = {"src": "mal", "id": "1735", "type": "anime", "season": 1, "episode": 3, "key": "mal:1735:1:3",
               "url": "https://cdn.test/master.m3u8", "referer": "https://player.test/", "track": "dub",
               "headers": {"Origin": "https://player.test", "Cookie": "secret=1"},
               "site": {"name": "Mine", "url": "https://mine.test/search?q={title}"}, "entry": "https://mine.test/anime/x"}
        got = syncplay.read_invite(syncplay.make_invite("syncplay.pl:8999", "watchanything-ab", host="me", now=now))
        self.assertEqual(got["now"]["site"]["url"], "https://mine.test/search?q={title}")
        self.assertEqual((got["now"]["entry"], got["now"]["track"]), ("https://mine.test/anime/x", "dub"))
        self.assertEqual(got["now"]["headers"], {"Origin": "https://player.test"})
        self.assertIsNone(syncplay.online_source({"site": {"url": "javascript:alert(1)"}}))

    def test_bad_codes(self):
        for code in ["", "hello", "WLP1.", "WLP1.abc", "WLP1." + "e30"]:
            with self.assertRaises(ValueError):
                syncplay.read_invite(code)

    def test_host_name(self):
        item = {"title": "Dark", "year": 2017, "type": "tv", "source": "tmdb", "source_id": "tv/70523"}
        code = syncplay.make_invite("syncplay.pl:8999", "watchlist-abc", item, host="Ali")
        got = syncplay.read_invite(code)
        self.assertEqual(got["host"], "Ali")
        self.assertNotIn("episode", got)
        self.assertIsNone(syncplay.read_invite(syncplay.make_invite("s:1", "r", item))["host"])
