import tempfile
import unittest
from pathlib import Path

from watchlist import scanner


def touch(root, rel, size=1000):
    p = Path(root, rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)
    return p


class ParseEpisodeTest(unittest.TestCase):
    def check(self, rel, season, episodes):
        self.assertEqual(scanner.parse_episode(tuple(rel.split("/"))), (season, tuple(episodes)), rel)

    def test_common_names(self):
        self.check("Breaking.Bad.S02E05.720p.BluRay.x264.mkv", 2, [5])
        self.check("The Office S01E01E02 1080p.mkv", 1, [1, 2])
        self.check("show.1x07.hdtv.avi", 1, [7])
        self.check("[SubsPlease] Sousou no Frieren - 05 (1080p) [ABCD1234].mkv", None, [5])
        self.check("[Group] One Piece - 1085v2 [1080p].mkv", None, [1085])
        self.check("Naruto Episode 12.mp4", None, [12])
        self.check("Show Name Ep 03.mkv", None, [3])

    def test_season_folders(self):
        self.check("Season 2/05 - The Title.mkv", 2, [5])
        self.check("Show/S03/E04.mkv", 3, [4])
        self.check("Season 1/Episode 9.mkv", 1, [9])

    def test_resolution_and_years_are_not_episodes(self):
        self.check("Movie.2010.1080p.mkv", None, [])
        self.check("Some Show - 2019.mkv", None, [])
        self.check("Thing x265 HEVC.mkv", None, [])


class FolderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_tv_show_with_subtitles(self):
        touch(self.root, "Season 1/Show.S01E01.mkv")
        touch(self.root, "Season 1/Show.S01E01.en.srt")
        touch(self.root, "Season 1/Show.S01E02.mkv")
        touch(self.root, "Season 1/Show.S01E02.srt")
        touch(self.root, "Season 2/Show.S02E01.mkv")
        touch(self.root, "Season 2/Subs/Show.S02E01/2_English.srt")
        touch(self.root, "Season 2/sample.mkv")
        item = {"seasons": [{"number": 1, "episodes": 2}, {"number": 2, "episodes": 8}]}
        found = scanner.scan_folder(self.root)
        self.assertEqual(len(found["videos"]), 3, "sample files are skipped")
        v = scanner.find_episode(item, found["videos"], 2, 1)
        self.assertEqual(Path(v["path"]).name, "Show.S02E01.mkv")
        subs = scanner.subtitles_for(v, found["subs"], found["videos"])
        self.assertEqual(Path(subs[0]["path"]).name, "2_English.srt")
        v = scanner.find_episode(item, found["videos"], 1, 1)
        subs = scanner.subtitles_for(v, found["subs"], found["videos"])
        self.assertEqual([Path(s["path"]).name for s in subs], ["Show.S01E01.en.srt"])
        self.assertTrue(subs[0]["english"])
        self.assertIsNone(scanner.find_episode(item, found["videos"], 2, 2))
        queue = scanner.episode_queue(found["videos"], scanner.find_episode(item, found["videos"], 1, 2))
        self.assertEqual([Path(q["path"]).name for q in queue], ["Show.S01E02.mkv", "Show.S02E01.mkv"])

    def test_anime_absolute_numbers(self):
        for n in (1, 2, 3, 13):
            touch(self.root, f"[SubsPlease] Frieren - {n:02d} (1080p).mkv")
        touch(self.root, "[SubsPlease] Frieren - 02 (1080p).ass")
        item = {"seasons": [{"number": 1, "episodes": 28}]}
        videos = scanner.scan_folder(self.root)["videos"]
        self.assertIn("- 13", scanner.find_episode(item, videos, 1, 13)["name"])
        found = scanner.scan_folder(self.root)
        v = scanner.find_episode(item, videos, 1, 2)
        self.assertEqual(len(scanner.subtitles_for(v, found["subs"], videos)), 1)

    def test_absolute_numbers_across_seasons(self):
        for n in range(1, 16):
            touch(self.root, f"Show - {n:02d}.mkv")
        item = {"seasons": [{"number": 1, "episodes": 12}, {"number": 2, "episodes": 12}]}
        videos = scanner.scan_folder(self.root)["videos"]
        self.assertIn("- 14", scanner.find_episode(item, videos, 2, 2)["name"])
        self.assertIsNone(scanner.find_episode(item, videos, 1, 14))

    def test_one_season_anime_labelled_with_later_season(self):
        touch(self.root, "Attack on Titan S03E05.mkv")
        touch(self.root, "Attack on Titan S03E06.mkv")
        item = {"seasons": [{"number": 1, "episodes": 12}]}
        videos = scanner.scan_folder(self.root)["videos"]
        self.assertIn("S03E06", scanner.find_episode(item, videos, 1, 6)["name"])

    def test_unnumbered_files_use_name_order(self):
        for name in ("b.mkv", "a.mkv", "c.mkv"):
            touch(self.root, name)
        videos = scanner.scan_folder(self.root)["videos"]
        self.assertEqual(scanner.find_episode({"seasons": []}, videos, 1, 2)["name"], "b.mkv")

    def test_movie_prefers_title_match_then_size(self):
        touch(self.root, "Inception.2010.1080p.mkv", 5000)
        touch(self.root, "Extras/Behind the scenes.mkv", 9000)
        touch(self.root, "Inception.2010.1080p.srt")
        found = scanner.scan_folder(self.root)
        movie = scanner.find_movie({"title": "Inception"}, found["videos"])
        self.assertEqual(movie["name"], "Inception.2010.1080p.mkv")
        subs = scanner.subtitles_for(movie, found["subs"], found["videos"])
        self.assertEqual(subs[0]["name"], "Inception.2010.1080p.srt")

    def test_missing_folder(self):
        with self.assertRaises(FileNotFoundError):
            scanner.scan_folder(Path(self.root, "nope"))


if __name__ == "__main__":
    unittest.main()


def video(rel, season, episodes, **extra):
    return dict({"rel": rel, "path": "/dl/" + rel, "name": rel.split("/")[-1], "season": season,
                 "episodes": list(episodes), "kind": "episode"}, **extra)


class AbsoluteNumberingTest(unittest.TestCase):
    """Long anime are numbered from their first episode (as on MyAnimeList), even
    when the files sit in "Season N" folders."""

    def test_a_season_folder_doesnt_change_a_myanimelist_episode(self):
        from watchlist.library import Library
        v = Library.describe({"rel": "Naruto Shippuden/Season 12/[Group] Naruto Shippuden - 250 [1080p].mkv",
                              "season": 12, "episodes": [250], "size": 400_000_000})
        self.assertEqual((v["season"], v["folder_season"]), (None, 12))
        kept = Library.describe({"rel": "Show/Season 2/Show - 05.mkv", "season": 2, "episodes": [5], "size": 1})
        self.assertEqual(kept["season"], 2)
        named = Library.describe({"rel": "Show/Season 12/Show S12E250.mkv", "season": 12, "episodes": [250], "size": 1})
        self.assertEqual(named["season"], 12)                     # the file's own name says so

        shippuden = {"seasons": [{"number": 1, "episodes": 500}]}           # one season on MyAnimeList
        files = [video("S11/a.mkv", None, [230], folder_season=11), video("S12/b.mkv", None, [250], folder_season=12),
                 video("S12/c.mkv", 12, [251])]
        self.assertEqual(sorted(scanner.episode_map(shippuden, files)), [(1, 230), (1, 250), (1, 251)])

    def test_tmdb_seasons_from_an_episode_counted_from_the_start(self):
        tmdb = {"seasons": [{"number": n, "episodes": 20} for n in range(1, 21)]}
        files = [video("S12/x.mkv", None, [250], folder_season=12), video("S12/y S12E250.mkv", 12, [250])]
        found = scanner.episode_map(tmdb, files)
        self.assertEqual(sorted(found), [(13, 10)])               # 12 seasons of 20, then episode 10

    def test_a_long_season_numbered_within_it(self):
        soap = {"seasons": [{"number": n, "episodes": 200} for n in range(1, 31)]}
        found = scanner.episode_map(soap, [video("Season 30/Soap - 150.mkv", None, [150], folder_season=30)])
        self.assertEqual(list(found), [(30, 150)])

    def test_season_in_name(self):
        self.assertTrue(scanner.season_in_name("Show S02E05.mkv"))
        self.assertTrue(scanner.season_in_name("Show 2x05.mkv"))
        self.assertFalse(scanner.season_in_name("[Group] Naruto Shippuden - 250 [1080p].mkv"))
