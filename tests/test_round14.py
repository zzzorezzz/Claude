"""Round 14: titles matched loosely but asked about once when unclear (another spelling, another
name, a number one has and the other hasn't, two named alike), an episode's page that went back to
its first episode not taken for this one's (Miruro), results whose links say only a year
(KickAssAnime), sites built like AnikotoTV, a search only a site's scripts run, a comment's buttons
not taken for servers, and a phone that can't change which programs or folders the PC uses."""
import json
import tempfile
from pathlib import Path
import unittest
from unittest import mock

from tests.test_remote import RemoteBase
from watchlist import scrape, webtools


def wanted(title, aliases=(), **kw):
    return scrape.wanted_of(dict({"title": title, "aliases": list(aliases), "type": "anime", "format": "series"}, **kw))


class LooseMatchTest(unittest.TestCase):
    def test_other_spellings_and_names_still_match(self):
        for result, title, aliases in [("Shingeki no Kyoujin", "Attack on Titan", ["Shingeki no Kyojin"]),
                                       ("Atack on Titan", "Attack on Titan", []),
                                       ("Kori no Jouheki", "The Ramparts of Ice", ["Koori no Jouheki"]),
                                       ("Kimi o Aishita Hitori no Kimi e", "Kimi wo Aishita Hitori no Kimi e", []),
                                       ("Delicious in Dungeon", "Dungeon Meshi", ["Delicious in Dungeon"]),
                                       ("SPY×FAMILY", "Spy x Family", [])]:
            self.assertGreaterEqual(scrape.score(result, wanted(title, aliases)), 75, result)

    def test_a_number_one_has_and_the_other_hasnt_is_another_title(self):
        self.assertLess(scrape.score("Jujutsu Kaisen 0", wanted("Jujutsu Kaisen")), scrape.MATCH_TO_PLAY)
        self.assertEqual(scrape.score("Kaiju No. 8", wanted("Kaiju No 8")), 100)

    def test_only_a_part_of_its_name_asks(self):
        # ("Naruto" for Naruto Shippuden, "Frieren" for its full name: asked once, not played)
        self.assertLess(scrape.score("Naruto", wanted("Naruto Shippuden")), scrape.MATCH_TO_PLAY)
        self.assertLess(scrape.score("Frieren", wanted("Frieren: Beyond Journey's End")), scrape.MATCH_TO_PLAY)
        self.assertGreaterEqual(scrape.score("Frieren", wanted("Frieren: Beyond Journey's End")), 20)


SITE = {"id": "z", "name": "Zone", "url": "https://site.test/search?q={title}", "types": ["anime"]}


def results(*rows):
    return [{"url": u, "name": n, "names": [n], "score": s, **({"year": y} if y else {})} for u, n, s, y in rows]


class AskOnceTest(unittest.TestCase):
    def scraper(self):
        return scrape.Scraper(tempfile.mkdtemp())

    def test_close_rivals_are_asked_about_then_remembered(self):
        s = self.scraper()
        item = {"id": "a", "title": "Frieren", "type": "anime", "format": "series", "source": "mal",
                "seasons": [{"number": 1, "episodes": 28}]}
        found = results(("https://site.test/a", "Frieren Beyond", 84, None), ("https://site.test/b", "Frieren Sequel", 80, None))
        with mock.patch.object(s, "search", return_value=found), self.assertRaises(scrape.ScrapeError) as asked:
            s._entry(SITE, item, None)
        self.assertEqual([c["url"] for c in asked.exception.choices], ["https://site.test/a", "https://site.test/b"])
        s.pin_entry(item, SITE, "https://site.test/b")
        with mock.patch.object(s, "search", side_effect=AssertionError("asked again")):
            self.assertEqual(s._entry(SITE, item, None)[0]["url"], "https://site.test/b")

    def test_a_remake_by_its_year(self):
        s = self.scraper()
        film = {"id": "f", "title": "The Lion King", "type": "movie", "format": "movie", "year": 2019}
        found = results(("https://site.test/1994", "The Lion King", 100, "1994"), ("https://site.test/2019", "The Lion King", 100, "2019"))
        with mock.patch.object(s, "search", return_value=found):
            self.assertEqual(s._entry(SITE, film, None)[0]["url"], "https://site.test/2019")

    def test_named_the_same_and_nothing_tells_them_apart_asks(self):
        s = self.scraper()
        film = {"id": "g", "title": "Hunter x Hunter", "type": "anime", "format": "movie"}
        found = results(("https://site.test/1", "Hunter x Hunter", 100, None), ("https://site.test/2", "Hunter x Hunter", 100, None))
        with mock.patch.object(s, "search", return_value=found), self.assertRaises(scrape.ScrapeError) as asked:
            s._entry(SITE, film, None)
        self.assertIn("more than one", str(asked.exception))


class CardNamesTest(unittest.TestCase):
    def test_a_link_saying_only_its_year_is_named_by_its_address(self):
        page = scrape.parse('<a href="/death-note-aa92">2006</a><a href="/death-note-relight-7401">2007</a>', "https://kaa.test/search?q=x")
        got = {c["url"]: c["name"] for c in scrape.candidates(page, "https://kaa.test/search?q=x")}
        self.assertEqual(got["https://kaa.test/death-note-aa92"], "Death Note")
        ranked = scrape.best_entry(scrape.candidates(page, "https://kaa.test/search?q=x"), wanted("Death Note"))
        self.assertEqual((ranked[0]["name"], ranked[0]["score"]), ("Death Note", 100))


class ServersTest(unittest.TestCase):
    def test_a_comments_buttons_arent_servers(self):
        page = scrape.parse('<div class="servers"><button data-type="sub" data-id="1">HD-1</button></div>'
                            '<div class="comments"><button class="btn cm-btn-vote" data-type="up" data-id="344">344</button>'
                            '<a class="report" data-type="spam" data-id="9">Report Spam</a></div>', "https://s.test/watch/x/ep-1")
        self.assertEqual([x["label"] for x in scrape.streams(page)], ["HD-1"])

    def test_menus_are_named_by_their_own_words(self):
        # (an episode's button holding its summary - "...the source of..." - isn't a Server menu)
        self.assertIn("menuNamed", webtools.SERVERS_JS)
        self.assertIn("menuNamed", webtools.CLICK_JS)
        self.assertNotIn("__MENU_NAMED__", webtools.SERVERS_JS + webtools.CLICK_JS)


class AnotherEpisodesVideoTest(unittest.TestCase):
    def test_the_video_another_episode_played_isnt_this_ones(self):
        s = scrape.Scraper(tempfile.mkdtemp())
        s.catch = lambda url, click=None: {"url": "https://cdn.test/ep1.m3u8", "headers": {}, "subs": []}
        s._here.taken = {"https://cdn.test/ep1.m3u8"}
        with mock.patch.object(scrape, "hls_length", return_value=None), \
                mock.patch.object(scrape, "scrambled_playlist", return_value=False), \
                mock.patch.object(scrape, "scrambled_video", return_value=False):
            self.assertIsNone(s._catch_stream("https://site.test/watch?ep=11", None))


class AnikotoLikeTest(unittest.TestCase):
    def test_a_copy_of_anikoto_has_its_whole_episode_list(self):
        s = scrape.Scraper(tempfile.mkdtemp())
        title = ('<div id="watch-main" data-id="1610"></div><a href="https://copy.test/watch/death-note-fc8mq/ep-1">Watch</a>')
        listing = json.dumps({"result": "".join(f'<a href="#" data-num="{n}" data-ids="x{n}">{n}</a>' for n in range(1, 38))})

        def fetch(url, *a, **kw):
            return (listing, url) if "/ajax/episode/list/1610" in url else (title, url)
        with mock.patch.object(s, "_read", return_value=(title, "https://copy.test/anime/death-note-fc8mq")), \
                mock.patch.object(scrape, "fetch", side_effect=fetch):
            eps = s._episodes("https://copy.test/anime/death-note-fc8mq")[0]
        self.assertEqual(len(eps), 37)
        self.assertEqual(eps[(None, 7)], "https://copy.test/watch/death-note-fc8mq/ep-7")


class TypedSearchTest(unittest.TestCase):
    def test_the_address_a_typed_search_went_to(self):
        self.assertEqual(scrape._search_template("https://a.test/search?keyword=Naruto", "Naruto"), "https://a.test/search?keyword={title}")
        self.assertEqual(scrape._search_template("https://a.test/s/death-note", "Death Note"), "https://a.test/s/{slug}")
        self.assertIsNone(scrape._search_template("https://a.test/", "Naruto"))


class PhoneCantChangeTheComputerTest(RemoteBase):
    def test_programs_and_folders_stay_as_the_computer_set_them(self):
        token, _ = self.pair()
        status, _, _ = self.phone("PUT", "/api/settings", {"custom_player_path": r"C:\evil.exe", "player": "custom",
                                                           "vlc_path": r"C:\evil.exe", "theme": "mono"}, cookie=token)
        self.assertEqual(status, 200)
        s = self.app.settings.get()
        self.assertEqual((s["custom_player_path"], s["player"], s["vlc_path"], s["theme"]), ("", "mpv", "", "mono"))

    def test_a_titles_folder_isnt_moved_from_a_phone(self):
        token, _ = self.pair()
        item = self.add("tv", "tmdb", "tv/1396")
        status, _, _ = self.phone("PUT", f"/api/items/{item['id']}", {"local_folder": tempfile.gettempdir()}, cookie=token)
        self.assertEqual(status, 403)
        self.assertEqual(self.app.store.get(item["id"])["local_folder"], "")


class UnreadableTorrentListTest(unittest.TestCase):
    def test_downloads_are_kept_when_the_list_of_torrents_cant_be_read(self):
        from watchlist.server import App
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        app = App(tmp.name)
        self.addCleanup(app.torrents.shutdown)
        folder = Path(app.torrents._save_dir())
        (folder / "Show S01").mkdir(parents=True, exist_ok=True)
        (folder / "Show S01" / "Show - 01.mkv").write_bytes(b"x" * 10)
        app.torrents.state_path.write_bytes(bytes(64))          # (a save cut short: all zeros)
        app.torrents.records = scrape.read_json(app.torrents.state_path, {}).get("torrents", {})
        self.assertEqual(app.torrents.clean_leftovers(), 0)
        self.assertTrue((folder / "Show S01" / "Show - 01.mkv").exists())


class ShortsTest(unittest.TestCase):
    def test_a_short_isnt_watched_by_being_opened(self):
        from watchlist import player
        self.assertFalse(player.finished(5, 90))
        self.assertTrue(player.finished(85, 90))
        self.assertTrue(player.finished(1290, 1400))


class NegativeLengthTest(RemoteBase):
    def test_a_body_said_to_be_minus_one_long_is_refused(self):
        status, _, _ = self.phone("POST", "/api/pair/request", None, headers={"Content-Length": "-1"})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
