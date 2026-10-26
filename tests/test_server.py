import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from tests import fixtures
from tests.test_scanner import touch
from watchlist import launcher, metadata
from watchlist.server import App, make_server


class ServerTest(unittest.TestCase):
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
        self.assertEqual(cmd[0], "C:/VLC/vlc.exe")
        self.assertEqual(cmd[1:3], [info["target"]["path"], f":sub-file={sub}"])
        self.assertTrue(cmd[3].endswith("Breaking.Bad.S01E02.mkv"))

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
        self.assertEqual(res["service"], "everythingmoe")
        self.assertEqual(self.opened[-1], "https://everythingmoe.com")
        self.app.settings.update({"region": "US"})
        _, res = self.call("POST", f"/api/items/{movie['id']}/online")
        self.assertEqual(res["service"], "HBO Max")

        anime = self.add("anime", "mal", "52991")
        self.app.settings.update({"region": "PK"})
        _, res = self.call("POST", f"/api/items/{anime['id']}/online")
        self.assertEqual(res["service"], "everythingmoe")
        self.assertEqual(res["services"], ["Amazon Prime Video"])

    def test_online_without_token_goes_straight_to_fallback(self):
        movie = self.add("movie", "tmdb", "movie/27205")
        self.app.settings.update({"tmdb_api_key": ""})
        _, res = self.call("POST", f"/api/items/{movie['id']}/online")
        self.assertEqual(res["service"], "everythingmoe")
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
