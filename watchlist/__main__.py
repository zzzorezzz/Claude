"""Start Watch Anything: python -m watchlist (or open Watch Anything.exe / Watch Anything.app)."""

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

from . import launcher
from .config import Settings, default_data_dir
from .server import App, make_server, start_background_refresh


def _already_running(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1) as r:
            return json.load(r).get("app") == "watchlist"
    except (OSError, ValueError):
        return False


def _tell(message):
    """Print, and also show a message box when running as Watch Anything.exe (no console)."""
    print(message)
    if not getattr(sys, "frozen", False):
        return
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "Watch Anything", 0x10)
    elif sys.platform == "darwin":
        import subprocess
        subprocess.run(["osascript", "-e", "on run argv", "-e",
                        'display alert "Watch Anything" message (item 1 of argv) as critical',
                        "-e", "end run", message], check=False)


def _self_test(path):
    """Used by the Windows and Mac builds to check everything made it into the app."""
    from .server import WEB_DIR
    report = {"frozen": bool(getattr(sys, "frozen", False)),
              "web_files": all((WEB_DIR / f).is_file() for f in ("index.html", "app.js", "style.css"))}
    # The built-in player's modern controls (uosc), fetched by the build.
    from .player import UI_DIR
    report["player_ui"] = all((UI_DIR / f).is_file() for f in
                              ("scripts/uosc/main.lua", "fonts/uosc_icons.otf", "script-opts/uosc.conf"))
    # The modules the app window needs: pythonnet on Windows, PyObjC + WebKit on a Mac.
    needed = {"win32": ("webview", "clr"), "darwin": ("webview", "webview.platforms.cocoa")}
    for module in needed.get(sys.platform, ("webview",)):
        try:
            __import__(module)
            report[module] = True
        except Exception as e:  # noqa: BLE001 - report whatever went wrong
            report[module] = f"{type(e).__name__}: {e}"
    # Torrent streaming (libtorrent, with the libraries it needs).
    try:
        import libtorrent
        report["libtorrent"] = bool(libtorrent.__version__)
    except Exception as e:  # noqa: BLE001
        report["libtorrent"] = f"{type(e).__name__}: {e}"
    # A real HTTPS request: TMDB answers 401 without a key, which still proves
    # the secure connection (and its certificates) work.
    from .metadata import ssl_context
    import urllib.error
    try:
        urllib.request.urlopen("https://api.themoviedb.org/3/configuration", timeout=20,
                               context=ssl_context())
        report["https"] = True
    except urllib.error.HTTPError:
        report["https"] = True
    except Exception as e:  # noqa: BLE001
        report["https"] = f"{type(e).__name__}: {e}"
    Path(path).write_text(json.dumps(report), encoding="utf-8")
    return 0


def _player_test(path):
    """Used by the builds: play a 3-second test video in the bundled mpv, following
    it the same way as a real episode, and report what happened."""
    import threading
    from . import player
    report = {"mpv": player.find_mpv()}
    if report["mpv"]:
        done = threading.Event()
        finished = []
        session = player.Session(
            report["mpv"], "test",
            [{"path": "av://lavfi:testsrc=duration=3:size=160x120:rate=10", "key": "1:1", "label": "test"}],
            extra_args=["--vo=null", "--ao=null", "--force-window=no"],
            on_finished=lambda item, entry: finished.append(entry["key"]),
            on_exit=lambda s: done.set())
        session.start()
        done.wait(90)
        report.update(connected=session.connected, ended=session.ended, error=session.error,
                      last_position=session.pos, finished=finished == ["1:1"])
        # The opening skipper: a video whose chapters mark an opening from 1 s to 4 s.
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        chapters, log = tmp / "chapters.txt", tmp / "skip.log"
        chapters.write_text(";FFMETADATA1\n" + "".join(
            f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={a}\nEND={b}\ntitle={t}\n"
            for a, b, t in [(0, 1000, "Prologue"), (1000, 4000, "Opening"), (4000, 6000, "Part A")]),
            encoding="utf-8")
        done.clear()
        skip = player.Session(
            report["mpv"], "test",
            [{"path": "av://lavfi:testsrc=duration=6:size=160x120:rate=10", "key": "1:2", "label": "skip test"}],
            extra_args=["--vo=null", "--ao=null", "--force-window=no", f"--chapters-file={chapters}",
                        *player.script_args(True), f"--script-opts-append=watchlist-skip-log={log}"],
            on_exit=lambda s: done.set())
        skip.start()
        done.wait(60)
        report["skip_log"] = log.read_text(encoding="utf-8").strip() if log.exists() else ""
        # Watch parties send commands while the player runs: start paused, then press play from here.
        done.clear()
        live = player.Session(
            report["mpv"], "test",
            [{"path": "av://lavfi:testsrc=duration=4:size=160x120:rate=10", "key": "1:3", "label": "send test"}],
            extra_args=["--vo=null", "--ao=null", "--force-window=no", "--pause"],
            on_exit=lambda s: done.set())
        live.start()
        deadline = time.time() + 30
        while time.time() < deadline and not (live.connected and live.dur):
            time.sleep(0.1)
        report["send_ok"] = bool(live.send(["set_property", "pause", False]))
        done.wait(30)
        report["played_after_send"] = bool(live.pos and live.pos > 2)
    Path(path).write_text(json.dumps(report), encoding="utf-8")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="watchlist", description=__doc__)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--data", help="folder for your list and settings (default: ./data)")
    ap.add_argument("--browser", action="store_true",
                    help="open in your browser (Opera GX) instead of an app window")
    ap.add_argument("--no-open", action="store_true", help="don't open any window")
    ap.add_argument("--self-test", metavar="FILE", help=argparse.SUPPRESS)
    ap.add_argument("--player-test", metavar="FILE", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test(args.self_test)
    if args.player_test:
        return _player_test(args.player_test)

    url = f"http://127.0.0.1:{args.port}/"
    data_dir = Path(args.data) if args.data else default_data_dir()
    if _already_running(args.port):
        print(f"Watch Anything is already running - opening {url}")
        if not args.no_open:
            browser = launcher.find_browser(Settings(data_dir / "settings.json").get()["browser_path"])
            browser = "" if browser == launcher.BUILTIN_BROWSER else browser
            launcher.open_app_window(url, browser)
        return 0

    app = App(data_dir)
    try:
        server = make_server(app, args.port)
    except OSError as e:
        _tell(f"Watch Anything couldn't start: port {args.port} is in use by another program ({e}).\n\n"
              'Start it with a different port, e.g.: "Watch Anything.exe" --port 8800')
        return 1
    start_background_refresh(app)
    print(f"Watch Anything is running at {url}")
    print("Keep this window open while you use the app. Close it (or press Ctrl+C) to quit.")

    browser = launcher.find_browser(app.settings.get()["browser_path"])
    browser = "" if browser == launcher.BUILTIN_BROWSER else browser     # (that's for your sites)
    if not args.no_open:
        if args.browser:
            launcher.open_url(url, browser)
        else:
            import threading
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            if launcher.open_app_window(url, browser):
                # The pywebview window was closed: quit.
                server.shutdown()
                return 0
            try:
                while thread.is_alive():
                    thread.join(0.5)
            except KeyboardInterrupt:
                pass
            server.shutdown()
            return 0
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
