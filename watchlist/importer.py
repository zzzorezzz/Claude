"""Bring in your MyAnimeList list: from your user name (if the list is public)
or from the file MyAnimeList exports (myanimelist.net/panel.php?go=export)."""

import gzip
import threading
import time
import xml.etree.ElementTree as ET

from . import metadata
from .store import new_item

PAUSE = 0.5   # between titles, to go easy on MyAnimeList
STATUSES = ("watching", "plan_to_watch", "on_hold", "completed", "dropped")
_XML_STATUS = {"watching": "watching", "1": "watching", "completed": "completed", "2": "completed",
               "on-hold": "on_hold", "3": "on_hold", "dropped": "dropped", "4": "dropped",
               "plan to watch": "plan_to_watch", "6": "plan_to_watch"}


def read_export(path):
    """The anime in a MyAnimeList export (.xml or .xml.gz)."""
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        raise ValueError("that isn't a MyAnimeList export file") from e
    out = []
    for a in root.iter("anime"):
        mal_id = (a.findtext("series_animedb_id") or "").strip()
        if mal_id.isdigit():
            watched = (a.findtext("my_watched_episodes") or "0").strip()
            out.append({"mal_id": mal_id, "title": (a.findtext("series_title") or "").strip(),
                        "status": _XML_STATUS.get((a.findtext("my_status") or "").strip().lower(), ""),
                        "watched": int(watched) if watched.isdigit() else 0})
    if not out and root.tag != "myanimelist":
        raise ValueError("that isn't a MyAnimeList export file")
    return out


class Importer:
    def __init__(self, app):
        self.app = app
        self._lock = threading.Lock()
        self.status = {"running": False, "done": 0, "total": 0, "added": 0, "already": 0,
                       "failed": [], "current": "", "error": ""}

    def start(self, entries, statuses):
        wanted = [e for e in entries if e["status"] in statuses]
        with self._lock:
            if self.status["running"]:
                raise ValueError("an import is already running")
            self.status = {"running": True, "done": 0, "total": len(wanted), "added": 0, "already": 0,
                           "failed": [], "current": "", "error": ""}
        threading.Thread(target=self._run, args=(wanted,), daemon=True).start()
        return self.state()

    def state(self):
        with self._lock:
            return dict(self.status, failed=list(self.status["failed"]))

    def _run(self, entries):
        settings = self.app.settings.get()
        try:
            for e in entries:
                self.status["current"] = e["title"]
                existing = self.app.store.find_source("mal", e["mal_id"])
                try:
                    if existing:
                        self.status["already"] += 1
                        item = existing
                    else:
                        info = metadata.details(settings, "anime", "mal", e["mal_id"])
                        item = self.app.store.add(new_item("anime", info))
                        self.status["added"] += 1
                        time.sleep(PAUSE)
                    self._set_progress(item, e, fresh=not existing)
                except metadata.MetadataError as err:
                    self.status["failed"].append(f'{e["title"]}: {err}')
                    if "rate limiting" in str(err):
                        time.sleep(PAUSE * 10)
                self.status["done"] += 1
        finally:
            self.status["running"] = False
            self.status["current"] = ""

    def _set_progress(self, item, e, fresh):
        """Where you're up to, from the episodes you've watched (not moved back)."""
        total = item.get("episodes_total") or 0
        watched = e["watched"]
        if e["status"] == "completed" and total:
            watched = total
        if not watched:
            return
        nxt = watched + 1 if not total or watched < total else total
        current = item["progress"]
        if fresh or (current["season"] == 1 and current["episode"] < nxt):
            self.app.store.update(item["id"], {"progress": {"season": 1, "episode": nxt}})
