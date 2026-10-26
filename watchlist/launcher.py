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


def find_vlc(configured=""):
    if configured and Path(configured).is_file():
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
    """playlist: [(video_path, subtitle_path_or_None), ...]"""
    # Always start at the first file and play the rest in order, even if
    # shuffle was left on in VLC, and replace (not add to) the playlist of a
    # VLC window that's already open.
    cmd = [vlc, "--no-random", "--no-playlist-enqueue"]
    for video, subtitle in playlist:
        cmd.append(video)
        if subtitle:
            # Options starting with ":" apply to the file just before them.
            cmd.append(f":sub-file={subtitle}")
    return cmd


def play_in_vlc(vlc, playlist):
    if not vlc:
        raise LaunchError("VLC wasn't found - set its location in Settings")
    _spawn(vlc_command(vlc, playlist))


def open_url(url, browser=""):
    """Open a web link in `browser` (a path), or the system default browser."""
    if not url.lower().startswith(("http://", "https://")):
        raise LaunchError("only web links can be opened")
    if browser:
        _spawn([browser, url])
    elif not webbrowser.open(url, new=2):
        raise LaunchError("couldn't open your default browser")


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
    if configured and Path(configured).is_file():
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
        for p in (Path("/Applications/Opera GX.app/Contents/MacOS/Opera"),
                  Path.home() / "Applications/Opera GX.app/Contents/MacOS/Opera"):
            if p.is_file():
                return str(p)
    return ""


def open_app_window(url, browser=""):
    """Open the UI. Returns True if it blocks until the window is closed."""
    try:
        import webview  # pywebview, optional
    except ImportError:
        webview = None
    if webview is not None:
        try:
            dialogs.window = webview.create_window(
                "Watchlist", url, width=1440, height=900, min_size=(900, 600),
                background_color="#141414")
            webview.start()
            return True
        except Exception:
            # e.g. the WebView2 runtime is missing: use the browser instead.
            dialogs.window = None
    if browser:
        _spawn([browser, "--new-window", url])
    else:
        webbrowser.open(url, new=1)
    return False
