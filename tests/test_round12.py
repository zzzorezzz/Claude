"""Round 12: watch parties that stay together (someone behind catches up, pressing play late
joins the others, a player waiting for its video isn't a skip, no loop of jumps back); every
server of an episode (listed by the page's player, each pressed in it), titles matched without
being fussy about spelling or years, sites addressed by TMDB's ids reached without a search."""
import json
import tempfile
import time
import unittest
from unittest import mock

from watchlist import party as party_mod
from watchlist import scrape, webplay, webtools
from watchlist.server import App
from watchlist.store import new_item


class FakeSession:
    def __init__(self, pos=0.0, paused=False):
        self.pos, self.paused, self.stalled, self.stall_seen = pos, paused, False, -1e9
        self.sent = []
        self.connected, self.dur, self.ended = True, 1400.0, False

    def position(self):
        return self.pos

    def send(self, *commands):
        self.sent.extend(commands)
        for c in commands:
            if c[0] == "seek":
                self.pos = float(c[1])
            elif c[0] == "set_property" and c[1] == "pause":
                self.paused = bool(c[2])


def party(role="guest"):
    p = party_mod.Party("127.0.0.1:1", "room", "me", role)
    p.conn = mock.Mock()
    p._announced = True
    return p


def playing_room(p, session, at):
    """The room playing, seen a moment ago at `at`; this player playing where it is."""
    now = time.time()
    p._global_pos, p._global_paused, p._global_at = at, False, now
    p._player_pos, p._player_paused, p._player_at = session.pos, session.paused, now


class PartySyncTest(unittest.TestCase):
    def test_someone_behind_jumps_to_the_others(self):
        p, s = party(), FakeSession(pos=100.0)
        p.session = s
        playing_room(p, s, 100.0)
        p._apply_room_state(s, 104.0, False, False, "host")
        self.assertIn(["seek", 104.0 + party_mod.FASTFORWARD_EXTRA, "absolute"], s.sent)

    def test_a_little_behind_plays_faster_until_caught_up(self):
        p, s = party(), FakeSession(pos=100.0)
        p.session = s
        playing_room(p, s, 100.0)
        p._apply_room_state(s, 101.0, False, False, "host")
        self.assertIn(["set_property", "speed", party_mod.SPEEDUP_RATE], s.sent)
        s.sent.clear()
        p._player_pos, p._player_at = 101.0, time.time()
        p._apply_room_state(s, 101.0, False, False, "host")
        self.assertIn(["set_property", "speed", 1.0], s.sent)

    def test_pressing_play_late_joins_the_others_where_they_are(self):
        p, s = party(), FakeSession(pos=50.0, paused=True)
        p.session = s
        now = time.time()
        p._global_pos, p._global_paused, p._global_at = 58.0, False, now
        p._player_pos, p._player_paused, p._player_at = 50.0, True, now
        s.paused = False                                  # you press play
        p._update_player(s)
        seeks = [c for c in s.sent if c[0] == "seek"]
        self.assertTrue(seeks and abs(seeks[0][1] - 58.0) < 0.5, s.sent)
        # ...and the others aren't sent back to where you were.
        for call in p.conn.send.call_args_list:
            state = call.args[0].get("State", {}).get("playstate") or {}
            self.assertFalse(state.get("doSeek"), state)

    def test_waiting_for_the_video_isnt_a_skip_back(self):
        p, s = party(), FakeSession(pos=200.0)
        p.session = s
        playing_room(p, s, 200.0)
        p._player_at = time.time() - 3                    # three seconds ago it was at 200
        s.pos, s.stalled, s.stall_seen = 200.5, True, time.monotonic()
        p._update_player(s)
        for call in p.conn.send.call_args_list:
            state = call.args[0].get("State", {}).get("playstate") or {}
            self.assertFalse(state.get("doSeek"), "a wait was told to the room as a skip")

    def test_a_skip_back_is_still_told(self):
        p, s = party(), FakeSession(pos=200.0)
        p.session = s
        playing_room(p, s, 200.0)
        s.pos = 120.0                                     # you went back yourself
        p._update_player(s)
        sent = [c.args[0]["State"]["playstate"] for c in p.conn.send.call_args_list if "playstate" in c.args[0].get("State", {})]
        self.assertTrue(any(x.get("doSeek") for x in sent))

    def test_the_same_few_seconds_again_and_again_stop(self):
        p, s = party(), FakeSession(pos=300.0)
        p.session = s
        playing_room(p, s, 300.0)
        for _ in range(party_mod.LOOP_MAX):
            s.pos = p._player_pos = 303.0
            p._player_at = time.time()
            p._apply_room_state(s, 300.0, False, True, "friend")
        s.sent.clear()
        s.pos = p._player_pos = 303.0
        p._player_at = time.time()
        p._apply_room_state(s, 300.0, False, True, "friend")
        self.assertFalse([c for c in s.sent if c[0] == "seek"], "it went round the same seconds again")


class StallTest(unittest.TestCase):
    def test_a_phone_waiting_for_its_video_isnt_moving_on(self):
        w = webplay.WebSession("dev", "item", [{"key": "1:1"}])
        w.event({"index": 0, "dur": 1400, "pos": 60, "paused": False, "stall": True})
        w.pos_at -= 5
        self.assertAlmostEqual(w.position(), 60.0)
        w.event({"index": 0, "pos": 60.2, "paused": False})
        w.pos_at -= 2
        self.assertGreater(w.position(), 62)


class MatchTest(unittest.TestCase):
    def test_a_spelling_mistake_still_matches(self):
        wanted = scrape.wanted_of({"title": "Naruto Shippuden", "type": "anime"})
        self.assertGreaterEqual(scrape.score("Naruto Shipuden", wanted), 80)
        wanted = scrape.wanted_of({"title": "Breaking Bad", "type": "tv"})
        self.assertGreaterEqual(scrape.score("Breakin Bad", wanted), 80)

    def test_a_wrong_year_is_only_a_hint(self):
        wanted = scrape.wanted_of({"title": "Dune", "type": "movie", "year": 2021})
        ranked = scrape.best_entry([{"url": "/a", "name": "Dune", "names": ["Dune"], "year": 2020}], wanted)
        self.assertGreaterEqual(ranked[0]["score"], 85)

    def test_only_a_little_like_it_asks_instead_of_playing(self):
        s = scrape.Scraper(tempfile.mkdtemp())
        [site] = scrape.clean([{"name": "Site", "url": "https://site.test/search?q={title}"}])
        item = new_item("anime", {"source": "mal", "source_id": "1", "title": "Haikyuu!! Karasuno Koukou vs Shiratorizawa Gakuen Koukou"})
        weak = [{"url": "https://site.test/a/1", "name": "High School Agent", "names": ["High School Agent"], "score": 30}]
        with mock.patch.object(s, "search", return_value=weak):
            with self.assertRaises(scrape.ScrapeError) as e:
                s._entry(site, item, None)
        self.assertTrue(e.exception.choices)

    def test_a_site_addressed_by_tmdb_ids_is_reached_without_a_search(self):
        s = scrape.Scraper(tempfile.mkdtemp())
        [site] = scrape.clean([{"name": "Site", "url": "https://site.test/explore?q={title}"}])
        bb = dict(new_item("tv", {"source": "tmdb", "source_id": "tv/1396", "title": "Breaking Bad"}), format="series")
        got = [{"url": "https://site.test/tv/1396", "name": "Breaking Bad", "names": ["Breaking Bad"], "score": 100}]
        with mock.patch.object(s, "search", return_value=got):
            s._entry(site, bb, 1)
        got_item = dict(new_item("tv", {"source": "tmdb", "source_id": "tv/1399", "title": "Game of Thrones"}), format="series")
        with mock.patch.object(s, "search") as search, mock.patch.object(scrape, "_exists", return_value=True):
            entry, _ = s._entry(site, got_item, 1)
        search.assert_not_called()
        self.assertEqual(entry["url"], "https://site.test/tv/1399")

    def test_a_guessed_address_thats_another_show_isnt_played(self):
        s = scrape.Scraper(tempfile.mkdtemp())
        [site] = scrape.clean([{"name": "Site", "url": "https://site.test/search?q={title}"}])
        bb = dict(new_item("tv", {"source": "tmdb", "source_id": "tv/1396", "title": "Breaking Bad"}), format="series")
        page = "<html><head><title>Watch Totally Different Show - Site</title></head></html>"
        with mock.patch.object(s, "search", return_value=[]), \
                mock.patch.object(scrape, "fetch", return_value=(page, "https://site.test/watch/1396")):
            with self.assertRaises(scrape.ScrapeError):
                s._entry(site, bb, 1)


class ServersTest(unittest.TestCase):
    def test_a_page_telling_its_servers(self):
        opts = {}
        msg = {"f": "player.test", "i": [{"l": "Prism", "k": "", "a": True}, {"l": "Edge", "k": "", "a": False}]}
        self.assertTrue(webtools.servers_signal(webtools.SERVERS + json.dumps(msg), opts))
        self.assertFalse(webtools.servers_signal("https://site.test/x.m3u8", opts))
        got = webtools.servers_of(opts, "https://site.test/watch/1")
        self.assertEqual([x["label"] for x in got], ["Prism", "Edge"])
        self.assertEqual(got[0]["frame"], "player.test")
        streams, playing = scrape.clicked_streams(got)
        self.assertEqual(playing["label"], "Prism")
        self.assertEqual(len({x["id"] for x in streams}), 2)

    def test_one_listed_server_is_pressed_in_the_page(self):
        s = scrape.Scraper(tempfile.mkdtemp())
        s.catch = mock.Mock(return_value={"url": "https://cdn.test/edge.m3u8", "headers": {}})
        [site] = scrape.clean([{"name": "Site", "url": "https://site.test/explore?q={title}"}])
        item = dict(new_item("tv", {"source": "tmdb", "source_id": "tv/1396", "title": "Breaking Bad"}), format="series")
        page = "https://site.test/watch/tv/1396/1/1"
        streams, _ = scrape.clicked_streams([{"label": "Edge", "kind": "", "frame": "player.test"}])
        with mock.patch.object(s, "episode_page", return_value=(page, {"url": "https://site.test/tv/1396"}, [])), \
                mock.patch.object(scrape, "qualities", return_value=[]):
            got = s.resolve(site, item, 1, 1, stream=f"{streams[0]['id']}::Edge", fresh=True, remember=False)
        s.catch.assert_called_once_with(page, click={"label": "Edge", "kind": "", "exact": True, "frame": "player.test"})
        self.assertEqual(got["url"], "https://cdn.test/edge.m3u8")
        self.assertFalse((s.memory.get("_sites") or {}).get(site["url"], {}).get("label"), "a check changed the site's choice")

    def test_a_show_doesnt_press_sub(self):
        s = scrape.Scraper(tempfile.mkdtemp())
        s.catch = mock.Mock(return_value={"url": "https://cdn.test/v.m3u8", "headers": {},
                                          "servers": [{"label": "Prism", "kind": "", "on": True, "frame": ""},
                                                      {"label": "Edge", "kind": "", "on": False, "frame": ""}]})
        [site] = scrape.clean([{"name": "Site", "url": "https://site.test/explore?q={title}"}])
        item = dict(new_item("tv", {"source": "tmdb", "source_id": "tv/1396", "title": "Breaking Bad"}), format="series")
        page = "https://site.test/watch/tv/1396/1/1"
        with mock.patch.object(s, "episode_page", return_value=(page, {"url": "https://site.test/tv/1396"}, [])), \
                mock.patch.object(scrape, "qualities", return_value=[]), \
                mock.patch.object(scrape, "fetch", return_value=("", page)):
            got = s.resolve(site, item, 1, 1)
        s.catch.assert_called_once_with(page, click=None)
        self.assertEqual([x["label"] for x in got["streams"]], ["Prism", "Edge"])
        self.assertEqual(got["stream"]["label"], "Prism")


class ServerListTest(unittest.TestCase):
    def setUp(self):
        # (the background matching of the season can still be writing as the test ends)
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.app = App(self.tmp.name)
        self.app.settings.update({"video_sites": [{"name": "Site", "url": "https://site.test/explore?q={title}", "types": ["tv"]}]})
        self.site = self.app.settings.get()["video_sites"][0]
        self.item = self.app.store.add(dict(new_item("tv", {"source": "tmdb", "source_id": "tv/1396", "title": "Breaking Bad"}),
                                            format="series"))

    def test_every_server_checked_and_one_chosen(self):
        app, site = self.app, self.site
        streams, playing = scrape.clicked_streams([{"label": "Prism", "kind": "", "on": True, "frame": ""},
                                                   {"label": "Edge", "kind": "", "on": False, "frame": ""},
                                                   {"label": "Flux", "kind": "", "on": False, "frame": ""}])
        found = {"url": "https://cdn.test/prism.m3u8", "page": "https://site.test/watch/tv/1396/1/1", "referer": "", "headers": {},
                 "streams": streams, "stream": playing, "entry": {"url": "https://site.test/tv/1396"}}
        session = webplay.WebSession("dev", self.item["id"], [{"key": "1:1", "web": True, "path": found["url"]}])
        session.swap = mock.Mock()
        app._online_found[(self.item["id"], "1:1")] = (site, found, time.time())

        def resolve(site_, item, season, episode, stream="", fresh=False, remember=True, **kw):
            label = stream.split(":", 2)[2]
            if label == "Flux":
                raise scrape.ScrapeError("nothing")
            return {"url": f"https://cdn.test/{label.lower()}.m3u8", "page": found["page"], "referer": "", "headers": {}, "streams": []}
        with mock.patch.object(app.scraper, "resolve", side_effect=resolve):
            app.server_list(session)
            for _ in range(100):
                if not app._servers[(self.item["id"], "1:1")]["running"]:
                    break
                time.sleep(0.05)
            got = app.server_list(session, start=False)
        states = {x["label"]: x["state"] for x in got["items"]}
        self.assertEqual(states["Prism"], "ok")
        self.assertEqual(states["Edge"], "ok")
        self.assertEqual(states["Flux"], "failed")
        self.assertTrue(next(x for x in got["items"] if x["label"] == "Prism")["on"])
        edge = next(x for x in got["items"] if x["label"] == "Edge")
        app.use_server(session, edge["id"])
        entry = session.swap.call_args.args[0]
        self.assertIn("edge.m3u8", json.dumps(entry))
        self.assertEqual(app._online_found[(self.item["id"], "1:1")][1]["stream"]["label"], "Edge")


if __name__ == "__main__":
    unittest.main()


class GenericSiteTest(unittest.TestCase):
    """What other sites taught the finder (no particular site's code)."""

    def test_links_follow_the_pages_base(self):
        page = scrape.parse('<base href="https://site.test/"><a href="watch-abc-dune-2021.html"><h3>Dune (2021)</h3></a>',
                            "https://site.test/search/dune.html")
        found = scrape.candidates(page, "https://site.test/search/dune.html")
        self.assertEqual(found[0]["url"], "https://site.test/watch-abc-dune-2021.html")

    def test_other_titles_cards_arent_its_episodes(self):
        cards = "".join(f'<a href="/anime/{900 + n}">EP {n * 12} Other show {n}</a>' for n in range(1, 6))
        own = "".join(f'<a href="/watch/269/{n}">{n}</a>' for n in range(1, 13))
        eps = scrape.episode_links(scrape.parse(cards + own, "https://site.test/anime/269"))
        self.assertEqual(scrape.pick_episode(eps, None, 1), "https://site.test/watch/269/1")

    def test_a_server_named_like_youtube_isnt_a_trailer(self):
        self.assertFalse(webtools.trailer("https://m3u8-play.xyz-youtube.cfd/hls/1080/abc"))
        self.assertTrue(webtools.trailer("https://www.youtube.com/embed/abc"))
        self.assertTrue(webtools.trailer("https://cdn.site.test/trailers/dune.mp4"))

    def test_scrambled_bytes_arent_a_video(self):
        def answer(data):
            r = mock.MagicMock()
            r.__enter__.return_value.read.return_value = data
            return r
        with mock.patch("urllib.request.urlopen", return_value=answer(bytes(range(7, 255)) * 4)):
            self.assertTrue(scrape.scrambled_video("https://cdn.test/p/1/abc"))
        with mock.patch("urllib.request.urlopen", return_value=answer(b"\x00\x00\x00\x20ftypisom" + bytes(600))):
            self.assertFalse(scrape.scrambled_video("https://cdn.test/p/1/abc"))
        self.assertFalse(scrape.scrambled_video("https://cdn.test/master.m3u8"))

    def test_a_search_link_with_a_slug(self):
        self.assertEqual(scrape.search_url({"url": "https://site.test/search/{slug}.html"}, "Breaking Bad: Season 1"),
                         "https://site.test/search/breaking-bad-season-1.html")

    def test_a_search_only_counts_when_it_finds_both_titles(self):
        home = '<form action="/search"><input type="search" name="q"></form><a href="/show/naruto">Naruto</a>'
        trending = '<a href="/show/naruto">Naruto</a><a href="/show/one-piece">One Piece</a>'

        def fetch(url, **kw):
            if "/search?q=" in url and "Death" in url:
                return trending, url                 # (a page of what's popular, whatever is searched)
            if "/filter?keyword=" in url:
                name = "Naruto" if "Naruto" in url else "Death Note"
                return f'<a href="/show/{name.lower().replace(" ", "-")}-x">{name}</a>', url
            if "/search?q=" in url:
                return trending, url
            return home, url
        with mock.patch.object(scrape, "fetch", side_effect=fetch):
            got = scrape.find_search("https://site.test", "anime")
        self.assertEqual(got["url"], "https://site.test/filter?keyword={title}")


class NothingPlayedTest(unittest.TestCase):
    def test_the_pages_other_servers_are_tried(self):
        s = scrape.Scraper(tempfile.mkdtemp())
        servers = [{"label": "Main", "kind": "", "on": True, "frame": ""}, {"label": "Backup", "kind": "", "on": False, "frame": "embed.test"}]

        def catch(url, click=None):
            if click is None:
                return {"url": "", "headers": {}, "subs": [], "servers": servers}       # (nothing played by itself)
            return {"url": "https://cdn.test/backup.m3u8", "headers": {}} if click["label"] == "Backup" else None
        s.catch = mock.Mock(side_effect=catch)
        [site] = scrape.clean([{"name": "Site", "url": "https://site.test/search?q={title}"}])
        item = dict(new_item("tv", {"source": "tmdb", "source_id": "tv/1396", "title": "Breaking Bad"}), format="series")
        page = "https://site.test/watch/1"
        with mock.patch.object(s, "episode_page", return_value=(page, {"url": "https://site.test/show/1"}, [])), \
                mock.patch.object(scrape, "qualities", return_value=[]), \
                mock.patch.object(scrape, "fetch", return_value=("", page)), \
                mock.patch.object(scrape, "scrambled_video", return_value=False):
            got = s.resolve(site, item, 1, 1)
        self.assertEqual(got["url"], "https://cdn.test/backup.m3u8")
        clicks = [c.kwargs["click"] for c in s.catch.call_args_list]
        self.assertEqual(clicks[1]["label"], "Backup")          # (the other server first, not the one that didn't play)
        self.assertEqual(clicks[1]["frame"], "embed.test")


class EpisodeButtonTest(unittest.TestCase):
    def test_episodes_that_are_buttons_are_pressed(self):
        page = ('<button class="btn season-btn" data-season="1"> Season 1 </button>'
                '<button class="btn episode-btn" idepisode="a1"> S05-E16 </button><button class="btn episode-btn" idepisode="a2"> S05-E15 </button>')
        with mock.patch.object(scrape, "fetch", return_value=(page, "https://site.test/watch-x.html")):
            self.assertEqual(scrape._episode_button("https://site.test/watch-x.html", 5, 15), "S05-E15")
            self.assertEqual(scrape._episode_button("https://site.test/watch-x.html", 1, 1), "S01-E01|Season 1")
            self.assertIsNone(scrape._episode_button("https://site.test/watch-x.html", None, 400))


class AnotherShowsEpisodeTest(unittest.TestCase):
    def test_a_recommended_shows_episode_isnt_this_ones(self):
        page = scrape.parse('<a href="/watch/anila-to-cocora-episode-1-8so0pl">Episode 1</a>',
                            "https://site.test/watch/bleach-episode-1-g1jm73")
        self.assertEqual(scrape.episode_links(page), {})
        own = scrape.parse("".join(f'<a href="/watch/bleach-episode-{n}-g1jm73">Episode {n}</a>' for n in (1, 2, 3)),
                           "https://site.test/watch/bleach-episode-1-g1jm73")
        self.assertEqual(scrape.pick_episode(scrape.episode_links(own), None, 2), "https://site.test/watch/bleach-episode-2-g1jm73")


class SwitchKeepsTracksTest(unittest.TestCase):
    def test_a_stream_caught_alone_becomes_its_whole_playlist(self):
        media = "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\nseg1.ts\n"
        master = "#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID=\"a\",URI=\"a.m3u8\"\n#EXT-X-STREAM-INF:BANDWIDTH=1,AUDIO=\"a\"\nv0.m3u8\n"
        url = "https://bridge.test/?url=https%3A%2F%2Fcdn.test%2Fdash%2Fx%2Fv2.m3u8&Policy=abc"
        heads = {url: media, "https://bridge.test/?url=https%3A%2F%2Fcdn.test%2Fdash%2Fx%2Fmaster.m3u8&Policy=abc": master}
        with mock.patch.object(scrape, "_playlist_head", side_effect=lambda u, h=None: heads.get(u, "")):
            self.assertEqual(scrape.master_playlist(url), "https://bridge.test/?url=https%3A%2F%2Fcdn.test%2Fdash%2Fx%2Fmaster.m3u8&Policy=abc")
            self.assertEqual(scrape.master_playlist("https://cdn.test/a/master.m3u8"), "https://cdn.test/a/master.m3u8")

    def test_a_swap_carries_the_new_servers_headers(self):
        from watchlist import player
        s = player.Session("mpv", "i", [{"key": "1:1", "path": "https://a.test/v.m3u8", "web": True}])
        s.send = mock.Mock(return_value=True)
        s.swap({"key": "1:1", "path": "https://b.test/v.m3u8", "web": True, "referer": "https://site.test/",
                "headers": {"Origin": "https://site.test", "User-Agent": "UA x"}}, 42)
        opts = s.send.call_args.args[0][4]
        self.assertIn("start=42", opts)
        self.assertIn("referrer=%18%https://site.test/", opts)
        self.assertIn("http-header-fields=%25%Origin: https://site.test", opts)


class PartyServerTest(ServerListTest):
    def test_a_server_chosen_is_chosen_for_everyone(self):
        app, site = self.app, self.site
        streams, playing = scrape.clicked_streams([{"label": "Prism", "kind": "", "on": True, "frame": ""},
                                                   {"label": "Edge", "kind": "", "on": False, "frame": ""}])
        found = {"url": "https://cdn.test/prism.m3u8", "page": "https://site.test/watch/tv/1396/1/1", "referer": "", "headers": {},
                 "streams": streams, "stream": playing, "entry": {"url": "https://site.test/tv/1396"}}
        session = webplay.WebSession("dev", self.item["id"], [{"key": "1:1", "web": True, "path": found["url"]}])
        session.swap = mock.Mock()
        app._online_found[(self.item["id"], "1:1")] = (site, found, time.time())
        party = mock.Mock(active=True, session=session)
        app.party = party
        with mock.patch.object(app.scraper, "resolve", return_value={"url": "https://cdn.test/x.m3u8", "page": found["page"],
                                                                     "referer": "", "headers": {}, "streams": []}):
            app.server_list(session)
            for _ in range(100):
                if not app._servers[(self.item["id"], "1:1")]["running"]:
                    break
                time.sleep(0.05)
        edge = next(k for k, x in app._servers[(self.item["id"], "1:1")]["items"].items() if x["label"] == "Edge")
        with mock.patch.object(app.scraper, "resolve", return_value={"url": "https://cdn.test/edge.m3u8", "page": found["page"],
                                                                     "referer": "", "headers": {}, "streams": []}):
            app.use_server(session, edge)
        choice = party.share_server.call_args.args[0]
        self.assertEqual((choice["k"], choice["h"], choice["l"]), ("1:1", "site.test", "Edge"))

        # ...and on a guest's computer, that same server is chosen (without telling everyone again).
        app._online_found[(self.item["id"], "1:1")] = (site, dict(found, stream=playing), time.time())
        app._servers.clear()
        party.share_server.reset_mock()
        # (the host's app then tells its new copy: the guest takes that very one)
        files = iter([{"watchlist": {"url": "https://cdn.test/prism.m3u8"}}] + [{"watchlist": {"url": "https://cdn.test/host-edge.m3u8"}}] * 50)
        party.leader_file.side_effect = lambda: next(files)
        with mock.patch.object(app, "use_server") as use, mock.patch.object(scrape, "plays_here", return_value=True):
            app._party_server_choice(party, choice)
        sid = use.call_args.args[1]
        chosen = app._servers[(self.item["id"], "1:1")]["items"][sid]
        self.assertEqual(chosen["label"], "Edge")
        self.assertEqual(chosen["found"]["url"], "https://cdn.test/host-edge.m3u8")
        self.assertEqual(use.call_args.kwargs, {"share": False})


class NoClashTest(unittest.TestCase):
    def test_the_app_has_no_two_methods_of_the_same_name(self):
        import ast
        from pathlib import Path
        tree = ast.parse(Path(__file__).resolve().parent.parent.joinpath("watchlist", "server.py").read_text(encoding="utf-8"))
        for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
            names = [f.name for f in cls.body if isinstance(f, ast.FunctionDef)]
            self.assertEqual(sorted({n for n in names if names.count(n) > 1}), [], cls.name)


try:
    import libtorrent as _lt  # noqa: F401
except ImportError:
    _lt = None


@unittest.skipUnless(_lt, "needs libtorrent")
class MagnetWhileLookingInsideTest(unittest.TestCase):
    def test_a_magnet_added_while_its_files_are_being_looked_at_stays_added(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        app = App(tmp.name)
        # (libtorrent's session stopped and let go now, while all's running - not as Python exits,
        # where Linux's libtorrent aborts)
        self.addCleanup(lambda: (setattr(app.torrents, "ses", None), __import__("gc").collect()))
        self.addCleanup(app.torrents.shutdown)
        item = app.store.add(new_item("movie", {"source": "tmdb", "source_id": "movie/10378", "title": "Big Buck Bunny"}))
        magnet = "magnet:?xt=urn:btih:dd8255ecdc7ca55fb0bbf81323d87062db1f6d1c&dn=Big+Buck+Bunny"
        peeker = app.torrents.peeker()
        peeker.add(magnet)                               # (its files being looked at - search results)
        app.torrents.add(item["id"], magnet)             # ...and added meanwhile
        peeker.close()
        view = app.torrents.view("dd8255ecdc7ca55fb0bbf81323d87062db1f6d1c")   # (was "invalid torrent handle used")
        self.assertEqual(view["hash"], "dd8255ecdc7ca55fb0bbf81323d87062db1f6d1c")
        self.assertTrue(app.torrents.handles["dd8255ecdc7ca55fb0bbf81323d87062db1f6d1c"].is_valid())


class OwnPauseThenPlayTest(unittest.TestCase):
    def test_play_after_your_own_pause_plays_for_everyone(self):
        p, s = party(), FakeSession(pos=100.0)
        p.session = s
        playing_room(p, s, 100.0)
        s.paused = True                                  # you pause...
        p._update_player(s)
        p._player_at -= 9                                # (nine seconds on)
        p._client_ignoring = 0                           # (the server heard it)
        s.paused = False                                 # ...and press play again
        p._update_player(s)
        self.assertFalse([c for c in s.sent if c[0] == "seek"], "it jumped to where it thought the others were")
        states = [c.args[0]["State"]["playstate"] for c in p.conn.send.call_args_list if "playstate" in c.args[0].get("State", {})]
        self.assertEqual([x["paused"] for x in states], [True, False], "the room wasn't told about the play")


class TwoChangesQuicklyTest(unittest.TestCase):
    def test_a_second_change_before_the_first_is_heard_doesnt_shut_the_player_out(self):
        p, s = party(), FakeSession(pos=100.0)
        p.session = s
        playing_room(p, s, 100.0)
        s.paused = True
        p._update_player(s)                              # a pause...
        s.paused = False
        p._update_player(s)                              # ...and play, before the server answered the pause
        p._handle_state({"ignoringOnTheFly": {"client": 1}})   # (the server hears the pause)
        self.assertEqual(p._client_ignoring, 0, "waiting for an answer that never comes")
        s.sent.clear()
        p._handle_state({"playstate": {"position": 150.0, "paused": False, "doSeek": True, "setBy": "Ben"}})
        self.assertIn(["seek", 150.0, "absolute"], s.sent, "what the others do no longer reached this player")


class FilmTorrentPartyTest(unittest.TestCase):
    def test_a_films_torrent_goes_with_the_party(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        app = App(tmp.name)
        item = app.store.add(new_item("movie", {"source": "tmdb", "source_id": "movie/10378", "title": "Big Buck Bunny"}))
        h = "dd8255ecdc7ca55fb0bbf81323d87062db1f6d1c"
        app.torrents.records[h] = {"hash": h, "item_id": item["id"], "magnet": f"magnet:?xt=urn:btih:{h}", "files": []}
        app.torrents.port, app.torrents.token = 5555, "tok"
        path = f"http://127.0.0.1:5555/stream/tok/{h}/1/Big%20Buck%20Bunny.mp4"
        entries, _ = app._entries(item, path, None)
        self.assertEqual(entries[0]["party"]["th"], h)
        self.assertEqual(entries[0]["party"]["ti"], 1)
        self.assertTrue(entries[0]["party"]["magnet"].startswith("magnet:?xt=urn:btih:" + h))


class AdTest(unittest.TestCase):
    def test_a_video_minutes_too_short_is_an_ad(self):
        short = "#EXTM3U\n" + "#EXTINF:10.0,\nseg.ts\n" * 3
        long_ = "#EXTM3U\n" + "#EXTINF:10.0,\nseg.ts\n" * 150
        r = mock.MagicMock()
        r.__enter__.return_value.read.return_value = short.encode()
        r.__enter__.return_value.geturl.return_value = "https://ads.test/a.m3u8"
        with mock.patch("urllib.request.urlopen", return_value=r):
            self.assertEqual(scrape.hls_length("https://ads.test/a.m3u8"), 30.0)
        r.__enter__.return_value.read.return_value = long_.encode()
        with mock.patch("urllib.request.urlopen", return_value=r):
            self.assertEqual(scrape.hls_length("https://cdn.test/ep.m3u8"), 1500.0)
