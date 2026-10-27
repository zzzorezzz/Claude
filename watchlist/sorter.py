"""The automatic sorter: what happens to downloads the app couldn't place by
their names alone, before you're asked about them.

1. An OVA, special, film or later season of an anime in your list: its
   MyAnimeList relations are checked for that exact entry.
2. MyAnimeList and TMDB are searched for the guessed title.
3. With an AI key (Gemini, Groq or OpenRouter), the AI looks at whatever is still left.

A confident answer is applied straight away (adding the title to your list if
needed) and shown under "Sorted for you", where it can be undone. Anything
else stays a question, with what was found shown as a suggestion.
"""

import re
import threading
import time
from pathlib import PurePosixPath

from . import ai, metadata
from .library import norm, score_file
from .store import new_item

LOOKUPS_PER_PASS = 6
RETRY_AFTER = 3 * 24 * 3600
GEMINI_BATCH = 12
NOW_LOOKUPS = 40          # Sort now: searches in one go
NOW_BATCHES = 5           # Sort now: AI requests in one go (12 downloads each)
NOW_RESEARCH_AFTER = 600  # Sort now searches again what was searched over 10 minutes ago
SURE = 90

# MyAnimeList media types for each kind of file.
_MAL_KINDS = {"ova": {"ova", "ona"}, "special": {"special", "tv_special"}, "movie": {"movie"},
              "episode": {"tv", "ona"}}


def _type_order(q, settings):
    """Which lists to search, most likely first."""
    names = [PurePosixPath(f).name for f in q["files"]]
    anime_like = q["file_kind"] in ("ova", "special") or any(
        n.startswith("[") or re.search(r"\s-\s\d{1,4}\b", n) for n in names)
    if q["file_kind"] == "movie":
        order = ["anime", "movie"] if anime_like else ["movie", "anime"]
    elif anime_like:
        order = ["anime", "tv", "movie"]
    elif not q.get("has_eps") and q.get("year"):
        order = ["movie", "tv", "anime"]
    else:
        order = ["tv", "anime", "movie"]
    have = {"anime": bool(settings.get("mal_client_id")), "tv": bool(settings.get("tmdb_api_key")),
            "movie": bool(settings.get("tmdb_api_key"))}
    return [t for t in order if have[t]]


def _as_item(result, item_type):
    return {"type": item_type, "format": result.get("format") or ("movie" if item_type == "movie" else "series"),
            "title": result.get("title", ""), "original_title": result.get("original_title", ""),
            "aliases": result.get("aliases") or [], "year": result.get("year"),
            "media_type": result.get("media_kind") or result.get("media_type") or "",
            "episodes_total": result.get("episodes_total"), "seasons": []}


def _guess(q):
    return {"title": q["guess"], "full": q["guess"], "title_season": q.get("season") if q.get("season") else None,
            "file_season": None, "year": q.get("year"), "has_eps": bool(q.get("has_eps"))}


def _suggestion(result, item_type, why=""):
    return {"type": item_type, "source": result["source"], "source_id": str(result["source_id"]),
            "title": result.get("title", ""), "year": result.get("year"), "poster": result.get("poster", ""),
            "media_type": result.get("media_type") or "", "media_kind": result.get("media_kind") or "",
            "format": result.get("format") or "", "rating": result.get("rating"),
            "aliases": (result.get("aliases") or [])[:6], "original_title": result.get("original_title") or "",
            "relation": result.get("relation", ""), "why": why}


def _pick(scored):
    """The one clear winner of [(score, suggestion)], or None."""
    scored = sorted(scored, key=lambda x: -x[0])
    if not scored or scored[0][0] < SURE:
        return None
    if len(scored) > 1 and scored[1][0] >= scored[0][0] - 5 and scored[1][1]["source_id"] != scored[0][1]["source_id"]:
        return None
    return scored[0][1]


class Sorter:
    def __init__(self, app):
        self.app = app
        self._running = threading.Lock()
        self._force = False          # Sort now: apply decisions even with "sort for me" off
        self.now = {"running": False, "last": None}

    def run_once(self):
        if not self._running.acquire(blocking=False):
            return
        try:
            self._run()
        finally:
            self.app.library.busy = ""
            self._running.release()

    # ---- Sort now

    def run_now(self):
        """Sort now: search for, and ask the AI about, every download that's
        waiting, straight away. Returns what happened (also kept in self.now)."""
        lib = self.app.library
        if not self._running.acquire(timeout=120):
            return {"error": "the sorter is busy - try again in a minute"}
        self.now = {"running": True, "last": self.now.get("last")}
        auto_before = len(lib.answers["auto"])
        report = {"at": time.time(), "sorted": 0, "left": 0, "suggested": 0, "ai": "", "error": ""}
        try:
            self._force = True
            lib.busy = "Looking through your downloads…"
            lib.scan(force=True)
            settings = self.app.settings.get()
            rejected = set(lib.answers["rejected"])
            lookups = lib.answers["lookups"]
            waiting = [q for q in lib.pending() if q["kind"] == "title" and q["key"] not in rejected]
            if settings.get("mal_client_id") or settings.get("tmdb_api_key"):
                todo = [q for q in waiting
                        if time.time() - (lookups.get(q["key"]) or {}).get("at", 0) > NOW_RESEARCH_AFTER]
                for n, q in enumerate(todo[:NOW_LOOKUPS]):
                    lib.busy = f'Looking up "{q["guess"]}" ({n + 1} of {min(len(todo), NOW_LOOKUPS)})…'
                    self._look_up(q, settings)
            helpers = ai.available(settings)
            if helpers:
                waiting = [q for q in lib.pending() if q["kind"] == "title" and q["key"] not in rejected]
                batches = [waiting[i:i + GEMINI_BATCH] for i in range(0, len(waiting), GEMINI_BATCH)][:NOW_BATCHES]
                for n, batch in enumerate(batches):
                    lib.busy = (f"Asking the AI about {len(batch)} download{'s' if len(batch) != 1 else ''}"
                                + (f" (batch {n + 1} of {len(batches)})" if len(batches) > 1 else "") + "…")
                    used = self._ask_gemini(batch, settings, fresh=True)
                    if used:
                        report["ai"] = ai.NAMES[used]
                    elif lib.gemini_error:
                        report["error"] = lib.gemini_error
                        break
            elif waiting:
                report["error"] = "no AI key - add a free one in Settings to let AI sort the rest"
        finally:
            self._force = False
            lib.busy = ""
            state = lib.state()
            report["sorted"] = max(0, len(lib.answers["auto"]) - auto_before)
            titles = [q for q in state["questions"] if q["kind"] == "title"]
            report["left"] = len(state["questions"])
            report["suggested"] = sum(1 for q in titles if q.get("suggest") or q.get("hint"))
            self.now = {"running": False, "last": report}
            self._running.release()
        return report

    # ---- the passes

    def _run(self):
        lib = self.app.library
        settings = self.app.settings.get()
        rejected = set(lib.answers["rejected"])
        lookups = lib.answers["lookups"]
        now = time.time()
        waiting = [q for q in lib.pending() if q["kind"] == "title" and q["key"] not in rejected]
        if settings.get("mal_client_id") or settings.get("tmdb_api_key"):
            todo = [q for q in waiting if now - (lookups.get(q["key"]) or {}).get("at", 0) > RETRY_AFTER]
            for q in todo[:LOOKUPS_PER_PASS]:
                lib.busy = f'Looking up "{q["guess"]}"…'
                self._look_up(q, settings)
        if ai.available(settings):
            waiting = [q for q in lib.pending() if q["kind"] == "title" and q["key"] not in rejected]
            todo = [q for q in waiting
                    if now - (lookups.get(q["key"]) or {}).get("gemini_at", 0) > RETRY_AFTER]
            if todo:
                lib.busy = f"Asking {ai.NAMES[ai.available(settings)[0]]} about your downloads…"
                self._ask_gemini(todo[:GEMINI_BATCH], settings)

    def _look_up(self, q, settings):
        lib = self.app.library
        suggest = []
        try:
            pick, suggest = self._from_relations(q, settings)
            if not pick:
                pick, found = self._from_search(q, settings)
                suggest += [s for s in found if s["source_id"] not in {x["source_id"] for x in suggest}]
        except metadata.MetadataError as e:
            # Offline or a key problem: try again on a later pass, not in 3 days.
            lib.remember_lookup(q["key"], {"at": time.time() - RETRY_AFTER + 600, "error": str(e)})
            return
        if pick and self._apply(q, pick, "search", pick.get("why") or "found on " + (
                "MyAnimeList" if pick["source"] == "mal" else "TMDB")):
            return
        lib.remember_lookup(q["key"], {"at": time.time(), "suggest": suggest[:4]})

    def _from_relations(self, q, settings):
        """An OVA / special / film / later season of an anime that's in your list."""
        kind = q["file_kind"]
        later = kind == "episode" and (q.get("season") or 1) == 2
        if kind not in ("ova", "special", "movie") and not later:
            return None, []
        out = []
        for item_id in q.get("candidates") or []:
            try:
                item = self.app.store.get(item_id)
            except KeyError:
                continue
            if item["source"] != "mal":
                continue
            for r in metadata.related(settings, item):
                media = (r.get("media_kind") or "").lower()
                if later and (r.get("relation") or "").lower() != "sequel":
                    continue
                if media in _MAL_KINDS[kind]:
                    out.append(_suggestion(r, "anime", f'{r.get("relation") or "related"} of {item["title"]}'))
        pick = out[0] if len(out) == 1 else None
        return pick, out

    def _from_search(self, q, settings):
        scored = []
        if len(q["guess"]) < 3:
            return None, []   # too short to search for
        for item_type in _type_order(q, settings):
            query = q["guess"] if not q.get("season") or q["season"] == 1 or item_type != "anime" \
                else f'{q["guess"]} season {q["season"]}'
            results = metadata.search(settings, item_type, query)[:15]
            for r in results:
                s = score_file(_as_item(r, item_type), _guess(q), q["file_kind"])
                if s >= 50:
                    scored.append((s, _suggestion(r, item_type)))
            pick = _pick([x for x in scored if x[1]["type"] == item_type])
            if pick:
                return pick, [s for _, s in sorted(scored, key=lambda x: -x[0])]
        return None, [s for _, s in sorted(scored, key=lambda x: -x[0])]

    def _ask_gemini(self, todo, settings, fresh=False):
        lib = self.app.library
        lookups = lib.answers["lookups"]
        items = self.app.store.list()
        by_id = {i["id"]: i for i in items}
        asked = [{"key": q["key"], "files": q["files"][:3], "guess": q["guess"], "season": q.get("season"),
                  "file_kind": q["file_kind"],
                  "found": [{"type": s["type"], "title": s["title"], "year": s["year"]}
                            for s in (lookups.get(q["key"]) or {}).get("suggest", [])[:4]]}
                 for q in todo]
        try:
            replies, provider = ai.sort_downloads(settings, items, asked, fresh=fresh)
        except ai.AIError as e:
            lib.gemini_error = str(e)
            for q in todo:   # try again in an hour
                lib.remember_lookup(q["key"], {"gemini_at": time.time() - RETRY_AFTER + 3600})
            return None
        lib.gemini_error = ""
        who = ai.NAMES[provider]
        for q in todo:
            row = replies.get(q["key"]) or {}
            decision, sure = row.get("decision"), row.get("confidence", 0) >= 0.8
            why = str(row.get("why") or "")[:160]
            note = {"gemini_at": time.time()}
            if decision == "in_list" and row.get("item_id") in by_id:
                if sure and self._apply(q, {"item_id": row["item_id"]}, provider, why):
                    continue
                note["hint"] = f'{who} thinks this is {by_id[row["item_id"]]["title"]}' + (f" ({why})" if why else "")
                note["hint_item"] = row["item_id"]
            elif decision == "add" and isinstance(row.get("add"), dict):
                found = self._verify(row["add"], settings)
                if found and sure and self._apply(q, found, provider, why):
                    continue
                if found:
                    current = (lookups.get(q["key"]) or {}).get("suggest", [])
                    note["suggest"] = [found] + [s for s in current if s["source_id"] != found["source_id"]][:3]
                    note["hint"] = f'{who} thinks this is {found["title"]}' + (f" ({why})" if why else "")
            elif decision == "not_a_show":
                note["hint"] = f"{who} thinks this isn't a show or film" + (f" ({why})" if why else "")
            lib.remember_lookup(q["key"], note)
        return provider

    def _verify(self, add, settings):
        """A title the AI named, checked against MyAnimeList / TMDB, or None."""
        item_type = add.get("type")
        title = str(add.get("title") or "")
        if item_type not in ("anime", "tv", "movie") or not title:
            return None
        if not settings.get("mal_client_id" if item_type == "anime" else "tmdb_api_key"):
            return None
        try:
            results = metadata.search(settings, item_type, title)[:10]
        except metadata.MetadataError:
            return None
        want = norm(title)
        year = add.get("year") if isinstance(add.get("year"), int) else None
        for r in results:
            names = {norm(n) for n in [r.get("title"), r.get("original_title"), *(r.get("aliases") or [])] if n}
            if want in names and (not year or not r.get("year") or abs(r["year"] - year) <= 1):
                return _suggestion(r, item_type, "named by AI, found on " +
                                   ("MyAnimeList" if item_type == "anime" else "TMDB"))
        return None

    # ---- applying a decision

    def _apply(self, q, pick, by, why):
        """Connect the files to a title, adding it to your list first if needed."""
        if not self._force and not self.app.settings.get().get("auto_sort", True):
            return False   # you'd rather decide: it's shown as a suggestion instead
        lib = self.app.library
        added = False
        item_id = pick.get("item_id")
        try:
            if not item_id:
                existing = self.app.store.find_source(pick["source"], pick["source_id"])
                if existing:
                    item_id = existing["id"]
                else:
                    info = metadata.details(self.app.settings.get(), pick["type"], pick["source"], pick["source_id"])
                    item_id = self.app.store.add(new_item(pick["type"], info))["id"]
                    added = True
            title = self.app.store.get(item_id)["title"]
            count = len(q["files"])
            lib.answer({"kind": "title", "key": q["key"], "item": item_id, "added": added, "why": why,
                        "label": f'{count} file{"s" if count != 1 else ""} → {title}'
                                 + (" (added to your list)" if added else "")}, by=by)
            return True
        except (metadata.MetadataError, KeyError, ValueError):
            return False
