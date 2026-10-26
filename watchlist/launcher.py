"""Starting VLC, the default browser, the file manager and the app window."""

import os
import shutil
import subprocess
import sys
import urllib.parse
import webbrowser
from pathlib import Path


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
    cmd = [vlc]
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


def open_url(url):
    if not url.lower().startswith(("http://", "https://")):
        raise LaunchError("only web links can be opened")
    if not webbrowser.open(url, new=2):
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


def _app_browser():
    """Edge or Chrome, which can show a page as its own app window."""
    names = ["msedge", "chrome", "google-chrome", "chromium", "chromium-browser"]
    if sys.platform == "win32":
        for env in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if not base:
                continue
            for rel in (r"Microsoft\Edge\Application\msedge.exe",
                        r"Google\Chrome\Application\chrome.exe"):
                p = Path(base) / rel
                if p.is_file():
                    return str(p)
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return ""


def open_app_window(url, data_dir):
    """Open the UI. Returns True if it blocks until the window is closed."""
    try:
        import webview  # pywebview, optional
    except ImportError:
        webview = None
    if webview is not None:
        webview.create_window("Watchlist", url, width=1440, height=900, min_size=(900, 600),
                              background_color="#141414")
        webview.start()
        return True
    browser = _app_browser()
    if browser:
        profile = Path(data_dir) / "window-profile"
        _spawn([browser, f"--app={url}", f"--user-data-dir={profile}",
                "--window-size=1440,900", "--no-first-run"])
        return False
    webbrowser.open(url, new=1)
    return False
