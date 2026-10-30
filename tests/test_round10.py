"""Round 10: playing from your own video sites (the scraper), "Play this with",
packs worked out by name straight away (another season's episode never plays
as this one's), old torrent files cleaned up, related entries by name, and
the settings that came with them."""

import http.server
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.test_server import ServerBase
from watchlist import launcher, packs, player, scrape, webtools
from watchlist import torrents as torrents_mod
from watchlist.config import Settings


def anime(title, aliases=(), media_type="tv", **kw):
    return dict({"id": "x1", "title": title, "original_title": "", "aliases": list(aliases), "source": "mal",
                 "source_id": "1", "format": "movie" if media_type == "movie" else "series", "media_type": media_type,
                 "type": "anime", "year": None, "seasons": [{"number": 1, "episodes": 25}]}, **kw)


# ---- the scraper's parts

class ScoringTest(unittest.TestCase):
    RESULTS = ["Haikyuu!!", "Haikyuu!! Second Season", "Haikyuu!! Karasuno Koukou vs. Shiratorizawa Gakuen Koukou",
               "Haikyuu!! To the Top", "Haikyuu!! To the Top Part 2", "Haikyuu!! Movie 1: Owari to Hajimari",
               "Haikyuu!! (Dub)", "Haikyuu!! Riku vs. Kuu (OVA)"]

    def best(self, item, season=None):
        wanted = scrape.wanted_of(item, season)
        found = [{"url": f"https://site/{i}", "name": n, "names": [n]} for i, n in enumerate(self.RESULTS)]
        return scrape.best_entry(found, wanted)[0]["name"]

    def test_the_right_season_of_an_anime(self):
        self.assertEqual(self.best(anime("Haikyuu!!", ["Haikyu!!"])), "Haikyuu!!")
        self.assertEqual(self.best(anime("Haikyuu!! Second Season", ["Haikyu!! 2nd Season"])), "Haikyuu!! Second Season")
        self.assertEqual(self.best(anime("Haikyuu!! To the Top", ["Haikyu!! 4th Season"])), "Haikyuu!! To the Top")
        self.assertEqual(self.best(anime("Haikyuu!! To the Top Part 2")), "Haikyuu!! To the Top Part 2")

    def test_a_film_or_ova_isnt_the_series(self):
        self.assertEqual(self.best(anime("Haikyuu!! Movie 1: Owari to Hajimari", media_type="movie")),
                         "Haikyuu!! Movie 1: Owari to Hajimari")
        self.assertEqual(self.best(anime("Haikyuu!! Riku vs. Kuu", media_type="ova")), "Haikyuu!! Riku vs. Kuu (OVA)")

    def test_a_tv_shows_season_is_the_episodes(self):
        show = {"title": "Dark", "aliases": [], "source": "tmdb", "format": "series", "media_type": "", "type": "tv"}
        found = [{"url": f"https://site/{i}", "name": n, "names": [n]} for i, n in enumerate(["Dark Season 1", "Dark Season 2", "Darker"])]
        self.assertEqual(scrape.best_entry(found, scrape.wanted_of(show, 2))[0]["name"], "Dark Season 2")
        self.assertEqual(scrape.best_entry(found, scrape.wanted_of(show, 1))[0]["name"], "Dark Season 1")

    def test_closest_when_nothing_fits_exactly(self):
        found = [{"url": "https://site/a", "name": "Frieren Beyond Journeys End", "names": ["Frieren Beyond Journeys End"]},
                 {"url": "https://site/b", "name": "Naruto", "names": ["Naruto"]}]
        best = scrape.best_entry(found, scrape.wanted_of(anime("Sousou no Frieren", ["Frieren: Beyond Journey's End"])))
        self.assertEqual(best[0]["url"], "https://site/a")


class PageTest(unittest.TestCase):
    def test_results_page_links(self):
        html = """<nav><a href="/">Home</a><a href="/genre/action">Action</a></nav>
          <div class="item"><a href="/anime/haikyuu-3x7"><img alt="Haikyuu!!"></a><a href="/anime/haikyuu-3x7">Haikyuu!!</a></div>
          <div class="item"><a href="/anime/haikyuu-2nd-season" title="Haikyuu!! Second Season">Haikyuu 2</a></div>
          <a href="https://ads.example.com/x">Haikyuu promo</a><a href="/search?q=haikyuu&page=2">2</a>"""
        page = scrape.parse(html, "https://site.test/search?q=haikyuu")
        found = scrape.candidates(page, "https://site.test/search?q=haikyuu")
        self.assertEqual({c["url"] for c in found}, {"https://site.test/anime/haikyuu-3x7", "https://site.test/anime/haikyuu-2nd-season"})
        second = next(c for c in found if c["url"].endswith("2nd-season"))
        self.assertIn("Haikyuu!! Second Season", second["names"])

    def test_episode_links(self):
        html = """<ul><li><a href="/watch/show-episode-1">Episode 1</a></li><li><a href="/watch/show-episode-2">Ep 2 - The Rival</a></li>
          <li><a href="/watch/show?ep=3">3</a></li><li><a href="/w/x" data-number="4">Watch</a></li>
          <li><a href="/watch/show-s02e05">Next</a></li><li><a href="/tv/dark/season-2/episode-6">Six</a></li></ul>"""
        eps = scrape.episode_links(scrape.parse(html, "https://site.test/anime/show"))
        self.assertEqual(eps[(None, 1)], "https://site.test/watch/show-episode-1")
        self.assertEqual(eps[(None, 2)], "https://site.test/watch/show-episode-2")
        self.assertEqual(eps[(None, 3)], "https://site.test/watch/show?ep=3")
        self.assertEqual(eps[(None, 4)], "https://site.test/w/x")
        self.assertEqual(eps[(2, 6)], "https://site.test/tv/dark/season-2/episode-6")
        self.assertEqual(scrape.pick_episode(eps, None, 2), "https://site.test/watch/show-episode-2")

    def test_videos_on_a_page(self):
        html = """<video><source src="/media/ep1-720p.mp4"></video>
          <script>var player = {"file":"https:\\/\\/cdn.site.test\\/hls\\/ep1\\/master.m3u8","poster":"https:\\/\\/cdn.site.test\\/thumb.jpg"};</script>
          <script>jwplayer().setup({sources:[{file:'https://cdn.site.test/ep1-1080p.mp4'}]});</script>
          <a href="https://cdn.site.test/trailer.mp4">trailer</a>"""
        page = scrape.parse(html, "https://site.test/watch/ep1")
        found = scrape.videos_in(page, html)
        self.assertEqual(found[0], "https://cdn.site.test/hls/ep1/master.m3u8")
        self.assertIn("https://site.test/media/ep1-720p.mp4", found)
        self.assertIn("https://cdn.site.test/ep1-1080p.mp4", found)
        self.assertNotIn("https://cdn.site.test/trailer.mp4", found)
        self.assertLess(found.index("https://cdn.site.test/ep1-1080p.mp4"), found.index("https://site.test/media/ep1-720p.mp4"))


class _Site(http.server.BaseHTTPRequestHandler):
    """A tiny video site: search, a title's page with its episodes, an episode page
    that embeds a player, and the player's HLS playlist."""
    pages = {}
    hits = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        self.hits.append(self.path)
        body = self.pages.get(self.path.split("?")[0] if not self.path.startswith("/search") else "/search")
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        kind = "application/vnd.apple.mpegurl" if self.path.endswith(".m3u8") else "text/html"
        data = body(self) if callable(body) else body
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data.encode())))
        self.end_headers()
        self.wfile.write(data.encode())


class ScraperTest(unittest.TestCase):
    def setUp(self):
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Site)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.base = base
        _Site.hits = []
        _Site.pages = {
            "/search": """<a href="/anime/haikyuu-2">Haikyuu!! Second Season</a><a href="/anime/haikyuu">Haikyuu!!</a>
                          <a href="/anime/haikyuu-movie">Haikyuu!! Movie</a>""",
            "/anime/haikyuu": """<h1>Haikyuu!!</h1><a href="/anime/haikyuu/watch">Watch now</a>""",
            "/anime/haikyuu/watch": "".join(f'<a href="/ep/haikyuu-{n}">Episode {n}</a>' for n in range(1, 26)),
            "/ep/haikyuu-3": f'<iframe src="{base}/embed/abc"></iframe>',
            "/embed/abc": """<script>var src = "/videos/abc/master.m3u8".replace('x','x'); player.setup({file: '"""
                          + base + """/videos/abc/master.m3u8'});</script>""",
            "/videos/abc/master.m3u8": "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nlow.m3u8\n",
        }
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.scraper = scrape.Scraper(self.tmp.name)
        self.site = scrape.clean([{"name": "Mine", "url": base + "/search?q={title}"}])[0]

    def test_finds_the_entry_the_episode_and_its_video(self):
        got = self.scraper.resolve(self.site, anime("Haikyuu!!", ["Haikyu!!"]), 1, 3)
        self.assertEqual(got["entry"]["url"], self.base + "/anime/haikyuu")
        self.assertEqual(got["page"], self.base + "/ep/haikyuu-3")
        self.assertEqual(got["url"], self.base + "/videos/abc/master.m3u8")
        self.assertEqual(got["referer"], self.base + "/embed/abc")
        # Remembered: the next time nothing is searched or read again (the video's address is reused for a while).
        _Site.hits.clear()
        again = self.scraper.resolve(self.site, anime("Haikyuu!!", ["Haikyu!!"]), 1, 3)
        self.assertEqual(again["url"], got["url"])
        self.assertEqual(_Site.hits, [])
        self.assertTrue((Path(self.tmp.name) / "scraped.json").exists())

    def test_a_missing_episode_says_so_with_the_page(self):
        with self.assertRaises(scrape.ScrapeError) as e:
            self.scraper.resolve(self.site, anime("Haikyuu!!"), 1, 40)
        self.assertIn("episode 40", str(e.exception))

    def test_no_video_gives_the_page_for_the_browser(self):
        _Site.pages["/ep/haikyuu-4"] = "<div id=player>Loading with JavaScript…</div>"
        with self.assertRaises(scrape.ScrapeError) as e:
            self.scraper.resolve(self.site, anime("Haikyuu!!"), 1, 4)
        self.assertEqual(e.exception.page, self.base + "/ep/haikyuu-4")

    def test_a_site_set_to_the_browser_gives_the_page(self):
        site = dict(self.site, open="browser")
        got = self.scraper.resolve(site, anime("Haikyuu!!"), 1, 3)
        self.assertEqual((got["url"], got["page"]), ("", self.base + "/ep/haikyuu-3"))

    def test_choosing_another_entry_is_remembered(self):
        item = anime("Haikyuu!!")
        self.scraper.resolve(self.site, item, 1, 3)
        _Site.pages["/anime/haikyuu-2"] = '<a href="/ep/h2-3">Episode 3</a>'
        _Site.pages["/ep/h2-3"] = f'<video src="{self.base}/videos/abc/master.m3u8"></video>'
        got = self.scraper.resolve(self.site, item, 1, 3, pin=self.base + "/anime/haikyuu-2")
        self.assertEqual(got["page"], self.base + "/ep/h2-3")
        self.assertEqual(self.scraper.resolve(self.site, item, 1, 3)["entry"]["url"], self.base + "/anime/haikyuu-2")

    def test_a_new_link_for_the_site_starts_afresh(self):
        item = anime("Haikyuu!!")
        self.scraper.resolve(self.site, item, 1, 3)
        moved = dict(self.site, url=self.base + "/find?q={title}")       # same site in Settings, new link
        _Site.pages["/find"] = '<a href="/anime/haikyuu-new">Haikyuu!!</a>'
        _Site.pages["/anime/haikyuu-new"] = '<a href="/ep/new-3">Episode 3</a>'
        _Site.pages["/ep/new-3"] = f'<video src="{self.base}/videos/abc/master.m3u8"></video>'
        _Site.hits.clear()
        got = self.scraper.resolve(moved, item, 1, 3)
        self.assertEqual((got["entry"]["url"], got["page"]), (self.base + "/anime/haikyuu-new", self.base + "/ep/new-3"))
        self.assertTrue(any(h.startswith("/find") for h in _Site.hits))

    def test_settings_check_the_link(self):
        with self.assertRaises(ValueError):
            scrape.clean([{"name": "x", "url": "https://site/search?q=frieren"}])
        [site] = scrape.clean([{"name": "", "url": "https://my.site/s?q={title}", "types": ["anime"], "open": "nope"}])
        self.assertEqual((site["name"], site["types"], site["open"]), ("my.site", ["anime"], "player"))


# ---- the app: Play online (my sites), Play this with, related entries

class PlayScrapedTest(ServerBase):
    def setUp(self):
        super().setUp()
        self.app.settings.update({"video_sites": [{"name": "Mine", "url": "https://mine.test/search?q={title}"}]})
        self.item = self.add("movie", "tmdb", "movie/27205")

    def test_status_lists_your_video_sites(self):
        status, state = self.call("GET", "/api/state")
        self.assertEqual([x["name"] for x in state["status"]["video_sites"]["movie"]], ["Mine"])

    def test_plays_in_the_built_in_player(self):
        found = {"url": "https://cdn.mine.test/inception.m3u8", "referer": "https://mine.test/watch/1", "page": "https://mine.test/watch/1",
                 "entry": {"url": "https://mine.test/inception", "name": "Inception"}, "others": [], "site": "Mine"}
        played = []
        with mock.patch.object(self.app.scraper, "resolve", return_value=found), \
                mock.patch.object(player, "find_mpv", return_value="mpv"), \
                mock.patch.object(self.app.player, "play", side_effect=lambda s: played.append(s) or s):
            status, res = self.call("POST", f"/api/items/{self.item['id']}/scraped", {})
        self.assertEqual(status, 200, res)
        self.assertEqual(res["player"], "mpv")
        session = played[0]
        self.assertEqual(session.entries[0]["path"], found["url"])
        self.assertIn("--referrer=https://mine.test/watch/1", session.extra_args)
        self.assertEqual(session.entries[0]["party"]["url"], found["url"])

    def test_without_a_video_the_page_opens_in_the_browser(self):
        err = scrape.ScrapeError("no video", page="https://mine.test/watch/1")
        with mock.patch.object(self.app.scraper, "resolve", side_effect=err):
            status, res = self.call("POST", f"/api/items/{self.item['id']}/scraped", {})
            self.assertEqual((status, res["ok"], res["page"]), (200, False, "https://mine.test/watch/1"))
            status, res = self.call("POST", f"/api/items/{self.item['id']}/scraped", {"open_page": True})
        self.assertEqual(res["player"], "browser")
        self.assertIn("https://mine.test/watch/1", self.opened)

    def test_play_this_with_is_remembered(self):
        status, item = self.call("PUT", f"/api/items/{self.item['id']}", {"play_with": "scraped"})
        self.assertEqual(item["play_with"], "scraped")
        status, _ = self.call("PUT", f"/api/items/{self.item['id']}", {"play_with": "nonsense"})
        self.assertEqual(status, 400)
        self.assertEqual(self.app.store.get(self.item["id"])["play_with"], "scraped")

    def test_related_entries_by_name(self):
        rows = [{"source": "tmdb", "source_id": "movie/27205", "title": "Inception", "type": "movie"},
                {"source": "tmdb", "source_id": "movie/1", "title": "Inception: The Cobol Job", "type": "movie"},
                {"source": "tmdb", "source_id": "movie/2", "title": "Something Else", "type": "movie"}]
        with mock.patch("watchlist.metadata.search", return_value=rows):
            status, res = self.call("GET", f"/api/items/{self.item['id']}/similar")
        self.assertEqual([x["title"] for x in res["entries"]], ["Inception: The Cobol Job"])


# ---- packs: worked out by name straight away

def pack():
    files, k = [], 0
    for folder, name in [("", "Haikyuu!!"), ("Haikyuu!! Second Season/", "Haikyuu!! Second Season"),
                         ("Haikyuu!! To the Top/", "Haikyuu!! To the Top")]:
        for e in range(1, 4):
            files.append({"key": k, "rel": f"Haikyuu!! S01+02+03+04/{folder}{name} - {e:02d}.mkv", "size": 300 << 20})
            k += 1
    files.append({"key": k, "rel": "Haikyuu!! S01+02+03+04/Movies/Haikyuu!! Movie 1 - Owari to Hajimari.mkv", "size": 2 << 30})
    return files


class QuickPackTest(unittest.TestCase):
    def names(self, item):
        files = pack()
        own = packs.quick_own(files, item)
        return None if own is None else sorted(Path(files[k]["rel"]).name for k in own)

    def test_each_title_gets_its_own_files(self):
        self.assertEqual(self.names(anime("Haikyuu!!", ["Haikyu!!"])), [f"Haikyuu!! - 0{e}.mkv" for e in (1, 2, 3)])
        self.assertEqual(self.names(anime("Haikyuu!! Second Season")), [f"Haikyuu!! Second Season - 0{e}.mkv" for e in (1, 2, 3)])
        self.assertEqual(self.names(anime("Haikyuu!! To the Top", ["Haikyu!! 4th Season"])),
                         [f"Haikyuu!! To the Top - 0{e}.mkv" for e in (1, 2, 3)])

    def test_one_title_torrents_are_left_alone(self):
        files = [{"key": e, "rel": f"Frieren - {e:02d}.mkv", "size": 300 << 20} for e in range(1, 5)]
        self.assertIsNone(packs.quick_own(files, anime("Sousou no Frieren")))

    def test_season_folders(self):
        files = [{"key": k, "rel": f"Show/Season {s}/Show - {e:02d}.mkv", "size": 1} for k, (s, e) in
                 enumerate((s, e) for s in (1, 2) for e in (1, 2))]
        own = packs.quick_own(files, anime("Show Season 2"))
        self.assertEqual(sorted(own), [2, 3])

    def test_side_stories_and_named_seasons_in_the_chain(self):
        ents = [{"source_id": "1", "title": "Show", "names": ["Show"], "kind": "tv", "media_type": "tv", "episodes": 12, "relation": "This title"},
                {"source_id": "9", "title": "Show Shorts", "names": ["Show Shorts"], "kind": "tv", "media_type": "ona", "episodes": 6, "relation": "Side Story"},
                {"source_id": "5", "title": "Show: Recap", "names": ["Show: Recap"], "kind": "tv", "media_type": "tv", "episodes": 1, "relation": "Summary"},
                {"source_id": "2", "title": "Show Second Season", "names": ["Show Second Season", "Show 2nd Season"], "kind": "tv",
                 "media_type": "tv", "episodes": 12, "relation": "Sequel"}]
        groups = [{"id": "tv:2", "kind": "tv", "season": 2, "label": "Season 2", "title": "show", "distinct": False,
                   "files": [1], "episodes": {1: 1}, "sample": ["Show S2 - 01.mkv"]}]
        plan = packs.rule_plan(groups, ents, "1")
        self.assertEqual(plan["tv:2"]["parts"][1], ("2", 1))


class TorrentQuickOwnerTest(unittest.TestCase):
    def test_files_of_other_seasons_arent_this_titles_before_the_plan(self):
        app = mock.Mock()
        app.store.get.return_value = anime("Haikyuu!!", id="item1")
        t = torrents_mod.Torrents.__new__(torrents_mod.Torrents)
        t.app = app
        rec = {"item_id": "item1", "files": [{"index": f["key"], "path": f["rel"], "size": f["size"]} for f in pack()],
               "overrides": {}, "plan": {"status": "working"}}
        t._quick(rec)
        owners = {f["index"]: t._owner(rec, f)[0] for f in rec["files"]}
        self.assertEqual([i for i, o in owners.items() if o == "item1"], [0, 1, 2])
        rec["plan"] = {"status": "single"}            # worked out: it's all one title after all
        self.assertTrue(all(t._owner(rec, f)[0] == "item1" for f in rec["files"]))


# ---- old torrent files

class CleanOldTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.folder = root / "downloads" / torrents_mod.DIR_NAME
        (self.folder / "Show").mkdir(parents=True)
        app = mock.Mock()
        app.settings.get.return_value = {"downloads_folder": str(root / "downloads")}
        app.player.session = None
        app.store.get.side_effect = KeyError
        with mock.patch.object(torrents_mod, "lt", None):
            self.t = torrents_mod.Torrents(app, root / "data")
        for name in ("Show - 01.mkv", "Show - 02.mkv"):
            (self.folder / "Show" / name).write_bytes(b"x")
        (self.folder / "leftover.mkv").write_bytes(b"x")
        self.t.records = {"h1": {"hash": "h1", "item_id": "kept", "save_path": str(self.folder), "files": [
            {"index": 0, "path": "Show/Show - 01.mkv", "size": 1}], "overrides": {}, "priorities": {}, "finished": {}},
            "h2": {"hash": "h2", "item_id": "other", "save_path": str(self.folder), "files": [
                {"index": 0, "path": "Show/Show - 02.mkv", "size": 1}], "overrides": {}, "priorities": {}, "finished": {}}}
        self.t.keep = {"kept": True}
        self.deleted = []
        self.t.delete_file = lambda h, i: self.deleted.append((h, i))

    def test_only_files_older_than_48_hours_and_not_kept(self):
        now = time.time()
        self.assertEqual(self.t.clean_old(now), 0)                  # first seen now: nothing is old yet
        self.assertEqual(self.t.clean_old(now + 47 * 3600), 0)
        self.assertEqual(self.t.clean_old(now + 49 * 3600), 2)
        self.assertEqual(self.deleted, [("h2", 0)])                 # the kept show's file stays
        self.assertFalse((self.folder / "leftover.mkv").exists())  # a file of no torrent goes too

    def test_a_file_played_recently_isnt_old(self):
        now = time.time()
        self.t.clean_old(now)
        self.t.records["h2"]["used"] = {"0": now + 40 * 3600}
        self.t.clean_old(now + 49 * 3600)
        self.assertEqual(self.deleted, [])


# ---- settings

class SettingsTest(unittest.TestCase):
    def test_the_built_in_browser_is_the_default_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text('{"browser_path": "", "region": "US"}', encoding="utf-8")
            s = Settings(path)
            self.assertEqual(s.get()["browser_path"], "builtin")
            s.update({"browser_path": ""})                         # chosen afterwards: it sticks
            self.assertEqual(Settings(path).get()["browser_path"], "")
            self.assertEqual(Settings(Path(tmp) / "new.json").get()["browser_path"], "builtin")

    def test_sounds_and_setup_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Settings(Path(tmp) / "settings.json")
            self.assertTrue(s.get()["sounds"] and s.get()["setup_prompt"])
            s.update({"sounds": False, "setup_prompt": False})
            got = Settings(Path(tmp) / "settings.json").get()
            self.assertFalse(got["sounds"] or got["setup_prompt"])

    def test_player_controls_hide_after_5_seconds(self):
        conf = (Path(player.__file__).parent / "mpv_config" / "script-opts" / "uosc.conf").read_text(encoding="utf-8")
        self.assertIn("autohide=yes", conf)
        with mock.patch.object(Path, "is_file", return_value=True):     # (uosc is fetched by the build)
            args = player.ui_args("violet")
        self.assertIn("--cursor-autohide=5000", args)

# ---- the ad blocker, unpacking, the hidden browser and the site's test

class AdBlockTest(unittest.TestCase):
    def test_ad_servers_and_addresses(self):
        for url in ("https://pagead2.googlesyndication.com/x.js", "https://www.doubleclick.net/a", "https://cdn.popads.net/pop.js",
                    "https://my-anime.test/js/popunder.min.js", "https://player.test/vast.xml?id=1"):
            self.assertTrue(webtools.blocked(url), url)
        for url in ("https://my-anime.test/watch/ep-1", "https://cdn.host.test/hls/master.m3u8", "https://adventure-time.test/"):
            self.assertFalse(webtools.blocked(url), url)

    def test_the_apps_own_windows_arent_touched(self):
        webtools._by_window.clear()
        own = mock.Mock(uid="a", real_url="http://127.0.0.1:8765/#/party-window")
        site = mock.Mock(uid="b", real_url="https://www.crunchyroll.com/")
        with mock.patch.object(webtools, "ADBLOCK", True):
            self.assertFalse(webtools._options(own)["adblock"])
            self.assertTrue(webtools._options(site)["adblock"])
        with mock.patch.object(webtools, "ADBLOCK", False):
            self.assertFalse(webtools._options(mock.Mock(uid="c", real_url="https://x.test/"))["adblock"])

    def test_switch_in_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Settings(Path(tmp) / "settings.json")
            self.assertTrue(s.get()["adblock"])
            s.update({"adblock": False})
            self.assertFalse(Settings(Path(tmp) / "settings.json").get()["adblock"])


class UnpackTest(unittest.TestCase):
    # A script packed with the usual "eval(function(p,a,c,k,e,d)" packer.
    PACKED = ("eval(function(p,a,c,k,e,d){e=function(c){return c.toString(36)};if(!''.replace(/^/,String)){while(c--){d[c.toString(a)]"
              "=k[c]||c.toString(a)}k=[function(e){return d[e]}];e=function(){return'\\w+'};c=1};while(c--){if(k[c]){p=p.replace("
              "new RegExp('\\b'+e(c)+'\\b','g'),k[c])}}return p}('0 1={2:\"3://4.5/6/7.8\"};',9,9,"
              "'var|player|file|https|cdn|test|hls|master|m3u8'.split('|'),0,{}))")

    def test_packed_and_base64_players(self):
        import base64
        self.assertIn("https://cdn.test/hls/master.m3u8", scrape.unpack(self.PACKED))
        hidden = base64.b64encode(b'{"file":"https://cdn.test/v/1080p.mp4"}').decode()
        html = f'<script>{self.PACKED}</script><script>var s = atob("{hidden}")</script>'
        self.assertEqual(scrape.videos_in(scrape.parse(html, "https://x.test/"), html),
                         ["https://cdn.test/hls/master.m3u8", "https://cdn.test/v/1080p.mp4"])


class BrowserFallbackTest(ScraperTest):
    def test_a_player_built_with_javascript_is_caught_in_the_hidden_browser(self):
        _Site.pages["/ep/haikyuu-5"] = "<div id=player></div><script src=/player.js></script>"
        caught = []
        self.scraper.catch = lambda url: caught.append(url) or {
            "url": "https://cdn.test/hls/x.m3u8", "headers": {"Referer": "https://embed.test/e/1", "Origin": "https://embed.test"}}
        got = self.scraper.resolve(self.site, anime("Haikyuu!!"), 1, 5)
        self.assertEqual(caught, [self.base + "/ep/haikyuu-5"])
        self.assertEqual((got["url"], got["referer"], got["headers"]["Origin"]),
                         ("https://cdn.test/hls/x.m3u8", "https://embed.test/e/1", "https://embed.test"))
        # The site's videos need the browser: next time it goes straight there.
        self.assertTrue(self.scraper.memory["_sites"][self.site["url"]]["browser"])

    def test_an_episode_list_built_with_javascript_is_read_after_it_ran(self):
        _Site.pages["/anime/haikyuu/watch"] = "<div id=eps></div><script>load()</script>"
        rendered = '<a href="/ep/haikyuu-3">Episode 3</a>'
        self.scraper.render = lambda url: (rendered, url)
        got = self.scraper.resolve(self.site, anime("Haikyuu!!"), 1, 3)
        self.assertEqual(got["page"], self.base + "/ep/haikyuu-3")

    def test_without_the_hidden_browser_it_says_so(self):
        _Site.pages["/ep/haikyuu-5"] = "<div id=player></div>"
        self.scraper.catch = lambda url: None
        self.scraper.can_catch = lambda: False
        with self.assertRaises(scrape.ScrapeError) as e:
            self.scraper.resolve(self.site, anime("Haikyuu!!"), 1, 5)
        self.assertIn("no video", str(e.exception))

    def test_the_sites_test_shows_each_step_and_keeps_your_choice(self):
        item = anime("Haikyuu!!")
        self.scraper.resolve(self.site, item, 1, 3, pin=self.base + "/anime/haikyuu")
        r = self.scraper.diagnose(self.site, item, 1, 3)
        self.assertTrue(r["ok"], r)
        self.assertTrue(any("episode link" in x for x in r["steps"]))
        self.assertTrue(any("Found the video" in x for x in r["steps"]))
        self.assertTrue(self.scraper.remembered("x1", self.site)["entries"][""]["pinned"])


class SkippedSiteTest(PlayScrapedTest):
    def test_a_site_you_skipped_doesnt_start_playing(self):
        found = {"url": "https://cdn.mine.test/a.m3u8", "referer": "", "page": "https://mine.test/1", "headers": {},
                 "entry": {"url": "https://mine.test/a", "name": "Inception"}, "others": [], "site": "Mine"}

        def slow(*a, **k):
            self.app._scrape_runs[self.item["id"]] = "newer"      # another site was asked for meanwhile
            return found
        with mock.patch.object(self.app.scraper, "resolve", side_effect=slow),                 mock.patch.object(self.app.player, "play") as play:
            status, res = self.call("POST", f"/api/items/{self.item['id']}/scraped", {"run": "older"})
        self.assertTrue(res.get("superseded"))
        play.assert_not_called()

    def test_the_diagnose_endpoint(self):
        with mock.patch.object(self.app.scraper, "diagnose", return_value={"ok": False, "steps": ["a"], "url": "", "page": "", "error": "x"}) as d:
            status, res = self.call("POST", "/api/video-sites/diagnose", {"site": {"name": "Mine", "url": "https://m.test/s?q={title}"},
                                                                          "item_id": self.item["id"]})
        self.assertEqual((status, res["steps"]), (200, ["a"]))
        self.assertIsNone(d.call_args[0][2])                       # a film: no season or episode


if __name__ == "__main__":
    unittest.main()
