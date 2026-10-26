"""Your list: saved titles and collections, kept in one JSON file."""

import copy
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
)


def _blank_item() -> dict:
    return {
        "id": uuid.uuid4().hex[:12],
        "type": "movie", "format": "movie", "source": "", "source_id": "",
        "title": "", "original_title": "", "aliases": [], "year": None,
        "overview": "", "tagline": "", "poster": "", "backdrop": "", "genres": [],
        "rating": None, "rating_source": "", "votes": None, "popularity": 0,
        "revenue": None, "mal_members": None, "mal_rank": None, "runtime": None,
        "cast": [], "studios": [], "seasons": [], "episodes_total": None,
        "airing_status": "", "trailer": "", "links": {}, "tmdb_ref": "",
        # Things you control.
        "collections": [],
        "progress": {"season": 1, "episode": 1},
        "local_folder": "",
        "local_file": "",
        # Set with "Not working": Watch now goes online until it's fixed.
        "local_broken": False,
        "added": time.time(),
        "last_played": None,
        "updated": time.time(),
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


class Store:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        data = read_json(path, {})
        if not isinstance(data, dict):
            data = {}
        self._items = [i for i in data.get("items", []) if isinstance(i, dict) and i.get("id")]
        self._collections = [c for c in data.get("collections", [])
                             if isinstance(c, dict) and c.get("id")]
        # Older files may be missing newer fields.
        for item in self._items:
            for key, value in _blank_item().items():
                item.setdefault(key, value)
        for col in self._collections:
            col.setdefault("type", None)

    def _save(self):
        write_json(self.path, {"version": 1, "items": self._items,
                               "collections": self._collections})

    def _find(self, item_id):
        for item in self._items:
            if item["id"] == item_id:
                return item
        raise KeyError(item_id)

    # Items

    def list(self):
        with self._lock:
            return copy.deepcopy(self._items)

    def get(self, item_id):
        with self._lock:
            return copy.deepcopy(self._find(item_id))

    def find_source(self, source, source_id):
        with self._lock:
            for item in self._items:
                if item["source"] == source and item["source_id"] == str(source_id):
                    return copy.deepcopy(item)
        return None

    def add(self, item: dict):
        with self._lock:
            known = {c["id"] for c in self._collections}
            item["collections"] = [c for c in item.get("collections", []) if c in known]
            self._items.append(item)
            self._save()
            return copy.deepcopy(item)

    def update(self, item_id, patch: dict):
        """Apply an already-validated patch."""
        with self._lock:
            item = self._find(item_id)
            item.update(copy.deepcopy(patch))
            self._save()
            return copy.deepcopy(item)

    def delete(self, item_id):
        with self._lock:
            self._items.remove(self._find(item_id))
            self._save()

    # Collections

    def collections(self):
        with self._lock:
            return copy.deepcopy(self._collections)

    def add_collection(self, name: str, kind=None):
        """kind None: a collection any title can join. kind "tv"/"anime"/"movie":
        a folder inside that section, which holds each title at most once."""
        name = (name or "").strip()
        if not name:
            raise ValueError("give it a name")
        with self._lock:
            if any(c["name"].lower() == name.lower() and c.get("type") == kind
                   for c in self._collections):
                raise ValueError(f'you already have one called "{name}"')
            col = {"id": uuid.uuid4().hex[:10], "name": name, "type": kind, "created": time.time()}
            self._collections.append(col)
            self._save()
            return copy.deepcopy(col)

    def rename_collection(self, col_id, name: str):
        name = (name or "").strip()
        if not name:
            raise ValueError("give the collection a name")
        with self._lock:
            for col in self._collections:
                if col["id"] == col_id:
                    col["name"] = name
                    self._save()
                    return copy.deepcopy(col)
        raise KeyError(col_id)

    def delete_collection(self, col_id):
        with self._lock:
            before = len(self._collections)
            self._collections = [c for c in self._collections if c["id"] != col_id]
            if len(self._collections) == before:
                raise KeyError(col_id)
            for item in self._items:
                item["collections"] = [c for c in item["collections"] if c != col_id]
            self._save()

    def move_to_folder(self, item_id, folder_id):
        """Put a title in one of its section's folders ("" = no folder)."""
        with self._lock:
            item = self._find(item_id)
            folders = {c["id"] for c in self._collections if c.get("type") == item["type"]}
            if folder_id and folder_id not in folders:
                raise KeyError(folder_id)
            item["collections"] = [c for c in item["collections"] if c not in folders]
            if folder_id:
                item["collections"].append(folder_id)
            self._save()
            return copy.deepcopy(item)

    def set_item_collections(self, item_id, col_ids):
        with self._lock:
            known = {c["id"] for c in self._collections}
            item = self._find(item_id)
            item["collections"] = [c for c in dict.fromkeys(col_ids) if c in known]
            self._save()
            return copy.deepcopy(item)
