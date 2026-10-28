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
            "[SubsPlease] Sousou no Frieren [1080p].mkv",
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
        self.app.sorter.run_now()          # nothing is sorted until Sort now

    def files_of(self, item):
        return sorted(Path(v["rel"]).name for v in self.lib.videos_for(item["id"]))

    def question(self, kind, contains):
        return next(q for q in self.lib.state()["questions"]
                    if q["kind"] == kind and any(contains in f for f in q["files"]))

    def test_files_are_connected_to_the_right_titles(self):
        self.assertEqual(self.files_of(self.frieren), sorted([
            "[SubsPlease] Sousou no Frieren [1080p].mkv",
            "[SubsPlease] Sousou no Frieren - 01 (1080p) [AB12CD34].mkv",
            "[SubsPlease] Sousou no Frieren - 02 (1080p) [AB12CD34].mkv",
            "[SubsPlease] Sousou no Frieren - NCOP (1080p).mkv"]))
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
        unnumbered = self.question("episode", "Frieren [1080p]")
        self.assertEqual(unnumbered["item"], self.frieren["id"])
        ncop = next(v for v in self.lib.videos_for(self.frieren["id"]) if "NCOP" in v["name"])
        self.assertEqual(ncop["kind"], "extra", "openings are extras, not questions")
        sub = self.question("subtitle", "mystery-subtitle")
        self.assertEqual(sub["kind"], "subtitle")

    def test_answers_are_remembered(self):
        typo = self.question("title", "Frierne")
        self.lib.answer({"kind": "title", "key": typo["key"], "item": self.frieren["id"]})
        self.assertIn("Frierne - 03.mkv", self.files_of(self.frieren))
        cut = self.question("episode", "Frieren [1080p]")
        self.lib.answer({"kind": "episode", "key": cut["key"], "item": "ignore"})
        self.assertNotIn("[SubsPlease] Sousou no Frieren [1080p].mkv", self.files_of(self.frieren))
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
        group = self.question("title", "Random.Show")
        self.assertEqual((group["reason"], group["match"]), ("sure", random_show["id"]))   # suggested, not sorted
        self.assertEqual(self.files_of(random_show), [])
        self.app.sorter.run_now()
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




class KindsTest(unittest.TestCase):
    """Telling episodes, OVAs, specials, films and extras apart."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.dl = root / "Downloads"
        self.app = App(root / "data")
        self.aot = add(self.app, "anime", "Attack on Titan", aliases=["Shingeki no Kyojin"],
                       media_type="tv", seasons=[{"number": 1, "episodes": 25}])
        self.aot_ova = add(self.app, "anime", "Shingeki no Kyojin OVA", media_type="ova",
                           episodes_total=8, seasons=[{"number": 1, "episodes": 8}])
        self.ds = add(self.app, "anime", "Demon Slayer", aliases=["Kimetsu no Yaiba"], media_type="tv")
        self.ds_movie = add(self.app, "anime", "Demon Slayer Mugen Train", media_type="movie",
                            format="movie", year=2020, seasons=[])
        self.office = add(self.app, "tv", "The Office", media_type="tv",
                          seasons=[{"number": 0, "episodes": 30}, {"number": 1, "episodes": 6}])
        for rel, size in [
            ("Shingeki no Kyojin/[Group] Shingeki no Kyojin - 01 [1080p].mkv", 1),
            ("Shingeki no Kyojin/[Group] Shingeki no Kyojin - 01 [720p].mkv", 1),
            ("Shingeki no Kyojin/[Group] Shingeki no Kyojin OVA - 03 [1080p].mkv", 1),
            ("Shingeki no Kyojin/Extras/[Group] Shingeki no Kyojin - NCED 2 [1080p].mkv", 1),
            ("Shingeki no Kyojin/Extras/Creditless Opening.mkv", 1),
            ("Demon.Slayer.Mugen.Train.2020.1080p.BluRay.mkv", 2000 * 1024 * 1024),
            ("The.Office.US/The.Office.S00E02.Webisode.mkv", 1),
            ("The.Office.US/The.Office.S01E02.mkv", 1),
        ]:
            touch(self.dl, rel, size)
        self.app.settings.update({"downloads_folder": str(self.dl)})
        self.app.library.scan(force=True)
        self.app.sorter.run_now()

    def names(self, item, kind=None):
        return sorted(Path(v["rel"]).name for v in self.app.library.videos_for(item["id"])
                      if kind is None or v["kind"] == kind)

    def test_ovas_go_to_the_ova_entry(self):
        self.assertEqual(self.names(self.aot_ova), ["[Group] Shingeki no Kyojin OVA - 03 [1080p].mkv"])
        self.assertNotIn("[Group] Shingeki no Kyojin OVA - 03 [1080p].mkv", self.names(self.aot))

    def test_films_go_to_the_film_entry(self):
        self.assertEqual(self.names(self.ds_movie), ["Demon.Slayer.Mugen.Train.2020.1080p.BluRay.mkv"])
        self.assertEqual(self.names(self.ds), [])

    def test_extras_join_their_show(self):
        self.assertEqual(self.names(self.aot, "extra"),
                         ["Creditless Opening.mkv", "[Group] Shingeki no Kyojin - NCED 2 [1080p].mkv"])
        info = self.app.local_info(self.app.store.get(self.aot["id"]), 1, 1, "")
        self.assertEqual(len(info["extras"]), 2)
        self.assertNotIn("extra", {v.get("kind") for v in info["videos"]})

    def test_the_same_folder_shows_the_shows_other_parts(self):
        aot = self.app.local_info(self.app.store.get(self.aot["id"]), 1, 1, "")
        self.assertEqual([t["title"] for t in aot["family"]["titles"]], ["Shingeki no Kyojin OVA"])
        ova = self.app.local_info(self.app.store.get(self.aot_ova["id"]), 1, 3, "")
        self.assertEqual([t["title"] for t in ova["family"]["titles"]], ["Attack on Titan"])
        # Files straight in the downloads folder aren't "a show's folder".
        film = self.app.local_info(self.app.store.get(self.ds_movie["id"]), 1, 1, "")
        self.assertEqual(film["family"], {"titles": [], "files": []})
        # Which file each episode plays, and whether it can be moved.
        self.assertTrue(aot["by_episode"]["1:1"]["rel"].startswith("Shingeki no Kyojin/"))

    def test_the_better_copy_of_an_episode_plays(self):
        info = self.app.local_info(self.app.store.get(self.aot["id"]), 1, 1, "")
        self.assertIn("[1080p]", info["target"]["name"])

    def test_tv_specials_are_season_0(self):
        info = self.app.local_info(self.app.store.get(self.office["id"]), 0, 2, "")
        self.assertEqual(info["target"]["name"], "The.Office.S00E02.Webisode.mkv")
        self.assertIn("0:2", info["available"])
        self.assertIn("1:2", info["available"])


class SpeedTest(unittest.TestCase):
    def test_a_big_list_and_folder_sort_quickly(self):
        import time as _time
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        app = App(root / "data")
        for n in range(400):
            add(app, "anime" if n % 2 else "tv", f"Show Number {n} Of The Year")
        for n in range(0, 400, 4):
            for ep in range(1, 13):
                touch(root / "dl", f"Show Number {n} Of The Year/Show.Number.{n}.Of.The.Year.S01E{ep:02d}.mkv")
        for n in range(30):
            touch(root / "dl", f"Unknown Thing {n} - 01.mkv")
        app.settings.update({"downloads_folder": str(root / "dl")})
        started = _time.monotonic()
        app.library.scan(force=True)
        app.sorter.run_now()
        took = _time.monotonic() - started
        self.assertEqual(len(app.library.by_item), 100)
        self.assertLess(took, 8, f"sorting 1230 files against 400 titles took {took:.1f}s")


class SortOnDemandTest(unittest.TestCase):
    """Nothing is sorted until Sort now; what's sorted is never changed by it."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.dl = self.root / "Downloads"
        self.app = App(self.root / "data")
        self.frieren = add(self.app, "anime", "Frieren: Beyond Journey's End", aliases=["Sousou no Frieren"],
                           seasons=[{"number": 1, "episodes": 28}])
        self.bb = add(self.app, "tv", "Breaking Bad", seasons=[{"number": 1, "episodes": 7}])
        for n in (1, 2):
            touch(self.dl, f"[SubsPlease] Sousou no Frieren - {n:02d} (1080p).mkv")
        touch(self.dl, "Breaking.Bad.S01E01.mkv")
        self.app.settings.update({"downloads_folder": str(self.dl)})
        self.lib = self.app.library

    def names(self, item):
        return sorted(Path(v["rel"]).name for v in self.lib.videos_for(item["id"]))

    def test_season_folders_of_a_one_season_anime(self):
        shippuden = add(self.app, "anime", "Naruto: Shippuuden", aliases=["Naruto Shippuden"],
                        seasons=[{"number": 1, "episodes": 500}])
        for n in (250, 251):
            touch(self.dl, f"Naruto Shippuden/Season 12/[Group] Naruto Shippuden - {n} [1080p].mkv")
        self.lib.scan(force=True)
        self.app.sorter.run_now()
        self.assertEqual(len(self.names(shippuden)), 2)
        # The episodes the watch party offers are the ones the title page plays.
        self.assertEqual(self.lib.state()["items"][shippuden["id"]]["episodes"], ["1:250", "1:251"])
        info = self.app.local_info(self.app.store.get(shippuden["id"]), 1, 250)
        self.assertEqual(Path(info["target"]["path"]).name, "[Group] Naruto Shippuden - 250 [1080p].mkv")

    def test_new_files_wait_for_sort_now(self):
        self.lib.scan(force=True)
        self.assertEqual(self.names(self.frieren), [])
        state = self.lib.state()
        self.assertEqual(state["waiting"], 3)
        self.assertEqual({q["reason"] for q in state["questions"]}, {"sure"})
        report = self.app.sorter.run_now()
        self.assertEqual(report["sorted"], 2)                 # two groups: Frieren and Breaking Bad
        self.assertEqual(len(self.names(self.frieren)), 2)
        # A new episode later waits again, until the next Sort now.
        touch(self.dl, "[SubsPlease] Sousou no Frieren - 03 (1080p).mkv")
        self.lib.scan(force=True)
        self.assertEqual(len(self.names(self.frieren)), 2)
        self.app.sorter.run_now()
        self.assertEqual(len(self.names(self.frieren)), 3)

    def test_upgrading_keeps_what_was_sorted(self):
        # A library from before Sort now (no version), with a downloads folder already set.
        (self.root / "data" / "library.json").write_text('{"titles": {}, "files": {}}')
        again = App(self.root / "data")
        again.library.scan(force=True)
        self.assertEqual(len(again.library.videos_for(self.frieren["id"])), 2)
        self.assertEqual(again.library.answers["v"], 2)
        # From then on, new files wait.
        touch(self.dl, "[SubsPlease] Sousou no Frieren - 03 (1080p).mkv")
        again.library.scan(force=True)
        self.assertEqual(len(again.library.videos_for(self.frieren["id"])), 2)

    def test_moving_one_file(self):
        self.lib.scan(force=True)
        self.app.sorter.run_now()
        rel = "[SubsPlease] Sousou no Frieren - 02 (1080p).mkv"
        # To another title and episode.
        self.lib.route(rel, self.bb["id"], 1, 5)
        moved = next(v for v in self.lib.videos_for(self.bb["id"]) if v["rel"] == rel)
        self.assertEqual((moved["season"], moved["episodes"]), (1, [5]))
        # Out of every title: Sort now leaves it alone.
        self.lib.route(rel, "unsorted")
        self.app.sorter.run_now()
        held = next(q for q in self.lib.state()["questions"] if rel in q["files"])
        self.assertEqual((held["reason"], held["was"]), ("held", "Breaking Bad"))
        self.assertNotIn(rel, [v["rel"] for v in self.lib.videos_for(self.frieren["id"])])
        # Ignored.
        self.lib.route(rel, "ignore")
        self.assertFalse(any(rel in q["files"] for q in self.lib.state()["questions"]))
        with self.assertRaises(ValueError):
            self.lib.route("not-there.mkv", self.bb["id"])

    def test_removing_a_title_doesnt_bring_its_files_back(self):
        self.lib.scan(force=True)
        self.app.sorter.run_now()
        self.app.store.delete(self.bb["id"])
        self.lib.forget_item(self.bb["id"])
        again = add(self.app, "tv", "Breaking Bad", source_id="bb2", seasons=[{"number": 1, "episodes": 7}])
        self.app.sorter.run_now()
        self.assertEqual(self.names(again), [])               # still yours to sort
        held = next(q for q in self.lib.state()["questions"] if "Breaking.Bad.S01E01.mkv" in q["files"])
        self.assertEqual(held["reason"], "held")
        self.lib.answer({"kind": "title", "key": held["key"], "item": again["id"]})
        self.assertEqual(self.names(again), ["Breaking.Bad.S01E01.mkv"])

    def test_undone_stays_undone(self):
        self.lib.scan(force=True)
        self.app.sorter.run_now()
        entry = next(a for a in self.lib.state()["auto"] if a["item"] == self.frieren["id"])
        self.lib.undo(entry["key"])
        self.assertEqual(self.names(self.frieren), [])
        self.app.sorter.run_now()
        self.assertEqual(self.names(self.frieren), [])
        group = next(q for q in self.lib.state()["questions"] if q["key"] == entry["key"])
        self.assertEqual(group["reason"], "undone")

    def test_your_answers_are_never_changed(self):
        self.lib.scan(force=True)
        group = next(q for q in self.lib.state()["questions"] if "Breaking" in q["files"][0])
        self.lib.answer({"kind": "title", "key": group["key"], "item": self.frieren["id"]})   # on purpose
        self.app.sorter.run_now()
        self.assertIn("Breaking.Bad.S01E01.mkv", self.names(self.frieren))
