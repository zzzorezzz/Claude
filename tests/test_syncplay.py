import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_library import add
from tests.test_scanner import touch
from watchlist import launcher, player, syncplay
from watchlist.server import ApiError, App


class InviteTest(unittest.TestCase):
    def test_round_trip(self):
        item = {"title": "Frieren: Beyond Journey's End", "year": 2023, "type": "anime",
                "source": "mal", "source_id": "52991"}
        code = syncplay.make_invite("syncplay.pl:8999", "watchlist-abc", item, 1, 5)
        self.assertTrue(code.startswith("WLP1."))
        got = syncplay.read_invite(f"  here you go: {code[:20]}\n{code[20:]} ")
        self.assertEqual((got["room"], got["server"], got["source_id"], got["season"], got["episode"]),
                         ("watchlist-abc", "syncplay.pl:8999", "52991", 1, 5))

    def test_bad_codes(self):
        for code in ["", "hello", "WLP1.", "WLP1.abc", "WLP1." + "e30"]:
            with self.assertRaises(ValueError):
                syncplay.read_invite(code)

    def test_command(self):
        cmd = syncplay.command("/s/Syncplay", "syncplay.pl:8999", "Ali", "room1", "/m/mpv", "/v/ep.mkv", "/v/ep.srt")
        self.assertEqual(cmd, ["/s/Syncplay", "-a", "syncplay.pl:8999", "-n", "Ali", "-r", "room1",
                               "--player-path", "/m/mpv", "/v/ep.mkv", "--", "--sub-file=/v/ep.srt"])


class PartyTest(unittest.TestCase):
    def make_app(self, root, name):
        dl = root / f"{name}-dl"
        touch(dl, "[SubsPlease] Sousou no Frieren - 05 (1080p).mkv")
        touch(dl, "[SubsPlease] Sousou no Frieren - 05 (1080p).en.srt")
        app = App(root / f"{name}-data")
        prog = root / "Syncplay"
        prog.write_text("")
        app.settings.update({"downloads_folder": str(dl), "syncplay_path": str(prog), "party_name": name})
        return app

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.spawned = []
        for p in [mock.patch.object(launcher, "_spawn", side_effect=self.spawned.append),
                  mock.patch.object(player, "find_mpv", return_value="/bundled/mpv")]:
            p.start()
            self.addCleanup(p.stop)

    def test_host_and_guest(self):
        host = self.make_app(self.root, "Ali")
        frieren = add(host, "anime", "Frieren: Beyond Journey's End", source_id="52991",
                      aliases=["Sousou no Frieren"], seasons=[{"number": 1, "episodes": 28}])
        host.library.scan(force=True)
        out = host.party_start(host.store.get(frieren["id"]), 1, 5)
        self.assertTrue(out["ok"])
        cmd = self.spawned[-1]
        self.assertEqual(cmd[1:7], ["-a", "syncplay.pl:8999", "-n", "Ali", "-r", cmd[6]])
        self.assertIn("--player-path", cmd)
        self.assertTrue(cmd[cmd.index("--") + 1].endswith(".en.srt"))

        guest = self.make_app(self.root, "Sara")
        add(guest, "anime", "Frieren", source_id="52991", aliases=["Sousou no Frieren"],
            seasons=[{"number": 1, "episodes": 28}])
        guest.library.scan(force=True)
        joined = guest.party_join(out["code"])
        self.assertTrue(joined["ok"])
        self.assertEqual(self.spawned[-1][6], cmd[6])          # the same room
        self.assertIn("Sousou no Frieren - 05", self.spawned[-1][9])

    def test_guest_without_the_episode(self):
        host = self.make_app(self.root, "Ali")
        frieren = add(host, "anime", "Frieren", source_id="52991", aliases=["Sousou no Frieren"],
                      seasons=[{"number": 1, "episodes": 28}])
        host.library.scan(force=True)
        code = host.party_start(host.store.get(frieren["id"]), 1, 5)["code"]
        guest = App(self.root / "g-data")
        prog = self.root / "Syncplay"
        guest.settings.update({"syncplay_path": str(prog)})
        add(guest, "anime", "Frieren", source_id="52991", seasons=[{"number": 1, "episodes": 28}])
        out = guest.party_join(code)
        self.assertFalse(out["ok"])
        self.assertTrue(out["need_file"])

    def test_no_syncplay(self):
        host = self.make_app(self.root, "Ali")
        host.settings.update({"syncplay_path": ""})
        frieren = add(host, "anime", "Frieren", source_id="52991", seasons=[{"number": 1, "episodes": 28}])
        with mock.patch.object(syncplay, "find_syncplay", return_value=""):
            with self.assertRaisesRegex(ApiError, "syncplay.pl"):
                host.party_start(host.store.get(frieren["id"]), 1, 5)
