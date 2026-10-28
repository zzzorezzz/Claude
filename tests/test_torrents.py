"""Streaming episodes from a torrent: a real torrent, shared on this computer."""

import http.client
import os
import shutil
import subprocess
import tempfile
import time
import unittest
import urllib.parse
import warnings
from pathlib import Path
from unittest import mock

from tests import test_server
from watchlist import torrents
from watchlist.store import new_item

lt = torrents.lt
LOCAL = {"user_agent": "Watchlist test", "listen_interfaces": "127.0.0.1:0", "enable_dht": False,
         "enable_lsd": False, "enable_upnp": False, "enable_natpmp": False,
         "allow_multiple_connections_per_ip": True}


def wait_for(check, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        if check():
            return True
        time.sleep(0.1)
    return False


@unittest.skipUnless(lt, "libtorrent isn't installed")
class TorrentTest(test_server.ServerBase):
    def setUp(self):
        patch = mock.patch.object(torrents.Torrents, "SETTINGS", LOCAL)
        patch.start()
        self.addCleanup(patch.stop)
        super().setUp()
        self.app.torrents.port = self.port
        # Someone sharing a season: three episodes and a subtitle.
        self.seed_dir = Path(self.tmp.name) / "seed"
        show = self.seed_dir / "[Group] Mystery Show"
        show.mkdir(parents=True)
        self.data = {}
        for n in (1, 2, 3):
            name = f"[Group] Mystery Show - {n:02d} [1080p].mkv"
            self.data[n] = os.urandom(1_500_000 + n * 1000)
            (show / name).write_bytes(self.data[n])
        (show / "[Group] Mystery Show - 02 [1080p].ass").write_text("[Script Info]\nTitle: two\n")
        (show / "readme.txt").write_text("hi")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            fs = lt.file_storage()
            lt.add_files(fs, str(show))
            t = lt.create_torrent(fs, 65536)
            lt.set_piece_hashes(t, str(self.seed_dir))
            ti = lt.torrent_info(lt.bencode(t.generate()))
        self.magnet = lt.make_magnet_uri(ti)
        self.seeder = lt.session(LOCAL)
        p = lt.add_torrent_params()
        p.ti, p.save_path = ti, str(self.seed_dir)
        self.seed_handle = self.seeder.add_torrent(p)
        wait_for(lambda: self.seed_handle.status().is_seeding)
        self.addCleanup(self.app.torrents.shutdown)
        self.item = self.app.store.add(new_item("anime", {
            "source": "mal", "source_id": "1", "title": "Mystery Show", "format": "series",
            "seasons": [{"number": 1, "episodes": 12}]}))

    def connect(self, h):
        handle = self.app.torrents.handles[h]
        handle.connect_peer(("127.0.0.1", self.seeder.listen_port()))

    def add(self):
        status, t = self.call("POST", f"/api/items/{self.item['id']}/torrents", {"magnet": self.magnet})
        self.assertEqual(status, 200, t)
        self.assertTrue(self.connected(t["hash"]), "no file list")
        return t["hash"]

    def connected(self, h, timeout=40):
        """Until the torrent has its list of files from the test's sharer. (Asked
        again every second or so: on a busy computer a first try can be lost.)"""
        end, last = time.time() + timeout, 0
        while time.time() < end:
            if self.app.torrents.records.get(h, {}).get("files"):
                return True
            if time.time() - last > 1 and h in self.app.torrents.handles:
                self.connect(h)
                last = time.time()
            time.sleep(0.1)
        return False

    def fetch(self, url, start=None, end=None):
        u = urllib.parse.urlsplit(url)
        conn = http.client.HTTPConnection(u.hostname, u.port, timeout=30)
        headers = {"Host": f"127.0.0.1:{self.port}"}
        if start is not None:
            headers["Range"] = f"bytes={start}-{'' if end is None else end}"
        conn.request("GET", u.path, headers=headers)
        r = conn.getresponse()
        body = r.read()
        conn.close()
        return r.status, body, dict(r.getheaders())

    def test_files_are_matched_to_episodes_before_anything_downloads(self):
        h = self.add()
        status, data = self.call("GET", f"/api/items/{self.item['id']}/torrents")
        self.assertEqual(status, 200, data)
        self.assertEqual(sorted(data["episodes"]), ["1:1", "1:2", "1:3"])
        [t] = data["torrents"]
        kinds = sorted(f["kind"] for f in t["files"])
        self.assertEqual(kinds, ["other", "subtitle", "video", "video", "video"])
        time.sleep(1)
        self.assertEqual(t["wanted_done"], 0)                          # nothing downloaded yet
        self.assertFalse(any(Path(self.app.torrents.records[h]["save_path"]).rglob("*.mkv")))

    def test_streaming_one_episode_downloads_only_that_one(self):
        h = self.add()
        info = self.app.local_info(self.app.store.get(self.item["id"]), 1, 2)
        ep = info["by_episode"]["1:2"]
        self.assertTrue(ep["stream"])
        status, body, headers = self.fetch(ep["path"], 100, 299)
        self.assertEqual((status, body), (206, self.data[2][100:300]))
        self.assertEqual(headers["Content-Range"], f"bytes 100-299/{len(self.data[2])}")
        status, body, _ = self.fetch(ep["path"])
        self.assertEqual((status, body), (200, self.data[2]))
        status, body, _ = self.fetch(ep["path"], len(self.data[2]) - 10)   # the end, as players look there
        self.assertEqual(body, self.data[2][-10:])
        rec = self.app.torrents.records[h]
        self.assertTrue(wait_for(lambda: self.app.torrents.complete(rec, self.app.torrents._file(rec, self._index(h, "02 [1080p].mkv")))))
        view = self.app.torrents.view(h)
        done = {Path(f["path"]).name: f["complete"] for f in view["files"]}
        self.assertFalse(done["[Group] Mystery Show - 01 [1080p].mkv"])
        self.assertFalse(done["[Group] Mystery Show - 03 [1080p].mkv"])
        # Finished: it's played from the file now, and is "on your computer".
        info = self.app.local_info(self.app.store.get(self.item["id"]), 1, 2)
        self.assertFalse(info["by_episode"]["1:2"]["stream"])
        self.assertTrue(os.path.isfile(info["by_episode"]["1:2"]["path"]))

    def _index(self, h, ending):
        return next(f["index"] for f in self.app.torrents.records[h]["files"] if f["path"].endswith(ending))

    def test_the_subtitle_comes_first(self):
        h = self.add()
        subs = self.app.torrents.subtitles(h, self._index(h, "02 [1080p].mkv"), wait=15)
        self.assertEqual([Path(p).name for p in subs], ["[Group] Mystery Show - 02 [1080p].ass"])

    def finish_episode_two(self, h):
        index = self._index(h, "02 [1080p].mkv")
        self.app.torrents.prepare(h, index)
        rec = self.app.torrents.records[h]
        self.assertTrue(wait_for(lambda: self.app.torrents.complete(rec, self.app.torrents._file(rec, index))))
        self.app.torrents.finished_watching(h, index)
        return index, rec

    def test_watched_to_the_end_is_deleted_a_few_minutes_later(self):
        h = self.add()
        index, rec = self.finish_episode_two(h)
        path = self.app.torrents.path_of(rec, self.app.torrents._file(rec, index))
        self.app.torrents._tick()
        self.assertTrue(os.path.isfile(path))                          # not straight away
        rec["finished"][str(index)] -= torrents.DELETE_AFTER + 1
        # Not while the next episode (same torrent) is streaming...
        self.app.torrents.reading = {(h, self._index(h, "03 [1080p].mkv")): time.time()}
        self.app.torrents._tick()
        self.assertTrue(os.path.isfile(path))
        # ...but once it stops.
        self.app.torrents.reading.clear()
        self.app.torrents._tick()
        self.assertFalse(os.path.exists(path))
        self.assertEqual(rec["priorities"][str(index)], 0)              # and not downloaded again

    def test_kept_when_keep_is_ticked(self):
        h = self.add()
        self.call("POST", f"/api/items/{self.item['id']}/torrent-keep", {"keep": True})
        index, rec = self.finish_episode_two(h)
        rec["finished"][str(index)] -= torrents.DELETE_AFTER + 1
        self.app.torrents.reading.clear()
        self.app.torrents._tick()
        self.assertTrue(os.path.isfile(self.app.torrents.path_of(rec, self.app.torrents._file(rec, index))))

    def test_by_hand(self):
        h = self.add()
        three = self._index(h, "03 [1080p].mkv")
        # Choose a file to download, say which episode it is, delete it, remove the torrent.
        status, view = self.call("POST", f"/api/torrents/{h}/files/{three}", {"priority": 4, "episode": "1:7"})
        self.assertEqual(status, 200, view)
        self.assertEqual(self.app.torrents.episodes(self.app.store.get(self.item["id"]))["1:7"]["index"], three)
        rec = self.app.torrents.records[h]
        self.assertTrue(wait_for(lambda: self.app.torrents.complete(rec, self.app.torrents._file(rec, three))))
        path = self.app.torrents.path_of(rec, self.app.torrents._file(rec, three))
        status, _ = self.call("POST", f"/api/items/{self.item['id']}/delete-file", {"path": path})
        self.assertEqual(status, 200)
        self.assertFalse(os.path.exists(path))
        status, view = self.call("POST", f"/api/torrents/{h}/pause", {"paused": True})
        self.assertEqual(view["state"], "paused")
        status, _ = self.call("DELETE", f"/api/torrents/{h}")
        self.assertEqual(status, 200)
        self.assertEqual(self.app.torrents.records, {})

    def test_watch_party_tells_the_other_side_the_torrent(self):
        h = self.add()
        item = self.app.store.get(self.item["id"])
        url = self.app.local_info(item, 1, 1)["by_episode"]["1:1"]["path"]
        entries, _ = self.app._entries(item, url, None, 1, 1)
        self.assertEqual(entries[0]["party"]["magnet"], self.magnet)
        self.assertEqual(entries[0]["name"], "[Group] Mystery Show - 01 [1080p].mkv")
        self.assertEqual([e["key"] for e in entries], ["1:1", "1:2", "1:3"])       # the next ones queue up
        self.assertTrue(self.app.playable(item, url))
        self.assertEqual(self.app.torrents.records[h]["item_id"], item["id"])

    def test_a_guest_streams_the_hosts_torrent(self):
        import threading
        # The guest has the show but no copy of the episode: the invite's magnet is used.
        def connect_when_added():
            for _ in range(400):
                for h in list(self.app.torrents.handles):
                    self.connected(h)
                    return
                time.sleep(0.05)
        threading.Thread(target=connect_when_added, daemon=True).start()
        entry = self.app._party_entry(self.app.store.get(self.item["id"]), 1, 3, self.magnet)
        self.assertIsNotNone(entry)
        self.assertTrue(self.app.torrents.parse_url(entry["path"]))
        self.assertEqual(entry["key"], "1:3")
        status, body, _ = self.fetch(entry["path"], 0, 99)
        self.assertEqual(body, self.data[3][:100])

    @unittest.skipUnless(shutil.which("mpv"), "needs mpv")
    def test_mpv_plays_a_stream(self):
        """The built-in player really plays (and seeks in) an episode as it streams."""
        show = self.seed_dir / "[Group] Mystery Show"
        video = Path(__file__).with_name("sample.mkv").read_bytes()
        (show / "[Group] Mystery Show - 04 [1080p].mkv").write_bytes(video)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            fs = lt.file_storage()
            lt.add_files(fs, str(show))
            t = lt.create_torrent(fs, 16384)
            lt.set_piece_hashes(t, str(self.seed_dir))
            ti = lt.torrent_info(lt.bencode(t.generate()))
        self.magnet = lt.make_magnet_uri(ti)
        p = lt.add_torrent_params()
        p.ti, p.save_path = ti, str(self.seed_dir)
        self.seeder.remove_torrent(self.seed_handle)
        self.seed_handle = self.seeder.add_torrent(p)
        wait_for(lambda: self.seed_handle.status().is_seeding)
        self.add()
        url = self.app.local_info(self.app.store.get(self.item["id"]), 1, 4)["by_episode"]["1:4"]["path"]
        result = subprocess.run(["mpv", "--no-config", "--vo=null", "--ao=null", "--start=4", "--length=2",
                                 "--network-timeout=60", url], capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("End of file", result.stdout + result.stderr)

    def test_getting_an_episode_ready_first(self):
        """Before the player opens: its start and end, from this file alone."""
        h = self.add()
        eps = self.app.torrents.episodes(self.app.store.get(self.item["id"]))
        self.app.torrents.set_priority(h, eps["1:3"]["index"], 4)        # wanted before
        url = self.app.torrents.stream_url(h, eps["1:2"]["index"], "x.mkv")
        body = {"path": url, "season": 1, "episode": 2}
        status, st = self.call("POST", f"/api/items/{self.item['id']}/stream-buffer", body)
        self.assertEqual(status, 200, st)
        self.assertGreater(st["need"], 0)
        self.assertTrue(wait_for(lambda: self.call("POST", f"/api/items/{self.item['id']}/stream-buffer", body)[1]["ready"]))
        prio = {f["index"]: f["priority"] for f in self.app.torrents.view(h)["files"]}
        self.assertEqual((prio[eps["1:2"]["index"]], prio[eps["1:3"]["index"]]), (7, 0))   # this one only
        status, _ = self.call("POST", f"/api/items/{self.item['id']}/stream-buffer", {"path": "/etc/passwd"})
        self.assertEqual(status, 400)

    def test_peeking_inside_a_torrent_adds_nothing(self):
        """Get magnet compares torrents by their files before one is added."""
        magnet = f"{self.magnet}&x.pe=127.0.0.1:{self.seeder.listen_port()}"
        h = self.app.torrents.hash_of_magnet(magnet)
        got = self.app.torrents.peek_many([magnet], wait=20)
        self.assertEqual(len(got[h]["files"]), 5)
        self.assertEqual((self.app.torrents.records, self.app.torrents.handles), ({}, {}))
        # Added afterwards: its list of files is there at once.
        status, t = self.call("POST", f"/api/items/{self.item['id']}/torrents", {"magnet": self.magnet})
        self.assertEqual(status, 200, t)
        self.assertTrue(wait_for(lambda: self.app.torrents.records[t["hash"]].get("files"), timeout=5))

    def test_after_a_restart(self):
        h = self.add()
        self.app.torrents._save()
        again = torrents.Torrents(self.app, Path(self.app.data_dir))
        self.addCleanup(again.shutdown)
        self.assertIn(h, again.handles)
        self.assertEqual(len(again.records[h]["files"]), 5)


class NoTorrentsTest(unittest.TestCase):
    def test_magnet_links_are_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            from watchlist.server import App
            app = App(Path(tmp))
            with self.assertRaises(torrents.TorrentError):
                app.torrents.add("x", "https://example.com/not-a-magnet")
