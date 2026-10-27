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
