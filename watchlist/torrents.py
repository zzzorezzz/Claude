"""Streaming a show's episodes from a torrent (a magnet link you add to it).

- Adding a magnet only fetches the torrent's list of files (from the people
  sharing it); nothing is downloaded yet. Each video file is matched to an
  episode the same way your downloads are, and you can correct it.
- Playing an episode downloads just that file (and its subtitle, if the
  torrent has one), in order, from the part you're watching. The built-in
  player streams it while it downloads, through the app's own local server,
  so it starts within seconds and skipping ahead fetches that part first.
  While you watch, the next episode downloads too.
- An episode you watched to the end credits is deleted a few minutes later,
  unless "Keep downloaded episodes" is ticked for that show; one you stopped
  part way through is kept. Everything can also be done by hand in the
  Torrents window: pause, resume, choose files, delete files or a torrent.

Files go in "Watchlist torrents" inside your downloads folder (which the
download sorting skips), or in the app's data folder without one.
"""

import os
import secrets
import shutil
import threading
import time
import urllib.parse
import warnings
from pathlib import Path

from . import scanner
from .config import read_json, write_json

# libtorrent 2.1 renamed a few things; the old names still work everywhere.
warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"watchlist\.torrents")

try:
    import libtorrent as lt
except ImportError:  # pragma: no cover - the app still works without torrents
    lt = None

DIR_NAME = "Watchlist torrents"
DELETE_AFTER = 5 * 60         # an episode watched to the end is deleted this long after
READ_AHEAD = 32 * 1024 * 1024  # bytes asked for urgently ahead of what's being played
WAIT_FOR_DATA = 90            # seconds a stream waits for a piece before giving up
METADATA_WAIT = 60            # seconds to wait for a magnet's list of files
PRIORITIES = {0: "Skip", 1: "Normal", 4: "High", 7: "Now"}


class TorrentError(Exception):
    pass


def available():
    return lt is not None


def _hash_of(obj):
    """The info-hash (as hex) of a torrent handle or add_torrent_params."""
    try:
        hashes = obj.info_hashes() if callable(getattr(obj, "info_hashes", None)) else obj.info_hashes
        return str(hashes.get_best())
    except AttributeError:
        return str(obj.info_hash() if callable(getattr(obj, "info_hash", None)) else obj.info_hash)


def _is_pad(fs, i):
    return bool(fs.file_flags(i) & lt.file_storage.flag_pad_file)


def kind_of(path):
    ext = Path(path).suffix.lower()
    return "video" if ext in scanner.VIDEO_EXTS else "subtitle" if ext in scanner.SUB_EXTS else "other"


class Torrents:
    SETTINGS = {"user_agent": "Watchlist", "listen_interfaces": "0.0.0.0:6881,[::]:6881",
                "enable_dht": True, "enable_lsd": True, "enable_upnp": True, "enable_natpmp": True}

    def __init__(self, app, data_dir):
        self.app = app
        self.dir = Path(data_dir) / "torrents"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.dir / "torrents.json"
        state = read_json(self.state_path, {})
        self.records = state.get("torrents", {}) if isinstance(state, dict) else {}
        self.keep = state.get("keep", {}) if isinstance(state, dict) else {}
        self.lock = threading.RLock()
        self.handles = {}
        self.ses = None
        self.error = "" if lt else "Torrents need libtorrent, which is missing from this copy of the app."
        self.token = secrets.token_hex(8)
        self.port = 0                       # the app's local server, for stream links
        self.reading = {}                   # (hash, index) -> when a stream last read it
        self._pieces = {}                   # (hash, piece) -> bytes, from read_piece
        self._piece_cond = threading.Condition()
        self._dirty = False
        self._stop = False
        if lt and self.records:
            self._start()

    # ---- the libtorrent session

    def _start(self):
        if self.ses or not lt:
            return
        self.ses = lt.session(dict(self.SETTINGS, alert_mask=lt.alert_category.error | lt.alert_category.status
                                   | lt.alert_category.storage))
        for h, rec in list(self.records.items()):
            try:
                self._add_handle(h, rec)
            except Exception as e:  # noqa: BLE001 - one broken torrent mustn't stop the rest
                rec["error"] = str(e)
        threading.Thread(target=self._loop, daemon=True).start()

    def _add_handle(self, h, rec):
        meta = self.dir / f"{h}.torrent"
        if meta.exists():
            p = lt.add_torrent_params()
            p.ti = lt.torrent_info(str(meta))
            prio = self._priorities(rec, p.ti.num_files())
            p.file_priorities = prio
        else:
            p = lt.parse_magnet_uri(rec["magnet"])
            p.flags |= lt.torrent_flags.upload_mode       # only the list of files, for now
        p.save_path = rec["save_path"]
        p.flags &= ~lt.torrent_flags.auto_managed
        if rec.get("paused"):
            p.flags |= lt.torrent_flags.paused
        else:
            p.flags &= ~lt.torrent_flags.paused
        handle = self.ses.add_torrent(p)
        self.handles[h] = handle
        if meta.exists():
            handle.set_flags(lt.torrent_flags.sequential_download)
        return handle

    @staticmethod
    def _priorities(rec, count):
        prio = [0] * count
        for k, v in (rec.get("priorities") or {}).items():
            if int(k) < count:
                prio[int(k)] = int(v)
        return prio

    def _save_soon(self):
        self._dirty = True

    def _save(self):
        with self.lock:
            write_json(self.state_path, {"torrents": self.records, "keep": self.keep})
            self._dirty = False

    # ---- the background loop: alerts, newly found file lists, cleaning up

    def _loop(self):
        last_tick = 0.0
        while not self._stop:
            try:
                self.ses.wait_for_alert(250)
                for a in self.ses.pop_alerts():
                    self._alert(a)
                if time.time() - last_tick >= 1:
                    last_tick = time.time()
                    self._tick()
            except Exception as e:  # noqa: BLE001 - keep the loop alive
                self.error = f"torrents: {e}"
                time.sleep(1)

    def _alert(self, a):
        if isinstance(a, lt.read_piece_alert):
            with self._piece_cond:
                self._pieces[(_hash_of(a.handle), a.piece)] = bytes(a.buffer) if a.buffer else b""
                if len(self._pieces) > 48:
                    for k in list(self._pieces)[:16]:
                        self._pieces.pop(k, None)
                self._piece_cond.notify_all()
        elif isinstance(a, (lt.torrent_error_alert, lt.file_error_alert)):
            rec = self.records.get(_hash_of(a.handle))
            if rec:
                rec["error"] = a.message()

    def _tick(self):
        with self.lock:
            for h, handle in list(self.handles.items()):
                rec = self.records.get(h)
                if not rec:
                    continue
                if not rec.get("files") and handle.status().has_metadata:
                    self._got_metadata(h, handle, rec)
            self._prefetch_next()
            self._delete_watched()
            if self._dirty:
                self._save()

    def _got_metadata(self, h, handle, rec):
        ti = handle.torrent_file()
        fs = ti.files()
        try:
            data = lt.bencode({"info": lt.bdecode(ti.metadata())})
            (self.dir / f"{h}.torrent").write_bytes(data)
        except Exception:  # noqa: BLE001 - it can still be fetched again next time
            pass
        rec["name"] = ti.name()
        rec["files"] = [{"index": i, "path": fs.file_path(i), "size": fs.file_size(i)}
                        for i in range(fs.num_files()) if not _is_pad(fs, i)]
        handle.prioritize_files(self._priorities(rec, fs.num_files()))
        handle.unset_flags(lt.torrent_flags.upload_mode)
        handle.set_flags(lt.torrent_flags.sequential_download)
        rec.pop("error", None)
        self._save_soon()

    # ---- adding and removing

    def _save_dir(self):
        folder = self.app.settings.get()["downloads_folder"]
        base = Path(folder) / DIR_NAME if folder and os.path.isdir(folder) else self.dir / "files"
        base.mkdir(parents=True, exist_ok=True)
        return str(base)

    def add(self, item_id, magnet):
        if not lt:
            raise TorrentError(self.error)
        magnet = (magnet or "").strip()
        if not magnet.startswith("magnet:?"):
            raise TorrentError("paste a magnet link (it starts with magnet:?)")
        try:
            p = lt.parse_magnet_uri(magnet)
        except Exception as e:  # noqa: BLE001 - libtorrent raises its own errors
            raise TorrentError("that magnet link can't be read") from e
        h = _hash_of(p)
        with self.lock:
            self._start()
            if h in self.records:
                rec = self.records[h]
                if rec["item_id"] != item_id:
                    raise TorrentError("that torrent is already added to another title")
                return self.view(h)
            rec = {"hash": h, "item_id": item_id, "magnet": magnet, "name": p.name or "", "save_path": self._save_dir(),
                   "added": time.time(), "paused": False, "priorities": {}, "overrides": {}, "finished": {},
                   "files": []}
            self.records[h] = rec
            self._add_handle(h, rec)
            self._save()
        return self.view(h)

    def remove(self, h, delete_files=True):
        with self.lock:
            rec = self.records.pop(h, None)
            handle = self.handles.pop(h, None)
            if handle is not None:
                self.ses.remove_torrent(handle, lt.options_t.delete_files if delete_files else 0)
            (self.dir / f"{h}.torrent").unlink(missing_ok=True)
            self._save()
        if rec and delete_files:
            # What libtorrent leaves behind (its .parts file, empty folders).
            threading.Timer(2, self._tidy, args=(rec,)).start()
        return {"ok": bool(rec)}

    @staticmethod
    def _tidy(rec):
        root = Path(rec["save_path"])
        for f in rec.get("files") or []:
            top = root / Path(f["path"]).parts[0]
            if top.is_dir() and not any(p.is_file() for p in top.rglob("*")):
                shutil.rmtree(top, ignore_errors=True)
        for parts in root.glob(f".{rec['hash']}*.parts"):
            parts.unlink(missing_ok=True)

    def forget_item(self, item_id):
        for h in [h for h, r in self.records.items() if r["item_id"] == item_id]:
            self.remove(h, delete_files=True)
        self.keep.pop(item_id, None)
        self._save_soon()

    # ---- what's in a torrent

    def _rec(self, h):
        rec = self.records.get(h)
        if not rec:
            raise TorrentError("that torrent isn't added any more")
        return rec

    def _file(self, rec, index):
        f = next((f for f in rec.get("files") or [] if f["index"] == index), None)
        if not f:
            raise TorrentError("that file isn't in the torrent")
        return f

    def path_of(self, rec, f):
        return os.path.join(rec["save_path"], f["path"])

    def _progress(self, h):
        handle = self.handles.get(h)
        try:
            return handle.file_progress() if handle is not None else []
        except Exception:  # noqa: BLE001 - a torrent being re-added
            return []

    def complete(self, rec, f, progress=None):
        progress = self._progress(rec["hash"]) if progress is None else progress
        done = progress[f["index"]] if f["index"] < len(progress) else 0
        return f["size"] > 0 and done >= f["size"] and os.path.isfile(self.path_of(rec, f))

    def videos(self, item, rec=None):
        """The torrent's video files as videos the rest of the app understands:
        finished ones by their real path, the rest by a stream link."""
        out = []
        recs = [rec] if rec else [r for r in self.records.values() if r["item_id"] == item["id"]]
        for rec in recs:
            progress = self._progress(rec["hash"])
            for f in rec.get("files") or []:
                if kind_of(f["path"]) != "video":
                    continue
                override = (rec.get("overrides") or {}).get(str(f["index"]))
                if override == "none":
                    continue
                v = self._describe(f["path"], f["size"])
                if override and ":" in override:
                    s, e = (int(x) for x in override.split(":"))
                    v.update(season=s, episodes=[e], kind="episode" if s else "special")
                done = progress[f["index"]] if f["index"] < len(progress) else 0
                whole = self.complete(rec, f, progress)
                v.update(path=self.path_of(rec, f) if whole else self.stream_url(rec["hash"], f["index"], f["path"]),
                         torrent={"hash": rec["hash"], "index": f["index"], "complete": whole,
                                  "progress": round(done / f["size"], 3) if f["size"] else 0},
                         stream=not whole, dl=False)
                out.append(v)
        return out

    _described = {}

    @classmethod
    def _describe(cls, rel, size):
        """What a file in a torrent is (worked out once: packs can hold hundreds)."""
        from .library import Library
        key = (rel, size)
        if key not in cls._described:
            season, eps = scanner.parse_episode(Path(rel).parts)
            v = {"rel": rel, "name": Path(rel).name, "size": size, "season": season, "episodes": list(eps), "year": None}
            cls._described[key] = Library.describe(v)
        return dict(cls._described[key], episodes=list(cls._described[key]["episodes"]))

    def episodes(self, item):
        """{"s:e": {"hash", "index", "complete", "progress", "name"}} for the
        episodes a show's torrents have."""
        found = scanner.episode_map(item, self.videos(item)) if item.get("format") == "series" else {}
        return {f"{s}:{e}": dict(v["torrent"], name=v["name"]) for (s, e), v in found.items()}

    def stream_url(self, h, index, path):
        return (f"http://127.0.0.1:{self.port}/stream/{self.token}/{h}/{index}/"
                f"{urllib.parse.quote(Path(path).name)}")

    def parse_url(self, path):
        """(hash, index) for one of this app's stream links, else None."""
        prefix = f"http://127.0.0.1:{self.port}/stream/{self.token}/"
        if not isinstance(path, str) or not path.startswith(prefix):
            return None
        parts = path[len(prefix):].split("/")
        if len(parts) < 2 or parts[0] not in self.records or not parts[1].isdigit():
            return None
        return parts[0], int(parts[1])

    def owns(self, item_id, path):
        """A stream link or downloaded file of one of this title's torrents."""
        found = self.parse_url(path)
        if found:
            return self.records[found[0]]["item_id"] == item_id
        for rec in self.records.values():
            if rec["item_id"] == item_id and isinstance(path, str) and path.startswith(rec["save_path"]):
                real = os.path.realpath(path)
                return any(os.path.realpath(self.path_of(rec, f)) == real for f in rec.get("files") or [])
        return False

    def find_file(self, path):
        """(hash, index) of a torrent's downloaded file, by its path."""
        found = self.parse_url(path)
        if found:
            return found
        real = os.path.realpath(path) if isinstance(path, str) else ""
        for h, rec in self.records.items():
            for f in rec.get("files") or []:
                if os.path.realpath(self.path_of(rec, f)) == real:
                    return h, f["index"]
        return None

    def magnet(self, h):
        rec = self.records.get(h)
        return rec["magnet"] if rec else ""

    def ready_subtitles(self, h, index):
        """The torrent's subtitles for a video that are downloaded already."""
        rec = self.records.get(h)
        if not rec:
            return []
        return [self.path_of(rec, f) for f in self._subtitle_files(rec, index) if self.complete(rec, f)]

    def _subtitle_files(self, rec, index):
        try:
            video = self._file(rec, index)
        except TorrentError:
            return []
        stem = scanner._clean(Path(video["path"]).stem).lower()
        _, eps = scanner.parse_episode(Path(video["path"]).parts)
        out = []
        for f in rec.get("files") or []:
            if kind_of(f["path"]) != "subtitle":
                continue
            s_stem = scanner._clean(Path(f["path"]).stem).lower()
            _, s_eps = scanner.parse_episode(Path(f["path"]).parts)
            if s_stem.startswith(stem) or (eps and s_eps and set(eps) == set(s_eps)):
                out.append(f)
        return out

    # ---- playing

    def prepare(self, h, index):
        """Get a file ready to stream: wanted now, downloaded in order, and not
        on its way to being deleted."""
        with self.lock:
            rec = self._rec(h)
            handle = self.handles.get(h)
            if handle is None:
                raise TorrentError("that torrent isn't loaded")
            self._wait_for_files(h, rec)
            f = self._file(rec, index)
            rec["priorities"][str(index)] = 7
            rec["finished"].pop(str(index), None)
            for other, prio in list(rec["priorities"].items()):
                if prio == 7 and other != str(index) and kind_of(self._file(rec, int(other))["path"]) == "video":
                    rec["priorities"][other] = 4                # the one before keeps coming, after this
                    handle.file_priority(int(other), 4)
            if rec.get("paused"):
                rec["paused"] = False
                handle.resume()
            handle.file_priority(index, 7)
            handle.set_flags(lt.torrent_flags.sequential_download)
            self._save_soon()
            return rec, f

    def _wait_for_files(self, h, rec):
        deadline = time.time() + METADATA_WAIT
        while not rec.get("files"):
            handle = self.handles.get(h)
            if handle is not None and handle.status().has_metadata:
                self._got_metadata(h, handle, rec)
                break
            if time.time() > deadline:
                raise TorrentError("nobody sharing this torrent could be reached to get its list of files")
            self.lock.release()
            try:
                time.sleep(0.5)
            finally:
                self.lock.acquire()

    def hash_of_magnet(self, magnet):
        try:
            return _hash_of(lt.parse_magnet_uri(magnet))
        except Exception as e:  # noqa: BLE001
            raise TorrentError("that magnet link can't be read") from e

    def wait_for_files(self, h):
        """Until a torrent's list of files is known (a minute at most)."""
        with self.lock:
            self._wait_for_files(h, self._rec(h))

    def subtitles(self, h, index, wait=20):
        """The torrent's subtitle files for a video, downloaded first (they're small)."""
        rec = self._rec(h)
        subs = self._subtitle_files(rec, index)
        if not subs:
            return []
        handle = self.handles[h]
        with self.lock:
            for f in subs:
                rec["priorities"][str(f["index"])] = 7
                handle.file_priority(f["index"], 7)
            self._save_soon()
        deadline = time.time() + wait
        while time.time() < deadline and not all(self.complete(rec, f) for f in subs):
            time.sleep(0.3)
        return [self.path_of(rec, f) for f in subs if self.complete(rec, f)]

    def read(self, h, index, start, end):
        """The bytes start..end (inclusive) of a file, as they arrive."""
        rec = self._rec(h)
        f = self._file(rec, index)
        handle = self.handles[h]
        ti = handle.torrent_file()
        # (The handle is looked up again as it goes: deleting another file puts the torrent back.)
        fs = ti.files()
        offset, plen = fs.file_offset(index), ti.piece_length()
        last_piece = (offset + f["size"] - 1) // plen
        key = (h, index)
        handle.clear_piece_deadlines()
        pos = start
        while pos <= end:
            self.reading[key] = time.time()
            handle = self.handles.get(h) or handle
            piece = (offset + pos) // plen
            ahead = max(4, READ_AHEAD // plen)
            for n, p in enumerate(range(piece, min(piece + ahead, last_piece) + 1)):
                if not handle.have_piece(p):
                    handle.set_piece_deadline(p, 300 + 150 * n)
            if piece != last_piece and not handle.have_piece(last_piece):
                handle.set_piece_deadline(last_piece, 600)      # players look at the end first
            data = self._piece(h, handle, piece)
            begin = offset + pos - piece * plen
            chunk = data[begin:begin + (end - pos + 1)]
            if not chunk:
                return
            yield chunk
            pos += len(chunk)

    def _piece(self, h, handle, piece):
        deadline = time.time() + WAIT_FOR_DATA
        while not handle.have_piece(piece):
            if time.time() > deadline:
                raise TorrentError("the torrent stopped sending data")
            time.sleep(0.05)
        with self._piece_cond:
            self._pieces.pop((h, piece), None)
            handle.read_piece(piece)
            while (h, piece) not in self._pieces:
                if not self._piece_cond.wait(timeout=15):
                    raise TorrentError("couldn't read the downloaded data")
            return self._pieces[(h, piece)]

    def size(self, h, index):
        return self._file(self._rec(h), index)["size"]

    def finished_watching(self, h, index):
        """Watched to the end credits: deleted in a few minutes (unless kept)."""
        with self.lock:
            rec = self.records.get(h)
            if rec:
                rec["finished"][str(index)] = time.time()
                self._save_soon()

    def _prefetch_next(self):
        """While an episode streams, the next one downloads too (quietly)."""
        now = time.time()
        for (h, index), at in list(self.reading.items()):
            if now - at > 30:
                continue
            rec = self.records.get(h)
            try:
                item = self.app.store.get(rec["item_id"]) if rec else None
            except KeyError:
                item = None
            if not item:
                continue
            eps = self.episodes(item)
            here = next((k for k, v in eps.items() if v["hash"] == h and v["index"] == index), None)
            if not here:
                continue
            s, e = (int(x) for x in here.split(":"))
            nxt = eps.get(f"{s}:{e + 1}") or eps.get(f"{s + 1}:1")
            if nxt and not nxt["complete"]:
                other = self.records.get(nxt["hash"])
                handle = self.handles.get(nxt["hash"])
                if other is not None and handle is not None and int(other["priorities"].get(str(nxt["index"]), 0)) < 4:
                    other["priorities"][str(nxt["index"])] = 4
                    handle.file_priority(nxt["index"], 4)
                    self._save_soon()

    def _delete_watched(self):
        now = time.time()
        playing = self._playing_paths()
        for h, rec in list(self.records.items()):
            if self.keep.get(rec["item_id"]):
                continue
            # Deleting puts the torrent back for a moment: not while any of its
            # episodes is streaming (it happens once you stop).
            if any(now - at < 120 for (rh, _), at in self.reading.items() if rh == h):
                continue
            for index, at in list((rec.get("finished") or {}).items()):
                if now - at < DELETE_AFTER:
                    continue
                try:
                    f = self._file(rec, int(index))
                except TorrentError:
                    rec["finished"].pop(index, None)
                    continue
                if self.path_of(rec, f) in playing or self.stream_url(h, f["index"], f["path"]) in playing:
                    continue
                self.delete_file(h, int(index))

    def _playing_paths(self):
        session = self.app.player.session
        if not session or session.ended:
            return set()
        return {e["path"] for e in session.entries}

    # ---- doing things by hand

    def set_keep(self, item_id, keep):
        with self.lock:
            self.keep[item_id] = bool(keep)
            self._save()

    def set_priority(self, h, index, priority):
        if priority not in PRIORITIES:
            raise TorrentError("unknown priority")
        with self.lock:
            rec = self._rec(h)
            self._file(rec, index)
            rec["priorities"][str(index)] = priority
            handle = self.handles.get(h)
            if handle is not None and rec.get("files"):
                handle.file_priority(index, priority)
            self._save_soon()

    def set_episode(self, h, index, key):
        """Which episode a file is ("s:e"), not an episode ("none"), or automatic (None)."""
        with self.lock:
            rec = self._rec(h)
            self._file(rec, index)
            if key in (None, ""):
                rec["overrides"].pop(str(index), None)
            elif key == "none" or (":" in key and all(x.isdigit() for x in key.split(":"))):
                rec["overrides"][str(index)] = key
            else:
                raise TorrentError("episode must look like 1:5")
            self._save_soon()

    def pause(self, h, paused=True):
        with self.lock:
            rec = self._rec(h)
            rec["paused"] = bool(paused)
            handle = self.handles.get(h)
            if handle is not None:
                handle.pause() if paused else handle.resume()
            self._save_soon()

    def delete_file(self, h, index):
        """Delete one downloaded file and stop downloading it. (libtorrent keeps
        its files open, so the torrent is taken out and put back around it.)"""
        with self.lock:
            rec = self._rec(h)
            f = self._file(rec, index)
            rec["priorities"][str(index)] = 0
            rec["finished"].pop(str(index), None)
            handle = self.handles.pop(h, None)
            if handle is not None:
                self.ses.remove_torrent(handle)
                time.sleep(0.4)
            path = Path(self.path_of(rec, f))
            for _ in range(10):
                try:
                    path.unlink(missing_ok=True)
                    break
                except PermissionError:      # Windows: still closing
                    time.sleep(0.3)
            self._add_handle(h, rec)
            self._save()
        return {"ok": True}

    # ---- for the Torrents window

    def view(self, h):
        rec = self._rec(h)
        handle = self.handles.get(h)
        st = handle.status() if handle is not None else None
        progress = self._progress(h)
        item = None
        try:
            item = self.app.store.get(rec["item_id"])
        except KeyError:
            pass
        eps = {}
        if item and item.get("format") == "series":
            for key, v in self.episodes(item).items():
                if v["hash"] == h:
                    eps[v["index"]] = key
        files = []
        for f in rec.get("files") or []:
            done = progress[f["index"]] if f["index"] < len(progress) else 0
            files.append({"index": f["index"], "path": f["path"], "size": f["size"], "done": done,
                          "kind": kind_of(f["path"]), "priority": int(rec["priorities"].get(str(f["index"]), 0)),
                          "episode": eps.get(f["index"]), "override": rec["overrides"].get(str(f["index"])),
                          "complete": self.complete(rec, f, progress),
                          "finished_at": rec["finished"].get(str(f["index"]))})
        state = "paused" if rec.get("paused") else "loading" if not rec.get("files") else (
            str(st.state).split(".")[-1] if st else "stopped")
        return {"hash": h, "item_id": rec["item_id"], "title": item["title"] if item else "", "name": rec.get("name") or "",
                "magnet": rec["magnet"], "save_path": rec["save_path"], "added": rec["added"], "state": state,
                "paused": bool(rec.get("paused")), "error": rec.get("error", ""),
                "down": st.download_rate if st else 0, "up": st.upload_rate if st else 0,
                "peers": st.num_peers if st else 0, "seeds": st.num_seeds if st else 0,
                "wanted": st.total_wanted if st else 0, "wanted_done": st.total_wanted_done if st else 0,
                "keep": bool(self.keep.get(rec["item_id"])), "files": files}

    def all(self, item_id=None):
        with self.lock:
            hashes = [h for h, r in self.records.items() if item_id in (None, r["item_id"])]
            return {"available": bool(lt), "error": self.error, "keep_after": DELETE_AFTER,
                    "torrents": [self.view(h) for h in hashes]}

    def shutdown(self):
        self._stop = True
        if self.ses:
            self._save()
