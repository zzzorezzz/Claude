import tempfile
import unittest
from pathlib import Path

from tests.test_scanner import touch
from watchlist.server import App
from watchlist.store import new_item


def add(app, kind, title, **meta):
    fmt = "movie" if kind == "movie" else "series"
    meta.setdefault("seasons", [] if fmt == "movie" else [{"number": 1, "episodes": 12}])
    return app.store.add(new_item(kind, {"source": "mal" if kind == "anime" else "tmdb",
                                         "source_id": title, "title": title, "format": fmt, **meta}))


class LibraryTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.dl = root / "Downloads"
        self.app = App(root / "data")
        self.frieren = add(self.app, "anime", "Frieren: Beyond Journey's End",
                           aliases=["Sousou no Frieren", "Frieren: Beyond Journey's End"],
                           seasons=[{"number": 1, "episodes": 28}])
        self.spy = add(self.app, "anime", "Spy x Family")
        self.spy2 = add(self.app, "anime", "Spy x Family Season 2")
        self.bb = add(self.app, "tv", "Breaking Bad",
                      seasons=[{"number": 1, "episodes": 7}, {"number": 2, "episodes": 13}])
        self.inception = add(self.app, "movie", "Inception", year=2010)
        self.toy = add(self.app, "movie", "Toy Story", year=1995)
        for rel in [
            "[SubsPlease] Sousou no Frieren - 01 (1080p) [AB12CD34].mkv",
            "[SubsPlease] Sousou no Frieren - 02 (1080p) [AB12CD34].mkv",
            "[SubsPlease] Sousou no Frieren - 02 (1080p) [AB12CD34].en.srt",
            "[SubsPlease] Sousou no Frieren - NCOP (1080p).mkv",
            "Frierne - 03.mkv",
            "Spy x Family/S01E01.mkv",
            "Spy x Family Season 2 - 04 [1080p].mkv",
            "Breaking Bad (2008)/Season 1/Breaking.Bad.S01E01.720p.mkv",
            "Breaking Bad (2008)/Season 1/Subs/Breaking.Bad.S01E01.720p/2_English.srt",
            "Breaking Bad (2008)/Season 2/Breaking.Bad.S02E03.720p.mkv",
            "Inception.2010.1080p.BluRay.x264/Inception.2010.1080p.BluRay.x264.mkv",
            "Inception.2010.1080p.BluRay.x264/Inception.2010.1080p.BluRay.x264.srt",
            "Toy.Story.3.2010.1080p.mkv",
            "Some.Random.Show.S01E01.mkv",
            "mystery-subtitle.srt",
        ]:
            touch(self.dl, rel)
        self.app.settings.update({"downloads_folder": str(self.dl)})
        self.lib = self.app.library
        self.lib.scan(force=True)

    def files_of(self, item):
        return sorted(Path(v["rel"]).name for v in self.lib.videos_for(item["id"]))

    def question(self, kind, contains):
        return next(q for q in self.lib.state()["questions"]
                    if q["kind"] == kind and any(contains in f for f in q["files"]))

    def test_files_are_connected_to_the_right_titles(self):
        self.assertEqual(self.files_of(self.frieren), [
            "[SubsPlease] Sousou no Frieren - 01 (1080p) [AB12CD34].mkv",
            "[SubsPlease] Sousou no Frieren - 02 (1080p) [AB12CD34].mkv",
            "[SubsPlease] Sousou no Frieren - NCOP (1080p).mkv"])
        self.assertEqual(self.files_of(self.spy), ["S01E01.mkv"], "a show folder names its files")
        self.assertEqual(self.files_of(self.spy2), ["Spy x Family Season 2 - 04 [1080p].mkv"])
        self.assertEqual(self.files_of(self.bb), ["Breaking.Bad.S01E01.720p.mkv", "Breaking.Bad.S02E03.720p.mkv"])
        self.assertEqual(self.files_of(self.inception), ["Inception.2010.1080p.BluRay.x264.mkv"])
        self.assertEqual(self.files_of(self.toy), [], "Toy Story 3 isn't Toy Story")
        state = self.lib.state()
        self.assertEqual(state["items"][self.bb["id"]]["episodes"], ["1:1", "2:3"])
        self.assertEqual(state["items"][self.spy2["id"]]["episodes"], ["1:4"])

    def test_subtitles_are_paired(self):
        frieren2 = next(v for v in self.lib.videos_for(self.frieren["id"]) if "- 02" in v["name"])
        self.assertEqual([s["name"] for s in self.lib.subtitles_for(frieren2["path"])],
                         ["[SubsPlease] Sousou no Frieren - 02 (1080p) [AB12CD34].en.srt"])
        bb1 = next(v for v in self.lib.videos_for(self.bb["id"]) if "S01E01" in v["name"])
        self.assertEqual([s["name"] for s in self.lib.subtitles_for(bb1["path"])], ["2_English.srt"])

    def test_unsure_files_become_questions(self):
        typo = self.question("title", "Frierne")
        self.assertEqual(typo["reason"], "unsure")
        self.assertEqual(typo["candidates"][0], self.frieren["id"])
        unknown = self.question("title", "Random.Show")
        self.assertEqual(unknown["reason"], "unknown")
        toy = self.question("title", "Toy.Story.3")
        self.assertEqual(toy["reason"], "unknown")
        ncop = self.question("episode", "NCOP")
        self.assertEqual(ncop["item"], self.frieren["id"])
        sub = self.question("subtitle", "mystery-subtitle")
        self.assertEqual(sub["kind"], "subtitle")

    def test_answers_are_remembered(self):
        typo = self.question("title", "Frierne")
        self.lib.answer({"kind": "title", "key": typo["key"], "item": self.frieren["id"]})
        self.assertIn("Frierne - 03.mkv", self.files_of(self.frieren))
        ncop = self.question("episode", "NCOP")
        self.lib.answer({"kind": "episode", "key": ncop["key"], "item": "ignore"})
        self.assertNotIn("[SubsPlease] Sousou no Frieren - NCOP (1080p).mkv", self.files_of(self.frieren))
        unknown = self.question("title", "Random.Show")
        self.lib.answer({"kind": "title", "key": unknown["key"], "item": "ignore"})
        sub = self.question("subtitle", "mystery-subtitle")
        target = next(v for v in self.lib.videos_for(self.inception["id"]))
        self.lib.answer({"kind": "subtitle", "key": sub["key"], "video": target["rel"]})
        self.assertIn("mystery-subtitle.srt", [s["name"] for s in self.lib.subtitles_for(target["path"])])
        remaining = {q["key"] for q in self.lib.state()["questions"]}
        self.assertNotIn(typo["key"], remaining)
        # A fresh start (new app, same data folder) still knows the answers.
        again = App(self.app.data_dir)
        again.library.scan(force=True)
        self.assertIn("Frierne - 03.mkv", sorted(Path(v["rel"]).name for v in again.library.videos_for(self.frieren["id"])))

    def test_new_title_in_the_list_resolves_unknown_files(self):
        random_show = add(self.app, "tv", "Some Random Show")
        self.lib.scan()
        self.assertEqual(self.files_of(random_show), ["Some.Random.Show.S01E01.mkv"])

    def test_watching_uses_the_downloads(self):
        info = self.app.local_info(self.bb, 2, 3, "")
        self.assertTrue(info["exists"])
        self.assertEqual(Path(info["target"]["path"]).name, "Breaking.Bad.S02E03.720p.mkv")
        self.assertEqual(info["available"], ["1:1", "2:3"])
        self.assertTrue(self.app.playable(self.bb, info["target"]["path"]))
        outside = touch(Path(self.app.data_dir), "elsewhere.mkv")
        self.assertFalse(self.app.playable(self.bb, str(outside)))
        movie = self.app.local_info(self.inception, 1, 1, "")
        self.assertEqual([s["name"] for s in movie["subtitles"]], ["Inception.2010.1080p.BluRay.x264.srt"])


if __name__ == "__main__":
    unittest.main()
