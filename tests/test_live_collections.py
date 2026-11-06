"""A shared collection's link stays the same, and what's added to it reaches whoever has it."""
import json
from pathlib import Path
from unittest import mock

from watchlist import launcher, livecollections, share
from tests.test_server import ServerBase


class LiveCollectionTest(ServerBase):
    def setUp(self):
        super().setUp()
        _, self.col = self.call("POST", "/api/collections", {"name": "Weekend"})
        self.bb = self.add("tv", "tmdb", "tv/1396")
        self.put_in(self.bb)

    def put_in(self, item):
        now = self.app.store.get(item["id"])["collections"]
        self.call("PUT", f"/api/items/{item['id']}/collections", {"collections": now + [self.col["id"]]})

    def share(self):
        status, r = self.call("POST", f"/api/collections/{self.col['id']}/share")
        self.assertEqual(status, 200, r)
        return r

    def test_the_link_stays_the_same(self):
        first = self.share()["code"]
        self.put_in(self.add("movie", "tmdb", "movie/27205"))
        again = self.share()
        self.assertEqual(again["code"], first)
        self.assertEqual(again["count"], 2)
        self.assertEqual(len(self.relays.fetch(livecollections.read_code(first))["data"]["items"]), 2)

    def test_whoever_has_it_gets_what_is_added_and_taken_out(self):
        code = self.share()["code"]
        # Someone else (a list without these titles) adds it from the link.
        self.app.store.delete(self.bb["id"])
        status, peek = self.call("POST", "/api/shared/peek", {"code": code})
        self.assertEqual((status, peek["live"] is not None, len(peek["items"])), (200, True, 1))
        status, done = self.call("POST", "/api/shared/import", {"code": code})
        self.assertEqual((status, done["live"]), (200, True), done)
        mine = done["collection"]["id"]
        in_mine = lambda: sorted(i["title"] for i in self.app.store.list() if mine in i["collections"])  # noqa: E731
        self.assertEqual(in_mine(), ["Breaking Bad"])
        # The sharer adds a film (straight onto the relay here: it's their app's copy).
        key = livecollections.read_code(code)
        data = self.relays.fetch(key)["data"]
        data["items"].append({"t": "Inception", "y": 2010, "ty": "movie", "s": "tmdb", "i": "movie/27205", "p": "", "r": None})
        self.relays.publish(key, data)
        status, r = self.call("POST", f"/api/collections/{mine}/follow")
        self.assertEqual((status, r["added"], r["removed"]), (200, 1, 0), r)
        self.assertEqual(in_mine(), ["Breaking Bad", "Inception"])
        # Nothing new: nothing changes.
        _, r = self.call("POST", f"/api/collections/{mine}/follow")
        self.assertEqual((r["added"], r["removed"]), (0, 0))
        # The sharer takes Breaking Bad out: it leaves the collection (and, only ever in it, goes).
        data["items"] = [x for x in data["items"] if x["i"] != "tv/1396"]
        self.relays.publish(key, data)
        _, r = self.call("POST", f"/api/collections/{mine}/follow")
        self.assertEqual((r["added"], r["removed"]), (0, 1))
        self.assertEqual(in_mine(), ["Inception"])

    def test_a_title_you_took_out_stays_out(self):
        code = self.share()["code"]
        _, done = self.call("POST", "/api/shared/import", {"code": code})
        mine = done["collection"]["id"]
        self.call("PUT", f"/api/items/{self.bb['id']}/collections",
                  {"collections": [c for c in self.app.store.get(self.bb["id"])["collections"] if c != mine]})
        self.put_in(self.add("movie", "tmdb", "movie/27205"))
        self.share()                                  # (the sharer's app puts the new version up)
        _, r = self.call("POST", f"/api/collections/{mine}/follow")
        self.assertEqual(r["added"], 1)
        self.assertNotIn(mine, self.app.store.get(self.bb["id"])["collections"])

    def test_stopped_sharing(self):
        code = self.share()["code"]
        _, done = self.call("POST", "/api/shared/import", {"code": code})
        status, r = self.call("POST", f"/api/collections/{self.col['id']}/share", {"stop": True})
        self.assertEqual((status, r["collection"]["share_key"]), (200, ""))
        self.assertTrue(livecollections.read_code(self.share()["code"]) != livecollections.read_code(code))
        import time
        for _ in range(50):
            if self.relays.fetch(livecollections.read_code(code))["data"].get("stopped"):
                break
            time.sleep(0.05)
        _, r = self.call("POST", f"/api/collections/{done['collection']['id']}/follow")
        self.assertTrue(r["stopped"])

    def test_a_file_follows_the_link(self):
        home = Path(self.tmp.name) / "home"
        (home / "Downloads").mkdir(parents=True)
        with mock.patch.object(Path, "home", return_value=home), mock.patch.object(launcher, "reveal"):
            _, r = self.call("POST", f"/api/collections/{self.col['id']}/save")
        saved = json.loads(Path(r["path"]).read_text(encoding="utf-8"))
        self.assertTrue(saved["live"].startswith("WLC3.") and saved["watchlist_collection"].startswith("WLC2."))
        _, done = self.call("POST", "/api/shared/import", {"file": r["path"]})
        self.assertTrue(done["live"])
        # The relays forgot it: the file's titles are still read.
        self.relays.kept.clear()
        _, peek = self.call("POST", "/api/shared/peek", {"file": r["path"]})
        self.assertEqual(len(peek["items"]), 1)

    def test_a_link_nobody_has_put_up(self):
        status, r = self.call("POST", "/api/shared/peek", {"code": livecollections.to_code(b"x" * 16)})
        self.assertEqual(status, 400)
        self.assertIn("not on the relays", r["error"])
