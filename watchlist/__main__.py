"""Start Watchlist: python -m watchlist (or double-click Watchlist.bat)."""

import argparse
import json
import sys
import urllib.request
import webbrowser
from pathlib import Path

from . import launcher
from .config import default_data_dir
from .server import App, make_server, start_background_refresh


def _already_running(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1) as r:
            return json.load(r).get("app") == "watchlist"
    except (OSError, ValueError):
        return False


def main(argv=None):
    ap = argparse.ArgumentParser(prog="watchlist", description=__doc__)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--data", help="folder for your list and settings (default: ./data)")
    ap.add_argument("--browser", action="store_true",
                    help="open in your normal browser instead of an app window")
    ap.add_argument("--no-open", action="store_true", help="don't open any window")
    args = ap.parse_args(argv)

    url = f"http://127.0.0.1:{args.port}/"
    if _already_running(args.port):
        print(f"Watchlist is already running - opening {url}")
        if not args.no_open:
            launcher.open_app_window(url, Path(args.data or default_data_dir()))
        return 0

    app = App(Path(args.data) if args.data else default_data_dir())
    try:
        server = make_server(app, args.port)
    except OSError as e:
        print(f"Couldn't start on port {args.port} ({e}). Try: Watchlist.bat --port 8800")
        return 1
    start_background_refresh(app)
    print(f"Watchlist is running at {url}")
    print("Keep this window open while you use the app. Close it (or press Ctrl+C) to quit.")

    if not args.no_open:
        if args.browser:
            webbrowser.open(url, new=1)
        else:
            import threading
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            if launcher.open_app_window(url, app.data_dir):
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
