"""Round 8: reviews, how the episodes are connected, quick find, your own AI
helpers, your own player and browser, and the player's look."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_scanner import touch
from tests.test_server import ServerBase
from watchlist import ai, launcher, net, player
from watchlist.config import Settings, clean_ai_custom


class ReviewTest(ServerBase):
    def test_personal_review(self):
        show = self.add("tv", "tmdb", "tv/1396")
        status, item = self.call("PUT", f"/api/items/{show['id']}", {"review": {"stars": 4, "text": "  So good  "}})
        self.assertEqual(status, 200, item)
        self.assertEqual((item["review"]["stars"], item["review"]["text"]), (4, "So good"))
        status, _ = self.call("PUT", f"/api/items/{show['id']}", {"review": {"stars": 7}})
        self.assertEqual(status, 400)
        _, item = self.call("PUT", f"/api/items/{show['id']}", {"review": None})
        self.assertIsNone(item["review"])
        _, item = self.call("PUT", f"/api/items/{show['id']}", {"opened": True})
        self.assertTrue(item["opened_at"])


class ConnectionsTest(ServerBase):
    def setUp(self):
        super().setUp()
        self.show = self.add("tv", "tmdb", "tv/1396")
        self.dl = self.media / "dl"
        touch(self.dl, "Breaking Bad (2008)/Season 1/Breaking.Bad.S01E01.720p.mkv")
        touch(self.dl, "Breaking Bad (2008)/Season 1/Breaking.Bad.S01E02.720p.mkv")
        self.call("PUT", "/api/settings", {"downloads_folder": str(self.dl)})
        self.app.sorter.run_now()

    def episode(self, data, key):
        return next(e for e in data["episodes"] if e["key"] == key)

    def test_every_episode_and_its_file(self):
        status, data = self.call("GET", f"/api/items/{self.show['id']}/connections")
        self.assertEqual(status, 200, data)
        self.assertTrue(data["series"])
        self.assertTrue(self.episode(data, "1:1")["path"].endswith("S01E01.720p.mkv"))
        self.assertIsNone(self.episode(data, "1:3")["path"])
        self.assertEqual({f["source"] for f in data["files"]}, {"download"})

    def test_change_which_episode_a_file_is(self):
        _, data = self.call("GET", f"/api/items/{self.show['id']}/connections")
        second = self.episode(data, "1:2")["path"]
        status, data = self.call("POST", f"/api/items/{self.show['id']}/connections", {"path": second, "key": "1:5"})
        self.assertEqual(status, 200, data)
        self.assertEqual(self.episode(data, "1:5")["path"], second)
        self.assertIsNone(self.episode(data, "1:2")["path"])
        # Back to going by its name.
        _, data = self.call("POST", f"/api/items/{self.show['id']}/connections", {"path": second, "key": "auto"})
        self.assertEqual(self.episode(data, "1:2")["path"], second)

    def test_sort_again(self):
        status, res = self.call("POST", f"/api/items/{self.show['id']}/connections/redo")
        self.assertEqual(status, 200, res)
        self.assertEqual(res["files"], 2)


class QuickFindTest(ServerBase):
    def test_a_single_word_finds_the_title(self):
        status, res = self.call("POST", "/api/find/quick", {"q": "frieren"})
        self.assertEqual(status, 200, res)
        self.assertTrue(res["results"])
        self.assertIn("Frieren", res["results"][0]["title"])
        _, res = self.call("POST", "/api/find/quick", {"q": "x"})
        self.assertEqual(res["results"], [])


class YourOwnAITest(unittest.TestCase):
    def settings(self, **extra):
        s = {"gemini_api_key": "g", "groq_api_key": "", "openrouter_api_key": "",
             "ai_custom": [{"id": "c1", "name": "My Claude", "kind": "anthropic", "url": "https://api.anthropic.com/v1",
                            "key": "sk-1", "model": "claude-test"},
                           {"id": "o1", "name": "Ollama", "kind": "openai", "url": "http://localhost:11434/v1",
                            "key": "", "model": "llama3"}],
             "ai_order": ["custom:c1", "gemini"]}
        s.update(extra)
        return s

    def test_order_and_who_is_set_up(self):
        s = self.settings()
        self.assertEqual(ai.order(s)[:2], ["custom:c1", "gemini"])
        # Ollama on this computer needs no key; Groq and OpenRouter have none.
        self.assertEqual(ai.available(s), ["custom:c1", "gemini", "custom:o1"])
        self.assertEqual(ai.NAMES["custom:c1"], "My Claude")

    def test_claude_is_asked_with_its_own_api(self):
        calls = []

        def fake(url, service, headers=None, body=None, timeout=20, method=None):
            calls.append((url, headers, body))
            return {"content": [{"type": "text", "text": '{"ok": true}'}]}
        with mock.patch.object(net, "request_json", side_effect=fake):
            reply, provider = ai.ask(self.settings(), "Say ok", fresh=True)
        self.assertEqual((reply, provider), ({"ok": True}, "custom:c1"))
        url, headers, body = calls[0]
        self.assertEqual(url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(headers["x-api-key"], "sk-1")
        self.assertEqual(body["model"], "claude-test")

    def test_helpers_are_checked_when_saved(self):
        with self.assertRaises(ValueError):
            clean_ai_custom([{"name": "x", "url": "ftp://nope"}])
        [conf] = clean_ai_custom([{"name": "DeepSeek", "url": "https://api.deepseek.com/v1/", "kind": "weird"}])
        self.assertEqual((conf["kind"], conf["url"]), ("openai", "https://api.deepseek.com/v1"))
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(Path(tmp) / "s.json")
            settings.update({"ai_custom": [conf], "ai_order": [f"custom:{conf['id']}", "gemini"]})
            self.assertEqual(settings.get()["ai_order"][0], f"custom:{conf['id']}")


class YourPlayerAndBrowserTest(ServerBase):
    def test_another_player_of_your_choice(self):
        mpc = touch(self.media, "MPC-HC/mpc-hc64.exe")
        video = touch(self.media, "show/Breaking.Bad.S01E01.mkv")
        show = self.add("tv", "tmdb", "tv/1396")
        self.call("PUT", f"/api/items/{show['id']}", {"local_folder": str(video.parent)})
        status, _ = self.call("PUT", "/api/settings", {"player": "custom", "custom_player_path": str(mpc)})
        self.assertEqual(status, 200)
        status, res = self.call("POST", f"/api/items/{show['id']}/play", {"path": str(video)})
        self.assertEqual(status, 200, res)
        self.assertEqual((res["player"], res["player_name"]), ("custom", "mpc-hc64"))
        self.assertEqual(self.spawned[-1], [str(mpc), str(video)])
        status, _ = self.call("PUT", "/api/settings", {"player": "winamp"})
        self.assertEqual(status, 400)

    def test_built_in_browser(self):
        opened = []
        fake = mock.Mock()
        fake.create_window.side_effect = lambda title, url, **kw: opened.append(url)
        with mock.patch.object(launcher, "_webview", fake):
            launcher.open_url("https://example.com/x", launcher.BUILTIN_BROWSER)
        self.assertEqual(opened, ["https://example.com/x"])
        # No app window (the app runs in a browser): the default browser opens it instead.
        launcher.open_url("https://example.com/y", launcher.BUILTIN_BROWSER)
        self.assertEqual(self.opened[-1], "https://example.com/y")
        self.call("PUT", "/api/settings", {"browser_path": "builtin"})
        _, state = self.call("GET", "/api/state")
        self.assertEqual(state["status"]["browser"], "builtin")


class PlayerLookTest(unittest.TestCase):
    def test_modern_controls_when_bundled(self):
        with tempfile.TemporaryDirectory() as tmp:
            ui = Path(tmp)
            with mock.patch.object(player, "UI_DIR", ui):
                plain = player.script_args(True)
                self.assertIn("--script-opts-append=osc-layout=bottombar", plain)
                self.assertNotIn("--osc=no", plain)
                touch(ui, "scripts/uosc/main.lua")
                modern = player.script_args(False)
                self.assertIn(f"--config-dir={ui}", modern)
                self.assertIn("--osc=no", modern)
                self.assertIn("--script-opts-append=watchlist-skip=no", modern)

    def test_its_settings_ship_with_the_app(self):
        conf = (player.UI_DIR / "script-opts" / "uosc.conf").read_text(encoding="utf-8")
        self.assertIn("color=foreground=7c5cff", conf)


if __name__ == "__main__":
    unittest.main()
