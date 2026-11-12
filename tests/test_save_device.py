"""Save to this iPad: a video got ready on the PC for a phone to keep, handed over in pieces,
and let go of once the phone has it. And a video a phone couldn't decode is converted."""

import http.client
import json
import time
import unittest
from pathlib import Path

from tests.test_remote import RemoteBase
from watchlist import transcode, webplay

SAMPLE = Path(__file__).with_name("sample.mkv")


def _info(codec="h264", fmt="mov,mp4,m4a,3gp,3g2,mj2", audio=("aac",), bits=8, pix="yuv420p"):
    return {"format": fmt, "duration": 30.0,
            "video": {"codec": codec, "bits": bits, "pix_fmt": pix, "profile": "High", "height": 1080, "fps": 24.0},
            "audio": [{"n": i, "codec": c, "lang": "eng", "title": "", "channels": 2} for i, c in enumerate(audio)],
            "subs": []}


class KeepAsIsTest(unittest.TestCase):
    def test_what_a_phone_keeps_as_it_is(self):
        self.assertTrue(transcode.keep_as_is(_info()))
        self.assertFalse(transcode.keep_as_is(_info(fmt="matroska,webm")))        # (an MKV: repackaged)
        self.assertFalse(transcode.keep_as_is(_info(audio=("ac3",))))             # (its sound converted)
        self.assertFalse(transcode.keep_as_is(_info(bits=10, pix="yuv420p10le")))  # (a picture phones can't play)
        # HEVC: always repackaged (Apple's players want it marked hvc1), even when the phone plays it.
        self.assertFalse(transcode.keep_as_is(_info(codec="hevc"), {"hevc": True}))

    def test_the_command(self):
        hevc = transcode.Mp4("in.mkv", _info(codec="hevc", fmt="matroska,webm", audio=("aac", "ac3")), "out.mp4", {"hevc": True})
        cmd = hevc.command()
        self.assertEqual(hevc.how["video"], "copy")
        self.assertEqual(cmd[cmd.index("-c:v") + 1:cmd.index("-c:v") + 4], ["copy", "-tag:v", "hvc1"])
        self.assertEqual(cmd[cmd.index("-c:a:0") + 1], "copy")
        self.assertEqual(cmd[cmd.index("-c:a:1") + 1], "aac")
        self.assertIn("+faststart", cmd)
        self.assertEqual(cmd[-3:], ["-f", "mp4", "out.mp4"])
        # Without HEVC on the phone: the picture is converted.
        self.assertEqual(transcode.Mp4("in.mkv", _info(codec="hevc"), "out.mp4", {}).how["video"], "encode")


@unittest.skipUnless(transcode.find()[0] and SAMPLE.exists(), "ffmpeg (or the sample video) isn't here")
class CopiesTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name) / "copies"
        self.copies = webplay.Copies(self.folder)

    def tearDown(self):
        for j in list(self.copies.jobs.values()):
            self.copies._drop(j)
        self.tmp.cleanup()

    def wait(self, device, cid):
        until = time.time() + 120
        while time.time() < until:
            job = self.copies.get(device, cid)
            if job["state"] != "preparing":
                return job
            time.sleep(0.3)
        self.fail("it was never made")

    def test_made_handed_over_and_let_go(self):
        info = transcode.probe(str(SAMPLE))
        cid = self.copies.start("dev", str(SAMPLE), info, {}, "1080", {"item_id": "i", "key": "1:1", "title": "T", "label": "E1"})
        # The same file asked for again meanwhile: the same copy.
        self.assertEqual(self.copies.start("dev", str(SAMPLE), info, {}, "1080", {}), cid)
        self.assertIsNone(self.copies.get("someone else", cid))
        job = self.wait("dev", cid)
        self.assertEqual(job["state"], "ready", job.get("error"))
        self.assertIn(job["how"], ("repackaged", "converted"))
        made = Path(job["path"])
        self.assertTrue(made.is_file() and made.parent == self.folder)
        self.assertEqual(job["size"], made.stat().st_size)
        out = transcode.probe(str(made))
        self.assertIn("mp4", out["format"])
        self.assertEqual(out["video"]["codec"], "h264")
        self.assertTrue(all(a["codec"] == "aac" for a in out["audio"]))
        self.assertAlmostEqual(out["duration"], info["duration"], delta=1.0)
        self.assertEqual(self.copies.of("dev"), [job])
        # The phone has it: what was made goes.
        self.assertFalse(self.copies.done("someone else", cid))
        self.assertTrue(self.copies.done("dev", cid))
        self.assertFalse(made.exists())
        self.assertEqual(self.copies.of("dev"), [])

    def test_left_too_long(self):
        info = transcode.probe(str(SAMPLE))
        cid = self.copies.start("dev", str(SAMPLE), info, {}, "1080", {})
        job = self.wait("dev", cid)
        job["at"] -= webplay.Copies.KEEP + 1
        self.copies.tidy()
        self.assertIsNone(self.copies.get("dev", cid))
        self.assertFalse(Path(job["path"]).exists())

    def test_what_was_left_from_before_goes(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / "old.mp4").write_bytes(b"x")
        webplay.Copies(self.folder)
        self.assertFalse((self.folder / "old.mp4").exists())


@unittest.skipUnless(transcode.find()[0] and SAMPLE.exists(), "ffmpeg (or the sample video) isn't here")
class SaveRoutesTest(RemoteBase):
    def get(self, path, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.lan_port, timeout=20)
        conn.request("GET", path, headers=dict({"Host": self.host}, **(headers or {})))
        r = conn.getresponse()
        body = r.read()
        conn.close()
        return r, body

    def setUp(self):
        super().setUp()
        self.token, _ = self.pair()
        self.show = self.add("tv", "tmdb", "tv/1396")
        self.app.store.update(self.show["id"], {"local_folder": str(SAMPLE.parent)})

    def tearDown(self):
        for j in list(self.app.web_copies.jobs.values()):
            self.app.web_copies._drop(j)
        super().tearDown()

    def ready(self, cid):
        until = time.time() + 120
        while time.time() < until:
            status, st, _ = self.phone("GET", f"/api/web/save/{cid}", cookie=self.token)
            self.assertEqual(status, 200, st)
            if st["state"] != "preparing":
                return st
            time.sleep(0.3)
        self.fail("it was never made")

    def test_saving_an_episode(self):
        status, r, _ = self.phone("POST", "/api/web/save", {"item_id": self.show["id"], "key": "1:1", "label": "S1 E1",
                                                            "path": str(SAMPLE), "caps": {}}, cookie=self.token)
        self.assertEqual(status, 200, r)
        self.assertEqual((r["item_id"], r["key"], r["title"], r["label"]), (self.show["id"], "1:1", self.show["title"], "S1 E1"))
        st = self.ready(r["id"])
        self.assertEqual(st["state"], "ready", st.get("error"))
        self.assertNotIn(str(SAMPLE.parent), json.dumps(st))       # (where it is stays on the PC)
        # Fetched in pieces, as the phone does.
        half = st["size"] // 2
        a = self.get(st["url"], {"Range": f"bytes=0-{half - 1}"})
        b = self.get(st["url"], {"Range": f"bytes={half}-{st['size'] - 1}"})
        self.assertEqual((a[0].status, b[0].status), (206, 206))
        self.assertEqual(len(a[1]) + len(b[1]), st["size"])
        self.assertIn(b"ftyp", a[1][:64])
        status, saves, _ = self.phone("GET", "/api/web/saves", cookie=self.token)
        self.assertEqual([x["id"] for x in saves["saves"]], [r["id"]])
        made = Path(self.app.web_copies.get(self.app.remote.devices.list()[0]["id"], r["id"])["path"])
        self.assertTrue(made.exists())
        self.assertEqual(self.phone("POST", f"/api/web/save/{r['id']}/done", {}, cookie=self.token)[1], {"ok": True})
        self.assertFalse(made.exists())
        status, gone, _ = self.phone("GET", f"/api/web/save/{r['id']}", cookie=self.token)
        self.assertEqual((status, gone.get("code")), (404, "gone"))

    def test_saving_what_the_player_plays(self):
        status, r, _ = self.phone("POST", f"/api/items/{self.show['id']}/play", {"path": str(SAMPLE), "season": 1, "episode": 1},
                                  cookie=self.token)
        self.assertEqual((status, r.get("player")), (200, "web"), r)
        status, s, _ = self.phone("POST", "/api/web/save", {"sid": r["session"], "index": r["index"], "caps": {}}, cookie=self.token)
        self.assertEqual(status, 200, s)
        self.assertEqual((s["item_id"], s["key"]), (self.show["id"], "1:1"))
        self.assertEqual(self.ready(s["id"])["state"], "ready")
        self.phone("POST", f"/api/web/{r['session']}/state", {"closed": True}, cookie=self.token)

    def test_only_this_titles_files(self):
        status, r, _ = self.phone("POST", "/api/web/save", {"item_id": self.show["id"], "path": __file__}, cookie=self.token)
        self.assertEqual(status, 400, r)
        self.app.store.update(self.show["id"], {"local_folder": ""})
        status, r, _ = self.phone("POST", "/api/web/save", {"item_id": self.show["id"], "path": str(SAMPLE)}, cookie=self.token)
        self.assertEqual(status, 400, r)

    def test_not_without_pairing(self):
        status, _, _ = self.phone("POST", "/api/web/save", {"item_id": self.show["id"], "path": str(SAMPLE)})
        self.assertEqual(status, 401)

    def test_a_phone_that_couldnt_play_it_gets_it_converted(self):
        status, r, _ = self.phone("POST", f"/api/items/{self.show['id']}/play", {"path": str(SAMPLE), "season": 1, "episode": 1},
                                  cookie=self.token)
        caps = {"hevc": True}
        status, media, _ = self.phone("POST", f"/api/web/{r['session']}/open", {"index": r["index"], "caps": caps}, cookie=self.token)
        self.assertEqual(status, 200, media)
        status, media, _ = self.phone("POST", f"/api/web/{r['session']}/open", {"index": r["index"], "caps": caps, "convert": True},
                                      cookie=self.token)
        self.assertEqual((status, media["how"]), (200, "encode"), media)
        # From then on (it's remembered for that file), and when it's saved too.
        status, media, _ = self.phone("POST", f"/api/web/{r['session']}/open", {"index": r["index"], "caps": caps}, cookie=self.token)
        self.assertEqual(media["how"], "encode")
        status, s, _ = self.phone("POST", "/api/web/save", {"sid": r["session"], "index": r["index"], "caps": caps}, cookie=self.token)
        self.assertEqual(self.ready(s["id"])["how"], "converted")
        self.phone("POST", f"/api/web/{r['session']}/state", {"closed": True}, cookie=self.token)


if __name__ == "__main__":
    unittest.main()
