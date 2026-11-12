"""Round 13: a show's first season told from its second when a site names them alike ("Koori no
Jouheki" and "Koori no Jouheki (2026)"), and the next result as good tried when the one opened
hasn't the episode."""
import tempfile
import threading
import time
import unittest
from unittest import mock

from watchlist import scrape, webtools

S1 = "https://site.test/anime/s1"
S2 = "https://site.test/anime/s2"
SITE = {"id": "z", "name": "Zone", "url": "https://site.test/anime?search={title}", "types": ["anime"]}
ITEM = {"id": "k1", "title": "Koori no Jouheki", "type": "anime", "source": "mal", "format": "series", "year": 2026,
        "seasons": [{"number": 1, "episodes": 14}]}


def episodes(url):
    count = {S1: 14, S2: 6}[url]
    return {(None, n): f"{url}/{n}" for n in range(1, count + 1)}, url, None, ""


def ranked(first, second):
    names = {S1: "Koori no Jouheki", S2: "Koori no Jouheki (2026)"}
    return [{"url": u, "name": names[u], "names": [names[u]], "score": 100} for u in (first, second)]


class AlikeNamesTest(unittest.TestCase):
    def test_same_but_a_year(self):
        self.assertTrue(scrape._same_but_year("Koori no Jouheki", "Koori no Jouheki (2026)"))
        self.assertTrue(scrape._same_but_year("Hunter x Hunter 2011", "Hunter x Hunter"))
        self.assertFalse(scrape._same_but_year("Koori no Jouheki", "Koori no Jouheki"))
        self.assertFalse(scrape._same_but_year("Bleach", "Bleach: Thousand-Year Blood War"))

    def test_the_season_whose_episodes_fit(self):
        s = scrape.Scraper(tempfile.mkdtemp())
        with mock.patch.object(s, "search", return_value=ranked(S2, S1)), \
                mock.patch.object(s, "_episodes", side_effect=episodes):
            page, entry, _ = s.episode_page(SITE, ITEM, None, 7)
        self.assertEqual((entry["url"], page), (S1, f"{S1}/7"))
        # Its second season (6 episodes so far) is the other one.
        s = scrape.Scraper(tempfile.mkdtemp())
        second = dict(ITEM, id="k2", seasons=[{"number": 1, "episodes": 6}])
        with mock.patch.object(s, "search", return_value=ranked(S1, S2)), \
                mock.patch.object(s, "_episodes", side_effect=episodes):
            self.assertEqual(s.episode_page(SITE, second, None, 3)[1]["url"], S2)

    def test_no_count_known_you_are_asked(self):
        # (both of the title's year, as far as anyone can tell: you pick - once - the two first)
        s = scrape.Scraper(tempfile.mkdtemp())
        with mock.patch.object(s, "search", return_value=ranked(S2, S1)), \
                mock.patch.object(s, "_episodes", side_effect=episodes), \
                self.assertRaises(scrape.ScrapeError) as asked:
            s.episode_page(SITE, dict(ITEM, seasons=[]), None, 2)
        self.assertEqual({c["url"] for c in asked.exception.choices[:2]}, {S1, S2})
        s.pin_entry(ITEM, SITE, S1)
        with mock.patch.object(s, "_episodes", side_effect=episodes):
            self.assertEqual(s.episode_page(SITE, dict(ITEM, seasons=[]), None, 2)[1]["url"], S1)

    def test_chosen_before_is_checked_once(self):
        s = scrape.Scraper(tempfile.mkdtemp())
        s._remember(ITEM["id"], SITE, entries={"": {"url": S2, "name": "Koori no Jouheki (2026)", "score": 100,
                                                    "at": 9e12}},
                    others=[{"url": S2, "name": "Koori no Jouheki (2026)", "score": 100},
                            {"url": S1, "name": "Koori no Jouheki", "score": 100}],
                    episodes={":1:2": f"{S2}/2"})
        with mock.patch.object(s, "_episodes", side_effect=episodes) as read:
            page, entry, _ = s.episode_page(SITE, ITEM, None, 2)
            self.assertEqual((entry["url"], page), (S1, f"{S1}/2"))
            n = read.call_count
            s.episode_page(SITE, ITEM, None, 3)
            self.assertEqual(read.call_count, n)              # (not again)

    def test_no_such_episode_the_next_as_good(self):
        s = scrape.Scraper(tempfile.mkdtemp())
        s._remember(ITEM["id"], SITE, entries={"": {"url": S2, "name": "Koori no Jouheki (2026)", "score": 100,
                                                    "at": 9e12, "alike": True}},
                    others=[{"url": S2, "name": "Koori no Jouheki (2026)", "score": 100},
                            {"url": S1, "name": "Koori no Jouheki", "score": 96}])
        with mock.patch.object(s, "_episodes", side_effect=episodes), \
                mock.patch.object(scrape, "_exists", return_value=False):
            page, entry, _ = s.episode_page(SITE, ITEM, None, 9)
        self.assertEqual((entry["url"], page), (S1, f"{S1}/9"))
        self.assertEqual(s.remembered(ITEM["id"], SITE)["entries"][""]["url"], S1)    # (kept from now on)


class ServersListTest(unittest.TestCase):
    def test_only_sub_servers_seen_its_page_is_listed_again(self):
        s = scrape.Scraper(tempfile.mkdtemp())
        s.servers = mock.Mock(return_value=[{"label": "sun DL S-SUB", "kind": "sub", "on": True, "frame": ""},
                                            {"label": "sun DL", "kind": "dub", "on": False, "frame": ""}])
        have = [{"id": "click||sun DL S-SUB|sub", "label": "sun DL S-SUB", "kind": "sub"}]
        got = s.episode_servers({"page": "https://site.test/watch/1?ep=1", "streams": have})
        self.assertEqual([(x["label"], x["kind"]) for x in got], [("sun DL S-SUB", "sub"), ("sun DL", "dub")])
        # Sub and dub seen already: nothing more to look for.
        s.servers.reset_mock()
        both = have + [{"id": "4:dub:HD-1", "label": "HD-1", "kind": "dub"}]
        self.assertEqual(s.episode_servers({"page": "https://site.test/watch/1?ep=1", "streams": both}), both)
        s.servers.assert_not_called()


class HiddenPagesTest(unittest.TestCase):
    def test_one_is_always_left_for_what_you_ask(self):
        """The app's background look-ups take two of the three hidden pages at most."""
        # (pages of its own: other tests' look-ups may still be going)
        for name, n in (("_lock", 3), ("_background", 2)):
            patch = mock.patch.object(webtools, name, threading.BoundedSemaphore(n))
            patch.start()
            self.addCleanup(patch.stop)
        held, release = [], threading.Event()

        def behind():
            webtools.background()
            with webtools.slot():
                held.append(1)
                release.wait(5)
        threads = [threading.Thread(target=behind) for _ in range(3)]
        for th in threads:
            th.start()
        time.sleep(0.3)
        self.assertEqual(len(held), 2)
        got = threading.Event()

        def yours():
            with webtools.slot():
                got.set()
        threading.Thread(target=yours).start()
        self.assertTrue(got.wait(2))
        release.set()
        for th in threads:
            th.join(5)
        self.assertEqual(len(held), 3)

    def test_a_pressed_servers_video_is_taken_when_it_comes(self):
        opts = {"clicked_at": 100.0}
        self.assertTrue(webtools.pressed({"label": "HD-1"}, opts, [("v.m3u8", {}, 101.0)]))
        self.assertFalse(webtools.pressed({"label": "HD-1"}, opts, [("v.m3u8", {}, 99.0)]))     # (before the press)
        self.assertFalse(webtools.pressed(None, opts, [("v.m3u8", {}, 101.0)]))
        self.assertFalse(webtools.pressed({"label": "HD-1"}, {}, [("v.m3u8", {}, 101.0)]))


if __name__ == "__main__":
    unittest.main()
