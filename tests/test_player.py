import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.test_library import add
from tests.test_scanner import touch
from watchlist import player
from watchlist.server import App

FAKE = [sys.executable, str(Path(__file__).with_name("fake_mpv.py"))]


@unittest.skipIf(sys.platform == "win32", "the fake player uses a Unix socket")
class PlayerTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.args_file = root / "args.json"
        dl = root / "Downloads"
        for n in (1, 2, 3):
            touch(dl, f"Breaking.Bad.S01E0{n}.mkv")
        self.app = App(root / "data")
        self.app.settings.update({"downloads_folder": str(dl)})
        self.show = add(self.app, "tv", "Breaking Bad", seasons=[{"number": 1, "episodes": 7}])
        self.app.library.scan(force=True)
        patcher = mock.patch.object(player, "find_mpv", return_value=FAKE)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_player(self, plan, episode=1, **body):
        os.environ["FAKE_MPV_PLAN"] = json.dumps(plan)
        os.environ["FAKE_MPV_ARGS"] = str(self.args_file)
        self.addCleanup(os.environ.pop, "FAKE_MPV_PLAN", None)
        info = self.app.local_info(self.app.store.get(self.show["id"]), 1, episode, "")
        res = self.app.play(self.app.store.get(self.show["id"]),
                            {"path": info["target"]["path"], "season": 1, "episode": episode, **body})
        session = self.app.player.session
        deadline = time.time() + 15
        while not session.ended and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue(session.ended, "the player session should end")
        self.assertTrue(session.connected, session.error)
        return res, json.loads(self.args_file.read_text())

    def test_watching_marks_episodes_and_saves_your_place(self):
        res, args = self.run_player([{"dur": 1400, "to": 1400, "eof": True},
                                     {"dur": 1400, "to": 600}])
        self.assertEqual(res["player"], "mpv")
        self.assertEqual(res["queued"], 2)
        self.assertIn("--force-media-title=Breaking Bad - S1 E1", args)
        item = self.app.store.get(self.show["id"])
        self.assertEqual(item["progress"], {"season": 1, "episode": 2}, "episode 1 was finished")
        self.assertNotIn("1:1", item["positions"])
        self.assertEqual(item["positions"]["1:2"]["pos"], 600)

        # Watching episode 2 again resumes just before where you stopped.
        _, args = self.run_player([{"dur": 1400, "to": 1300}], episode=2)
        self.assertIn("--start=595", args)
        item = self.app.store.get(self.show["id"])
        self.assertEqual(item["progress"], {"season": 1, "episode": 3},
                         "stopping in the end credits counts as watched")

    def test_picking_an_earlier_episode_continues_from_there(self):
        self.app.store.update(self.show["id"], {"progress": {"season": 1, "episode": 3}})
        self.run_player([{"dur": 1400, "to": 1400, "eof": True}], episode=1)
        self.assertEqual(self.app.store.get(self.show["id"])["progress"], {"season": 1, "episode": 2},
                         "you picked episode 1 and finished it, so episode 2 is next")

    def test_resume_rules(self):
        self.assertEqual(player.resume_point(None), 0)
        self.assertEqual(player.resume_point({"pos": 10, "dur": 1400}), 0)
        self.assertEqual(player.resume_point({"pos": 700, "dur": 1400}), 695)
        self.assertEqual(player.resume_point({"pos": 1350, "dur": 1400}), 0)
        self.assertTrue(player.finished(1290, 1400))
        self.assertFalse(player.finished(700, 1400))

    def test_switching_to_vlc_continues_where_you_are(self):
        os.environ["FAKE_MPV_PLAN"] = json.dumps([{"dur": 1400, "to": 1400, "eof": True}])
        self.addCleanup(os.environ.pop, "FAKE_MPV_PLAN", None)
        session = player.Session(FAKE, self.show["id"], [
            {"path": "/a.mkv", "key": "1:1", "label": "x"}, {"path": "/b.mkv", "key": "1:2", "label": "y"}])
        session.index, session.pos = 1, 321.0
        session.ended = False
        self.app.player.session = session
        spawned = []
        with mock.patch("watchlist.launcher._spawn", side_effect=spawned.append), \
                mock.patch("watchlist.launcher.find_vlc", return_value="vlc"):
            self.app.switch_to_vlc()
        self.assertEqual(spawned[0][3:], ["/b.mkv", ":start-time=321"])


if __name__ == "__main__":
    unittest.main()
