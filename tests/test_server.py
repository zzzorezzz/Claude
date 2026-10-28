import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from tests import fixtures
from tests.test_scanner import touch
from watchlist import launcher, metadata, player
from watchlist.server import App, make_server


class ServerBase(unittest.TestCase):
    """A running app with fake TMDB / MyAnimeList answers."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.media = root / "media"
        self.app = App(root / "data")
        self.app.settings.update({"tmdb_api_key": "tok", "mal_client_id": "cid"})
        self.server = make_server(self.app, 0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

        self.spawned, self.opened = [], []
        for target, value in [
            (mock.patch.object(metadata, "_get_json", side_effect=fixtures.fake_get_json), None),
            (mock.patch.object(launcher, "_spawn", side_effect=self.spawned.append), None),
            (mock.patch.object(launcher.webbrowser, "open", side_effect=lambda url, new=0: self.opened.append(url) or True), None),
            (mock.patch.object(launcher, "find_vlc", return_value="C:/VLC/vlc.exe"), None),
            # These tests play in VLC; the built-in player has tests of its own.
            (mock.patch.object(player, "find_mpv", return_value=""), None),
        ]:
            target.start()
            self.addCleanup(target.stop)
        metadata._provider_cache._data.clear()

    def call(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        hdrs = {"Host": f"127.0.0.1:{self.port}", "X-Watchlist": "1", "Content-Type": "application/json"}
        hdrs.update(headers or {})
        conn.request(method, path, json.dumps(body) if body is not None else None, hdrs)
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        try:
            return resp.status, json.loads(raw)
        except ValueError:
            return resp.status, raw

    def add(self, type_, source, source_id):
        status, item = self.call("POST", "/api/items", {"type": type_, "source": source, "source_id": source_id})
        self.assertEqual(status, 200, item)
        return item


class ServerTest(ServerBase):
    def test_ui_is_served(self):
        status, body = self.call("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"WATCHLIST", body)
        status, _ = self.call("GET", "/../config.py")
        self.assertEqual(status, 404)

    def test_requests_from_other_sites_are_refused(self):
        status, _ = self.call("GET", "/api/state", headers={"Host": "evil.example:80"})
        self.assertEqual(status, 403)
        status, _ = self.call("POST", "/api/items", {"type": "tv"}, headers={"X-Watchlist": ""})
        self.assertEqual(status, 403)

    def test_add_search_and_duplicates(self):
        status, data = self.call("GET", "/api/search?type=tv&q=breaking")
        self.assertEqual(data["results"][0]["source_id"], "tv/1396")
        self.assertIsNone(data["results"][0]["in_list"])
        item = self.add("tv", "tmdb", "tv/1396")
        self.assertEqual(item["genres"], ["Drama", "Crime"])
        again = self.add("tv", "tmdb", "tv/1396")
        self.assertEqual(again["id"], item["id"])
        _, data = self.call("GET", "/api/search?type=tv&q=breaking")
        self.assertEqual(data["results"][0]["in_list"], item["id"])
        _, state = self.call("GET", "/api/state")
        self.assertEqual(len(state["items"]), 1)
        _, season = self.call("GET", f"/api/items/{item['id']}/season/1")
        self.assertEqual(season["episodes"][0]["name"], "Pilot")

    def test_local_play_with_subtitles_and_queue(self):
        item = self.add("tv", "tmdb", "tv/1396")
        touch(self.media, "Breaking Bad/Season 1/Breaking.Bad.S01E01.mkv")
        touch(self.media, "Breaking Bad/Season 1/Breaking.Bad.S01E01.srt")
        touch(self.media, "Breaking Bad/Season 1/Breaking.Bad.S01E02.mkv")
        folder = str(self.media / "Breaking Bad")
        status, _ = self.call("PUT", f"/api/items/{item['id']}", {"local_folder": folder + "/missing"})
        self.assertEqual(status, 400)
        status, updated = self.call("PUT", f"/api/items/{item['id']}", {"local_folder": folder})
        self.assertEqual(updated["local_folder"], folder)

        _, info = self.call("GET", f"/api/items/{item['id']}/local?season=1&episode=1")
        self.assertEqual(info["target"]["name"], "Breaking.Bad.S01E01.mkv")
        self.assertEqual(info["available"], ["1:1", "1:2"])
        sub = info["subtitles"][0]["path"]

        status, res = self.call("POST", f"/api/items/{item['id']}/play",
                                {"path": info["target"]["path"], "subtitle": sub, "season": 1, "episode": 1})
        self.assertEqual(status, 200, res)
        self.assertEqual(res["queued"], 1)
        cmd = self.spawned[-1]
        self.assertEqual(cmd[:3], ["C:/VLC/vlc.exe", "--no-random", "--no-playlist-enqueue"])
        self.assertEqual(cmd[3:5], [info["target"]["path"], f":sub-file={sub}"])
        self.assertTrue(cmd[5].endswith("Breaking.Bad.S01E02.mkv"))

        # Files outside the title's folder can't be launched.
        outside = touch(self.media, "elsewhere.mkv")
        status, _ = self.call("POST", f"/api/items/{item['id']}/play", {"path": str(outside)})
        self.assertEqual(status, 400)

    def test_progress_and_watched(self):
        item = self.add("tv", "tmdb", "tv/1396")
        self.call("PUT", f"/api/items/{item['id']}", {"progress": {"season": 1, "episode": 7}})
        _, res = self.call("POST", f"/api/items/{item['id']}/watched")
        self.assertEqual(res["item"]["progress"], {"season": 2, "episode": 1})
        self.call("PUT", f"/api/items/{item['id']}", {"progress": {"season": 2, "episode": 13}})
        _, res = self.call("POST", f"/api/items/{item['id']}/watched")
        self.assertTrue(res["finished"])

    def test_online_netflix_then_fallback(self):
        show = self.add("tv", "tmdb", "tv/1396")
        _, res = self.call("POST", f"/api/items/{show['id']}/online")
        self.assertEqual(res["service"], "Netflix")
        self.assertEqual(self.opened[-1], "https://www.netflix.com/search?q=Breaking+Bad")

        # On Max in the US but only for rent in Pakistan -> everythingmoe.
        movie = self.add("movie", "tmdb", "movie/27205")
        _, res = self.call("POST", f"/api/items/{movie['id']}/online")
        self.assertEqual(res["service"], "everythingmoe.com")
        self.assertEqual(self.opened[-1], "https://everythingmoe.com")
        self.app.settings.update({"region": "US"})
        _, res = self.call("POST", f"/api/items/{movie['id']}/online")
        self.assertEqual(res["service"], "HBO Max")

        anime = self.add("anime", "mal", "52991")
        self.app.settings.update({"region": "PK"})
        _, res = self.call("POST", f"/api/items/{anime['id']}/online")
        self.assertEqual(res["service"], "everythingmoe.com")
        self.assertEqual(res["services"], ["Amazon Prime Video"])

    def test_online_uses_your_own_site_for_each_type(self):
        listed = self.app.settings.get()["services"]
        self.app.settings.update({"services": listed + [
            {"name": "My anime site", "url": "https://anime.example/search?keyword={title}", "types": ["anime"]},
            {"name": "", "url": "https://films.example/find/{title}", "types": ["tv", "movie"]}]})
        anime = self.add("anime", "mal", "52991")
        _, res = self.call("POST", f"/api/items/{anime['id']}/online")
        self.assertEqual((res["service"], res["kind"]), ("My anime site", "search"))
        self.assertEqual(self.opened[-1], "https://anime.example/search?keyword=Frieren%3A+Beyond+Journey%27s+End")
        movie = self.add("movie", "tmdb", "movie/27205")
        _, res = self.call("POST", f"/api/items/{movie['id']}/online")
        self.assertEqual((res["service"], self.opened[-1]), ("films.example", "https://films.example/find/Inception"))
        status, _ = self.call("PUT", "/api/settings", {"services": [{"name": "x", "url": "ftp://nope"}]})
        self.assertEqual(status, 400)

    def test_your_list_of_services(self):
        show = self.add("tv", "tmdb", "tv/1396")
        _, opts = self.call("GET", f"/api/items/{show['id']}/online-options")
        self.assertEqual([(o["name"], o["has_it"]) for o in opts["options"]], [("Netflix", True), ("HBO Max", False)])
        # Pick one yourself, even one that doesn't have it.
        hbo = opts["options"][1]["id"]
        _, res = self.call("POST", f"/api/items/{show['id']}/online", {"service": hbo})
        self.assertEqual(self.opened[-1], "https://play.hbomax.com/search?q=Breaking+Bad")
        # Netflix removed, a new service added in front, one switched off.
        _, presets = self.call("GET", "/api/services/presets")
        crunchy = next(p for p in presets["presets"] if p["name"] == "Crunchyroll")
        services = self.app.settings.get()["services"]
        services = [dict(crunchy, types=["anime"])] + [dict(x, on=False) for x in services if x["name"] == "HBO Max"]
        status, _ = self.call("PUT", "/api/settings", {"services": services})
        self.assertEqual(status, 200)
        _, opts = self.call("GET", f"/api/items/{show['id']}/online-options")
        self.assertEqual(opts["options"], [])                                  # Crunchyroll: anime only
        _, res = self.call("POST", f"/api/items/{show['id']}/online")
        self.assertEqual(res["kind"], "fallback")

    def test_old_settings_become_the_list(self):
        from watchlist.config import Settings, write_json
        path = Path(self.tmp.name) / "old.json"
        write_json(path, {"anime_site_url": "https://anime.example/s?q={title}", "region": "TH"})
        names = [(x["name"], x["types"], bool(x["check"])) for x in Settings(path).get()["services"]]
        self.assertEqual(names, [("Netflix", ["anime", "tv", "movie"], True), ("HBO Max", ["anime", "tv", "movie"], True),
                                 ("anime.example", ["anime"], False)])

    def test_online_without_token_goes_straight_to_fallback(self):
        movie = self.add("movie", "tmdb", "movie/27205")
        self.app.settings.update({"tmdb_api_key": ""})
        _, res = self.call("POST", f"/api/items/{movie['id']}/online")
        self.assertEqual(res["service"], "everythingmoe.com")
        self.assertIn("No TMDB token", res["message"])

    def test_links_open_in_the_chosen_browser(self):
        opera = touch(self.media, "Opera GX/launcher.exe")
        self.app.settings.update({"browser_path": str(opera)})
        _, state = self.call("GET", "/api/state")
        self.assertEqual(state["status"]["browser"], str(opera))
        show = self.add("tv", "tmdb", "tv/1396")
        _, res = self.call("POST", f"/api/items/{show['id']}/online")
        self.assertEqual(self.spawned[-1], [str(opera), "https://www.netflix.com/search?q=Breaking+Bad"])
        self.call("POST", "/api/open", {"url": "https://www.youtube.com/watch?v=x"})
        self.assertEqual(self.spawned[-1], [str(opera), "https://www.youtube.com/watch?v=x"])
        self.assertEqual(self.opened, [], "the system default browser isn't used")

    def test_section_folders_hold_a_title_once(self):
        anime = self.add("anime", "mal", "52991")
        movie = self.add("movie", "tmdb", "movie/27205")
        _, shounen = self.call("POST", "/api/collections", {"name": "Shounen", "type": "anime"})
        _, binge = self.call("POST", "/api/collections", {"name": "To binge", "type": "anime"})
        _, global_col = self.call("POST", "/api/collections", {"name": "Favourites"})
        status, _ = self.call("POST", "/api/collections", {"name": "Shounen", "type": "movie"})
        self.assertEqual(status, 200, "the same name is fine in another section")
        self.call("PUT", f"/api/items/{anime['id']}/collections", {"collections": [global_col["id"]]})
        _, item = self.call("PUT", f"/api/items/{anime['id']}/folder", {"folder": shounen["id"]})
        _, item = self.call("PUT", f"/api/items/{anime['id']}/folder", {"folder": binge["id"]})
        self.assertEqual(sorted(item["collections"]), sorted([global_col["id"], binge["id"]]))
        _, item = self.call("PUT", f"/api/items/{anime['id']}/folder", {"folder": ""})
        self.assertEqual(item["collections"], [global_col["id"]])
        status, _ = self.call("PUT", f"/api/items/{movie['id']}/folder", {"folder": shounen["id"]})
        self.assertEqual(status, 404, "an anime folder can't hold a movie")
        status, _ = self.call("POST", "/api/collections", {"name": "x", "type": "music"})
        self.assertEqual(status, 400)

    def test_not_working_flag_and_picking_a_file(self):
        movie = self.add("movie", "tmdb", "movie/27205")
        f = touch(self.media, "Films/Inception.mkv")
        _, item = self.call("PUT", f"/api/items/{movie['id']}", {"local_file": str(f)})
        self.assertEqual(item["local_folder"], str(f.parent), "the file's folder is remembered too")
        self.assertFalse(item["local_broken"])
        _, item = self.call("PUT", f"/api/items/{movie['id']}", {"local_broken": True})
        self.assertTrue(item["local_broken"])
        other = touch(self.media, "Other/Inception 1080p.mkv")
        _, item = self.call("PUT", f"/api/items/{movie['id']}", {"local_file": str(other)})
        self.assertFalse(item["local_broken"], "choosing a new file clears the flag")
        self.assertEqual(item["local_folder"], str(other.parent))
        status, _ = self.call("PUT", f"/api/items/{movie['id']}", {"local_file": str(self.media / "nope.mkv")})
        self.assertEqual(status, 400)

    def test_collections(self):
        item = self.add("movie", "tmdb", "movie/27205")
        _, col = self.call("POST", "/api/collections", {"name": "Weekend binge"})
        status, _ = self.call("POST", "/api/collections", {"name": "weekend BINGE"})
        self.assertEqual(status, 400)
        _, updated = self.call("PUT", f"/api/items/{item['id']}/collections", {"collections": [col["id"], "bogus"]})
        self.assertEqual(updated["collections"], [col["id"]])
        self.call("DELETE", f"/api/collections/{col['id']}")
        _, state = self.call("GET", "/api/state")
        self.assertEqual(state["items"][0]["collections"], [])
        self.assertEqual(state["collections"], [])

    def test_remove_and_settings_validation(self):
        item = self.add("movie", "tmdb", "movie/27205")
        status, _ = self.call("DELETE", f"/api/items/{item['id']}")
        self.assertEqual(status, 200)
        status, _ = self.call("GET", f"/api/items/{item['id']}")
        self.assertEqual(status, 404)
        status, err = self.call("PUT", "/api/settings", {"region": "Pakistan"})
        self.assertEqual(status, 400)
        status, err = self.call("PUT", "/api/settings", {"fallback_url": "javascript:alert(1)"})
        self.assertEqual(status, 400)
        status, _ = self.call("POST", "/api/open", {"url": "file:///C:/Windows"})
        self.assertEqual(status, 400)

    def test_list_survives_restart(self):
        self.add("anime", "mal", "52991")
        again = App(self.app.data_dir)
        self.assertEqual(again.store.list()[0]["title"], "Frieren: Beyond Journey's End")
        self.assertEqual(again.settings.get()["mal_client_id"], "cid")


if __name__ == "__main__":
    unittest.main()

    def test_moving_a_file_that_isnt_there(self):
        item = self.add("tv", "tmdb", "tv/1396")
        status, data = self.call("POST", "/api/library/route", {"rel": "nope.mkv", "item": item["id"]})
        self.assertEqual(status, 400, data)
