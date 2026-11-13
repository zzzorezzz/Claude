"""Subtitles made by AI (aisubs.py): the episode's sound to a speech-to-text AI (a stand-in for
Groq here, on this computer), in pieces, into English, kept for the episode."""
import json
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from watchlist import aisubs, transcode


class FakeWhisper(BaseHTTPRequestHandler):
    calls = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        what = self.path.rsplit("/", 1)[1]
        FakeWhisper.calls.append((what, self.headers.get("Authorization"), b"Content-Type: audio/mpeg" in body))
        if what == "transcriptions":            # (Japanese: said so, in Japanese)
            out = {"language": "japanese", "segments": [{"start": 1.0, "end": 3.0, "text": "こんにちは"}]}
        else:                                   # (translated)
            out = {"language": "english", "segments": [{"start": 1.0, "end": 3.0, "text": "Hello there."},
                                                       {"start": 5.0, "end": 6.0, "text": "Thanks for watching!"},
                                                       {"start": 7.0, "end": 9.0, "text": "Where is he?"}]}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@unittest.skipUnless(transcode.find()[0], "needs ffmpeg")
class AISubsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.video = self.root / "ep.mkv"
        subprocess.run([transcode.find()[0], "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc=duration=80:size=160x120:rate=10", "-f", "lavfi", "-i", "sine=duration=80",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(self.video)],
                       check=True, capture_output=True, timeout=60)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeWhisper)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        FakeWhisper.calls = []
        self.provider = {"name": "Groq", "url": f"http://127.0.0.1:{self.server.server_address[1]}/openai/v1",
                         "key": "gsk-test", "model": "whisper-large-v3"}

    def test_the_sound_is_small(self):
        sound = aisubs.audio(str(self.video), 10, 20)
        self.assertTrue(sound[:3] == b"ID3" or sound[:1] == b"\xff")
        self.assertLess(len(sound), 120_000)                 # (20 s: well under 100 KB)
        self.assertEqual(aisubs.audio(str(self.video), 500, 20), b"")

    def test_made_from_where_you_are_into_english_and_kept(self):
        seen = []
        with mock.patch.object(aisubs, "providers", return_value=[self.provider]), \
                mock.patch.object(aisubs, "FIRST", 30), mock.patch.object(aisubs, "PIECE", 30):
            job = aisubs.Job({}, str(self.video), self.root / "subs" / "1_2.ai.vtt", start=20, duration=80,
                             on_lines=lambda j: seen.append(len(j.lines)))
            job.run()
        self.assertEqual(job.error, "")
        self.assertTrue(job.translate, "Japanese sound: put into English")
        vtt = job.path.read_text(encoding="utf-8")
        self.assertIn("00:00:21.000 --> 00:00:23.000\nHello there.", vtt)       # (from 20 s on)
        self.assertIn("00:00:51.000 --> 00:00:53.000\nHello there.", vtt)       # (the next piece)
        self.assertNotIn("Thanks for watching", vtt)
        self.assertNotIn("こんにちは", vtt)
        self.assertEqual([c[0] for c in FakeWhisper.calls], ["transcriptions", "translations", "translations"])
        self.assertTrue(all(c[1] == "Bearer gsk-test" and c[2] for c in FakeWhisper.calls))
        self.assertTrue(seen and seen[-1] == 4)

    def test_without_a_key_it_says_what_to_add(self):
        job = aisubs.Job({}, str(self.video), self.root / "x.ai.vtt")
        job.run()
        self.assertIn("Groq key", job.error)

    def test_the_app_keeps_them_for_the_episode(self):
        from watchlist.server import App
        from watchlist.store import new_item
        app = App(self.root / "data")
        self.addCleanup(app.torrents.shutdown)
        item = app.store.add(dict(new_item("anime", {"source": "mal", "source_id": "1", "title": "Show"}), format="series"))
        with mock.patch.object(aisubs, "providers", return_value=[self.provider]), \
                mock.patch.object(aisubs, "FIRST", 40), mock.patch.object(aisubs, "PIECE", 40):
            job = app.ai_subs(item, "1:2", str(self.video), {}, 0, 80)
            end = time.time() + 60
            while job.running and time.time() < end:
                time.sleep(0.2)
        self.assertEqual(app.saved_sub(app.store.get(item["id"]), "1:2"), str(job.path))


@unittest.skipUnless(transcode.find()[0], "needs ffmpeg")
class InThePlayerTest(AISubsTest):
    def test_the_player_gets_the_track_as_it_grows(self):
        from watchlist.server import App
        from watchlist.store import new_item
        app = App(self.root / "data")
        self.addCleanup(app.torrents.shutdown)
        item = app.store.add(dict(new_item("anime", {"source": "mal", "source_id": "1", "title": "Show"}), format="series"))
        entry = {"key": "1:3", "path": str(self.video), "label": "Show - 3"}
        session = mock.Mock(item_id=item["id"], ended=False, dur=80.0)
        session.current.return_value = entry
        session.position.return_value = 0.0
        app.settings.update({"groq_api_key": "gsk-test"})
        with mock.patch.object(aisubs, "providers", return_value=[self.provider]), \
                mock.patch.object(aisubs, "FIRST", 40), mock.patch.object(aisubs, "PIECE", 40):
            app._ai_subs_player(session)
            job = app._aisubs[(item["id"], "1:3")]
            end = time.time() + 60
            while job.running and time.time() < end:
                time.sleep(0.2)
        sent = [c.args for c in session.send.call_args_list]
        added = [a for a in sent if a[0][0] == "sub-add"]
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0][0][1:], [str(job.path), "select", "AI subtitles", "en"])
        self.assertIn((["sub-reload"],), sent)

    test_the_sound_is_small = test_made_from_where_you_are_into_english_and_kept = None
    test_without_a_key_it_says_what_to_add = test_the_app_keeps_them_for_the_episode = None


class ProvidersTest(unittest.TestCase):
    def test_groq_first_then_your_openai(self):
        got = aisubs.providers({"groq_api_key": "g", "ai_custom": [
            {"name": "OpenAI", "url": "https://api.openai.com/v1", "key": "sk"},
            {"name": "Local", "url": "http://localhost:11434/v1", "key": "x"}]})
        self.assertEqual([(p["name"], p["model"]) for p in got], [("Groq", "whisper-large-v3"), ("OpenAI", "whisper-1")])
        self.assertEqual(aisubs.providers({}), [])


if __name__ == "__main__":
    unittest.main()
