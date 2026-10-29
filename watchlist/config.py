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
    # "builtin" (the default): a window of the app itself. Empty means
    # auto-detect Opera GX, falling back to the system default.
    "browser_path": "builtin",
    # Queue the following episodes in VLC after the one you start.
    "queue_next_episodes": True,
    # Links opened for online viewing. {title} is replaced with the title.
    "netflix_url": "https://www.netflix.com/search?q={title}",
    "hbomax_url": "https://play.hbomax.com/search?q={title}",
    # Where to watch online, in order: [{"id", "name", "url" with {title},
    # "check": TMDB's names for it, "check_mode": "tmdb" / "ai" (only used when
    # it has the title in your country) or "off" (always searched),
    # "types": ["anime", "tv", "movie"], "on"}]. Nothing else is ever opened.
    # None until first used: then made from the older settings above and below.
    "services": None,
    # Your own torrent sites, searched by "Get magnet" on an anime's page:
    # [{"id", "name", "url" (its search link, with {title}), "on"}]. Empty: none.
    "torrent_sites": [],
    # Your own video sites, played from with "Play online (my sites)": [{"id", "name",
    # "url" (its search link, with {title}), "types", "on", "open": "player" or "browser"}].
    "video_sites": [],
    # Your own sites, searched when Netflix and HBO Max don't have a title.
    # {title} is replaced with the title (older versions; now part of "services").
    "anime_site_url": "",
    "movie_tv_site_url": "",
    # One folder holding all your downloads, sorted into titles automatically.
    "downloads_folder": "",
    # "mpv" (the built-in player), "vlc", or "custom" (custom_player_path).
    "player": "mpv",
    # Another media player of your choice (its program, or its .app on a Mac).
    "custom_player_path": "",
    # A folder both computers sync (Google Drive, OneDrive, Dropbox, iCloud
    # Drive...). When set, the list lives there instead of the data folder.
    "sync_folder": "",
    # Last folder picked in a folder dialog, so the next one starts there.
    "last_folder": "",
    # Optional: a free Google Gemini API key (aistudio.google.com). Without
    # one, the app's own rules and searches do all the sorting.
    "gemini_api_key": "",
    # Optional too, and free: Groq and OpenRouter keys. When one helper has
    # run out or can't be reached, the next one is used.
    "groq_api_key": "",
    "openrouter_api_key": "",
    # Your own AI helpers: [{"id", "name", "kind" ("openai" or "anthropic"), "url",
    # "key", "model" (empty: the app picks)}], and the order every helper is
    # asked in: ["gemini", "groq", "openrouter", "custom:<id>", ...].
    "ai_custom": [],
    "ai_order": [],
    # The app's colours: "violet", "crimson", "ocean", "emerald", "sunset", "rose" or "mono".
    "theme": "violet",
    # Skip openings and endings in the built-in player (when the file marks them).
    "skip_intro": True,
    # Watch together (Syncplay): your name, the server and Syncplay's location.
    "party_name": "",
    # Your picture in watch parties (a small "data:image/jpeg;base64,..."), or "".
    "party_avatar": "",
    "party_server": "syncplay.pl:8999",
    # Small sounds for small things (adding a title, closing a window, changing section).
    "sounds": True,
    # Ask "Do you want help setting it up?" when the app opens (until "Don't show again").
    "setup_prompt": True,
    # Changes made once to settings saved by older versions (see Settings.__init__).
    "migrated": [],
}

URL_KEYS = ("netflix_url", "hbomax_url")
# Colour themes: name -> (accent, second accent, background), also used for the player's controls.
THEMES = {"violet": ("7c5cff", "22d3ee", "0d0f1a"), "crimson": ("e5303f", "ff8a5c", "0e0b0d"),
          "ocean": ("2f7dff", "2ee6c8", "0a0f19"), "emerald": ("16b67a", "a3e635", "0a110e"),
          "sunset": ("ff6a3d", "ffc145", "120c0c"), "rose": ("ec4899", "c084fc", "110b12"),
          "mono": ("e6e6e6", "9aa0aa", "0c0c0d")}
# What older versions opened when nothing else fitted. Now you add it yourself.
OLD_FALLBACK = "https://everythingmoe.com"
OPTIONAL_URL_KEYS = ("anime_site_url", "movie_tv_site_url")
TEXT_KEYS = ("tmdb_api_key", "mal_client_id", "vlc_path", "browser_path", "custom_player_path", "sync_folder",
             "downloads_folder", "last_folder", "gemini_api_key", "groq_api_key", "openrouter_api_key",
             "party_name", "party_server")
BOOL_KEYS = ("queue_next_episodes", "skip_intro", "sounds", "setup_prompt")


def clean_ai_custom(helpers):
    """Your own AI helpers from Settings, checked. Raises ValueError."""
    import uuid
    if not isinstance(helpers, list):
        raise ValueError("your AI helpers must be a list")
    out, seen = [], set()
    for x in helpers[:12]:
        if not isinstance(x, dict):
            continue
        name = str(x.get("name") or "").strip()[:40]
        url = str(x.get("url") or "").strip().rstrip("/")
        if not name and not url:
            continue
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError(f'{name or "your AI helper"}: its address must start with https:// (or http:// on your computer)')
        kind = x.get("kind") if x.get("kind") in ("openai", "anthropic") else "openai"
        hid = re.sub(r"[^\w-]", "", str(x.get("id") or ""))[:16] or uuid.uuid4().hex[:8]
        if hid in seen:
            hid = uuid.uuid4().hex[:8]
        seen.add(hid)
        out.append({"id": hid, "name": name or url, "kind": kind, "url": url,
                    "key": str(x.get("key") or "").strip(), "model": str(x.get("model") or "").strip()[:80]})
    return out


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
        if not isinstance(data.get("services"), list):
            from .services import from_old_settings
            data["services"] = from_old_settings(data)
        # A fallback site you set yourself in an older version stays, as the last of your sites.
        old = stored.get("fallback_url") if isinstance(stored, dict) else None
        if isinstance(old, str) and old.lower().startswith(("http://", "https://")) \
                and old.rstrip("/") != OLD_FALLBACK and all(x.get("url") != old for x in data["services"]):
            from .services import host_name, new_id
            data["services"].append({"id": new_id(), "name": host_name(old), "url": old, "check": [],
                                     "check_mode": "off", "types": ["anime", "tv", "movie"], "on": True})
        # The built-in browser became the default: settings saved before then had
        # "" (Recommended) without anyone choosing it, so they switch once.
        if not isinstance(data.get("migrated"), list):
            data["migrated"] = []
        if "builtin-browser" not in data["migrated"]:
            if isinstance(stored, dict) and stored and not stored.get("browser_path"):
                data["browser_path"] = "builtin"
            data["migrated"] = data["migrated"] + ["builtin-browser"]
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
            if "services" in patch:
                from .services import clean
                data["services"] = clean(patch["services"])
            if "ai_custom" in patch:
                data["ai_custom"] = clean_ai_custom(patch["ai_custom"])
            if "ai_order" in patch:
                if not isinstance(patch["ai_order"], list):
                    raise ValueError("ai_order must be a list")
                data["ai_order"] = [str(x)[:40] for x in patch["ai_order"]][:30]
            if "torrent_sites" in patch:
                from .sites import clean as clean_sites
                data["torrent_sites"] = clean_sites(patch["torrent_sites"])
            if "video_sites" in patch:
                from .scrape import clean as clean_video_sites
                data["video_sites"] = clean_video_sites(patch["video_sites"])
            if "party_avatar" in patch:
                pic = str(patch["party_avatar"] or "")
                if pic and (not re.fullmatch(r"data:image/(jpeg|png|webp);base64,[A-Za-z0-9+/=]+", pic) or len(pic) > 12000):
                    raise ValueError("that picture can't be used - choose a JPEG or PNG image")
                data["party_avatar"] = pic
            if "theme" in patch:
                if patch["theme"] not in THEMES:
                    raise ValueError("unknown colour theme")
                data["theme"] = patch["theme"]
            if "player" in patch:
                if patch["player"] not in ("mpv", "vlc", "custom"):
                    raise ValueError("player must be the built-in one, VLC or your own")
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
