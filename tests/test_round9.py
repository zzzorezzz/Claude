"""Round 9: watch parties (the host's torrent with the invite, getting ready
together, closing for everyone, links and pictures), I'm in the mood for,
adding titles straight away, Everything connected, torrents that stop when
they're done, and the settings that came with them."""

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tests import fixtures
from tests.fake_syncplay import FakeSyncplayServer
from tests.test_party import wait_for
from tests.test_server import ServerBase
from tests.test_sorter import _Base, result
from watchlist import ai, launcher, metadata, mood, syncplay
from watchlist import party as party_mod
from watchlist import torrents as torrents_mod
from watchlist.config import Settings

PIC = "data:image/jpeg;base64,/9j/4AAQSkZJRg=="


class PartyRoomTest(unittest.TestCase):
    """The parts of a party that don't need a player."""

    def setUp(self):
        self.server = FakeSyncplayServer()
        self.addCleanup(self.server.stop)
        self.resolved, self.opened = [], []

        def resolve(file_, report=None, at=0.0):
            self.resolved.append(file_)
            return None
        self.host = party_mod.Party(self.server.address, "room", "Ali", "host", avatar=PIC).connect()
        self.guest = party_mod.Party(self.server.address, "room", "Sara", "guest", leader="Ali", resolve=resolve,
                                     open_link=self.opened.append).connect()
        self.addCleanup(lambda: [p.leave() for p in (self.host, self.guest)])

    def person(self, party, name):
        return next((p for p in party.status()["people"] if p["name"] == name), None)

    def test_everyone_sees_your_picture(self):
        self.assertTrue(wait_for(lambda: self.person(self.guest, "Ali")["avatar"] == PIC))
        self.assertEqual(self.person(self.host, "Ali")["avatar"], PIC)
        self.assertTrue(wait_for(lambda: self.person(self.host, "Sara")))
        self.assertEqual(self.person(self.host, "Sara")["avatar"], "")

    def test_guests_get_ready_while_the_host_does(self):
        meta = {"key": "mal:1:1:5", "src": "mal", "id": "1", "type": "anime", "title": "Show", "season": 1,
                "episode": 5, "magnet": "magnet:?xt=urn:btih:" + "a" * 40, "th": "a" * 40, "ti": 3}
        self.host.announce_upcoming(meta, "Show - S1 E5")
        self.assertTrue(wait_for(lambda: self.resolved))
        got = self.resolved[0]["watchlist"]
        self.assertEqual((got["status"], got["ti"], got["label"]), ("preparing", 3, "Show - S1 E5"))
        self.assertEqual(self.person(self.guest, "Ali")["state"], "preparing")
        # The same thing announced again (then played) isn't followed twice.
        self.host.announce_upcoming(meta, "Show - S1 E5")
        time.sleep(0.8)
        self.assertEqual(len(self.resolved), 1)

    def test_host_closing_the_player_closes_the_guests(self):
        session = mock.Mock(ended=False)
        session.current.return_value = None
        self.guest.session = session
        self.guest._following = self.guest._last_leader_key = "mal:1:1:5"
        self.host._set_file({"name": "", "duration": 0, "size": 0, "watchlist": {"status": "idle"}})
        self.assertTrue(wait_for(lambda: session.stop.called, 5))
        self.assertTrue(self.guest.active and self.host.active)        # the party carries on
        self.assertIsNone(self.guest._following)

    def test_a_page_the_host_opens_opens_for_the_guests(self):
        self.host.share_link("https://www.netflix.com/search?q=Dark", "Dark on Netflix")
        self.assertTrue(wait_for(lambda: self.opened == ["https://www.netflix.com/search?q=Dark"]))
        self.guest.share_link("https://example.com/")                   # only the host can
        self.host.conn.send({"Chat": party_mod.OPEN_LINK + "file:///etc/passwd"})
        time.sleep(0.6)
        self.assertEqual(len(self.opened), 1)


class InviteTest(unittest.TestCase):
    def test_the_hosts_torrent_comes_with_the_invite(self):
        now = {"src": "mal", "id": "52991", "type": "anime", "title": "Frieren", "season": 1, "episode": 3,
               "magnet": "magnet:?xt=urn:btih:" + "b" * 40, "th": "b" * 40, "ti": 2, "peer": "1.2.3.4:6881",
               "secret": "not copied"}
        code = syncplay.make_invite("syncplay.pl:8999", "room-1", host="Ali", now=now)
        got = syncplay.read_invite(code)["now"]
        self.assertEqual((got["ti"], got["peer"], got["episode"]), (2, "1.2.3.4:6881", 3))
        self.assertNotIn("secret", got)
        self.assertNotIn("now", syncplay.read_invite(syncplay.make_invite("s:1", "r", host="A")))


class MoodTest(ServerBase):
    def setUp(self):
        super().setUp()
        self.calls = []
        real = fixtures.fake_get_json

        def fake(url, headers=None, service="the server"):
            self.calls.append(url)
            if "/3/search/movie" in url:
                q = url.split("query=")[1].split("&")[0].lower()
                if q.startswith("interstella"):
                    return {"results": [INTERSTELLAR]}
                return {"results": []}
            if "/3/movie/157336/recommendations" in url:
                return {"results": [HAIL_MARY, dict(INTERSTELLAR, id=27205, title="Inception")]}
            if "/3/movie/157336/similar" in url:
                return {"results": []}
            if "/3/trending/movie/week" in url:
                return {"results": [HAIL_MARY]}
            return real(url, headers, service)
        p = mock.patch.object(metadata, "_get_json", side_effect=fake)
        p.start()
        self.addCleanup(p.stop)
        metadata._alike_cache._data.clear()

    def run_job(self, text, kind="movie", picks=None):
        prompts = []

        def fake_ask(settings, prompt, **kw):
            prompts.append(prompt)
            return picks or {"understood": "", "intro": "", "from_list": [], "new": []}, "gemini"
        with mock.patch.object(ai, "ask", side_effect=fake_ask):
            status, job = self.call("POST", "/api/find/mood", {"text": text, "kind": kind})
            self.assertEqual(status, 200, job)
            self.assertTrue(wait_for(lambda: self.call("GET", f"/api/find/mood/{job['id']}")[1]["done"], 15))
        return self.call("GET", f"/api/find/mood/{job['id']}")[1], prompts

    def test_a_misspelled_title_finds_titles_like_it(self):
        job, _ = self.run_job("indterstella")
        self.assertEqual(job["understood"], "Interstellar (2014)")
        self.assertEqual([r["title"] for r in job["like"]], ["Project Hail Mary", "Inception"])
        self.assertEqual(job["like"][1]["in_list"], None)

    def test_the_ai_gets_what_you_meant_and_whats_new(self):
        self.app.settings.update({"gemini_api_key": "k"})
        picks = {"understood": "space films", "intro": "Big space stories", "from_list": [],
                 "new": [{"title": "Inception", "year": 2010, "type": "movie", "why": "mind-bending"},
                         {"title": "Breaking Bad", "year": 2008, "type": "tv", "why": "not a film"}]}
        job, prompts = self.run_job("indterstella", picks=picks)
        self.assertIn('they mean the title "Interstellar"', prompts[0])
        self.assertIn("Project Hail Mary (2026", prompts[0])           # what's new is in the question
        self.assertIn("never ask a question", prompts[0])
        # Inception was already shown under "Like Interstellar"; the TV show isn't a film.
        self.assertEqual(job["picks"], [])
        self.assertEqual(job["intro"], "Big space stories")

    def test_a_kind_is_needed_and_it_can_be_cancelled(self):
        status, _ = self.call("POST", "/api/find/mood", {"text": "cozy", "kind": "podcast"})
        self.assertEqual(status, 400)
        gate = threading.Event()
        with mock.patch.object(mood.Job, "_seed", side_effect=lambda s: gate.wait(5) and None):
            _, job = self.call("POST", "/api/find/mood", {"text": "cozy", "kind": "anime"})
            self.call("POST", f"/api/find/mood/{job['id']}/cancel")
            gate.set()
            _, after = self.call("GET", f"/api/find/mood/{job['id']}")
        self.assertTrue(after["done"])
        self.assertEqual(after["stage"], "Cancelled")


INTERSTELLAR = {"id": 157336, "title": "Interstellar", "original_title": "Interstellar", "release_date": "2014-11-05",
                "overview": "", "poster_path": "/i.jpg", "vote_average": 8.4, "vote_count": 30000, "popularity": 80}
HAIL_MARY = {"id": 687163, "title": "Project Hail Mary", "original_title": "Project Hail Mary",
             "release_date": "2026-03-20", "overview": "", "poster_path": "/p.jpg", "vote_average": 8.1,
             "vote_count": 900, "popularity": 300}


class AddStraightAwayTest(ServerBase):
    def test_added_at_once_and_filled_in_later(self):
        gate = threading.Event()
        real = metadata.details
        with mock.patch.object(metadata, "details", side_effect=lambda *a: gate.wait(5) and real(*a)):
            status, item = self.call("POST", "/api/items", {"type": "tv", "source": "tmdb", "source_id": "tv/1396",
                                                            "hint": {"title": "Breaking Bad", "year": 2008, "poster": "x"}})
            self.assertEqual(status, 200, item)
            self.assertTrue(item["pending"])
            self.assertEqual((item["title"], item["year"]), ("Breaking Bad", 2008))
            # Asking again (a second click) doesn't add it twice.
            _, again = self.call("POST", "/api/items", {"type": "tv", "source": "tmdb", "source_id": "tv/1396",
                                                        "hint": {"title": "Breaking Bad"}})
            self.assertEqual(again["id"], item["id"])
            gate.set()
            self.assertTrue(wait_for(lambda: not self.app.store.get(item["id"])["pending"]))
        full = self.app.store.get(item["id"])
        self.assertEqual(full["tagline"], "Remember my name")
        self.assertEqual(len(self.app.store.list()), 1)

    def test_a_shared_collection_only_in_collections_unless_ticked(self):
        show = self.add("tv", "tmdb", "tv/1396")
        movie = self.add("movie", "tmdb", "movie/27205")
        _, col = self.call("POST", "/api/collections", {"name": "Weekend"})
        self.call("PUT", f"/api/items/{show['id']}/collections", {"collections": [col["id"]]})
        self.call("PUT", f"/api/items/{movie['id']}/collections", {"collections": [col["id"]]})
        _, shared = self.call("POST", f"/api/collections/{col['id']}/share")
        for i in (show, movie):
            self.call("DELETE", f"/api/items/{i['id']}")
        _, res = self.call("POST", "/api/shared/import", {"code": shared["code"]})
        self.assertEqual((res["titles"], res["listed"]), (2, False))
        items = self.app.store.list()
        self.assertEqual({i["listed"] for i in items}, {False})
        self.assertTrue(all(res["collection"]["id"] in i["collections"] for i in items))
        # Into your list from its page.
        _, item = self.call("PUT", f"/api/items/{items[0]['id']}", {"listed": True})
        self.assertTrue(item["listed"])
        # Ticked: in your list too.
        _, res = self.call("POST", "/api/shared/import", {"code": shared["code"], "add_to_list": True})
        self.assertEqual({i["listed"] for i in self.app.store.list()}, {True})
        self.assertTrue(wait_for(lambda: not any(i["pending"] for i in self.app.store.list())))


class ConnectedTest(_Base):
    def test_a_spinoff_finds_the_show_it_comes_from(self):
        curtis = self.app.store.add(dict(__import__("watchlist.store", fromlist=["new_item"]).new_item("tv", {
            "source": "tmdb", "source_id": "tv/900", "title": "President Curtis", "format": "series",
            "overview": "A spin-off of Rick and Morty following the President of the United States."})))
        self.searches[("tv", "Rick and Morty")] = [result("tv", "Rick and Morty", "tv/60625", year=2013, votes=9000)]
        self.searches[("tv", "Rick and Morty: The Anime")] = []
        self.searches[("tv", "President Curtis")] = [result("tv", "President Curtis", "tv/900", year=2026)]
        self.searches[("movie", "Rick and Morty")] = []
        rick_spinoffs = [result("tv", "Rick and Morty", "tv/60625", year=2013, votes=9000),
                         result("tv", "Rick and Morty: The Anime", "tv/245000", year=2024, votes=200)]
        self.searches[("tv", "Rick and Morty")] = rick_spinoffs
        out = self.app.connected(curtis, run=True, wait=True)
        by_title = {e["title"]: e for e in out["entries"]}
        self.assertEqual(by_title["Rick and Morty"]["group"], "main")
        self.assertEqual(by_title["Rick and Morty"]["relation"], "It comes from this")
        self.assertEqual(by_title["Rick and Morty: The Anime"]["group"], "other")
        self.assertEqual(out["entries"][0]["title"], "Rick and Morty")     # the same story first

    def test_other_versions_of_a_film_are_shown_apart(self):
        film = self.app.store.add(__import__("watchlist.store", fromlist=["new_item"]).new_item("movie", {
            "source": "tmdb", "source_id": "movie/969681", "title": "Spider-Man: Brand New Day", "format": "movie",
            "tmdb_collection": 531241}))
        with mock.patch.object(metadata.TMDB, "collection", return_value=[
                result("movie", "Spider-Man: Homecoming", "movie/315635", year=2017)]):
            self.searches[("movie", "Spider-Man")] = [
                result("movie", "Spider-Man", "movie/557", year=2002), result("movie", "Spider-Man: Homecoming", "movie/315635", year=2017),
                result("movie", "Spider-Man: Into the Spider-Verse", "movie/324857", year=2018)]
            self.searches[("tv", "Spider-Man")] = [result("tv", "Spider-Man: The Animated Series", "tv/2172", year=1994)]
            out = self.app.connected(film, run=True, wait=True)
        groups = {e["title"]: e["group"] for e in out["entries"]}
        self.assertEqual(groups["Spider-Man: Homecoming"], "main")
        self.assertEqual(groups["Spider-Man"], "other")
        self.assertEqual(groups["Spider-Man: Into the Spider-Verse"], "other")
        self.assertEqual(groups["Spider-Man: The Animated Series"], "other")
        self.assertEqual(out["entries"][0]["title"], "Spider-Man: Homecoming")

    def test_can_be_cancelled(self):
        self.app.settings.update({"gemini_api_key": "g"})
        gate = threading.Event()
        with mock.patch.object(ai, "connected_titles", side_effect=lambda s, i: gate.wait(10) and []):
            item = self.app.store.get(self.frieren["id"])
            self.app.connected(item, run=True)
            time.sleep(0.3)
            self.app.cancel_connected(item)
            self.assertTrue(wait_for(lambda: not self.app._connected_jobs[f"connected:mal:{item['source_id']}"]["running"], 5))
            gate.set()
        self.assertEqual(self.app._connected_jobs[f"connected:mal:{item['source_id']}"]["stage"], "Cancelled")


class TorrentsStopWhenDoneTest(unittest.TestCase):
    def test_stops_sharing_once_everything_wanted_is_here(self):
        t = torrents_mod.Torrents.__new__(torrents_mod.Torrents)
        handle = mock.Mock()
        t.handles = {"h": handle}
        t.reading, t._dirty = {}, False
        t.records = {"h": {"hash": "h", "files": [{"index": 0, "path": "a.mkv", "size": 10},
                                                  {"index": 1, "path": "b.mkv", "size": 10}],
                           "priorities": {"0": 7, "1": 0}, "paused": False}}
        progress = [10, 0]
        t._progress = lambda h: progress
        t.complete = lambda rec, f, prog: prog[f["index"]] >= f["size"]
        t._stop_when_done()
        self.assertTrue(t.records["h"]["done"])
        handle.pause.assert_called_once()
        # Something new wanted: it starts again.
        t._wake("h", t.records["h"])
        self.assertFalse(t.records["h"]["done"])
        handle.resume.assert_called_once()
        # Not while it's streaming, and not while something wanted is missing.
        t.records["h"]["priorities"]["1"] = 4
        t._stop_when_done()
        self.assertFalse(t.records["h"]["done"])


class SettingsTest(unittest.TestCase):
    def test_theme_and_picture(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Settings(Path(tmp) / "s.json")
            s.update({"theme": "crimson", "party_avatar": PIC})
            self.assertEqual((s.get()["theme"], s.get()["party_avatar"]), ("crimson", PIC))
            for bad in ({"theme": "neon"}, {"party_avatar": "javascript:alert(1)"},
                        {"party_avatar": "data:image/jpeg;base64," + "A" * 20000}):
                with self.assertRaises(ValueError):
                    s.update(bad)

    def test_built_in_browser_sign_out_clears_it_at_the_next_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "browser-data"
            store.mkdir()
            (store / "Cookies").write_text("session")
            launcher.sign_out_of_sites(store)
            self.assertEqual(launcher._prepare_storage(store), str(store))
            self.assertFalse((store / "Cookies").exists())
            self.assertTrue(store.is_dir())
            self.assertIsNone(launcher._prepare_storage(None))


if __name__ == "__main__":
    unittest.main()


class PhoneTest(ServerBase):
    """The phone app's side of the server (android.py)."""

    def test_the_phone_player_reports_where_you_are(self):
        show = self.add("tv", "tmdb", "tv/1396")
        status, _ = self.call("POST", "/api/android/progress", {"item_id": show["id"], "key": "1:3", "pos": 600, "dur": 2800})
        self.assertEqual(status, 200)
        self.assertEqual(self.app.store.get(show["id"])["positions"]["1:3"]["pos"], 600)
        self.call("PUT", f"/api/items/{show['id']}", {"progress": {"season": 1, "episode": 3}})
        self.call("POST", "/api/android/progress", {"item_id": show["id"], "key": "1:3", "finished": True})
        item = self.app.store.get(show["id"])
        self.assertEqual(item["progress"], {"season": 1, "episode": 4})
        self.assertNotIn("1:3", item["positions"])
        status, _ = self.call("POST", "/api/android/progress", {"item_id": "nope", "key": "1:1"})
        self.assertEqual(status, 404)

    def test_playing_on_a_phone_goes_to_the_phone_player(self):
        video = self.media / "show" / "Breaking.Bad.S01E01.mkv"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"x")
        show = self.add("tv", "tmdb", "tv/1396")
        self.call("PUT", f"/api/items/{show['id']}", {"local_folder": str(video.parent)})
        with mock.patch.object(launcher, "ANDROID", True):
            status, res = self.call("POST", f"/api/items/{show['id']}/play", {"path": str(video), "season": 1, "episode": 1})
            _, state = self.call("GET", "/api/state")
        self.assertEqual(status, 200, res)
        self.assertEqual((res["player"], res["entries"][0]["key"]), ("android", "1:1"))
        self.assertEqual(state["status"]["platform"], "android")
        self.assertEqual(self.spawned, [])                      # nothing started on a computer

    def test_the_apps_own_file_browser(self):
        (self.media / "Anime" / "Frieren").mkdir(parents=True)
        (self.media / "Anime" / "ep1.mkv").write_bytes(b"x")
        (self.media / "Anime" / "notes.txt").write_text("x")
        status, d = self.call("GET", "/api/fs?path=" + str(self.media / "Anime"))
        self.assertEqual(status, 200, d)
        self.assertEqual((d["dirs"], [f["name"] for f in d["files"]]), (["Frieren"], ["ep1.mkv"]))

    def test_links_open_on_the_phone(self):
        opened = []
        bridge = mock.Mock(openUrl=opened.append)
        with mock.patch.object(launcher, "ANDROID", True), mock.patch.object(launcher, "android_bridge", bridge):
            launcher.open_url("https://www.netflix.com/search?q=Dark")
        self.assertEqual(opened, ["https://www.netflix.com/search?q=Dark"])
