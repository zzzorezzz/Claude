"""Your list: saved titles and collections.

The list lives in one JSON file. With sync turned on, that file sits in a
cloud folder (Google Drive, OneDrive, Dropbox, iCloud Drive...) that both
computers sync, so it can change underneath us at any time. To cope:

- every title and collection carries a "modified" time, and deletions leave a
  tombstone, so two copies can be merged without losing either side's edits;
- before every read and write the file is checked for outside changes and
  merged in, and "conflicted copy" files made by cloud apps are merged too;
- things that only make sense on one computer (which folder holds a show's
  files) are kept in a separate local file that is never synced.
"""

import copy
import shutil
import threading
import time
import uuid
from pathlib import Path

from .config import read_json, write_json

TYPES = ("movie", "tv", "anime")
FORMATS = ("movie", "series")

# Fields filled from TMDB / MyAnimeList. Refreshing an entry replaces these.
METADATA_FIELDS = (
    "title", "original_title", "aliases", "year", "overview", "tagline", "poster",
    "backdrop", "genres", "rating", "rating_source", "votes", "popularity",
    "revenue", "mal_members", "mal_rank", "runtime", "cast", "studios", "seasons",
    "episodes_total", "airing_status", "trailer", "links", "tmdb_ref", "format",
    "media_type", "tmdb_collection", "date", "language",
)

# How often (seconds) to look for changes the other computer made.
CHECK_EVERY = 2.0

# Per-computer fields: kept in the local file, never synced.
LOCAL_DEFAULTS = {
    "local_folder": "",
    "local_file": "",
    # Set with "Not working": Watch now goes online until it's fixed.
    "local_broken": False,
}


def _blank_item() -> dict:
    now = time.time()
    return {
        "id": uuid.uuid4().hex[:12],
        "type": "movie", "format": "movie", "source": "", "source_id": "",
        "title": "", "original_title": "", "aliases": [], "year": None,
        "overview": "", "tagline": "", "poster": "", "backdrop": "", "genres": [],
        "rating": None, "rating_source": "", "votes": None, "popularity": 0,
        "revenue": None, "mal_members": None, "mal_rank": None, "runtime": None,
        "cast": [], "studios": [], "seasons": [], "episodes_total": None,
        "airing_status": "", "trailer": "", "links": {}, "tmdb_ref": "",
        # tv / ova / ona / special / tv_special / movie (from MyAnimeList or TMDB).
        "media_type": "", "tmdb_collection": None,
        # Things you control.
        "collections": [],
        "progress": {"season": 1, "episode": 1},
        # Where you stopped in each episode ("1:3") or film ("movie"): {"pos", "dur", "at"}.
        "positions": {},
        "added": now,
        "last_played": None,
        "watched_at": None,  # in your Watched list since then
        "updated": now,      # when the online info was last fetched
        "modified": now,     # when anything about it last changed (for sync)
    }


def new_item(item_type: str, metadata: dict) -> dict:
    if item_type not in TYPES:
        raise ValueError("type must be movie, tv or anime")
    item = _blank_item()
    item["type"] = item_type
    item["source"] = metadata.get("source", "")
    item["source_id"] = str(metadata.get("source_id", ""))
    for key in METADATA_FIELDS:
        if key in metadata and metadata[key] is not None:
            item[key] = metadata[key]
    if item["format"] not in FORMATS:
        item["format"] = "movie" if item_type == "movie" else "series"
    if not str(item["title"]).strip():
        raise ValueError("title can't be empty")
    return item


def next_episode(item: dict):
    """The (season, episode) after the current progress, or None when finished."""
    season = item["progress"]["season"]
    episode = item["progress"]["episode"]
    counts = {s["number"]: s["episodes"] for s in item.get("seasons", [])}
    count = counts.get(season, 0)
    if count and episode >= count:
        later = sorted(n for n in counts if n > season)
        return (later[0], 1) if later else None
    return season, episode + 1


def _read_quiet(path: Path):
    """Read a JSON file that another program (a cloud app) may be writing right
    now. Returns None if it can't be read yet; never moves or changes it."""
    import json
    for _ in range(5):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else None
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            time.sleep(0.2)
    return None


class Store:
    def __init__(self, shared_path: Path, local_path: Path):
        self._lock = threading.RLock()
        self.local_path = Path(local_path)
        local = read_json(self.local_path, {})
        self._local = {k: v for k, v in local.items() if isinstance(v, dict)} if isinstance(local, dict) else {}
        self._items, self._collections, self._deleted = {}, {}, {}
        self.version = 0
        self.path = Path(shared_path)
        self._seen = None
        self._checked_at = 0.0
        with self._lock:
            self._load()
            self._migrate()

    # ---- files

    def _files(self):
        """The list file plus any conflicted copies a cloud app left next to it."""
        files = [self.path]
        if self.path.parent.is_dir():
            files += sorted(p for p in self.path.parent.glob(f"{self.path.stem}*.json")
                            if p != self.path)
        return files

    def _signature(self):
        sig = []
        for p in self._files():
            try:
                st = p.stat()
                sig.append((str(p), st.st_mtime_ns, st.st_size, st.st_ino))
            except OSError:
                pass
        return tuple(sig)

    def _load(self):
        for p in self._files():
            data = _read_quiet(p)
            if data is None:
                if p == self.path and p.exists():
                    # Unreadable even after retrying: keep a copy before we write over it.
                    backup = self.local_path.with_name(f"unreadable-list-{int(time.time())}.json")
                    try:
                        shutil.copy2(p, backup)
                    except OSError:
                        pass
                continue
            self._merge(data)
        self._seen = self._signature()

    def _merge(self, data):
        for kind, store in (("items", self._items), ("collections", self._collections)):
            for row in data.get(kind, []):
                if not isinstance(row, dict) or not row.get("id"):
                    continue
                row.setdefault("modified", row.get("added") or row.get("created") or 0)
                mine = store.get(row["id"])
                if mine is None or row["modified"] > mine["modified"]:
                    store[row["id"]] = row
        for obj_id, when in (data.get("deleted") or {}).items():
            if isinstance(when, (int, float)) and when > self._deleted.get(obj_id, 0):
                self._deleted[obj_id] = when
        for obj_id, when in self._deleted.items():
            for store in (self._items, self._collections):
                if obj_id in store and store[obj_id]["modified"] <= when:
                    del store[obj_id]
        # Fill in fields newer versions of the app added.
        blank = _blank_item()
        for item in self._items.values():
            for key, value in blank.items():
                item.setdefault(key, copy.deepcopy(value))
        for col in self._collections.values():
            col.setdefault("type", None)

    def _migrate(self):
        """Older versions kept each show's folder inside the shared list."""
        moved = False
        for item_id, item in self._items.items():
            found = {k: item.pop(k) for k in LOCAL_DEFAULTS if k in item}
            if found:
                moved = True
                if any(found.values()) and item_id not in self._local:
                    self._local[item_id] = found
        if moved:
            self._save()
            self._save_local()

    def _check(self, force=False):
        """Merge in changes made elsewhere (another computer via the cloud).

        Looking at the file costs real time on a cloud drive (Google Drive's
        virtual drive in particular), so it's done at most every CHECK_EVERY
        seconds - except right before writing, when it always happens."""
        now = time.monotonic()
        if not force and now - self._checked_at < CHECK_EVERY:
            return
        self._checked_at = now
        if self._signature() != self._seen:
            self._load()
            self.version += 1

    def _save(self):
        self._check(force=True)  # never overwrite what the other computer just wrote
        write_json(self.path, {
            "version": 2,
            "items": list(self._items.values()),
            "collections": list(self._collections.values()),
            "deleted": self._deleted,
        }, compact=True)
        self._seen = self._signature()
        self.version += 1

    def _save_local(self):
        write_json(self.local_path, self._local, compact=True)

    def use_file(self, shared_path: Path):
        """Switch to another list file (turning sync on/off), merging both."""
        with self._lock:
            self.path = Path(shared_path)
            self._load()
            self._save()

    def current_version(self, force=False):
        with self._lock:
            self._check(force)
            return self.version

    # ---- items

    def _view(self, item):
        view = copy.deepcopy(item)
        view.update(LOCAL_DEFAULTS)
        view.update(self._local.get(item["id"], {}))
        return view

    def _find(self, item_id):
        if item_id not in self._items:
            raise KeyError(item_id)
        return self._items[item_id]

    @staticmethod
    def _after(obj):
        """Now, or just after the object's last change if the other computer's
        clock is ahead of ours, so this change always counts as the newest."""
        return max(time.time(), obj.get("modified", 0) + 0.001)

    def _touch(self, obj):
        obj["modified"] = self._after(obj)

    def list(self):
        with self._lock:
            self._check()
            return [self._view(i) for i in self._items.values()]

    def get(self, item_id):
        with self._lock:
            self._check()
            return self._view(self._find(item_id))

    def find_source(self, source, source_id):
        with self._lock:
            self._check()
            for item in self._items.values():
                if item["source"] == source and item["source_id"] == str(source_id):
                    return self._view(item)
        return None

    def add(self, item: dict):
        with self._lock:
            self._check()
            item = copy.deepcopy(item)
            local = {k: item.pop(k) for k in LOCAL_DEFAULTS if k in item}
            item["collections"] = [c for c in item.get("collections", []) if c in self._collections]
            self._touch(item)
            self._items[item["id"]] = item
            if any(local.values()):
                self._local[item["id"]] = local
                self._save_local()
            self._save()
            return self._view(item)

    def update(self, item_id, patch: dict):
        """Apply an already-validated patch."""
        with self._lock:
            self._check()
            item = self._find(item_id)
            local = {k: v for k, v in patch.items() if k in LOCAL_DEFAULTS}
            shared = {k: v for k, v in patch.items() if k not in LOCAL_DEFAULTS}
            if local:
                self._local.setdefault(item_id, {}).update(copy.deepcopy(local))
                self._save_local()
            if shared:
                item.update(copy.deepcopy(shared))
                self._touch(item)
                self._save()
            return self._view(item)

    def delete(self, item_id):
        with self._lock:
            self._check()
            self._deleted[item_id] = self._after(self._find(item_id))
            del self._items[item_id]
            if self._local.pop(item_id, None) is not None:
                self._save_local()
            self._save()

    # ---- collections and folders

    def collections(self):
        with self._lock:
            self._check()
            return copy.deepcopy(list(self._collections.values()))

    def add_collection(self, name: str, kind=None):
        """kind None: a collection any title can join. kind "tv"/"anime"/"movie":
        a folder inside that section, which holds each title at most once."""
        name = (name or "").strip()
        if not name:
            raise ValueError("give it a name")
        with self._lock:
            self._check()
            if any(c["name"].lower() == name.lower() and c.get("type") == kind
                   for c in self._collections.values()):
                raise ValueError(f'you already have one called "{name}"')
            col = {"id": uuid.uuid4().hex[:10], "name": name, "type": kind, "created": time.time()}
            self._touch(col)
            self._collections[col["id"]] = col
            self._save()
            return copy.deepcopy(col)

    def rename_collection(self, col_id, name: str):
        name = (name or "").strip()
        if not name:
            raise ValueError("give it a name")
        with self._lock:
            self._check()
            col = self._collections.get(col_id)
            if col is None:
                raise KeyError(col_id)
            col["name"] = name
            self._touch(col)
            self._save()
            return copy.deepcopy(col)

    def delete_collection(self, col_id):
        with self._lock:
            self._check()
            if col_id not in self._collections:
                raise KeyError(col_id)
            self._deleted[col_id] = self._after(self._collections.pop(col_id))
            for item in self._items.values():
                if col_id in item["collections"]:
                    item["collections"] = [c for c in item["collections"] if c != col_id]
                    self._touch(item)
            self._save()

    def move_to_folder(self, item_id, folder_id):
        """Put a title in one of its section's folders ("" = no folder)."""
        with self._lock:
            self._check()
            item = self._find(item_id)
            folders = {c["id"] for c in self._collections.values() if c.get("type") == item["type"]}
            if folder_id and folder_id not in folders:
                raise KeyError(folder_id)
            item["collections"] = [c for c in item["collections"] if c not in folders]
            if folder_id:
                item["collections"].append(folder_id)
            self._touch(item)
            self._save()
            return self._view(item)

    def set_item_collections(self, item_id, col_ids):
        with self._lock:
            self._check()
            item = self._find(item_id)
            item["collections"] = [c for c in dict.fromkeys(col_ids) if c in self._collections]
            self._touch(item)
            self._save()
            return self._view(item)
