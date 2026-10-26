"""Native "choose a folder / file" dialogs.

Inside the app window these are pywebview's own Windows dialogs. Otherwise
they run in a separate process so they never fight with the web server over
the main thread.
"""

import os
import subprocess
import sys

# The pywebview window, when the app runs in one (set by launcher.open_app_window).
window = None

_WEBVIEW_TYPES = {
    "video": ("Video files (*.mkv;*.mp4;*.avi;*.mov;*.wmv;*.m4v;*.webm;*.ts)", "All files (*.*)"),
    "subtitle": ("Subtitles (*.srt;*.ass;*.ssa;*.vtt;*.sub)", "All files (*.*)"),
    "exe": ("Programs (*.exe)", "All files (*.*)"),
}

_TK_SCRIPT = r"""
import sys, tkinter as tk
from tkinter import filedialog
kind, title, initial, types = sys.argv[1], sys.argv[2], sys.argv[3] or None, sys.argv[4]
root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)
if kind == "folder":
    path = filedialog.askdirectory(title=title, initialdir=initial, mustexist=True)
else:
    patterns = {"video": ("Video files", "*.mkv *.mp4 *.avi *.mov *.wmv *.m4v *.webm *.ts"),
                "subtitle": ("Subtitles", "*.srt *.ass *.ssa *.vtt *.sub"),
                "exe": ("Programs", "*.exe")}
    filetypes = [patterns[types]] if types in patterns else []
    filetypes.append(("All files", "*.*"))
    path = filedialog.askopenfilename(title=title, initialdir=initial, filetypes=filetypes)
root.destroy()
sys.stdout.write(path or "")
"""

_PS_FOLDER = r"""
Add-Type -AssemblyName System.Windows.Forms
$d = New-Object System.Windows.Forms.FolderBrowserDialog
$d.Description = $env:WL_TITLE
$d.ShowNewFolderButton = $false
if ($env:WL_INITIAL) { $d.SelectedPath = $env:WL_INITIAL }
$owner = New-Object System.Windows.Forms.Form -Property @{TopMost = $true}
if ($d.ShowDialog($owner) -eq 'OK') { [Console]::Out.Write($d.SelectedPath) }
"""

_PS_FILE = r"""
Add-Type -AssemblyName System.Windows.Forms
$d = New-Object System.Windows.Forms.OpenFileDialog
$d.Title = $env:WL_TITLE
$d.Filter = $env:WL_FILTER
if ($env:WL_INITIAL) { $d.InitialDirectory = $env:WL_INITIAL }
$owner = New-Object System.Windows.Forms.Form -Property @{TopMost = $true}
if ($d.ShowDialog($owner) -eq 'OK') { [Console]::Out.Write($d.FileName) }
"""

_PS_FILTERS = {
    "video": "Video files|*.mkv;*.mp4;*.avi;*.mov;*.wmv;*.m4v;*.webm;*.ts|All files|*.*",
    "subtitle": "Subtitles|*.srt;*.ass;*.ssa;*.vtt;*.sub|All files|*.*",
    "exe": "Programs|*.exe|All files|*.*",
}


class DialogUnavailable(Exception):
    pass


def _webview_choose(kind, initial, types):
    import webview
    dialog = getattr(webview, "FileDialog", None)
    if dialog is not None:
        mode = dialog.FOLDER if kind == "folder" else dialog.OPEN
    else:  # pywebview < 5
        mode = webview.FOLDER_DIALOG if kind == "folder" else webview.OPEN_DIALOG
    file_types = () if kind == "folder" else _WEBVIEW_TYPES.get(types, ())
    picked = window.create_file_dialog(mode, directory=initial or "", file_types=file_types)
    if not picked:
        return ""
    return picked if isinstance(picked, str) else picked[0]


def choose(kind="folder", title="Choose a folder", initial="", types="video"):
    """Returns the chosen path, or "" if the person cancelled."""
    if window is not None:
        try:
            return _webview_choose(kind, initial, types)
        except Exception:  # fall back to the other dialogs below
            pass
    # A frozen .exe has no separate Python to run the tkinter dialog with.
    if not getattr(sys, "frozen", False):
        try:
            result = subprocess.run([sys.executable, "-c", _TK_SCRIPT, kind, title, initial or "", types],
                                    capture_output=True, text=True, timeout=600)
            if result.returncode == 0:
                return result.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    if sys.platform == "win32":
        env = dict(os.environ, WL_TITLE=title, WL_INITIAL=initial or "",
                   WL_FILTER=_PS_FILTERS.get(types, "All files|*.*"))
        script = _PS_FOLDER if kind == "folder" else _PS_FILE
        try:
            result = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", script],
                                    capture_output=True, text=True, timeout=600, env=env,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode == 0:
                return result.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    raise DialogUnavailable("couldn't open a file dialog - type or paste the path instead")
