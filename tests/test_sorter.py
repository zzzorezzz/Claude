import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_library import add
from tests.test_scanner import touch
from watchlist import ai, metadata, sorter
from watchlist.server import App


def result(item_type, title, sid, year=2023, **extra):
    fmt = "movie" if item_type == "movie" or extra.get("media_kind") == "movie" else "series"
    return {"source": "mal" if item_type == "anime" else "tmdb", "source_id": sid, "title": title,
            "original_title": "", "year": year, "overview": "", "poster": "", "rating": 8.0,
            "format": fmt, "media_type": "", "media_kind": "tv", "aliases": [title], **extra}


class _Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.dl = root / "Downloads"
        self.dl.mkdir()
        self.app = App(root / "data")
        self.app.settings.update({"mal_client_id": "cid", "tmdb_api_key": "tok",
                                  "downloads_folder": str(self.dl)})
        self.frieren = add(self.app, "anime", "Frieren: Beyond Journey's End",
                           aliases=["Sousou no Frieren"], seasons=[{"number": 1, "episodes": 28}])
        self.searches = {}
        self.related = {}
        patches = [
            mock.patch.object(metadata, "search", side_effect=self.fake_search),
            mock.patch.object(metadata, "details", side_effect=self.fake_details),
            mock.patch.object(metadata, "related", side_effect=lambda s, item: self.related.get(item["source_id"], [])),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def fake_search(self, settings, item_type, query):
        return [dict(r) for r in self.searches.get((item_type, query), [])]

    def fake_details(self, settings, item_type, source, source_id):
        for rows in list(self.searches.values()) + list(self.related.values()):
            for r in rows:
                if r["source_id"] == source_id:
                    return dict(r, seasons=[{"number": 1, "episodes": 24}])
        raise metadata.MetadataError("not found")

    def sort(self):
        self.app.library.scan(force=True)
        self.app.sorter.run_once()
        return self.app.library.state()


class SorterTest(_Base):
    def test_finds_and_adds_a_show_not_in_your_list(self):
        touch(self.dl, "[Erai-raws] Kusuriya no Hitorigoto - 03 [1080p].mkv")
        self.searches[("anime", "kusuriya no hitorigoto")] = [
            result("anime", "The Apothecary Diaries", "54492", aliases=["Kusuriya no Hitorigoto"]),
            result("anime", "Kusuriya no Hitorigoto 2nd Season", "58514", year=2025,
                   aliases=["Kusuriya no Hitorigoto 2nd Season"])]
        state = self.sort()
        self.assertEqual(state["questions"], [])
        added = self.app.store.find_source("mal", "54492")
        self.assertIsNotNone(added)
        self.assertEqual(state["items"][added["id"]]["episodes"], ["1:3"])
        [auto] = state["auto"]
        self.assertEqual((auto["by"], auto["added"]), ("search", True))
        self.assertIn("The Apothecary Diaries", auto["label"])

        # Undo: back to a question, never sorted automatically again.
        self.app.undo_sorted(auto["key"])
        self.assertIsNone(self.app.store.find_source("mal", "54492"))
        self.app.sorter.run_once()
        state = self.app.library.state()
        self.assertEqual(len(state["questions"]), 1)
        self.assertEqual(state["auto"], [])

    def test_an_ova_comes_from_the_shows_relations(self):
        touch(self.dl, "[Group] Sousou no Frieren OVA [1080p].mkv")
        self.related[self.frieren["source_id"]] = [
            result("anime", "Frieren: Beyond Journey's End Season 2", "59978", media_kind="tv", relation="Sequel"),
            result("anime", "Frieren: The Little Adventures", "56885", media_kind="ova", relation="Side story")]
        state = self.sort()
        self.assertEqual(state["questions"], [])
        ova = self.app.store.find_source("mal", "56885")
        self.assertIsNotNone(ova)
        self.assertEqual(state["items"][ova["id"]]["files"], 1)

    def test_unclear_results_become_suggestions(self):
        touch(self.dl, "Hunter x Hunter - 05.mkv")
        self.searches[("anime", "hunter x hunter")] = [
            result("anime", "Hunter x Hunter", "136", year=1999), result("anime", "Hunter x Hunter", "11061", year=2011)]
        state = self.sort()
        [q] = state["questions"]
        self.assertEqual([s["source_id"] for s in q["suggest"][:2]], ["136", "11061"])
        self.assertEqual(len(self.app.store.list()), 1)

    def test_turned_off_it_only_suggests(self):
        self.app.settings.update({"auto_sort": False})
        touch(self.dl, "[Erai-raws] Kusuriya no Hitorigoto - 03 [1080p].mkv")
        self.searches[("anime", "kusuriya no hitorigoto")] = [
            result("anime", "The Apothecary Diaries", "54492", aliases=["Kusuriya no Hitorigoto"])]
        state = self.sort()
        [q] = state["questions"]
        self.assertEqual(q["suggest"][0]["source_id"], "54492")
        self.assertIsNone(self.app.store.find_source("mal", "54492"))

    def test_without_keys_nothing_is_looked_up(self):
        self.app.settings.update({"mal_client_id": "", "tmdb_api_key": ""})
        touch(self.dl, "Some Show - 01.mkv")
        state = self.sort()
        self.assertEqual(len(state["questions"]), 1)
        self.assertEqual(self.app.library.answers["lookups"], {})

    def test_offline_tries_again_soon(self):
        touch(self.dl, "Some Show - 01.mkv")
        with mock.patch.object(metadata, "search", side_effect=metadata.MetadataError("offline")):
            self.sort()
        found = self.app.library.answers["lookups"]["some show|"]
        self.assertLess(found["at"], 1e10)
        self.assertEqual(found["error"], "offline")

    def test_gemini_decides_what_search_couldnt(self):
        self.app.settings.update({"gemini_api_key": "g"})
        touch(self.dl, "Frirn - 07.mkv")
        touch(self.dl, "Mystery Thing - 01.mkv")
        replies = {"frirn|": {"decision": "in_list", "item_id": self.frieren["id"], "confidence": 0.95, "why": "typo"},
                   "mystery thing|": {"decision": "in_list", "item_id": self.frieren["id"], "confidence": 0.4}}
        with mock.patch.object(ai, "sort_downloads", return_value=replies) as asked:
            state = self.sort()
        self.assertEqual(asked.call_count, 1)
        self.assertEqual(state["items"][self.frieren["id"]]["episodes"], ["1:7"])
        [q] = state["questions"]
        self.assertIn("Gemini thinks this is Frieren", q["hint"])
        self.assertEqual(q["hint_item"], self.frieren["id"])
        self.assertEqual(state["auto"][0]["by"], "gemini")

    def test_gemini_failing_changes_nothing(self):
        self.app.settings.update({"gemini_api_key": "g"})
        touch(self.dl, "Frirn - 07.mkv")
        with mock.patch.object(ai, "sort_downloads", side_effect=ai.AIError("Gemini's free limit is used up")):
            state = self.sort()
        self.assertEqual(len(state["questions"]), 1)
        self.assertIn("free limit", state["gemini_error"])

    def test_title_gemini_names_is_checked_before_adding(self):
        self.app.settings.update({"gemini_api_key": "g"})
        touch(self.dl, "Apo Diaries - 01.mkv")
        self.searches[("anime", "The Apothecary Diaries")] = [result("anime", "The Apothecary Diaries", "54492")]
        replies = {"apo diaries|": {"decision": "add", "confidence": 0.9,
                                    "add": {"type": "anime", "title": "The Apothecary Diaries", "year": 2023}}}
        with mock.patch.object(ai, "sort_downloads", return_value=replies):
            state = self.sort()
        self.assertEqual(state["questions"], [])
        self.assertIsNotNone(self.app.store.find_source("mal", "54492"))

        # A title that can't be found isn't added.
        touch(self.dl, "Made Up - 01.mkv")
        replies = {"made up|": {"decision": "add", "confidence": 0.9, "add": {"type": "anime", "title": "Made Up Show"}}}
        with mock.patch.object(ai, "sort_downloads", return_value=replies):
            state = self.sort()
        self.assertEqual(len(state["questions"]), 1)


class GeminiTest(unittest.TestCase):
    def test_picks_the_newest_flash_model(self):
        names = ["models/gemini-2.0-flash", "models/gemini-2.5-flash", "models/gemini-2.5-flash-lite",
                 "models/gemini-2.5-flash-image", "models/gemini-2.5-pro", "models/gemini-3-flash-preview",
                 "models/gemini-2.5-flash-preview-tts", "models/gemini-flash-latest", "models/gemini-2.0-flash-001"]
        self.assertEqual(ai.rank_models(names)[:4], ["gemini-3-flash-preview", "gemini-2.5-flash",
                                                     "gemini-2.0-flash", "gemini-2.0-flash-001"])
        self.assertEqual(ai.rank_models(["models/gemini-3-flash", "models/gemini-3-flash-preview"])[0], "gemini-3-flash")

    def test_ask_reads_the_json_answer_and_falls_back_to_the_next_model(self):
        ai._models.clear()
        calls = []

        def fake(key, path, body=None):
            calls.append(path)
            if path.startswith("/models?"):
                return {"models": [{"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
                                   {"name": "models/gemini-2.0-flash", "supportedGenerationMethods": ["generateContent"]}]}
            if "2.5" in path:
                raise ai.AIError("Gemini's free limit is used up for now")
            return {"candidates": [{"content": {"parts": [{"text": '```json\n{"ok": true}\n```'}]}}]}

        with mock.patch.object(ai, "_request", side_effect=fake):
            self.assertEqual(ai.Gemini("k").ask("hi"), {"ok": True})
            self.assertEqual(ai.test_key("k"), {"ok": True, "model": "gemini-2.5-flash"})
        self.assertIn("/models/gemini-2.0-flash:generateContent", calls)

    def test_bad_key(self):
        ai._models.clear()
        with mock.patch.object(ai, "_request", side_effect=ai.AIError("Gemini rejected your key - check it in Settings")):
            self.assertEqual(ai.test_key("bad"), {"ok": False, "error": "Gemini rejected your key - check it in Settings"})


class RelatedTest(_Base):
    def test_related_entries_show_where_each_one_is(self):
        self.related[self.frieren["source_id"]] = [
            result("anime", "Frieren: Beyond Journey's End Season 2", "59978", relation="Sequel",
                   aliases=["Sousou no Frieren 2nd Season"]),
            result("anime", "Frieren: The Little Adventures", "56885", media_kind="ova", relation="Side story")]
        self.app.settings.update({"auto_sort": False})
        touch(self.dl, "[Group] Sousou no Frieren 2nd Season - 01 [1080p].mkv")
        self.app.library.scan(force=True)
        self.app.add_title("anime", "mal", "56885")
        out = self.app.related(self.app.store.get(self.frieren["id"]))
        by_id = {e["source_id"]: e for e in out["entries"]}
        self.assertEqual(by_id["59978"]["relation"], "Sequel")
        self.assertIsNone(by_id["59978"]["in_list"])
        self.assertEqual(by_id["59978"]["waiting"], 1)
        self.assertIsNotNone(by_id["56885"]["in_list"])

    def test_tv_spinoffs_without_gemini_go_by_name(self):
        bb = add(self.app, "tv", "Breaking Bad", source_id="tv/1396")
        self.searches[("tv", "Breaking Bad")] = [result("tv", "Breaking Bad", "tv/1396", year=2008)]
        self.searches[("movie", "Breaking Bad")] = [
            result("movie", "El Camino: A Breaking Bad Movie", "movie/559969", year=2019, media_kind="movie")]
        out = self.app.related(self.app.store.get(bb["id"]))
        self.assertEqual([e["title"] for e in out["entries"]], ["El Camino: A Breaking Bad Movie"])

    def test_tv_spinoffs_with_gemini_are_checked_on_tmdb(self):
        self.app.settings.update({"gemini_api_key": "g"})
        bb = add(self.app, "tv", "Breaking Bad", source_id="tv/1396")
        self.searches[("tv", "Better Call Saul")] = [result("tv", "Better Call Saul", "tv/60059", year=2015)]
        named = [{"title": "Better Call Saul", "year": 2015, "type": "tv", "relation": "Spin-off"},
                 {"title": "Breaking Good", "year": 2030, "type": "tv", "relation": "Sequel"}]
        with mock.patch.object(ai, "related_titles", return_value=named):
            out = self.app.related(self.app.store.get(bb["id"]))
        self.assertEqual([(e["title"], e["relation"]) for e in out["entries"]], [("Better Call Saul", "Spin-off")])
