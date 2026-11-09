"""Local HTTP server: serves the UI and a small JSON API.

It only listens on 127.0.0.1. Requests must carry the right Host header (to
stop DNS-rebinding tricks) and changes need an X-Watchlist header, which
other websites can't add to requests they send to your PC.
"""

import hashlib
import http.cookies
import json
import mimetypes
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from concurrent.futures import ThreadPoolExecutor

from . import ai, cdp, dialogs, discover, hlsfix, livecollections, partyserver, remote, transcode, webplay, picker, relay, subfinder, importer, launcher, library, metadata, phone_player, player, scanner, scrape, services, share, sites, sorter, syncplay, webtools
from . import mood as mood_mod
from . import party as party_mod
from . import torrents as torrents_mod
from .config import Settings, read_json, write_json
from .store import METADATA_FIELDS, TYPES, Store, new_item, next_episode

WEB_DIR = Path(__file__).resolve().parent / "web"
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("text/javascript", ".js")
# The request being answered for a phone over the network: links the app would open on this
# computer go back to the phone instead (open_link).
_request = threading.local()
REFRESH_AFTER = 7 * 24 * 3600
RELATED_TTL = 3 * 24 * 3600
# "Play this with" on a title's page: "" is "Use anything" (a file on your computer,
# else the torrent, else it asks between your video sites and watching online).
PLAY_WITH = ("", "file", "torrent", "scraped", "scraped-browser", "online")


SUBS_FOR = 12 * 3600           # subtitles found online for a title that doesn't keep them: deleted after this


def _english(path):
    """0 for a subtitle file named as English, 1 otherwise (English first when there are both)."""
    return 0 if re.search(r"(?:^|[\W_])(?:en|eng|english)(?:[\W_]|$)", Path(path).stem.lower()) else 1


# Public Syncplay servers, tried in order by "Find a working server".


class OnlineError(Exception):
    """No video site played an episode: why, the titles to pick from (when the match was
    unclear: {"site", "site_name", "choices", "question"}), and a page to open instead."""

    def __init__(self, message, choices=None, page=""):
        super().__init__(message)
        self.choices, self.page = choices, page


class ApiError(Exception):
    def __init__(self, message, status=HTTPStatus.BAD_REQUEST, code=""):
        super().__init__(message)
        self.status = status
        self.code = code          # (what the screens can act on: "no_server" - offer the other ways)


def _inside(path, folder):
    try:
        path, folder = os.path.realpath(path), os.path.realpath(folder)
        return os.path.commonpath([path, folder]) == folder
    except ValueError:  # different drives on Windows
        return False


def item_kind_is_single(item):
    return (item.get("episodes_total") or 0) <= 1 and library.item_kind(item) in ("ova", "special", "movie")


def _entry(result, item_type):
    """A connected title, with what the story guide needs to know about it."""
    return dict(sorter._suggestion(result, item_type), via=result.get("via") or "", date=result.get("date") or "",
                episodes_total=result.get("episodes_total"), overview=(result.get("overview") or "")[:400])


KIND_WORDS = {"anime": "anime", "tv": "TV shows", "movie": "movies"}


class App:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.settings = Settings(self.data_dir / "settings.json")
        self.sync_problem = ""
        self.store = Store(self._list_path(), self.data_dir / "local.json")
        self.library = library.Library(self)
        self.sorter = sorter.Sorter(self)
        self.importer = importer.Importer(self)
        self.player = player.Player()
        self._related_path = self.data_dir / "related.json"
        self._related = read_json(self._related_path, {})
        self._related_lock = threading.Lock()
        self.anime_index = discover.AnimeIndex(self.data_dir / "anime_index.json")
        self.torrents = torrents_mod.Torrents(self, self.data_dir)
        self.site_jobs = sites.Jobs(self)
        self.scraper = scrape.Scraper(self.data_dir)                       # your video sites
        self.scraper.render, self.scraper.catch = self._render_page, self._catch_video
        self.scraper.servers = self._list_servers
        # Every server of the episode playing, each checked in the background (the player's
        # Servers list): {(title, episode): {"site", "page", "items": {id: {...}}, ...}}.
        self._servers = {}
        # Your own sites: videos found ({(item, "s:e"): (site, found, when)}), the background
        # matching of a title's episodes (the latest run per title goes on) and how far it is.
        self._online_found, self._link_runs, self._link_progress = {}, {}, {}
        self._finding = {}               # (title, episode) -> being found now (an Event)
        self._tried_sources = {}         # (title, episode) -> sources that didn't play there
        # What you showed the app on each site, finding a video yourself (picker.py).
        phone_player.on_closed = self._player_exited          # (the phone's player closed)
        # What's left of torrents that are gone, cleared once the app is up.
        tidy = threading.Timer(30, self._clean_leftovers)
        tidy.daemon = True
        tidy.start()
        # Your phone or tablet on your own network (Settings → Use on your phone): off until you
        # switch it on; then the app listens on this computer's own addresses too (remote.py).
        self.remote = remote.Access(self, lambda ip, port: remote_listener(self, ip, port))
        # A phone playing on itself (webplay.py): its player, the tickets its videos come
        # through, and the videos made playable for it (transcode.py).
        self.tickets = webplay.Tickets()
        self.web_jobs = webplay.Jobs(self.data_dir / "phone-cache")
        self.web_jobs.clear()
        self.web_sessions = {}           # device -> WebSession
        self.party_device = None         # the phone a watch party is for (its own player), or None
        self._probes = {}                # a video -> what's in it (ffprobe)
        # Shared collections that stay up to date: yours put on the relays, others' looked at.
        self._live_lock, self._live_sent, self._live_status = threading.Lock(), {}, {}
        self._learned_path = self.data_dir / "learned-sites.json"
        webtools.LEARNED.update(read_json(self._learned_path, {}))
        # Sub or dub, switched in the player (the episodes after it follow); subtitle searches.
        self._online_track, self._subjobs = {}, {}
        threading.Thread(target=self._clean_subs, daemon=True).start()
        # Elsewhere than Windows, videos are found in a hidden Chromium kept in the app's folder
        # (the one on the computer, or a small one fetched once - now, so it's ready).
        cdp.HOME = self.data_dir / "video-finder"
        if webtools.can_catch() and sys.platform != "win32" and not cdp.find():
            threading.Thread(target=self._get_chromium, daemon=True).start()
        self.scraper.can_render, self.scraper.can_catch = webtools.can_render, webtools.can_catch
        self._connected_jobs = {}           # Everything connected being looked for: how far along
        self._filler = ThreadPoolExecutor(4, thread_name_prefix="fill")   # titles added straight away
        self.moods = mood_mod.Jobs(self)                                   # I'm in the mood for…
        self.party = None
        self.party_last = None
        self._party_lock = threading.Lock()
        self._lock = threading.Lock()
        self._ai_streams = {}               # (item, country, services) -> (when, {name: has it})
        self._similar = {}                  # (item, title) -> (when, related entries by name)
        self._scrape_runs = {}              # item -> the latest "Play online (my sites)" asked for

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
                "platform": "android" if launcher.ANDROID else sys.platform,
                "sync": {"folder": s["sync_folder"], "file": str(self.store.path),
                         "problem": self.sync_problem},
                "downloads": s["downloads_folder"], "player": s["player"],
                "mpv": player.find_mpv(), "ai": [ai.NAMES[p] for p in ai.available(s)],
                "ai_resting": ai.resting(s),
                "party": bool(self.party and self.party.active),
                "services": [{"name": x["name"], "types": x["types"], "on": x["on"]} for x in s["services"]],
                "torrents": {"available": torrents_mod.available(), "count": len(self.torrents.records),
                             "delete_after": torrents_mod.DELETE_AFTER},
                "torrent_sites": {t: len(sites.sites_for(s, t)) for t in TYPES},
                "video_sites": {t: [{"id": x["id"], "name": x["name"]} for x in scrape.sites_for(s, t)] for t in TYPES},
                "sounds": s["sounds"], "setup_prompt": s["setup_prompt"], "adblock": s["adblock"],
                "mode": s["mode"], "ask_guide": s["ask_guide"],
                "catch_videos": webtools.can_catch(), "ytdlp": scrape.has_ytdlp(),
                "windows": launcher.can_open_windows(), "theme": s["theme"],
                "phone_access": bool(s["phone_access"])}

    # ---- a phone playing on itself (Use on your phone: webplay.py, transcode.py)

    def _web_device(self):
        """The phone a request came from, when it plays on itself (not on this computer's screen)."""
        device = getattr(_request, "device", None)
        return device if device and device.get("play_on") != "pc" else None

    def web_session(self, device, item, entries, start=0.0, first=0, paused=False):
        old = self.web_sessions.get(device["id"])
        if old and not old.ended:
            old.stop()
        self.web_jobs.stop(device["id"])
        session = webplay.WebSession(device["id"], item["id"], entries, start, paused, first,
                                     anime=item["type"] == "anime", title=item["title"])
        session.on_end = self._web_ended
        self.web_sessions[device["id"]] = session
        return session

    def web_forget(self, device_ids):
        """Devices taken out: their videos' addresses stop working, their players close."""
        for d in device_ids:
            self.tickets.drop_device(d)
            session = self.web_sessions.pop(d, None)
            if session and not session.ended:
                session.stop()
            self.web_jobs.stop(d)

    def _web_ended(self, session):
        self.web_jobs.stop(session.device)
        if not any(s is not session and not s.ended for s in self.web_sessions.values()):
            self._player_exited(session)

    def _probe(self, source):
        try:
            st = os.stat(source)
            key = (source, st.st_size, st.st_mtime)
        except OSError:
            key = (source,)
        hit = self._probes.get(source)
        if hit and hit[0] == key:
            return hit[1]
        try:
            info = transcode.probe(source)
        except RuntimeError as e:
            raise ApiError(f"the video couldn't be read ({e})") from e
        self._probes[source] = (key, info)
        return info

    def _phone_sub(self, device, target, label, lang="", absolute=True, styled=False):
        t = self.tickets.make(device, dict(target, kind="sub"))
        return {"label": label, "lang": lang, "url": f"/stream/web/{t}/sub.vtt", "absolute": absolute, "styled": styled}

    def web_media(self, session, index, at=0.0, caps=None, quality=None, burn=None):
        """What the phone's player opens for an entry: {"src", "type" ("hls" / "mp4"), "offset" (where
        the video it gets starts, in the episode), "duration", "audio", "subs", "how"}."""
        if not 0 <= index < len(session.entries):
            raise ApiError("that episode isn't in the player's list")
        if not transcode.find()[0]:
            raise ApiError("playing on a phone needs ffmpeg, which is missing from this copy of the app")
        entry, device = session.entries[index], session.device
        item = self.store.get(session.item_id)
        key = entry.get("key") or ""
        quality = str(quality or self.settings.get().get("phone_quality") or "1080")
        out = {"index": index, "key": key, "label": entry.get("label") or "", "offset": 0.0, "duration": None,
               "audio": [], "subs": [], "how": ""}
        if entry.get("web") or entry.get("online"):
            # A site's video: through the app, as the site wants it asked for (its headers stay here).
            got = self._online_found.get((item["id"], key))
            path = str(entry.get("path") or "")
            if not got and path.startswith(("http://", "https://")) and "/online/" not in path \
                    and not path.startswith("http://127.0.0.1"):
                # (a watch party's: the host's video, which plays here too)
                got = ({"name": (entry.get("party") or {}).get("site", {}).get("name") or "the host's site", "id": ""},
                       {"url": path, "referer": entry.get("referer") or "", "headers": entry.get("headers") or {},
                        "subtitle": (entry.get("party") or {}).get("sub") or ""}, time.time())
            if not got or "/online/" in path:
                season, episode = self._key_numbers(key)
                try:
                    self.online_video(item["id"], season, episode, entry.get("site") or "", entry.get("track") or "")
                except (OnlineError, KeyError) as e:
                    raise ApiError(str(e), code="no_video") from e
                got = self._online_found.get((item["id"], key))
            if not got:
                raise ApiError("couldn't find this episode's video", code="no_video")
            site, found = got[0], got[1]
            headers = hlsfix._headers(found.get("headers"), found.get("referer"))
            kind = phone_player.video_kind({"path": found["url"], "headers": found.get("headers"), "referer": found.get("referer")})
            if kind == "dash":
                raise ApiError("this server's video is a kind phones can't play - try another server", code="no_video")
            hls = kind == "hls" or ".m3u8" in found["url"].lower()
            t = self.tickets.make(device, {"kind": "remote", "url": found["url"], "headers": headers, "urls": [], "hls": hls})
            out.update(src=f"/stream/web/{t}/media.{'m3u8' if hls else 'mp4'}", type="hls" if hls else "mp4", how="site",
                       site=site["name"], track=(found.get("stream") or {}).get("kind") or entry.get("track") or "sub")
            if found.get("subtitle"):
                out["subs"].append(self._phone_sub(device, {"url": found["subtitle"], "headers": headers},
                                                 "English", "en"))
            saved = self.saved_sub(item, key)
            if saved and os.path.isfile(saved):
                out["subs"].append(self._phone_sub(device, {"path": saved}, Path(saved).stem[:40]))
            return out
        source = entry["path"]
        local = os.path.isfile(source)
        info = self._probe(source)
        burn_spec = None
        if burn is not None and burn != "":
            if str(burn) == "file" and entry.get("sub"):
                burn_spec = {"path": entry["sub"], "n": None}
            elif str(burn).isdigit():
                burn_spec = {"path": source, "n": int(burn)}
        how = transcode.plan(info, caps, quality, burn=burn_spec, local=local)
        out["duration"] = info["duration"]
        if how["direct"]:
            t = self.tickets.make(device, {"kind": "file", "path": source})
            out.update(src=f"/stream/web/{t}/video.mp4", type="mp4", how="direct")
            for s in info["subs"]:
                if s["text"]:
                    out["subs"].append(self._phone_sub(device, {"path": source, "stream": s["n"]},
                                                     transcode.track_name(s, s["n"], "Subtitles"), s["lang"], styled=s["styled"]))
        else:
            # Repackaged from the start when only the box changes (fast, from a file here);
            # converted from where you are otherwise.
            start = 0.0 if how["video"] == "copy" and local else float(at or 0)
            if start and info["duration"] and start > info["duration"] - 5:
                start = max(0.0, info["duration"] - 30)
            texts = [s for s in info["subs"] if s["text"]][:6]
            job = self.web_jobs.start(device, source, info, how, start, subs=[s["n"] for s in texts])
            if not job.wait_for("master.m3u8", 40):
                why = job.failed()
                job.stop()
                raise ApiError(f"the video couldn't be got ready for the phone ({why or 'it took too long'})")
            t = self.tickets.make(device, {"kind": "job", "job": job})
            out.update(src=f"/stream/web/{t}/master.m3u8", type="hls", offset=job.start, how=how["video"],
                       audio=[{"label": a["label"], "lang": a["lang"], "default": a["default"]} for a in job.audio],
                       encoder=transcode.encoder() if how["video"] == "encode" else "",
                       burned=burn_spec is not None)
            for s in texts:
                out["subs"].append({"label": transcode.track_name(s, s["n"], "Subtitles"), "lang": s["lang"],
                                    "url": f"/stream/web/{t}/sub{s['n']}.vtt", "absolute": False, "live": True,
                                    "styled": s["styled"], "n": s["n"]})
        if entry.get("sub") and os.path.isfile(entry["sub"]):
            out["subs"].insert(0, self._phone_sub(device, {"path": entry["sub"]}, Path(entry["sub"]).stem[:40]))
        return out

    def web_seek(self, session, t, caps=None, quality=None, burn=None):
        """A seek on the phone past what's ready: wait for it (a file here, repackaged, is quickly
        all there), or start again from there."""
        job = self.web_jobs.current(session.device)
        entry = session.current()
        if not job or not entry or job.source != entry.get("path"):
            return {"restart": False}
        if job.start <= t <= job.start + job.ready_until() + 6:
            return {"restart": False}
        if job.how["video"] == "copy" and os.path.isfile(job.source) and t >= job.start:
            return {"restart": False, "wait": True}
        return dict(self.web_media(session, session.index, at=t, caps=caps, quality=quality, burn=burn), restart=True)

    def open_link(self, url):
        here = getattr(_request, "links", None)
        if here is not None:
            here.append(url)            # (asked for by a phone: it opens there, not on this computer)
            return
        webtools.ADBLOCK = self.settings.get()["adblock"]
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

    def add_title(self, item_type, source, source_id, collections=(), hint=None, listed=True):
        """Add a title. With `hint` (what a search result already shows: its name,
        poster, year...) it's in your list straight away and its full info is
        fetched in the background - that carries on whatever you do next."""
        if item_type not in TYPES:
            raise ApiError("type must be movie, tv or anime")
        collections = [c for c in collections if isinstance(c, str)]
        existing = self.store.find_source(source, source_id)
        if existing:
            patch = {}
            if collections:
                patch["collections"] = existing["collections"] + [c for c in collections if c not in existing["collections"]]
            if listed and existing.get("listed") is False:
                patch["listed"] = True
            return self.store.update(existing["id"], patch) if patch else existing
        if isinstance(hint, dict) and str(hint.get("title") or "").strip() and source in ("tmdb", "mal"):
            info = {k: hint.get(k) for k in ("title", "original_title", "year", "poster", "backdrop", "overview",
                                              "rating", "rating_source", "genres", "media_type", "episodes_total",
                                              "popularity", "mal_members") if hint.get(k) not in (None, "", [])}
            item = new_item(item_type, dict(info, source=source, source_id=str(source_id)))
            item.update(collections=collections, listed=bool(listed), pending=True)
            item = self.store.add(item)
            self.fill_later(item["id"])
            return item
        s = self.settings.get()
        info = metadata.details(s, item_type, source, str(source_id or ""))
        if s["mal_client_id"] and metadata.tmdb_is_anime(info):
            # Anime always come from MyAnimeList (its episode numbers match fansub releases).
            twin = metadata.mal_twin(s, info)
            if twin:
                existing = self.store.find_source("mal", twin["source_id"])
                if existing:
                    return existing
                item_type, info = "anime", metadata.details(s, "anime", "mal", twin["source_id"])
        item = new_item(item_type, info)
        item["collections"] = collections
        item["listed"] = bool(listed)
        return self.store.add(item)

    def mark_listed(self, rows):
        """Each search result with the id of the title in your list it is, if it is."""
        for r in rows:
            mine = self.store.find_source(r["source"], r["source_id"])
            r["in_list"] = mine["id"] if mine else None
        return rows

    def fill_later(self, item_id):
        self._filler.submit(self._fill, item_id)

    def _fill(self, item_id):
        """A title added straight away: its full info (and, for an anime found on
        TMDB, its MyAnimeList entry instead). Tried again later if it fails."""
        try:
            item = self.store.get(item_id)
        except KeyError:
            return
        if not item.get("pending"):
            return
        s = self.settings.get()
        try:
            item_type = item["type"]
            info = metadata.details(s, item_type, item["source"], str(item["source_id"]))
            if s["mal_client_id"] and metadata.tmdb_is_anime(info):
                twin = metadata.mal_twin(s, info)
                if twin:
                    other = self.store.find_source("mal", twin["source_id"])
                    if other and other["id"] != item_id:
                        # It's in your list already: the two become one.
                        self.store.update(other["id"], {
                            "collections": other["collections"] + [c for c in item["collections"] if c not in other["collections"]],
                            "listed": other.get("listed") is not False or item.get("listed") is not False})
                        self.store.delete(item_id)
                        return
                    item_type, info = "anime", metadata.details(s, "anime", "mal", twin["source_id"])
        except (metadata.MetadataError, OSError, ValueError) as e:
            self.store.update(item_id, {"fill_error": str(e)[:200]})
            return                           # stays pending: tried again at the next start
        fresh = new_item(item_type, info)
        patch = {k: fresh[k] for k in ("type", "format", "source", "source_id", *METADATA_FIELDS) if k in fresh}
        patch.update(pending=False, fill_error="", updated=time.time())
        try:
            self.store.update(item_id, patch)
        except KeyError:
            pass                             # removed meanwhile

    def fill_pending(self):
        """At start: titles whose info wasn't fetched before the app closed."""
        for item in self.store.list():
            if item.get("pending"):
                self.fill_later(item["id"])

    # ---- sharing collections

    def share_collection(self, col_id):
        col = next((c for c in self.store.collections() if c["id"] == col_id), None)
        if not col:
            raise ApiError("collection not found", HTTPStatus.NOT_FOUND)
        items = [i for i in self.store.list() if col_id in i["collections"]]
        if not items:
            raise ApiError("this collection is empty")
        return share.pack(col["name"], items, self.settings.get()["party_name"])

    def read_shared(self, body):
        """A shared collection from a pasted code or a chosen file, each title marked
        with whether it's in your list already. One that stays up to date (a WLC3 link, or a
        file holding one) is read as it is now; "live" is its key, followed once it's added."""
        text = str(body.get("code") or "")
        path = str(body.get("file") or "")
        if path:
            try:
                if os.path.getsize(path) > 2_000_000:
                    raise ApiError("that file is too big to be a shared collection")
                text = Path(path).read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                raise ApiError(f"couldn't open that file ({e})") from e
        live = None
        if text.strip().startswith("{"):
            try:
                live = livecollections.read_code(str(json.loads(text).get("live") or ""))
            except (ValueError, AttributeError):
                live = None
        else:
            live = livecollections.read_code(text)
        try:
            if live:
                try:
                    version = livecollections.fetch(live)
                    data = dict(share.from_data(version["data"]), at=version.get("at") or 0)
                except livecollections.LiveError as e:
                    if not text.strip().startswith("{"):
                        raise ApiError(str(e)) from e
                    data = dict(share.read(text), at=0)         # (the file's titles, as it was saved)
                data["live"] = livecollections.key_text(live)
            else:
                data = share.read(text)
        except share.ShareError as e:
            raise ApiError(str(e)) from e
        for r in data["items"]:
            mine = self.store.find_source(r["source"], r["source_id"])
            r["in_list"] = mine["id"] if mine else None
        return data

    def import_shared(self, body):
        """Add a shared collection (under its own name, or with "(shared)" after it).
        Its titles you don't have are added straight away (their info fills in in
        the background); with `add_to_list` they're in your everyday rows too,
        otherwise only in the collection."""
        data = self.read_shared(body)
        name = str(body.get("name") or data["name"]).strip() or "Shared collection"
        existing = {c["name"].lower() for c in self.store.collections() if not c.get("type")}
        final, n = name, 1
        while final.lower() in existing:
            n += 1
            final = f"{name} (shared)" if n == 2 else f"{name} (shared {n - 1})"
        col = self.store.add_collection(final)
        listed = bool(body.get("add_to_list"))
        if data.get("live"):
            # Kept up to date: what's added to it (or taken out) later comes here too.
            col = self.store.update_collection(col["id"], {
                "follow_key": data["live"], "follow_at": data.get("at") or 0, "follow_listed": listed,
                "follow_by": data.get("by") or "", "follow_checked": time.time(),
                "follow_ids": [[r["source"], r["source_id"]] for r in data["items"]]})
        ok = 0
        for r in data["items"]:
            try:
                self.add_title(r["type"], r["source"], r["source_id"], [col["id"]], hint=r, listed=listed)
                ok += 1
            except (metadata.MetadataError, ApiError, ValueError, KeyError):
                pass
        return {"collection": col, "titles": ok, "new": sum(1 for r in data["items"] if not r["in_list"]),
                "failed": len(data["items"]) - ok, "listed": listed, "live": bool(data.get("live"))}

    # ---- shared collections that stay up to date (livecollections.py)

    LIVE_REPOST = 6 * 3600       # the sharer's app puts its collection back this often (relays forget)
    LIVE_CHECK = 30 * 60         # an app that has someone's collection looks for changes this often

    def _collection(self, col_id):
        col = next((c for c in self.store.collections() if c["id"] == col_id), None)
        if not col:
            raise ApiError("collection not found", HTTPStatus.NOT_FOUND)
        return col

    def live_share(self, col_id):
        """The collection's link that never changes (made the first time), put on the relays now."""
        self.share_collection(col_id)                 # (an empty one can't be shared)
        col = self._collection(col_id)
        if not col.get("share_key"):
            col = self.store.update_collection(col_id, {"share_key": livecollections.key_text(livecollections.new_key())})
        self._live_publish(col_id, True)              # (the link works as soon as it's copied)
        return livecollections.to_code(livecollections.key_bytes(col["share_key"]))

    def live_stop(self, col_id):
        """Stop sharing it: the link stops working, and those who have it keep what they have."""
        col = self._collection(col_id)
        if col.get("share_key"):
            key = livecollections.key_bytes(col["share_key"])
            gone = {"v": 1, "name": col["name"], "by": "", "items": [], "stopped": True}
            threading.Thread(target=livecollections.publish, args=(key, gone), daemon=True).start()
        self._live_sent.pop(col_id, None)
        return self.store.update_collection(col_id, {"share_key": ""})

    def _live_publish(self, col_id, force=False):
        """Your shared collection on the relays: when it changed, or once in a while (they forget)."""
        with self._live_lock:
            col = next((c for c in self.store.collections() if c["id"] == col_id), None)
            if not col or not col.get("share_key"):
                return 0
            items = [i for i in self.store.list() if col_id in i["collections"]]
            data = share.pack(col["name"], items, self.settings.get()["party_name"])
            sig = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
            last = self._live_sent.get(col_id)
            if not force and last and last[0] == sig and time.time() - last[1] < self.LIVE_REPOST:
                return 0
            n = livecollections.publish(livecollections.key_bytes(col["share_key"]), data)
            if n:
                self._live_sent[col_id] = (sig, time.time())
            self._live_status[col_id] = {"relays": n, "at": time.time()}
            return n

    def live_follow(self, col_id, version=None):
        """A collection added from someone's link: what they added to it since is added here
        (in your list too if you chose that), what they took out leaves it. Titles you took
        out of it yourself stay out."""
        col = self._collection(col_id)
        if not col.get("follow_key"):
            raise ApiError("this collection isn't one that's kept up to date")
        if version is None:
            try:
                version = livecollections.fetch(livecollections.key_bytes(col["follow_key"]))
            except livecollections.LiveError as e:
                self.store.update_collection(col_id, {"follow_checked": time.time()})
                raise ApiError(str(e)) from e
        if (version.get("at") or 0) <= (col.get("follow_at") or 0):
            self.store.update_collection(col_id, {"follow_checked": time.time()})
            return {"added": 0, "removed": 0, "stopped": False}
        if version["data"].get("stopped"):
            self.store.update_collection(col_id, {"follow_key": "", "follow_stopped": True, "follow_checked": time.time()})
            return {"added": 0, "removed": 0, "stopped": True}
        try:
            rows = share.from_data(version["data"])["items"]
        except share.ShareError as e:
            raise ApiError(str(e)) from e
        wanted = {(r["source"], r["source_id"]): r for r in rows}
        before = {tuple(x) for x in col.get("follow_ids") or []}
        added = removed = 0
        for k in [k for k in wanted if k not in before]:
            mine = self.store.find_source(*k)
            if mine and col_id in mine["collections"]:
                continue
            try:
                if mine:
                    self.store.set_item_collections(mine["id"], mine["collections"] + [col_id])
                else:
                    r = wanted[k]
                    self.add_title(r["type"], r["source"], r["source_id"], [col_id], hint=r,
                                   listed=bool(col.get("follow_listed")))
                added += 1
            except (metadata.MetadataError, ApiError, ValueError, KeyError):
                pass
        for k in before - set(wanted):
            mine = self.store.find_source(*k)
            if not mine or col_id not in mine["collections"]:
                continue
            rest = [c for c in mine["collections"] if c != col_id]
            if not rest and mine.get("listed") is False and not mine.get("last_played"):
                self.store.delete(mine["id"])          # (only ever in their collection, never watched)
            else:
                self.store.set_item_collections(mine["id"], rest)
            removed += 1
        self.store.update_collection(col_id, {"follow_at": version.get("at") or 0, "follow_checked": time.time(),
                                              "follow_ids": [list(k) for k in wanted]})
        return {"added": added, "removed": removed, "stopped": False}

    def _live_loop(self):
        checked = 0.0
        while True:
            try:
                cols = self.store.collections()
                for col in cols:
                    if col.get("share_key"):
                        self._live_publish(col["id"])
                if time.time() - checked >= self.LIVE_CHECK:
                    checked = time.time()
                    for col in cols:
                        if col.get("follow_key"):
                            try:
                                self.live_follow(col["id"])
                            except ApiError:
                                pass             # (the relays didn't have it just now: next time)
            except Exception:  # noqa: BLE001 - never the app's end
                pass
            time.sleep(60)

    def use_mal(self, item, mal_id):
        """Switch a title whose info came from TMDB to its MyAnimeList entry (anime
        always should), keeping its files, progress and collections. If that entry
        is in your list already, the two become one."""
        s = self.settings.get()
        if not s["mal_client_id"]:
            raise ApiError("add your MyAnimeList Client ID in Settings first")
        info = metadata.details(s, "anime", "mal", str(mal_id))
        counts = {x["number"]: x["episodes"] for x in item.get("seasons") or [] if x["number"] > 0}

        def flat(season, episode):         # TMDB's "S3 E10" is MyAnimeList's episode 10 + seasons 1-2
            if not season or not episode:
                return season, episode
            return 1, sum(n for k, n in counts.items() if k < season) + episode

        p = item["progress"]
        _, ep = flat(p["season"], p["episode"])
        total = info.get("episodes_total")
        progress = {"season": 1, "episode": min(ep, total) if total else ep}
        positions = {}
        for key, value in (item.get("positions") or {}).items():
            a, _, b = key.partition(":")
            positions[f"1:{flat(int(a), int(b))[1]}" if a.isdigit() and b.isdigit() else key] = value
        existing = self.store.find_source("mal", str(mal_id))
        if existing and existing["id"] != item["id"]:
            self.library.reassign(item["id"], existing["id"], flat)
            patch = {"collections": list(dict.fromkeys(existing["collections"] + item["collections"])),
                     "positions": {**positions, **existing.get("positions", {})}}
            if existing["progress"] == {"season": 1, "episode": 1}:
                patch["progress"] = progress
            self.store.update(existing["id"], patch)
            self.store.delete(item["id"])
            return self.store.get(existing["id"])
        fresh = new_item("anime", info)
        patch = {k: fresh[k] for k in METADATA_FIELDS if k in fresh}
        patch.update(type="anime", source="mal", source_id=str(mal_id), progress=progress,
                     positions=positions, updated=time.time())
        self.library.reassign(item["id"], item["id"], flat)
        return self.store.update(item["id"], patch)

    # ---- watching together

    def _party_server(self):
        return self.settings.get()["party_server"] or syncplay.DEFAULT_SERVER

    def party_guest_of(self):
        """The host's name, when you're a guest in someone's watch party; else ""."""
        with self._party_lock:
            party = self.party
        if party and party.active and party.role == "guest":
            return party.leader_name() or "the host"
        return ""

    def _party_name(self):
        return self.settings.get()["party_name"] or syncplay.default_name()

    def _need_mpv(self):
        if launcher.ANDROID or self._web_device():
            return None                      # the phone's own player (phone_player.py, webplay.py)
        mpv = player.find_mpv()
        if not mpv:
            raise ApiError("watching together needs the built-in player, which is missing")
        return mpv

    def _party_ended(self, party, reason):
        for part in getattr(party, "backend", None) or []:
            try:
                part.close() if hasattr(part, "close") else part.stop()
            except Exception:  # noqa: BLE001
                pass
        with self._party_lock:
            if self.party is party:
                self.party = None
                self.party_last = {"ended": reason, "at": time.time(), "room": party.room}

    def _party_role(self, party):
        """You became a guest (someone else is the host now): you follow them, as a guest does."""
        if party.role == "guest":
            party.resolve, party.open_player, party.open_link = self.party_resolve, self._party_open, self.open_link

    def party_make_host(self, name):
        with self._party_lock:
            party = self.party
        if not party or not party.active:
            raise ApiError("there's no watch party on")
        try:
            party.make_host(name)
        except party_mod.PartyError as e:
            raise ApiError(str(e)) from e
        return self.party_status()

    def _start_party(self, party):
        party.on_role = self._party_role
        party.on_server = self._party_server
        with self._party_lock:
            old, self.party = self.party, party
        if old and old.active:
            old.leave("you started another watch party")

    def _web_launch(self, res, out):
        """A phone's player opened for the party: what the phone needs to show it."""
        if res.get("player") == "web":
            out.update({k: res[k] for k in ("player", "session", "entries", "index", "start", "paused", "item_id",
                                            "title", "anime", "party") if k in res})

    def party_start(self, item=None, season=None, episode=None, path="", source="", site="", how=""):
        """Host: open a private room and hand back an invite. The party isn't tied
        to anything playing: whatever you play while it's on, everyone follows.
        With a title, its episode starts playing for the party too.

        Where the party is: your server (Settings), else another of Syncplay's that works,
        else - by itself - the backup relay (relay.py). If that fails too, you choose
        (`how`): "direct" (your app is the server, guests come straight to your computer)."""
        self._need_mpv()
        with self._party_lock:
            current = self.party
        self.party_device = self._web_device()       # (a phone hosting: its own player plays for everyone)
        note = ""
        if current and current.active and current.role == "host":
            party = current                  # already hosting: just play this in it
        elif how == "direct":
            party, note = self._party_direct()
        else:
            party, note = self._party_somewhere()
        out = {"ok": True, "code": party.invite, "room": party.room, "server": party.server, "file": "",
               "note": note, "via": getattr(party, "via", "syncplay")}
        if item is not None and source == "online":
            # Watch online (your own site): found there and played for everyone; the invite
            # then carries the site and the title, so guests find the same episode.
            res = self.play_scraped(item, {"site": site, "season": season, "episode": episode})
            if not res.get("ok"):
                out.update(need_file=True, item_id=item["id"],
                           message=f"The party is on, but it couldn't be played from {res.get('site') or 'your site'}: {res.get('error')}")
                return out
            out.update(file=res.get("file") or "", code=party.invite)
            self._web_launch(res, out)
            threading.Thread(target=self._party_hello, args=(party,), daemon=True).start()
            return out
        if item is not None:
            if path and not self.playable(item, path):
                raise ApiError("that file isn't in this title's folder or your downloads")
            info = self.local_info(item, season or 1, episode or 1, path)
            target = info["target"]
            if not target:
                what = f"{item['title']} S{season} E{episode}" if item["format"] == "series" else item["title"]
                out.update(need_file=True, item_id=item["id"],
                           message=f"The party is on, but you don't have {what} in your downloads or a torrent for it.")
                return out
            subs = info["subtitles"]
            if target.get("stream"):
                subs = self._ready_stream(target) or subs
            res = self.play(item, {"path": target["path"], "subtitle": subs[0]["path"] if subs else None,
                                   "season": season, "episode": episode, "paused": True})
            if res.get("player") == "android" and phone_player.current():
                phone_player.current().open()     # (on a computer, play() opened the player itself)
            self._web_launch(res, out)
            out["file"] = target["name"]
            threading.Thread(target=self._party_hello, args=(party,), daemon=True).start()
        return out

    def _party_host(self, server, room):
        party = party_mod.Party(server, room, self._party_name(), "host", context=metadata.ssl_context(),
                                on_end=self._party_ended, avatar=self.settings.get()["party_avatar"])
        party.connect()
        return party

    def _party_somewhere(self):
        """A party on Syncplay's servers (yours first, then any public one that works, kept in
        Settings), else through the backup relay. ApiError (code "no_server") if nothing works."""
        room = syncplay.new_room()
        chosen = self._party_server()
        servers = [chosen]
        # (yours first; if it doesn't work, the others are checked all at once - quickly - and
        # the first that works is used)
        others = [x for x in syncplay.PUBLIC_SERVERS if x != chosen]

        def more():
            found = syncplay.working_server(others, stay=1.5) if others else ""
            return [found] if found else []
        for server in self._servers_to_try(servers, more):
            try:
                party = self._party_host(server, room)
            except party_mod.PartyError:
                continue
            if server != chosen and server in syncplay.PUBLIC_SERVERS:
                try:
                    self.settings.update({"party_server": server})
                except ValueError:
                    pass
            party.invite = syncplay.short_invite(party.server, room) or syncplay.make_invite(party.server, room, host=party.me)
            party.via = "syncplay"
            self._start_party(party)
            return party, ""
        # Syncplay's servers are all down: the backup relay, by itself.
        own = partyserver.PartyServer()
        port = own.listen(0, "127.0.0.1")
        try:
            hub, secret, index = relay.host(own)
            party = self._party_host(f"127.0.0.1:{port}", relay.ROOM)
        except (OSError, party_mod.PartyError) as e:
            own.stop()
            raise ApiError(f"none of the watch-party servers works right now, nor the backup relay ({e})",
                           HTTPStatus.BAD_GATEWAY, code="no_server") from e
        party.invite = syncplay.relay_invite(secret, index)
        party.via, party.backend = "relay", [hub, own]
        self._start_party(party)
        return party, "Syncplay's servers are down right now, so this party goes through the backup relay (encrypted)."

    @staticmethod
    def _servers_to_try(first, more):
        """`first`, then (only once those failed) what more() finds."""
        yield from first
        yield from more()

    def _party_direct(self):
        """Your app as the server: guests come straight to your computer. Your router is asked
        to let them in (UPnP); if it doesn't, it works only for people on your network.
        ApiError straight away if it can't work at all."""
        own = partyserver.PartyServer()
        port = own.listen(8999 if not launcher.ANDROID else 0)
        party = None
        try:
            party = self._party_host(f"127.0.0.1:{port}", syncplay.new_room())
        except party_mod.PartyError as e:
            own.stop()
            raise ApiError(f"your app couldn't run the party itself ({e})", HTTPStatus.BAD_GATEWAY) from e
        opened = partyserver.open_port(port)
        outside = partyserver.public_address() if opened else ""
        local = partyserver.local_address()
        if opened and outside:
            address, note = f"{outside}:{port}", (f"Your app is the server: your router let it open port {port}, "
                                                  "so people can join from anywhere.")
        elif local and not local.startswith("127."):
            address, note = f"{local}:{port}", ("Your app is the server, but your router didn't let it open a port: only "
                                                "people on your network (the same Wi-Fi) can join. For others, set up your "
                                                "own Syncplay server.")
        else:
            party.leave("no network")
            own.stop()
            raise ApiError("your app can't be the server here: this device isn't on a network others can reach",
                           HTTPStatus.BAD_GATEWAY)
        party.invite = syncplay.short_invite(address, party.room)
        party.via, party.backend = "direct", [own]
        self._start_party(party)
        return party, note

    @staticmethod
    def _party_hello(party):
        for _ in range(40):
            if party.session and party.session.connected:
                time.sleep(1.0)
                party.osd("Watch party on - send the invite. Press Space when you're all here.", 8)
                return
            time.sleep(0.25)

    def party_join(self, code):
        """Guest: join the room. What the host plays is followed from then on (in
        the background): your own copy, or the host's torrent made ready first."""
        self._need_mpv()
        self.party_device = self._web_device()       # (a phone joining: what the host plays opens on it)
        invite = syncplay.read_invite(code)
        party = party_mod.Party(invite["server"], invite["room"], self._party_name(), "guest",
                                resolve=self.party_resolve, leader=invite.get("host"),
                                context=metadata.ssl_context(), on_end=self._party_ended,
                                open_player=self._party_open, invite=code,
                                avatar=self.settings.get()["party_avatar"], open_link=self.open_link)
        if invite.get("now"):
            # The host's torrent comes with the invite: start getting it now, while joining.
            threading.Thread(target=self._party_prefetch, args=(invite["now"],), daemon=True).start()
        try:
            party.connect()
        except party_mod.PartyError as e:
            raise ApiError(str(e)) from e
        if invite.get("host") and invite["host"] not in party.people:
            party.leave("the host isn't there")
            raise ApiError(f'{invite["host"]} isn\'t in that watch party any more - ask for a new invite')
        if not any(n != party.me for n in party.people):
            party.leave("nobody's there")
            raise ApiError("nobody is in that watch party - check the code, or ask for it again")
        self._start_party(party)
        party._follow_leader()
        return {"ok": True, "leader": party.leader_name(), "invite": invite}

    def _party_now(self, party, entry, upcoming=False):
        """The host plays (or is about to play) `entry`: the invite carries it, and
        with `upcoming` everyone starts getting it ready now."""
        meta = dict(entry.get("party") or {}, label=entry.get("label") or "")
        if upcoming:
            party.announce_upcoming(meta, meta["label"])

    def party_upcoming(self, item, season, episode, path):
        """The host is getting a torrent episode ready: the party starts on it too."""
        with self._party_lock:
            party = self.party
        if not party or not party.active or party.role != "host":
            return
        series = item["format"] == "series"
        entries, _ = self._entries(item, path, None, season if series else None, episode if series else None, False)
        self._party_now(party, entries[0], upcoming=True)

    def _party_prefetch(self, now):
        """A guest joining: the host's torrent episode added and started at once, or
        the host's episode found on the same video site."""
        try:
            item = self._party_item(now.get("src"), now.get("id"), now.get("title"), now.get("type"))
            if item and syncplay.online_source(now) and not now.get("magnet"):
                self._party_online_entry(item, now, lambda *a, **k: None)
            elif item:
                self._party_torrent(item, now)
        except (ApiError, torrents_mod.TorrentError, metadata.MetadataError, KeyError, ValueError):
            pass

    def _party_torrent(self, item, meta):
        """The host's torrent's exact file: added (if it isn't), its list of files
        fetched, connected to the host, and that one file wanted first. Its stream link."""
        magnet, index = meta.get("magnet") or "", meta.get("ti")
        if not magnet or not isinstance(index, int):
            return None
        h = self.torrents.hash_of_magnet(magnet)
        try:
            self.torrents.add(item["id"], magnet)
        except torrents_mod.TorrentError:
            if h not in self.torrents.records:
                raise                        # (already there for another title: used as it is)
        # The host's magnet replaces a different one attached to this title here.
        self.torrents.replace_for(item["id"], h)
        if meta.get("peer"):
            self.torrents.add_peer(h, meta["peer"])
        path, _size = self.torrents.file_in(h, index)
        if meta.get("peer"):
            self.torrents.add_peer(h, meta["peer"])       # (again: now it knows what to ask for)
        self.torrents.prepare(h, index)
        return self.torrents.stream_url(h, index, path)

    def _party_playlist(self, item, entry):
        """A guest's playlist, as the host's: the whole season around the episode (from the
        same site, the same torrent or your own files), so Next and Previous play straight away."""
        if item["format"] != "series":
            return [entry], 0
        if entry.get("web"):
            site = next((x for x in self._video_sites(item) if x["id"] == entry.get("site")), None)
            return self._online_playlist(item, site, entry) if site else ([entry], 0)
        season, episode = self._key_numbers(entry.get("key"))
        if season is None:
            return [entry], 0
        try:
            entries, patch = self._entries(item, entry["path"], entry.get("sub"), season, episode, True, around=True)
        except Exception:  # noqa: BLE001 - the episode on its own, then
            return [entry], 0
        first = patch.get("first", 0)
        entries[first] = entry
        return entries, first

    def _party_open(self, party, entry):
        """A guest's player, opened for what the host plays (paused until it's in step)."""
        item = self.store.get(entry["item_id"])
        entries, first = self._party_playlist(item, entry)
        device = self.party_device
        if device and self.remote.devices.check_id(device["id"]):
            # A phone in the party: what the host plays opens in the phone's own player.
            session = self.web_session(device, item, entries, 0, first, paused=True)
            session.party = True
            party.attach(session)
            return
        if launcher.ANDROID:
            session = phone_player.PhoneSession(item["id"], entries, 0, paused=True, keep_open=True,
                                                anime=item["type"] == "anime", index=first)
            session.party = True
            party.attach(session)
            session.open()
            return
        session = self.player.play(self._session(self._need_mpv(), item, entries, 0,
                                                 ["--pause", "--keep-open=yes"], first=first,
                                                 playlist=any(e.get("online") for e in entries)))
        party.attach(session)
        if entry.get("web"):
            # From a video site: as for your own (its subtitles, where it's from, Sub / Dub).
            session.listeners.append(lambda sess, msg: self._online_event(sess, item["id"], msg))
            self._online_extras(session, item["id"], entry)

    def party_use_file(self, path):
        """A guest choosing their own file for what the host is playing."""
        with self._party_lock:
            party = self.party
        if not party or not party.active or party.role != "guest":
            raise ApiError("you aren't in someone's watch party")
        if not os.path.isfile(path) or Path(path).suffix.lower() not in scanner.VIDEO_EXTS:
            raise ApiError("choose a video file")
        meta = (party.leader_file() or {}).get("watchlist") or {}
        item = self._party_item(meta.get("src"), meta.get("id"), meta.get("title"), meta.get("type"))
        if not item:
            raise ApiError("the host isn't playing anything right now")
        series = item["format"] == "series"
        entries, _ = self._entries(item, path, None, meta.get("season") if series else None,
                                   meta.get("episode") if series else None, False)
        entry = dict(entries[0], item_id=item["id"])
        threading.Thread(target=party.use, args=(entry,), daemon=True).start()
        return {"ok": True, "file": Path(path).name}

    def _party_item(self, source, source_id, title, item_type):
        item = self.store.find_source(source, source_id) if source else None
        if not item and title:
            want = library.norm(title)
            item = next((i for i in self.store.list() if want in {n for n, _ in library.item_names(dict(i))}
                         and item_type in ("", None, i["type"])), None)
        if not item and source in ("tmdb", "mal") and item_type in TYPES:
            try:
                item = self.add_title(item_type, source, source_id)
            except (metadata.MetadataError, ApiError):
                item = None
        return item

    def _ready_stream(self, target):
        """Get a torrent's episode ready to stream; its subtitles, if it has any."""
        found = self.torrents.parse_url(target["path"])
        if not found:
            return []
        try:
            self.torrents.prepare(*found)
            return [{"path": p, "name": Path(p).name} for p in self.torrents.subtitles(*found, wait=10)]
        except torrents_mod.TorrentError as e:
            raise ApiError(str(e)) from e

    def _party_entry(self, item, season, episode, magnet=""):
        info = self.local_info(item, season, episode)
        target = info["target"]
        subs = info["subtitles"]
        if not target:
            target = self.library.unsorted_match(item, season, episode)
            if target:
                subs = self.library.subtitles_for(target["path"])
        if not target and magnet and torrents_mod.available():
            # The host streams it from a torrent: stream the same one.
            try:
                self.torrents.add(item["id"], magnet)
                h = self.torrents.hash_of_magnet(magnet)
                self.torrents.wait_for_files(h)
                self.torrents.wait_for_plan(h)
            except torrents_mod.TorrentError:
                return None
            info = self.local_info(item, season, episode)
            target, subs = info["target"], info["subtitles"]
        if not target:
            return None
        if target.get("stream"):
            try:
                subs = self._ready_stream(target) or subs
            except ApiError:
                return None
        entries, _ = self._entries(item, target["path"], subs[0]["path"] if subs else None,
                                   season if item["format"] == "series" else None, episode, False)
        entry = entries[0]
        entry["item_id"] = item["id"]
        return entry

    def _party_torrent_entry(self, item, meta):
        """What to play for the host's torrent episode, by its exact file (without
        waiting for the whole torrent to be worked out). None if that can't be done."""
        try:
            url = self._party_torrent(item, meta)
        except (torrents_mod.TorrentError, ApiError):
            return None
        if not url:
            return None
        h, index = self.torrents.parse_url(url)
        series = item["format"] == "series"
        subs = []
        try:
            subs = self.torrents.subtitles(h, index, wait=8)
        except torrents_mod.TorrentError:
            pass
        entries, _ = self._entries(item, url, subs[0] if subs else None,
                                   (meta.get("season") or 1) if series else None, (meta.get("episode") or 1) if series else None, False)
        entry = entries[0]
        entry.update(item_id=item["id"], torrent={"hash": h, "index": index, "complete": False})
        return entry

    def party_resolve(self, file_, report=None, at=0.0):
        """This computer's copy of what someone in the party is playing, or None:
        a file you have, or the same torrent, got ready to stream (from where
        the room is) before the player opens. Watch Anything players say which
        title and episode it is; for others (the Syncplay app) it's worked out
        from the file's name. report(status, progress, eta, label) follows along."""
        report = report or (lambda *a, **k: None)
        meta = (file_ or {}).get("watchlist") or {}
        label = meta.get("label") or (file_ or {}).get("name") or ""
        entry = None
        if meta.get("src") or meta.get("title"):
            item = self._party_item(meta.get("src"), meta.get("id"), meta.get("title"), meta.get("type"))
            if not item:
                return None
            have = self.local_info(item, meta.get("season") or 1, meta.get("episode") or 1)["target"]
            if meta.get("magnet") and not (have and not have.get("stream")):
                report("preparing", 0, None, label)       # the host's torrent: its exact file, straight away
                entry = self._party_torrent_entry(item, meta)
            if not entry:
                entry = self._party_entry(item, meta.get("season") or 1, meta.get("episode") or 1, meta.get("magnet") or "")
            if not entry and syncplay.online_source(meta):
                entry = self._party_online_entry(item, meta, report)
            if not entry and meta.get("url"):
                entry = self._party_url_entry(item, meta)
        else:
            name = (file_ or {}).get("name") or ""
            if not name:
                return None
            g = library.guess_full(Path(name).parts)
            season, episodes = scanner.parse_episode(Path(name).parts)
            best = None
            for item in self.store.list():
                score = library.score_item(dict(item), g["title"], g["title_season"], season, g["year"], bool(episodes))
                if score >= 85 and (not best or score > best[0]):
                    best = (score, item)
            if best:
                entry = self._party_entry(best[1], season or 1, episodes[0] if episodes else 1)
        if entry and entry.get("torrent") and not entry["torrent"].get("complete"):
            self._party_buffer(entry, report, label, at)
        return entry

    def _party_buffer(self, entry, report, label, at=0.0, wait=600):
        """A guest's torrent episode: the part the room is at, downloaded first,
        with its progress told to the room."""
        found = self.torrents.parse_url(entry["path"])
        if not found:
            return
        deadline = time.time() + wait
        rates = []
        while time.time() < deadline:
            try:
                st = self.torrents.buffer(*found, at=max(0.0, min(float(at or 0), 0.97)))
            except torrents_mod.TorrentError:
                return
            rates = (rates + [st["rate"]])[-6:]
            rate = sum(rates) / len(rates)
            left = max(0, st["need"] - st["have"])
            eta = round(left / rate) if rate > 1024 else None
            report("preparing", round(st["have"] / st["need"], 3) if st["need"] else 0, eta, label)
            if st["ready"]:
                return
            time.sleep(0.7)

    # ---- which file each episode plays from

    def connections(self, item):
        """Every episode of a title and the file it plays from (downloads,
        the title's own folder, torrents), and the title's other files."""
        videos, _subs = self._sources(item)
        series = item["format"] == "series"
        main = [v for v in videos if v.get("kind") != "extra"]
        emap = scanner.episode_map(item, main) if series else {}
        playing = {f"{s}:{e}": v["path"] for (s, e), v in emap.items()}
        keys_of = {}
        for key, path in playing.items():
            keys_of.setdefault(path, []).append(key)
        files = []
        for v in videos:
            source = "torrent" if v.get("torrent") else "download" if v.get("dl") else "folder"
            files.append({"path": v["path"], "name": v["name"], "source": source, "kind": v.get("kind") or "",
                          "size": v.get("size") or 0, "keys": keys_of.get(v["path"], []),
                          "torrent": v.get("torrent"), "rel": v.get("rel") if source == "download" else "",
                          "how": v.get("how") or ""})
        episodes = []
        if series:
            for season in item["seasons"]:
                for n in range(1, (season.get("episodes") or 0) + 1):
                    key = f"{season['number']}:{n}"
                    episodes.append({"key": key, "season": season["number"], "episode": n, "path": playing.get(key)})
            listed = {e["key"] for e in episodes}
            for key in sorted(set(playing) - listed, key=lambda k: tuple(int(x) for x in k.split(":"))):
                s_, e_ = (int(x) for x in key.split(":"))
                episodes.append({"key": key, "season": s_, "episode": e_, "path": playing[key]})
        else:
            target = self.local_info(item, None, None)["target"]
            episodes.append({"key": "movie", "path": target["path"] if target else None})
        return {"series": series, "episodes": episodes, "files": files}

    def connect_file(self, item, path, key):
        """You say which episode a file is ("s:e"), that it isn't one of this
        title's ("none"), or that the app should work it out ("auto")."""
        found = self.torrents.find_file(path)
        if found and self.torrents.owns(item["id"], path):
            value = None if key == "auto" else key
            try:
                self.torrents.set_episode(found[0], found[1], value, item["id"])
            except torrents_mod.TorrentError as e:
                raise ApiError(str(e)) from e
            return
        rel = next((v["rel"] for v in self.library.videos_for(item["id"]) if v["path"] == path), None)
        if rel is None:
            raise ApiError("files in this title's own folder go by their names - rename the file, "
                           "or pick it with Choose file")
        try:
            if key == "none":
                self.library.route(rel, "unsorted")
            elif key == "auto":
                self.library.route(rel, item["id"])
            elif key == "movie":
                self.library.route(rel, item["id"], kind="movie")
            else:
                season, episode = (int(x) for x in key.split(":"))
                self.library.route(rel, item["id"], season, episode)
        except ValueError as e:
            raise ApiError(str(e)) from e

    def sort_again(self, item):
        """Work out a title's files again from scratch: its torrents (with the AI,
        if there's a key) and its downloads (by Sort now)."""
        redone = 0
        for h, rec in list(self.torrents.records.items()):
            if rec["item_id"] == item["id"] or self.torrents._owns_any(rec, item["id"]):
                for f in rec.get("files") or []:
                    owner = self.torrents._owner(rec, f)[0]
                    if owner in (item["id"], None):
                        rec["overrides"].pop(str(f["index"]), None)
                if rec["item_id"] == item["id"]:
                    self.torrents.make_plan(h)
                redone += 1
        files = self.library.sort_again(item["id"])
        if files:
            threading.Thread(target=self.sorter.run_now, kwargs={"with_ai": bool(ai.available(self.settings.get()))},
                             daemon=True).start()
        return {"torrents": redone, "files": files}

    def party_status(self):
        with self._party_lock:
            party = self.party
        if party:
            return party.status()
        return {"active": False, **(self.party_last or {})}

    def party_leave(self):
        with self._party_lock:
            party = self.party
        if party:
            party.leave("you left the watch party")
        return self.party_status()

    def undo_sorted(self, key):
        for entry in self.library.undo(key):
            if entry.get("added") and not self.library.uses(entry["item"]):
                try:
                    self.store.delete(entry["item"])
                except KeyError:
                    pass
        self.library.rematch()

    # ---- everything connected to a title

    def cancel_connected(self, item):
        job = self._connected_jobs.get(f'connected:{item["source"]}:{item["source_id"]}')
        if job and job["running"]:
            job.update(cancel=True, stage="Stopping…")
        return {"ok": True}

    def connected(self, item, run=False, wait=False):
        """Everything connected to a title (seasons, films, OVAs, ONAs, specials,
        sequels, prequels, spin-offs...), each marked as on your computer, in your
        list, waiting in your downloads, or missing. Looked for when you ask
        (`run`); otherwise what was found last time, or {"entries": None}."""
        cache_key = f'connected:{item["source"]}:{item["source_id"]}'
        if run:
            job = self._connected_jobs.get(cache_key)
            if not job or not job["running"]:
                job = {"running": True, "stage": "Starting…", "progress": 0.02, "found": 0, "guide": False}
                self._connected_jobs[cache_key] = job
                thread = threading.Thread(target=self._connected_job, args=(item, cache_key, job), daemon=True)
                thread.start()
                if wait:
                    thread.join(300)
        job = self._connected_jobs.get(cache_key)
        with self._related_lock:
            cached = self._related.get(cache_key)
        job_view = {k: v for k, v in job.items()} if job and (job["running"] or time.time() - job.get("ended", 0) < 5) else None
        if not cached:
            return {"entries": None, "ai": [ai.NAMES[p] for p in ai.available(self.settings.get())], "job": job_view}
        lib = self.library.state()
        waiting = [q for q in lib["questions"] if q["kind"] == "title"]
        out = []
        this = {k: item.get(k) for k in ("title", "type", "source", "source_id", "year", "poster", "format",
                                          "media_type", "media_kind", "episodes_total")}
        this.update(self=True, relation="This title", date=item.get("date") or "")
        for e in [this] + cached["entries"]:
            e = dict(e)
            mine = self.store.find_source(e["source"], e["source_id"])
            e["in_list"] = mine["id"] if mine else None
            got = lib["items"].get(mine["id"], {}) if mine else {}
            e["files"] = got.get("files", 0)
            e["episodes_here"] = len(got.get("episodes", []))
            e["episodes_total"] = (mine or {}).get("episodes_total") or e.get("episodes_total")
            e["waiting"] = 0
            if not mine:
                as_item = sorter._as_item(e, e["type"])
                for q in waiting:
                    if library.score_file(as_item, sorter._guess(q), q["file_kind"]) >= 85:
                        e["waiting"] += len(q["files"])
            out.append(e)
        return {"self": out[0], "entries": out[1:], "note": cached.get("note", ""), "at": cached["at"],
                "guide": cached.get("guide"), "ai": [ai.NAMES[p] for p in ai.available(self.settings.get())],
                "job": job_view}

    def _save_connected(self, cache_key, cached):
        with self._related_lock:
            self._related[cache_key] = cached
            for k in sorted(self._related, key=lambda k: self._related[k]["at"])[:-300]:
                del self._related[k]
            write_json(self._related_path, self._related, compact=True)

    def _connected_job(self, item, cache_key, job):
        """Finding everything connected, in the background, saying how far along
        it is: the list is shown as soon as it's found; the story guide follows."""
        try:
            if not item.get("date") and item["source"] in ("tmdb", "mal"):
                job.update(stage=f"Looking up {item['title']}…", progress=0.05)
                try:                                    # its first air date, for the release order
                    item = self.refresh_metadata(item)
                except (metadata.MetadataError, KeyError):
                    pass
            with self._related_lock:
                old = self._related.get(cache_key) or {}

            def publish(found):
                # What's found so far is shown straight away (the AI's finds follow).
                self._save_connected(cache_key, {"at": time.time(), "entries": found, "note": "",
                                                 "guide": None, "partial": True})
            entries, note = self._find_connected(item, job, publish)
            s = self.settings.get()
            if job.get("cancel"):
                if entries:
                    self._save_connected(cache_key, {"at": time.time(), "entries": entries, "guide": None,
                                                     "note": "Stopped before the end - press ↻ to look again."})
                job.update(stage="Cancelled", progress=1.0)
                return
            cached = {"at": time.time(), "entries": entries, "note": note,
                      "guide": old.get("guide") if old.get("entries") == entries else None}
            self._save_connected(cache_key, cached)
            if ai.available(s) and not job.get("cancel"):
                job.update(stage=f"Found {len(entries)} - {ai.NAMES[ai.available(s)[0]]} is writing the story guide…",
                           progress=0.85, guide=True)
                try:
                    cached = dict(cached, guide=self._story_guide(item, [e for e in entries if e.get('group') != 'other'], s))
                except ai.AIError as e:
                    cached = dict(cached, note=(note + " " if note else "") + f"The story guide couldn't be written: {e}")
                if not job.get("cancel"):
                    self._save_connected(cache_key, cached)
            job.update(stage="Done", progress=1.0)
        except Exception as e:  # noqa: BLE001 - shown, rather than a job that never ends
            job.update(stage=f"Something went wrong: {e}", error=str(e))
        finally:
            job.update(running=False, ended=time.time())

    @staticmethod
    def _story_guide(item, entries, s):
        """The AI's guide, keyed by "self" or "source:source_id"."""
        g = ai.story_guide(s, item, entries)
        keys = ["self"] + [f'{e["source"]}:{e["source_id"]}' for e in entries]
        return {"overview": g["overview"], "overview_spoilers": g["overview_spoilers"], "by": g["by"],
                "story_order": [keys[n] for n in g["story_order"] if n < len(keys)],
                "entries": {keys[n]: v for n, v in g["entries"].items() if n < len(keys)}}

    def _find_connected(self, item, job=None, publish=None):
        """Everything connected: "main" (the same story: seasons, sequels, films,
        OVAs, the show it's a spin-off of...) and "other" (other versions, reboots,
        other series of the franchise). What the links find is shown (`publish`)
        while the AI's answer is still coming."""
        s = self.settings.get()
        job = job if job is not None else {}
        entries, notes = [], []
        lock = threading.Lock()

        def add(rows, group=None):
            if job.get("cancel"):
                return
            with lock:
                have = {e["source_id"]: e for e in entries}
                for r in rows:
                    if r["source_id"] == item["source_id"]:
                        continue
                    r = dict(r, group=group or r.get("group") or "main")
                    old = have.get(r["source_id"])
                    if old is None:
                        entries.append(r)
                        have[r["source_id"]] = r
                    elif old["group"] == "other" and r["group"] == "main":
                        old.update(group="main", relation=r.get("relation") or old.get("relation"))
                job["found"] = len(entries)
                snapshot = sorted(entries, key=lambda e: e["group"] != "main")
            if publish:
                publish(snapshot)

        def linked():
            """What MyAnimeList / TMDB link to it directly."""
            if item["source"] == "mal" and s["mal_client_id"]:
                def seen(n):
                    job.update(found=max(job.get("found", 0), n), progress=min(0.5, 0.1 + n * 0.02),
                               stage=f"Following MyAnimeList's links: {n} found…")
                add([_entry(r, "anime") for r in
                     metadata.MAL(s["mal_client_id"]).franchise(item["source_id"], on_progress=seen)])
            elif s["tmdb_api_key"]:
                if item["format"] == "movie" and item.get("tmdb_collection"):
                    add([_entry(r, "movie") for r in metadata.TMDB(s["tmdb_api_key"]).collection(item["tmdb_collection"])])
                job.update(stage="Looking for the show or film it comes from…", progress=max(job.get("progress", 0), 0.2))
                parents = self._mentioned(item, s)
                add(parents)
                # The franchise by name ("Spider-Man" for "Spider-Man: Brand New Day"), and the
                # franchise of the show it comes from: other versions and series.
                job.update(stage="Looking for other versions and series…", progress=max(job.get("progress", 0), 0.3))
                for base in self._franchise_names(item, parents):
                    add(self._related_by_name(item, s, base), "other")

        job.update(stage="Looking for everything connected…", progress=0.08)
        pool = ThreadPoolExecutor(2)
        try:
            links = pool.submit(linked)
            by_ai = pool.submit(self._connected_by_ai, item, s, job) if ai.available(s) else None
            try:
                links.result()
            except metadata.MetadataError as e:
                notes.append(str(e))
            if by_ai is not None:
                if not job.get("cancel"):
                    job.update(stage=f"Found {len(entries)} - {ai.NAMES[ai.available(s)[0]]} is looking for more…",
                               progress=max(job.get("progress", 0), 0.45))
                while not by_ai.done() and not job.get("cancel"):
                    time.sleep(0.2)
                if by_ai.done():
                    try:
                        add(by_ai.result())
                    except ai.AIError as e:
                        notes.append(f"AI helper: {e}")
        finally:
            pool.shutdown(wait=False)       # (a cancelled AI answer is left to finish on its own)
        if not ai.available(s):
            notes.append("With an AI key (Settings) this also finds connected titles that aren't linked directly.")
        return sorted(entries, key=lambda e: e["group"] != "main"), " ".join(notes)

    @staticmethod
    def _franchise_names(item, parents):
        """Names to look for other versions by: the part of the title before a colon
        ("Spider-Man"), and the name of the show or film it comes from."""
        names = []
        for title in [item["title"]] + [p["title"] for p in parents]:
            base = re.split(r"\s*[:\u2013\u2014]\s*|\s+-\s+", title)[0].strip()
            if len(library.norm(base)) >= 4 and base.lower() not in (n.lower() for n in names):
                names.append(base)
        return names[:3]

    def _mentioned(self, item, s):
        """The show or film a spin-off comes from, when its summary names it
        ("...from the world of Rick and Morty"): names in its summary found on TMDB."""
        text = " ".join(x for x in (item.get("overview"), item.get("tagline")) if x)
        if not text:
            return []
        candidates = re.findall(r"[\"\u201c']([^\"\u201d']{3,60})[\"\u201d']", text)
        cap = r"[A-Z0-9][\w'!.\-]*"
        candidates += re.findall(rf"{cap}(?:\s+(?:and|of|the|&|in|on|a|to)?\s*{cap}){{1,5}}", text)
        own = library.norm(item["title"])
        seen, out = set(), []
        for c in candidates:
            key = library.norm(c)
            if len(key) < 5 or key in seen or key == own or key in own:
                continue
            seen.add(key)
            for media in ("tv", "movie"):
                for r in self._search_safely(s, media, c)[:3]:
                    if library.norm(r["title"]) == key and (r.get("votes") or 0) >= 100 \
                            and not (media == "tv" and discover._is_anime(r)):
                        out.append(_entry(dict(r, relation="It comes from this"), media))
                        break
            if len(seen) >= 8:
                break
        return out[:3]

    def _connected_by_ai(self, item, s, job=None):
        """What the AI names, each checked on MyAnimeList (anime) or TMDB first."""
        job = job if job is not None else {}
        named = ai.connected_titles(s, item)
        done = [0]
        job.update(stage=f"Checking the {len(named)} titles the AI named…", progress=max(job.get("progress", 0), 0.5))

        def check(row):
            if item["type"] == "anime" or row["kind"] in ("ova", "ona"):
                kinds = [("anime", "anime")] if s["mal_client_id"] else []
            else:
                kinds = []
            if s["tmdb_api_key"]:
                kinds.append(("movie", "movie") if row["kind"] == "movie" else ("tv", "tv"))
            want = library.norm(row["title"])
            for search_type, item_type in kinds:
                for r in self._search_safely(s, search_type, row["title"]):
                    names = {library.norm(n) for n in (r["title"], r.get("original_title"), *(r.get("aliases") or [])) if n}
                    if want in names and (not row["year"] or not r.get("year") or abs(r["year"] - row["year"]) <= 1):
                        r, item_type = sorter._anime_from_mal(s, r, item_type)
                        return dict(_entry(dict(r, relation=row["relation"]), item_type), group=row.get("group", "main")) if r else None
            return None

        def counted(row):
            try:
                return check(row)
            finally:
                done[0] += 1
                job.update(stage=f"Checking the titles the AI named ({done[0]} of {len(named)})…",
                           progress=max(job.get("progress", 0), 0.5 + 0.3 * done[0] / max(1, len(named))))

        with ThreadPoolExecutor(8) as pool:
            return [r for r in pool.map(counted, named) if r]

    @staticmethod
    def _search_safely(s, item_type, title):
        try:
            return metadata.search(s, item_type, title)[:10]
        except metadata.MetadataError:
            return []

    def _related_by_name(self, item, s, base=None):
        """TV shows and films whose name contains a franchise's (other versions, other series)."""
        name = library.norm(base or item["title"])
        out = []
        if len(name) < 3:
            return out
        for media in ("tv", "movie"):
            for r in self._search_safely(s, media, base or item["title"]):
                other = library.norm(r["title"])
                if other != library.norm(item["title"]) and re.search(rf"(^|\s){re.escape(name)}(\s|$)", other) \
                        and not (media == "tv" and discover._is_anime(r)):
                    out.append(_entry(dict(r, relation="Same franchise"), media))
        return out

    def similar(self, item, limit=5):
        """Related entries beside a title's page: the title's name searched on
        MyAnimeList (anime) or TMDB (films and TV shows, both), and the results
        with a similar name, up to `limit`. Remembered for a few hours."""
        key = (item["id"], item["title"])
        with self._lock:
            hit = self._similar.get(key)
        if hit and time.time() - hit[0] < 6 * 3600:
            return hit[1]
        s = self.settings.get()
        base = library.split_season(library.norm(item["title"]))[0]
        base = re.sub(r"\b(?:" + "|".join(scanner._SEASON_WORDS) + r")\s+season\b|\b\d+(?:st|nd|rd|th)\s+season\b",
                      " ", base)
        base = " ".join(base.split()[:4]) or item["title"]
        kinds = ["anime"] if item["type"] == "anime" else ["tv", "movie"] if item["type"] == "tv" else ["movie", "tv"]
        rows = []

        def one(kind):
            try:
                return [dict(r, type=r.get("type") or kind) for r in metadata.search(s, kind, base)[:12]]
            except metadata.MetadataError:
                return []
        with ThreadPoolExecutor(2) as pool:
            for got in pool.map(one, kinds):
                rows += got
        words = {w for w in base.split() if w not in library._STOP and len(w) > 1}
        out, seen = [], set()
        for r in rows:
            sid = (r.get("source"), str(r.get("source_id")))
            if sid in seen or sid == (item["source"], str(item["source_id"])):
                continue
            theirs = set(library.norm(" ".join(filter(None, [r.get("title"), r.get("original_title")]))).split())
            if words and not (words & theirs):
                continue
            seen.add(sid)
            existing = self.store.find_source(r["source"], r["source_id"])
            out.append({k: r.get(k) for k in ("source", "source_id", "title", "year", "poster", "media_type",
                                              "media_kind", "format", "type", "episodes_total")}
                       | {"in_list": existing["id"] if existing else None})
            if len(out) >= limit:
                break
        with self._lock:
            self._similar[key] = (time.time(), out)
        return out

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
        for v in videos:
            v["dl"] = True            # from the downloads folder: can be moved to another title
        folder_subs, folder_videos = [], []
        folder = item["local_folder"]
        if folder and os.path.isdir(folder):
            found = scanner.scan_folder(folder)
            folder_videos, folder_subs = found["videos"], found["subs"]
            for v in folder_videos:
                library.Library.describe(v)
            known = {v["path"] for v in videos}
            videos += [v for v in folder_videos if v["path"] not in known]
        # Episodes from the title's torrents: finished ones by path, the rest streamed.
        videos += self.torrents.videos(item)
        # Episode order; when there are two copies of an episode, the better one
        # first, and a file on your computer before a torrent's.
        videos.sort(key=lambda v: (v["season"] if v["season"] is not None else 1, v["episodes"][:1] or [0],
                                   bool(v.get("torrent")), (v.get("torrent") or {}).get("rank", 0),
                                   [-x for x in library.quality(v)],
                                   scanner.natural_key(v["rel"])))
        in_folder = {v["path"] for v in folder_videos}

        def subtitles(video):
            if video.get("torrent"):
                t = video["torrent"]
                return [{"path": p, "name": Path(p).name} for p in self.torrents.ready_subtitles(t["hash"], t["index"])]
            if video["path"] in in_folder:
                return scanner.subtitles_for(video, folder_subs, folder_videos)
            return self.library.subtitles_for(video["path"])
        return videos, subtitles

    def local_info(self, item, season, episode, path=""):
        folder = item["local_folder"]
        everything, subtitles = self._sources(item)
        videos = [v for v in everything if v.get("kind") != "extra"]
        info = {"folder": folder, "exists": bool(everything) or (bool(folder) and os.path.isdir(folder)),
                "downloads": len(self.library.videos_for(item["id"])),
                "videos": videos, "extras": [v for v in everything if v.get("kind") == "extra"],
                "target": None, "subtitles": [], "available": []}
        if not info["exists"]:
            return info
        if path:
            target = next((v for v in everything if v["path"] == path), None)
        elif item["format"] == "movie":
            target = next((v for v in videos if v["path"] == item["local_file"]), None)
            target = target or scanner.find_movie(item, videos)
        elif item_kind_is_single(item) and videos and not any(v["episodes"] for v in videos):
            target = videos[0]   # a one-off OVA or special with an unnumbered file
        else:
            episodes = scanner.episode_map(item, videos)
            info["available"] = [f"{s}:{e}" for s, e in sorted(episodes)]
            target = episodes.get((season, episode))
            # Which file each episode plays (files from the downloads folder can be moved).
            in_downloads = {v["path"] for v in self.library.videos_for(item["id"])}
            info["by_episode"] = {f"{s}:{e}": {"name": v["name"], "path": v["path"],
                                               "rel": v["rel"] if v["path"] in in_downloads else None,
                                               "torrent": v.get("torrent"), "stream": bool(v.get("stream"))}
                                  for (s, e), v in episodes.items()}
        info["target"] = target
        if target:
            info["subtitles"] = subtitles(target)
        info["family"] = self._family(item)
        return info

    def _family(self, item):
        """The films, OVAs, specials and extras in the same folder as this title's
        files, grouped: other titles in your list, and loose files."""
        titles, loose = {}, []
        for v in self.library.family(item["id"]):
            other = None
            if v["item"]:
                try:
                    other = self.store.get(v["item"])
                except KeyError:
                    other = None
            if other:
                entry = titles.setdefault(other["id"], {"id": other["id"], "title": other["title"],
                                                        "type": other["type"], "poster": other.get("poster", ""),
                                                        "kind": library.item_kind(other), "files": 0,
                                                        "media_type": other.get("media_type", "")})
                entry["files"] += 1
            else:
                loose.append({"name": v["name"], "path": v["path"], "rel": v["rel"], "kind": v["kind"]})
        return {"titles": sorted(titles.values(), key=lambda t: t["title"]), "files": loose[:60]}

    def playable(self, item, path):
        """Only files from this title's folder, the downloads folder or the title's
        torrents can be played."""
        if self.torrents.owns(item["id"], path):
            return True
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
        if body.get("extra"):
            return self._play_extra(item, path, subtitle, body.get("player") or s["player"])
        found = self.torrents.parse_url(path)
        if found:
            # A torrent's episode: wanted now, and its subtitle fetched first.
            try:
                self.torrents.prepare(*found)
                if not subtitle and body.get("subtitles", True):
                    # (Got ready first: its subtitle came meanwhile, or isn't worth waiting for.)
                    subs = self.torrents.subtitles(*found, wait=3 if found in self.torrents.buffers else 20)
                    subtitle = subs[0] if subs else None
            except torrents_mod.TorrentError as e:
                raise ApiError(str(e)) from e
        entries, patch = self._entries(item, path, subtitle, body.get("season"), body.get("episode"),
                                       s["queue_next_episodes"], around=bool(s["queue_next_episodes"]))
        first = patch.pop("first", 0)
        this = entries[first]
        start = 0 if body.get("from_start") else player.resume_point(item["positions"].get(this["key"]))
        web = self._web_device()
        if web:
            # A phone over the network: its own player plays the list - nothing opens here.
            with self._party_lock:
                hosting = self.party if self.party and self.party.active and self.party.role == "host" else None
            hold = bool(hosting and any(p != hosting.me for p in hosting.people))
            session = self.web_session(web, item, entries, start, first, paused=bool(body.get("paused") or hold))
            if hosting:
                session.party = True
                hosting.attach(session, hold=hold and not body.get("paused"))
                self._party_now(hosting, this)
            self.store.update(item["id"], patch)
            return dict(session.launch(), ok=True, player="web", file=this.get("name") or Path(path).name,
                        queued=len(entries) - 1 - first, resumed_at=start, stream=bool(found))
        if launcher.ANDROID:
            # On a phone: the phone app's own player plays the list, and reports back
            # where you are (/api/android/progress). In a party you host, it plays for
            # everyone (paused until they're all ready), as on a computer.
            with self._party_lock:
                hosting = self.party if self.party and self.party.active and self.party.role == "host" else None
            hold = bool(hosting and any(p != hosting.me for p in hosting.people))
            session = phone_player.PhoneSession(item["id"], entries, start, paused=bool(body.get("paused") or hold),
                                                anime=item["type"] == "anime", index=first)
            if hosting:
                session.party = True
                hosting.attach(session, hold=hold and not body.get("paused"))
                self._party_now(hosting, this)
            self.store.update(item["id"], patch)
            return dict(session.launch(), ok=True, player="android", file=this.get("name") or Path(path).name,
                        queued=len(entries) - 1 - first, resumed_at=start, stream=bool(found))
        wanted = body.get("player") or s["player"]
        note = ""
        with self._party_lock:
            hosting = self.party if self.party and self.party.active and self.party.role == "host" else None
        if hosting and player.find_mpv():
            wanted = "mpv"                   # it plays in the party: the built-in player keeps everyone in step
        if wanted == "mpv":
            mpv = player.find_mpv()
            if mpv:
                # In a party with others getting it ready, it starts paused and plays for
                # everyone at once when they're all ready.
                hold = bool(hosting and any(p != hosting.me for p in hosting.people))
                session = self.player.play(self._session(mpv, item, entries, start,
                                                         ["--pause"] if body.get("paused") or hold else (),
                                                         first=first))
                with self._party_lock:
                    party = self.party
                if party and party.active and party.role == "host":
                    party.attach(session, hold=hold and not body.get("paused"))   # whatever the host plays, the party follows
                    self._party_now(party, this)
                self.store.update(item["id"], patch)
                return {"ok": True, "player": "mpv", "file": this.get("name") or Path(path).name,
                        "queued": len(entries) - 1 - first, "resumed_at": start, "stream": bool(found),
                        "subtitle": Path(this["sub"]).name if this.get("sub") else ""}
            note = "The built-in player is missing, so VLC was used."
        if wanted == "custom" and s["custom_player_path"]:
            # Your own player: the file (or its stream link) is handed to it; it keeps its own place.
            try:
                launcher.play_in_player(s["custom_player_path"], this["path"])
            except launcher.LaunchError as e:
                raise ApiError(str(e)) from e
            self.store.update(item["id"], patch)
            return {"ok": True, "player": "custom", "file": Path(path).name, "queued": 0, "resumed_at": 0,
                    "player_name": Path(s["custom_player_path"]).stem}
        self._play_vlc(entries[first:], start)
        self.store.update(item["id"], patch)
        return {"ok": True, "player": "vlc", "file": Path(path).name, "queued": len(entries) - 1 - first,
                "resumed_at": start, "note": note}

    def _session(self, mpv, item, entries, start=0, extra=(), first=0, playlist=False):
        web = next((e for e in entries if e.get("web")), None)
        if web:
            # A video from your own site: sent as if from its page (some sites check).
            headers = web.get("headers") or {}
            # (an episode of the playlist is found when it opens: a minute or two, at worst)
            extra = [f"--log-file={player.web_log()}", "--network-timeout=150", "--cache=yes", "--demuxer-max-bytes=150MiB",
                     "--demuxer-readahead-secs=60", f"--user-agent={headers.get('User-Agent') or scrape.UA}"] + (
                [f"--referrer={web['referer']}"] if web.get("referer") else []) + [
                f"--http-header-fields-append={k}: {v}" for k, v in headers.items()
                if k in ("Origin", "Cookie") and v] + list(extra)
        elif self.torrents.records or any(self.torrents.parse_url(e["path"]) for e in entries):
            # Streams: wait patiently for data, and keep plenty of it ahead.
            extra = ["--network-timeout=180", "--cache=yes", "--demuxer-max-bytes=200MiB",
                     "--demuxer-readahead-secs=90"] + list(extra)
        for e in entries:
            if not e.get("sub") and e.get("key"):
                e["sub"] = self.saved_sub(item, e["key"]) or None       # (your subtitles for it)
        session = player.Session(
            mpv, item["id"], entries, start=start,
            extra_args=player.script_args(self.settings.get()["skip_intro"], self.settings.get()["theme"]) + list(extra),
            audio_langs="jpn,ja,japanese,eng,en" if item["type"] == "anime" else "",
            on_progress=self._save_position, on_finished=self._finished_entry, first=first, playlist=playlist,
            on_exit=self._player_exited)
        session.listeners.append(self._player_message)
        return session

    def _entries(self, item, path, subtitle, season=None, episode=None, queue=True, around=False):
        """What to play: the file, then (with `queue`) the episodes after it. With `around`,
        the episodes before it come first too (Previous works), and patch["first"] says
        which entry is the file asked for."""
        series = item["format"] == "series"
        if series and season is not None and episode is not None:
            first_key = f"{max(0, int(season))}:{max(1, int(episode))}"
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
            if first and first.get("torrent"):
                entries[0]["torrent"] = first["torrent"]
            if first and queue:
                # The episodes after it, in episode order (files on your computer and
                # a torrent's episodes mixed, one copy of each).
                ordered = list(dict.fromkeys(v["path"] for _, v in sorted(scanner.episode_map(item, videos).items())))
                by_path = {v["path"]: v for v in videos}
                here = ordered.index(path) if path in ordered else -1
                after = ordered[here + 1:] if here >= 0 else []
                before = ordered[:here] if around and here > 0 else []          # (every episode: the player lists them all)

                def queued(p):
                    v = by_path[p]
                    subs = subtitles(v)
                    # Keep using the same kind of subtitle (none / auto) for the queue.
                    sub = subs[0]["path"] if subs and subtitle else None
                    entry = {"path": v["path"], "sub": sub, "key": keys.get(v["path"], v["path"])}
                    if v.get("torrent"):
                        entry["torrent"] = v["torrent"]
                    return entry
                entries += [queued(p) for p in after]
                if before:
                    entries[:0] = [queued(p) for p in before]
                    patch["first"] = len(before)
            if first_key:
                patch["progress"] = {"season": int(first_key.split(":")[0]),
                                     "episode": int(first_key.split(":")[1])}
        else:
            patch["local_file"] = path
        for e in entries:
            e["label"] = self._label(item, e["key"])
            e["party"] = self._party_meta(item, e["key"])
            if e.get("torrent"):
                # The other side of a watch party can stream the same torrent: its exact
                # file (no need to work out the whole torrent first), and from you.
                e["party"].update(magnet=self.torrents.magnet(e["torrent"]["hash"]), th=e["torrent"]["hash"],
                                  ti=e["torrent"]["index"], peer=self.torrents.my_address())
            if self.torrents.parse_url(e["path"]):
                e["name"] = Path(urllib.parse.unquote(urllib.parse.urlsplit(e["path"]).path)).name
        return entries, patch

    @staticmethod
    def _party_meta(item, key):
        """What a watch party tells the other side about a file."""
        meta = {"src": item["source"], "id": item["source_id"], "type": item["type"], "title": item["title"]}
        if ":" in key and key.replace(":", "").isdigit():
            meta["season"], meta["episode"] = (int(x) for x in key.split(":"))
        meta["key"] = f'{item["source"]}:{item["source_id"]}:{meta.get("season", "")}:{meta.get("episode", "")}'
        return meta

    def _play_extra(self, item, path, subtitle, wanted):
        entry = {"path": path, "sub": subtitle or None, "key": f"extra:{Path(path).name}",
                 "label": f"{item['title']} - {Path(path).stem}"}
        mpv = player.find_mpv() if wanted == "mpv" else ""
        if mpv:
            self.player.play(player.Session(mpv, item["id"], [entry]))
            return {"ok": True, "player": "mpv", "file": Path(path).name, "queued": 0, "resumed_at": 0}
        self._play_vlc([entry])
        return {"ok": True, "player": "vlc", "file": Path(path).name, "queued": 0, "resumed_at": 0}

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

    def phone_progress(self, item_id, key, pos, dur, finished=False):
        """The phone player: where you are in an episode, or that you watched it to the end."""
        entry = {"key": str(key)}
        session = phone_player.current()
        if session is not None and session.item_id == item_id:
            entry = next((e for e in session.entries if e.get("key") == entry["key"]), entry)
        if finished:
            self._finished_entry(item_id, entry)
        elif pos and dur:
            self._save_position(item_id, entry, float(pos), float(dur))

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
        if entry.get("torrent"):
            self.torrents.finished_watching(entry["torrent"]["hash"], entry["torrent"]["index"])
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

    # ---- your own video sites ("Play online (my sites)")

    # ---- your own video sites: which one, the links found, the player's playlist

    # ---- subtitles: yours (a folder, or a file in the player) and found online

    def _subs_dir(self, item_id=""):
        d = self.data_dir / "subtitles" / item_id if item_id else self.data_dir / "subtitles"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def saved_sub(self, item, key):
        """The subtitle file saved for an episode ("" if none, or it's gone)."""
        path = (item.get("subs") or {}).get(key) or ""
        return path if path and os.path.isfile(path) else ""

    def _save_sub(self, item_id, key, path):
        item = self.store.get(item_id)
        subs = dict(item.get("subs") or {})
        subs[key] = path
        self.store.update(item_id, {"subs": subs})

    def subs_info(self, item):
        job = self._subjobs.get(item["id"]) or {}
        return {"saved": {k: Path(v).name for k, v in (item.get("subs") or {}).items() if os.path.isfile(v)},
                "keep": item.get("keep_subs", True), "job": {k: v for k, v in job.items() if not k.startswith("_")}}

    def subs_from_folder(self, item, body):
        """Every subtitle file in a folder, matched to the episodes by their names."""
        folder = str(body.get("path") or "")
        if not os.path.isdir(folder):
            raise ApiError("choose a folder with the subtitle files")
        files = []
        for root, _dirs, names in os.walk(folder):
            if root.count(os.sep) - folder.count(os.sep) > 2:
                continue
            files += [os.path.join(root, n) for n in names if Path(n).suffix.lower() in scanner.SUB_EXTS]
        subs, series = dict(item.get("subs") or {}), item["format"] == "series"
        matched = 0
        for f in sorted(files):
            if not series:
                subs["movie"] = f
                matched = 1
                break
            season, eps = scanner.parse_episode(Path(os.path.relpath(f, folder)).parts)
            if not eps:
                continue
            for e in eps:
                key = f"{season or item['progress']['season'] or 1}:{e}"
                if key not in subs or _english(f) < _english(subs[key]):
                    subs[key] = f
                    matched += 1
        self.store.update(item["id"], {"subs": subs})
        return {"ok": True, "matched": matched, "files": len(files)}

    def set_keep_subs(self, item, body):
        return self.store.update(item["id"], {"keep_subs": bool(body.get("keep"))})

    def clear_subs(self, item, body):
        for key, path in list((item.get("subs") or {}).items()):
            if _inside(path, str(self._subs_dir())):
                try:
                    os.remove(path)
                except OSError:
                    pass
        return self.store.update(item["id"], {"subs": {}})

    def _clean_subs(self):
        """Subtitles found online for a title that doesn't keep them: gone after 12 hours."""
        while True:
            time.sleep(1800)
            try:
                for item in self.store.list():
                    if item.get("keep_subs", True) or not item.get("subs"):
                        continue
                    keep = {}
                    for key, path in item["subs"].items():
                        old = os.path.isfile(path) and time.time() - os.path.getmtime(path) > SUBS_FOR
                        if old and _inside(path, str(self._subs_dir())):
                            try:
                                os.remove(path)
                            except OSError:
                                keep[key] = path
                        elif os.path.isfile(path):
                            keep[key] = path
                    if keep != item["subs"]:
                        self.store.update(item["id"], {"subs": keep})
            except Exception:  # noqa: BLE001 - only tidying: next time
                pass

    def find_subs(self, item, body):
        """Find with AI: subtitles for one episode found online, tried in the player with you
        (in sync? yes: the same release for every episode; no: the next one)."""
        old = self._subjobs.get(item["id"])
        if old and old.get("running"):
            return {"ok": True, "job": {k: v for k, v in old.items() if not k.startswith("_")}}
        series = item["format"] == "series"
        season = int(body.get("season") or item["progress"]["season"] or 1) if series else None
        episode = int(body.get("episode") or item["progress"]["episode"] or 1) if series else None
        job = self._subjobs[item["id"]] = {"running": True, "stage": "Looking for subtitles…", "progress": 0.03,
                                          "asking": False, "error": "", "done": 0, "total": 0, "release": "",
                                          "episode": f"{season}:{episode}" if series else "movie",
                                          "_event": threading.Event(), "_answer": ""}
        threading.Thread(target=self._find_subs, args=(item["id"], season, episode, job), daemon=True).start()
        return {"ok": True, "job": {k: v for k, v in job.items() if not k.startswith("_")}}

    def answer_subs(self, item_id, answer):
        job = self._subjobs.get(item_id)
        if not job or not job.get("running"):
            return {"ok": False}
        job["_answer"] = answer if answer in ("yes", "no", "stop") else "stop"
        job["_event"].set()
        return {"ok": True}

    def _find_subs(self, item_id, season, episode, job):
        item = self.store.get(item_id)
        key = f"{season}:{episode}" if season is not None else "movie"
        session = None
        try:
            found = subfinder.search(item, season, episode)
            job.update(stage="Picking the best match…", progress=0.2)
            ranked = subfinder.rank(item, season, episode, found)
            got = self._online_found.get((item_id, key))
            ranked = subfinder.ai_order(self.settings.get(), item, season, episode, ranked,
                                        source=got[0]["name"] if got else "")
            if not ranked:
                raise subfinder.SubError("no subtitles were found for it")
            for n, cand in enumerate(ranked[:8]):
                job.update(stage=f"Downloading {cand['release'] or cand['name']}…", progress=0.35, release=cand["release"] or cand["name"])
                try:
                    path = self._store_sub(item_id, key, cand)
                except subfinder.SubError:
                    continue
                job.update(stage="Opening the episode with them…", progress=0.6)
                session = self._sub_session(item, season, episode, path, session)
                job.update(stage="Are they in sync? Answer in the player (Y / N) or here.", progress=0.75, asking=True)
                session.send(["script-message-to", "watchlist_skip", "ask-sync",
                              f"Subtitles {n + 1}: {(cand['release'] or cand['name'])[:60]}"])
                job["_event"].clear()
                job["_answer"] = ""
                while not job["_event"].wait(1):
                    if session.ended:
                        job["_answer"] = "stop"
                        break
                job["asking"] = False
                answer = job["_answer"]
                if answer == "stop":
                    job.update(stage="Stopped", running=False)
                    return
                if answer == "no":
                    session.send(["sub-remove"])
                    continue
                self._save_sub(item_id, key, path)
                self._subs_for_the_rest(item, season, episode, cand, job)
                return
            job.update(stage="None of them fit - try Browse with your own files", error="no subtitles fitted", running=False)
        except (subfinder.SubError, OnlineError, ApiError, KeyError) as e:
            job.update(stage=str(e), error=str(e), running=False)
        finally:
            if session and not session.ended:
                session.send(["script-message-to", "watchlist_skip", "stop-asking"])
            job["running"] = False

    def _store_sub(self, item_id, key, cand):
        data = subfinder.download(cand)
        path = self._subs_dir(item_id) / f"{key.replace(':', 'x')}-{cand['id']}.{cand['format'] if cand['format'] in ('srt', 'ass', 'ssa', 'vtt') else 'srt'}"
        path.write_bytes(data)
        return str(path)

    def _sub_session(self, item, season, episode, path, session):
        """The player with the episode and these subtitles in it (the one open when it's this episode)."""
        key = f"{season}:{episode}" if season is not None else "movie"
        now = self.player.session
        if now and not now.ended and now.item_id == item["id"] and (now.current() or {}).get("key") == key:
            now.send(["sub-add", path, "select", "Found subtitles", "en"])
            return now
        local = self.local_info(item, season or 1, episode or 1)["target"]
        if local and not local.get("stream"):
            self.play(item, {"path": local["path"], "subtitle": path, "season": season, "episode": episode})
        elif self._video_sites(item):
            res = self.play_scraped(item, {"season": season, "episode": episode})
            if not res.get("ok"):
                raise ApiError(res.get("error") or "the episode couldn't be played")
        else:
            raise ApiError("play the episode once first (it isn't on your computer or your sites)")
        for _ in range(120):
            s = self.player.session
            if s and s.connected and s.loaded:
                s.send(["sub-add", path, "select", "Found subtitles", "en"])
                return s
            time.sleep(0.5)
        raise ApiError("the player didn't open")

    def _subs_for_the_rest(self, item, season, episode, chosen, job):
        """You said it's in sync: the same release's subtitles for every other episode."""
        if item["format"] != "series":
            job.update(stage="Saved", progress=1, running=False)
            return
        keys = [k for k in self._episode_keys(item, (season, episode)) if k != f"{season}:{episode}"]
        job.update(total=len(keys), done=0, stage="Getting the other episodes' subtitles…")
        from concurrent.futures import ThreadPoolExecutor

        def one(k):
            s, e = self._key_numbers(k)
            if job["_answer"] == "stop":
                return
            try:
                cands = subfinder.rank(item, s, e, subfinder.search(item, s, e))
                best = subfinder.same_source(chosen, cands) if cands else None
                if best:
                    self._save_sub(item["id"], k, self._store_sub(item["id"], k, best))
            except (subfinder.SubError, KeyError):
                pass
            finally:
                job["done"] += 1
                job["progress"] = 0.8 + 0.2 * job["done"] / max(1, job["total"])
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(one, keys))
        job.update(stage=f"Done - subtitles for {job['done'] + 1} episodes", progress=1, running=False)

    def _player_message(self, session, msg):
        """What you do in the player that the app answers: the subtitle check, your own
        subtitle file, switching between sub and dub."""
        if msg.get("event") != "client-message":
            return
        args = msg.get("args") or []
        if not args:
            return
        if args[0] == "watchlist-sync" and len(args) > 1:
            self.answer_subs(session.item_id, args[1])
        elif args[0] == "watchlist-load-subtitle":
            threading.Thread(target=self._load_own_sub, args=(session,), daemon=True).start()
        elif args[0] == "watchlist-switch-version":
            threading.Thread(target=self._switch_version, args=(session,), daemon=True).start()
        elif args[0] == "watchlist-other-source":
            threading.Thread(target=self._other_source, args=(session,), daemon=True).start()
        elif args[0] == "watchlist-servers":
            if self._servers_menu(session):
                threading.Thread(target=self._follow_servers_menu, args=(session,), daemon=True).start()
        elif args[0] == "watchlist-server" and len(args) > 1:
            threading.Thread(target=self.use_server, args=(session, args[1]), daemon=True).start()
        elif args[0] == "watchlist-other-site":
            threading.Thread(target=self._other_site, args=(session,), daemon=True).start()

    def _load_own_sub(self, session):
        path = dialogs.choose("file", "Choose a subtitle file", "", "subtitle")
        if not path or Path(path).suffix.lower() not in scanner.SUB_EXTS:
            return
        session.send(["sub-add", path, "select", Path(path).stem, ""])
        entry = session.current()
        if entry:
            try:
                self._save_sub(session.item_id, entry["key"], path)
            except KeyError:
                pass

    def _other_source(self, session):
        """The video won't load: the episode's next source on the same site (the same sub or
        dub), else on your next site, swapped in where you are. The one that works is the
        site's choice from then on, and the episodes found ahead are found again with it."""
        entry = session.current()
        got = self._online_found.get((session.item_id, (entry or {}).get("key")))
        if not entry or not entry.get("web") or not got:
            session.send(["show-text", "Another source: only for videos from your own sites", 3000])
            return
        key = entry["key"]
        item = self.store.get(session.item_id)
        site, found = got[0], got[1]
        stream = found.get("stream") or {}
        track = stream.get("kind") or entry.get("track") or "sub"
        tried = self._tried_sources.setdefault((item["id"], key), set())
        tried.update({stream.get("id") or "auto", f"url:{found['url']}"})
        session.send(["show-text", "Trying another source…", 60000])
        season, episode = self._key_numbers(key)
        new, where = None, site
        sites = [site] + [x for x in self._online_order(item, key) if x["id"] != site["id"]]
        for n, candidate in enumerate(sites):
            try:
                new = self.scraper.resolve(candidate, item, season, episode, track=track if track == "dub" else "",
                                           skip=tuple(tried) if n == 0 else (), fresh=True)
            except scrape.ScrapeError:
                continue
            if new.get("url") and f"url:{new['url']}" not in tried:
                where = candidate
                break
            new = None
        if not new:
            session.send(["show-text", "No other source found for this one", 4000])
            return
        picked = new.get("stream") or {}
        if where is site and picked.get("label"):
            self.scraper.prefer_stream(site, picked["label"], picked.get("kind") or track)
        # The episodes found ahead (with the source that didn't work) are found again.
        for k in [k for k in self._online_found if k[0] == item["id"] and k[1] != key]:
            self._online_found.pop(k, None)
        self._online_found[(item["id"], key)] = (where, new, time.time())
        fresh = dict(self._web_entry(item, where, key, new), online=True)
        sub = self.saved_sub(item, key)
        if sub:
            fresh["sub"] = sub
        session.swap(fresh, session.position() or 0)
        name = picked.get("label") or where["name"]
        session.send(["show-text", f"Playing from {name}" + ("" if where is site else f" on {where['name']}"), 3000])
        if item["format"] == "series":
            threading.Thread(target=self._link_around, args=(item["id"], where, key, session), daemon=True).start()

    # ---- the player's Servers list: every server of the episode on its site (each checked in
    # the background, a few at once), then your other sites; one press plays it from where you are.

    SERVER_CHECKS = 3          # servers checked at once (each is a hidden page)

    def server_list(self, session, start=True):
        """{"items": [{"id", "site_id", "site", "label", "kind", "state", "on"}], "checking", "note"}
        for the episode playing ("state": "ok", "checking", "failed", "unchecked")."""
        entry = session.current()
        got = self._online_found.get((session.item_id, (entry or {}).get("key")))
        if not entry or not entry.get("web") or not got:
            return {"items": [], "checking": False, "note": "Servers: only for videos from your own sites"}
        key = entry["key"]
        slot = (session.item_id, key)
        site, found = got[0], got[1]
        state = self._servers.get(slot)
        if not state or state.get("page") != found.get("page") or state.get("site") != site["id"]:
            state = self._servers[slot] = {"site": site["id"], "page": found.get("page"), "items": {}, "order": [],
                                           "running": False, "listed": False}
        playing = (found.get("stream") or {}).get("id")
        if start and not state["running"]:
            state["running"] = True
            threading.Thread(target=self._check_servers, args=(session.item_id, key, slot), daemon=True).start()
        items = []
        for sid in state["order"]:
            x = state["items"][sid]
            # (the one chosen; by its video only when which one is playing isn't known - two
            # servers can give the same video)
            on = x["site_id"] == site["id"] and (sid == playing if playing in state["items"]
                                                 else bool(x.get("url")) and x["url"] == found.get("url"))
            items.append({k: x.get(k) for k in ("id", "site_id", "site", "label", "kind", "state")} | {"on": bool(on)})
        if not any(x["on"] for x in items):
            label = (found.get("stream") or {}).get("label") or site["name"]
            items.insert(0, {"id": playing or "", "site_id": site["id"], "site": site["name"], "label": label,
                             "kind": (found.get("stream") or {}).get("kind") or "", "state": "ok", "on": True})
        return {"items": items, "checking": state["running"],
                "note": "" if state["listed"] or items[1:] else "Looking for this episode's servers…"}

    def _check_servers(self, item_id, key, slot):
        """Every server of the episode's page (as its player lists them), each found in a hidden
        page of its own, a few at once; then the episode on each of your other sites."""
        state = self._servers[slot]
        try:
            item = self.store.get(item_id)
            site_id = state["site"]
            got = self._online_found.get(slot)
            if not got:
                return
            site, found = got[0], got[1]
            season, episode = self._key_numbers(key)
            streams = self.scraper.episode_servers(found)
            if streams and not found.get("streams"):
                found["streams"] = streams
            for s in streams:
                if s["id"] not in state["items"]:
                    state["order"].append(s["id"])
                    state["items"][s["id"]] = dict(s, site_id=site_id, site=site["name"], state="unchecked")
            state["listed"] = True
            playing = (found.get("stream") or {}).get("id")
            if playing in state["items"]:
                state["items"][playing].update(state="ok", found=found, url=found.get("url"))
            todo = [x for x in state["items"].values() if x["state"] == "unchecked"]

            def check(x):
                if self._servers.get(slot) is not state:
                    return
                x["state"] = "checking"
                try:
                    new = self.scraper.resolve(site, item, season, episode, stream=f"{x['id']}:{x['kind']}:{x['label']}",
                                               fresh=True, remember=False)
                    ok = bool(new.get("url"))
                except scrape.ScrapeError:
                    new, ok = None, False
                x.update(state="ok" if ok else "failed", found=new if ok else None, url=(new or {}).get("url"),
                         at=time.time())
            with ThreadPoolExecutor(self.SERVER_CHECKS) as pool:
                list(pool.map(check, todo))
            # ...then your other sites (each the way it plays by itself).
            others = [x for x in self._online_order(item, key) if x["id"] != site_id]

            def other(s):
                sid = f"site|{s['id']}"
                if sid in state["items"]:
                    return
                x = state["items"][sid] = {"id": sid, "site_id": s["id"], "site": s["name"], "label": s["name"],
                                           "kind": "", "state": "checking"}
                state["order"].append(sid)
                try:
                    new = self.scraper.resolve(s, item, season, episode, fresh=False)
                    ok = bool(new.get("url"))
                except scrape.ScrapeError:
                    new, ok = None, False
                x.update(state="ok" if ok else "failed", found=new if ok else None, url=(new or {}).get("url"),
                         at=time.time())
                if ok and (new.get("stream") or {}).get("label"):
                    x["label"] = f"{s['name']} · {new['stream']['label']}"
            with ThreadPoolExecutor(self.SERVER_CHECKS) as pool:
                list(pool.map(other, others))
        except Exception:  # noqa: BLE001 - the list shows what was checked; the rest stays unchecked
            pass
        finally:
            state["running"] = False

    def use_server(self, session, server_id, share=True):
        """One of the Servers list's: played from where you are (found now if it wasn't yet) - in a
        watch party, for everyone (`share`)."""
        entry = session.current()
        if not entry or not entry.get("web"):
            return
        key = entry["key"]
        slot = (session.item_id, key)
        state = self._servers.get(slot) or {}
        x = (state.get("items") or {}).get(server_id)
        item = self.store.get(session.item_id)
        if x is None:
            session.send(["show-text", "That server isn't in the list any more", 3000])
            return
        site = next((s for s in self._video_sites(item) if s["id"] == x["site_id"]), None)
        if not site:
            return
        session.send(["show-text", f"Switching to {x['label']}…", 60000])
        new = x.get("found") if x.get("found") and time.time() - x.get("at", 0) < self.FOUND_FOR else None
        if not new:
            season, episode = self._key_numbers(key)
            try:
                if server_id.startswith("site|"):
                    new = self.scraper.resolve(site, item, season, episode, fresh=True)
                else:
                    new = self.scraper.resolve(site, item, season, episode, stream=f"{x['id']}:{x['kind']}:{x['label']}",
                                               fresh=True, remember=False)
            except scrape.ScrapeError as e:
                x["state"] = "failed"
                session.send(["show-text", f"{x['label']} didn't give a video ({e})"[:160], 5000])
                return
            if not new.get("url"):
                x["state"] = "failed"
                session.send(["show-text", f"{x['label']} didn't give a video", 4000])
                return
            x.update(state="ok", found=new, url=new["url"], at=time.time())
        if not server_id.startswith("site|"):
            self.scraper.prefer_stream(site, x["label"], x["kind"])      # (it plays: tried first from now on)
            new = dict(new, stream={"id": x["id"], "label": x["label"], "kind": x["kind"]},
                       streams=new.get("streams") or [{k: y[k] for k in ("id", "label", "kind")}
                                                       for y in state["items"].values() if y["site_id"] == site["id"]])
        else:
            self._online_linked(item, key, site)
        for k in [k for k in self._online_found if k[0] == item["id"] and k[1] != key]:
            self._online_found.pop(k, None)           # (the episodes found ahead: found again with it)
        self._online_found[slot] = (site, new, time.time())
        state["site"], state["page"] = site["id"], new.get("page")
        fresh = dict(self._web_entry(item, site, key, new), online=True)
        sub = self.saved_sub(item, key)
        if sub:
            fresh["sub"] = sub
        session.swap(fresh, session.position() or 0)
        session.send(["show-text", f"Playing from {x['label']}" + ("" if x["site"] in x["label"] else f" on {x['site']}"), 3000])
        if item["format"] == "series":
            threading.Thread(target=self._link_around, args=(item["id"], site, key, session), daemon=True).start()
        with self._party_lock:
            party = self.party if self.party and self.party.active and self.party.session is session else None
        if share and party:
            whole = server_id.startswith("site|")
            party.share_server({"k": key, "h": urllib.parse.urlsplit(site["url"]).netloc.lower().removeprefix("www."),
                                "l": "" if whole else x["label"], "d": "" if whole else x["kind"], "w": 1 if whole else 0})

    def _party_server(self, party, choice):
        """Someone in the party chose another server (or site) for the episode playing: this
        player moves to it too, from where it is (the same server on the same site)."""
        session = party.session
        entry = session.current() if session and not session.ended else None
        if not entry or not entry.get("web") or entry.get("key") != choice.get("k"):
            return
        item = self.store.get(session.item_id)
        host = str(choice.get("h") or "")
        site = next((s for s in self._video_sites(item)
                     if urllib.parse.urlsplit(s["url"]).netloc.lower().removeprefix("www.") == host), None)
        if not site:
            session.send(["show-text", f"They switched to {host}, which isn't one of your sites", 4000])
            return
        state = self.server_list(session, start=False)
        slot = (session.item_id, entry["key"])
        if slot not in self._servers:
            return                                      # (not one of your sites' videos here)
        items = self._servers[slot]["items"]
        if choice.get("w"):
            sid = f"site|{site['id']}"
            if sid not in items:
                self._servers[slot]["order"].append(sid)
                items[sid] = {"id": sid, "site_id": site["id"], "site": site["name"], "label": site["name"], "kind": "",
                              "state": "unchecked"}
        else:
            label, kind = str(choice.get("l") or ""), str(choice.get("d") or "")
            playing = next((x for x in state["items"] if x["on"]), None)
            if playing and playing["site_id"] == site["id"] and playing["label"] == label:
                return                                  # (on that one already)
            sid = next((k for k, x in items.items() if x["site_id"] == site["id"] and x["label"] == label
                        and (x["kind"] or "") == kind), None)
            if not sid:
                sid = f"click||{label.replace(':', '~')}|{kind}"
                self._servers[slot]["order"].append(sid)
                items[sid] = {"id": sid, "site_id": site["id"], "site": site["name"], "label": label, "kind": kind,
                              "state": "unchecked"}
        self.use_server(session, sid, share=False)

    def _other_site(self, session):
        """Find it on another site: the episode looked for again on your other sites (not the one
        playing), the first that has it played from where you are."""
        entry = session.current()
        got = self._online_found.get((session.item_id, (entry or {}).get("key")))
        if not entry or not entry.get("web") or not got:
            session.send(["show-text", "Another site: only for videos from your own sites", 3000])
            return
        key = entry["key"]
        item = self.store.get(session.item_id)
        here = got[0]
        state = self._servers.get((item["id"], key)) or {}
        ready = [x for x in (state.get("items") or {}).values()
                 if x["site_id"] != here["id"] and x["state"] == "ok" and x.get("found")]
        if ready:
            self.use_server(session, ready[0]["id"])
            return
        session.send(["show-text", "Looking for it on your other sites…", 60000])
        season, episode = self._key_numbers(key)
        try:
            site, new = self._online_find(item, season, episode, skip=[here["id"]], fresh=True)
        except OnlineError as e:
            session.send(["show-text", f"None of your other sites had it ({e})"[:200], 6000])
            return
        sid = f"site|{site['id']}"
        state = self._servers.setdefault((item["id"], key), {"site": here["id"], "page": got[1].get("page"), "items": {},
                                                             "order": [], "running": False, "listed": False})
        if sid not in state["items"]:
            state["order"].append(sid)
        state["items"][sid] = {"id": sid, "site_id": site["id"], "site": site["name"], "label": site["name"], "kind": "",
                               "state": "ok", "found": new, "url": new.get("url"), "at": time.time()}
        self.use_server(session, sid)

    def _list_servers(self, url):
        webtools.ADBLOCK = self.settings.get()["adblock"]
        return webtools.list_servers(url)

    def _servers_menu(self, session, update=False):
        """The built-in player's Servers menu (uosc), kept up to date while servers are checked."""
        got = self.server_list(session)
        marks = {"ok": "✓", "checking": "checking…", "failed": "didn't work", "unchecked": ""}
        items = []
        for x in got["items"]:
            kind = {"sub": "Sub", "dub": "Dub", "raw": "Raw"}.get(x["kind"], "")
            hint = " · ".join(t for t in (kind, "playing" if x["on"] else marks.get(x["state"], "")) if t)
            items.append({"title": x["label"], "hint": hint, "active": x["on"], "muted": x["state"] == "failed",
                          "value": ["script-message", "watchlist-server", x["id"]]})
        items.append({"title": "Find it on another site", "hint": "", "bold": True,
                      "value": ["script-message", "watchlist-other-site"]})
        if got["note"]:
            items.insert(0, {"title": got["note"], "muted": True, "selectable": False})
        menu = {"type": "watchlist-servers", "title": "Servers" + (" (checking…)" if got["checking"] else ""),
                "items": items}
        session.send(["script-message-to", "uosc", "update-menu" if update else "open-menu", json.dumps(menu)])
        return got["checking"]

    def _follow_servers_menu(self, session):
        """While the Servers menu is open and servers are being checked: shown again as they are."""
        for _ in range(90):
            time.sleep(2)
            if session.ended or not self._servers_menu(session, update=True):
                break

    def _switch_version(self, session):
        """Sub to dub (or back) for the episode playing, from where it is; the episodes after
        it follow."""
        entry = session.current()
        got = self._online_found.get((session.item_id, (entry or {}).get("key")))
        if not entry or not entry.get("web") or not got:
            session.send(["show-text", "Sub / Dub: only for episodes from your own sites", 3000])
            return
        site, found = got[0], got[1]
        playing = (found.get("stream") or {}).get("kind") or entry.get("track") or "sub"
        other = "sub" if playing == "dub" else "dub"
        session.send(["show-text", f"Finding the {other}…", 30000])
        item = self.store.get(session.item_id)
        season, episode = self._key_numbers(entry["key"])
        try:
            new = self.scraper.resolve(site, item, season, episode, track=other)
        except scrape.ScrapeError as e:
            session.send(["show-text", f"No {other} found here ({e})", 4000])
            return
        if not new.get("url") or new["url"] == found["url"] or ((new.get("stream") or {}).get("kind") or "sub") != other:
            session.send(["show-text", f"This site has no {other} of it", 4000])
            return
        self._online_track[session.item_id] = other
        self._online_found[(session.item_id, entry["key"])] = (site, new, time.time())
        fresh = dict(self._web_entry(item, site, entry["key"], new), online=True)
        sub = self.saved_sub(item, entry["key"])
        if sub:
            fresh["sub"] = sub
        session.swap(fresh, session.position() or 0)
        session.send(["show-text", f"Now playing the {other}", 2500])

    @staticmethod
    def _get_chromium():
        try:
            cdp.download()
        except Exception:  # noqa: BLE001 - tried again when a video is looked for
            pass

    def shutdown(self):
        """Quitting: your place saved, the player closed, torrents saved and stopped, the
        watch party left, and any program the app started ended."""
        for step in (lambda: self.player.session and self.player.session.stop(),
                     lambda: self.party and self.party.leave("closed the app"),
                     self.torrents.shutdown, cdp.shutdown):
            try:
                step()
            except Exception:  # noqa: BLE001 - quitting goes on whatever happens
                pass
        player.kill_children()

    def _video_sites(self, item):
        return scrape.sites_for(self.settings.get(), item["type"])

    def _online_order(self, item, key, prefer=""):
        """The sites to try for an episode, in order: the one asked for, the one chosen for
        this episode (right-click), the one chosen for the title, the one it's linked to
        (first, when "keep connected" is ticked), then Settings' order."""
        sites = self._video_sites(item)
        linked = (item.get("online_links") or {}).get(key, "")
        ids = [prefer, (item.get("online_site_eps") or {}).get(key, ""), item.get("online_site") or ""]
        ids = ([linked] + ids) if item.get("keep_site") else (ids + [linked])
        order = []
        for sid in ids:
            site = next((x for x in sites if x["id"] == sid), None)
            if site and site not in order:
                order.append(site)
        return order + [x for x in sites if x not in order]

    FOUND_FOR = 15 * 60       # a video found on a site still plays this long after (its address expires)

    def _episode_video(self, item, season, episode, track="", prefer="", pin="", skip=()):
        """(site, found) for an episode: the one found a moment ago (ahead of time, while the one
        before played), the one being found right now (waited for), or found now."""
        key = f"{season}:{episode}" if item["format"] == "series" else "movie"
        slot = (item["id"], key)
        for _ in range(2):
            got = self._online_found.get(slot)
            # (only from the site that comes first for it now: the one asked for, the one you
            # chose for this episode or the title - not one found on another site before)
            order = [x["id"] for x in self._online_order(self.store.get(item["id"]), key, prefer) if x["id"] not in skip]
            if got and not pin and time.time() - got[2] < self.FOUND_FOR and order and got[0]["id"] == order[0] \
                    and (not track or ((got[1].get("stream") or {}).get("kind") or "sub") == track):
                return got[0], got[1]
            with self._lock:
                busy = self._finding.get(slot)
                if not busy:
                    busy = self._finding[slot] = threading.Event()
                    mine = True
                else:
                    mine = False
            if not mine:
                busy.wait(120)                 # (being found already: that one, not a second)
                continue
            try:
                site, found = self._online_find(item, season, episode, track=track, prefer=prefer, pin=pin, skip=skip)
                self._online_found[slot] = (site, found, time.time())
                return site, found
            finally:
                with self._lock:
                    self._finding.pop(slot, None)
                busy.set()
        return self._online_find(item, season, episode, track=track, prefer=prefer, pin=pin, skip=skip)

    def _online_find(self, item, season, episode, track="", prefer="", pin="", fresh=False, skip=()):
        """(site, found) from the first site that has the episode; OnlineError if none does."""
        key = f"{season}:{episode}" if item["format"] == "series" else "movie"
        errors, choices, page = [], None, ""
        sites = [x for x in self._online_order(item, key, prefer) if x["id"] not in skip]
        if pin:
            sites = sites[:1]                 # (a title you picked on that site: only there)
        chosen = prefer or (item.get("online_site_eps") or {}).get(key) or item.get("online_site") \
            or (item.get("keep_site") and (item.get("online_links") or {}).get(key))
        if len(sites) > 1 and not chosen and not pin:
            return self._online_race(item, key, season, episode, track, sites, fresh)
        for site in sites:
            try:
                found = self.scraper.resolve(site, item, season, episode, pin=pin, track=track, fresh=fresh)
            except scrape.ScrapeError as e:
                errors.append(f"{site['name']}: {e}")
                page = page or e.page
                if e.choices and choices is None:
                    choices = {"site": site["id"], "site_name": site["name"], "choices": e.choices, "question": str(e)}
                continue
            except Exception as e:  # noqa: BLE001 - one site's trouble isn't the others' (as in the race)
                import traceback
                traceback.print_exc()
                errors.append(f"{site['name']}: {e}")
                continue
            self._online_linked(item, key, site)
            return site, found
        raise OnlineError("; ".join(errors) or "no video site in Settings has it", choices, page)

    RACE_WAIT = 5        # seconds the first site gets, once another has the video

    def _online_race(self, item, key, season, episode, track, sites, fresh=False):
        """No site chosen for it: every site searched at once. The first in order is played if
        it has the video; another that has it already is played if the first takes more than
        RACE_WAIT seconds longer (or doesn't have it). Only one is ever played."""
        results, cond = {}, threading.Condition()

        def search(site):
            try:
                got = ("ok", self.scraper.resolve(site, item, season, episode, track=track, fresh=fresh))
            except scrape.ScrapeError as e:
                got = ("error", e)
            except Exception as e:  # noqa: BLE001 - one site's trouble isn't the others'
                got = ("error", scrape.ScrapeError(str(e)))
            with cond:
                results[site["id"]] = got
                cond.notify_all()
        for site in sites:
            threading.Thread(target=search, args=(site,), daemon=True).start()
        first, other_since, deadline = sites[0]["id"], None, time.time() + 240
        winner = None
        with cond:
            while winner is None and time.time() < deadline:
                head = results.get(first)
                found = [x for x in sites if (results.get(x["id"]) or ("",))[0] == "ok"]
                if head and head[0] == "ok":
                    winner = sites[0]
                elif found and (head is not None or (other_since and time.time() - other_since >= self.RACE_WAIT)):
                    winner = found[0]
                elif len(results) == len(sites):
                    break
                else:
                    if found and other_since is None:
                        other_since = time.time()
                    cond.wait(0.25)
        if winner is not None:
            got = results[winner["id"]][1]
            self._online_linked(item, key, winner)
            return winner, got
        errors, choices, page = [], None, ""
        for site in sites:
            got = results.get(site["id"])
            if not got or got[0] != "error":
                continue
            e = got[1]
            errors.append(f"{site['name']}: {e}")
            page = page or getattr(e, "page", "")
            if getattr(e, "choices", None) and choices is None:
                choices = {"site": site["id"], "site_name": site["name"], "choices": e.choices, "question": str(e)}
        raise OnlineError("; ".join(errors) or "none of your sites had it in time", choices, page)

    def _online_linked(self, item, key, site):
        """One link per episode: the site that played it. A link to another site goes."""
        links = dict(item.get("online_links") or {})
        old = links.get(key)
        if old == site["id"]:
            return
        if old:
            gone = next((x for x in self.settings.get().get("video_sites") or [] if x["id"] == old), None)
            season, episode = self._key_numbers(key)
            if gone and episode is not None:
                self.scraper.unlink_episode(item["id"], gone, season, episode)
        links[key] = site["id"]
        try:
            self.store.update(item["id"], {"online_links": links})
        except KeyError:
            pass

    def _episode_keys(self, item, around=None):
        """Every episode of a series, in order ("season:episode"), as far as it's known."""
        keys = []
        for s in sorted(item.get("seasons") or [], key=lambda x: x.get("number") or 0):
            if (s.get("number") or 0) > 0:
                keys += [f"{s['number']}:{e}" for e in range(1, (s.get("episodes") or 0) + 1)]
        if not keys:
            total = item.get("episodes_total") or 0
            last = max(total, (around[1] + 12) if around else 12)
            keys = [f"{(around or (1, 1))[0]}:{e}" for e in range(1, last + 1)]
        if around and f"{around[0]}:{around[1]}" not in keys:
            keys.append(f"{around[0]}:{around[1]}")
            keys.sort(key=lambda k: tuple(int(x) for x in k.split(":")))
        return keys

    def _online_url(self, item, key, site, track):
        season, episode = self._key_numbers(key)
        return (f"http://127.0.0.1:{self.torrents.port}/online/{self.torrents.token}/{item['id']}/{season}/{episode}"
                f"?{urllib.parse.urlencode({'site': site['id'], 'track': track or ''})}")

    def play_scraped(self, item, body):
        """Find the episode (or film) on your video sites and play it: the first site
        that has it, in the player (with the season around it as its playlist, each
        episode found as it opens) or its page in the browser."""
        if not self._video_sites(item):
            raise ApiError(f"add your own video site for {KIND_WORDS.get(item['type'], 'this')} in Settings first")
        series = item["format"] == "series"
        season = max(0, int(body.get("season") or item["progress"]["season"] or 1)) if series else None
        episode = max(1, int(body.get("episode") or item["progress"]["episode"] or 1)) if series else None
        key = f"{season}:{episode}" if series else "movie"
        run = str(body.get("run") or "")
        with self._lock:
            self._scrape_runs[item["id"]] = run
        track = str(body.get("track") or "")
        if not track and body.get("stream"):
            track = (str(body["stream"]).split(":") + ["", ""])[1]
        try:
            site, found = self._episode_video(item, season, episode, track=track, prefer=str(body.get("site") or ""),
                                              pin=str(body.get("entry") or ""))
        except OnlineError as e:
            if body.get("open_page") and e.page:
                self.open_link(e.page)
                return {"ok": True, "player": "browser", "page": e.page, "note": str(e)}
            return {"ok": False, "error": str(e), "choices": e.choices, "page": e.page, "key": key}
        with self._lock:
            if run and self._scrape_runs.get(item["id"]) != run:
                return {"ok": False, "superseded": True, "site": site["name"], "error": "skipped"}
        result = {"ok": True, "site": site["name"], "site_id": site["id"], "entry": found["entry"], "page": found["page"],
                  "key": key, "track": (found.get("stream") or {}).get("kind") or "sub", "stream": found.get("stream")}
        patch = {"last_played": time.time()}
        if series:
            patch["progress"] = {"season": max(1, season), "episode": episode}
        browser = body.get("open") == "browser" or site.get("open") == "browser"
        if not found["url"] or browser:
            self.open_link(found["page"])
            self.store.update(item["id"], patch)
            return dict(result, player="browser")
        entry = self._web_entry(item, site, key, found)
        start = 0 if body.get("from_start") else player.resume_point(item["positions"].get(key))
        with self._party_lock:
            hosting = self.party if self.party and self.party.active and self.party.role == "host" else None
        if hosting:
            self._party_now(hosting, entry, upcoming=True)      # guests start finding it now
        hold = bool(hosting and any(p != hosting.me for p in hosting.people))
        web = self._web_device()
        if web:
            # A phone over the network: the whole season in its own player, each episode found as it opens.
            entries, first = self._online_playlist(item, site, entry)
            session = self.web_session(web, item, entries, start, first, paused=hold)
            self._online_found[(item["id"], key)] = (site, found, time.time())
            if hosting:
                session.party = True
                hosting.attach(session, hold=hold)
                self._party_now(hosting, entry)
            self.store.update(item["id"], patch)
            if series:
                threading.Thread(target=self._link_around, args=(item["id"], site, key, session), daemon=True).start()
            return dict(result, **session.launch(), player="web", resumed_at=start)
        if launcher.ANDROID:
            # The whole season in the phone's player too, each episode found as it opens.
            entries, first = self._online_playlist(item, site, entry)
            session = phone_player.PhoneSession(item["id"], entries, start, paused=hold, anime=item["type"] == "anime",
                                                index=first)
            self._online_found[(item["id"], key)] = (site, found, time.time())
            if hosting:
                session.party = True
                hosting.attach(session, hold=hold)
                self._party_now(hosting, entry)
            self.store.update(item["id"], patch)
            if series:
                threading.Thread(target=self._link_around, args=(item["id"], site, key, session), daemon=True).start()
            return dict(result, **session.launch(), player="android", resumed_at=start)
        mpv = player.find_mpv()
        if not mpv:
            self.open_link(found["page"])
            self.store.update(item["id"], patch)
            return dict(result, player="browser")
        tried, refreshed, skip, streams = [], False, [], []
        while True:
            entries, first = self._online_playlist(item, site, entry)
            session = self.player.play(self._session(mpv, item, entries, start, ["--pause"] if hold else (),
                                                     first=first, playlist=series))
            self._online_found[(item["id"], key)] = (site, found, time.time())
            started, why = self._web_started(session)
            if started:
                break
            got = None
            if found.get("cached") and not refreshed:
                refreshed = True             # a video found earlier whose address ran out: found again
                try:
                    got = self.scraper.resolve(site, item, season, episode, track=track, fresh=True)
                except scrape.ScrapeError:
                    pass
            else:
                label = (found.get("stream") or {}).get("label") or site["name"]
                tried.append(f"{label}: {why}")
                if len(tried) >= 4:
                    return dict(result, ok=False, error="the player couldn't play it - " + "; ".join(tried), tried=tried)
                if found.get("stream"):
                    # The site's next server first (like trying it by hand)...
                    streams.append(found["stream"]["id"])
                    try:
                        got = self.scraper.resolve(site, item, season, episode, track=track, skip=tuple(streams))
                    except scrape.ScrapeError:
                        pass
            if got:
                found = got
                self._online_found[(item["id"], key)] = (site, found, time.time())
            else:
                self._online_found.pop((item["id"], key), None)
                # ...then the next site.
                skip.append(site["id"])
                streams = []
                try:
                    site, found = self._online_find(item, season, episode, track=track, skip=skip)
                except OnlineError as e:
                    return dict(result, ok=False, error="; ".join(tried + [str(e)]), tried=tried, choices=e.choices)
            entry = self._web_entry(item, site, key, found)
            result.update(site=site["name"], site_id=site["id"], entry=found["entry"], page=found["page"],
                          stream=found.get("stream"))
        if hosting:
            hosting.attach(session, hold=hold)
            self._party_now(hosting, entry)
        self.store.update(item["id"], patch)
        session.listeners.append(lambda sess, msg: self._online_event(sess, item["id"], msg))
        self._online_extras(session, item["id"], entry)
        if series:
            self._link_around(item["id"], site, key, session)
        return dict(result, player="mpv", resumed_at=start, file=entry["name"], tried=tried)

    def _online_playlist(self, item, site, entry):
        """The player's playlist: every episode of the series, the one found first and the
        others as this app's links (each found when it opens, or found already)."""
        if item["format"] != "series":
            return [entry], 0
        season, episode = self._key_numbers(entry["key"])
        keys = self._episode_keys(item, (season, episode))
        out = []
        for k in keys:
            if k == entry["key"]:
                out.append(entry)
            else:
                out.append({"path": self._online_url(item, k, site, entry.get("track")), "key": k, "web": True,
                            "online": True, "label": self._label(item, k), "referer": entry["referer"],
                            "headers": entry["headers"], "track": entry.get("track"), "site": site["id"],
                            # (a watch party's guests find it on the same site, the same version)
                            "party": dict(self._party_meta(item, k), track=entry.get("track") or "",
                                          site={"name": site["name"], "url": site["url"]},
                                          entry=(entry.get("party") or {}).get("entry") or "")})
        return out, keys.index(entry["key"])

    def online_video(self, item_id, season, episode, site_id, track):
        """The address of an episode's video, for the player's playlist (found now, or
        found a moment ago in the background). Raises OnlineError / KeyError."""
        item = self.store.get(item_id)
        key = f"{season}:{episode}"
        track = self._online_track.get(item_id) or track
        with self._party_lock:
            hosting = self.party if self.party and self.party.active and self.party.role == "host" else None
        if hosting:
            site_now = next((x for x in self._video_sites(item) if x["id"] == site_id), None)
            if site_now:
                meta_entry = {"label": self._label(item, key),
                              "party": dict(self._party_meta(item, key), track=track or "",
                                            site={"name": site_now["name"], "url": site_now["url"]})}
                self._party_now(hosting, meta_entry, upcoming=True)
        site, found = self._episode_video(item, season, episode, track=track, prefer=site_id)
        playing = phone_player.current() if launcher.ANDROID else self.player.session
        threading.Thread(target=self._link_around, args=(item_id, site, key, playing), daemon=True).start()
        return self._playable(found)

    def _playable(self, found):
        """The address the player gets for a video found on a site: its own, or this app's
        when its pieces are dressed up as pictures (hlsfix.py)."""
        if hlsfix.needs_fix(found["url"], found.get("headers"), found.get("referer")):
            return hlsfix.local_url(f"http://127.0.0.1:{self.torrents.port}/hls/{self.torrents.token}",
                                    found["url"], found.get("headers"), found.get("referer"))
        return found["url"]

    def _online_event(self, session, item_id, msg):
        """The player opened another episode: its subtitles, where it's from, its other version."""
        if msg.get("event") != "file-loaded":
            return
        entry = session.current()
        if entry and entry.get("online"):
            self._online_extras(session, item_id, entry)

    def _online_extras(self, session, item_id, entry):
        got = self._online_found.get((item_id, entry["key"]))
        if not got:
            return
        site, found, _ = got
        session.send(["set_property", "user-data/watchlist/source", f"{site['name']}: {found.get('page') or ''}"])
        try:
            mine = self.saved_sub(self.store.get(item_id), entry["key"])
        except KeyError:
            mine = ""
        if mine and not entry.get("added_mine"):
            entry["added_mine"] = True
            session.add_tracks(entry, [{"kind": "sub", "url": mine, "title": "Your subtitles", "lang": "en", "select": True}])
        if entry.get("online") and found.get("subtitle") and not entry.get("added_sub"):
            entry["added_sub"] = True
            dub = (found.get("stream") or {}).get("kind") == "dub"
            session.add_tracks(entry, [{"kind": "sub", "url": found["subtitle"], "title": "English (subtitles)",
                                        "lang": "en", "select": not dub}])
        # (The other version isn't laid over it as a second audio track - a dub's timing differs
        # from the sub's; the player's Sub / Dub button switches to it from where you are.)

    def _link_around(self, item_id, site, key, session=None):
        """Match the title's episodes on the site in the background, from the one playing:
        the next one, the one before it, then on to the end, then back to the start. The
        next and previous videos themselves are found too (ready when you skip to them).
        Only the latest run for a title goes on."""
        with self._lock:
            gen = self._link_runs[item_id] = self._link_runs.get(item_id, 0) + 1
        try:
            item = self.store.get(item_id)
        except KeyError:
            return
        if item["format"] != "series":
            return
        keys = self._episode_keys(item, self._key_numbers(key) if ":" in key else None)
        i = keys.index(key) if key in keys else 0
        order = [k for k in [keys[i + 1] if i + 1 < len(keys) else None, keys[i - 1] if i > 0 else None] if k]
        order += [k for k in keys[i + 2:]] + [k for k in reversed(keys[:max(0, i - 1)])]
        progress = self._link_progress[item_id] = {"done": 0, "total": len(order), "running": True, "site": site["name"]}
        misses = 0
        try:
            for n, k in enumerate(order):
                if self._link_runs.get(item_id) != gen:
                    return
                season, episode = self._key_numbers(k)
                track = (session.current() or {}).get("track") if session else ""
                try:
                    if n < 2 and session and not session.ended:
                        self._episode_video(item, season, episode, track=track or "", prefer=site["id"])
                    else:
                        self.scraper.episode_page(site, item, season, episode)
                        self._online_linked(item, k, site)
                    misses = 0
                except (scrape.ScrapeError, OnlineError):
                    misses += 1
                    if misses >= 4 and n >= 2:
                        # (past the last episode out, going forward): on to the earlier ones
                        before = [x for x in order[n + 1:] if keys.index(x) < i]
                        if not before:
                            return
                except KeyError:
                    return
                progress["done"] = n + 1
        finally:
            progress["running"] = False

    def link_all(self, item, body):
        """Match every episode on a site now (without watching), in the background."""
        sites = self._video_sites(item)
        if not sites:
            raise ApiError("add your own video site in Settings first")
        site = next((x for x in sites if x["id"] == body.get("site")), None) or self._online_order(item, "")[0]
        if body.get("entry"):
            self.scraper.pin_entry(item, site, str(body["entry"]))
        if body.get("fresh"):
            self.scraper.unlink(item["id"], site)
            self.store.update(item["id"], {"online_links": {}})
        if item["format"] != "series":
            def one():
                try:
                    self.scraper.episode_page(site, item, None, None)
                    self._online_linked(item, "movie", site)
                except scrape.ScrapeError:
                    pass
            threading.Thread(target=one, daemon=True).start()
            return {"ok": True, "site": site["name"]}
        start = f"{item['progress']['season'] or 1}:{max(1, (item['progress']['episode'] or 1))}"
        threading.Thread(target=self._link_around_first, args=(item, site, start), daemon=True).start()
        return {"ok": True, "site": site["name"]}

    def _link_around_first(self, item, site, start):
        season, episode = self._key_numbers(start)
        try:
            self.scraper.episode_page(site, item, season, episode)
            self._online_linked(item, start, site)
        except scrape.ScrapeError as e:
            self._link_progress[item["id"]] = {"done": 0, "total": 0, "running": False, "site": site["name"],
                                              "error": str(e), "choices": e.choices}
            return
        self._link_around(item["id"], site, start)

    def online_links(self, item):
        """What each episode is linked to, for the title's page."""
        sites = {x["id"]: x for x in self._video_sites(item)}
        eps = {}
        for sid, site in sites.items():
            for k, page in self.scraper.linked_episodes(item["id"], site).items():
                if (item.get("online_links") or {}).get(k, sid) == sid:
                    eps[k] = {"site": sid, "site_name": site["name"], "page": page}
        mem_titles = {sid: (self.scraper.remembered(item["id"], site) or {}) for sid, site in sites.items()}
        return {"sites": [{"id": x["id"], "name": x["name"], "open": x.get("open") or "player"} for x in sites.values()],
                "preferred": item.get("online_site") or "", "keep": bool(item.get("keep_site")),
                "per_episode": item.get("online_site_eps") or {}, "episodes": eps,
                "titles": {sid: {"entries": m.get("entries") or {}, "others": m.get("others") or []}
                           for sid, m in mem_titles.items() if m},
                "linking": self._link_progress.get(item["id"]) or {}}

    def relink_episode(self, item, body):
        """A wrong episode: matched again on a site (or set to the page you give)."""
        season, episode = int(body.get("season") or 1), int(body.get("episode") or 1)
        key = f"{season}:{episode}" if item["format"] == "series" else "movie"
        sites = self._video_sites(item)
        sid = body.get("site") or (item.get("online_links") or {}).get(key) or (self._online_order(item, key)[0]["id"] if sites else "")
        site = next((x for x in sites if x["id"] == sid), None)
        if not site:
            raise ApiError("that video site isn't in Settings any more")
        if body.get("entry"):
            self.scraper.pin_entry(item, site, str(body["entry"]))
        if item["format"] == "series":
            self.scraper.unlink_episode(item["id"], site, season, episode)
        self._online_found.pop((item["id"], key), None)
        if body.get("page"):
            page = str(body["page"]).strip()
            if not page.lower().startswith(("http://", "https://")):
                raise ApiError("paste the episode's page link (it starts with https://)")
            self.scraper.set_episode(item, site, season, episode, page)
        else:
            try:
                page = self.scraper.episode_page(site, item, season if item["format"] == "series" else None,
                                                 episode if item["format"] == "series" else None)[0]
            except scrape.ScrapeError as e:
                return {"ok": False, "error": str(e), "choices": e.choices}
        self._online_linked(item, key, site)
        return {"ok": True, "site": site["name"], "page": page}

    def set_online_prefs(self, item, body):
        """The title's preferred site, one episode's site, keep connected, or forget every link."""
        patch = {}
        sites = {x["id"] for x in self._video_sites(item)}
        if "preferred" in body:
            sid = str(body["preferred"] or "")
            if sid and sid not in sites:
                raise ApiError("that video site isn't in Settings any more")
            patch["online_site"] = sid
        if "episode" in body:
            per = dict(item.get("online_site_eps") or {})
            k = str(body["episode"])
            if body.get("site"):
                per[k] = str(body["site"])
            else:
                per.pop(k, None)
            patch["online_site_eps"] = per
            self._online_found.pop((item["id"], k), None)     # (found on the other site: found again)
        if "keep" in body:
            patch["keep_site"] = bool(body["keep"])
        if body.get("forget"):
            self.scraper.forget(item["id"])
            patch.update(online_links={}, online_site_eps={})
            for k in [k for k in self._online_found if k[0] == item["id"]]:
                self._online_found.pop(k, None)
        return self.store.update(item["id"], patch) if patch else item

    def _clean_leftovers(self):
        try:
            self.torrents.clean_leftovers()
        except Exception:  # noqa: BLE001 - tried again next start
            pass

    def _player_exited(self, session, *_):
        """The player was closed: its torrents stop downloading - unless another player is
        already playing (you went straight on to something else)."""
        playing = phone_player.current() if launcher.ANDROID else self.player.session
        if playing is not None and playing is not session and not playing.ended:
            return
        self.torrents.stop_streaming()

    @staticmethod
    def _key_numbers(key):
        """(season, episode) of a player entry's key ("1:3"); (None, None) for a film."""
        try:
            season, episode = (int(x) for x in str(key).split(":")[:2])
            return season, episode
        except ValueError:
            return None, None

    def clear_connections(self, item, body):
        """Everything a title is connected to, undone: its links to your own sites, the folder or
        file you chose for it, and its torrent (with what it downloaded)."""
        self.scraper.forget(item["id"])
        for k in [k for k in self._online_found if k[0] == item["id"]]:
            self._online_found.pop(k, None)
        self._online_track.pop(item["id"], None)
        # Its torrents go - and what they downloaded, unless "Keep downloaded episodes" is ticked
        # (then the finished episodes stay, as your downloads).
        self.torrents.replace_for(item["id"], "", keep_done=bool(self.torrents.keep.get(item["id"])))
        return self.store.update(item["id"], {"online_links": {}, "online_site_eps": {}, "local_folder": "",
                                              "local_file": "", "local_broken": False, "picked": {}})

    # ---- finding a video yourself (picker.py)

    def pick_start(self, item, body):
        """Open the episode (or film) on one of your sites in a browser the app watches."""
        sites = self._video_sites(item)
        if not sites:
            raise ApiError("add your own video site in Settings first")
        series = item["format"] == "series"
        season = max(1, int(body.get("season") or 1)) if series else None
        episode = max(1, int(body.get("episode") or 1)) if series else None
        key = f"{season}:{episode}" if series else "movie"
        site = next((x for x in sites if x["id"] == body.get("site")), None) or self._online_order(item, key)[0]
        track = "dub" if body.get("track") == "dub" else ("sub" if item["type"] == "anime" else "")
        url = ""
        if series:
            url = (self.scraper.linked_episodes(item["id"], site) or {}).get(key) or ""
        if not url:
            url = scrape.search_url(site, item["title"]) if site.get("kind") not in ("archive", "json_api") else site["url"]
        webtools.ADBLOCK = self.settings.get()["adblock"]
        try:
            job = picker.start(item["id"], season, episode, site["id"], url,
                               list_url=f"http://127.0.0.1:{self.torrents.port}/")
        except RuntimeError as e:
            raise ApiError(str(e)) from e
        job.track = track
        return dict(job.view(), site=site["name"], track=track, key=key)

    def pick_view(self, job_id):
        job = picker.get(job_id)
        if not job:
            raise ApiError("that browser window has gone", HTTPStatus.NOT_FOUND)
        try:
            picked = self.store.get(job.item_id).get("picked") or {}
        except KeyError:
            picked = {}
        return dict(job.view(), picked=picked, track=getattr(job, "track", ""))

    def pick_use(self, job_id, body):
        """The video you chose: this episode is connected to it (and the rest the same way),
        and the site is remembered for next time."""
        job = picker.get(job_id)
        if not job:
            raise ApiError("that browser window has gone", HTTPStatus.NOT_FOUND)
        entry = next((v for v in job.videos if v["n"] == int(body.get("n") or 0)), None)
        if not entry:
            raise ApiError("that video isn't in the list")
        item = self.store.get(job.item_id)
        track = getattr(job, "track", "") or ""
        series = item["format"] == "series"
        key = f"{job.season}:{job.episode}" if series else "movie"
        # Every version of it the page loaded: the one that really plays is used (the one listing
        # every quality first, so the player can choose), else the one you picked.
        entry = next((v for v in picker.versions(job, entry) if picker.plays(v)), entry)
        headers = entry["headers"]
        page = entry["page"] or job.page
        found = {"url": entry["url"], "referer": headers.get("Referer") or page,
                 "headers": {k: v for k, v in headers.items() if k in ("Origin", "User-Agent", "Cookie")},
                 "subtitle": scrape.pick_subtitle(list(job.subs), headers) if track != "dub" else None,
                 "page": page, "entry": {"url": page, "name": item["title"]},
                 "stream": {"kind": track or "sub", "label": entry["frame"] or entry["host"]}}
        # The site it's on: one of yours (the page's server), else the one it was opened on.
        sites = self._video_sites(item)
        host = urllib.parse.urlsplit(page).netloc.lower().removeprefix("www.")
        site = next((x for x in sites if urllib.parse.urlsplit(x["url"]).netloc.lower().removeprefix("www.") == host), None) \
            or next((x for x in sites if x["id"] == job.site_id), None)
        if not site:
            raise ApiError("that video site isn't in Settings any more")
        # Learned for the site (for every title on it).
        recipe = picker.recipe(job, entry)
        for h in {urllib.parse.urlsplit(site["url"]).netloc.lower(), urllib.parse.urlsplit(page).netloc.lower()}:
            if h:
                webtools.LEARNED[h] = recipe
        write_json(self._learned_path, webtools.LEARNED)
        # This episode's page, kept until Delete connections.
        if series:
            self.scraper.set_episode(item, site, job.season, job.episode, page)
        else:
            self.scraper.pin_entry(item, site, page)
            self.scraper._remember(item["id"], site, movie_page={"entry": page, "url": page})
        self._online_linked(item, key, site)
        self._online_found[(item["id"], key)] = (site, found, time.time())
        if track:
            self._online_track[item["id"]] = track
        picked = dict(item.get("picked") or {})
        picked[track or "video"] = {"site": site["id"], "site_name": site["name"], "key": key, "page": page,
                                    "from": entry["frame"] or entry["host"], "at": time.time()}
        self.store.update(item["id"], {"picked": picked})
        if series:
            # The other episodes, connected the same way (in the background).
            threading.Thread(target=self._link_around_first, args=(item, site, key), daemon=True).start()
        job.close()                       # (found: the browser and its list close)
        return {"ok": True, "picked": picked, "site": site["name"], "used": entry["n"],
                "quality": entry.get("quality") or ""}

    def pick_close(self, job_id):
        job = picker.get(job_id)
        if job:
            job.close()
        return {"ok": True}

    def scraped_unlink(self, item, body):
        """Forget the title found on a site (a wrong match, or to start over)."""
        site = next((x for x in self.settings.get().get("video_sites") or [] if x["id"] == body.get("site")), None)
        if not site:
            raise ApiError("that video site isn't in Settings any more")
        self.scraper.unlink(item["id"], site)
        return {"ok": True}

    @staticmethod
    def _web_sub(found):
        """The subtitles the site's player loaded: shown with the sub track, not over a dub."""
        if (found.get("stream") or {}).get("kind") == "dub":
            return None
        return found.get("subtitle") or None

    @staticmethod
    def _web_started(session, wait=12):
        """(True, "") once a video from a site plays (or is still loading after
        `wait` seconds); (False, why) when the player closed without playing."""
        deadline = time.time() + wait
        while time.time() < deadline:
            if session.pos is not None:
                return True, ""
            if session.ended:
                return False, player.why_it_stopped(session) or "the stream refused to play"
            time.sleep(0.25)
        return True, ""

    def _web_entry(self, item, site, key, found):
        """A player entry for a video found on a site: its subtitles, and what a watch party needs."""
        track = (found.get("stream") or {}).get("kind") or "sub"
        entry = {"path": self._playable(found), "sub": self._web_sub(found), "key": key, "web": True, "referer": found["referer"],
                 "headers": found.get("headers") or {}, "track": track,
                 "label": self._label(item, key), "name": f"{item['title']} ({site['name']})"}
        if found.get("subtitle") and not entry["sub"]:
            # A dub's subtitles: there to switch on, not shown over it.
            entry["extras"] = [{"kind": "sub", "url": found["subtitle"], "title": "English (subtitles)", "lang": "en"}]
        shared = {k: v for k, v in (found.get("headers") or {}).items() if k in ("Origin", "User-Agent")}  # (never cookies)
        entry["party"] = dict(self._party_meta(item, key), url=found["url"], referer=found["referer"],
                              headers=shared, sub=found.get("subtitle") or "", track=track,
                              stream={k: v for k, v in (found.get("stream") or {}).items() if k in ("id", "label", "kind")},
                              site={"name": site["name"], "url": site["url"]},
                              entry=(found.get("entry") or {}).get("url") or "")
        return entry

    def _render_page(self, url):
        webtools.ADBLOCK = self.settings.get()["adblock"]
        return webtools.render(url)

    def _catch_video(self, url, click=None):
        return webtools.catch_video(url, click=click)

    def _party_online_entry(self, item, meta, report):
        """The host plays from a video site: the same video if it plays here too, else the
        episode found on that site by this app (the title the host has there, the same
        version), with its progress told to the room. The next episode is found meanwhile."""
        source = syncplay.online_source(meta)
        series = item["format"] == "series"
        season, episode = (meta.get("season") or 1, meta.get("episode") or 1) if series else (None, None)
        key = f"{season}:{episode}" if series else "movie"
        label = meta.get("label") or self._label(item, key)
        report("preparing", 0.05, None, f"{label} - finding it on {source['name']}")
        site = self._party_site(source, item)
        track = meta.get("track") or ""
        report("preparing", 0.2, None, f"{label} - opening it on {source['name']}")
        # Found by this app, from here: a site's video link is often good only for the computer
        # that found it (Atlantic: "error code 14"), so the host's is the last resort.
        found = None
        try:
            pin = "" if self.scraper.remembered(item["id"], site) else str(meta.get("entry") or "")
            want = meta.get("stream") or {}
            if want.get("label") and not str(want.get("id") or "").startswith(("lang:", "switch:")):
                # The host's own server (another server is often another copy, seconds apart).
                try:
                    found = self.scraper.resolve(site, item, season, episode, pin=pin, remember=False,
                                                 stream=f"{want.get('id') or 'x'}:{want.get('kind') or track}:{want['label']}")
                    self._online_found[(item["id"], key)] = (site, found, time.time())
                except scrape.ScrapeError:
                    found = None
            if found and found.get("url"):
                pass
            elif pin:
                found = self.scraper.resolve(site, item, season, episode, pin=pin, track=track)
                self._online_found[(item["id"], key)] = (site, found, time.time())
            else:
                found = self._episode_video(item, season, episode, track=track, prefer=site["id"])[1]
        except (scrape.ScrapeError, OnlineError):
            found = None
        url = str(meta.get("url") or "")
        if not (found and found.get("url")) and url.startswith(("http://", "https://")) \
                and not scrape.scrambled_playlist(url, dict(meta.get("headers") or {}, Referer=meta.get("referer") or "")):
            found = {"url": url, "referer": meta.get("referer") or "", "headers": meta.get("headers") or {},
                     "subtitle": meta.get("sub") or "", "stream": {"kind": track or "sub"}}
        if not (found and found.get("url")):
            return None
        self._online_found[(item["id"], key)] = (site, found, time.time())
        self._online_linked(item, key, site)
        if track:
            self._online_track[item["id"]] = track
        report("preparing", 0.9, None, label)
        entry = dict(self._web_entry(item, site, key, found), online=True, site=site["id"])
        entry["item_id"] = item["id"]
        if series:
            # The next episode found now (ready when the host moves on), the others linked.
            threading.Thread(target=self._prefetch_online, args=(site, item, season, episode + 1, meta),
                             daemon=True).start()
        return entry

    def _party_site(self, source, item):
        """The host's video site, as one of this app's own (added to your sites if it isn't one,
        so everything works the same for you: Sub / Dub, its episodes linked, playing on later)."""
        sites = self.settings.get().get("video_sites") or []
        host = urllib.parse.urlsplit(source["url"]).netloc.lower().removeprefix("www.")
        mine = next((x for x in sites if x["url"] == source["url"]), None) or \
            next((x for x in sites if urllib.parse.urlsplit(x["url"]).netloc.lower().removeprefix("www.") == host), None)
        if mine:
            if item["type"] not in mine.get("types", []) or not mine.get("on", True):
                changed = [dict(x, types=sorted(set(x.get("types") or []) | {item["type"]}), on=True) if x is mine else x
                           for x in sites]
                try:
                    self.settings.update({"video_sites": changed})
                except (ValueError, TypeError):
                    pass
                mine = next((x for x in self.settings.get().get("video_sites") or [] if x["id"] == mine["id"]), mine)
            return mine
        new = scrape.clean([{"name": source["name"], "url": source["url"], "types": [item["type"]]}])[0]
        try:
            self.settings.update({"video_sites": [*sites, new]})
            return next((x for x in self.settings.get().get("video_sites") or [] if x["url"] == new["url"]), new)
        except (ValueError, TypeError):
            return new

    def _prefetch_online(self, site, item, season, episode, meta):
        total = item.get("episodes_total") or 0
        if total and episode > total:
            return
        try:
            self._episode_video(item, season, episode, track=meta.get("track") or "", prefer=site["id"])
        except (scrape.ScrapeError, OnlineError):
            pass
        # ...and the rest of the episodes linked on the site, as when you play it yourself.
        self._link_around(item["id"], site, f"{season}:{max(1, episode - 1)}")

    def _party_url_entry(self, item, meta):
        """The host plays a video from their own site: the guest streams the same one."""
        if not str(meta.get("url") or "").lower().startswith(("http://", "https://")):
            return None
        series = item["format"] == "series"
        key = f'{meta.get("season") or 1}:{meta.get("episode") or 1}' if series else "movie"
        return {"path": meta["url"], "sub": None, "key": key, "web": True, "referer": meta.get("referer") or "",
                "headers": {k: str(v) for k, v in (meta.get("headers") or {}).items() if k in ("Origin", "User-Agent", "Cookie")},
                "label": self._label(item, key), "party": self._party_meta(item, key), "item_id": item["id"]}

    def streaming_on(self, item):
        """Where TMDB says a title streams in your country: ([names], message)."""
        s = self.settings.get()
        if not s["tmdb_api_key"]:
            return [], "No TMDB token in Settings, so which services have it couldn't be checked."
        try:
            tmdb = metadata.TMDB(s["tmdb_api_key"])
            ref = item["tmdb_ref"]
            if not ref and item["type"] == "anime":
                ref = tmdb.find_anime(item["aliases"] or [item["title"]], item["year"], item["format"])
                if ref:
                    self.store.update(item["id"], {"tmdb_ref": ref})
            if not ref:
                return [], "Couldn't match this anime on TMDB to check which services have it."
            providers = tmdb.providers(ref, s["region"])
            return [p["name"] for p in providers["services"] if p["kind"] in ("flatrate", "free", "ads")], ""
        except metadata.MetadataError as e:
            return [], f"Couldn't check which services have it: {e}."

    def ai_streams_on(self, item, names):
        """{service name: has it} from your AI helper, remembered for a day. ({}, message) when it can't say."""
        s = self.settings.get()
        if not names:
            return {}, ""
        if not ai.available(s):
            return {}, "No AI helper is set up, so the AI check was skipped."
        key = (item["id"], s["region"], tuple(sorted(names)))
        with self._lock:
            hit = self._ai_streams.get(key)
        if hit and time.time() - hit[0] < 86400:
            return hit[1], ""
        try:
            found = ai.streams_on(s, item, s["region"], sorted(names))
        except ai.AIError as e:
            return {}, f"The AI check didn't answer: {e}."
        with self._lock:
            self._ai_streams[key] = (time.time(), found)
        return found, ""

    def online_options(self, item):
        """Your services for this kind of title, in order, each with whether it has
        it (by its own check: TMDB, AI or none)."""
        s = self.settings.get()
        mine = [x for x in s["services"] if x["on"] and item["type"] in x["types"]]
        modes = {x["id"]: services.mode_of(x) for x in mine}
        names, message = self.streaming_on(item) if "tmdb" in modes.values() else ([], "")
        by_ai, ai_message = self.ai_streams_on(item, [x["name"] for x in mine if modes[x["id"]] == "ai"])
        options = []
        for x in mine:
            mode = modes[x["id"]]
            has = (services.has_it(x, names) if mode == "tmdb" and not message
                   else by_ai.get(x["name"]) if mode == "ai" and x["name"] in by_ai else None)
            options.append({"id": x["id"], "name": x["name"], "check_mode": mode,
                            "kind": "search" if mode == "off" else "check", "has_it": has})
        return {"region": s["region"], "streaming": names, "message": " ".join(m for m in (message, ai_message) if m),
                "options": options}

    def watch_online(self, item, service_id=None):
        """Open a service: the one asked for, or the first in your list that fits (one
        with a check only when the check says it has the title). Nothing fits: nothing is opened."""
        s = self.settings.get()
        title = item["title"]
        info = self.online_options(item)
        result = {"region": s["region"], "services": info["streaming"], "message": info["message"],
                  "service": "", "url": "", "kind": ""}
        by_id = {x["id"]: x for x in s["services"]}
        chosen = None
        if service_id:
            chosen = by_id.get(service_id)
            if not chosen:
                raise ApiError("that service isn't in your list any more")
        else:
            option = next((o for o in info["options"] if o["kind"] == "search" or o["has_it"]), None)
            chosen = by_id[option["id"]] if option else None
        if not chosen:
            result["kind"] = "none"
            result["message"] = ((info["message"] + " ") if info["message"] else "") + (
                "None of your services has it here. Pick one yourself, or add a site in Settings."
                if info["options"] else f"You haven't added a site for {KIND_WORDS.get(item['type'], 'this')} yet: add one in Settings.")
            return result
        option = next((o for o in info["options"] if o["id"] == chosen["id"]), {})
        result.update(service=chosen["name"], url=services.fill(chosen, title),
                      kind=option.get("kind") or ("search" if services.mode_of(chosen) == "off" else "check"),
                      has_it=bool(option.get("has_it")))
        self.open_link(result["url"])
        self.store.update(item["id"], {"last_played": time.time()})
        with self._party_lock:
            party = self.party
        if party and party.active and party.role == "host":
            party.share_link(result["url"], f'{item["title"]} on {result["service"]}')
            result["party"] = True
        return result


class Handler(BaseHTTPRequestHandler):
    server_version = "WatchAnything"
    app: App = None  # set by make_server

    ROUTES = [
        ("GET", r"/api/ping", "ping"),
        ("GET", r"/api/state", "state"),
        ("GET", r"/api/version", "version"),
        ("POST", r"/api/pair/request", "pair_request"),
        ("GET", r"/api/web/current", "web_current"),
        ("POST", r"/api/web/(\w+)/open", "web_open"),
        ("POST", r"/api/web/(\w+)/state", "web_state"),
        ("POST", r"/api/web/(\w+)/seek", "web_seek"),
        ("POST", r"/api/web/(\w+)/source", "web_source"),
        ("POST", r"/api/web/(\w+)/version", "web_version"),
        ("GET", r"/api/web/(\w+)/servers", "web_servers"),
        ("POST", r"/api/web/(\w+)/server", "web_server"),
        ("POST", r"/api/web/(\w+)/other-site", "web_other_site"),
        ("GET", r"/api/pair/status", "pair_status"),
        ("POST", r"/api/pair/answer", "pair_answer"),
        ("GET", r"/api/phone", "phone_status"),
        ("GET", r"/api/phone/me", "phone_me"),
        ("PUT", r"/api/phone/devices/(\w+)", "phone_device"),
        ("DELETE", r"/api/phone/devices/(\w+)", "phone_device_remove"),
        ("POST", r"/api/phone/remove-all", "phone_remove_all"),
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
        ("GET", r"/api/items/(\w+)/online-options", "online_options"),
        ("GET", r"/api/services/presets", "service_presets"),
        ("POST", r"/api/items/(\w+)/watched", "watched"),
        ("PUT", r"/api/items/(\w+)/collections", "item_collections"),
        ("PUT", r"/api/items/(\w+)/folder", "move_to_folder"),
        ("POST", r"/api/collections", "add_collection"),
        ("PUT", r"/api/collections/(\w+)", "rename_collection"),
        ("DELETE", r"/api/collections/(\w+)", "delete_collection"),
        ("POST", r"/api/collections/(\w+)/share", "share_collection"),
        ("POST", r"/api/collections/(\w+)/follow", "follow_check"),
        ("POST", r"/api/collections/(\w+)/save", "save_collection"),
        ("POST", r"/api/shared/peek", "shared_peek"),
        ("POST", r"/api/shared/import", "shared_import"),
        ("GET", r"/api/player", "player_status"),
        ("POST", r"/api/player/vlc", "player_vlc"),
        ("POST", r"/api/player/stop", "player_stop"),
        ("GET", r"/api/library", "library"),
        ("POST", r"/api/library/rescan", "library_rescan"),
        ("POST", r"/api/library/answer", "library_answer"),
        ("POST", r"/api/library/undo", "library_undo"),
        ("POST", r"/api/library/sort", "library_sort"),
        ("POST", r"/api/library/route", "library_route"),
        ("GET", r"/api/library/extra", "library_extra"),
        ("POST", r"/api/library/extra/ai", "library_extra_ai"),
        ("POST", r"/api/open-place", "open_place"),
        ("GET", r"/api/items/(\w+)/connected", "connected"),
        ("GET", r"/api/items/(\w+)/similar", "similar"),
        ("POST", r"/api/items/(\w+)/connected", "find_connected"),
        ("POST", r"/api/items/(\w+)/connected/cancel", "cancel_connected"),
        ("POST", r"/api/items/(\w+)/recap", "recap"),
        ("POST", r"/api/settings/ai", "test_ai"),
        ("POST", r"/api/android/progress", "phone_progress"),
        ("GET", r"/api/fs", "fs"),
        ("GET", r"/api/settings/ai-presets", "ai_presets"),
        ("GET", r"/api/find/top", "find_top"),
        ("GET", r"/api/find/search", "find_search"),
        ("GET", r"/api/find/genres", "find_genres"),
        ("GET", r"/api/find/about", "find_about"),
        ("GET", r"/api/torrents", "torrents"),
        ("GET", r"/api/items/(\w+)/torrents", "item_torrents"),
        ("POST", r"/api/items/(\w+)/torrents", "add_torrent"),
        ("POST", r"/api/items/(\w+)/torrent-keep", "torrent_keep"),
        ("POST", r"/api/items/(\w+)/delete-file", "delete_file"),
        ("POST", r"/api/torrents/(\w+)/pause", "torrent_pause"),
        ("POST", r"/api/torrents/(\w+)/move", "torrent_move"),
        ("POST", r"/api/items/(\w+)/download-full", "download_full"),
        ("POST", r"/api/torrents/(\w+)/folder", "torrent_folder"),
        ("POST", r"/api/torrents/(\w+)/files/(\d+)", "torrent_file"),
        ("POST", r"/api/torrents/(\w+)/files/(\d+)/delete", "torrent_file_delete"),
        ("DELETE", r"/api/torrents/(\w+)", "remove_torrent"),
        ("GET", r"/api/torrents/(\w+)/plan", "torrent_plan"),
        ("GET", r"/api/items/(\w+)/find-torrent", "find_torrent_state"),
        ("POST", r"/api/items/(\w+)/find-torrent", "find_torrent"),
        ("POST", r"/api/items/(\w+)/find-torrent/use", "find_torrent_use"),
        ("POST", r"/api/items/(\w+)/stream-buffer", "stream_buffer"),
        ("GET", r"/api/items/(\w+)/connections", "connections"),
        ("POST", r"/api/items/(\w+)/connections", "connect_file"),
        ("POST", r"/api/items/(\w+)/connections/redo", "sort_again"),
        ("POST", r"/api/sites/test", "test_site"),
        ("POST", r"/api/video-sites/test", "test_video_site"),
        ("POST", r"/api/video-sites/find-search", "find_video_site_search"),
        ("POST", r"/api/video-sites/diagnose", "diagnose_video_site"),
        ("POST", r"/api/items/(\w+)/scraped", "play_scraped"),
        ("POST", r"/api/items/(\w+)/scraped/unlink", "scraped_unlink"),
        ("GET", r"/api/items/(\w+)/online/links", "online_links"),
        ("POST", r"/api/items/(\w+)/connections/clear", "clear_connections"),
        ("POST", r"/api/items/(\w+)/pick", "pick_start"),
        ("GET", r"/api/pick/(\w+)", "pick_view"),
        ("POST", r"/api/pick/(\w+)/use", "pick_use"),
        ("POST", r"/api/pick/(\w+)/close", "pick_close"),
        ("POST", r"/api/party-server/find", "find_party_server"),
        ("GET", r"/api/items/(\w+)/subs", "subs_info"),
        ("POST", r"/api/items/(\w+)/subs/find", "find_subs"),
        ("POST", r"/api/items/(\w+)/subs/answer", "answer_subs"),
        ("POST", r"/api/items/(\w+)/subs/folder", "subs_folder"),
        ("POST", r"/api/items/(\w+)/subs/keep", "keep_subs"),
        ("POST", r"/api/items/(\w+)/subs/clear", "clear_subs"),
        ("POST", r"/api/items/(\w+)/online/link-all", "link_all"),
        ("POST", r"/api/items/(\w+)/online/relink", "relink_episode"),
        ("POST", r"/api/items/(\w+)/online/prefs", "online_prefs"),
        ("POST", r"/api/torrents/(\w+)/plan", "torrent_plan_group"),
        ("POST", r"/api/torrents/(\w+)/plan/redo", "torrent_plan_redo"),
        ("POST", r"/api/torrents/(\w+)/plan/add", "torrent_plan_add"),
        ("GET", r"/api/items/(\w+)/mal-matches", "mal_matches"),
        ("POST", r"/api/items/(\w+)/use-mal", "use_mal"),
        ("POST", r"/api/find/mood", "find_mood"),
        ("GET", r"/api/find/mood/(\w+)", "find_mood_job"),
        ("POST", r"/api/find/mood/(\w+)/cancel", "find_mood_cancel"),
        ("POST", r"/api/find/quick", "find_quick"),
        ("POST", r"/api/import/mal", "import_mal"),
        ("GET", r"/api/import/status", "import_status"),
        ("POST", r"/api/party/start", "party_start"),
        ("POST", r"/api/party/join", "party_join"),
        ("POST", r"/api/party/peek", "party_peek"),
        ("POST", r"/api/party/use-file", "party_use_file"),
        ("POST", r"/api/party/follow", "party_follow"),
        ("POST", r"/api/window", "open_window"),
        ("GET", r"/api/party", "party_status"),
        ("POST", r"/api/party/leave", "party_leave"),
        ("POST", r"/api/party/host", "party_host"),
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

    remote = False       # a server listening on your network (remote.py): paired phones and tablets only
    device = None        # the paired device a request over the network came from
    _out_headers = None  # headers this answer carries too (the pairing cookie)

    # What a phone over the network may not do: answer for this computer, or take devices out.
    LOCAL_ONLY = {"pair_answer", "phone_remove_all", "phone_device_remove"}

    def _allowed_host(self):
        if self.remote:
            return self.app.remote.allowed_host(self.headers.get("Host", ""))
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _cookie(self, name):
        try:
            jar = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
        except http.cookies.CookieError:
            return ""
        return jar[name].value if name in jar else ""

    def _remote_gate(self, method, url):
        """A request over the network: True when it was answered here (not let in)."""
        path = url.path
        if path.startswith(("/online/", "/hls/")) or (path.startswith("/stream/") and not path.startswith("/stream/web/")):
            self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")         # (this computer's own player's)
            return True
        if path.startswith("/stream/web/"):
            return False                     # (its own short-lived ticket says who may have it)
        if not path.startswith("/api/"):
            if method in ("GET", "HEAD") and remote.SHELL.fullmatch(path):
                return False                 # (the app's pages themselves: nothing of yours in them)
            self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
            return True
        if path in remote.OPEN_API:
            return False
        device = self.app.remote.devices.check(self._cookie(remote.COOKIE))
        if not device:
            self._json({"error": "This device isn't connected to the app yet.", "code": "unpaired"},
                       HTTPStatus.UNAUTHORIZED)
            return True
        self.device = device
        self.app.remote.devices.seen(device, self.client_address[0])
        self.app.remote.awake.poke()
        return False

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
        self._send(status, json.dumps(data).encode("utf-8"), "application/json; charset=utf-8", self._out_headers)

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
        self.device, self._out_headers = None, None
        if url.path.startswith("/stream/web/") and method in ("GET", "HEAD"):
            return self._web_stream(url)
        if self.remote and self._remote_gate(method, url):
            return None
        em = re.fullmatch(r"/api/web/(\w+)/events", url.path)
        if em and method == "GET":
            return self._web_events(em.group(1), url)
        if url.path == "/api/phone/speed" and method == "GET":
            return self._speed(url)
        if url.path.startswith("/stream/") and method in ("GET", "HEAD"):
            return self._stream(url.path)
        if url.path.startswith("/online/") and method in ("GET", "HEAD"):
            return self._online(url)
        if url.path.startswith("/hls/") and method in ("GET", "HEAD"):
            return self._hls(url)
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
                if self.remote and name in self.LOCAL_ONLY:
                    return self._json({"error": "only on the computer itself"}, HTTPStatus.FORBIDDEN)
                guest = self.app.party_guest_of()
                if guest and name in self.HOST_PLAYS:
                    # In someone's watch party: what plays is the host's choice.
                    return self._json({"error": f"You're in {guest}'s watch party - {guest} chooses what plays. "
                                                "Leave the party to play something yourself.", "code": "party_guest"},
                                      HTTPStatus.CONFLICT)
                try:
                    body = self._body() if method in ("POST", "PUT") else {}
                    _request.links = [] if self.remote else None
                    _request.device = self.device
                    result = getattr(self, "api_" + name)(body, *m.groups())
                    if _request.links and isinstance(result, dict):
                        result = dict(result, open_here=list(_request.links))
                    return self._json(result)
                except ApiError as e:
                    return self._json({"error": str(e), **({"code": e.code} if e.code else {})}, e.status)
                except metadata.MetadataError as e:
                    return self._json({"error": str(e)}, HTTPStatus.BAD_GATEWAY)
                except torrents_mod.TorrentError as e:
                    return self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
                except (ValueError, TypeError) as e:
                    return self._json({"error": str(e) or "invalid request"}, HTTPStatus.BAD_REQUEST)
                except Exception as e:  # keep the app alive whatever happens
                    return self._json({"error": f"unexpected error: {e}"},
                                      HTTPStatus.INTERNAL_SERVER_ERROR)
                finally:
                    _request.links = _request.device = None
        return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def _online(self, url):
        """An episode of the player's playlist from your own site: sent on to its video."""
        m = re.fullmatch(r"/online/([\w-]+)/(\w+)/(\d+)/(\d+)", url.path)
        if not m or m.group(1) != self.app.torrents.token:
            return self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
        q = {k: v[-1] for k, v in urllib.parse.parse_qs(url.query).items()}
        try:
            video = self.app.online_video(m.group(2), int(m.group(3)), int(m.group(4)), q.get("site", ""), q.get("track", ""))
        except (OnlineError, KeyError) as e:
            return self._send(HTTPStatus.NOT_FOUND, str(e).encode("utf-8", "replace"), "text/plain")
        return self._send(HTTPStatus.FOUND, b"", "text/plain", {"Location": video})

    def _hls(self, url):
        """A video's playlist or piece through the app (hlsfix.py)."""
        m = re.fullmatch(r"/hls/([\w-]+)/(\w+)/v\.(?:m3u8|ts)", url.path)
        target = urllib.parse.parse_qs(url.query).get("u", [""])[-1]
        if not m or m.group(1) != self.app.torrents.token or not target.startswith(("http://", "https://")):
            return self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
        base = f"http://127.0.0.1:{self.server.server_address[1]}/hls/{m.group(1)}"
        try:
            got = hlsfix.serve(m.group(2), target, base)
        except Exception as e:  # noqa: BLE001 - the site's answer, passed on
            return self._send(HTTPStatus.BAD_GATEWAY, str(e).encode("utf-8", "replace"), "text/plain")
        if not got:
            return self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
        return self._send(HTTPStatus.OK, got[1], got[0])

    STREAM_TYPES = {".mkv": "video/x-matroska", ".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm",
                    ".avi": "video/x-msvideo", ".mov": "video/quicktime", ".ts": "video/mp2t"}

    def _stream(self, path):
        """A torrent's file for the player, as it downloads (with seeking)."""
        found = self.app.torrents.parse_url(f"http://127.0.0.1:{self.server.server_address[1]}{path}")
        if not found:
            return self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
        torrents = self.app.torrents
        try:
            torrents.prepare(*found)
            size = torrents.size(*found)
        except torrents_mod.TorrentError as e:
            return self._send(HTTPStatus.SERVICE_UNAVAILABLE, str(e).encode(), "text/plain")
        start, end = 0, size - 1
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", "").strip())
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            if start >= size or start > end:
                return self._send(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE, b"", "text/plain",
                                  {"Content-Range": f"bytes */{size}"})
        self.send_response(HTTPStatus.PARTIAL_CONTENT if m and (m.group(1) or m.group(2)) else HTTPStatus.OK)
        self.send_header("Content-Type", self.STREAM_TYPES.get(Path(path).suffix.lower(), "application/octet-stream"))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if m and (m.group(1) or m.group(2)):
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return None
        try:
            for chunk in torrents.read(found[0], found[1], start, end):
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, torrents_mod.TorrentError):
            pass      # the player moved on (or seeked), or the torrent stalled
        return None

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
        csp = ("default-src 'self'; img-src 'self' https: data: blob:; style-src 'self' 'unsafe-inline'; "
               "frame-src https://www.youtube.com https://www.youtube-nocookie.com; "
               "script-src 'self'; connect-src 'self'")
        self._send(HTTPStatus.OK, target.read_bytes(), ctype,
                   {"Content-Security-Policy": csp, "Referrer-Policy": "strict-origin-when-cross-origin"})

    # ---- API

    def api_ping(self, body):
        return {"app": "watchlist"}

    def _version(self):
        party = 1 if self.app.party and self.app.party.active else 0
        return f"{self.app.store.current_version()}.{self.app.library.version}.{party}"

    def api_state(self, body):
        self.app.retry_sync()
        out = {"items": self.app.store.list(), "collections": self.app.store.collections(),
               "status": self.app.status(), "library": self.app.library.state(),
               "version": self._version()}
        if self.device:
            out["device"] = self.app.remote.devices.public(self.device)
        return out

    def api_version(self, body):
        """Polled by the window, so changes from the other computer (or new
        downloads) show up - and, on this computer, a phone asking to connect."""
        self.app.retry_sync()
        out = {"version": self._version()}
        if not self.remote:
            out["pair"] = self.app.remote.devices.waiting()
        return out

    # ---- a phone playing on itself (webplay.py)

    def _web_session(self, sid):
        session = next((s for s in self.app.web_sessions.values() if s.id == sid), None)
        if not session or (self.device and session.device != self.device["id"]):
            raise ApiError("that player has closed", HTTPStatus.NOT_FOUND, code="closed")
        return session

    def _web_events(self, sid, url):
        """What the phone's player is asked to do (a watch party: pause, play, seek, move on), as
        it happens: Server-Sent Events, each batch with its number (the phone skips what it had)."""
        try:
            session = self._web_session(sid)
        except ApiError as e:
            return self._json({"error": str(e), "code": e.code}, e.status)
        try:
            after = int(urllib.parse.parse_qs(url.query).get("after", ["0"])[-1])
        except ValueError:
            after = 0
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            while not session.ended:
                seq, cmds = session.wait(after, 15)
                if cmds:
                    self.wfile.write(f"data: {json.dumps({'seq': seq, 'commands': cmds})}\n\n".encode())
                    after = seq
                else:
                    self.wfile.write(b": still here\n\n")
                self.wfile.flush()
            self.wfile.write(b"event: closed\ndata: {}\n\n")
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        return None

    _NOISE = os.urandom(1 << 20)

    def _speed(self, url):
        """Settings → Test my phone connection: some megabytes, as fast as they go."""
        try:
            mb = max(1, min(32, int(urllib.parse.parse_qs(url.query).get("mb", ["4"])[-1])))
        except ValueError:
            mb = 4
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(mb << 20))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            for _ in range(mb):
                self.wfile.write(self._NOISE)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        return None

    def api_web_current(self, body):
        """The player this phone had open (the page was reloaded): opened again."""
        session = self.app.web_sessions.get((self.device or {}).get("id"))
        if not session or session.ended:
            return {"launch": None}
        return {"launch": dict(session.launch(), start=session.position() or session.start_at)}

    def api_web_open(self, body, sid):
        session = self._web_session(sid)
        try:
            index = int(body.get("index", session.index))
        except (TypeError, ValueError):
            raise ApiError("which episode?") from None
        return self.app.web_media(session, index, float(body.get("at") or 0), body.get("caps") or {},
                                  body.get("quality"), body.get("burn"))

    def api_web_state(self, body, sid):
        """The phone's player: what it's doing (where it is, paused, which episode) - your place
        is saved from it - and, back, what it's asked to do (a watch party: pause, seek...)."""
        session = self._web_session(sid)
        session.event(body)
        entry = session.current()
        if entry and session.dur and (body.get("ended") or (session.pos is not None and (
                time.time() - session.saved_at > 10 or body.get("paused") or body.get("closed")))):
            session.saved_at = time.time()
            if body.get("ended"):
                self.app._finished_entry(session.item_id, entry)
            else:
                self.app._save_position(session.item_id, entry, float(session.pos), float(session.dur))
        try:
            after = int(body.get("after") or 0)
        except (TypeError, ValueError):
            after = 0
        seq, cmds = session.take(after)
        out = {"seq": seq, "commands": cmds, "ended": session.ended}
        job = self.app.web_jobs.current(session.device)
        if job and entry and job.source == entry.get("path"):
            job.used = time.time()
            out["ready"] = job.ready_until()        # (how far the video made for it goes, from where it starts)
        return out

    def api_web_seek(self, body, sid):
        session = self._web_session(sid)
        return self.app.web_seek(session, float(body.get("t") or 0), body.get("caps") or {}, body.get("quality"),
                                 body.get("burn"))

    def api_web_source(self, body, sid):
        """Not working? Try another server (or your next site) - as in the computer's player."""
        session = self._web_session(sid)
        threading.Thread(target=self.app._other_source, args=(session,), daemon=True).start()
        return {"ok": True}

    def api_web_servers(self, query, sid):
        return self.app.server_list(self._web_session(sid))

    def api_web_server(self, body, sid):
        session = self._web_session(sid)
        threading.Thread(target=self.app.use_server, args=(session, str(body.get("id") or "")), daemon=True).start()
        return {"ok": True}

    def api_web_other_site(self, body, sid):
        session = self._web_session(sid)
        threading.Thread(target=self.app._other_site, args=(session,), daemon=True).start()
        return {"ok": True}

    def api_web_version(self, body, sid):
        session = self._web_session(sid)
        threading.Thread(target=self.app._switch_version, args=(session,), daemon=True).start()
        return {"ok": True}

    # ---- the phone's videos (/stream/web/<ticket>/...)

    MEDIA_TYPES = {".m3u8": "application/vnd.apple.mpegurl", ".m4s": "video/iso.segment", ".mp4": "video/mp4",
                   ".vtt": "text/vtt; charset=utf-8", ".ts": "video/mp2t", ".aac": "audio/aac", ".key": "application/octet-stream",
                   ".m4a": "audio/mp4", ".mp3": "audio/mpeg"}

    def _web_stream(self, url):
        m = re.fullmatch(r"/stream/web/([\w.-]+)/(.+)", url.path)
        target = self.app.tickets.check(m.group(1)) if m else None
        if not target:
            return self._send(HTTPStatus.FORBIDDEN, b"this video's address has run out - open it again", "text/plain")
        self.app.remote.awake.poke()
        rest = m.group(2)
        try:
            if target["kind"] == "file":
                size = os.path.getsize(target["path"])

                def read(start, end):
                    with open(target["path"], "rb") as f:
                        f.seek(start)
                        left = end - start + 1
                        while left > 0:
                            chunk = f.read(min(1 << 20, left))
                            if not chunk:
                                return
                            left -= len(chunk)
                            yield chunk
                return self._send_ranges(size, read, "video/mp4")
            if target["kind"] == "job":
                return self._web_job(target["job"], rest)
            if target["kind"] == "remote":
                return self._web_remote(target, m.group(1), rest)
            if target["kind"] == "sub":
                return self._web_subtitle(target)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return None
        return self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")

    def _send_ranges(self, size, read, ctype):
        """A file's bytes, with byte ranges (206) so the phone's player can seek."""
        start, end = 0, size - 1
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", "").strip())
        ranged = bool(m and (m.group(1) or m.group(2)))
        if ranged:
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            if start >= size or start > end:
                return self._send(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE, b"", "text/plain",
                                  {"Content-Range": f"bytes */{size}"})
        self.send_response(HTTPStatus.PARTIAL_CONTENT if ranged else HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if ranged:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return None
        for chunk in read(start, end):
            self.wfile.write(chunk)
        return None

    def _web_job(self, job, rest):
        job.used = time.time()
        folder = job.folder.resolve()
        path = (folder / rest).resolve()
        if not _inside(str(path), str(folder)):
            return self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
        ext = path.suffix.lower()
        if ext == ".vtt" and not path.is_file():
            return self._send(HTTPStatus.OK, b"WEBVTT\n\n", self.MEDIA_TYPES[".vtt"])
        if not job.wait_for(rest, 40 if ext == ".m3u8" else 25):
            why = job.failed()
            return self._send(HTTPStatus.SERVICE_UNAVAILABLE if not why else HTTPStatus.BAD_GATEWAY,
                              (why or "not ready yet").encode("utf-8", "replace"), "text/plain")
        data = path.read_bytes()
        if ext == ".m3u8" and b"#EXT-X-START" not in data:
            # (still being made, it'd look live: players start it from the beginning, not its end)
            data = data.replace(b"#EXTM3U", b"#EXTM3U\n#EXT-X-START:TIME-OFFSET=0,PRECISE=YES", 1)
        if rest == "master.m3u8":
            # (ffmpeg's names for the audio tracks, and its Windows slashes, put right)
            text = data.decode("utf-8", "replace").replace("\\", "/")
            names = iter(a["label"] for a in job.audio)
            text = re.sub(r'(#EXT-X-MEDIA:TYPE=AUDIO[^\n]*?)NAME="[^"]*"',
                          lambda mm: f'{mm.group(1)}NAME="{next(names, "Audio").replace(chr(34), "")}"', text)
            data = text.encode("utf-8")
        return self._send(HTTPStatus.OK, data, self.MEDIA_TYPES.get(ext, "application/octet-stream"))

    def _web_remote(self, target, ticket, rest):
        """A site's video through the app: playlists pointed at the ticket, pieces passed on (any
        picture in front of them taken off), MP4s with byte ranges."""
        rm = re.fullmatch(r"r/(\d+)\.(\w+)", rest)
        if rm:
            n = int(rm.group(1))
            if n >= len(target["urls"]):
                return self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
            url, kind = target["urls"][n], rm.group(2)
        elif rest in ("media.m3u8", "media.mp4"):
            url, kind = target["url"], rest.rsplit(".", 1)[1]
        else:
            return self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")

        def remember(u):
            urls = target["urls"]
            if u not in urls:
                urls.append(u)
            return urls.index(u)
        try:
            upstream = webplay.open_remote(url, target["headers"], self.headers.get("Range") if kind == "mp4" else None)
        except urllib.error.HTTPError as e:
            return self._send(HTTPStatus.BAD_GATEWAY, f"the site said {e.code}".encode(), "text/plain")
        except (OSError, ValueError) as e:
            return self._send(HTTPStatus.BAD_GATEWAY, f"the site didn't answer ({e})".encode("utf-8", "replace"), "text/plain")
        with upstream:
            if kind == "m3u8":
                text = upstream.read().decode("utf-8", "replace")
                if not text.lstrip().startswith("#EXTM3U"):
                    return self._send(HTTPStatus.BAD_GATEWAY, b"the site didn't send a playlist", "text/plain")
                body = webplay.rewrite(text, upstream.geturl(), ticket, remember).encode("utf-8")
                return self._send(HTTPStatus.OK, body, self.MEDIA_TYPES[".m3u8"])
            if kind == "mp4" and not rm:
                # (the whole video from the site: its byte ranges passed through)
                self.send_response(upstream.status)
                self.send_header("Content-Type", "video/mp4")
                for h in ("Content-Length", "Content-Range", "Accept-Ranges"):
                    if upstream.headers.get(h):
                        self.send_header(h, upstream.headers[h])
                self.end_headers()
                while True:
                    chunk = upstream.read(1 << 16)
                    if not chunk:
                        return None
                    self.wfile.write(chunk)
            head = upstream.read(65536)
            fixed = webplay.strip_picture(head) if kind in ("ts", "m4s") else head
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", self.MEDIA_TYPES.get("." + kind, "application/octet-stream"))
            length = upstream.headers.get("Content-Length")
            if length and fixed is head:
                self.send_header("Content-Length", length)
            self.end_headers()
            self.wfile.write(fixed)
            while True:
                chunk = upstream.read(1 << 16)
                if not chunk:
                    return None
                self.wfile.write(chunk)

    def _web_subtitle(self, target):
        if target.get("vtt") is None:
            try:
                if target.get("url"):
                    with webplay.open_remote(target["url"], target.get("headers")) as r:
                        raw = r.read().decode("utf-8", "replace")
                    text = transcode.clean_vtt(raw) if "-->" in raw and "[Script Info]" not in raw \
                        else transcode.to_vtt(target["url"], headers=target.get("headers"))
                elif target.get("stream") is not None:
                    text = transcode.to_vtt(target["path"], stream=target["stream"])
                elif str(target["path"]).lower().endswith((".srt", ".vtt")):
                    raw = Path(target["path"]).read_text(encoding="utf-8", errors="replace")
                    text = transcode.clean_vtt(raw)
                else:
                    text = transcode.to_vtt(target["path"])
            except (OSError, RuntimeError, ValueError) as e:
                return self._send(HTTPStatus.BAD_GATEWAY, f"the subtitles couldn't be read ({e})".encode("utf-8", "replace"), "text/plain")
            target["vtt"] = text
        return self._send(HTTPStatus.OK, target["vtt"].encode("utf-8"), self.MEDIA_TYPES[".vtt"])

    # ---- your phone or tablet (remote.py)

    def api_pair_request(self, body):
        """A phone asks to connect: the code it shows (this computer shows it too, with Allow / Deny)."""
        if not self.remote:
            raise ApiError("this is the computer itself - it's always connected")
        try:
            rid, code = self.app.remote.devices.request(body.get("name"), self.client_address[0])
        except PermissionError as e:
            raise ApiError(str(e), HTTPStatus.TOO_MANY_REQUESTS) from e
        threading.Thread(target=launcher.bring_to_front, daemon=True).start()
        return {"id": rid, "code": code}

    def api_pair_status(self, body):
        if not self.remote:
            raise ApiError("this is the computer itself - it's always connected")
        state, token = self.app.remote.devices.poll(str(self.query.get("id") or ""), self.client_address[0])
        if token:
            # The device's key, kept by the browser where scripts (and other sites) can't read it.
            self._out_headers = {"Set-Cookie": f"{remote.COOKIE}={token}; Max-Age={remote.COOKIE_AGE}; Path=/; "
                                               "HttpOnly; SameSite=Strict"}
        return {"state": state}

    def api_pair_answer(self, body):
        try:
            device = self.app.remote.devices.answer(str(body.get("id") or ""), bool(body.get("allow")))
        except KeyError as e:
            raise ApiError(str(e.args[0]) if e.args else "that request isn't waiting any more",
                           HTTPStatus.NOT_FOUND) from None
        return {"device": device}

    def api_phone_status(self, body):
        return self.app.remote.status()

    def api_phone_me(self, body):
        if not self.device:
            return {"device": None, "remote": False}
        return {"device": self.app.remote.devices.public(self.device), "remote": True}

    def api_phone_device(self, body, device_id):
        if self.device and device_id != self.device["id"]:
            raise ApiError("a phone can only change itself", HTTPStatus.FORBIDDEN)
        try:
            return {"device": self.app.remote.devices.update(device_id, body.get("name"), body.get("play_on"))}
        except KeyError:
            raise ApiError("that device isn't connected", HTTPStatus.NOT_FOUND) from None

    def api_phone_device_remove(self, body, device_id):
        self.app.web_forget([device_id])
        return {"removed": self.app.remote.devices.remove(device_id)}

    def api_phone_remove_all(self, body):
        self.app.web_forget([d["id"] for d in self.app.remote.devices.list()])
        return {"removed": self.app.remote.devices.remove(None)}

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
        add = body.get("add")
        if isinstance(add, dict):
            # "Add it to my list": a suggestion from the search, added and connected.
            item = self.app.add_title(add.get("type"), add.get("source"), add.get("source_id"))
            body = dict(body, item=item["id"])
        try:
            self.app.library.answer(body)
        except ValueError as e:
            raise ApiError(str(e)) from e
        return self.app.library.state()

    def api_library_undo(self, body):
        """Undo something sorted automatically; a title it added to your list is
        removed again, unless something else uses it now."""
        self.app.undo_sorted(str(body.get("key") or ""))
        return self.app.library.state()

    def api_library_sort(self, body):
        """Sort now (with the AI when body["ai"]), in the background; the window
        follows it through /api/library."""
        if not self.app.sorter.now.get("running"):
            self.app.sorter.now = {"running": True, "last": self.app.sorter.now.get("last")}
            threading.Thread(target=self.app.sorter.run_now, args=(bool(body.get("ai")),), daemon=True).start()
        return self.app.library.state()

    def api_library_extra(self, body):
        """Your downloads no title plays from: not matched to a title, not in a title's own folder
        (the folder chosen on its page), not one of a title's torrent's files."""
        folders = [i["local_folder"] for i in self.app.store.list() if i.get("local_folder")]
        # Every file a title plays from, worked out as the player does (downloads, the title's
        # own folder, its torrents): none of those is "extra", whatever the reason.
        used = set()
        for item in self.app.store.list():
            try:
                videos, _ = self.app._sources(item)
            except Exception:  # noqa: BLE001 - a title whose files can't be read: nothing of it counted
                continue
            used.update(os.path.normcase(os.path.realpath(v["path"])) for v in videos if v.get("path"))
        files = [f for f in self.app.library.extra_files()
                 if os.path.normcase(os.path.realpath(f["path"])) not in used
                 and not any(_inside(f["path"], d) for d in folders) and not self.app.torrents.find_file(f["path"])]
        return {"files": files, "folder": self.app.settings.get().get("downloads_folder") or "",
                "ai": bool(ai.available(self.app.settings.get()))}

    def api_library_extra_ai(self, body):
        """Extra files matched again by the app (with your AI helper): each back in Sort your
        downloads, then sorted now."""
        rels = [str(r) for r in body.get("rels") or []]
        if not rels:
            raise ApiError("choose the files first")
        for rel in rels:
            try:
                self.app.library.route(rel, "unsorted")
            except ValueError:
                pass
        return self.api_library_sort({"ai": True})

    def api_open_place(self, body):
        """Open one of the app's folders: your downloads, its own data (keys, settings), the sync folder."""
        s = self.app.settings.get()
        place = {"downloads": s.get("downloads_folder") or "", "data": str(self.app.data_dir),
                 "sync": s.get("sync_folder") or ""}.get(str(body.get("which") or ""), "")
        if not place:
            raise ApiError("that folder isn't set")
        try:
            launcher.reveal(place)
        except launcher.LaunchError as e:
            raise ApiError(str(e)) from e
        return {"ok": True, "path": place}

    def api_library_route(self, body):
        """Move one file: to a title and episode, out of its title, or ignore it."""
        try:
            self.app.library.route(str(body.get("rel") or ""), str(body.get("item") or ""),
                                   body.get("season"), body.get("episode"), body.get("kind"))
        except ValueError as e:
            raise ApiError(str(e)) from e
        return self.app.library.state()

    def api_share_collection(self, body, col_id):
        """Its link (the same every time, kept up to date) and a code of the titles as they are now."""
        if body.get("stop"):
            return {"collection": self.app.live_stop(col_id)}
        data = self.app.share_collection(col_id)
        return {"code": self.app.live_share(col_id), "snapshot": share.to_code(data), "name": data["name"],
                "count": len(data["items"]), "status": self.app._live_status.get(col_id)}

    def api_follow_check(self, body, col_id):
        return dict(self.app.live_follow(col_id), collection=self.app._collection(col_id))

    def api_save_collection(self, body, col_id):
        """Save a collection as a file (in your Downloads folder) and show it."""
        data = self.app.share_collection(col_id)
        folder = Path.home() / "Downloads"
        folder = folder if folder.is_dir() else Path.home()
        path = folder / share.file_name(data["name"])
        try:
            path.write_text(share.to_file_text(data, live=self.app.live_share(col_id)), encoding="utf-8")
        except OSError as e:
            raise ApiError(f"couldn't save the file ({e})") from e
        launcher.reveal(str(path))
        return {"path": str(path), "count": len(data["items"])}

    def api_shared_peek(self, body):
        return self.app.read_shared(body)

    def api_shared_import(self, body):
        return self.app.import_shared(body)

    def api_mal_matches(self, body, item_id):
        """MyAnimeList entries that could be this title (to switch a TMDB anime over)."""
        item = self.app.item(item_id)
        s = self.app.settings.get()
        if not s["mal_client_id"]:
            raise ApiError("add your MyAnimeList Client ID in Settings first")
        rows = metadata.mal_matches(s, dict(item, source_id=item["source_id"]))
        for r in rows:
            mine = self.app.store.find_source("mal", r["source_id"])
            r["in_list"] = mine["id"] if mine else None
        return {"results": rows}

    def api_use_mal(self, body, item_id):
        try:
            return self.app.use_mal(self.app.item(item_id), str(int(body.get("mal_id"))))
        except (TypeError, ValueError) as e:
            raise ApiError("choose a MyAnimeList entry") from e

    def api_connected(self, body, item_id):
        return self.app.connected(self.app.item(item_id))

    def api_similar(self, body, item_id):
        item = self.app.item(item_id)
        s = self.app.settings.get()
        if not s["mal_client_id" if item["type"] == "anime" else "tmdb_api_key"]:
            return {"entries": [], "note": f"Add your {'MyAnimeList Client ID' if item['type'] == 'anime' else 'TMDB token'} "
                                           "in Settings to see related titles."}
        return {"entries": self.app.similar(item)}

    def api_find_connected(self, body, item_id):
        return self.app.connected(self.app.item(item_id), run=True)

    def api_cancel_connected(self, body, item_id):
        return self.app.cancel_connected(self.app.item(item_id))

    def api_recap(self, body, item_id):
        item = self.app.item(item_id)
        s = self.app.settings.get()
        if not ai.available(s):
            raise ApiError("add an AI key (Gemini, Groq or OpenRouter) in Settings for recaps")
        try:
            return ai.recap(s, item, int(body.get("season") or 1), int(body.get("episode") or 1))
        except ai.AIError as e:
            raise ApiError(str(e)) from e

    # ---- Find Media

    def _find_kind(self):
        kind = self.query.get("type", "")
        if kind not in TYPES:
            raise ApiError("type must be movie, tv or anime")
        s = self.app.settings.get()
        if kind != "anime" and not s["tmdb_api_key"]:
            raise ApiError("add your TMDB token in Settings to find TV shows and films")
        return kind, s

    def _mark_listed(self, rows):
        return self.app.mark_listed(rows)

    def api_find_top(self, body):
        kind, s = self._find_kind()
        if kind == "anime" and not s["mal_client_id"]:
            raise ApiError("add your MyAnimeList Client ID in Settings to find anime by year")
        year = int(self.query.get("year") or time.strftime("%Y"))
        order = "rated" if self.query.get("order") == "rated" else "popular"
        return {"results": self._mark_listed([dict(r) for r in discover.top_by_year(s, kind, year, order)])}

    def api_find_search(self, body):
        """Search & browse: any mix of types (none means all), keywords, genres,
        year, format and sort, 50 at a time."""
        s = self.app.settings.get()
        q = self.query
        kinds = [k for k in (q.get("types") or "").split(",") if k in TYPES]
        sort = q.get("sort") if q.get("sort") in {k for k, _ in discover.SORT_NAMES} else "popular"
        year = q.get("year") or ""
        year = int(year) if year.isdigit() and 1900 <= int(year) <= 2100 else None
        try:
            page = max(1, min(int(q.get("page") or 1), 200))
        except ValueError:
            page = 1
        keys = [k for k in (q.get("genres") or "").split(",") if k][:12]
        try:
            out = discover.search(s, self.app.anime_index, kinds, q.get("q", "")[:100], keys, year, sort,
                                  q.get("format", ""), page)
        except metadata.MetadataError as e:
            raise ApiError(str(e)) from e
        out["results"] = self._mark_listed(out["results"])
        return out

    def api_find_genres(self, body):
        """Every genre to filter by. Opening Find Media also starts collecting
        MyAnimeList's anime (for genre searches), if that's not done yet."""
        self.app.anime_index.ensure(self.app.settings.get())
        out = discover.genres(self.app.anime_index)
        out["index"] = self.app.anime_index.state()
        return out

    def api_find_about(self, body):
        """The summary of a search result that came without one."""
        s = self.app.settings.get()
        source, sid = self.query.get("source", ""), self.query.get("id", "")
        try:
            if source == "mal" and sid.isdigit() and s["mal_client_id"]:
                return {"overview": metadata.MAL(s["mal_client_id"]).details(sid).get("overview", "")}
            if source == "tmdb" and s["tmdb_api_key"]:
                return {"overview": metadata.TMDB(s["tmdb_api_key"]).details(sid).get("overview", "")}
        except metadata.MetadataError as e:
            raise ApiError(str(e)) from e
        return {"overview": ""}

    def api_find_mood(self, body):
        """"I'm in the mood for...": starts looking (in the background) for the kind
        you picked; the page follows it with /api/find/mood/<job>."""
        text = str(body.get("text") or "").strip()[:500]
        if not text:
            raise ApiError("tell me what you're in the mood for first")
        kind = body.get("kind") or ""
        if kind not in ("", *mood_mod.KINDS):
            raise ApiError("pick films, TV shows or anime")
        return self.app.moods.start(kind, text).view()

    def api_find_mood_job(self, body, job_id):
        job = self.app.moods.get(job_id)
        if not job:
            raise ApiError("that search is over - start it again", HTTPStatus.NOT_FOUND)
        return job.view()

    def api_find_mood_cancel(self, body, job_id):
        job = self.app.moods.get(job_id)
        if job:
            job.cancel()
        return {"ok": True}

    def api_find_quick(self, body):
        """Straight away, while the AI thinks: titles whose name is what you typed
        (MyAnimeList and TMDB at once), so "frieren" shows Frieren in a second."""
        q = str(body.get("q") or "").strip()
        s = self.app.settings.get()
        if len(q) < 2 or len(q.split()) > 8:
            return {"results": []}
        kinds = [k for k, ok in (("anime", s["mal_client_id"]), ("tv", s["tmdb_api_key"]), ("movie", s["tmdb_api_key"])) if ok]

        def look(kind):
            try:
                return [dict(r, type=kind) for r in metadata.search(s, kind, q)[:4]]
            except metadata.MetadataError:
                return []
        with ThreadPoolExecutor(3) as pool:
            rows = [r for found in pool.map(look, kinds) for r in found]
        want = library.norm(q)
        # The ones whose name matches best first.
        rows.sort(key=lambda r: (want not in library.norm(r.get("title") or "") and
                                 not any(want in library.norm(a) for a in r.get("aliases") or []),
                                 -(r.get("popularity") or 0)))
        return {"results": self._mark_listed(rows[:8])}

    def api_import_mal(self, body):
        """Bring in a MyAnimeList list, by user name or from an export file."""
        statuses = [x for x in body.get("statuses") or [] if x in importer.STATUSES]
        if not statuses and not body.get("preview"):
            raise ApiError("choose which parts of your list to bring in")
        if body.get("file"):
            try:
                entries = importer.read_export(str(body["file"]))
            except OSError as e:
                raise ApiError(f"couldn't open that file ({e})") from e
        else:
            s = self.app.settings.get()
            try:
                entries = metadata.MAL(s["mal_client_id"]).user_list(str(body.get("user") or ""))
            except metadata.MetadataError as e:
                if "doesn't have" in str(e) or "rejected" in str(e):
                    raise ApiError("couldn't read that list - check the user name, and that the list "
                                   "is public (or use your exported list file instead)") from e
                raise
        if not entries:
            raise ApiError("that list is empty")
        counts = {st: sum(1 for e in entries if e["status"] == st) for st in importer.STATUSES}
        if body.get("preview"):
            return {"counts": counts, "total": len(entries)}
        if not self.app.settings.get()["mal_client_id"]:
            raise ApiError("add your MyAnimeList Client ID in Settings first")
        return self.app.importer.start(entries, statuses)

    def api_import_status(self, body):
        return self.app.importer.state()

    def api_party_start(self, body):
        item = self.app.item(str(body["item_id"])) if body.get("item_id") else None
        return self.app.party_start(item, int(body.get("season") or 1), int(body.get("episode") or 1),
                                    str(body.get("path") or ""), str(body.get("source") or ""), str(body.get("site") or ""),
                                    how=str(body.get("how") or ""))

    def api_party_join(self, body):
        return self.app.party_join(str(body.get("code") or ""))

    def api_party_use_file(self, body):
        return self.app.party_use_file(str(body.get("path") or ""))

    def api_party_follow(self, body):
        """A guest who closed their player, watching along again."""
        party = self.app.party
        if not party or not party.active or party.role != "guest":
            raise ApiError("you aren't in someone's watch party")
        party._last_leader_key = None
        party._follow_leader()
        return {"ok": True}

    def api_open_window(self, body):
        """One of the app's own pages in a window of its own (the watch party)."""
        kind = body.get("kind")
        port = self.server.server_address[1]
        if kind == "party":
            ok = launcher.open_window(f"http://127.0.0.1:{port}/#/party-window", "Watch party - Watch Anything",
                                      380, 640, kind="party", on_top=body.get("on_top", True) is not False)
        elif kind == "party-on-top":
            # The party window's pin: above every window (the player too), or behind it.
            return {"ok": launcher.set_on_top("party", body.get("on_top") is not False)}
        elif kind == "sign-out":
            launcher.sign_out_of_sites(self.app.data_dir / "browser-data")
            return {"ok": True}
        else:
            raise ApiError("unknown window")
        if not ok:
            raise ApiError("separate windows need the app window (they don't work in a browser tab)")
        return {"ok": True}

    def api_party_status(self, body):
        return self.app.party_status()

    def api_party_leave(self, body):
        return self.app.party_leave()

    def api_party_host(self, body):
        return self.app.party_make_host(str(body.get("name") or ""))

    def api_party_peek(self, body):
        """What an invite is for, before joining."""
        invite = syncplay.read_invite(body.get("code"))
        mine = self.app.store.find_source(invite["source"], invite["source_id"]) if invite["source"] else None
        invite["in_list"] = mine["id"] if mine else None
        return invite

    def api_test_ai(self, body):
        """Save the key, then check it answers (saved even if it doesn't, with the reason shown).
        Your own helper is checked as it's filled in, before it's saved."""
        provider = body.get("provider")
        if provider == "custom":
            from .config import clean_ai_custom
            try:
                [conf] = clean_ai_custom([body.get("conf") or {}]) or [None]
            except ValueError as e:
                raise ApiError(str(e)) from e
            if not conf:
                raise ApiError("fill in its address first")
            settings = dict(self.app.settings.get())
            settings["ai_custom"] = [c for c in settings["ai_custom"] if c["id"] != conf["id"]] + [conf]
            pid = f"custom:{conf['id']}"
            key = ai.key_of(settings, pid)
            if not key:
                return {"ok": False, "error": "paste its API key first", "tried": []}
            return dict(ai.test_key(pid, key, settings), id=conf["id"])
        if provider not in ai.PROVIDERS:
            raise ApiError("unknown AI helper")
        key = str(body.get("key") or "").strip()
        self.app.settings.update({ai.KEY_SETTINGS[provider]: key})
        if not key:
            return {"ok": False, "error": "paste your key first", "tried": []}
        return ai.test_key(provider, key)

    def api_ai_presets(self, body):
        return {"presets": ai.CUSTOM_PRESETS}

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
        return self.app.add_title(body.get("type"), body.get("source"), body.get("source_id"),
                                  body.get("collections", []), hint=body.get("hint"),
                                  listed=body.get("listed", True) is not False)

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
        if "watched" in body:
            # "Watched": kept as part of your collection, out of the everyday rows.
            patch["in_watched"] = (item.get("in_watched") or time.time()) if body["watched"] else None
            if body["watched"]:
                patch["watching"] = False                # (finished: off Currently watching)
        if "watching" in body:
            patch["watching"] = bool(body["watching"])
        if "review" in body:
            r = body["review"]
            if r is None or (isinstance(r, dict) and not r.get("stars") and not str(r.get("text") or "").strip()):
                patch["review"] = None
            elif isinstance(r, dict):
                stars = int(r.get("stars") or 0)
                if stars and not 1 <= stars <= 5:
                    raise ApiError("a review is 1 to 5 stars")
                patch["review"] = {"stars": stars or None, "text": str(r.get("text") or "").strip()[:5000],
                                   "at": time.time()}
            else:
                raise ApiError("a review is stars and some words")
        if body.get("opened"):
            patch["opened_at"] = time.time()
        if "listed" in body:
            patch["listed"] = bool(body["listed"])
        if "play_with" in body:
            if body["play_with"] not in PLAY_WITH:
                raise ApiError("unknown way to play")
            patch["play_with"] = body["play_with"]
        if "online_service" in body:
            patch["online_service"] = str(body["online_service"] or "")[:40]
        if "keep_magnet" in body:
            patch["keep_magnet"] = bool(body["keep_magnet"])
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
        # Its downloads wait for you; Sort now won't put them back by itself.
        self.app.library.forget_item(item_id)
        self.app.torrents.forget_item(item_id)
        self.app.scraper.forget(item_id)
        return {"ok": True}

    # ---- torrents

    def api_torrents(self, body):
        return self.app.torrents.all()

    def api_item_torrents(self, body, item_id):
        item = self.app.item(item_id)
        out = self.app.torrents.all(item_id)
        out["keep"] = bool(self.app.torrents.keep.get(item_id))
        out["episodes"] = self.app.torrents.episodes(item)
        return out

    def api_add_torrent(self, body, item_id):
        self.app.item(item_id)
        got = self.app.torrents.add(item_id, str(body.get("magnet") or ""))
        if body.get("replace"):
            # One magnet per title: the one attached before goes (its downloads too).
            self.app.torrents.replace_for(item_id, got.get("hash") if isinstance(got, dict) else "")
        return got

    def api_torrent_keep(self, body, item_id):
        self.app.item(item_id)
        self.app.torrents.set_keep(item_id, bool(body.get("keep")))
        return {"keep": bool(body.get("keep"))}

    def api_torrent_move(self, body, h):
        """A title's magnets in order: the first one's file is used for an episode both have."""
        self.app.torrents.move(h, -1 if body.get("dir") == "up" else 1)
        return self.app.torrents.all(self.app.torrents.records[h]["item_id"]) if h in self.app.torrents.records else {}

    def api_download_full(self, body, item_id):
        """An episode (or the film) downloaded whole now, without playing it."""
        item = self.app.item(item_id)
        key = f'{int(body.get("season") or 1)}:{int(body.get("episode") or 1)}' if item["format"] == "series" else "movie"
        if item["format"] == "series":
            ep = self.app.torrents.episodes(item).get(key)
        else:
            vids = [v for v in self.app.torrents.videos(item)]
            ep = vids[0]["torrent"] if vids else None
        if not ep:
            raise ApiError("its torrent doesn't have that one")
        self.app.torrents.download_whole(ep["hash"], ep["index"])
        return {"ok": True, "name": ep.get("name") or ""}

    def api_torrent_pause(self, body, h):
        self.app.torrents.pause(h, bool(body.get("paused", True)))
        return self.app.torrents.view(h)

    def api_torrent_file(self, body, h, index):
        if "priority" in body:
            self.app.torrents.set_priority(h, int(index), int(body["priority"]))
        if "episode" in body:
            item_id = str(body.get("item_id") or "")
            if item_id:
                self.app.item(item_id)
            self.app.torrents.set_episode(h, int(index), body["episode"], item_id or None)
        return self.app.torrents.view(h)

    def api_find_torrent(self, body, item_id):
        item = self.app.item(item_id)
        if not torrents_mod.available():
            raise ApiError(self.app.torrents.error)
        season = body.get("season")
        season = "all" if season == "all" else (int(season) if str(season or "").isdigit() else None)
        return self.app.site_jobs.start(item, season)

    def api_find_torrent_state(self, body, item_id):
        self.app.item(item_id)
        return self.app.site_jobs.get(item_id) or {"running": False, "choices": [], "error": "", "added": None}

    def api_find_torrent_use(self, body, item_id):
        self.app.item(item_id)
        try:
            return self.app.site_jobs.use(item_id, str(body.get("key") or ""), str(body.get("how") or ""))
        except (sites.SiteError, torrents_mod.TorrentError) as e:
            raise ApiError(str(e)) from e

    def api_connections(self, body, item_id):
        return self.app.connections(self.app.item(item_id))

    def api_connect_file(self, body, item_id):
        item = self.app.item(item_id)
        key = str(body.get("key") or "")
        if key not in ("none", "auto", "movie") and not re.fullmatch(r"\d+:\d+", key):
            raise ApiError("say which episode it is, like 1:5")
        self.app.connect_file(item, str(body.get("path") or ""), key)
        return self.app.connections(self.app.item(item_id))

    def api_sort_again(self, body, item_id):
        return self.app.sort_again(self.app.item(item_id))

    def api_stream_buffer(self, body, item_id):
        """Getting a torrent's episode ready to play: how far along it is."""
        item = self.app.item(item_id)
        path = str(body.get("path") or "")
        found = self.app.torrents.parse_url(path)
        if not found or not self.app.torrents.owns(item_id, path):
            raise ApiError("that isn't one of this title's torrent episodes")
        at = 0.0
        season, episode = body.get("season"), body.get("episode")
        key = f"{season}:{episode}" if item["format"] == "series" and season is not None and episode is not None else "movie"
        saved = item["positions"].get(key)
        if not body.get("from_start") and saved and saved.get("dur"):
            at = round(player.resume_point(saved) / saved["dur"], 3)
        try:
            first = found not in self.app.torrents.buffers
            if first:
                self.app.party_upcoming(item, season, episode, path)
            self.app.torrents.prepare(*found)
            out = self.app.torrents.buffer(*found, at=at)
            if first:
                # Its subtitle (small) comes along meanwhile.
                threading.Thread(target=self.app.torrents.subtitles, args=found, kwargs={"wait": 60}, daemon=True).start()
        except torrents_mod.TorrentError as e:
            raise ApiError(str(e)) from e
        return out

    def api_test_site(self, body):
        try:
            [site] = sites.clean([body.get("site") or {}]) or [None]
        except ValueError as e:
            raise ApiError(str(e)) from e
        if not site:
            raise ApiError("add the site's search link first")
        query = str(body.get("query") or "").strip()
        if not query:
            anime = [i for i in self.app.store.list() if i["type"] == "anime"]
            query = (anime[0].get("original_title") or anime[0]["title"]) if anime else "naruto"
        try:
            return dict(sites.test_site(site, query), query=query)
        except sites.SiteError as e:
            raise ApiError(str(e)) from e

    def api_find_video_site_search(self, body):
        """A site given by its home page: its search link, found by searching it."""
        url = str(body.get("url") or "").strip()
        if not url:
            raise ApiError("paste the site's address first")
        types = body.get("types") or ["anime"]
        log = []
        for kind in [t for t in ("anime", "tv", "movie") if t in types] or ["anime"]:
            got = scrape.find_search(url, kind, renderer=self.app._render_page, log=log)
            if got:
                return {"ok": True, "url": got["url"], "how": got["how"], "log": log}
        return {"ok": False, "log": log, "error": "its search link wasn't found - search for any title on the site and "
                                                  "paste the results page's address with {title} where the name was"}

    def api_test_video_site(self, body):
        try:
            [site] = scrape.clean([body.get("site") or {}]) or [None]
        except ValueError as e:
            raise ApiError(str(e)) from e
        if not site:
            raise ApiError("add the site's search link first")
        query = str(body.get("query") or "").strip()
        mine = [i for i in self.app.store.list() if i["type"] in site["types"]]
        item = next((i for i in mine if not query or i["title"] == query), None)
        if site.get("kind") in ("archive", "json_api"):
            try:
                return scrape.test_site(site, query or (item["title"] if item else "Frieren"))
            except scrape.ScrapeError as e:
                raise ApiError(str(e)) from e
        # As a real search goes: every form of the name (all the titles it has, shorter ones),
        # and the page after its scripts have run when it needs them.
        item = dict(item) if item else {"id": "test", "title": query or "Frieren", "type": site["types"][0],
                                        "format": "movie" if site["types"][0] == "movie" else "series"}
        try:
            found = self.app.scraper.search(site, item)
        except scrape.ScrapeError as e:
            raise ApiError(str(e)) from e
        return {"url": scrape.search_url(site, item["title"]), "query": item["title"], "count": len(found),
                "examples": [{"name": c["name"], "score": c["score"]} for c in found[:5]]}

    def api_diagnose_video_site(self, body):
        """A video site's "Test with an episode": every step, from scratch."""
        try:
            [site] = scrape.clean([body.get("site") or {}]) or [None]
        except ValueError as e:
            raise ApiError(str(e)) from e
        if not site:
            raise ApiError("add the site's search link first")
        item = self.app.item(str(body.get("item_id") or ""))
        series = item["format"] == "series"
        return self.app.scraper.diagnose(site, item, int(body.get("season") or 1) if series else None,
                                         int(body.get("episode") or 1) if series else None)

    def api_play_scraped(self, body, item_id):
        return self.app.play_scraped(self.app.item(item_id), body)

    def api_scraped_unlink(self, body, item_id):
        return self.app.scraped_unlink(self.app.item(item_id), body)

    def api_subs_info(self, body, item_id):
        return self.app.subs_info(self.app.item(item_id))

    def api_find_subs(self, body, item_id):
        return self.app.find_subs(self.app.item(item_id), body)

    def api_answer_subs(self, body, item_id):
        self.app.item(item_id)
        return self.app.answer_subs(item_id, str(body.get("answer") or ""))

    def api_subs_folder(self, body, item_id):
        return self.app.subs_from_folder(self.app.item(item_id), body)

    def api_keep_subs(self, body, item_id):
        return self.app.set_keep_subs(self.app.item(item_id), body)

    def api_clear_subs(self, body, item_id):
        return self.app.clear_subs(self.app.item(item_id), body)

    def api_find_party_server(self, body):
        """A watch-party server that answers now (Settings: Find a working server)."""
        addr = syncplay.working_server()
        if not addr:
            raise ApiError("none of Syncplay's watch-party servers is working right now - a watch party then "
                           "goes through the backup relay by itself")
        self.app.settings.update({"party_server": addr})          # (kept: used from now on)
        return {"server": addr, "saved": True}

    # What a guest in someone's watch party can't start (the host chooses what plays).
    HOST_PLAYS = {"play", "play_scraped", "online", "player_vlc"}

    def api_pick_start(self, body, item_id):
        return self.app.pick_start(self.app.item(item_id), body)

    def api_pick_view(self, body, job_id):
        return self.app.pick_view(job_id)

    def api_pick_use(self, body, job_id):
        return self.app.pick_use(job_id, body)

    def api_pick_close(self, body, job_id):
        return self.app.pick_close(job_id)

    def api_clear_connections(self, body, item_id):
        return self.app.clear_connections(self.app.item(item_id), body)

    def api_online_links(self, body, item_id):
        return self.app.online_links(self.app.item(item_id))

    def api_link_all(self, body, item_id):
        return self.app.link_all(self.app.item(item_id), body)

    def api_relink_episode(self, body, item_id):
        return self.app.relink_episode(self.app.item(item_id), body)

    def api_online_prefs(self, body, item_id):
        return self.app.set_online_prefs(self.app.item(item_id), body)

    def api_torrent_plan(self, body, h):
        return self.app.torrents.plan_view(h)

    def api_torrent_plan_group(self, body, h):
        sid = body.get("source_id")
        return self.app.torrents.set_group(h, str(body.get("group") or ""), str(sid) if sid else None)

    def api_torrent_plan_redo(self, body, h):
        self.app.torrents._rec(h)
        self.app.torrents.make_plan(h, use_ai=body.get("ai", True) is not False)
        return self.app.torrents.plan_view(h)

    def api_torrent_plan_add(self, body, h):
        """Add the pack's titles that aren't in your list yet (all, or the ones chosen)."""
        rec = self.app.torrents._rec(h)
        wanted = body.get("source_ids")
        missing = self.app.torrents._missing(rec)
        added, failed = [], []
        for sid in missing:
            if isinstance(wanted, list) and sid not in [str(x) for x in wanted]:
                continue
            try:
                added.append(self.app.add_title("anime", "mal", sid)["id"])
            except (ApiError, metadata.MetadataError) as e:
                failed.append(f"{sid}: {e}")
        out = self.app.torrents.plan_view(h)
        out.update(added=len(added), failed=failed)
        return out

    def api_torrent_file_delete(self, body, h, index):
        self.app.torrents.delete_file(h, int(index))
        return self.app.torrents.view(h)

    def api_remove_torrent(self, body, h):
        return self.app.torrents.remove(h, delete_files=self.query.get("files", "1") != "0")

    def api_torrent_folder(self, body, h):
        rec = self.app.torrents.records.get(h)
        if not rec:
            raise ApiError("that torrent isn't added any more", HTTPStatus.NOT_FOUND)
        folder = Path(rec["save_path"]) / Path((rec.get("files") or [{"path": ""}])[0]["path"]).parts[0] \
            if rec.get("files") else Path(rec["save_path"])
        try:
            launcher.reveal(str(folder if folder.exists() else rec["save_path"]))
        except launcher.LaunchError as e:
            raise ApiError(str(e)) from e
        return {"ok": True}

    def api_delete_file(self, body, item_id):
        """Delete one episode's file: a torrent's is deleted for good (and not
        downloaded again unless you play it), your own goes to the Recycle Bin / Trash."""
        item = self.app.item(item_id)
        path = str(body.get("path") or "")
        found = self.app.torrents.find_file(path)
        if found:
            self.app.torrents.delete_file(*found)
            return {"ok": True, "where": "torrent"}
        if not self.app.playable(item, path):
            raise ApiError("that file isn't one of this title's")
        try:
            launcher.trash(path)
        except launcher.LaunchError as e:
            raise ApiError(str(e)) from e
        self.app.library.scan(force=True)
        return {"ok": True, "where": "trash"}

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
        return self.app.watch_online(self.app.item(item_id), str(body.get("service") or "") or None)

    def api_online_options(self, body, item_id):
        return self.app.online_options(self.app.item(item_id))

    def api_service_presets(self, body):
        return {"presets": services.PRESETS}

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
        return {"settings": self.app.settings.get(), "status": self.app.status(),
                "browsers": launcher.list_browsers(), "players": launcher.list_players()}

    def api_put_settings(self, body):
        if self.remote:
            for key in ("phone_access", "phone_port"):
                body.pop(key, None)
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
        if any(k in body for k in ("phone_access", "phone_port", "phone_keep_awake")):
            self.app.remote.start()
            self.app.remote.refresh()
        return {"settings": settings, "status": self.app.status()}

    def api_test_settings(self, body):
        return metadata.test_keys(self.app.settings.get())

    def api_phone_progress(self, body):
        try:
            self.app.item(str(body.get("item_id") or ""))
            self.app.phone_progress(body["item_id"], body.get("key") or "movie", body.get("pos") or 0,
                                    body.get("dur") or 0, bool(body.get("finished")))
        except (KeyError, TypeError, ValueError) as e:
            raise ApiError(f"bad progress report ({e})") from e
        return {"ok": True}

    def api_fs(self, body):
        """The app's own file browser (on a phone, where there are no file dialogs):
        a folder's folders, and its files of one kind."""
        exts = {"video": scanner.VIDEO_EXTS, "subtitle": {".srt", ".ass", ".ssa", ".vtt", ".sub"},
                "list": {".xml", ".gz"}, "collection": {".wlcollection", ".txt", ".json"}}
        home = "/storage/emulated/0" if launcher.ANDROID else str(Path.home())
        path = str(self.query.get("path") or "") or home
        want = exts.get(str(self.query.get("types") or "video"), scanner.VIDEO_EXTS)
        if not os.path.isdir(path):
            path = home
        dirs, files = [], []
        try:
            with os.scandir(path) as it:
                for e in it:
                    if e.name.startswith("."):
                        continue
                    try:
                        if sys.platform == "win32" and e.stat().st_file_attributes & 0x6:
                            continue                 # (Windows' hidden and system folders: AppData, Cookies...)
                        if e.is_dir():
                            dirs.append(e.name)
                        elif Path(e.name).suffix.lower() in want:
                            files.append({"name": e.name, "size": e.stat().st_size})
                    except OSError:
                        continue
        except PermissionError:
            raise ApiError("the app isn't allowed to look at your files yet - allow it (All files access) and try again")
        except OSError as e:
            raise ApiError(f"couldn't open that folder ({e})") from e
        if launcher.ANDROID:
            parent = os.path.dirname(path.rstrip("/")) or "/"
            parent = parent if path != home and len(path) > len("/storage") else None
            places = [("Phone", home), ("Download", f"{home}/Download"), ("Movies", f"{home}/Movies"), ("DCIM", f"{home}/DCIM")]
        else:
            path = os.path.normpath(path)
            parent = os.path.dirname(path)
            parent = parent if parent != path else None           # (a drive's top: nothing above it)
            s = self.app.settings.get()
            places = [("Your downloads", s.get("downloads_folder") or ""), ("Home", home),
                      ("Downloads", str(Path.home() / "Downloads"))]
            if sys.platform == "win32":
                places += [(f"{d}:", f"{d}:\\") for d in "CDEFGHIJKLMNOPQRSTUVWXYZ" if os.path.isdir(f"{d}:\\")]
            elif sys.platform == "darwin" and os.path.isdir("/Volumes"):
                places += [(v, f"/Volumes/{v}") for v in sorted(os.listdir("/Volumes")) if not v.startswith(".")]
        seen, out = set(), []
        for n, p in places:
            if p and os.path.isdir(p) and os.path.normcase(p) not in seen:
                seen.add(os.path.normcase(p))
                out.append({"name": n, "path": p})
        return {"path": path, "parent": parent,
                "dirs": sorted(dirs, key=str.lower), "files": sorted(files, key=lambda f: f["name"].lower()),
                "places": out}

    def api_browse(self, body):
        if self.remote:
            raise ApiError("choose it in the app's own file browser (this computer's file windows stay on this computer)")
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


def remote_listener(app: App, ip: str, port: int):
    """The app on one of this computer's network addresses, for paired phones (remote.py)."""
    handler = type("RemoteHandler", (Handler,), {"app": app, "remote": True})
    server = ThreadingHTTPServer((ip, port), handler)
    server.daemon_threads = True
    return server


def make_server(app: App, port: int):
    handler = type("BoundHandler", (Handler,), {"app": app})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    app.torrents.port = server.server_address[1]     # for the stream links the player opens
    return server


def start_background_refresh(app: App):
    app.fill_pending()
    app.remote.start()
    # Shared collections that stay up to date: yours put on the relays, others' looked at.
    live = threading.Timer(20, app._live_loop)
    live.daemon = True
    live.start()
    threading.Thread(target=app.refresh_stale, daemon=True).start()
    threading.Thread(target=library.watch, args=(app,), daemon=True).start()
