import json
import tempfile
import unittest
from pathlib import Path

from watchlist.server import App
from watchlist.store import new_item


def movie(title="Inception"):
    return new_item("movie", {"source": "tmdb", "source_id": f"movie/{title}", "title": title})


class SyncTest(unittest.TestCase):
    """Two computers ("win" and "mac") sharing one cloud folder."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.cloud = root / "Google Drive" / "Watchlist"
        self.cloud.mkdir(parents=True)
        self.win = App(root / "win")
        self.mac = App(root / "mac")

    def connect(self, *apps):
        for app in apps:
            app.set_sync_folder(str(self.cloud))

    def test_changes_show_up_on_the_other_computer(self):
        self.connect(self.win, self.mac)
        item = self.win.store.add(movie())
        self.assertEqual([i["title"] for i in self.mac.store.list()], ["Inception"])
        self.mac.store.update(item["id"], {"progress": {"season": 1, "episode": 4}})
        self.assertEqual(self.win.store.get(item["id"])["progress"]["episode"], 4)
        self.win.store.delete(item["id"])
        self.assertEqual(self.mac.store.list(), [])

    def test_deleting_wins_even_if_the_other_clock_is_ahead(self):
        self.connect(self.win, self.mac)
        item = self.mac.store.add(movie())
        # The Mac's clock is 10 minutes ahead of the Windows PC's.
        self.mac.store._items[item["id"]]["modified"] += 600
        self.mac.store._save()
        self.win.store.delete(item["id"])
        self.assertEqual(self.mac.store.list(), [])

    def test_version_changes_when_the_other_computer_writes(self):
        self.connect(self.win, self.mac)
        before = self.mac.store.current_version()
        self.win.store.add(movie())
        self.assertGreater(self.mac.store.current_version(), before)

    def test_show_folders_stay_on_each_computer(self):
        self.connect(self.win, self.mac)
        item = self.win.store.add(movie())
        self.win.store.update(item["id"], {"local_folder": r"D:\Movies\Inception", "local_broken": True})
        on_mac = self.mac.store.get(item["id"])
        self.assertEqual(on_mac["local_folder"], "")
        self.assertFalse(on_mac["local_broken"])
        self.mac.store.update(item["id"], {"local_folder": "/Users/me/Movies/Inception"})
        self.assertEqual(self.win.store.get(item["id"])["local_folder"], r"D:\Movies\Inception")
        shared = (self.cloud / "watchlist.json").read_text(encoding="utf-8")
        self.assertNotIn("Movies", shared, "folders on each computer never go into the shared file")

    def test_turning_sync_on_merges_both_lists(self):
        self.win.store.add(movie("Arrival"))
        self.mac.store.add(movie("Heat"))
        self.connect(self.win)
        self.connect(self.mac)
        titles = sorted(i["title"] for i in self.win.store.list())
        self.assertEqual(titles, ["Arrival", "Heat"])
        self.assertEqual(sorted(i["title"] for i in self.mac.store.list()), titles)

    def test_edits_made_before_seeing_each_other_are_both_kept(self):
        self.connect(self.win, self.mac)
        a = self.win.store.add(movie("Arrival"))
        self.mac.store.list()
        # The cloud app hasn't delivered the files yet: each computer edits its own copy.
        self.mac.store._seen = self.mac.store._signature()
        self.win.store.update(a["id"], {"progress": {"season": 1, "episode": 2}})
        self.mac.store.add(movie("Heat"))
        titles = sorted(i["title"] for i in self.win.store.list())
        self.assertEqual(titles, ["Arrival", "Heat"])
        self.assertEqual(self.win.store.get(a["id"])["progress"]["episode"], 2)

    def test_conflicted_copies_from_cloud_apps_are_merged(self):
        self.connect(self.win)
        self.win.store.add(movie("Arrival"))
        extra = movie("Heat")
        (self.cloud / "watchlist (mac's conflicted copy 2026-10-26).json").write_text(
            json.dumps({"items": [extra], "collections": [], "deleted": {}}), encoding="utf-8")
        self.assertEqual(sorted(i["title"] for i in self.win.store.list()), ["Arrival", "Heat"])

    def test_deleted_folders_disappear_everywhere(self):
        self.connect(self.win, self.mac)
        item = self.win.store.add(movie())
        col = self.win.store.add_collection("Rewatch", "movie")
        self.win.store.move_to_folder(item["id"], col["id"])
        self.assertEqual(self.mac.store.get(item["id"])["collections"], [col["id"]])
        self.mac.store.delete_collection(col["id"])
        self.assertEqual(self.win.store.collections(), [])
        self.assertEqual(self.win.store.get(item["id"])["collections"], [])

    def test_half_written_file_is_never_overwritten_with_nothing(self):
        self.connect(self.win)
        self.win.store.add(movie())
        (self.cloud / "watchlist.json").write_text('{"items": [', encoding="utf-8")
        self.win.store.add(movie("Heat"))
        data = json.loads((self.cloud / "watchlist.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(i["title"] for i in data["items"]), ["Heat", "Inception"])

    def test_missing_sync_folder_falls_back_and_recovers(self):
        self.connect(self.win)
        self.win.store.add(movie())
        moved = self.cloud.with_name("elsewhere")
        self.cloud.rename(moved)
        again = App(self.win.data_dir)
        self.assertIn("isn't available", again.sync_problem)
        moved.rename(self.cloud)
        again.retry_sync()
        self.assertEqual(again.sync_problem, "")
        self.assertEqual([i["title"] for i in again.store.list()], ["Inception"])

    def test_old_lists_move_show_folders_out_of_the_shared_file(self):
        data_dir = Path(self.tmp.name) / "old"
        data_dir.mkdir()
        old = movie()
        old["local_folder"] = r"D:\Movies"
        (data_dir / "watchlist.json").write_text(json.dumps({"version": 1, "items": [old]}), encoding="utf-8")
        app = App(data_dir)
        self.assertEqual(app.store.get(old["id"])["local_folder"], r"D:\Movies")
        self.assertNotIn("local_folder", (data_dir / "watchlist.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
