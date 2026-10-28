"""Anime always from MyAnimeList, the Watched list, and sharing collections."""

import urllib.parse
from pathlib import Path
from unittest import mock

from tests import fixtures, test_server
from tests.test_scanner import touch
from watchlist import launcher, metadata, share


class AnimeFromMalTest(test_server.ServerBase):
    def setUp(self):
        super().setUp()
        metadata._twin_cache._data.clear()
        # TMDB's Frieren, marked as Japanese animation the way TMDB does.
        patch = mock.patch.object(metadata, "_get_json", side_effect=self.fake)
        patch.start()
        self.addCleanup(patch.stop)

    @staticmethod
    def fake(url, headers=None, service="the server"):
        if urllib.parse.urlsplit(url).path == "/3/tv/209867":
            return dict(fixtures.TMDB_ANIME_TV, original_language="ja", first_air_date="2023-09-29")
        return fixtures.fake_get_json(url, headers, service)

    def test_adding_tmdbs_copy_of_an_anime_adds_myanimelists(self):
        item = self.add("tv", "tmdb", "tv/209867")
        self.assertEqual((item["source"], item["source_id"], item["type"]), ("mal", "52991", "anime"))
        again = self.add("tv", "tmdb", "tv/209867")
        self.assertEqual(again["id"], item["id"])

    def tmdb_copy(self, **extra):
        """An anime added from TMDB before this version."""
        info = metadata.TMDB("tok").details("tv/209867")
        from watchlist.store import new_item
        item = new_item("tv", dict(info, **extra))
        return self.app.store.add(item)

    def test_switching_an_old_tmdb_copy_keeps_files_and_progress(self):
        old = self.tmdb_copy()
        self.assertEqual([s["episodes"] for s in old["seasons"] if s["number"]], [7, 13])
        self.app.store.update(old["id"], {"progress": {"season": 2, "episode": 3},
                                          "positions": {"2:3": {"pos": 100, "dur": 1400, "at": 1}}})
        dl = Path(self.tmp.name) / "dl"
        touch(dl, "Frieren/Season 2/Frieren - 03.mkv")
        self.app.settings.update({"downloads_folder": str(dl)})
        self.app.library.scan(force=True)
        self.app.library.route("Frieren/Season 2/Frieren - 03.mkv", old["id"], 2, 3)

        status, data = self.call("GET", f"/api/items/{old['id']}/mal-matches")
        self.assertEqual(status, 200, data)
        self.assertEqual(data["results"][0]["source_id"], "52991")
        status, item = self.call("POST", f"/api/items/{old['id']}/use-mal", {"mal_id": "52991"})
        self.assertEqual(status, 200, item)
        self.assertEqual((item["id"], item["source"], item["type"]), (old["id"], "mal", "anime"))
        self.assertEqual(item["progress"], {"season": 1, "episode": 10})     # 7 in season 1, then 3
        self.assertIn("1:10", item["positions"])
        self.assertEqual(self.app.library.state()["items"][item["id"]]["episodes"], ["1:10"])

    def test_switching_to_one_already_in_your_list_makes_them_one(self):
        mal = self.add("anime", "mal", "52991")
        old = self.tmdb_copy()
        status, item = self.call("POST", f"/api/items/{old['id']}/use-mal", {"mal_id": "52991"})
        self.assertEqual(status, 200, item)
        self.assertEqual(item["id"], mal["id"])
        self.assertEqual([i["id"] for i in self.app.store.list()], [mal["id"]])


class WatchedTest(test_server.ServerBase):
    def test_marking_watched_and_back(self):
        item = self.add("tv", "tmdb", "tv/1396")
        status, data = self.call("PUT", f"/api/items/{item['id']}", {"watched": True})
        self.assertEqual(status, 200, data)
        self.assertTrue(data["watched_at"])
        first = data["watched_at"]
        _, data = self.call("PUT", f"/api/items/{item['id']}", {"watched": True})
        self.assertEqual(data["watched_at"], first)                         # since when, kept
        _, data = self.call("PUT", f"/api/items/{item['id']}", {"watched": False})
        self.assertIsNone(data["watched_at"])


class ShareTest(test_server.ServerBase):
    def make_collection(self):
        bb = self.add("tv", "tmdb", "tv/1396")
        frieren = self.add("anime", "mal", "52991")
        _, col = self.call("POST", "/api/collections", {"name": "Weekend"})
        for item in (bb, frieren):
            self.call("PUT", f"/api/items/{item['id']}/collections", {"collections": [col["id"]]})
        return col, bb, frieren

    def test_a_code_round_trip(self):
        col, bb, frieren = self.make_collection()
        status, r = self.call("POST", f"/api/collections/{col['id']}/share")
        self.assertEqual(status, 200, r)
        self.assertTrue(r["code"].startswith("WLC1."))
        self.assertEqual(r["count"], 2)
        data = share.read(f"here you go: {r['code']} enjoy")                  # pasted with other words
        self.assertEqual(data["name"], "Weekend")
        self.assertEqual({(i["source"], i["source_id"]) for i in data["items"]}, {("tmdb", "tv/1396"), ("mal", "52991")})
        self.assertNotIn("progress", str(data))

        # Someone else: the same titles aren't in their list yet.
        self.app.store.delete(bb["id"])
        status, peek = self.call("POST", "/api/shared/peek", {"code": r["code"]})
        self.assertEqual(status, 200, peek)
        self.assertEqual(sorted(bool(i["in_list"]) for i in peek["items"]), [False, True])
        status, done = self.call("POST", "/api/shared/import", {"code": r["code"]})
        self.assertEqual(status, 200, done)
        self.assertEqual((done["collection"]["name"], done["titles"], done["new"]), ("Weekend (shared)", 2, 1))
        cid = done["collection"]["id"]
        self.assertEqual(sorted(i["title"] for i in self.app.store.list() if cid in i["collections"]),
                         ["Breaking Bad", "Frieren: Beyond Journey's End"])

    def test_a_file(self):
        col, _, _ = self.make_collection()
        home = Path(self.tmp.name) / "home"
        (home / "Downloads").mkdir(parents=True)
        with mock.patch.object(Path, "home", return_value=home), mock.patch.object(launcher, "reveal") as reveal:
            status, r = self.call("POST", f"/api/collections/{col['id']}/save")
        self.assertEqual(status, 200, r)
        self.assertTrue(r["path"].endswith("Watchlist - Weekend.wlcollection"))
        reveal.assert_called_once()
        status, peek = self.call("POST", "/api/shared/peek", {"file": r["path"]})
        self.assertEqual((status, peek["name"], len(peek["items"])), (200, "Weekend", 2))

    def test_bad_codes(self):
        for code in ("hello", "WLC1.abc", "WLC1." + "A" * 40):
            status, data = self.call("POST", "/api/shared/peek", {"code": code})
            self.assertEqual(status, 400, (code, data))
        status, _ = self.call("POST", "/api/collections/nope/share")
        self.assertEqual(status, 404)
