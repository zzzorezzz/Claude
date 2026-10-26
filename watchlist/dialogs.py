"""Native "choose a folder / file" dialogs.

They run in a separate process so they never fight with the app window or the
web server over the main thread.
"""

import subprocess
import sys

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


def choose(kind="folder", title="Choose a folder", initial="", types="video"):
    """Returns the chosen path, or "" if the person cancelled."""
    try:
        result = subprocess.run([sys.executable, "-c", _TK_SCRIPT, kind, title, initial or "", types],
                                capture_output=True, text=True, timeout=600)
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    if sys.platform == "win32":
        import os
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
