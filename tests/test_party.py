"""Watch parties: two copies of the app, each with its own real mpv, kept in step
through a stand-in Syncplay server. Skipped where mpv isn't installed."""

import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.fake_syncplay import FakeSyncplayServer
from tests.test_library import add
from watchlist import party as party_mod
from watchlist import player, syncplay
from watchlist.server import App

MPV = shutil.which("mpv")


def wait_for(check, timeout=12.0, step=0.1):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if check():
                return True
        except Exception:  # noqa: BLE001 - not ready yet
            pass
        time.sleep(step)
    return False


@unittest.skipUnless(MPV, "mpv isn't installed")
class PartyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.videos = {}
        for ep in (5, 6):
            out = root / f"ep{ep}.mkv"
            subprocess.run([MPV, "--no-config", "av://lavfi:testsrc=duration=60:size=64x48:rate=5",
                            f"--o={out}", "--of=matroska", "--ovc=mpeg4", "--oac=no", "--really-quiet"],
                           check=True, timeout=120)
            cls.videos[ep] = out

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def make_app(self, name, files):
        root = Path(tempfile.mkdtemp(dir=self.tmp.name)) / name
        dl = root / "downloads"
        dl.mkdir(parents=True)
        for ep, filename in files.items():
            shutil.copy(self.videos[ep], dl / filename)
        app = App(root / "data")
        app.settings.update({"downloads_folder": str(dl), "party_server": self.server.address,
                             "party_name": name, "skip_intro": False})
        item = add(app, "anime", "Test Show", source_id="123", aliases=["Test Show"],
                   seasons=[{"number": 1, "episodes": 12}])
        app.library.scan(force=True)
        app.sorter.run_now()
        return app, item

    def setUp(self):
        self.server = FakeSyncplayServer()
        self.addCleanup(self.server.stop)
        p = mock.patch.object(player, "find_mpv",
                              return_value=[MPV, "--no-config", "--vo=null", "--ao=null"])
        p.start()
        self.addCleanup(p.stop)
        self.host, self.host_item = self.make_app("Ali", {5: "[Group] Test Show - 05 [1080p].mkv",
                                                          6: "[Group] Test Show - 06 [1080p].mkv"})
        self.guest, _ = self.make_app("Sara", {5: "Test.Show.S01E05.720p.mkv", 6: "Test.Show.S01E06.720p.mkv"})
        self.addCleanup(self.stop_all)

    def stop_all(self):
        for app in (self.host, self.guest):
            if app.party:
                app.party.leave("test over")
            if app.player.session and not app.player.session.ended:
                app.player.session.stop()
        for app in (self.host, self.guest):
            if app.player.session:
                wait_for(lambda: app.player.session.ended, 10)

    def pos(self, app):
        return app.player.session.position()

    def guest_session(self, ending):
        """The guest's player, once it's playing the file whose name ends so."""
        self.assertTrue(wait_for(lambda: self.guest.player.session and self.guest.player.session.current()
                                 and self.guest.player.session.current()["path"].endswith(ending), 20),
                        self.guest.party_status())
        return self.guest.player.session

    def state_of(self, app, name):
        return next(p for p in app.party_status()["people"] if p["name"] == name)

    def test_watching_together(self):
        started = self.host.party_start(self.host.store.get(self.host_item["id"]), 1, 5)
        self.assertTrue(started["ok"], started)
        self.assertEqual(syncplay.read_invite(started["code"])["host"], "Ali")
        host_session = self.host.player.session
        self.assertTrue(wait_for(lambda: host_session.connected and host_session.dur and host_session.paused))

        joined = self.guest.party_join(started["code"])
        self.assertTrue(joined["ok"], joined)
        guest_session = self.guest_session("Test.Show.S01E05.720p.mkv")     # found by episode, not by name
        guest_party = self.guest.party
        self.assertTrue(wait_for(lambda: guest_session.connected and not guest_party._init_pending))
        self.assertTrue(guest_session.paused)
        self.assertEqual({p["name"] for p in self.host.party.status()["people"]}, {"Ali", "Sara"})
        self.assertTrue(wait_for(lambda: self.state_of(self.host, "Sara")["state"] == "watching"))

        # The host presses play: both play.
        host_session.send(["set_property", "pause", False])
        self.assertTrue(wait_for(lambda: not guest_session.paused))
        time.sleep(2)
        self.assertLess(abs(self.pos(self.host) - self.pos(self.guest)), 1.5)

        # The host skips ahead: the guest follows.
        host_session.send(["seek", 30, "absolute"])
        self.assertTrue(wait_for(lambda: self.pos(self.guest) > 28))
        self.assertLess(abs(self.pos(self.host) - self.pos(self.guest)), 1.5)

        # The guest pauses: so does the host.
        guest_session.send(["set_property", "pause", True])
        self.assertTrue(wait_for(lambda: host_session.paused))
        self.assertLess(abs(self.pos(self.host) - self.pos(self.guest)), 1.5)

        # The host moves on to the next episode: the guest's app finds its own copy.
        host_session.send(["set_property", "pause", False], ["playlist-next"])
        self.assertTrue(wait_for(lambda: guest_session.current()["path"].endswith("S01E06.720p.mkv"), 15))
        self.assertTrue(wait_for(lambda: not guest_party._init_pending and guest_session.pos is not None))
        self.assertTrue(wait_for(lambda: abs(self.pos(self.host) - self.pos(self.guest)) < 1.5, 8))
        self.assertLess(self.pos(self.guest), 15)

        # The host closes the player: the party carries on (everyone paused).
        host_session.stop()
        self.assertTrue(wait_for(lambda: guest_session.paused, 10))
        time.sleep(1)
        self.assertTrue(self.host.party_status()["active"])
        self.assertTrue(self.guest.party_status()["active"])

        # The host ends the party: it's over for the guest too.
        self.host.party_leave()
        self.assertTrue(wait_for(lambda: not self.guest.party_status()["active"], 15))
        self.assertIn("Ali left", self.guest.party_status()["ended"])

    def test_a_party_without_anything_playing_yet(self):
        started = self.host.party_start()
        self.assertTrue(started["ok"], started)
        self.assertIsNone(self.host.player.session)
        joined = self.guest.party_join(started["code"])
        self.assertTrue(joined["ok"], joined)
        self.assertEqual(self.guest.party_status()["host_playing"], "")
        # The guest closes nothing and waits; the host plays something from their list.
        item = self.host.store.get(self.host_item["id"])
        target = self.host.local_info(item, 1, 6)["target"]
        self.host.play(item, {"path": target["path"], "season": 1, "episode": 6})
        guest_session = self.guest_session("S01E06.720p.mkv")
        self.assertTrue(wait_for(lambda: self.state_of(self.host, "Sara")["state"] == "watching"))
        # The guest closes their player and leaves: the host carries on.
        guest_session.stop()
        self.guest.party_leave()
        self.assertTrue(wait_for(lambda: len(self.host.party_status()["people"]) == 1, 10))
        self.assertTrue(self.host.party_status()["active"])
        self.assertFalse(self.host.player.session.ended)

    def test_guest_without_the_episode(self):
        self.guest, _ = self.make_app("Zoe", {6: "Test.Show.S01E06.mkv"})
        started = self.host.party_start(self.host.store.get(self.host_item["id"]), 1, 5)
        self.assertTrue(wait_for(lambda: self.host.party.people.get("Ali")))
        joined = self.guest.party_join(started["code"])
        self.assertTrue(joined["ok"], joined)
        self.assertTrue(wait_for(lambda: self.guest.party_status()["mine"]["status"] == "needs-file"))
        self.assertIn("Test Show - S1 E5", self.guest.party_status()["missing"])
        # The host sees it next to the guest's name.
        self.assertTrue(wait_for(lambda: self.state_of(self.host, "Zoe")["state"] == "needs-file"))
        # Picking the file by hand works.
        path = str(Path(self.guest.settings.get()["downloads_folder"]) / "Test.Show.S01E06.mkv")
        self.assertTrue(self.guest.party_use_file(path)["ok"])
        self.guest_session("Test.Show.S01E06.mkv")
        self.assertTrue(wait_for(lambda: self.state_of(self.host, "Zoe")["state"] == "watching"))

    def test_the_host_sees_a_guest_getting_ready(self):
        started = self.host.party_start(self.host.store.get(self.host_item["id"]), 1, 5)
        self.assertTrue(wait_for(lambda: self.host.party.people.get("Ali")))
        go = threading.Event()
        real = self.guest.party_resolve

        def slow(file_, report=None, at=0.0):
            report("preparing", 0.4, 12, "Test Show - S1 E5")   # a torrent getting ready
            go.wait(15)
            return real(file_)
        with mock.patch.object(self.guest, "party_resolve", side_effect=slow):
            self.guest.party_join(started["code"])
            self.assertTrue(wait_for(lambda: self.state_of(self.host, "Sara")["state"] == "preparing"))
            sara = self.state_of(self.host, "Sara")
            self.assertEqual((sara["progress"], sara["eta"], sara["label"]), (0.4, 12, "Test Show - S1 E5"))
            self.assertEqual(self.guest.party_status()["mine"]["progress"], 0.4)
            go.set()
            self.guest_session("S01E05.720p.mkv")
            self.assertTrue(wait_for(lambda: self.state_of(self.host, "Sara")["state"] == "watching"))

    def test_finds_a_download_that_isnt_sorted_yet(self):
        self.guest, _ = self.make_app("Zoe", {5: "Test - 05.mkv"})
        self.assertEqual(len(self.guest.library.state()["questions"]), 1)   # not sure it's Test Show
        started = self.host.party_start(self.host.store.get(self.host_item["id"]), 1, 5)
        self.assertTrue(wait_for(lambda: self.host.party.people.get("Ali")))
        joined = self.guest.party_join(started["code"])
        self.assertTrue(joined["ok"], joined)
        self.guest_session("Test - 05.mkv")

    def test_old_invite(self):
        started = self.host.party_start(self.host.store.get(self.host_item["id"]), 1, 5)
        self.host.party.leave("done")
        self.host.player.session.stop()
        time.sleep(0.5)
        with self.assertRaisesRegex(Exception, "isn't in that watch party"):
            self.guest.party_join(started["code"])


class ProtocolTest(unittest.TestCase):
    """The parts that don't need a player."""

    def test_other_rooms_are_ignored(self):
        server = FakeSyncplayServer()
        self.addCleanup(server.stop)
        a = party_mod.Party(server.address, "room-a", "Ali", "host").connect()
        b = party_mod.Party(server.address, "room-b", "Bo", "guest").connect()
        c = party_mod.Party(server.address, "room-a", "Ali", "guest", leader="Ali").connect()
        self.addCleanup(lambda: [p.leave() for p in (a, b, c)])
        self.assertEqual(c.me, "Ali_")                       # names are made unique by the server
        self.assertTrue(wait_for(lambda: set(a.people) == {"Ali", "Ali_"}))
        self.assertEqual(set(b.people), {"Bo"})
        b.leave()
        time.sleep(0.5)
        self.assertEqual(set(a.people), {"Ali", "Ali_"})

    def test_unreachable_server(self):
        with self.assertRaisesRegex(party_mod.PartyError, "couldn't connect"):
            party_mod.Party("127.0.0.1:1", "r", "Ali", "host").connect(timeout=2)
