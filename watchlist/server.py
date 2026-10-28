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

from . import ai, dialogs, discover, importer, launcher, library, metadata, player, scanner, services, share, sorter, syncplay
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
        self.party = None
        self.party_last = None
        self._party_lock = threading.Lock()

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
                "mpv": player.find_mpv(), "ai": [ai.NAMES[p] for p in ai.available(s)],
                "ai_resting": ai.resting(s),
                "party": bool(self.party and self.party.active),
                "services": [{"name": x["name"], "types": x["types"], "on": x["on"]} for x in s["services"]],
                "torrents": {"available": torrents_mod.available(), "count": len(self.torrents.records),
                             "delete_after": torrents_mod.DELETE_AFTER}}

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

    def add_title(self, item_type, source, source_id, collections=()):
        if item_type not in TYPES:
            raise ApiError("type must be movie, tv or anime")
        existing = self.store.find_source(source, source_id)
        if existing:
            return existing
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
        item["collections"] = [c for c in collections if isinstance(c, str)]
        return self.store.add(item)

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
        """Add a shared collection: its titles that aren't in your list yet, and
        the collection itself (under its own name, or with "(shared)" after it)."""
        data = self.read_shared(body)
        name = str(body.get("name") or data["name"]).strip() or "Shared collection"
        existing = {c["name"].lower() for c in self.store.collections() if not c.get("type")}
        final, n = name, 1
        while final.lower() in existing:
            n += 1
            final = f"{name} (shared)" if n == 2 else f"{name} (shared {n - 1})"
        col = self.store.add_collection(final)

        def one(r):
            try:
                return self.add_title(r["type"], r["source"], r["source_id"])
            except (metadata.MetadataError, ApiError, ValueError, KeyError):
                return None

        with ThreadPoolExecutor(4) as pool:
            added = list(pool.map(one, data["items"]))
        ok = [a for a in added if a]
        for item in ok:
            fresh = self.store.get(item["id"])
            self.store.set_item_collections(item["id"], fresh["collections"] + [col["id"]])
        return {"collection": col, "titles": len(ok), "new": sum(1 for r in data["items"] if not r["in_list"]),
                "failed": len(data["items"]) - len(ok)}

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

    def party_start(self, item, season, episode, path=""):
        """Host: open a private room, start the episode paused, and hand back an invite."""
        mpv = self._need_mpv()
        if path and not self.playable(item, path):
            raise ApiError("that file isn't in this title's folder or your downloads")
        info = self.local_info(item, season, episode, path)
        target = info["target"]
        if not target:
            what = f"{item['title']} S{season} E{episode}" if item["format"] == "series" else item["title"]
            return {"ok": False, "need_file": True, "item_id": item["id"],
                    "message": f"You don't have {what} in your downloads, or in a torrent for it."}
        subs = info["subtitles"]
        if target.get("stream"):
            subs = self._ready_stream(target) or subs
        entries, patch = self._entries(item, target["path"], subs[0]["path"] if subs else None,
                                       season if item["format"] == "series" else None, episode,
                                       self.settings.get()["queue_next_episodes"])
        room = syncplay.new_room()
        party = party_mod.Party(self._party_server(), room, self._party_name(), "host",
                                context=metadata.ssl_context(), on_end=self._party_ended)
        try:
            party.connect()
        except party_mod.PartyError as e:
            raise ApiError(str(e)) from e
        self._start_party(party)
        start = player.resume_point(item["positions"].get(entries[0]["key"]))
        session = self.player.play(self._session(mpv, item, entries, start, ["--pause"]))
        party.attach(session)
        self.store.update(item["id"], patch)
        threading.Thread(target=self._party_hello, args=(party,), daemon=True).start()
        series = item["format"] == "series" and not item_kind_is_single(item)
        code = syncplay.make_invite(party.server, room, item, *((season, episode) if series else (None, None)),
                                    host=party.me)
        return {"ok": True, "code": code, "file": target["name"], "room": room, "server": party.server}

    @staticmethod
    def _party_hello(party):
        for _ in range(40):
            if party.session and party.session.connected:
                time.sleep(1.0)
                party.osd("Watch party ready - send the invite. Press Space when you're both ready.", 8)
                return
            time.sleep(0.25)

    def party_join(self, code, path=""):
        """Guest: join the room, find what the host is playing in this computer's
        downloads, and play it in step."""
        self._need_mpv()
        invite = syncplay.read_invite(code)
        item = self._party_item(invite["source"], invite["source_id"], invite["title"], invite["type"])
        if not item:
            raise ApiError(f'add {invite["title"]} to your list first, then join again')
        if path and not self.playable(item, path):
            raise ApiError("that file isn't in this title's folder or your downloads")
        party = party_mod.Party(invite["server"], invite["room"], self._party_name(), "guest",
                                resolve=self.party_resolve, leader=invite.get("host"),
                                context=metadata.ssl_context(), on_end=self._party_ended)
        try:
            party.connect()
        except party_mod.PartyError as e:
            raise ApiError(str(e)) from e
        if invite.get("host") and invite["host"] not in party.people:
            party.leave("the host isn't there")
            raise ApiError(f'{invite["host"]} isn\'t in that watch party any more - ask for a new invite')
        entry = None
        if path:
            entries, _ = self._entries(item, path, None, invite.get("season"), invite.get("episode"), False)
            entry = dict(entries[0], item_id=item["id"])
        else:
            leading = party.leader_file()
            entry = self.party_resolve(leading) if leading else None
            if not entry and not leading:
                entry = self._party_entry(item, invite.get("season") or 1, invite.get("episode") or 1)
        if not entry:
            want = ((party.leader_file() or {}).get("watchlist") or {}).get("label") or invite["title"]
            party.leave("you don't have the episode")
            return {"ok": False, "need_file": True, "item_id": item["id"],
                    "message": f"You don't have {want} in your downloads."}
        self._start_party(party)
        self._party_follow(party, entry)
        return {"ok": True, "file": Path(entry["path"]).name, "invite": invite, "leader": party.leader_name()}

    def _party_follow(self, party, entry):
        """Open the guest's player on `entry` (paused until it's in step)."""
        item = self.store.get(entry["item_id"])
        session = self.player.play(self._session(self._need_mpv(), item, [entry], 0,
                                                 ["--pause", "--keep-open=yes"]))
        party.attach(session)

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
                self.torrents.wait_for_files(self.torrents.hash_of_magnet(magnet))
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

    def party_resolve(self, file_):
        """This computer's copy of what someone in the party is playing, or None.
        Watchlist players say which title and episode it is; for others (the
        Syncplay app) it's worked out from the file's name."""
        meta = (file_ or {}).get("watchlist") or {}
        if meta.get("src") or meta.get("title"):
            item = self._party_item(meta.get("src"), meta.get("id"), meta.get("title"), meta.get("type"))
            if not item:
                return None
            return self._party_entry(item, meta.get("season") or 1, meta.get("episode") or 1, meta.get("magnet") or "")
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
        if not best:
            return None
        return self._party_entry(best[1], season or 1, episodes[0] if episodes else 1)

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

    def connected(self, item, run=False):
        """Everything connected to a title (seasons, films, OVAs, ONAs, specials,
        sequels, prequels, spin-offs...), each marked as on your computer, in your
        list, waiting in your downloads, or missing. Looked for when you ask
        (`run`); otherwise what was found last time, or {"entries": None}."""
        cache_key = f'connected:{item["source"]}:{item["source_id"]}'
        with self._related_lock:
            cached = self._related.get(cache_key)
        if run:
            if not item.get("date") and item["source"] in ("tmdb", "mal"):
                try:                                    # its first air date, for the release order
                    item = self.refresh_metadata(item)
                except (metadata.MetadataError, KeyError):
                    pass
            entries, note = self._find_connected(item)
            guide = None
            s = self.settings.get()
            if ai.available(s):
                try:
                    guide = self._story_guide(item, entries, s)
                except ai.AIError as e:
                    note = (note + " " if note else "") + f"The story guide couldn't be written: {e}"
            cached = {"at": time.time(), "entries": entries, "note": note, "guide": guide}
            with self._related_lock:
                self._related[cache_key] = cached
                for k in sorted(self._related, key=lambda k: self._related[k]["at"])[:-300]:
                    del self._related[k]
                write_json(self._related_path, self._related, compact=True)
        if not cached:
            return {"entries": None, "ai": [ai.NAMES[p] for p in ai.available(self.settings.get())]}
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
                "guide": cached.get("guide"), "ai": [ai.NAMES[p] for p in ai.available(self.settings.get())]}

    @staticmethod
    def _story_guide(item, entries, s):
        """The AI's guide, keyed by "self" or "source:source_id"."""
        g = ai.story_guide(s, item, entries)
        keys = ["self"] + [f'{e["source"]}:{e["source_id"]}' for e in entries]
        return {"overview": g["overview"], "overview_spoilers": g["overview_spoilers"], "by": g["by"],
                "story_order": [keys[n] for n in g["story_order"] if n < len(keys)],
                "entries": {keys[n]: v for n, v in g["entries"].items() if n < len(keys)}}

    def _find_connected(self, item):
        s = self.settings.get()
        entries, notes = [], []

        def add(rows):
            have = {e["source_id"] for e in entries} | {item["source_id"]}
            entries.extend(r for r in rows if r["source_id"] not in have)

        try:
            if item["source"] == "mal" and s["mal_client_id"]:
                add([_entry(r, "anime") for r in metadata.MAL(s["mal_client_id"]).franchise(item["source_id"])])
            elif item["format"] == "movie" and item.get("tmdb_collection") and s["tmdb_api_key"]:
                add([_entry(r, "movie")
                     for r in metadata.TMDB(s["tmdb_api_key"]).collection(item["tmdb_collection"])])
        except metadata.MetadataError as e:
            notes.append(str(e))
        if ai.available(s):
            try:
                add(self._connected_by_ai(item, s))
            except ai.AIError as e:
                notes.append(f"AI helper: {e}")
        else:
            notes.append("With an AI key (Settings) this also finds connected titles that aren't linked directly.")
            if item["type"] != "anime" and s["tmdb_api_key"]:
                add(self._related_by_name(item, s))
        return entries, " ".join(notes)

    def _connected_by_ai(self, item, s):
        """What the AI names, each checked on MyAnimeList (anime) or TMDB first."""
        named = ai.connected_titles(s, item)

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
                        return _entry(dict(r, relation=row["relation"]), item_type) if r else None
            return None

        with ThreadPoolExecutor(4) as pool:
            return [r for r in pool.map(check, named) if r]

    @staticmethod
    def _search_safely(s, item_type, title):
        try:
            return metadata.search(s, item_type, title)[:10]
        except metadata.MetadataError:
            return []

    def _related_by_name(self, item, s):
        """Without Gemini: TV shows and films whose name contains this one's."""
        name = library.norm(item["title"])
        out = []
        if len(name) < 3:
            return out
        for media in ("tv", "movie"):
            for r in self._search_safely(s, media, item["title"]):
                other = library.norm(r["title"])
                if other != name and re.search(rf"(^|\s){re.escape(name)}(\s|$)", other):
                    out.append(_entry(dict(r, relation="Shares the name"), media))
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
                    subs = self.torrents.subtitles(*found)
                    subtitle = subs[0] if subs else None
            except torrents_mod.TorrentError as e:
                raise ApiError(str(e)) from e
        entries, patch = self._entries(item, path, subtitle, body.get("season"), body.get("episode"),
                                       s["queue_next_episodes"])
        start = 0 if body.get("from_start") else player.resume_point(item["positions"].get(entries[0]["key"]))
        wanted = body.get("player") or s["player"]
        note = ""
        if wanted == "mpv":
            mpv = player.find_mpv()
            if mpv:
                self.player.play(self._session(mpv, item, entries, start))
                self.store.update(item["id"], patch)
                return {"ok": True, "player": "mpv", "file": entries[0].get("name") or Path(path).name,
                        "queued": len(entries) - 1, "resumed_at": start, "stream": bool(found),
                        "subtitle": Path(entries[0]["sub"]).name if entries[0].get("sub") else ""}
            note = "The built-in player is missing, so VLC was used."
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
            extra_args=player.script_args(self.settings.get()["skip_intro"]) + list(extra),
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
                # The other side of a watch party can stream the same torrent.
                e["party"]["magnet"] = self.torrents.magnet(e["torrent"]["hash"])
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

    def online_options(self, item):
        """Your services for this kind of title, in order, each with whether it has it."""
        s = self.settings.get()
        names, message = self.streaming_on(item)
        options = []
        for x in s["services"]:
            if not x["on"] or item["type"] not in x["types"]:
                continue
            options.append({"id": x["id"], "name": x["name"], "kind": "check" if x["check"] else "search",
                            "has_it": services.has_it(x, names) if x["check"] else None})
        return {"region": s["region"], "streaming": names, "message": message, "options": options}

    def watch_online(self, item, service_id=None):
        """Open a service: the one asked for, or the first in your list that fits
        (a subscription service only when it has the title), else the fallback site."""
        s = self.settings.get()
        title = item["title"]
        info = self.online_options(item)
        result = {"region": s["region"], "services": info["streaming"], "message": info["message"],
                  "service": "", "url": "", "kind": ""}
        chosen = None
        by_id = {x["id"]: x for x in s["services"]}
        if service_id:
            chosen = by_id.get(service_id)
            if not chosen:
                raise ApiError("that service isn't in your list any more")
        else:
            for o in info["options"]:
                if o["kind"] == "search" or o["has_it"]:
                    chosen = by_id[o["id"]]
                    break
        if chosen:
            result.update(service=chosen["name"], url=services.fill(chosen, title),
                          kind="check" if chosen["check"] else "search", has_it=bool(chosen["check"]) and
                          services.has_it(chosen, info["streaming"]))
        else:
            url = launcher.fill_link(s["fallback_url"], title)
            result.update(service=services.host_name(url), url=url, kind="fallback")
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
        ("POST", r"/api/items/(\w+)/recap", "recap"),
        ("POST", r"/api/settings/ai", "test_ai"),
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
        ("GET", r"/api/items/(\w+)/mal-matches", "mal_matches"),
        ("POST", r"/api/items/(\w+)/use-mal", "use_mal"),
        ("POST", r"/api/find/mood", "find_mood"),
        ("POST", r"/api/import/mal", "import_mal"),
        ("GET", r"/api/import/status", "import_status"),
        ("POST", r"/api/party/start", "party_start"),
        ("POST", r"/api/party/join", "party_join"),
        ("POST", r"/api/party/peek", "party_peek"),
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
        csp = ("default-src 'self'; img-src 'self' https: data:; style-src 'self' 'unsafe-inline'; "
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
        for r in rows:
            mine = self.app.store.find_source(r["source"], r["source_id"])
            r["in_list"] = mine["id"] if mine else None
        return rows

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
        """"I'm in the mood for...": the AI asks up to two questions, then picks
        from your list and from the internet (each new one checked first)."""
        s = self.app.settings.get()
        if not ai.available(s):
            raise ApiError("this needs an AI helper - add a free key in Settings")
        conversation = [{"role": m.get("role"), "text": str(m.get("text") or "")[:1000]}
                        for m in body.get("messages") or [] if m.get("role") in ("you", "ai")][-12:]
        if not any(m["role"] == "you" and m["text"].strip() for m in conversation):
            raise ApiError("tell me what you're in the mood for first")
        asked = sum(1 for m in conversation if m["role"] == "ai")
        items = self.app.store.list()
        mine = [{"id": i["id"], "title": i["title"], "type": i["type"], "year": i.get("year"),
                 "genres": (i.get("genres") or [])[:4],
                 "status": "not started" if not i.get("last_played") else
                           f'up to S{i["progress"]["season"]} E{i["progress"]["episode"]}'} for i in items[:300]]
        try:
            reply = ai.mood(s, conversation, mine, may_ask=asked < 2)
        except ai.AIError as e:
            raise ApiError(str(e)) from e
        if reply.get("question"):
            return reply
        by_id = {i["id"]: i for i in items}
        reply["from_list"] = [dict(by_id[r["id"]], why=r.get("why") or "") for r in reply["from_list"] if r.get("id") in by_id]
        reply["new"] = self._mark_listed(discover.verify(s, reply["new"]))
        return reply

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
        item = self.app.item(str(body.get("item_id") or ""))
        return self.app.party_start(item, int(body.get("season") or 1), int(body.get("episode") or 1),
                                    str(body.get("path") or ""))

    def api_party_join(self, body):
        return self.app.party_join(str(body.get("code") or ""), str(body.get("path") or ""))

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
        """Save the key, then check it answers (saved even if it doesn't, with the reason shown)."""
        provider = body.get("provider")
        if provider not in ai.PROVIDERS:
            raise ApiError("unknown AI helper")
        key = str(body.get("key") or "").strip()
        self.app.settings.update({ai.KEY_SETTINGS[provider]: key})
        if not key:
            return {"ok": False, "error": "paste your key first", "tried": []}
        return ai.test_key(provider, key)

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
                                  body.get("collections", []))

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
            self.app.torrents.set_episode(h, int(index), body["episode"])
        return self.app.torrents.view(h)

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
        """Delete one episode's file: a torrent's is deleted (and not downloaded
        again unless you play it), your own goes to the Recycle Bin / Trash."""
        item = self.app.item(item_id)
        path = str(body.get("path") or "")
        found = self.app.torrents.find_file(path)
        if found and self.app.torrents.owns(item_id, path):
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
    app.torrents.port = server.server_address[1]     # for the stream links the player opens
    return server


def start_background_refresh(app: App):
    threading.Thread(target=app.refresh_stale, daemon=True).start()
    threading.Thread(target=library.watch, args=(app,), daemon=True).start()
