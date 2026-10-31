"""Every site searched at once when none is chosen: the first in order plays if it has
the video within RACE_WAIT seconds of another; else the one that has it. One only."""
import tempfile
import time
import unittest

from watchlist import scrape
from watchlist.server import App, OnlineError


class OnlineRaceTest(unittest.TestCase):
    def setUp(self):
        self.app = App(tempfile.mkdtemp())
        self.app.RACE_WAIT = 0.6
        self.sites = [{"id": "a", "name": "A", "url": "https://a.example/s?q={title}"},
                      {"id": "b", "name": "B", "url": "https://b.example/s?q={title}"}]
        self.item = {"id": "x", "title": "X", "format": "series"}

    def race(self, plan):
        """plan: site id -> (seconds, found or an error message)."""
        def resolve(site, item, season, episode, track="", fresh=False):
            secs, got = plan[site["id"]]
            time.sleep(secs)
            if isinstance(got, str):
                raise scrape.ScrapeError(got)
            return got
        self.app.scraper.resolve = resolve
        return self.app._online_race(self.item, "1:1", 1, 1, "", self.sites)

    def test_the_first_site_wins_when_it_comes_in_time(self):
        site, got = self.race({"a": (0.3, {"url": "A"}), "b": (0.05, {"url": "B"})})
        self.assertEqual((site["id"], got["url"]), ("a", "A"))

    def test_another_site_plays_when_the_first_takes_too_long(self):
        t0 = time.time()
        site, got = self.race({"a": (3.0, {"url": "A"}), "b": (0.05, {"url": "B"})})
        self.assertEqual((site["id"], got["url"]), ("b", "B"))
        self.assertLess(time.time() - t0, 2.0)

    def test_another_site_plays_when_the_first_has_nothing(self):
        site, got = self.race({"a": (0.05, "not there"), "b": (0.2, {"url": "B"})})
        self.assertEqual(site["id"], "b")

    def test_nothing_anywhere_says_why(self):
        with self.assertRaises(OnlineError) as e:
            self.race({"a": (0.05, "not there"), "b": (0.05, "nor here")})
        self.assertIn("A: not there", str(e.exception))
        self.assertIn("B: nor here", str(e.exception))


if __name__ == "__main__":
    unittest.main()
