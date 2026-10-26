"""Settings and on-disk JSON persistence.

Everything lives in the data folder (``data/`` next to the app by default),
which is git-ignored so API keys never end up in the repository.
"""

import copy
import json
import os
import re
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
    # Queue the following episodes in VLC after the one you start.
    "queue_next_episodes": True,
    # Links opened for online viewing. {title} is replaced with the title.
    "netflix_url": "https://www.netflix.com/search?q={title}",
    "hbomax_url": "https://play.hbomax.com/search?q={title}",
    "fallback_url": "https://everythingmoe.com",
    # Last folder picked in a folder dialog, so the next one starts there.
    "last_folder": "",
}

URL_KEYS = ("netflix_url", "hbomax_url", "fallback_url")
TEXT_KEYS = ("tmdb_api_key", "mal_client_id", "vlc_path", "last_folder")


def default_data_dir() -> Path:
    env = os.environ.get("WATCHLIST_DATA")
    if env:
        return Path(env).expanduser()
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


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
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
            if "region" in patch:
                region = str(patch["region"]).strip().upper()
                if not re.fullmatch(r"[A-Z]{2}", region):
                    raise ValueError("country must be a two-letter code, like PK")
                data["region"] = region
            if "queue_next_episodes" in patch:
                data["queue_next_episodes"] = bool(patch["queue_next_episodes"])
            write_json(self.path, data)
            self._data = data
            return copy.deepcopy(data)
