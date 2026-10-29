"""The phone version's own pieces, without a phone: libtorrent for Android
(its Java side stood in for) and the phone player's watch-party bridge."""

import importlib
import os
import sys
import tempfile
import types
import unittest
from unittest import mock


def _fake_java():
    java = types.ModuleType("java")
    java.jclass = lambda name: mock.MagicMock(name=name)
    java.jarray = lambda kind: (lambda data: data)
    java.jint = int
    return java


class LtAndroidTest(unittest.TestCase):
    def setUp(self):
        with mock.patch.dict(sys.modules, {"java": _fake_java()}):
            sys.modules.pop("watchlist.lt_android", None)
            self.lt = importlib.import_module("watchlist.lt_android")
        self.addCleanup(sys.modules.pop, "watchlist.lt_android", None)

    def test_bencode_round_trip_keeps_the_info_dict_exactly(self):
        lt = self.lt
        # Keys out of order inside "info": re-encoding would change its hash; it must come back as it was.
        info = b"d4:name3:abc6:lengthi5ee"
        torrent = b"d8:announce3:url4:info" + info + b"e"
        decoded = lt.bdecode(torrent)
        self.assertEqual(bytes(decoded[b"info"]), info)
        self.assertEqual(decoded[b"announce"], b"url")
        self.assertEqual(lt.bencode({"info": lt.bdecode(info)}), b"d4:info" + lt.bencode(lt.bdecode(info)) + b"e")
        self.assertEqual(lt.bencode(decoded), torrent)
        self.assertEqual(lt.bdecode(lt.bencode({"a": [1, b"x", {"b": 2}]})), {b"a": [1, b"x", {b"b": 2}]})

    def test_piece_read_from_the_files_it_spans(self):
        lt = self.lt
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "show"))
            with open(os.path.join(d, "show", "a.mkv"), "wb") as f:
                f.write(b"A" * 6)
            with open(os.path.join(d, "show", "c.mkv"), "wb") as f:
                f.write(b"C" * 6)
            files = mock.Mock()
            layout = [(0, 6, "show/a.mkv"), (6, 4, "show/b.mkv"), (10, 6, "show/c.mkv")]  # b isn't kept
            files.num_files.return_value = 3
            files.file_offset.side_effect = lambda i: layout[i][0]
            files.file_size.side_effect = lambda i: layout[i][1]
            files.file_path.side_effect = lambda i: layout[i][2]
            ti = mock.Mock()
            ti.files.return_value = files
            ti.piece_length.return_value = 8
            ti.piece_size.side_effect = lambda p: 8 if p == 0 else 8
            handle = mock.Mock()
            handle.torrent_file.return_value = ti
            handle.info_hashes.return_value.get_best.return_value = "h1"
            self.assertEqual(lt.piece_bytes(handle, 0, d), b"AAAAAA\0\0")
            self.assertEqual(lt.piece_bytes(handle, 1, d), b"\0\0CCCCCC")


class PhoneSessionTest(unittest.TestCase):
    """The phone player as a watch party sees it: the same calls as mpv's Session."""

    def setUp(self):
        from watchlist import phone_player
        self.pp = phone_player
        self.bridge = mock.Mock()
        phone_player.set_bridge(self.bridge)
        self.addCleanup(phone_player.set_bridge, None)

    def commands(self):
        import json
        return [json.loads(c.args[1]) for c in self.bridge.playerCommand.call_args_list]

    def test_it_follows_what_the_player_reports(self):
        s = self.pp.PhoneSession("i1", [{"path": "/a.mkv", "key": "1:1", "label": "E1"},
                                        {"path": "/b.mkv", "key": "1:2", "label": "E2"}], paused=True)
        heard = []
        s.listeners.append(lambda session, msg: heard.append(msg["name"]))
        self.assertFalse(s.connected)
        self.assertIsNone(s.position())
        self.pp.event(s.id, '{"index": 0, "pos": 12.5, "dur": 1400, "paused": true}')
        self.assertTrue(s.connected)
        self.assertEqual((s.pos, s.dur, s.paused, s.position()), (12.5, 1400.0, True, 12.5))
        self.assertEqual(heard, ["duration"])
        self.pp.event(s.id, {"index": 1, "pos": 0, "dur": 0, "paused": False})
        self.assertEqual((s.index, s.current()["key"], s.dur), (1, "1:2", None))
        self.assertIn("playlist-pos", heard)
        self.assertIs(self.pp.current(), s)
        self.pp.event(s.id, {"ended": True})
        self.assertTrue(s.ended)
        self.assertIsNone(self.pp.current())

    def test_a_party_drives_it(self):
        s = self.pp.PhoneSession("i1", [{"path": "/a.mkv", "key": "movie"}])
        self.pp.event(s.id, {"index": 0, "pos": 5, "dur": 100, "paused": False})
        s.send(["seek", 42.0, "absolute"], ["set_property", "pause", True])
        s.send(["set_property", "speed", 0.95])
        s.send(["show-text", "Sam paused", 2000])
        cmds = self.commands()
        self.assertEqual(cmds[0], [{"do": "seek", "to": 42.0}, {"do": "pause"}])
        self.assertEqual(cmds[1], [{"do": "speed", "to": 0.95}])
        self.assertEqual(cmds[2], [{"do": "text", "text": "Sam paused", "ms": 2000}])
        self.assertEqual((s.position(), s.paused), (42.0, True))
        s.replace({"path": "/c.mkv", "key": "movie", "label": "Other"})
        self.assertEqual(self.commands()[3][0]["launch"]["entries"][0]["path"], "/c.mkv")
        s.stop()
        self.assertEqual(self.commands()[4], [{"do": "close"}])
        self.assertTrue(s.ended)

    def test_one_player_at_a_time(self):
        a = self.pp.PhoneSession("i1", [{"path": "/a.mkv", "key": "movie"}])
        b = self.pp.PhoneSession("i2", [{"path": "/b.mkv", "key": "movie"}], keep_open=True)
        self.assertTrue(a.ended)
        self.assertIs(self.pp.current(), b)
        b.open()
        import json
        launch = json.loads(self.bridge.openPlayer.call_args.args[0])
        self.assertEqual((launch["session"], launch["keep_open"], launch["item_id"]), (b.id, True, "i2"))
        b.stop()


if __name__ == "__main__":
    unittest.main()
