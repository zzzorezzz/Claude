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

from concurrent.futures import ThreadPoolExecutor

from . import ai, dialogs, discover, importer, launcher, library, metadata, player, scanner, services, share, sites, sorter, syncplay
from . import mood as mood_mod
from . import party as party_mod
from . import torrents as torrents_mod
from .config import Settings, read_json, write_json
from .store import METADATA_FIELDS, TYPES, Store, new_item, next_episode

WEB_DIR = Path(__file__).resolve().parent / "web"
REFRESH_AFTER = 7 * 24 * 3600
RELATED_TTL = 3 * 24 * 3600


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
        self._connected_jobs = {}           # Everything connected being looked for: how far along
        self._filler = ThreadPoolExecutor(4, thread_name_prefix="fill")   # titles added straight away
        self.moods = mood_mod.Jobs(self)                                   # I'm in the mood for…
        self.party = None
        self.party_last = None
        self._party_lock = threading.Lock()
        self._lock = threading.Lock()
        self._ai_streams = {}               # (item, country, services) -> (when, {name: has it})

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
                "windows": launcher.can_open_windows(), "theme": s["theme"]}

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
        with whether it's in your list already."""
        text = str(body.get("code") or "")
        path = str(body.get("file") or "")
        if path:
            try:
                if os.path.getsize(path) > 2_000_000:
                    raise ApiError("that file is too big to be a shared collection")
                text = Path(path).read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                raise ApiError(f"couldn't open that file ({e})") from e
        try:
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
        ok = 0
        for r in data["items"]:
            try:
                self.add_title(r["type"], r["source"], r["source_id"], [col["id"]], hint=r, listed=listed)
                ok += 1
            except (metadata.MetadataError, ApiError, ValueError, KeyError):
                pass
        return {"collection": col, "titles": ok, "new": sum(1 for r in data["items"] if not r["in_list"]),
                "failed": len(data["items"]) - ok, "listed": listed}

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

    def _party_name(self):
        return self.settings.get()["party_name"] or syncplay.default_name()

    def _need_mpv(self):
        mpv = player.find_mpv()
        if not mpv:
            raise ApiError("watching together needs the built-in player, which is missing")
        return mpv

    def _party_ended(self, party, reason):
        with self._party_lock:
            if self.party is party:
                self.party = None
                self.party_last = {"ended": reason, "at": time.time(), "room": party.room}

    def _start_party(self, party):
        with self._party_lock:
            old, self.party = self.party, party
        if old and old.active:
            old.leave("you started another watch party")

    def party_start(self, item=None, season=None, episode=None, path=""):
        """Host: open a private room and hand back an invite. The party isn't tied
        to anything playing: whatever you play while it's on, everyone follows.
        With a title, its episode starts playing for the party too."""
        self._need_mpv()
        with self._party_lock:
            current = self.party
        if current and current.active and current.role == "host":
            party = current                  # already hosting: just play this in it
        else:
            room = syncplay.new_room()
            party = party_mod.Party(self._party_server(), room, self._party_name(), "host",
                                    context=metadata.ssl_context(), on_end=self._party_ended,
                                    avatar=self.settings.get()["party_avatar"])
            try:
                party.connect()
            except party_mod.PartyError as e:
                raise ApiError(str(e)) from e
            party.invite = syncplay.make_invite(party.server, room, host=party.me)
            self._start_party(party)
        out = {"ok": True, "code": party.invite, "room": party.room, "server": party.server, "file": ""}
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
            self.play(item, {"path": target["path"], "subtitle": subs[0]["path"] if subs else None,
                             "season": season, "episode": episode, "paused": True})
            out["file"] = target["name"]
            threading.Thread(target=self._party_hello, args=(party,), daemon=True).start()
        return out

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
        self._start_party(party)
        party._follow_leader()
        return {"ok": True, "leader": party.leader_name(), "invite": invite}

    def _party_now(self, party, entry, upcoming=False):
        """The host plays (or is about to play) `entry`: the invite carries it, and
        with `upcoming` everyone starts getting it ready now."""
        meta = dict(entry.get("party") or {}, label=entry.get("label") or "")
        try:
            party.invite = syncplay.make_invite(party.server, party.room, host=party.me,
                                                now=meta if meta.get("magnet") else None)
        except (TypeError, ValueError):
            pass
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
        """A guest joining: the host's torrent episode, added and started at once."""
        try:
            item = self._party_item(now.get("src"), now.get("id"), now.get("title"), now.get("type"))
            if item:
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
        if meta.get("peer"):
            self.torrents.add_peer(h, meta["peer"])
        path, _size = self.torrents.file_in(h, index)
        if meta.get("peer"):
            self.torrents.add_peer(h, meta["peer"])       # (again: now it knows what to ask for)
        self.torrents.prepare(h, index)
        return self.torrents.stream_url(h, index, path)

    def _party_open(self, party, entry):
        """A guest's player, opened for what the host plays (paused until it's in step)."""
        item = self.store.get(entry["item_id"])
        session = self.player.play(self._session(self._need_mpv(), item, [entry], 0,
                                                 ["--pause", "--keep-open=yes"]))
        party.attach(session)

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
                                   bool(v.get("torrent")), [-x for x in library.quality(v)],
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
                                       s["queue_next_episodes"])
        start = 0 if body.get("from_start") else player.resume_point(item["positions"].get(entries[0]["key"]))
        if launcher.ANDROID:
            # On a phone: the phone app's own player plays the list, and reports back
            # where you are (/api/android/progress).
            self.store.update(item["id"], patch)
            return {"ok": True, "player": "android", "item_id": item["id"], "start": start,
                    "anime": item["type"] == "anime", "file": entries[0].get("name") or Path(path).name,
                    "queued": len(entries) - 1, "resumed_at": start, "stream": bool(found),
                    "entries": [{k: e.get(k) for k in ("path", "sub", "key", "label")} for e in entries]}
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
                                                         ["--pause"] if body.get("paused") or hold else ()))
                with self._party_lock:
                    party = self.party
                if party and party.active and party.role == "host":
                    party.attach(session, hold=hold and not body.get("paused"))   # whatever the host plays, the party follows
                    self._party_now(party, entries[0])
                self.store.update(item["id"], patch)
                return {"ok": True, "player": "mpv", "file": entries[0].get("name") or Path(path).name,
                        "queued": len(entries) - 1, "resumed_at": start, "stream": bool(found),
                        "subtitle": Path(entries[0]["sub"]).name if entries[0].get("sub") else ""}
            note = "The built-in player is missing, so VLC was used."
        if wanted == "custom" and s["custom_player_path"]:
            # Your own player: the file (or its stream link) is handed to it; it keeps its own place.
            try:
                launcher.play_in_player(s["custom_player_path"], entries[0]["path"])
            except launcher.LaunchError as e:
                raise ApiError(str(e)) from e
            self.store.update(item["id"], patch)
            return {"ok": True, "player": "custom", "file": Path(path).name, "queued": 0, "resumed_at": 0,
                    "player_name": Path(s["custom_player_path"]).stem}
        self._play_vlc(entries, start)
        self.store.update(item["id"], patch)
        return {"ok": True, "player": "vlc", "file": Path(path).name, "queued": len(entries) - 1,
                "resumed_at": start, "note": note}

    def _session(self, mpv, item, entries, start=0, extra=()):
        if self.torrents.records or any(self.torrents.parse_url(e["path"]) for e in entries):
            # Streams: wait patiently for data, and keep plenty of it ahead.
            extra = ["--network-timeout=180", "--cache=yes", "--demuxer-max-bytes=200MiB",
                     "--demuxer-readahead-secs=90"] + list(extra)
        return player.Session(
            mpv, item["id"], entries, start=start,
            extra_args=player.script_args(self.settings.get()["skip_intro"], self.settings.get()["theme"]) + list(extra),
            audio_langs="jpn,ja,japanese,eng,en" if item["type"] == "anime" else "",
            on_progress=self._save_position, on_finished=self._finished_entry)

    def _entries(self, item, path, subtitle, season=None, episode=None, queue=True):
        """What to play: the file, then (with `queue`) the episodes after it."""
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
                after = ordered[ordered.index(path) + 1:] if path in ordered else []
                for p in after[:50]:
                    v = by_path[p]
                    subs = subtitles(v)
                    # Keep using the same kind of subtitle (none / auto) for the queue.
                    sub = subs[0]["path"] if subs and subtitle else None
                    entry = {"path": v["path"], "sub": sub, "key": keys.get(v["path"], v["path"])}
                    if v.get("torrent"):
                        entry["torrent"] = v["torrent"]
                    entries.append(entry)
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
        ("GET", r"/api/items/(\w+)/connected", "connected"),
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
        if url.path.startswith("/stream/") and method in ("GET", "HEAD"):
            return self._stream(url.path)
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
                except torrents_mod.TorrentError as e:
                    return self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
                except (ValueError, TypeError) as e:
                    return self._json({"error": str(e) or "invalid request"}, HTTPStatus.BAD_REQUEST)
                except Exception as e:  # keep the app alive whatever happens
                    return self._json({"error": f"unexpected error: {e}"},
                                      HTTPStatus.INTERNAL_SERVER_ERROR)
        return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

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

    def api_library_route(self, body):
        """Move one file: to a title and episode, out of its title, or ignore it."""
        try:
            self.app.library.route(str(body.get("rel") or ""), str(body.get("item") or ""),
                                   body.get("season"), body.get("episode"), body.get("kind"))
        except ValueError as e:
            raise ApiError(str(e)) from e
        return self.app.library.state()

    def api_share_collection(self, body, col_id):
        data = self.app.share_collection(col_id)
        return {"code": share.to_code(data), "name": data["name"], "count": len(data["items"])}

    def api_save_collection(self, body, col_id):
        """Save a collection as a file (in your Downloads folder) and show it."""
        data = self.app.share_collection(col_id)
        folder = Path.home() / "Downloads"
        folder = folder if folder.is_dir() else Path.home()
        path = folder / share.file_name(data["name"])
        try:
            path.write_text(share.to_file_text(data), encoding="utf-8")
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
                                    str(body.get("path") or ""))

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
        return self.app.torrents.add(item_id, str(body.get("magnet") or ""))

    def api_torrent_keep(self, body, item_id):
        self.app.item(item_id)
        self.app.torrents.set_keep(item_id, bool(body.get("keep")))
        return {"keep": bool(body.get("keep"))}

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
        return self.app.site_jobs.start(item)

    def api_find_torrent_state(self, body, item_id):
        self.app.item(item_id)
        return self.app.site_jobs.get(item_id) or {"running": False, "choices": [], "error": "", "added": None}

    def api_find_torrent_use(self, body, item_id):
        self.app.item(item_id)
        try:
            return self.app.site_jobs.use(item_id, str(body.get("key") or ""))
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
        parent = os.path.dirname(path.rstrip("/")) or "/"
        places = [{"name": n, "path": p} for n, p in (
            [("Phone", home), ("Download", f"{home}/Download"), ("Movies", f"{home}/Movies"), ("DCIM", f"{home}/DCIM")]
            if launcher.ANDROID else [("Home", home), ("Downloads", str(Path.home() / "Downloads"))]) if os.path.isdir(p)]
        return {"path": path, "parent": parent if path != home and len(path) > len("/storage") else None,
                "dirs": sorted(dirs, key=str.lower), "files": sorted(files, key=lambda f: f["name"].lower()),
                "places": places}

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
    app.torrents.port = server.server_address[1]     # for the stream links the player opens
    return server


def start_background_refresh(app: App):
    app.fill_pending()
    threading.Thread(target=app.refresh_stale, daemon=True).start()
    threading.Thread(target=library.watch, args=(app,), daemon=True).start()
