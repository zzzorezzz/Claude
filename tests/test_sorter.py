import json
import tempfile
import threading
import time
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
        self.searched = []
        self.related = {}
        patches = [
            mock.patch.object(metadata, "search", side_effect=self.fake_search),
            mock.patch.object(metadata, "details", side_effect=self.fake_details),
            mock.patch.object(metadata, "related", side_effect=lambda s, item: self.related.get(item["source_id"], [])),
            mock.patch.object(metadata.MAL, "franchise", side_effect=lambda mal_id, limit=45, on_progress=None: self.related.get(mal_id, [])),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def fake_search(self, settings, item_type, query):
        self.searched.append(item_type)
        return [dict(r) for r in self.searches.get((item_type, query), [])]

    def fake_details(self, settings, item_type, source, source_id):
        for rows in list(self.searches.values()) + list(self.related.values()):
            for r in rows:
                if r["source_id"] == source_id:
                    return dict(r, seasons=[{"number": 1, "episodes": 24}])
        raise metadata.MetadataError("not found")

    def sort(self):
        self.app.sorter.run_now(with_ai=True)
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
        self.app.sorter.run_now()
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

    def test_myanimelist_first_and_no_duplicates(self):
        # A file whose name is only close to a title in your list: a search hit that
        # isn't that title is suggested, never added as a second entry.
        burn = add(self.app, "anime", "Burn the Witch #0.8", source_id="58854")
        touch(self.dl, "Burn the Witch.mkv")
        self.searches[("anime", "burn the witch")] = [result("anime", "Burn the Witch", "41530", year=2020)]
        self.searches[("tv", "burn the witch")] = [result("tv", "Burn the Witch", "tv/99999", year=2020)]
        self.app.sorter.run_now()
        self.assertEqual(self.searched[0], "anime")
        self.assertEqual(len(self.app.store.list()), 2)        # Frieren and Burn the Witch #0.8 only
        [q] = self.app.library.state()["questions"]
        self.assertEqual(q["candidates"], [burn["id"]])
        self.assertEqual(q["suggest"][0]["source_id"], "41530")

    def test_without_keys_nothing_is_looked_up(self):
        self.app.settings.update({"mal_client_id": "", "tmdb_api_key": ""})
        touch(self.dl, "Some Show - 01.mkv")
        state = self.sort()
        self.assertEqual(len(state["questions"]), 1)
        self.assertEqual(self.app.library.answers["lookups"], {})

    def test_offline_tries_again_soon(self):
        touch(self.dl, "Some Show - 01.mkv")
        with mock.patch.object(metadata, "search", side_effect=metadata.MetadataError("offline")):
            self.app.sorter.run_now()
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


class SortNowTest(_Base):
    def test_sort_now_asks_the_ai_about_everything_waiting(self):
        self.app.settings.update({"gemini_api_key": "g"})
        words = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet",
                 "kilo", "lima", "mike", "november", "oscar"]
        for w in words:
            touch(self.dl, f"Mystery {w.title()} - 01.mkv")
        touch(self.dl, "Frirn - 07.mkv")
        replies = {"frirn|": {"decision": "in_list", "item_id": self.frieren["id"], "confidence": 0.9, "why": "typo"},
                   "mystery alpha|": {"decision": "in_list", "item_id": self.frieren["id"], "confidence": 0.3}}
        with mock.patch.object(ai, "sort_downloads", return_value=(replies, "groq")) as asked:
            report = self.app.sorter.run_now(with_ai=True)
        self.assertEqual(asked.call_count, 2)                 # 16 waiting: two batches
        self.assertTrue(all(c.kwargs.get("fresh") for c in asked.call_args_list))
        self.assertEqual((report["sorted"], report["ai"], report["error"]), (1, "Groq", ""))
        self.assertEqual(report["left"], 15)
        self.assertEqual(report["suggested"], 1)               # "Groq thinks this is Frieren"
        state = self.app.library.state()
        self.assertEqual(state["items"][self.frieren["id"]]["episodes"], ["1:7"])
        self.assertFalse(state["sort_now"]["running"])
        self.assertEqual(state["sort_now"]["last"]["sorted"], 1)
        # Plain Sort now doesn't ask the AI.
        with mock.patch.object(ai, "sort_downloads", return_value=({}, "groq")) as asked:
            self.app.sorter.run_now()
        self.assertEqual(asked.call_count, 0)

    def test_sort_now_without_an_ai_key(self):
        self.app.settings.update({"mal_client_id": "", "tmdb_api_key": ""})
        touch(self.dl, "Mystery - 01.mkv")
        report = self.app.sorter.run_now(with_ai=True)
        self.assertIn("AI key", report["error"])
        self.assertEqual(report["left"], 1)

    def test_sort_now_gives_a_resting_helper_another_go(self):
        self.app.settings.update({"gemini_api_key": "g"})
        ai._resting.clear()
        ai._resting[("gemini", "g")] = (time.time() + 300, "Gemini is busy", "busy")
        self.addCleanup(ai._resting.clear)
        touch(self.dl, "Frirn - 07.mkv")
        answer = {"answers": [{"key": "frirn|", "decision": "in_list", "item_id": self.frieren["id"], "confidence": 0.9}]}
        with mock.patch.object(ai.Gemini, "ask", return_value=(answer, "gemini-flash-latest")) as asked:
            report = self.app.sorter.run_now(with_ai=True)
        self.assertEqual(asked.call_count, 1)
        self.assertEqual(report["sorted"], 1)
        # A helper that's out for the day is still left alone.
        ai._resting[("gemini", "g")] = (time.time() + 300, "used up", "daily")
        with self.assertRaisesRegex(ai.AIError, "resting"):
            ai.ask({"gemini_api_key": "g"}, "hi", fresh=True)


class AITest(unittest.TestCase):
    def setUp(self):
        for d in (ai._models, ai._good, ai._bad, ai._resting):
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
            "gemini-3.1-flash-lite", "gemini-2.5-flash-lite", "gemini-3.1-flash-lite-preview",
            "gemma-4-31b-it"])

    def test_groq_and_openrouter_model_order(self):
        self.assertEqual(ai.Groq.rank(["llama-3.1-8b-instant", "whisper-large-v3", "openai/gpt-oss-20b",
                                       "llama-3.3-70b-versatile", "openai/gpt-oss-120b", "meta-llama/llama-guard-4-12b",
                                       "qwen/qwen3-32b", "playai-tts", "groq/compound"]),
                         ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "qwen/qwen3-32b",
                          "openai/gpt-oss-20b", "llama-3.1-8b-instant"])
        self.assertEqual(ai.OpenRouter.rank(["openai/gpt-oss-120b:free", "openai/gpt-oss-120b",
                                             "qwen/qwen2.5-vl-72b-instruct:free", "deepseek/deepseek-chat-v3.1:free",
                                             "qwen/qwen3-coder:free", "mistralai/mistral-small-3.2-24b-instruct:free",
                                             "meta-llama/llama-3.3-70b-instruct:free"]),
                         ["openai/gpt-oss-120b:free", "deepseek/deepseek-chat-v3.1:free",
                          "meta-llama/llama-3.3-70b-instruct:free", "mistralai/mistral-small-3.2-24b-instruct:free"])
        # Only free, text models are listed from OpenRouter.
        router = ai.OpenRouter("k")
        self.assertTrue(router._usable({"id": "x/y:free", "architecture": {"output_modalities": ["text"]}}))
        self.assertFalse(router._usable({"id": "x/y", "architecture": {"output_modalities": ["text"]}}))
        self.assertFalse(router._usable({"id": "x/y:free", "architecture": {"output_modalities": ["image"]}}))

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
            log = []
            reply, model = ai.Gemini("k").ask("hi", timeout=20, log=log)
            self.assertEqual((reply, model), ({"ok": True}, "gemini-2.5-flash"))
            self.assertEqual(len(log), 3)
            self.assertIn("free limit", log[0])
            calls.clear()
            self.assertEqual(ai.Gemini("k").ask("hi")[0], {"ok": True})
        self.assertEqual(len(calls), 1)                        # straight to the model that worked
        self.assertIn('"thinkingBudget": 0', calls[0][1])        # 2.5: no thinking

    def test_settings_test_keeps_the_fastest_model(self):
        # Under high demand: one model is overloaded, one hangs, one answers.
        calls = []
        slow = threading.Event()

        def behaviour_request(url, service, headers=None, body=None, timeout=20, method=None):
            if "gemini-3.8-flash:" in url:
                slow.wait(5)
                raise net.NetError("Gemini took too long to answer", "timeout")
            if "gemini-2.5-flash:" in url:
                time.sleep(0.3)        # answers a moment after the busy one has said so
            return fake(url, service, headers, body, timeout, method)
        fake = self.fake_gemini({"gemini-flash-latest": net.NetError(
            "x", "http", 503, "This model is currently experiencing high demand.")}, calls)
        with mock.patch.object(net, "request_json", side_effect=behaviour_request):
            started = time.monotonic()
            result = ai.test_key("gemini", "k")
            slow.set()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["model"], "gemini-2.5-flash")
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(ai._good[("gemini", "k")], "gemini-2.5-flash")
        self.assertTrue(any("busy" in t for t in result["tried"]), result["tried"])

    def test_all_overloaded(self):
        calls = []
        busy = net.NetError("x", "http", 503, "This model is currently experiencing high demand.")
        behaviour = {m: busy for m in ("gemini-3.8-flash", "gemini-flash-latest", "gemini-2.5-flash")}
        with mock.patch.object(net, "request_json", side_effect=self.fake_gemini(behaviour, calls)):
            result = ai.test_key("gemini", "k")
        self.assertEqual(result["kind"], "busy")
        self.assertIn("overloaded", result["error"])

    def test_busy_gets_another_go(self):
        calls, answers = [], {"n": 0}
        fake = self.fake_gemini({}, calls)

        def request(url, service, headers=None, body=None, timeout=20, method=None):
            if ":generateContent" in url:
                answers["n"] += 1
                if answers["n"] <= 3:
                    raise net.NetError("x", "http", 503, "high demand")
            return fake(url, service, headers, body, timeout, method)
        with mock.patch.object(net, "request_json", side_effect=request), \
                mock.patch.object(ai, "BUSY_RETRY_AFTER", 0):
            log = []
            reply, model = ai.Gemini("k").ask("hi", timeout=20, log=log)
        self.assertEqual(model, "gemini-flash-latest")
        self.assertIn("(again): answered", log[-1])

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

    def test_big_lists_are_cut_down_to_what_could_match(self):
        items = [{"id": str(n), "type": "tv", "title": f"Show {n}"} for n in range(400)]
        items.append({"id": "x", "type": "anime", "title": "Frieren", "aliases": ["Sousou no Frieren"]})
        picked = ai._relevant(items, [{"guess": "sousou no frieren", "files": ["[Group] Sousou no Frieren - 05.mkv"]}])
        self.assertEqual([i["id"] for i in picked], ["x"])
        self.assertEqual(len(ai._relevant(items[:100], [])), 100)

    def test_gemma_answers_in_plain_text(self):
        bodies = []

        def request(url, service, headers=None, body=None, timeout=20, method=None):
            if "/models?" in url:
                return {"models": [{"name": "models/gemma-4-31b-it", "supportedGenerationMethods": ["generateContent"]}]}
            bodies.append(body)
            return {"candidates": [{"content": {"parts": [{"text": 'Sure! ```json\n{"ok": true}\n```'}]}}]}
        with mock.patch.object(net, "request_json", side_effect=request):
            self.assertEqual(ai.Gemini("k").ask("hi")[0], {"ok": True})
        self.assertNotIn("responseMimeType", bodies[0]["generationConfig"])

    def openai_style(self, models, answer):
        calls = []

        def request(url, service, headers=None, body=None, timeout=20, method=None):
            calls.append((url, body))
            if url.endswith("/models"):
                return {"data": [{"id": m} for m in models]}
            result = answer(body)
            if isinstance(result, Exception):
                raise result
            return {"choices": [{"message": {"content": result}}]}
        return request, calls

    def test_groq_moves_to_the_next_model_when_one_is_used_up(self):
        def answer(body):
            if body["model"] == "openai/gpt-oss-120b":
                return net.NetError("x", "http", 429, "Rate limit reached for model on tokens per day (TPD)")
            return '{"ok": true}'
        request, calls = self.openai_style(["openai/gpt-oss-120b", "llama-3.3-70b-versatile"], answer)
        with mock.patch.object(net, "request_json", side_effect=request):
            log = []
            reply, model = ai.Groq("gsk").ask("hi", log=log)
        self.assertEqual(model, "llama-3.3-70b-versatile")
        self.assertIn("free limit", log[0])
        self.assertEqual(calls[1][1]["reasoning_effort"], "low")          # gpt-oss: think briefly
        self.assertEqual(calls[1][0], "https://api.groq.com/openai/v1/chat/completions")

    def test_openrouter_daily_limit_hands_over_to_the_next_helper(self):
        def answer(body):
            return net.NetError("x", "http", 429, "Rate limit exceeded: free-models-per-day. Add 10 credits...")
        request, calls = self.openai_style(["openai/gpt-oss-120b:free", "deepseek/deepseek-chat-v3.1:free"], answer)
        with mock.patch.object(net, "request_json", side_effect=request):
            result = ai.test_key("openrouter", "sk-or")
        self.assertEqual(result["kind"], "daily")
        self.assertIn("tomorrow", result["error"])

    def test_options_a_model_refuses_are_dropped(self):
        def answer(body):
            if "response_format" in body:
                return net.NetError("x", "http", 400, "response_format json_object is not supported by this model")
            return '{"ok": true}'
        request, calls = self.openai_style(["llama-3.3-70b-versatile"], answer)
        with mock.patch.object(net, "request_json", side_effect=request):
            self.assertTrue(ai.test_key("groq", "gsk")["ok"])
        self.assertNotIn("response_format", calls[-1][1])

    def test_the_next_helper_answers_and_the_tired_one_rests(self):
        settings = {"gemini_api_key": "g", "groq_api_key": "q", "openrouter_api_key": "o"}
        with mock.patch.object(ai.Gemini, "ask", side_effect=ai.AIError("Gemini is overloaded", "busy")) as gem, \
                mock.patch.object(ai.Groq, "ask", side_effect=ai.AIError("today's limit", "limit")) as groq, \
                mock.patch.object(ai.OpenRouter, "ask", return_value=({"answers": []}, "x:free")):
            self.assertEqual(ai.sort_downloads(settings, [], []), ({}, "openrouter"))
            self.assertEqual(set(ai.resting(settings)), {"gemini", "groq"})
            # Next time the resting ones aren't even asked.
            self.assertEqual(ai.sort_downloads(settings, [], []), ({}, "openrouter"))
            self.assertEqual((gem.call_count, groq.call_count), (1, 1))
        # When everything is out, the message says what and for how long.
        with mock.patch.object(ai.OpenRouter, "ask", side_effect=ai.AIError("used up", "daily")):
            with self.assertRaisesRegex(ai.AIError, "Gemini: .*resting"):
                ai.sort_downloads(settings, [], [])
        # Testing a key in Settings gives it a fresh chance.
        with mock.patch.object(ai.Gemini, "probe", return_value=({"ok": True}, "gemini-flash-latest")):
            self.assertTrue(ai.test_key("gemini", "g")["ok"])
        self.assertNotIn("gemini", ai.resting(settings))
        self.assertEqual(ai.available({"gemini_api_key": " ", "openrouter_api_key": "x"}), ["openrouter"])


class AnimeFromMyAnimeListTest(_Base):
    def setUp(self):
        super().setUp()
        metadata._twin_cache._data.clear()
        # Two MyAnimeList entries called "Naruto": too close to choose between by name alone.
        two = [result("anime", "Naruto", "20", year=2002), result("anime", "Naruto", "99", year=2023)]
        self.searches[("anime", "naruto")] = self.searches[("anime", "Naruto")] = two
        self.searches[("tv", "naruto")] = [result("tv", "Naruto", "tv/46260", year=2002, genre_ids=[16, 10759],
                                                  language="ja", original_title="ナルト", aliases=["Naruto", "ナルト"])]

    def test_an_anime_tmdb_finds_is_taken_from_myanimelist(self):
        touch(self.dl, "Naruto - 150 [1080p].mkv")
        self.app.library.scan(force=True)
        self.sort()
        added = [i for i in self.app.store.list() if i["title"] == "Naruto"]
        self.assertEqual([(i["source"], i["source_id"], i["type"]) for i in added], [("mal", "20", "anime")])

    def test_never_the_tmdb_copy_when_myanimelist_has_none(self):
        self.searches[("anime", "Naruto")] = self.searches[("anime", "naruto")] = []
        touch(self.dl, "Naruto - 150 [1080p].mkv")
        self.app.library.scan(force=True)
        self.sort()
        self.assertFalse(any(i["source"] == "tmdb" for i in self.app.store.list()))

    def test_live_action_stays_on_tmdb(self):
        self.searches[("tv", "breaking bad")] = [result("tv", "Breaking Bad", "tv/1396", year=2008,
                                                        genre_ids=[18], language="en")]
        touch(self.dl, "Breaking.Bad.S01E01.mkv")
        self.app.library.scan(force=True)
        self.sort()
        self.assertEqual([i["source"] for i in self.app.store.list() if i["title"] == "Breaking Bad"], ["tmdb"])


class ConnectedTest(_Base):
    def test_everything_connected_shows_where_each_one_is(self):
        self.related[self.frieren["source_id"]] = [
            result("anime", "Frieren: Beyond Journey's End Season 2", "59978", relation="Sequel",
                   aliases=["Sousou no Frieren 2nd Season"]),
            result("anime", "Frieren: The Little Adventures", "56885", media_kind="ova", relation="Side story")]
        touch(self.dl, "[Group] Sousou no Frieren 2nd Season - 01 [1080p].mkv")
        self.app.library.scan(force=True)
        self.app.add_title("anime", "mal", "56885")
        item = self.app.store.get(self.frieren["id"])
        self.assertIsNone(self.app.connected(item)["entries"])        # only looked for when you ask
        out = self.app.connected(item, run=True, wait=True)
        by_id = {e["source_id"]: e for e in out["entries"]}
        self.assertEqual(by_id["59978"]["relation"], "Sequel")
        self.assertIsNone(by_id["59978"]["in_list"])
        self.assertEqual(by_id["59978"]["waiting"], 1)
        self.assertIsNotNone(by_id["56885"]["in_list"])
        self.assertIn("AI key", out["note"])
        self.assertEqual(len(self.app.connected(item)["entries"]), 2)  # remembered

    def test_the_ai_adds_what_myanimelist_doesnt_link(self):
        self.app.settings.update({"gemini_api_key": "g"})
        self.related[self.frieren["source_id"]] = [result("anime", "Frieren Season 2", "59978", relation="Sequel")]
        self.searches[("anime", "Frieren Recap")] = [result("anime", "Frieren Recap", "60001", media_kind="special")]
        named = [{"title": "Frieren Recap", "year": 2023, "kind": "special", "relation": "Recap"},
                 {"title": "Frieren Season 2", "year": 2026, "kind": "tv", "relation": "Sequel"},
                 {"title": "Made Up Film", "year": 2030, "kind": "movie", "relation": "Film"}]
        with mock.patch.object(ai, "connected_titles", return_value=named), \
                mock.patch.object(ai, "story_guide", side_effect=ai.AIError("offline")):
            out = self.app.connected(self.app.store.get(self.frieren["id"]), run=True, wait=True)
        self.assertEqual(sorted(e["source_id"] for e in out["entries"]), ["59978", "60001"])

    def test_story_guide_explains_how_each_one_connects(self):
        self.app.settings.update({"gemini_api_key": "g"})
        self.related[self.frieren["source_id"]] = [
            result("anime", "Frieren Season 2", "59978", relation="Sequel"),
            result("anime", "Frieren Mini", "56885", media_kind="ova", relation="Side story")]
        guide = {"overview": "Start with season 1.", "overview_spoilers": "Himmel dies.",
                 "story_order": [0, 2, 1, 7],
                 "entries": [
                     {"n": 0, "connects": "The main story", "placement": "First", "canon": "canon",
                      "importance": "essential", "about": "An elf mage", "spoilers": "x"},
                     {"n": 1, "connects": "Direct sequel", "placement": "After season 1", "canon": "canon",
                      "importance": "essential", "about": "More journey", "spoilers": "y"},
                     {"n": 2, "connects": "Shorts", "placement": "Any time", "canon": "made up",
                      "importance": "very", "about": "Chibi", "spoilers": "z"},
                     {"n": 9, "connects": "nothing"}]}
        prompts = []
        with mock.patch.object(ai, "connected_titles", return_value=[]), \
                mock.patch.object(ai, "ask", side_effect=lambda s, p, **kw: prompts.append(p) or (guide, "gemini")):
            out = self.app.connected(self.app.store.get(self.frieren["id"]), run=True, wait=True)
        self.assertTrue(out["self"]["self"])
        self.assertEqual(out["self"]["in_list"], self.frieren["id"])
        g = out["guide"]
        self.assertEqual(g["overview"], "Start with season 1.")
        self.assertEqual(g["story_order"], ["self", "mal:56885", "mal:59978"])
        self.assertEqual(g["entries"]["mal:59978"]["placement"], "After season 1")
        # Answers outside the allowed values aren't shown as if they were facts.
        self.assertEqual((g["entries"]["mal:56885"]["canon"], g["entries"]["mal:56885"]["importance"]), ("unknown", ""))
        self.assertIn("Frieren Season 2", prompts[-1])
        self.assertEqual(self.app.connected(self.app.store.get(self.frieren["id"]))["guide"], g)   # remembered

    def test_tv_spinoffs_without_ai_go_by_name(self):
        bb = add(self.app, "tv", "Breaking Bad", source_id="tv/1396")
        self.searches[("tv", "Breaking Bad")] = [result("tv", "Breaking Bad", "tv/1396", year=2008)]
        self.searches[("movie", "Breaking Bad")] = [
            result("movie", "El Camino: A Breaking Bad Movie", "movie/559969", year=2019, media_kind="movie")]
        out = self.app.connected(self.app.store.get(bb["id"]), run=True, wait=True)
        self.assertEqual([e["title"] for e in out["entries"]], ["El Camino: A Breaking Bad Movie"])

    def test_tv_spinoffs_with_ai_are_checked_on_tmdb(self):
        self.app.settings.update({"gemini_api_key": "g"})
        bb = add(self.app, "tv", "Breaking Bad", source_id="tv/1396")
        self.searches[("tv", "Better Call Saul")] = [result("tv", "Better Call Saul", "tv/60059", year=2015)]
        named = [{"title": "Better Call Saul", "year": 2015, "kind": "tv", "relation": "Spin-off"},
                 {"title": "Breaking Good", "year": 2030, "kind": "tv", "relation": "Sequel"}]
        with mock.patch.object(ai, "connected_titles", return_value=named), \
                mock.patch.object(ai, "story_guide", side_effect=ai.AIError("offline")):
            out = self.app.connected(self.app.store.get(bb["id"]), run=True, wait=True)
        self.assertEqual([(e["title"], e["relation"]) for e in out["entries"]], [("Better Call Saul", "Spin-off")])


class FranchiseTest(unittest.TestCase):
    def test_follows_links_through_the_franchise(self):
        graph = {
            "1": ("Show", 2020, "tv", [("2", "sequel"), ("3", "character")]),
            "2": ("Show Season 2", 2022, "tv", [("1", "prequel"), ("4", "sequel"), ("5", "side_story")]),
            "4": ("Show Season 3", 2024, "tv", [("2", "prequel")]),
            "5": ("Show OVA", 2023, "ova", []),
            "3": ("Crossover", 2019, "tv", []),
        }

        def fake(url, headers=None, service=""):
            mal_id = url.split("/anime/")[1].split("?")[0]
            title, year, kind, links = graph[mal_id]
            return {"id": int(mal_id), "title": title, "start_date": f"{year}-01-01", "media_type": kind,
                    "related_anime": [{"node": {"id": int(r), "title": graph[r][0]}, "relation_type": rel,
                                       "relation_type_formatted": rel.replace("_", " ").title()} for r, rel in links]}
        metadata._related_cache._data.clear()
        with mock.patch.object(metadata, "_get_json", side_effect=fake):
            found = metadata.MAL("cid").franchise("1")
        by_id = {r["source_id"]: r for r in found}
        self.assertEqual(sorted(by_id), ["2", "4", "5"])          # not the crossover
        self.assertEqual((by_id["4"]["relation"], by_id["4"]["via"]), ("Sequel", "Show Season 2"))
        self.assertEqual((by_id["5"]["media_kind"], by_id["5"]["year"]), ("ova", 2023))
