"""Playing on a phone (webplay.py, transcode.py): tickets, a site's playlists through the app, how a
video is made playable, subtitles as WebVTT, the phone's player as the app sees it, the stream server."""
import http.client
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.test_remote import RemoteBase
from watchlist import transcode, webplay


class TicketTest(unittest.TestCase):
    def test_a_ticket_works_for_what_it_was_made_for(self):
        t = webplay.Tickets()
        k = t.make("phone1", {"kind": "file", "path": "/x.mp4"})
        self.assertEqual(t.check(k)["path"], "/x.mp4")
        tid, exp, sig = k.split(".")
        for bad in (f"{tid}.{exp}.{sig[:-2]}AA", f"{tid}.{int(exp) + 60}.{sig}", "nope", f"x{tid}.{exp}.{sig}", ""):
            self.assertIsNone(t.check(bad), bad)
        self.assertNotIn("/x.mp4", k)                       # (what's behind it isn't in it)

    def test_it_runs_out(self):
        t = webplay.Tickets()
        k = t.make("phone1", {"kind": "file", "path": "x"}, life=-1)
        self.assertIsNone(t.check(k))
        k = t.make("phone1", {"kind": "file", "path": "x"})
        with mock.patch.object(webplay.time, "time", return_value=time.time() + webplay.TICKET_IDLE + 5):
            self.assertIsNone(t.check(k))

    def test_a_removed_device_loses_its_tickets(self):
        t = webplay.Tickets()
        a, b = t.make("phone1", {"kind": "x"}), t.make("phone2", {"kind": "x"})
        t.drop_device("phone1")
        self.assertIsNone(t.check(a))
        self.assertIsNotNone(t.check(b))


class RewriteTest(unittest.TestCase):
    def setUp(self):
        self.urls = []

    def remember(self, u):
        if u not in self.urls:
            self.urls.append(u)
        return self.urls.index(u)

    def test_master_playlist(self):
        text = ('#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="en",URI="audio/en.m3u8"\n'
                '#EXT-X-STREAM-INF:BANDWIDTH=800000\n720/index.m3u8\n#EXT-X-I-FRAME-STREAM-INF:URI="if.m3u8"\n')
        out = webplay.rewrite(text, "https://cdn.example.com/v/master.m3u8", "T", self.remember)
        self.assertIn('URI="/stream/web/T/r/0.m3u8"', out)
        self.assertIn("\n/stream/web/T/r/1.m3u8\n", out)
        self.assertIn('URI="/stream/web/T/r/2.m3u8"', out)
        self.assertEqual(self.urls, ["https://cdn.example.com/v/audio/en.m3u8", "https://cdn.example.com/v/720/index.m3u8",
                                     "https://cdn.example.com/v/if.m3u8"])
        self.assertNotIn("cdn.example.com", out)

    def test_media_playlist(self):
        text = ('#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="https://k.example.com/key?id=1",IV=0x1\n#EXT-X-MAP:URI="init.mp4"\n'
                '#EXTINF:4,\nseg1.m4s?token=abc\n#EXTINF:4,\nhttps://img.example.com/p/piece.jpg\n#EXT-X-ENDLIST\n')
        out = webplay.rewrite(text, "https://cdn.example.com/v/720/index.m3u8", "T", self.remember)
        self.assertIn('URI="/stream/web/T/r/0.key"', out)
        self.assertIn('URI="/stream/web/T/r/1.mp4"', out)
        self.assertIn("/stream/web/T/r/2.m4s", out)
        self.assertIn("/stream/web/T/r/3.ts", out)            # (a piece dressed as a picture)
        self.assertIn("#EXT-X-ENDLIST", out)
        self.assertEqual(self.urls[2], "https://cdn.example.com/v/720/seg1.m4s?token=abc")

    def test_pictures_in_front_taken_off(self):
        ts = bytes([0x47] + [0] * 187) * 4
        self.assertEqual(webplay.strip_picture(b"\x89PNG\r\n\x1a\n" + b"x" * 40 + ts), ts)
        self.assertEqual(webplay.strip_picture(ts), ts)


def info(codec="h264", pix="yuv420p", bits=8, height=1080, fmt="matroska,webm", audio=("aac",), subs=()):
    return {"duration": 1440.0, "format": fmt,
            "video": {"codec": codec, "pix_fmt": pix, "bits": bits, "profile": "High", "width": 1920, "height": height, "fps": 24.0},
            "audio": [{"n": i, "codec": c, "channels": 2, "lang": "", "title": "", "default": i == 0} for i, c in enumerate(audio)],
            "subs": [{"n": i, "codec": c, "text": c in transcode.TEXT_SUBS, "lang": "", "title": "", "default": False,
                      "forced": False, "styled": c == "ass"} for i, c in enumerate(subs)]}


class PlanTest(unittest.TestCase):
    def test_an_mp4_the_phone_plays(self):
        self.assertTrue(transcode.plan(info(fmt="mov,mp4,m4a,3gp,3g2,mj2"), quality="original")["direct"])

    def test_not_from_a_torrent_though(self):
        self.assertFalse(transcode.plan(info(fmt="mov,mp4,m4a,3gp,3g2,mj2"), quality="original", local=False)["direct"])

    def test_an_mkv_is_repackaged(self):
        how = transcode.plan(info(audio=("flac", "aac")), quality="original")
        self.assertEqual((how["direct"], how["video"], how["audio"]), (False, "copy", ["aac", "copy"]))

    def test_ten_bit_is_converted(self):
        self.assertEqual(transcode.plan(info(pix="yuv420p10le", bits=10), quality="original")["video"], "encode")

    def test_hevc_only_where_the_phone_plays_it(self):
        self.assertEqual(transcode.plan(info(codec="hevc"), quality="original")["video"], "encode")
        self.assertEqual(transcode.plan(info(codec="hevc"), caps={"hevc": True}, quality="original")["video"], "copy")

    def test_phone_quality(self):
        how = transcode.plan(info(height=1080), quality="720")
        self.assertEqual((how["video"], how["height"]), ("encode", 720))
        self.assertEqual(transcode.plan(info(height=720), quality="720")["video"], "copy")

    def test_burned_in(self):
        self.assertEqual(transcode.plan(info(subs=("ass",)), quality="original", burn={"path": None, "n": 0})["video"], "encode")


class SubtitleTest(unittest.TestCase):
    def test_srt(self):
        vtt = transcode.srt_to_vtt("﻿1\r\n00:00:01,500 --> 00:00:03,000\r\n{\\an8}Hello <i>there</i>\r\n\r\n2\n0:01:02,5 --> 0:01:04,000\nBye\n")
        self.assertTrue(vtt.startswith("WEBVTT"))
        self.assertIn("00:00:01.500 --> 00:00:03.000\nHello <i>there</i>", vtt)
        self.assertIn("00:01:02.500 --> 00:01:04.000\nBye", vtt)

    def test_a_sites_vtt_kept(self):
        text = "WEBVTT\n\n00:01.000 --> 00:02.000\nReal words\n"
        self.assertEqual(transcode.clean_vtt(text), text)

    def test_names(self):
        self.assertEqual(transcode.track_name({"lang": "jpn", "title": ""}, 0), "Japanese")
        self.assertEqual(transcode.track_name({"lang": "eng", "title": "Dub"}, 1), "English - Dub")
        self.assertEqual(transcode.track_name({"lang": "", "title": ""}, 2), "Audio 3")


class SessionTest(unittest.TestCase):
    def test_the_phone_moving_on_is_heard(self):
        s = webplay.WebSession("d", "item", [{"key": "1:1", "label": "E1"}, {"key": "1:2", "label": "E2"}])
        heard = []
        s.listeners.append(lambda sess, m: heard.append(m))
        s.event({"index": 0, "pos": 5, "dur": 1400, "paused": False})
        s.event({"index": 1, "pos": 0, "dur": 1400})
        self.assertEqual([(m["name"], m.get("moved")) for m in heard], [("duration", True), ("playlist-pos", True), ("duration", True)])
        self.assertEqual(s.current()["key"], "1:2")

    def test_what_the_party_asks(self):
        s = webplay.WebSession("d", "item", [{"key": "1:1"}])
        s.send(["seek", 30.5, "absolute"], ["set_property", "pause", True], ["show-text", "Ali paused", 2000])
        seq, cmds = s.take(0)
        self.assertEqual(cmds, [{"do": "seek", "to": 30.5, "n": 1}, {"do": "pause", "n": 2},
                                {"do": "text", "text": "Ali paused", "ms": 2000, "n": 3}])     # (numbered: each once)
        self.assertEqual(s.take(seq), (seq, []))
        s.jump(0)
        self.assertEqual(s.take(seq)[1], [{"do": "index", "to": 0, "n": 4}])

    def test_closed(self):
        s = webplay.WebSession("d", "item", [{"key": "1:1"}])
        ended = []
        s.on_end = ended.append
        s.event({"closed": True})
        self.assertTrue(s.ended)
        self.assertEqual(ended, [s])


class StreamServerTest(RemoteBase):
    def get(self, path, headers=None, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.lan_port, timeout=10)
        conn.request("GET", path, headers=dict({"Host": host or self.host}, **(headers or {})))
        r = conn.getresponse()
        body = r.read()
        conn.close()
        return r, body

    def test_no_ticket_no_video(self):
        for path in ("/stream/web/abc/video.mp4", "/stream/web/a.1.b/video.mp4", "/stream/web/"):
            self.assertIn(self.get(path)[0].status, (403, 404), path)

    def test_a_file_with_byte_ranges(self):
        f = Path(self.tmp.name) / "v.mp4"
        f.write_bytes(bytes(range(256)) * 400)
        t = self.app.tickets.make("dev", {"kind": "file", "path": str(f)})
        r, body = self.get(f"/stream/web/{t}/video.mp4")
        self.assertEqual((r.status, r.getheader("Content-Type"), r.getheader("Accept-Ranges"), len(body)), (200, "video/mp4", "bytes", 102400))
        r, body = self.get(f"/stream/web/{t}/video.mp4", {"Range": "bytes=1000-1999"})
        self.assertEqual((r.status, r.getheader("Content-Range"), r.getheader("Content-Length")), (206, "bytes 1000-1999/102400", "1000"))
        self.assertEqual(body, (bytes(range(256)) * 400)[1000:2000])
        r, _ = self.get(f"/stream/web/{t}/video.mp4", {"Range": "bytes=999999-"})
        self.assertEqual(r.status, 416)
        # (no cookie needed: AirPlay asks without one - the ticket is enough)
        self.assertEqual(self.get(f"/stream/web/{t}/video.mp4", {"Range": "bytes=0-9"})[0].status, 206)

    def test_a_subtitle_as_webvtt(self):
        f = Path(self.tmp.name) / "s.srt"
        f.write_text("1\n00:00:01,000 --> 00:00:02,000\nHi\n", encoding="utf-8")
        t = self.app.tickets.make("dev", {"kind": "sub", "path": str(f)})
        r, body = self.get(f"/stream/web/{t}/sub.vtt")
        self.assertEqual(r.status, 200)
        self.assertIn("text/vtt", r.getheader("Content-Type"))
        self.assertIn(b"00:00:01.000 --> 00:00:02.000", body)

    def test_the_phone_plays_a_file_on_itself(self):
        if not transcode.find()[0]:
            self.skipTest("ffmpeg isn't here")
        token, _ = self.pair()
        src = Path(__file__).with_name("sample.mkv")
        show = self.add("tv", "tmdb", "tv/1396")
        self.app.store.update(show["id"], {"local_folder": str(src.parent)})
        status, r, _ = self.phone("POST", f"/api/items/{show['id']}/play", {"path": str(src), "season": 1, "episode": 1}, cookie=token)
        self.assertEqual((status, r.get("player")), (200, "web"), r)
        self.assertEqual(self.spawned, [])                       # (nothing opened on the PC)
        status, media, _ = self.phone("POST", f"/api/web/{r['session']}/open", {"index": r["index"]}, cookie=token)
        self.assertEqual(status, 200, media)
        self.assertTrue(media["src"].startswith("/stream/web/"))
        self.assertNotIn(str(src), json.dumps(media))           # (the file's path stays on the PC)
        resp, body = self.get(media["src"])
        self.assertEqual(resp.status, 200)
        self.assertTrue(body.startswith(b"#EXTM3U") or len(body) > 1000)
        status, st, _ = self.phone("POST", f"/api/web/{r['session']}/state", {"index": r["index"], "pos": 3, "dur": 30, "paused": True}, cookie=token)
        self.assertEqual(status, 200, st)
        self.assertEqual(self.app.store.get(show["id"])["positions"].get("1:1", {}).get("pos"), 3.0)
        self.phone("POST", f"/api/web/{r['session']}/state", {"closed": True}, cookie=token)
        self.assertIsNone(self.app.web_jobs.current(self.app.remote.devices.list()[0]["id"]))


class RemovedDeviceTest(RemoteBase):
    def test_its_videos_stop(self):
        token, _ = self.pair()
        dev = self.app.remote.devices.list()[0]
        f = Path(self.tmp.name) / "v.mp4"
        f.write_bytes(b"x" * 100)
        t = self.app.tickets.make(dev["id"], {"kind": "file", "path": str(f)})
        self.call("DELETE", f"/api/phone/devices/{dev['id']}")
        self.assertIsNone(self.app.tickets.check(t))


class EventsTest(RemoteBase):
    def test_the_phone_hears_at_once(self):
        token, _ = self.pair()
        dev = self.app.remote.devices.list()[0]
        item = self.add("tv", "tmdb", "tv/1396")
        session = self.app.web_session(dev, self.app.store.get(item["id"]), [{"key": "1:1", "path": "x"}])
        got = []

        def listen():
            conn = http.client.HTTPConnection("127.0.0.1", self.lan_port, timeout=10)
            conn.request("GET", f"/api/web/{session.id}/events?after=0",
                         headers={"Host": self.host, "Cookie": f"wa_device={token}"})
            r = conn.getresponse()
            got.append(r.getheader("Content-Type"))
            got.append(r.fp.readline().decode())
            conn.close()
        import threading
        t = threading.Thread(target=listen)
        t.start()
        time.sleep(0.5)
        session.send(["set_property", "pause", True])
        t.join(10)
        self.assertEqual(got[0], "text/event-stream")
        data = json.loads(got[1].split("data: ", 1)[1])
        self.assertEqual(data["commands"], [{"do": "pause", "n": 1}])

    def test_not_without_the_key(self):
        self.pair()
        dev = self.app.remote.devices.list()[0]
        item = self.add("tv", "tmdb", "tv/1396")
        session = self.app.web_session(dev, self.app.store.get(item["id"]), [{"key": "1:1", "path": "x"}])
        self.assertEqual(self.phone("GET", f"/api/web/{session.id}/events")[0], 401)
