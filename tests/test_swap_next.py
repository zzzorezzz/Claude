"""After an episode's file is swapped (Sub / Dub, Try another source), Next still moves the
player on - its title, and what a watch party is told (Haikyuu: the old episode's name
stayed and the party stopped following)."""
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from watchlist import party as party_mod
from watchlist import player

SAMPLE = Path(__file__).with_name("sample.mkv")
MPV = os.environ.get("WATCHLIST_MPV") or shutil.which("mpv") or ""


def wait_for(check, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if check():
            return True
        time.sleep(0.1)
    return False


@unittest.skipUnless(MPV and SAMPLE.exists(), "mpv isn't installed")
class SwapThenNextTest(unittest.TestCase):
    def test_next_after_a_swap(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        files = []
        for name in ("ep1.mkv", "ep2.mkv", "ep3.mkv", "ep1-dub.mkv"):
            shutil.copy(SAMPLE, Path(tmp.name) / name)
            files.append(str(Path(tmp.name) / name))
        entries = [{"path": f, "key": f"1:{i + 1}", "label": f"Episode {i + 1}"} for i, f in enumerate(files[:3])]
        moves = []
        s = player.Session(MPV, "x", entries, playlist=True, extra_args=["--vo=null", "--ao=null", "--loop-file=inf"])
        s.listeners.append(lambda sess, m: m.get("moved") and moves.append(m["data"]))
        s.start()
        self.addCleanup(s.stop)
        self.assertTrue(wait_for(lambda: s.dur is not None))
        s.swap({"path": files[3], "key": "1:1", "label": "Episode 1 (dub)"}, 1)
        self.assertTrue(wait_for(lambda: s._swapping is None))
        self.assertEqual((s.index, moves), (0, []))          # (still episode 1)
        s.send(["playlist-next"])
        self.assertTrue(wait_for(lambda: s.index == 1))
        self.assertEqual(s.status()["label"], "Episode 2")
        self.assertEqual(moves, [1])


class FakeSession:
    def __init__(self):
        self.listeners, self.sent, self.index = [], [], 0
        self.ended, self.connected, self.paused = False, True, False

    def send(self, *commands):
        self.sent.extend(commands)
        return True


class HostWaitsOnEveryEpisodeTest(unittest.TestCase):
    def party(self):
        p = party_mod.Party("127.0.0.1:1", "room", "me", "host")
        p.active, p.people = True, {"me": None, "her": None}
        s = FakeSession()
        p.attach(s)
        return p, s

    def test_a_move_pauses_the_host_until_everyone_is_ready(self):
        p, s = self.party()
        p._player_message(s, {"event": "property-change", "name": "playlist-pos", "data": 1, "moved": True})
        self.assertTrue(wait_for(lambda: ["set_property", "pause", True] in s.sent, 2))
        self.assertGreater(p._hold_until, time.time())

    def test_mpvs_own_count_isnt_a_move(self):
        p, s = self.party()
        p._announced = True
        p._player_message(s, {"event": "property-change", "name": "playlist-pos", "data": 1})
        time.sleep(0.2)
        self.assertEqual((s.sent, p._hold_until, p._announced), ([], 0.0, True))
