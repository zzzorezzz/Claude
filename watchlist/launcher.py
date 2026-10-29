"""Starting VLC, the browser (Opera GX), the file manager and the app window."""

import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import webbrowser
from pathlib import Path

from . import dialogs


class LaunchError(Exception):
    pass


def _mac_bundle(path):
    """"/Applications/X.app/Contents/MacOS/X" or "/Applications/X.app" -> "/Applications/X.app"."""
    parts = Path(path).parts
    for i, part in enumerate(parts):
        if part.lower().endswith(".app"):
            return str(Path(*parts[:i + 1]))
    return ""


def _bundle_binary(bundle):
    """The program inside a macOS .app bundle."""
    import plistlib
    try:
        with open(Path(bundle) / "Contents" / "Info.plist", "rb") as fh:
            name = plistlib.load(fh).get("CFBundleExecutable") or Path(bundle).stem
    except (OSError, ValueError):
        name = Path(bundle).stem
    return Path(bundle) / "Contents" / "MacOS" / name


def find_vlc(configured=""):
    if configured:
        p = Path(configured)
        if p.suffix.lower() == ".app" and p.is_dir():
            binary = _bundle_binary(p)
            if binary.is_file():
                return str(binary)
        if p.is_file():
            return configured
    candidates = []
    if sys.platform == "win32":
        try:
            import winreg
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                for sub in (r"SOFTWARE\VideoLAN\VLC", r"SOFTWARE\WOW6432Node\VideoLAN\VLC"):
                    try:
                        with winreg.OpenKey(hive, sub) as key:
                            for name in ("", "InstallDir"):
                                try:
                                    value = winreg.QueryValueEx(key, name)[0]
                                except OSError:
                                    continue
                                if value:
                                    p = Path(value)
                                    candidates.append(p if p.suffix.lower() == ".exe"
                                                      else p / "vlc.exe")
                    except OSError:
                        continue
        except ImportError:
            pass
        for env in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
            if os.environ.get(env):
                candidates.append(Path(os.environ[env]) / "VideoLAN" / "VLC" / "vlc.exe")
    elif sys.platform == "darwin":
        candidates += [Path("/Applications/VLC.app/Contents/MacOS/VLC"),
                       Path.home() / "Applications/VLC.app/Contents/MacOS/VLC"]
    for c in candidates:
        if c.is_file():
            return str(c)
    found = shutil.which("vlc")
    return found or ""


def vlc_command(vlc, playlist):
    """playlist: [(video_path, subtitle_path_or_None[, start_seconds]), ...]"""
    # Always start at the first file and play the rest in order, even if
    # shuffle was left on in VLC, and replace (not add to) the playlist of a
    # VLC window that's already open.
    cmd = [vlc, "--no-random", "--no-playlist-enqueue"]
    for video, subtitle, *rest in playlist:
        cmd.append(video)
        # Options starting with ":" apply to the file just before them.
        if subtitle:
            cmd.append(f":sub-file={subtitle}")
        if rest and rest[0]:
            cmd.append(f":start-time={int(rest[0])}")
    return cmd


def play_in_vlc(vlc, playlist):
    if not vlc:
        raise LaunchError("VLC wasn't found - set its location in Settings")
    _spawn(vlc_command(vlc, playlist))


def open_url(url, browser=""):
    """Open a web link in `browser` (a path), or the system default browser."""
    if not url.lower().startswith(("http://", "https://")):
        raise LaunchError("only web links can be opened")
    if browser == BUILTIN_BROWSER:
        if open_window(url, urllib.parse.urlsplit(url).hostname or "Watch Anything", 1280, 860):
            return
        if _webview is not None:
            raise LaunchError("the built-in browser window couldn't open")
        browser = ""                     # no app window (running in a browser): the default browser
    if browser:
        _spawn(_browser_command(browser, url))
    elif not webbrowser.open(url, new=2):
        raise LaunchError("couldn't open your default browser")


def trash(path):
    """Move a file to the Recycle Bin / Trash (so a delete by mistake can be undone)."""
    p = Path(path)
    if not p.exists():
        raise LaunchError("that file doesn't exist any more")
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                        ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                        ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]
        # FO_DELETE with FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT
        op = SHFILEOPSTRUCTW(None, 3, str(p.resolve()) + "\0", None, 0x40 | 0x10 | 0x400 | 0x4, False, None, None)
        if ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) or op.fAnyOperationsAborted:
            raise LaunchError("couldn't move it to the Recycle Bin")
        return
    if sys.platform == "darwin":
        bin_ = Path.home() / ".Trash"
    else:
        bin_ = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "Trash/files"
    try:
        bin_.mkdir(parents=True, exist_ok=True)
        target = bin_ / p.name
        n = 1
        while target.exists():
            n += 1
            target = bin_ / f"{p.stem} ({n}){p.suffix}"
        shutil.move(str(p), str(target))
    except OSError as e:
        raise LaunchError(f"couldn't move it to the Trash ({e})") from e


def reveal(path):
    """Show a file (or folder) in Explorer / Finder."""
    p = Path(path)
    if not p.exists():
        raise LaunchError("that file doesn't exist any more")
    if sys.platform == "win32":
        if p.is_file():
            _spawn(["explorer", f"/select,{p}"])
        else:
            os.startfile(str(p))  # noqa: S606 - opening a local folder
    elif sys.platform == "darwin":
        _spawn(["open", "-R", str(p)] if p.is_file() else ["open", str(p)])
    else:
        _spawn(["xdg-open", str(p.parent if p.is_file() else p)])


def _spawn(cmd):
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
              "stderr": subprocess.DEVNULL, "close_fds": True}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(cmd, **kwargs)
    except OSError as e:
        raise LaunchError(f"couldn't start {Path(cmd[0]).name}: {e}") from e


def fill_link(template, title):
    return template.replace("{title}", urllib.parse.quote_plus(title))


def _registry_browser(name_part):
    """Path of an installed browser whose Windows registry name contains name_part."""
    try:
        import winreg
    except ImportError:
        return ""
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            root = winreg.OpenKey(hive, r"SOFTWARE\Clients\StartMenuInternet")
        except OSError:
            continue
        with root:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                if name_part not in sub.lower().replace(" ", ""):
                    continue
                try:
                    with winreg.OpenKey(root, sub + r"\shell\open\command") as key:
                        command = winreg.QueryValueEx(key, "")[0]
                except OSError:
                    continue
                m = re.match(r'\s*"([^"]+)"|\s*(\S+)', command or "")
                path = m and (m.group(1) or m.group(2))
                if path and Path(path).is_file():
                    return path
    return ""


def find_browser(configured=""):
    """The browser to use: the one set in Settings, else Opera GX if it's installed.
    Returns "" when neither is found (the system default browser is used then)."""
    if configured == BUILTIN_BROWSER:
        return BUILTIN_BROWSER
    if configured and (Path(configured).is_file()
                       or (_mac_bundle(configured) and Path(configured).exists())):
        return configured
    if sys.platform == "win32":
        found = _registry_browser("operagx")
        if found:
            return found
        for env in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(env)
            if not base:
                continue
            for rel in (r"Programs\Opera GX\launcher.exe", r"Programs\Opera GX\opera.exe",
                        r"Opera GX\launcher.exe", r"Opera GX\opera.exe"):
                p = Path(base) / rel
                if p.is_file():
                    return str(p)
    elif sys.platform == "darwin":
        for p in (Path("/Applications/Opera GX.app"), Path.home() / "Applications/Opera GX.app"):
            if p.is_dir():
                return str(p)
    return ""


# ---- choosing a browser or another media player in Settings

BUILTIN_BROWSER = "builtin"     # browser_path for "a window of this app"

_MAC_BROWSERS = ["Safari", "Google Chrome", "Firefox", "Microsoft Edge", "Brave Browser", "Opera GX", "Opera",
                 "Arc", "Vivaldi", "Chromium", "Zen Browser", "DuckDuckGo"]
_WIN_BROWSERS = [("Google Chrome", r"Google\Chrome\Application\chrome.exe"),
                 ("Microsoft Edge", r"Microsoft\Edge\Application\msedge.exe"),
                 ("Firefox", r"Mozilla Firefox\firefox.exe"),
                 ("Brave", r"BraveSoftware\Brave-Browser\Application\brave.exe"),
                 ("Opera GX", r"Programs\Opera GX\launcher.exe"), ("Opera", r"Programs\Opera\launcher.exe"),
                 ("Vivaldi", r"Vivaldi\Application\vivaldi.exe")]
_MAC_PLAYERS = ["IINA", "VLC", "Infuse 7", "Infuse", "Movist Pro", "Movist", "Elmedia Player", "QuickTime Player", "mpv"]
_WIN_PLAYERS = [("MPC-HC", r"MPC-HC\mpc-hc64.exe"), ("MPC-HC", r"MPC-HC\mpc-hc.exe"),
                ("MPC-BE", r"MPC-BE x64\mpc-be64.exe"), ("MPC-BE", r"MPC-BE\mpc-be.exe"),
                ("PotPlayer", r"DAUM\PotPlayer\PotPlayerMini64.exe"), ("PotPlayer", r"DAUM\PotPlayer\PotPlayerMini.exe"),
                ("KMPlayer", r"KMPlayer\KMPlayer.exe"), ("GOM Player", r"GRETECH\GomPlayer\GOM.EXE"),
                ("VLC", r"VideoLAN\VLC\vlc.exe"), ("mpv", r"mpv\mpv.exe")]


def _win_roots():
    return [os.environ[e] for e in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)", "LOCALAPPDATA")
            if os.environ.get(e)]


def _mac_apps(names):
    out = []
    for name in names:
        for root in (Path("/Applications"), Path.home() / "Applications"):
            app = root / f"{name}.app"
            if app.is_dir():
                out.append({"name": name, "path": str(app)})
                break
    return out


def list_browsers():
    """The browsers installed on this computer: [{"name", "path"}]."""
    found = []
    if sys.platform == "win32":
        try:
            import winreg
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    root = winreg.OpenKey(hive, r"SOFTWARE\Clients\StartMenuInternet")
                except OSError:
                    continue
                with root:
                    i = 0
                    while True:
                        try:
                            sub = winreg.EnumKey(root, i)
                        except OSError:
                            break
                        i += 1
                        try:
                            with winreg.OpenKey(root, sub) as key:
                                name = winreg.QueryValueEx(key, "")[0] or sub
                            with winreg.OpenKey(root, sub + r"\shell\open\command") as key:
                                command = winreg.QueryValueEx(key, "")[0]
                        except OSError:
                            continue
                        m = re.match(r'\s*"([^"]+)"|\s*(\S+)', command or "")
                        path = m and (m.group(1) or m.group(2))
                        if path and Path(path).is_file():
                            found.append({"name": name, "path": path})
        except ImportError:
            pass
        for name, rel in _WIN_BROWSERS:
            for root in _win_roots():
                p = Path(root) / rel
                if p.is_file():
                    found.append({"name": name, "path": str(p)})
                    break
    elif sys.platform == "darwin":
        found = _mac_apps(_MAC_BROWSERS)
    seen, out = set(), []
    for b in found:
        key = os.path.normcase(b["path"])
        if key not in seen and b["name"].lower() not in {x["name"].lower() for x in out}:
            seen.add(key)
            out.append(b)
    return out


def list_players():
    """Other media players installed on this computer: [{"name", "path"}]."""
    found = []
    if sys.platform == "win32":
        for name, rel in _WIN_PLAYERS:
            if any(x["name"] == name for x in found):
                continue
            for root in _win_roots():
                p = Path(root) / rel
                if p.is_file():
                    found.append({"name": name, "path": str(p)})
                    break
    elif sys.platform == "darwin":
        found = _mac_apps(_MAC_PLAYERS)
    return found


def play_in_player(player, path):
    """A file (or a stream link) in another media player of your choice."""
    if not player or not (Path(player).exists()):
        raise LaunchError("that media player wasn't found - choose it again in Settings")
    if sys.platform == "darwin" and _mac_bundle(player):
        _spawn(["open", "-a", _mac_bundle(player), path])
    else:
        _spawn([player, path])


def _browser_command(browser, url, new_window=False):
    if sys.platform == "darwin" and _mac_bundle(browser):
        # On a Mac, ask the running browser to open it rather than starting a second copy.
        return ["open", "-a", _mac_bundle(browser), url]
    return [browser] + (["--new-window"] if new_window else []) + [url]


_webview = None          # pywebview, once the app window is open
_windows = {}            # the app's other windows by kind ("party", "browser")
CLEAR_MARK = "clear-browser"     # in the browser data folder: sign out of everything at the next start


def can_open_windows():
    return _webview is not None


def open_window(url, title, width=1100, height=800, kind="", on_top=False):
    """Another window of the app's own (the watch party, the built-in browser).
    False when there's no app window to do it with (running in a browser).
    A window of a `kind` that's already open is brought back instead of opened twice."""
    if _webview is None:
        return False
    old = _windows.get(kind) if kind else None
    if old is not None:
        try:
            old.restore()
            old.show()
            old.on_top = on_top
            return True
        except Exception:  # noqa: BLE001 - it was closed
            _windows.pop(kind, None)
    try:
        win = _webview.create_window(title, url, width=width, height=height, background_color="#0d0f1a",
                                     text_select=True, on_top=on_top)
    except Exception:  # noqa: BLE001 - the window couldn't be made
        return False
    if kind:
        _windows[kind] = win
        try:
            win.events.closed += lambda: _windows.pop(kind, None)
        except Exception:  # noqa: BLE001 - (older pywebview)
            pass
    return True


def set_on_top(kind, on_top):
    """Keep one of the app's windows above all others, or not. False when it isn't open."""
    win = _windows.get(kind)
    if win is None:
        return False
    try:
        win.on_top = bool(on_top)
        return True
    except Exception:  # noqa: BLE001
        return False


def close_window(kind):
    win = _windows.pop(kind, None)
    if win is not None:
        try:
            win.destroy()
        except Exception:  # noqa: BLE001 - already closed
            pass


def sign_out_of_sites(storage):
    """Forget every login made in the built-in browser: its cookies now, and its
    whole saved data (local storage too) the next time the app starts."""
    if storage:
        Path(storage).mkdir(parents=True, exist_ok=True)
        (Path(storage) / CLEAR_MARK).write_text("1", encoding="utf-8")
    for win in [dialogs.window, *_windows.values()]:
        try:
            if win is not None:
                win.clear_cookies()
        except Exception:  # noqa: BLE001 - (no window, or an older pywebview)
            pass


def _prepare_storage(storage):
    """The built-in browser's saved data (logins, cookies): wiped first if you signed out."""
    if not storage:
        return None
    path = Path(storage)
    if (path / CLEAR_MARK).exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def open_app_window(url, browser="", storage=None):
    """Open the UI. Returns True if it blocks until the window is closed.
    `storage` is where the app's windows keep logins and cookies, so a site you
    signed in to in the built-in browser still knows you after a restart."""
    global _webview
    try:
        import webview  # pywebview, optional
    except ImportError:
        webview = None
    if webview is not None:
        try:
            dialogs.window = webview.create_window(
                "Watch Anything", url, width=1440, height=900, min_size=(900, 600),
                background_color="#0d0f1a")
            _webview = webview
            path = _prepare_storage(storage)
            webview.start(private_mode=not path, storage_path=path)
            return True
        except Exception:
            # e.g. the WebView2 runtime is missing: use the browser instead.
            dialogs.window = None
            _webview = None
    if browser:
        _spawn(_browser_command(browser, url, new_window=True))
    else:
        webbrowser.open(url, new=1)
    return False
