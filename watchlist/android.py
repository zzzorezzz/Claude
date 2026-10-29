"""Watch Anything on an Android phone (the APK).

The same app runs inside the phone app (Chaquopy): its server on the phone
itself, its screens in the app's WebView. What's different on a phone:

- Videos play in the app's own phone player (Android's ExoPlayer), which
  tells the app where you are in each episode (/api/android/progress), so
  resuming and moving on to the next episode work as on a computer.
- Links (streaming services, trailers, your sites) open in their phone apps
  or the browser, through `bridge.openUrl`.
- Files and folders are chosen in the app's own file browser (/api/fs).
"""

import threading
from pathlib import Path

from . import launcher

PORT = 8765


def start(bridge, files_dir):
    """Start the app's server on the phone; returns the port the WebView loads.
    `bridge` is the Java side: bridge.openUrl(url) opens a link on the phone."""
    from .server import App, make_server, start_background_refresh
    launcher.ANDROID = True
    launcher.android_bridge = bridge
    app = App(Path(files_dir) / "data")
    try:
        server = make_server(app, PORT)          # the same port each time: the page's saved choices stay
    except OSError:
        server = make_server(app, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    start_background_refresh(app)
    return server.server_address[1]
