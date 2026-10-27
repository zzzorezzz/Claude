import gzip
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from watchlist import importer, metadata
from watchlist.server import App

EXPORT = """<?xml version="1.0" encoding="UTF-8" ?>
<myanimelist>
  <myinfo><user_name>ali</user_name></myinfo>
  <anime><series_animedb_id>52991</series_animedb_id><series_title><![CDATA[Sousou no Frieren]]></series_title>
    <my_watched_episodes>5</my_watched_episodes><my_status>Watching</my_status></anime>
  <anime><series_animedb_id>5114</series_animedb_id><series_title><![CDATA[Fullmetal Alchemist: Brotherhood]]></series_title>
    <my_watched_episodes>0</my_watched_episodes><my_status>Completed</my_status></anime>
  <anime><series_animedb_id>21</series_animedb_id><series_title><![CDATA[One Piece]]></series_title>
    <my_watched_episodes>0</my_watched_episodes><my_status>Plan to Watch</my_status></anime>
</myanimelist>"""


def fake_details(settings, item_type, source, source_id):
    titles = {"52991": ("Frieren", 28), "5114": ("FMA Brotherhood", 64), "21": ("One Piece", 0)}
    if source_id not in titles:
        raise metadata.MetadataError("MyAnimeList doesn't have that title")
    title, eps = titles[source_id]
    return {"source": "mal", "source_id": source_id, "title": title, "format": "series",
            "episodes_total": eps or None, "seasons": [{"number": 1, "episodes": eps}]}


class ImportTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.app = App(self.root / "data")
        self.app.settings.update({"mal_client_id": "cid"})
        p = mock.patch.object(metadata, "details", side_effect=fake_details)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(importer, "PAUSE", 0)
        p.start()
        self.addCleanup(p.stop)

    def wait(self):
        for _ in range(200):
            if not self.app.importer.state()["running"]:
                return self.app.importer.state()
            time.sleep(0.01)
        self.fail("import didn't finish")

    def test_export_file_plain_and_gzipped(self):
        for name, data in [("list.xml", EXPORT.encode()), ("list.xml.gz", gzip.compress(EXPORT.encode()))]:
            path = self.root / name
            path.write_bytes(data)
            entries = importer.read_export(path)
            self.assertEqual([(e["mal_id"], e["status"], e["watched"]) for e in entries],
                             [("52991", "watching", 5), ("5114", "completed", 0), ("21", "plan_to_watch", 0)])
        bad = self.root / "bad.xml"
        bad.write_text("not xml")
        with self.assertRaises(ValueError):
            importer.read_export(bad)

    def test_import_sets_where_you_are(self):
        path = self.root / "list.xml"
        path.write_text(EXPORT)
        self.app.importer.start(importer.read_export(path), ["watching", "completed"])
        st = self.wait()
        self.assertEqual((st["added"], st["done"], st["total"]), (2, 2, 2))
        frieren = self.app.store.find_source("mal", "52991")
        self.assertEqual(frieren["progress"], {"season": 1, "episode": 6})
        fma = self.app.store.find_source("mal", "5114")
        self.assertEqual(fma["progress"]["episode"], 64)
        self.assertIsNone(self.app.store.find_source("mal", "21"))

        # Again: nothing doubled, progress never goes backwards.
        self.app.store.update(frieren["id"], {"progress": {"season": 1, "episode": 10}})
        self.app.importer.start(importer.read_export(path), ["watching"])
        st = self.wait()
        self.assertEqual((st["added"], st["already"]), (0, 1))
        self.assertEqual(self.app.store.find_source("mal", "52991")["progress"]["episode"], 10)
        self.assertEqual(len(self.app.store.list()), 2)

    def test_failures_are_listed(self):
        self.app.importer.start([{"mal_id": "999", "title": "Gone", "status": "watching", "watched": 0}], ["watching"])
        st = self.wait()
        self.assertEqual(st["added"], 0)
        self.assertIn("Gone", st["failed"][0])

    def test_user_list_pages(self):
        pages = {
            "first": {"data": [{"node": {"id": 1, "title": "A"}, "list_status": {"status": "watching", "num_episodes_watched": 3}}],
                      "paging": {"next": "https://api.myanimelist.net/v2/users/ali/animelist?offset=1"}},
            "second": {"data": [{"node": {"id": 2, "title": "B"}, "list_status": {"status": "plan_to_watch"}}], "paging": {}},
        }
        with mock.patch.object(metadata, "_get_json",
                               side_effect=lambda url, h, s: pages["second" if "offset" in url else "first"]):
            rows = metadata.MAL("cid").user_list("ali")
        self.assertEqual([(r["mal_id"], r["status"], r["watched"]) for r in rows],
                         [("1", "watching", 3), ("2", "plan_to_watch", 0)])
        with self.assertRaises(metadata.MetadataError):
            metadata.MAL("cid").user_list("bad name!")
