"""Settings and on-disk JSON persistence.

Everything lives in the data folder (``data/`` next to the app by default),
which is git-ignored so API keys never end up in the repository.
"""

import copy
import json
import os
import re
import sys
import threading
from pathlib import Path

DEFAULT_SETTINGS = {
    # TMDB "API Read Access Token" (long) or "API Key" (short).
    "tmdb_api_key": "",
    # MyAnimeList Client ID, used for anime info.
    "mal_client_id": "",
    # Two-letter country code used for "where to watch" checks.
    "region": "PK",
    # Full path to vlc.exe. Empty means auto-detect.
    "vlc_path": "",
    # Browser for streaming sites (and the app window without pywebview).
    # Empty means auto-detect Opera GX, falling back to the system default.
    "browser_path": "",
    # Queue the following episodes in VLC after the one you start.
    "queue_next_episodes": True,
    # Links opened for online viewing. {title} is replaced with the title.
    "netflix_url": "https://www.netflix.com/search?q={title}",
    "hbomax_url": "https://play.hbomax.com/search?q={title}",
    "fallback_url": "https://everythingmoe.com",
    # Your own sites, searched when Netflix and HBO Max don't have a title.
    # {title} is replaced with the title. Empty means use fallback_url.
    "anime_site_url": "",
    "movie_tv_site_url": "",
    # One folder holding all your downloads, sorted into titles automatically.
    "downloads_folder": "",
    # "mpv" (the built-in player) or "vlc".
    "player": "mpv",
    # A folder both computers sync (Google Drive, OneDrive, Dropbox, iCloud
    # Drive...). When set, the list lives there instead of the data folder.
    "sync_folder": "",
    # Last folder picked in a folder dialog, so the next one starts there.
    "last_folder": "",
    # Optional: a free Google Gemini API key (aistudio.google.com). Without
    # one, the app's own rules and searches do all the sorting.
    "gemini_api_key": "",
    # Search MyAnimeList / TMDB for downloads that aren't in your list, and
    # add them when it's clear what they are.
    "auto_sort": True,
    # Skip openings and endings in the built-in player (when the file marks them).
    "skip_intro": True,
    # Watch together (Syncplay): your name, the server and Syncplay's location.
    "party_name": "",
    "party_server": "syncplay.pl:8999",
    "syncplay_path": "",
}

URL_KEYS = ("netflix_url", "hbomax_url", "fallback_url")
OPTIONAL_URL_KEYS = ("anime_site_url", "movie_tv_site_url")
TEXT_KEYS = ("tmdb_api_key", "mal_client_id", "vlc_path", "browser_path", "sync_folder",
             "downloads_folder", "last_folder", "gemini_api_key", "party_name", "party_server",
             "syncplay_path")
BOOL_KEYS = ("queue_next_episodes", "auto_sort", "skip_intro")


def default_data_dir() -> Path:
    env = os.environ.get("WATCHLIST_DATA")
    if env:
        return Path(env).expanduser()
    if getattr(sys, "frozen", False):
        # The packaged app: keep your list outside the app itself, so it
        # survives moving or replacing Watchlist.exe / Watchlist.app.
        if sys.platform == "win32":
            return Path(os.environ.get("APPDATA") or Path.home()) / "Watchlist"
        if sys.platform == "darwin":
            return Path.home() / "Library" / "Application Support" / "Watchlist"
        return Path.home() / ".watchlist"
    return Path(__file__).resolve().parent.parent / "data"


def read_json(path: Path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return copy.deepcopy(default)
    except (OSError, ValueError):
        # Keep the unreadable file rather than silently overwriting it.
        try:
            os.replace(path, path.with_suffix(path.suffix + ".broken"))
        except OSError:
            pass
        return copy.deepcopy(default)


def write_json(path: Path, data, compact=False) -> None:
    """Write atomically. compact=True is for big files written often: pretty
    printing makes Python use its slow JSON encoder (about 10x slower)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        if compact:
            # json.dumps (not json.dump) is what uses the fast C encoder.
            fh.write(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
        else:
            json.dump(data, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


class Settings:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        stored = read_json(path, {})
        data = copy.deepcopy(DEFAULT_SETTINGS)
        if isinstance(stored, dict):
            data.update({k: v for k, v in stored.items() if k in data})
        self._data = data

    def get(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._data)

    def update(self, patch: dict) -> dict:
        if not isinstance(patch, dict):
            raise ValueError("settings must be an object")
        with self._lock:
            data = copy.deepcopy(self._data)
            for key in TEXT_KEYS:
                if key in patch:
                    if not isinstance(patch[key], str):
                        raise ValueError(f"{key} must be text")
                    data[key] = patch[key].strip().strip('"')
            for key in URL_KEYS:
                if key in patch:
                    value = str(patch[key]).strip()
                    if not value.lower().startswith(("http://", "https://")):
                        raise ValueError("links must start with http:// or https://")
                    data[key] = value
            for key in OPTIONAL_URL_KEYS:
                if key in patch:
                    value = str(patch[key] or "").strip()
                    if value and not value.lower().startswith(("http://", "https://")):
                        raise ValueError("website links must start with http:// or https://")
                    data[key] = value
            if "player" in patch:
                if patch["player"] not in ("mpv", "vlc"):
                    raise ValueError("player must be mpv or vlc")
                data["player"] = patch["player"]
            if "region" in patch:
                region = str(patch["region"]).strip().upper()
                if not re.fullmatch(r"[A-Z]{2}", region):
                    raise ValueError("country must be a two-letter code, like PK")
                data["region"] = region
            for key in BOOL_KEYS:
                if key in patch:
                    data[key] = bool(patch[key])
            if "party_server" in patch and data["party_server"] and not re.fullmatch(
                    r"[A-Za-z0-9.-]+(:\d{1,5})?", data["party_server"]):
                raise ValueError("the watch-party server must look like syncplay.pl:8999")
            write_json(self.path, data)
            self._data = data
            return copy.deepcopy(data)
