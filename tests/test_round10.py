"""Round 10: playing from your own video sites (the scraper), "Play this with",
packs worked out by name straight away (another season's episode never plays
as this one's), old torrent files cleaned up, related entries by name, and
the settings that came with them."""

import http.server
import json
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

    def test_episode_index_beats_a_few_mirror_links(self):
        # Mirrors for a handful of episodes often precede a full title-page
        # episode index. The index, not the first mirror, must drive playback.
        html = "".join(f'<a href="https://mirror{n}.test/watch?episode={n}">Episode {n}</a>' for n in range(1, 7))
        html += "".join(f'<a href="/watch/show-episode-{n}">Episode {n}</a>' for n in range(1, 25))
        eps = scrape.episode_links(scrape.parse(html, "https://site.test/anime/show"))
        self.assertEqual(len(eps), 24)
        self.assertEqual(eps[(None, 24)], "https://site.test/watch/show-episode-24")

    def test_anikoto_xhr_episode_index_uses_data_numbers_not_server_mirrors(self):
        listing = "".join(f'<a href="#" data-id="{9000 + n}" data-num="{n}"><b>{n}</b></a>' for n in range(1, 25))
        eps = scrape._anikoto_episode_links("https://anikototv.to/watch/show/ep-1", listing)
        self.assertEqual(len(eps), 24)
        self.assertEqual(eps[(None, 24)], "https://anikototv.to/watch/show/ep-24")

    def test_cookie_from_browser_capture_is_preserved_for_the_player(self):
        # Some hosts bind a playlist to the browser session that opened it.
        page = "https://site.test/watch/episode-1"
        scraper = scrape.Scraper(tempfile.mkdtemp())
        scraper.catch = mock.Mock(return_value={"url": "https://cdn.test/master.m3u8", "headers": {
            "Referer": page, "Cookie": "session=abc", "User-Agent": "browser",
        }})
        got = scraper._catch_stream(page, None)
        self.assertEqual(got["headers"]["Cookie"], "session=abc")

    def test_ra_catches_autoplay_before_ambiguous_mirror_buttons(self):
        scraper = scrape.Scraper(tempfile.mkdtemp())
        scraper.catch = mock.Mock(return_value={"url": "https://cdn.test/master.m3u8", "headers": {}})
        [site] = scrape.clean([{"name": "RA", "url": "https://site.test/search?q={title}", "ra": True}])
        item = anime("A show", id="ra")
        with mock.patch.object(scraper, "episode_page", return_value=("https://site.test/watch/ep-1", {"url": "https://site.test/title"}, [])):
            got = scraper.resolve(site, item, 1, 1)
        self.assertEqual(got["url"], "https://cdn.test/master.m3u8")
        scraper.catch.assert_called_once_with("https://site.test/watch/ep-1", click=None)

    def test_anikoto_sub_and_dub_servers_come_from_its_server_list(self):
        page = "https://anikototv.to/watch/show-x1/ep-2"
        html = ('<div id="watch-main" class="watch" data-id="77"></div><div id="w-servers"></div>'
                '<input type="hidden" id="report-server-id" value=""><input name="server_name" id="w-report-server-label">')
        listing = json.dumps({"result": '<a href="#" data-id="5" data-num="1" data-ids="AAA"></a>'
                                        '<a href="#" data-id="6" data-num="2" data-ids="BBB"></a>'})
        servers = json.dumps({"result": '<div class="servers"><div class="type" data-type="sub"><ul>'
                                        '<li data-sv-id="1" data-link-id="s1">HD-1</li></ul></div>'
                                        '<div class="type" data-type="dub"><ul><li data-sv-id="1" data-link-id="d1">HD-1</li>'
                                        '<li data-sv-id="2" data-link-id="d2">Vidstream-1</li></ul></div></div>'})
        calls = []

        def fake(url, referer="", xhr=False, **kw):
            calls.append(url)
            return (listing if "/episode/list/77" in url else servers), url
        with mock.patch.object(scrape, "fetch", side_effect=fake):
            html += scrape._anikoto_servers(page, html)
        self.assertTrue(calls[1].endswith("/ajax/server/list?servers=BBB"))
        found = scrape.streams(scrape.parse(html, page))
        self.assertEqual([(x["label"], x["kind"], x["how"]) for x in found],
                         [("HD-1", "sub", "click"), ("HD-1", "dub", "click"), ("Vidstream-1", "dub", "click")])

    def test_episode_numbers_titled_with_their_tracks_are_still_episodes(self):
        html = "".join(f'<a href="/watch/show?ep={n}" title="{n} Sub · Dub">{n}</a>' for n in range(1, 6))
        html += '<a href="/watch/show?ep=1&server=2" title="Sub">HD-1</a>'
        eps = scrape.episode_links(scrape.parse(html, "https://reanime.to/watch/show"))
        self.assertEqual(eps[(None, 5)], "https://reanime.to/watch/show?ep=5")
        self.assertEqual(eps[(None, 1)], "https://reanime.to/watch/show?ep=1")

    def test_a_dub_on_a_site_whose_address_picks_the_track_is_autoplayed(self):
        scraper = scrape.Scraper(tempfile.mkdtemp())
        scraper.catch = mock.Mock(return_value={"url": "https://cdn.test/dub.m3u8", "headers": {}})
        [site] = scrape.clean([{"name": "ReAnime", "url": "https://reanime.to/search?q={title}", "ra": True}])
        page = "https://reanime.to/watch/show?ep=2"
        with mock.patch.object(scraper, "episode_page", return_value=(page, {"url": "https://reanime.to/anime/show"}, [])):
            got = scraper.resolve(site, anime("A show", id="rean"), 1, 2, stream="x:dub:")
        scraper.catch.assert_called_once_with("https://reanime.to/watch/show?ep=2&lang=dub", click=None)
        self.assertEqual(got["stream"]["kind"], "dub")

    def test_search_results_drawn_by_scripts_beat_the_sites_menus(self):
        scraper = scrape.Scraper(tempfile.mkdtemp())
        [site] = scrape.clean([{"name": "Miruro", "url": "https://www.miruro.test/search?query={title}"}])
        plain = '<a href="/trending">Trending anime</a><a href="/schedule">Airing schedule</a>'
        drawn = plain + '<a href="/watch/AbC123/naruto-shippuden">Naruto: Shippuden</a>'
        scraper.render = lambda url: (drawn, url)
        with mock.patch.object(scrape, "fetch", return_value=(plain, "https://www.miruro.test/search?query=x")):
            found = scraper.search(site, anime("Naruto Shippuden"))
        self.assertEqual(found[0]["url"], "https://www.miruro.test/watch/AbC123/naruto-shippuden")
        self.assertFalse(any("/trending" in c["url"] for c in found))

    def test_tiles_with_data_href_and_episodes_past_the_ones_shown(self):
        html = '<div class="eps">' + "".join(f'<div role="link" data-href="/anime/X/p-{n}-sub"><span>Ep.</span> <span>{n}</span></div>'
                                            for n in range(1, 51)) + '</div><a href="/manga/Y">Naruto Manga</a>'
        page = scrape.parse(html, "https://mkissa.test/anime/X")
        eps = scrape.episode_links(page)
        self.assertEqual(eps[(None, 7)], "https://mkissa.test/anime/X/p-7-sub")
        self.assertEqual(scrape.derive_episode(eps, 120), "https://mkissa.test/anime/X/p-120-sub")
        self.assertEqual(scrape._track_url("https://mkissa.test/anime/X/p-120-sub", "dub"), "https://mkissa.test/anime/X/p-120-dub")
        self.assertFalse(any("/manga/" in c["url"] for c in scrape.candidates(page, "https://mkissa.test/search")))
        self.assertEqual(scrape._with_ep("https://ani.test/anime/naruto-1498", 3), "https://ani.test/anime/naruto-1498?ep=3")

    def test_a_dub_without_a_dub_button_clicks_the_players_switch(self):
        scraper = scrape.Scraper(tempfile.mkdtemp())
        scraper.catch = mock.Mock(return_value={"url": "https://cdn.test/dub.m3u8", "headers": {}})
        [site] = scrape.clean([{"name": "Miruro", "url": "https://www.miruro.test/search?query={title}"}])
        page = "https://www.miruro.test/watch/AbC/naruto?ep=1"
        with mock.patch.object(scraper, "episode_page", return_value=(page, {"url": page}, [])),                 mock.patch.object(scrape, "fetch", return_value=("<div id=player></div>", page)),                 mock.patch.object(scrape, "scrambled_playlist", return_value=False):
            got = scraper.resolve(site, anime("Naruto", id="mir"), 1, 1, track="dub")
        scraper.catch.assert_called_once_with(page, click={"label": "Dub", "kind": "dub"})
        self.assertEqual(got["stream"]["kind"], "dub")
        # (the choice you made before is kept: track= finds a version without changing it)
        self.assertNotEqual(scraper.memory["_sites"][site["url"]].get("kind"), "dub")

    def test_other_versions_and_next_episodes_go_into_the_open_player(self):
        sess = player.Session(["mpv"], "x", [{"path": "https://cdn.test/sub-1.m3u8", "key": "1:1", "label": "E1"}])
        sent = []
        sess.send = lambda *cmds: sent.extend(cmds) or True
        first = sess.current()
        sess.add_tracks(first, [{"kind": "audio", "url": "https://cdn.test/dub-1.m3u8", "title": "English dub", "lang": "en"}])
        self.assertEqual(sent, [])                        # (not open yet: added when it is)
        sess._loaded()
        self.assertIn(["audio-add", "https://cdn.test/dub-1.m3u8", "auto", "English dub", "en"], sent)
        nxt = sess.append({"path": "https://cdn.test/sub-2.m3u8", "key": "1:2", "label": "E2", "sub": "https://cdn.test/2.vtt"})
        self.assertIn(["loadfile", "https://cdn.test/sub-2.m3u8", "append"], sent)
        sent.clear()
        sess.index, sess.loaded = 1, False
        sess._loaded()
        self.assertEqual(sess.current(), nxt)
        self.assertIn(["set_property", "force-media-title", "E2"], sent)
        self.assertIn(["sub-add", "https://cdn.test/2.vtt", "select"], sent)

    def test_a_scrambled_playlist_is_not_handed_to_the_player(self):
        def answer(body):
            r = mock.MagicMock()
            r.__enter__.return_value.read.return_value = body
            return r
        with mock.patch("urllib.request.urlopen", return_value=answer(b"Ub6MLrtlMWmNFvQTidhpN77VPiRy")):
            self.assertTrue(scrape.scrambled_playlist("https://cdn.test/master.m3u8"))
        with mock.patch("urllib.request.urlopen", return_value=answer(b"\xef\xbb\xbf#EXTM3U\n#EXT-X-VERSION:3")):
            self.assertFalse(scrape.scrambled_playlist("https://cdn.test/master.m3u8"))
        self.assertFalse(scrape.scrambled_playlist("https://cdn.test/video.mp4"))
        scraper = scrape.Scraper(tempfile.mkdtemp())
        scraper.catch = mock.Mock(return_value={"url": "https://cdn.test/master.m3u8", "headers": {}})
        with mock.patch.object(scrape, "scrambled_playlist", return_value=True):
            self.assertIsNone(scraper._catch_stream("https://site.test/watch/ep-1", None))

    def test_a_caught_players_subtitle_goes_to_the_player(self):
        scraper = scrape.Scraper(tempfile.mkdtemp())
        scraper.catch = mock.Mock(return_value={"url": "https://cdn.test/master.m3u8", "headers": {},
                                                "subs": ["https://cdn.test/subs/en.vtt"]})
        [site] = scrape.clean([{"name": "RA", "url": "https://site.test/search?q={title}", "ra": True}])
        with mock.patch.object(scraper, "episode_page", return_value=("https://site.test/watch/ep-1", {"url": "https://site.test/title"}, [])):
            got = scraper.resolve(site, anime("A show", id="ra-subs"), 1, 1)
        self.assertEqual(got["subtitle"], "https://cdn.test/subs/en.vtt")

    def test_ra_dub_choice_clicks_the_dub_button_not_autoplay(self):
        scraper = scrape.Scraper(tempfile.mkdtemp())
        scraper.catch = mock.Mock(return_value={"url": "https://cdn.test/dub.m3u8", "headers": {}})
        [site] = scrape.clean([{"name": "RA", "url": "https://site.test/search?q={title}", "ra": True}])
        item = anime("A show", id="ra-dub")
        html = '<div data-type="sub"><a data-server-id="1">HD-1</a></div><div data-type="dub"><a data-server-id="2">HD-1</a></div>'
        with mock.patch.object(scraper, "episode_page", return_value=("https://site.test/watch/ep-1", {"url": "https://site.test/title"}, [])), \
                mock.patch.object(scrape, "fetch", return_value=(html, "https://site.test/watch/ep-1")):
            got = scraper.resolve(site, item, 1, 1, stream="x:dub:HD-1")
        self.assertEqual(got["url"], "https://cdn.test/dub.m3u8")
        self.assertEqual(got["stream"]["kind"], "dub")
        scraper.catch.assert_called_once_with("https://site.test/watch/ep-1", click={"label": "HD-1", "kind": "dub"})

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


class AnimePaheApiTest(unittest.TestCase):
    """The AnimePahe preset is an API/session flow, not an HTML search page."""

    def setUp(self):
        [self.site] = scrape.clean([{
            "name": "AnimePahe", "url": "https://animepahe.pw/api?m=search&q={title}",
            "types": ["anime"],
        }])

    def test_an_api_search_link_is_recognised_without_any_options(self):
        self.assertEqual(self.site["kind"], "json_api")
        self.assertNotIn("ra", self.site)
        self.assertEqual(scrape.search_url(self.site, "Frieren: Beyond Journey's End"),
                         "https://animepahe.pw/api?m=search&q=Frieren%3A+Beyond+Journey%27s+End")

    def test_session_search_and_release_list_make_play_pages(self):
        def payload(url, origin="", renderer=None):
            if "m=search" in url:
                return {"data": [{"session": "anime-session", "title": "Frieren: Beyond Journey's End", "year": 2023}]}
            return {"data": [
                {"episode": "1", "session": "episode-one"},
                {"episode": 2, "session": "episode-two"},
            ], "last_page": 1}

        with mock.patch.object(scrape, "_json_payload", side_effect=payload):
            found = scrape.json_api_search(self.site, ["Frieren"])
            episodes = scrape.json_api_episodes(self.site, "anime-session")

        self.assertEqual(found[0]["url"], "https://animepahe.pw/play/anime-session")
        self.assertEqual([x["url"] for x in episodes], [
            "https://animepahe.pw/play/anime-session/episode-one",
            "https://animepahe.pw/play/anime-session/episode-two",
        ])

    def test_the_hidden_browser_is_tried_first_and_reading_the_page_without_it(self):
        scraper = scrape.Scraper(tempfile.mkdtemp())
        scraper.catch = mock.Mock(return_value={"url": "https://cdn.test/episode.m3u8", "headers": {}})
        page = "https://animepahe.pw/play/anime-session/episode-one"
        entry = {"url": "https://animepahe.pw/play/anime-session"}

        caught = scraper._stream_video(self.site, page, entry, {}, None, "", page)
        self.assertEqual(caught["url"], "https://cdn.test/episode.m3u8")
        scraper.catch.assert_called_once_with(page, click=None)

        scraper.can_catch = lambda: False
        with mock.patch.object(scrape, "find_video", return_value={"url": "https://cdn.test/plain.m3u8", "referer": page}) as read:
            resolved = scraper._stream_video(self.site, page, entry, {}, None, "", page)
        self.assertEqual(resolved["url"], "https://cdn.test/plain.m3u8")
        read.assert_called_once_with(page, referer=entry["url"], log=scraper.log)

    def test_cloudflare_response_is_not_reported_as_a_missing_series(self):
        with mock.patch.object(scrape, "fetch", return_value=("<html>Just a moment... Cloudflare</html>", "https://animepahe.pw/api")):
            with self.assertRaisesRegex(scrape.ScrapeError, "Cloudflare browser check"):
                scrape.json_api_search(self.site, ["Frieren"])


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
        self.scraper.ytdlp = lambda url, referer="": None        # (yt-dlp has tests of its own)
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
                mock.patch.object(self.app.player, "play", side_effect=lambda s: played.append(s) or setattr(s, "pos", 3.0) or s):
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
        self.scraper.catch = lambda url, click=None: caught.append(url) or {
            "url": "https://cdn.test/hls/x.m3u8", "headers": {"Referer": "https://embed.test/e/1", "Origin": "https://embed.test"}}
        got = self.scraper.resolve(self.site, anime("Haikyuu!!"), 1, 5)
        self.assertEqual(caught, [self.base + "/ep/haikyuu-5"])
        self.assertEqual((got["url"], got["referer"], got["headers"]["Origin"]),
                         ("https://cdn.test/hls/x.m3u8", "https://embed.test/e/1", "https://embed.test"))

    def test_an_episode_list_built_with_javascript_is_read_after_it_ran(self):
        _Site.pages["/anime/haikyuu/watch"] = "<div id=eps></div><script>load()</script>"
        rendered = '<a href="/ep/haikyuu-3">Episode 3</a>'
        self.scraper.render = lambda url: (rendered, url)
        got = self.scraper.resolve(self.site, anime("Haikyuu!!"), 1, 3)
        self.assertEqual(got["page"], self.base + "/ep/haikyuu-3")

    def test_without_the_hidden_browser_it_says_so(self):
        _Site.pages["/ep/haikyuu-5"] = "<div id=player></div>"
        self.scraper.catch = lambda url, click=None: None
        self.scraper.can_catch = lambda: False
        with self.assertRaises(scrape.ScrapeError) as e:
            self.scraper.resolve(self.site, anime("Haikyuu!!"), 1, 5)
        self.assertIn("neither by reading the page nor by yt-dlp", str(e.exception))

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


# ---- like ani-cli: the episodes, each episode's streams (sub / dub / servers) and qualities

SERVERS_PAGE = """<div class="episodes-list"><a href="/watch/x/ep-1">1</a><a href="/watch/x/ep-2">2</a><a href="/watch/x/ep-3">3</a></div>
<div class="servers"><div class="server-group" data-type="sub">SUB:
  <a class="server-item" data-server-id="4" href="/watch/x/ep-1?server=4">HD-1</a>
  <a class="server-item" data-server-id="1" href="/watch/x/ep-1?server=1">HD-2</a>
  <a class="server-item" href="/watch/x/ep-1?s=3">3</a></div>
 <div class="server-group" data-type="dub">DUB: <a class="server-item" data-server-id="4" href="/watch/x/ep-1?server=5">HD-1</a></div></div>
<ul class="mirrors"><li><a href="/m/1">1</a></li><li><a href="/m/2">2</a></li></ul>"""


class StreamsTest(unittest.TestCase):
    def test_stream_buttons_arent_episodes(self):
        page = scrape.parse(SERVERS_PAGE, "https://s.test/watch/x/ep-1")
        self.assertEqual(sorted(scrape.episode_links(page)), [(None, 1), (None, 2), (None, 3)])
        found = scrape.streams(page)
        self.assertEqual([(x["label"], x["kind"]) for x in found], [("HD-1", "sub"), ("HD-2", "sub"), ("3", "sub"), ("HD-1", "dub")])

    def test_a_lone_number_isnt_an_episode(self):
        page = scrape.parse('<a href="/about/2">2</a><a href="/watch/x/episode-7">Next</a>', "https://s.test/x")
        self.assertEqual(scrape.episode_links(page), {})

    def test_your_choice_comes_first(self):
        page = scrape.parse(SERVERS_PAGE, "https://s.test/watch/x/ep-1")
        ordered = scrape.order_streams(scrape.streams(page), {"kind": "dub", "label": "HD-1"})
        self.assertEqual((ordered[0]["label"], ordered[0]["kind"]), ("HD-1", "dub"))
        self.assertEqual(scrape.order_streams(scrape.streams(page), {})[0]["kind"], "sub")

    def test_buttons_without_an_address_are_clicked(self):
        page = scrape.parse('<ul><li class="server-item" data-type="dub" data-server-id="2">Vidstreaming</li></ul>', "https://s.test/ep")
        [x] = scrape.streams(page)
        self.assertEqual((x["label"], x["kind"], x["how"]), ("Vidstreaming", "dub", "click"))

    def test_qualities_of_a_playlist(self):
        master = ("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=854x480\n480/index.m3u8\n"
                  "#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080\n1080/index.m3u8\n"
                  "#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720\n720/index.m3u8\n")
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = master.encode()
        response.geturl.return_value = "https://cdn.test/v/master.m3u8"
        with mock.patch("urllib.request.urlopen", return_value=response):
            qs = scrape.qualities("https://cdn.test/v/master.m3u8", {"Referer": "https://s.test/"})
        self.assertEqual([q["label"] for q in qs], ["1080p", "720p", "480p"])
        self.assertEqual(qs[1]["url"], "https://cdn.test/v/720/index.m3u8")
        self.assertEqual(scrape.pick_quality(qs, "720p")["label"], "720p")
        self.assertEqual(scrape.pick_quality(qs, "900p")["label"], "720p")
        self.assertIsNone(scrape.pick_quality(qs, "auto"))


class YtdlpStepTest(ScraperTest):
    def test_yt_dlp_is_asked_when_reading_the_page_finds_nothing(self):
        _Site.pages["/ep/haikyuu-5"] = '<iframe src="https://host.test/e/abc"></iframe>'
        asked = []
        self.scraper.ytdlp = lambda url, referer="": asked.append((url, referer)) or {
            "url": "https://cdn.host.test/abc/master.m3u8", "referer": "https://host.test/", "headers": {"Origin": "https://host.test"}}
        got = self.scraper.resolve(self.site, anime("Haikyuu!!"), 1, 5)
        self.assertEqual(got["url"], "https://cdn.host.test/abc/master.m3u8")
        self.assertIn(("https://host.test/e/abc", self.base + "/ep/haikyuu-5"), asked)
        self.assertEqual(self.scraper.memory["_sites"][self.site["url"]]["way"], "ytdlp")

    @unittest.skipUnless(scrape.has_ytdlp(), "needs yt-dlp")
    def test_the_real_yt_dlp_reads_a_page(self):
        _Site.pages["/plain"] = f'<html><head><title>Ep</title></head><body><video src="{self.base}/v/ep.mp4"></video></body></html>'
        _Site.pages["/v/ep.mp4"] = "x"
        got = scrape.ytdlp_video(self.base + "/plain", referer=self.base + "/")
        self.assertEqual(got["url"], self.base + "/v/ep.mp4")


class PlayerClosingTest(PlayScrapedTest):
    def test_a_stream_the_player_cant_play_moves_on_to_the_next(self):
        first = {"url": "https://cdn.mine.test/a.m3u8", "referer": "", "page": "https://mine.test/1", "headers": {},
                 "entry": {"url": "https://mine.test/a", "name": "Inception"}, "others": [], "site": "Mine",
                 "streams": [{"id": "0:sub:HD-1", "label": "HD-1", "kind": "sub"}, {"id": "1:sub:HD-2", "label": "HD-2", "kind": "sub"}],
                 "stream": {"id": "0:sub:HD-1", "label": "HD-1", "kind": "sub"}, "qualities": [], "quality": ""}
        second = dict(first, url="https://cdn.mine.test/b.m3u8", stream={"id": "1:sub:HD-2", "label": "HD-2", "kind": "sub"})
        calls = []

        def resolve(*a, **k):
            calls.append(k.get("skip"))
            return first if len(calls) == 1 else second

        def play(session):
            if session.entries[0]["path"].endswith("a.m3u8"):
                session.ended = True                 # closed straight away
            else:
                session.pos = 1.0
            return session
        with mock.patch.object(self.app.scraper, "resolve", side_effect=resolve), \
                mock.patch.object(player, "find_mpv", return_value="mpv"), \
                mock.patch.object(player, "why_it_stopped", return_value="the site refused it (403)"), \
                mock.patch.object(self.app.player, "play", side_effect=play):
            status, res = self.call("POST", f"/api/items/{self.item['id']}/scraped", {})
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["stream"]["label"], "HD-2")
        self.assertEqual(res["tried"], ["HD-1: the site refused it (403)"])
        self.assertEqual(calls[1], ("0:sub:HD-1",))

    def test_why_the_player_stopped(self):
        log = Path(tempfile.gettempdir()) / "watchanything-mpv.log"
        log.write_text("[ffmpeg] https: HTTP error 403 Forbidden\n[cplayer] Failed to open https://x/a.m3u8.\n", encoding="utf-8")
        self.assertIn("403", player.why_it_stopped(None))
        log.write_text("[cplayer] Failed to recognize file format.\n", encoding="utf-8")
        self.assertEqual(player.why_it_stopped(None), "Failed to recognize file format.")


# ---- the Internet Archive: its search, then its file list

ARCHIVE_SEARCH = {"response": {"docs": [
    {"identifier": "NightOfTheLivingDeadTrailer", "title": "Night of the living dead Trailer"},
    {"identifier": "notld_1990", "title": "Night of the Living Dead", "year": "1990"},
    {"identifier": "notld_1968", "title": "Night of the Living Dead", "year": "1968"}]}}
ARCHIVE_FILM = {"files": [
    {"name": "notld_1080p.mkv", "source": "original", "format": "Matroska", "height": "1080", "size": "900"},
    {"name": "notld_1080p.mp4", "source": "derivative", "format": "h.264", "height": "1080", "size": "800"},
    {"name": "notld_512kb.mp4", "source": "derivative", "format": "512Kb MPEG4", "size": "100"},
    {"name": "notld.thumbs/notld_000001.jpg", "format": "Thumbnail"}, {"name": "notld_meta.xml", "format": "Metadata"}]}
ARCHIVE_SERIES = {"files": [
    {"name": "Popeye - 01 - Popeye the Sailor.mp4", "source": "original", "height": "480"},
    {"name": "Popeye - 02 - I Yam What I Yam.mp4", "source": "original", "height": "480"},
    {"name": "Popeye - 02 - I Yam What I Yam_512kb.mp4", "source": "derivative", "format": "512Kb MPEG4"}]}


def archive_answers(url, referer="", **k):
    import json as _json
    if "advancedsearch.php" in url:
        assert "mediatype%3A%28movies%29" in url, url
        return _json.dumps(ARCHIVE_SEARCH), url
    if url.endswith("/metadata/popeye"):
        return _json.dumps(ARCHIVE_SERIES), url
    return _json.dumps(ARCHIVE_FILM), url


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.scraper = scrape.Scraper(self.tmp.name)
        [self.site] = scrape.clean([{"name": "", "url": "", "kind": "archive"}])
        patcher = mock.patch.object(scrape, "fetch", side_effect=archive_answers)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_settings(self):
        self.assertEqual((self.site["name"], self.site["url"], self.site["kind"]), ("Internet Archive", scrape.ARCHIVE, "archive"))

    def test_a_film_in_every_quality(self):
        film = {"id": "f1", "title": "Night of the Living Dead", "aliases": [], "original_title": "", "source": "tmdb",
                "format": "movie", "media_type": "movie", "type": "movie", "year": 1968}
        got = self.scraper.resolve(self.site, film)
        self.assertEqual(got["entry"]["url"], "https://archive.org/details/notld_1968")      # the year picks the original
        self.assertEqual(got["url"], "https://archive.org/download/notld_1968/notld_1080p.mkv")
        self.assertEqual([q["label"] for q in got["qualities"]], ["1080p", "240p"])
        low = self.scraper.resolve(self.site, film, quality="240p")
        self.assertEqual(low["url"], "https://archive.org/download/notld_1968/notld_512kb.mp4")

    def test_a_series_by_its_episodes(self):
        show = {"id": "p1", "title": "Popeye", "aliases": [], "original_title": "", "source": "tmdb", "format": "series",
                "media_type": "", "type": "tv", "seasons": [{"number": 1, "episodes": 2}]}
        with mock.patch.object(scrape, "archive_search", return_value=[
                {"url": "https://archive.org/details/popeye", "name": "Popeye", "names": ["Popeye"], "id": "popeye"}]):
            got = self.scraper.resolve(self.site, show, 1, 2)
            self.assertTrue(got["url"].endswith("I%20Yam%20What%20I%20Yam.mp4"), got["url"])
            self.assertEqual(len(got["qualities"]), 2)
            with self.assertRaises(scrape.ScrapeError):
                self.scraper.resolve(self.site, show, 1, 5)

    def test_the_sites_test(self):
        r = scrape.test_site(self.site, "Night of the Living Dead")
        self.assertEqual(r["count"], 3)


if __name__ == "__main__":
    unittest.main()
