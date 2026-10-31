"""The video catcher on a Mac (and Linux): a hidden Chromium driven through its DevTools
protocol, doing what the Edge windows do on Windows (webtools.py) - open a site's page,
let its player start (clicking Sub / Dub or a server when asked), and catch the video
address it asks for, from the page or any player frame inside it, with the headers it
was asked with. It also reads pages after their scripts have run.

The Chromium is the one on the computer (Chrome, Edge, Brave, Chromium) or, without one,
Google's small headless Chromium, downloaded once into the app's folder."""
import base64
import json
import os
import platform
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

HOME = Path(tempfile.gettempdir()) / "watchanything-chromium"    # set to the app's data folder by the app
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0.0.0 Safari/537.36")
CFT = "https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json"

_lock = threading.BoundedSemaphore(3)    # up to three pages at once (as on Windows)
_starting = threading.Lock()      # (one browser, however many ask for it at once)
_browser = None
_download = threading.Lock()


class CDPError(Exception):
    pass


# ---- which Chromium

def _installed():
    if sys.platform == "darwin":
        apps = ["Google Chrome", "Microsoft Edge", "Brave Browser", "Chromium", "Vivaldi"]
        roots = ["/Applications", str(Path.home() / "Applications")]
        names = {"Google Chrome": "Google Chrome", "Microsoft Edge": "Microsoft Edge", "Brave Browser": "Brave Browser",
                 "Chromium": "Chromium", "Vivaldi": "Vivaldi"}
        return [f"{r}/{a}.app/Contents/MacOS/{names[a]}" for r in roots for a in apps]
    if sys.platform == "win32":
        out = []
        for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                out += [f"{base}\\Google\\Chrome\\Application\\chrome.exe", f"{base}\\Microsoft\\Edge\\Application\\msedge.exe",
                        f"{base}\\BraveSoftware\\Brave-Browser\\Application\\brave.exe"]
        return out
    return [shutil.which(n) or "" for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
                                             "microsoft-edge", "brave-browser")]


def _platform():
    if sys.platform == "darwin":
        return "mac-arm64" if platform.machine() in ("arm64", "aarch64") else "mac-x64"
    if sys.platform == "win32":
        return "win64"
    return "linux64"


def _downloaded():
    exe = "chrome-headless-shell.exe" if sys.platform == "win32" else "chrome-headless-shell"
    found = list((HOME / "chromium").glob(f"chrome-headless-shell-*/{exe}"))
    return str(found[0]) if found else ""


def find():
    """The Chromium to use ("" if none yet): Google's headless one when it's been fetched
    (made for this), else one installed on the computer."""
    if os.environ.get("WATCHLIST_CHROMIUM"):
        return os.environ["WATCHLIST_CHROMIUM"]
    got = _downloaded()
    if got:
        return got
    return next((p for p in _installed() if p and os.path.isfile(p)), "")


def _downloaded_full():
    if sys.platform == "darwin":
        found = list((HOME / "chrome").glob("chrome-*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"))
    elif sys.platform == "win32":
        found = list((HOME / "chrome").glob("chrome-*/chrome.exe"))
    else:
        found = list((HOME / "chrome").glob("chrome-*/chrome"))
    return str(found[0]) if found else ""


def find_full():
    """A Chromium that can show a window: one on the computer, else Google's (fetched)."""
    return next((p for p in _installed() if p and os.path.isfile(p)), "") or _downloaded_full()


def download_full():
    """Google's ordinary Chrome for Testing, fetched into the app's folder once (for the
    window you find a video in, on a computer without Chrome)."""
    with _download:
        if _downloaded_full():
            return _downloaded_full()
        with urllib.request.urlopen(CFT, timeout=30) as r:
            info = json.loads(r.read())
        rows = info["channels"]["Stable"]["downloads"]["chrome"]
        url = next(x["url"] for x in rows if x["platform"] == _platform())
        dest = HOME / "chrome"
        dest.mkdir(parents=True, exist_ok=True)
        part = dest / "download.zip"
        with urllib.request.urlopen(url, timeout=900) as r, open(part, "wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
        if sys.platform == "win32":
            with zipfile.ZipFile(part) as z:
                z.extractall(dest)
        else:
            # (unzip keeps the app's links and permissions, which Python's zipfile doesn't)
            subprocess.run(["ditto", "-x", "-k", str(part), str(dest)] if sys.platform == "darwin"
                           else ["unzip", "-q", "-o", str(part), "-d", str(dest)], check=True)
        part.unlink(missing_ok=True)
        return _downloaded_full()


def watch(url, job, at=None):
    """An ordinary browser window at `url` whose every video goes to `job` (picker.py).
    Returns what closes it."""
    from . import picker, webtools
    exe = find_full() or download_full()
    if not exe:
        raise CDPError("no Chrome to show the page in")
    browser = Browser(exe, visible=url, profile="profile-browse")
    target = None
    for _ in range(50):
        pages = [t for t in browser.call("Target.getTargets").get("targetInfos", []) if t.get("type") == "page"]
        target = next((t["targetId"] for t in pages if not t.get("url", "").startswith(("about:", "chrome:"))), None) \
            or (pages[0]["targetId"] if pages else None)
        if target:
            break
        time.sleep(0.1)

    def on_request(u, headers, kind):
        if u.startswith(picker.CLICKED_ON):
            job.clicked(urllib.parse.unquote(u[len(picker.CLICKED_ON):]))
        elif webtools.VIDEO.search(urllib.parse.urlsplit(u).path) or kind == "Media":
            job.video(u, headers)
        elif webtools.SUBTITLE.search(urllib.parse.urlsplit(u).path) or webtools.SUBTITLE_ASK.search(u):
            job.subtitle(u)

    def on_response(u, mime, headers):
        if "mpegurl" in mime or "dash+xml" in mime or mime.startswith("video/"):
            job.video(u, headers)
        elif "text/vtt" in mime or "subrip" in mime or "x-ass" in mime:
            job.subtitle(u)
    scripts = [picker.TRACK_JS] + ([webtools.ADBLOCK_JS] if webtools.ADBLOCK else [])
    page = _Page(browser, scripts, on_request, on_response, target=target)
    if at:
        try:
            win = browser.call("Browser.getWindowForTarget", {"targetId": target})["windowId"]
            browser.call("Browser.setWindowBounds", {"windowId": win, "bounds": {
                "left": at[0], "top": at[1], "width": at[2], "height": at[3], "windowState": "normal"}})
        except (CDPError, KeyError):
            pass

    def follow():
        # The page you're on (for linking the episode to it), until the window is closed.
        while browser.alive():
            try:
                job.page = page.run("location.href") or job.page
            except CDPError:
                pass
            time.sleep(1.5)
        job.closed()
    threading.Thread(target=follow, daemon=True).start()
    return browser.close


def download(log=lambda *_: None):
    """Google's headless Chromium for this computer, fetched into the app's folder once."""
    with _download:
        if _downloaded():
            return _downloaded()
        log("Getting the video finder (a small Chromium, once)…")
        with urllib.request.urlopen(CFT, timeout=30) as r:
            info = json.loads(r.read())
        rows = info["channels"]["Stable"]["downloads"]["chrome-headless-shell"]
        url = next(x["url"] for x in rows if x["platform"] == _platform())
        dest = HOME / "chromium"
        dest.mkdir(parents=True, exist_ok=True)
        part = dest / "download.zip"
        with urllib.request.urlopen(url, timeout=600) as r, open(part, "wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
        with zipfile.ZipFile(part) as z:
            for m in z.infolist():
                z.extract(m, dest)
                mode = m.external_attr >> 16
                if mode:
                    os.chmod(dest / m.filename, mode)          # (the program files stay runnable)
        part.unlink(missing_ok=True)
        got = _downloaded()
        if got and sys.platform != "win32":
            os.chmod(got, 0o755)
        return got


def available():
    return sys.platform != "android"


# ---- the DevTools connection (a WebSocket, written out: no extra packages)

class _Socket:
    def __init__(self, url):
        u = urllib.parse.urlsplit(url)
        self.sock = socket.create_connection((u.hostname, u.port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CDPError("the browser closed the connection")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise CDPError("the browser refused the connection")
        self.buf = head.split(b"\r\n\r\n", 1)[1]
        self.sock.settimeout(None)
        self.send_lock = threading.Lock()

    def send(self, text):
        data = text.encode()
        mask = os.urandom(4)
        n = len(data)
        head = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n)
                                if n < 65536 else bytes([0x80 | 127]) + struct.pack(">Q", n))
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        with self.send_lock:
            self.sock.sendall(head + mask + body)

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(1 << 16)
            if not chunk:
                raise CDPError("the browser closed the connection")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def recv(self):
        parts = []
        while True:
            b1, b2 = self._read(2)
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            payload = self._read(n)
            op = b1 & 0x0F
            if op == 8:
                raise CDPError("the browser closed the connection")
            if op in (1, 0, 2):
                parts.append(payload)
                if b1 & 0x80:
                    return b"".join(parts).decode("utf-8", "replace")

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class Browser:
    """One headless Chromium, its pages opened and closed as they're needed."""

    def __init__(self, exe, visible="", profile="profile"):
        self.profile = HOME / profile
        self.profile.mkdir(parents=True, exist_ok=True)
        (self.profile / "DevToolsActivePort").unlink(missing_ok=True)
        # Headless, except a full Chromium can run as an ordinary window kept off the screen
        # (WATCHLIST_CDP_WINDOW=1): some players only start in an ordinary browser. `visible`:
        # an ordinary window you use (picker.py), open at that page.
        shown = os.environ.get("WATCHLIST_CDP_WINDOW") == "1" and "headless-shell" not in exe
        mode = ["--window-position=-10000,-10000", "--no-startup-window"] if shown else ["--headless=new"]
        if visible:
            mode = ["--new-window"]
        args = [exe, *mode, "--remote-debugging-port=0", f"--user-data-dir={self.profile}",
                "--autoplay-policy=no-user-gesture-required", *([] if visible else ["--mute-audio"]), "--no-first-run",
                "--no-default-browser-check", "--disable-background-timer-throttling",
                "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows",
                "--window-size=1280,720",
                # (as an ordinary browser: some sites send a browser that says it's automated home)
                "--disable-blink-features=AutomationControlled", visible or "about:blank"]
        if not self._launch(args):
            # One left running on its folder (the app closed without stopping it): a new one
            # only hands over to it and goes. Stop that one, then start again.
            _stop_others(self.profile)
            if not self._launch(args):
                raise CDPError("the video finder (Chromium) didn't start")
        port, path = (self.profile / "DevToolsActivePort").read_text().split()[:2]
        self.ws = _Socket(f"ws://127.0.0.1:{port}{path}")
        self.ids, self.waiting, self.handlers = 0, {}, []
        self.lock = threading.Lock()
        threading.Thread(target=self._reader, daemon=True).start()
        # Its own identity (version and all, as its other signals say), just not "Headless".
        try:
            self.ua = self.call("Browser.getVersion")["userAgent"].replace("HeadlessChrome", "Chrome")
        except (CDPError, KeyError):
            self.ua = UA

    def _launch(self, args):
        kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        port_file = self.profile / "DevToolsActivePort"
        port_file.unlink(missing_ok=True)
        self.proc = subprocess.Popen(args, **kwargs)
        for _ in range(150):
            if port_file.is_file() and len(port_file.read_text().split()) >= 2:
                return True
            if self.proc.poll() is not None:
                return False
            time.sleep(0.1)
        if self.proc.poll() is None:
            self.proc.kill()
        return False

    def alive(self):
        return self.proc.poll() is None and not getattr(self, "dead", False)

    def _reader(self):
        try:
            while True:
                msg = json.loads(self.ws.recv())
                if "id" in msg:
                    slot = self.waiting.pop(msg["id"], None)
                    if slot:
                        slot[1].update(msg)
                        slot[0].set()
                else:
                    for h in list(self.handlers):
                        try:
                            h(msg)
                        except Exception:  # noqa: BLE001 - one handler mustn't stop the others
                            pass
        except (CDPError, OSError, ValueError):
            self.dead = True
            for ev, _ in list(self.waiting.values()):
                ev.set()

    def call(self, method, params=None, session=None, timeout=20):
        with self.lock:
            self.ids += 1
            n = self.ids
        ev, out = threading.Event(), {}
        self.waiting[n] = (ev, out)
        msg = {"id": n, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        self.ws.send(json.dumps(msg))
        if not ev.wait(timeout):
            self.waiting.pop(n, None)
            raise CDPError(f"the browser didn't answer ({method})")
        if "error" in out:
            raise CDPError(out["error"].get("message", "error"))
        return out.get("result", {})

    def send(self, method, params=None, session=None):
        """A command whose answer isn't waited for."""
        with self.lock:
            self.ids += 1
            n = self.ids
        msg = {"id": n, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        self.ws.send(json.dumps(msg))

    def close(self):
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass
        if self.proc.poll() is None:
            self.proc.kill()


def _stop_others(profile):
    """Stop browsers still running on the video finder's own folder (none but its own use it)."""
    mark = str(profile)
    try:
        if sys.platform == "win32":
            out = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  "Get-CimInstance Win32_Process | ForEach-Object { \"$($_.ProcessId) $($_.CommandLine)\" }"],
                                 capture_output=True, text=True, timeout=30,
                                 creationflags=subprocess.CREATE_NO_WINDOW).stdout
        else:
            out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return
    for line in out.splitlines():
        pid, _, cmd = line.strip().partition(" ")
        if mark in cmd and "--remote-debugging-port" in cmd and pid.isdigit() and int(pid) != os.getpid():
            try:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", pid], capture_output=True, timeout=15,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    os.kill(int(pid), 9)
            except (OSError, subprocess.SubprocessError):
                pass
    time.sleep(1)


def _get_browser():
    with _starting:
        return _get_browser_now()


def _get_browser_now():
    global _browser
    if _browser and _browser.alive():
        return _browser
    exe = find() or download()
    if not exe:
        raise CDPError("no Chromium to find videos with")
    _browser = Browser(exe)
    return _browser


def shutdown():
    global _browser
    if _browser:
        _browser.close()
        _browser = None


class _Page:
    """A page (and every frame in it, the players from other sites too)."""

    def __init__(self, browser, scripts, on_request=None, on_response=None, target=None):
        self.b, self.scripts, self.ua = browser, scripts, browser.ua
        self.on_request, self.on_response = on_request, on_response
        self.sessions, self.extra = set(), {}
        self.loaded = threading.Event()
        self.target = target or browser.call("Target.createTarget", {"url": "about:blank"})["targetId"]
        browser.handlers.append(self._event)
        self.session = browser.call("Target.attachToTarget", {"targetId": self.target, "flatten": True})["sessionId"]
        self._prepare(self.session, top=True)

    def _prepare(self, session, top=False, worker=False):
        from . import webtools
        self.sessions.add(session)
        b = self.b
        b.call("Network.enable", {}, session)
        if worker:
            return                     # (a worker: its requests are watched; it has no page to script)
        b.call("Page.enable", {}, session)
        b.call("Network.setUserAgentOverride", {"userAgent": b.ua}, session)
        if webtools.ADBLOCK:
            # The ad servers (and theirs below them) as on Windows - not every address with the name in it.
            b.call("Network.setBlockedURLs", {"urls": [p for h in webtools.AD_HOSTS for p in (f"*://{h}/*", f"*://*.{h}/*")]}, session)
        for s in self.scripts:
            b.call("Page.addScriptToEvaluateOnNewDocument", {"source": s}, session)
        b.call("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True}, session)

    def _event(self, msg):
        method, params, session = msg.get("method"), msg.get("params") or {}, msg.get("sessionId")
        if method == "Target.attachedToTarget" and session in self.sessions:
            child = params["sessionId"]
            kind = params.get("targetInfo", {}).get("type")
            if kind in ("iframe", "page", "worker", "service_worker", "shared_worker"):
                threading.Thread(target=self._child, args=(child, kind != "iframe" and kind != "page"), daemon=True).start()
            else:
                self.b.send("Runtime.runIfWaitingForDebugger", {}, child)
            return
        if session not in self.sessions:
            return
        if method == "Network.requestWillBeSentExtraInfo":
            self.extra[params.get("requestId")] = params.get("headers") or {}
        elif method == "Network.requestWillBeSent" and self.on_request:
            req = params.get("request") or {}
            headers = dict(req.get("headers") or {})
            headers.update(self.extra.get(params.get("requestId")) or {})
            self.on_request(req.get("url", ""), headers, params.get("type", ""))
        elif method == "Network.responseReceived" and self.on_response:
            resp = params.get("response") or {}
            self.on_response(resp.get("url", ""), (resp.get("mimeType") or "").lower(),
                             dict(resp.get("requestHeaders") or self.extra.get(params.get("requestId")) or {}))
        elif method == "Page.loadEventFired" and session == self.session:
            self.loaded.set()

    def _child(self, session, worker=False):
        try:
            self._prepare(session, worker=worker)
        except Exception:  # noqa: BLE001 - a frame gone already (or the page closed meanwhile)
            pass
        finally:
            # (it waits, paused, until it's let go: always let go, set up or not)
            try:
                self.b.send("Runtime.runIfWaitingForDebugger", {}, session)
            except Exception:  # noqa: BLE001
                pass

    def go(self, url):
        self.b.call("Page.navigate", {"url": url}, self.session)

    def run(self, js):
        got = self.b.call("Runtime.evaluate", {"expression": js, "returnByValue": True, "awaitPromise": False}, self.session)
        return (got.get("result") or {}).get("value")

    def close(self):
        try:
            self.b.handlers.remove(self._event)
        except ValueError:
            pass
        try:
            self.b.call("Target.closeTarget", {"targetId": self.target}, timeout=5)
        except CDPError:
            pass


def _header(headers, name):
    return next((v for k, v in headers.items() if k.lower() == name.lower()), "")


def _chromium_page(scripts, on_request=None, on_response=None):
    return _Page(_get_browser(), scripts, on_request, on_response)


def catch_video(url, timeout=30, click=None, open_page=None):
    """As webtools.catch_video: {"url", "headers", "subs"} for the video a page's player
    asks for (after clicking `click` when given), or None. `open_page` opens the page
    somewhere else than this Chromium (the phone's own WebView: droidweb.py)."""
    from . import webtools
    open_page = open_page or _chromium_page
    page = None
    click = webtools.learned_click(url, click)
    found, got, opts = [], threading.Event(), {"subs": []}

    def keep(video, headers):
        u = video.lower()
        if webtools.blocked(video) or u.startswith(("blob:", "data:")) or re.search(
                r"\.(ts|m4s|aac|key|vtt|srt|mp3|m4a|ogg|oga|opus|wav|flac)(\?|$)", u) \
                or re.search(r"imdb-video|media-imdb|youtube|googlevideo|ytimg|trailer", u):
            return
        if not any(v == video for v, _, _ in found):
            ref = _header(headers, "Referer")
            # (an Origin the browser didn't report: the page's, as a browser sends it)
            origin = _header(headers, "Origin") or ("{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(ref)) if ref else "")
            found.append((video, {k: v for k, v in {"Referer": ref, "Origin": origin,
                                                    "User-Agent": _header(headers, "User-Agent") or (page.ua if page else ""),
                                                    "Cookie": _header(headers, "Cookie")}.items() if v}, time.time()))
            if not click or opts.get("clicked_at"):
                got.set()

    def on_request(u, headers, kind):
        if u.startswith(webtools.CLICKED):
            opts["clicked_at"] = time.time()
        elif u.startswith(webtools.NO_CLICK):
            opts["no_click"] = True
        elif webtools.VIDEO.search(urllib.parse.urlsplit(u).path) or kind == "Media":
            keep(u, headers)
        elif (webtools.SUBTITLE.search(urllib.parse.urlsplit(u).path) or webtools.SUBTITLE_ASK.search(u)) \
                and not re.search(r"thumbnail|\.(?:js|css|png|jpe?g|webp|svg|woff2?|gif)(?:\?|$)", u, re.I):
            if u not in opts["subs"]:
                opts["subs"].append(u)

    def on_response(u, mime, headers):
        if "mpegurl" in mime or "dash+xml" in mime or mime.startswith("video/"):
            keep(u, headers)
        elif ("text/vtt" in mime or "subrip" in mime or "x-ass" in mime or "x-ssa" in mime) and u not in opts["subs"]                 and not u.startswith(("blob:", "data:")):
            opts["subs"].append(u)

    scripts = [webtools.AUTOPLAY_JS]
    if click:
        scripts.append(webtools.CLICK_JS.replace("__WANT__", json.dumps(click)))
    if webtools.ADBLOCK:
        scripts.append(webtools.ADBLOCK_JS)
    with _lock:
        try:
            page = open_page(scripts, on_request, on_response)
        except (CDPError, OSError, ValueError, KeyError, StopIteration):
            return None
        try:
            page.go(url)
            deadline = time.time() + timeout

            def videos():
                return [f for f in found if f[0] not in opts["subs"]]
            while time.time() < deadline:
                got.wait(0.5)
                if got.is_set():
                    time.sleep(1.2)
                    if videos() and (webtools.learned_found(url, videos()) or time.time() > deadline - timeout + 20):
                        break
                    got.clear()
                if click and not opts.get("clicked_at") and videos() and (opts.get("no_click") or time.time() > deadline - timeout + 15):
                    break
            found[:] = videos()
            if not found:
                return None
            after = [f for f in found if f[2] >= opts.get("clicked_at", 0)] or found
            best = min(after, key=lambda f: (webtools.learned_rank(url, f[0], f[1]),
                                             0 if ".m3u8" in f[0].lower() or ".mpd" in f[0].lower() else 1, after.index(f)))
            return {"url": best[0], "headers": best[1], "subs": list(opts["subs"])}
        except CDPError:
            return None
        finally:
            page.close()


def render(url, timeout=20, settle=2.5, open_page=None):
    """As webtools.render: (the page's HTML after its scripts ran, its address), or None."""
    from . import webtools
    open_page = open_page or _chromium_page
    with _lock:
        try:
            page = open_page([webtools.ADBLOCK_JS] if webtools.ADBLOCK else [])
        except (CDPError, OSError, ValueError, KeyError, StopIteration):
            return None
        try:
            page.go(url)
            page.loaded.wait(timeout)
            try:
                page.run(webtools.EPISODES_TAB_JS)
            except CDPError:
                pass
            last, deadline = -1, time.time() + max(settle, 1) * 3
            while time.time() < deadline:
                time.sleep(settle / 3)
                count = page.run("document.getElementsByTagName('a').length + document.getElementsByTagName('iframe').length")
                if count == last:
                    break
                last = count
            return page.run("document.documentElement.outerHTML"), page.run("location.href") or url
        except CDPError:
            return None
        finally:
            page.close()
