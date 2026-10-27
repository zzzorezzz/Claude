"""Local HTTP server: serves the UI and a small JSON API.

It only listens on 127.0.0.1. Requests must carry the right Host header (to
stop DNS-rebinding tricks) and changes need an X-Watchlist header, which
other websites can't add to requests they send to your PC.
"""

import json
import mimetypes
import os
import re
import sys
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import dialogs, launcher, library, metadata, player, scanner
from .config import Settings
from .store import TYPES, Store, new_item, next_episode

WEB_DIR = Path(__file__).resolve().parent / "web"
REFRESH_AFTER = 7 * 24 * 3600


class ApiError(Exception):
    def __init__(self, message, status=HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.status = status


def _inside(path, folder):
    try:
        path, folder = os.path.realpath(path), os.path.realpath(folder)
        return os.path.commonpath([path, folder]) == folder
    except ValueError:  # different drives on Windows
        return False


class App:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.settings = Settings(self.data_dir / "settings.json")
        self.sync_problem = ""
        self.store = Store(self._list_path(), self.data_dir / "local.json")
        self.library = library.Library(self)
        self.player = player.Player()

    # ---- syncing between computers

    def _list_path(self):
        folder = self.settings.get()["sync_folder"]
        if folder:
            if os.path.isdir(folder):
                self.sync_problem = ""
                return Path(folder) / "watchlist.json"
            self.sync_problem = (f"The sync folder isn't available right now ({folder}), so this "
                                 "computer's own copy of your list is being used until it's back.")
        return self.data_dir / "watchlist.json"

    def set_sync_folder(self, folder):
        folder = (folder or "").strip().strip('"')
        if folder and not os.path.isdir(folder):
            raise ApiError("that folder doesn't exist")
        self.settings.update({"sync_folder": folder})
        self.store.use_file(self._list_path())

    def retry_sync(self):
        """A sync folder that was missing at startup (drive not ready) may be back."""
        if self.sync_problem and self.settings.get()["sync_folder"]:
            path = self._list_path()
            if not self.sync_problem:
                self.store.use_file(path)

    # ---- helpers

    def item(self, item_id):
        try:
            return self.store.get(item_id)
        except KeyError:
            raise ApiError("that title isn't in your list", HTTPStatus.NOT_FOUND) from None

    def status(self):
        s = self.settings.get()
        return {"tmdb": bool(s["tmdb_api_key"]), "mal": bool(s["mal_client_id"]),
                "region": s["region"], "vlc": launcher.find_vlc(s["vlc_path"]),
                "browser": launcher.find_browser(s["browser_path"]),
                "platform": sys.platform,
                "sync": {"folder": s["sync_folder"], "file": str(self.store.path),
                         "problem": self.sync_problem},
                "downloads": s["downloads_folder"], "player": s["player"],
                "mpv": player.find_mpv()}

    def open_link(self, url):
        browser = launcher.find_browser(self.settings.get()["browser_path"])
        try:
            launcher.open_url(url, browser)
        except launcher.LaunchError as e:
            raise ApiError(str(e)) from e

    def refresh_metadata(self, item):
        s = self.settings.get()
        fresh = metadata.details(s, item["type"], item["source"], item["source_id"])
        patch = {k: v for k, v in fresh.items()
                 if k not in ("source", "source_id") and v not in (None, "", [], {})}
        patch["updated"] = time.time()
        return self.store.update(item["id"], patch)

    def refresh_stale(self):
        """Keep ratings and popularity current, quietly, in the background."""
        for item in self.store.list():
            if item["source"] in ("tmdb", "mal") and time.time() - item.get("updated", 0) > REFRESH_AFTER:
                try:
                    self.refresh_metadata(item)
                except (metadata.MetadataError, KeyError):
                    pass
                time.sleep(1)

    def _sources(self, item):
        """Every video for a title: from the downloads folder, plus its own folder if
        one was chosen. Returns (videos, subtitle-finder)."""
        videos = self.library.videos_for(item["id"])
        folder_subs, folder_videos = [], []
        folder = item["local_folder"]
        if folder and os.path.isdir(folder):
            found = scanner.scan_folder(folder)
            folder_videos, folder_subs = found["videos"], found["subs"]
            known = {v["path"] for v in videos}
            videos += [v for v in folder_videos if v["path"] not in known]
        videos.sort(key=lambda v: (v["season"] or 0, v["episodes"][:1] or [0], scanner.natural_key(v["rel"])))
        in_folder = {v["path"] for v in folder_videos}

        def subtitles(video):
            if video["path"] in in_folder:
                return scanner.subtitles_for(video, folder_subs, folder_videos)
            return self.library.subtitles_for(video["path"])
        return videos, subtitles

    def local_info(self, item, season, episode, path=""):
        folder = item["local_folder"]
        videos, subtitles = self._sources(item)
        info = {"folder": folder, "exists": bool(videos) or (bool(folder) and os.path.isdir(folder)),
                "downloads": len(self.library.videos_for(item["id"])),
                "videos": videos, "target": None, "subtitles": [], "available": []}
        if not info["exists"]:
            return info
        if path:
            target = next((v for v in videos if v["path"] == path), None)
        elif item["format"] == "movie":
            target = next((v for v in videos if v["path"] == item["local_file"]), None)
            target = target or scanner.find_movie(item, videos)
        else:
            episodes = scanner.episode_map(item, videos)
            info["available"] = [f"{s}:{e}" for s, e in sorted(episodes)]
            target = episodes.get((season, episode))
        info["target"] = target
        if target:
            info["subtitles"] = subtitles(target)
        return info

    def playable(self, item, path):
        """Only files from this title's folder or the downloads folder can be played."""
        return os.path.isfile(path) and (
            (item["local_folder"] and _inside(path, item["local_folder"]))
            or self.library.contains(path))

    def play(self, item, body):
        s = self.settings.get()
        path = body.get("path") or ""
        if not self.playable(item, path):
            raise ApiError("that file isn't in this title's folder or your downloads folder")
        subtitle = body.get("subtitle")
        if subtitle:
            if not os.path.isfile(subtitle) or Path(subtitle).suffix.lower() not in scanner.SUB_EXTS:
                raise ApiError("that subtitle file can't be used")
        series = item["format"] == "series"
        if series and "season" in body and "episode" in body:
            first_key = f"{max(1, int(body['season']))}:{max(1, int(body['episode']))}"
        else:
            first_key = "movie" if not series else ""
        entries = [{"path": path, "sub": subtitle or None, "key": first_key}]
        patch = {"last_played": time.time()}
        if series:
            videos, subtitles = self._sources(item)
            keys = {}
            for (season, episode), v in sorted(scanner.episode_map(item, videos).items()):
                keys.setdefault(v["path"], f"{season}:{episode}")
            entries[0]["key"] = first_key or keys.get(path, path)
            first = next((v for v in videos if v["path"] == path), None)
            if first and s["queue_next_episodes"]:
                for v in scanner.episode_queue(videos, first)[1:]:
                    subs = subtitles(v)
                    # Keep using the same kind of subtitle (none / auto) for the queue.
                    sub = subs[0]["path"] if subs and subtitle else None
                    entries.append({"path": v["path"], "sub": sub, "key": keys.get(v["path"], v["path"])})
            if first_key:
                patch["progress"] = {"season": int(first_key.split(":")[0]),
                                     "episode": int(first_key.split(":")[1])}
        else:
            patch["local_file"] = path
        for e in entries:
            e["label"] = self._label(item, e["key"])
        start = 0 if body.get("from_start") else player.resume_point(item["positions"].get(entries[0]["key"]))
        wanted = body.get("player") or s["player"]
        note = ""
        if wanted == "mpv":
            mpv = player.find_mpv()
            if mpv:
                self.player.play(player.Session(
                    mpv, item["id"], entries, start=start,
                    audio_langs="jpn,ja,japanese,eng,en" if item["type"] == "anime" else "",
                    on_progress=self._save_position, on_finished=self._finished_entry))
                self.store.update(item["id"], patch)
                return {"ok": True, "player": "mpv", "file": Path(path).name,
                        "queued": len(entries) - 1, "resumed_at": start}
            note = "The built-in player is missing, so VLC was used."
        self._play_vlc(entries, start)
        self.store.update(item["id"], patch)
        return {"ok": True, "player": "vlc", "file": Path(path).name, "queued": len(entries) - 1,
                "resumed_at": start, "note": note}

    def _play_vlc(self, entries, start=0):
        vlc = launcher.find_vlc(self.settings.get()["vlc_path"])
        playlist = [(e["path"], e.get("sub"), start if i == 0 else 0) for i, e in enumerate(entries)]
        try:
            launcher.play_in_vlc(vlc, playlist)
        except launcher.LaunchError as e:
            raise ApiError(str(e)) from e

    @staticmethod
    def _label(item, key):
        if ":" in key and key.replace(":", "").isdigit():
            season, episode = key.split(":")
            return f"{item['title']} - S{season} E{episode}"
        return item["title"]

    # ---- following the built-in player

    def _save_position(self, item_id, entry, pos, dur):
        try:
            item = self.store.get(item_id)
        except KeyError:
            return
        positions = dict(item["positions"])
        positions[entry["key"]] = {"pos": round(pos, 1), "dur": round(dur or 0, 1), "at": time.time()}
        self.store.update(item_id, {"positions": positions})

    def _finished_entry(self, item_id, entry):
        """An episode or film played to the end: forget its place, move progress on."""
        try:
            item = self.store.get(item_id)
        except KeyError:
            return
        positions = dict(item["positions"])
        positions.pop(entry["key"], None)
        patch = {"positions": positions}
        key = entry["key"]
        if item["format"] == "series" and ":" in key and key.replace(":", "").isdigit():
            season, episode = (int(x) for x in key.split(":"))
            nxt = next_episode(dict(item, progress={"season": season, "episode": episode}))
            current = (item["progress"]["season"], item["progress"]["episode"])
            if nxt and nxt > current:
                patch["progress"] = {"season": nxt[0], "episode": nxt[1]}
        elif item["format"] == "movie":
            patch["watched_at"] = time.time()
        self.store.update(item_id, patch)

    def switch_to_vlc(self):
        session = self.player.take()
        if not session:
            raise ApiError("nothing is playing in the built-in player")
        entries = session.entries[session.index:]
        self._play_vlc(entries, session.pos or 0)
        return {"ok": True, "file": Path(entries[0]["path"]).name}

    def watch_online(self, item):
        s = self.settings.get()
        title = item["title"]
        region = s["region"]
        result = {"region": region, "services": [], "service": "", "url": "", "message": ""}
        on = {"netflix": False, "hbomax": False}
        if not s["tmdb_api_key"]:
            result["message"] = "No TMDB token in Settings, so Netflix and HBO Max couldn't be checked."
        else:
            try:
                tmdb = metadata.TMDB(s["tmdb_api_key"])
                ref = item["tmdb_ref"]
                if not ref and item["type"] == "anime":
                    ref = tmdb.find_anime(item["aliases"] or [title], item["year"], item["format"])
                    if ref:
                        self.store.update(item["id"], {"tmdb_ref": ref})
                if ref:
                    providers = tmdb.providers(ref, region)
                    result["services"] = [p["name"] for p in providers["services"]
                                          if p["kind"] in ("flatrate", "free", "ads")]
                    for p in providers["services"]:
                        service = metadata.service_of(p)
                        if service and p["kind"] in ("flatrate", "free", "ads"):
                            on[service] = True
                else:
                    result["message"] = "Couldn't match this anime on TMDB to check streaming services."
            except metadata.MetadataError as e:
                result["message"] = f"Couldn't check streaming services: {e}."
        if on["netflix"]:
            result.update(service="Netflix", url=launcher.fill_link(s["netflix_url"], title))
        elif on["hbomax"]:
            result.update(service="HBO Max", url=launcher.fill_link(s["hbomax_url"], title))
        else:
            own = s["anime_site_url"] if item["type"] == "anime" else s["movie_tv_site_url"]
            url = launcher.fill_link(own or s["fallback_url"], title)
            host = urllib.parse.urlsplit(url).hostname or url
            result.update(service=host.removeprefix("www."), url=url, own_site=bool(own))
        self.open_link(result["url"])
        self.store.update(item["id"], {"last_played": time.time()})
        return result


class Handler(BaseHTTPRequestHandler):
    server_version = "Watchlist"
    app: App = None  # set by make_server

    ROUTES = [
        ("GET", r"/api/ping", "ping"),
        ("GET", r"/api/state", "state"),
        ("GET", r"/api/version", "version"),
        ("GET", r"/api/search", "search"),
        ("POST", r"/api/items", "add_item"),
        ("GET", r"/api/items/(\w+)", "get_item"),
        ("PUT", r"/api/items/(\w+)", "update_item"),
        ("DELETE", r"/api/items/(\w+)", "delete_item"),
        ("POST", r"/api/items/(\w+)/refresh", "refresh_item"),
        ("GET", r"/api/items/(\w+)/season/(\d+)", "season"),
        ("GET", r"/api/items/(\w+)/local", "local"),
        ("POST", r"/api/items/(\w+)/play", "play"),
        ("POST", r"/api/items/(\w+)/online", "online"),
        ("POST", r"/api/items/(\w+)/watched", "watched"),
        ("PUT", r"/api/items/(\w+)/collections", "item_collections"),
        ("PUT", r"/api/items/(\w+)/folder", "move_to_folder"),
        ("POST", r"/api/collections", "add_collection"),
        ("PUT", r"/api/collections/(\w+)", "rename_collection"),
        ("DELETE", r"/api/collections/(\w+)", "delete_collection"),
        ("GET", r"/api/player", "player_status"),
        ("POST", r"/api/player/vlc", "player_vlc"),
        ("POST", r"/api/player/stop", "player_stop"),
        ("GET", r"/api/library", "library"),
        ("POST", r"/api/library/rescan", "library_rescan"),
        ("POST", r"/api/library/answer", "library_answer"),
        ("GET", r"/api/settings", "get_settings"),
        ("PUT", r"/api/settings", "put_settings"),
        ("POST", r"/api/settings/test", "test_settings"),
        ("POST", r"/api/browse", "browse"),
        ("POST", r"/api/open", "open_link"),
        ("POST", r"/api/reveal", "reveal"),
    ]

    # ---- plumbing

    def log_message(self, fmt, *args):
        pass

    def _allowed_host(self):
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _send(self, status, body: bytes, content_type, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data, status=HTTPStatus.OK):
        self._send(status, json.dumps(data).encode("utf-8"), "application/json; charset=utf-8")

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1_000_000:
            raise ApiError("request too large")
        if not length:
            return {}
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            raise ApiError("invalid JSON") from None
        if not isinstance(data, dict):
            raise ApiError("expected a JSON object")
        return data

    def _dispatch(self, method):
        if not self._allowed_host():
            return self._send(HTTPStatus.FORBIDDEN, b"forbidden", "text/plain")
        url = urllib.parse.urlsplit(self.path)
        if not url.path.startswith("/api/"):
            if method in ("GET", "HEAD"):
                return self._static(url.path)
            return self._send(HTTPStatus.METHOD_NOT_ALLOWED, b"", "text/plain")
        if method != "GET" and self.headers.get("X-Watchlist") != "1":
            return self._json({"error": "missing X-Watchlist header"}, HTTPStatus.FORBIDDEN)
        self.query = {k: v[-1] for k, v in urllib.parse.parse_qs(url.query).items()}
        for route_method, pattern, name in self.ROUTES:
            m = re.fullmatch(pattern, url.path)
            if m and route_method == method:
                try:
                    body = self._body() if method in ("POST", "PUT") else {}
                    return self._json(getattr(self, "api_" + name)(body, *m.groups()))
                except ApiError as e:
                    return self._json({"error": str(e)}, e.status)
                except metadata.MetadataError as e:
                    return self._json({"error": str(e)}, HTTPStatus.BAD_GATEWAY)
                except (ValueError, TypeError) as e:
                    return self._json({"error": str(e) or "invalid request"}, HTTPStatus.BAD_REQUEST)
                except Exception as e:  # keep the app alive whatever happens
                    return self._json({"error": f"unexpected error: {e}"},
                                      HTTPStatus.INTERNAL_SERVER_ERROR)
        return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("HEAD")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _static(self, path):
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (WEB_DIR / rel).resolve()
        if not _inside(str(target), str(WEB_DIR)) or not target.is_file():
            return self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        csp = ("default-src 'self'; img-src 'self' https: data:; style-src 'self' 'unsafe-inline'; "
               "frame-src https://www.youtube.com https://www.youtube-nocookie.com; "
               "script-src 'self'; connect-src 'self'")
        self._send(HTTPStatus.OK, target.read_bytes(), ctype,
                   {"Content-Security-Policy": csp, "Referrer-Policy": "strict-origin-when-cross-origin"})

    # ---- API

    def api_ping(self, body):
        return {"app": "watchlist"}

    def _version(self):
        return f"{self.app.store.current_version()}.{self.app.library.version}"

    def api_state(self, body):
        self.app.retry_sync()
        return {"items": self.app.store.list(), "collections": self.app.store.collections(),
                "status": self.app.status(), "library": self.app.library.state(),
                "version": self._version()}

    def api_version(self, body):
        """Polled by the window, so changes from the other computer (or new
        downloads) show up."""
        self.app.retry_sync()
        return {"version": self._version()}

    def api_player_status(self, body):
        status = self.app.player.status()
        if status.get("item_id"):
            try:
                status["title"] = self.app.store.get(status["item_id"])["title"]
            except KeyError:
                pass
        return status

    def api_player_vlc(self, body):
        return self.app.switch_to_vlc()

    def api_player_stop(self, body):
        session = self.app.player.take()
        return {"ok": bool(session)}

    def api_library(self, body):
        return self.app.library.state()

    def api_library_rescan(self, body):
        self.app.library.scan(force=True)
        return self.app.library.state()

    def api_library_answer(self, body):
        self.app.library.answer(body)
        return self.app.library.state()

    def api_search(self, body):
        item_type = self.query.get("type", "")
        if item_type not in TYPES:
            raise ApiError("type must be movie, tv or anime")
        results = metadata.search(self.app.settings.get(), item_type, self.query.get("q", ""))
        for r in results:
            existing = self.app.store.find_source(r["source"], r["source_id"])
            r["in_list"] = existing["id"] if existing else None
        return {"results": results}

    def api_add_item(self, body):
        item_type = body.get("type")
        if item_type not in TYPES:
            raise ApiError("type must be movie, tv or anime")
        existing = self.app.store.find_source(body.get("source"), body.get("source_id"))
        if existing:
            return existing
        info = metadata.details(self.app.settings.get(), item_type,
                                body.get("source"), str(body.get("source_id", "")))
        item = new_item(item_type, info)
        item["collections"] = [c for c in body.get("collections", []) if isinstance(c, str)]
        return self.app.store.add(item)

    def api_get_item(self, body, item_id):
        return self.app.item(item_id)

    def api_update_item(self, body, item_id):
        item = self.app.item(item_id)
        patch = {}
        if "progress" in body:
            p = body["progress"]
            patch["progress"] = {"season": max(1, int(p.get("season", 1))),
                                 "episode": max(1, int(p.get("episode", 1)))}
        if "local_folder" in body:
            folder = str(body["local_folder"]).strip().strip('"')
            if folder and not os.path.isdir(folder):
                raise ApiError("that folder doesn't exist")
            patch["local_folder"] = folder
            if folder != item["local_folder"]:
                patch["local_file"] = ""
        if "local_file" in body:
            path = str(body["local_file"]).strip().strip('"')
            if path and not os.path.isfile(path):
                raise ApiError("that file doesn't exist")
            folder = patch.get("local_folder", item["local_folder"])
            if path and not (folder and _inside(path, folder)):
                # A file picked from somewhere else: its folder becomes the title's folder.
                patch["local_folder"] = os.path.dirname(path)
            patch["local_file"] = path
        if "local_broken" in body:
            patch["local_broken"] = bool(body["local_broken"])
        elif "local_folder" in patch or "local_file" in patch:
            # Pointing the title at a new folder or file counts as fixing it.
            patch["local_broken"] = False
        return self.app.store.update(item_id, patch)

    def api_move_to_folder(self, body, item_id):
        self.app.item(item_id)
        try:
            return self.app.store.move_to_folder(item_id, str(body.get("folder") or ""))
        except KeyError:
            raise ApiError("folder not found", HTTPStatus.NOT_FOUND) from None

    def api_delete_item(self, body, item_id):
        self.app.item(item_id)
        self.app.store.delete(item_id)
        return {"ok": True}

    def api_refresh_item(self, body, item_id):
        return self.app.refresh_metadata(self.app.item(item_id))

    def api_season(self, body, item_id, number):
        item = self.app.item(item_id)
        ref = item["tmdb_ref"]
        if item["type"] == "anime" or not ref.startswith("tv/"):
            return {"episodes": []}
        tmdb = metadata.TMDB(self.app.settings.get()["tmdb_api_key"])
        return {"episodes": tmdb.season(ref.split("/")[1], int(number))}

    def api_local(self, body, item_id):
        item = self.app.item(item_id)
        season = int(self.query.get("season") or item["progress"]["season"])
        episode = int(self.query.get("episode") or item["progress"]["episode"])
        try:
            return self.app.local_info(item, season, episode, self.query.get("path", ""))
        except OSError as e:
            raise ApiError(f"couldn't read the folder: {e}") from e

    def api_play(self, body, item_id):
        return self.app.play(self.app.item(item_id), body)

    def api_online(self, body, item_id):
        return self.app.watch_online(self.app.item(item_id))

    def api_watched(self, body, item_id):
        item = self.app.item(item_id)
        nxt = next_episode(item)
        if nxt is None:
            return {"item": item, "finished": True}
        item = self.app.store.update(item_id, {"progress": {"season": nxt[0], "episode": nxt[1]}})
        return {"item": item, "finished": False}

    def api_item_collections(self, body, item_id):
        self.app.item(item_id)
        cols = body.get("collections")
        if not isinstance(cols, list):
            raise ApiError("collections must be a list")
        return self.app.store.set_item_collections(item_id, [str(c) for c in cols])

    def api_add_collection(self, body):
        kind = body.get("type") or None
        if kind is not None and kind not in TYPES:
            raise ApiError("type must be movie, tv or anime")
        return self.app.store.add_collection(body.get("name", ""), kind)

    def api_rename_collection(self, body, col_id):
        try:
            return self.app.store.rename_collection(col_id, body.get("name", ""))
        except KeyError:
            raise ApiError("collection not found", HTTPStatus.NOT_FOUND) from None

    def api_delete_collection(self, body, col_id):
        try:
            self.app.store.delete_collection(col_id)
        except KeyError:
            raise ApiError("collection not found", HTTPStatus.NOT_FOUND) from None
        return {"ok": True}

    def api_get_settings(self, body):
        return {"settings": self.app.settings.get(), "status": self.app.status()}

    def api_put_settings(self, body):
        if "downloads_folder" in body:
            folder = str(body["downloads_folder"] or "").strip().strip('"')
            if folder and not os.path.isdir(folder):
                raise ApiError("that downloads folder doesn't exist")
            body["downloads_folder"] = folder
        if "sync_folder" in body:
            folder = str(body.pop("sync_folder") or "")
            if folder.strip().strip('"') != self.app.settings.get()["sync_folder"]:
                self.app.set_sync_folder(folder)
        settings = self.app.settings.update(body)
        if "downloads_folder" in body:
            self.app.library.scan(force=True)
        return {"settings": settings, "status": self.app.status()}

    def api_test_settings(self, body):
        return metadata.test_keys(self.app.settings.get())

    def api_browse(self, body):
        kind = body.get("kind", "folder")
        if kind not in ("folder", "file"):
            raise ApiError("kind must be folder or file")
        s = self.app.settings.get()
        initial = body.get("initial") or s["last_folder"]
        if initial and not os.path.isdir(initial):
            initial = os.path.dirname(initial)
        try:
            path = dialogs.choose(kind, str(body.get("title") or "Choose"), initial or "",
                                  str(body.get("types") or "video"))
        except dialogs.DialogUnavailable as e:
            raise ApiError(str(e)) from e
        if path and kind == "folder":
            self.app.settings.update({"last_folder": path})
        return {"path": path}

    def api_open_link(self, body):
        self.app.open_link(str(body.get("url", "")))
        return {"ok": True}

    def api_reveal(self, body):
        item = self.app.item(str(body.get("item_id", "")))
        path = str(body.get("path") or item["local_folder"])
        if not item["local_folder"] or not _inside(path, item["local_folder"]):
            raise ApiError("that file isn't in this title's folder")
        try:
            launcher.reveal(path)
        except launcher.LaunchError as e:
            raise ApiError(str(e)) from e
        return {"ok": True}


def make_server(app: App, port: int):
    handler = type("BoundHandler", (Handler,), {"app": app})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    return server


def start_background_refresh(app: App):
    threading.Thread(target=app.refresh_stale, daemon=True).start()
    threading.Thread(target=library.watch, args=(app,), daemon=True).start()
