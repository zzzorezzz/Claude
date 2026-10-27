import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_library import add
from tests.test_scanner import touch
from watchlist import ai, metadata, net
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
        with mock.patch.object(ai, "sort_downloads", return_value=(replies, "gemini")) as asked:
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
        with mock.patch.object(ai, "sort_downloads", return_value=(replies, "gemini")):
            state = self.sort()
        self.assertEqual(state["questions"], [])
        self.assertIsNotNone(self.app.store.find_source("mal", "54492"))

        # A title that can't be found isn't added.
        touch(self.dl, "Made Up - 01.mkv")
        replies = {"made up|": {"decision": "add", "confidence": 0.9, "add": {"type": "anime", "title": "Made Up Show"}}}
        with mock.patch.object(ai, "sort_downloads", return_value=(replies, "gemini")):
            state = self.sort()
        self.assertEqual(len(state["questions"]), 1)


class AITest(unittest.TestCase):
    def setUp(self):
        for d in (ai._models, ai._good, ai._bad):
            d.clear()

    def test_gemini_model_order(self):
        # Names from a real key's model list (October 2026).
        names = ["models/gemini-2.5-flash", "models/gemini-2.5-pro", "models/gemini-2.5-flash-preview-tts",
                 "models/gemma-4-31b-it", "models/gemini-flash-latest", "models/gemini-flash-lite-latest",
                 "models/gemini-2.5-flash-lite", "models/gemini-2.5-flash-image", "models/gemini-3-flash-preview",
                 "models/gemini-3.1-flash-lite-preview", "models/gemini-3.1-flash-lite", "models/gemini-3.5-flash",
                 "models/gemini-3.5-flash-lite", "models/gemini-omni-flash-preview", "models/gemini-omni-1.1-flash",
                 "models/gemini-3.6-flash", "models/gemini-3.7-flash", "models/gemini-3.8-flash",
                 "models/gemini-4-argon", "models/gemini-3.8-flash-tts", "models/gemini-3.1-flash-image"]
        self.assertEqual(ai.Gemini.rank(names), [
            "gemini-flash-latest", "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash",
            "gemini-2.5-flash", "gemini-3-flash-preview", "gemini-flash-lite-latest", "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite", "gemini-2.5-flash-lite", "gemini-3.1-flash-lite-preview"])

    def test_openai_and_grok_model_order(self):
        self.assertEqual(ai.OpenAI.rank(["gpt-5.2", "gpt-5.2-mini", "gpt-5-nano", "gpt-5-mini", "gpt-4o-mini",
                                         "gpt-5.2-2026-01-01", "dall-e-3", "gpt-image-1", "o3"]),
                         ["gpt-5.2-mini", "gpt-5.2", "gpt-5-mini", "gpt-5-nano", "gpt-4o-mini"])
        self.assertEqual(ai.XAI.rank(["grok-4.3", "grok-4.3-fast-non-reasoning", "grok-code-fast-1",
                                      "grok-2-image", "grok-3-mini", "grok-4.20"])[:3],
                         ["grok-4.3-fast-non-reasoning", "grok-4.3", "grok-4.20"])

    def fake_gemini(self, behaviour, calls):
        def request(url, service, headers=None, body=None, timeout=20, method=None):
            calls.append((url, json.dumps(body) if body else ""))
            if "/models?" in url:
                return {"models": [{"name": f"models/{m}", "supportedGenerationMethods": ["generateContent"]}
                                   for m in ("gemini-3.8-flash", "gemini-flash-latest", "gemini-2.5-flash")]}
            model = url.split("/models/")[1].split(":")[0]
            result = behaviour.get(model, "ok")
            if isinstance(result, Exception):
                raise result
            return {"candidates": [{"content": {"parts": [{"text": '```json\n{"ok": true}\n```'}]}}]}
        return request

    def test_skips_models_over_their_limit_and_remembers_the_one_that_works(self):
        calls = []
        behaviour = {"gemini-flash-latest": net.NetError("x", "http", 429, "quota exceeded"),
                     "gemini-3.8-flash": net.NetError("Gemini took too long to answer", "timeout")}
        with mock.patch.object(net, "request_json", side_effect=self.fake_gemini(behaviour, calls)):
            result = ai.test_key("gemini", "k")
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["model"], "gemini-2.5-flash")
            self.assertEqual(len(result["tried"]), 3)
            self.assertIn("free limit", result["tried"][0])
            calls.clear()
            self.assertEqual(ai.Gemini("k").ask("hi")[0], {"ok": True})
        self.assertEqual(len(calls), 1)                        # straight to the model that worked
        self.assertIn('"thinkingBudget": 0', calls[0][1])        # 2.5: no thinking

    def test_newer_models_think_briefly(self):
        calls = []
        with mock.patch.object(net, "request_json", side_effect=self.fake_gemini({}, calls)):
            ai.Gemini("k").ask("hi")
        self.assertIn("gemini-flash-latest", calls[-1][0])
        self.assertIn('"thinkingLevel": "low"', calls[-1][1])

    def test_errors_that_stop_straight_away(self):
        cases = [(net.NetError("x", "http", 400, "API key not valid. Please pass a valid API key."), "key"),
                 (net.NetError("x", "http", 400, "User location is not supported for the API use."), "region"),
                 (net.NetError("couldn't find Gemini's server", "dns"), "offline")]
        for error, kind in cases:
            calls = []
            ai._models.clear()
            behaviour = {m: error for m in ("gemini-3.8-flash", "gemini-flash-latest", "gemini-2.5-flash")}
            with mock.patch.object(net, "request_json", side_effect=self.fake_gemini(behaviour, calls)):
                result = ai.test_key("gemini", "k")
            self.assertEqual(result["kind"], kind, result)
            self.assertEqual(len(calls), 2)                    # the list, then one try

    def test_chatgpt_without_credit(self):
        def request(url, service, headers=None, body=None, timeout=20, method=None):
            if url.endswith("/models"):
                return {"data": [{"id": "gpt-5-mini"}]}
            raise net.NetError("x", "http", 429, "You exceeded your current quota, please check your plan and billing details.")
        with mock.patch.object(net, "request_json", side_effect=request):
            result = ai.test_key("openai", "sk-x")
        self.assertEqual(result["kind"], "credit")
        self.assertIn("platform.openai.com", result["error"])

    def test_grok_answers_and_options_it_refuses_are_dropped(self):
        bodies = []

        def request(url, service, headers=None, body=None, timeout=20, method=None):
            if url.endswith("/models"):
                return {"data": [{"id": "grok-4.3"}]}
            bodies.append(dict(body))
            if "response_format" in body:
                raise net.NetError("x", "http", 400, "response_format is not supported for this model")
            return {"choices": [{"message": {"content": '{"ok": true}'}}]}
        with mock.patch.object(net, "request_json", side_effect=request):
            self.assertTrue(ai.test_key("xai", "xai-k")["ok"])
        self.assertNotIn("response_format", bodies[-1])

    def test_falls_back_to_the_next_helper(self):
        settings = {"gemini_api_key": "g", "openai_api_key": "o", "xai_api_key": ""}
        with mock.patch.object(ai.Gemini, "ask", side_effect=ai.AIError("limit", "limit")), \
                mock.patch.object(ai.OpenAI, "ask", return_value=({"answers": []}, "gpt-5-mini")):
            self.assertEqual(ai.sort_downloads(settings, [], []), ({}, "openai"))
        self.assertEqual(ai.available({"gemini_api_key": " ", "xai_api_key": "x"}), ["xai"])


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
