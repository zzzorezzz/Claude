"""The app itself starts (as `python -m watchlist` does) and answers."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path


class StartsTest(unittest.TestCase):
    def test_the_app_starts_and_answers(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        data = tempfile.mkdtemp()
        root = Path(__file__).resolve().parent.parent
        proc = subprocess.Popen([sys.executable, "-m", "watchlist", "--no-open", "--port", str(port), "--data", data],
                                cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.addCleanup(proc.kill)
        answer, end = None, time.time() + 30
        while time.time() < end and answer is None:
            if proc.poll() is not None:
                self.fail("the app stopped while starting:\n" + proc.stdout.read().decode("utf-8", "replace")[-2000:])
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=2) as r:
                    answer = json.loads(r.read())
            except OSError:
                time.sleep(0.3)
        self.assertEqual((answer or {}).get("app"), "watchlist")


if __name__ == "__main__":
    unittest.main()
