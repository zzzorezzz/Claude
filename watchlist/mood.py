"""I'm in the mood for...: what you typed (a title, a word, a sentence, typos and
all) turned into things to watch, for the kind you picked first.

It runs in the background and shows what it has as soon as it has it:

1. What you meant: when it looks like a title, it's found straight away, even
   misspelled ("indterstella" is Interstellar), by searching for it and, if
   nothing comes back, for the spellings one letter away.
2. Titles like it, from TMDB's and MyAnimeList's recommendations: in seconds.
3. Your AI helper's picks, given what you meant, your list, and what's new and
   popular right now (so a new film with the same feel can come up): each one
   is checked on MyAnimeList / TMDB and shown the moment it is.

Any of it can be cancelled.
"""

import difflib
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import ai, discover, metadata

KINDS = ("movie", "tv", "anime")
WORDS = {"movie": "films", "tv": "TV shows", "anime": "anime"}


def _norm(text):
    return metadata.norm_title(text)


def _closeness(query, row):
    q = _norm(query)
    names = [_norm(n) for n in (row.get("title"), row.get("original_title"), *(row.get("aliases") or [])) if n]
    best = 0.0
    for n in names:
        if n == q:
            return 1.0
        if n.startswith(q + " ") or q.startswith(n + " "):
            best = max(best, 0.9)
        best = max(best, difflib.SequenceMatcher(None, q, n).ratio())
    return best


def _spellings(text):
    """The text with one letter left out, or two swapped: most typos are one of these."""
    t = text.strip().lower()
    out = []
    for i in range(len(t)):
        out.append(t[:i] + t[i + 1:])
    for i in range(len(t) - 1):
        out.append(t[:i] + t[i + 1] + t[i] + t[i + 2:])
    seen, keep = {t}, []
    for v in out:
        v = " ".join(v.split())
        if len(v) >= 4 and v not in seen:
            seen.add(v)
            keep.append(v)
    return keep


class Job:
    def __init__(self, app, kind, text):
        self.app = app
        self.id = uuid.uuid4().hex[:10]
        self.kind = kind if kind in KINDS else ""
        self.kinds = [self.kind] if self.kind else list(KINDS)
        self.text = text.strip()
        self.lock = threading.Lock()
        self.cancelled = False
        self.state = {"id": self.id, "kind": self.kind, "text": self.text, "stage": "Reading what you typed…",
                      "pct": 3, "done": False, "error": "", "understood": "", "seed": None,
                      "like": [], "picks": [], "from_list": [], "intro": "", "by": "", "note": ""}
        self.shown = set()

    # ---- what the page sees

    def view(self):
        with self.lock:
            return {k: (list(v) if isinstance(v, list) else v) for k, v in self.state.items()}

    def _set(self, **kw):
        if self.cancelled:
            return
        with self.lock:
            self.state.update(kw)

    def _add(self, group, rows):
        """New titles for a group, each shown once (in the first group it turns up in)."""
        if self.cancelled:
            return
        rows = [r for r in rows if (r["source"], r["source_id"]) not in self.shown]
        if not rows:
            return
        self.app.mark_listed(rows)
        with self.lock:
            for r in rows:
                self.shown.add((r["source"], r["source_id"]))
            self.state[group] = self.state[group] + rows

    def cancel(self):
        self.cancelled = True
        with self.lock:
            self.state.update(done=True, stage="Cancelled", pct=100)

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        return self

    # ---- the work

    def _run(self):
        s = self.app.settings.get()
        try:
            pool = ThreadPoolExecutor(3)
            seed_f = pool.submit(self._seed, s)
            trend_f = pool.submit(self._trending, s)
            seed = None
            try:
                seed = seed_f.result(timeout=12)
            except Exception:  # noqa: BLE001 - no seed: the AI still reads what you typed
                pass
            if self.cancelled:
                return
            like_f = pool.submit(self._like, s, seed) if seed else None
            helpers = ai.available(s)
            if helpers:
                try:
                    trending = trend_f.result(timeout=8)
                except Exception:  # noqa: BLE001
                    trending = []
                self._set(stage=f"{ai.NAMES[helpers[0]]} is picking for you…", pct=35)
                self._ask(s, seed, trending)
            else:
                self._set(note="Add a free AI key in Settings for picks that follow your words too, not only titles like one.")
            if like_f:
                like_f.result()
            if not (self.state["like"] or self.state["picks"] or self.state["from_list"]) and not self.state["error"]:
                self._set(error="Nothing came up - try other words, or another kind.")
            pool.shutdown(wait=False)
        except ai.AIError as e:
            self._set(error=f"Your AI helper didn't answer: {e}")
        except metadata.MetadataError as e:
            self._set(error=str(e))
        finally:
            self._set(done=True, pct=100, stage="Done")

    def _search(self, s, kind, q):
        try:
            return [dict(r, type=kind) for r in metadata.search(s, kind, q)[:6]]
        except metadata.MetadataError:
            return []

    def _seed(self, s):
        """The title you typed, if it looks like one: (row, how sure)."""
        words = self.text.split()
        if not (1 <= len(words) <= 7) or len(self.text) < 3:
            return None
        kinds = [k for k in self.kinds if (k == "anime" and s["mal_client_id"]) or (k != "anime" and s["tmdb_api_key"])]
        if not kinds:
            return None
        self._set(stage="Looking up what you typed…", pct=8)
        q = self.text.strip(" ?!.\"'")
        q = q[5:] if q.lower().startswith("like ") else q
        with ThreadPoolExecutor(6) as pool:
            rows = [r for found in pool.map(lambda k: self._search(s, k, q), kinds) for r in found]
        best = max(rows, key=lambda r: (_closeness(q, r) >= 0.85, _closeness(q, r), r.get("popularity") or 0), default=None)
        if not best or _closeness(q, best) < 0.85:
            # A typo? The spellings one letter away (all at once).
            if len(words) <= 3 and len(q) >= 5:
                variants = _spellings(q)[:40]
                with ThreadPoolExecutor(12) as pool:
                    futs = [pool.submit(self._search, s, k, v) for v in variants for k in kinds]
                    rows = [r for f in futs for r in f.result()]
                best = max(rows, key=lambda r: (_closeness(q, r), r.get("popularity") or 0), default=None)
                if not best or _closeness(q, best) < 0.72:
                    return None
            else:
                return None
        self._set(seed={k: best.get(k) for k in ("title", "year", "type", "poster", "source", "source_id")},
                  understood=f"{best['title']}" + (f" ({best['year']})" if best.get("year") else ""))
        return best

    def _like(self, s, seed):
        """Titles like the one you typed, from the same kind's recommendations."""
        self._set(stage=f"Finding titles like {seed['title']}…", pct=20)
        try:
            if seed["source"] == "mal":
                rows = [dict(r, type="anime") for r in metadata.MAL(s["mal_client_id"]).alike(seed["source_id"])]
            else:
                media = seed["source_id"].split("/")[0]
                tmdb = metadata.TMDB(s["tmdb_api_key"])
                rows = [dict(r, type="movie" if media == "movie" else "tv") for r in tmdb.alike(seed["source_id"])]
                rows = [r for r in rows if not discover._is_anime(r)]
        except metadata.MetadataError:
            return
        rows = [dict(r, why=f"Like {seed['title']}") for r in rows if r["type"] in self.kinds]
        self._add("like", rows[:16])

    def _trending(self, s):
        """What's new and popular now, as context for the AI (it knows less about the newest titles)."""
        out = []
        for k in self.kinds:
            try:
                if k == "anime" and s["mal_client_id"]:
                    out += [dict(r, type="anime") for r in metadata.MAL(s["mal_client_id"]).airing_now(30)]
                elif k in ("movie", "tv") and s["tmdb_api_key"]:
                    rows = metadata.TMDB(s["tmdb_api_key"]).trending("movie" if k == "movie" else "tv")
                    out += [dict(r, type=k) for r in rows if not discover._is_anime(r)]
            except metadata.MetadataError:
                continue
        return out

    def _ask(self, s, seed, trending):
        items = [i for i in self.app.store.list() if i["type"] in self.kinds]
        ranked = sorted(items, key=lambda i: (-(i.get("last_played") or 0), -(i.get("added") or 0)))[:80]
        mine = [f'{i["id"]}|{i["title"]}|' + ("not started" if not i.get("last_played") else
                f'up to S{i["progress"]["season"]} E{i["progress"]["episode"]}') for i in ranked]
        reply = ai.mood_picks(s, self.text, [WORDS[k] for k in self.kinds], seed, mine,
                              [f'{r["title"]} ({r.get("year") or "?"}, {r["type"]})' for r in trending[:45]])
        if self.cancelled:
            return
        by_id = {i["id"]: i for i in items}
        mine_rows = [dict(by_id[r["id"]], why=r.get("why") or "") for r in reply["from_list"] if r.get("id") in by_id]
        self._set(intro=reply["intro"], by=reply["by"], from_list=mine_rows,
                  understood=self.state["understood"] or reply.get("understood", ""),
                  stage="Checking each pick on MyAnimeList / TMDB…", pct=70)
        picks = [p for p in reply["new"] if p.get("type") in self.kinds]
        if not picks:
            return
        with ThreadPoolExecutor(8) as pool:
            futs = [pool.submit(discover.verify, s, [p]) for p in picks]
            done = 0
            for f in as_completed(futs):
                if self.cancelled:
                    return
                done += 1
                try:
                    self._add("picks", f.result())
                except metadata.MetadataError:
                    pass
                self._set(pct=70 + int(28 * done / len(futs)))


class Jobs:
    def __init__(self, app):
        self.app = app
        self.jobs = {}
        self.lock = threading.Lock()

    def start(self, kind, text):
        job = Job(self.app, kind, text)
        with self.lock:
            now = time.time()
            self.jobs = {k: v for k, v in self.jobs.items() if now - v.started < 1800}
            job.started = now
            self.jobs[job.id] = job
        return job.start()

    def get(self, job_id):
        with self.lock:
            return self.jobs.get(job_id)
