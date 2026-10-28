"""Your downloads folder, sorted into the titles in your list when you say so.

Every video's name (and, if that's not enough, its folder's name) is turned
into a guessed title, season and episode, and a kind: an ordinary episode, an
OVA, a special, a film, or an extra (openings, endings, trailers...). It is
matched against the titles, original titles and alternative names of
everything in your list, taking into account what each title is (a TV series,
an OVA, a film...).

A file only belongs to a title once you've said so, or Sort now has (that's
remembered per file, and never changed afterwards). New files wait, grouped
by guessed title with what they look like; Sort now sorts the clear ones and
searches MyAnimeList / TMDB (and asks the AI, with Sort now with AI) for the
rest. Files you take out of a title, or whose title you remove, are left for
you to sort.
"""

import difflib
import re
import threading
import time
import unicodedata
from collections import Counter
from pathlib import Path

from . import scanner
from .config import read_json, write_json

# A "Season N" folder holding episode 40 or more is numbered from the show's start.
ABSOLUTE_FROM = 40
RESCAN_EVERY = 60  # seconds

_JUNK = {
    "2160p", "1080p", "720p", "576p", "480p", "4k", "uhd", "hdr", "hdr10", "dv", "sdr",
    "bluray", "blu", "ray", "bd", "bdrip", "brrip", "bdremux", "remux", "web", "webrip",
    "webdl", "dl", "hdtv", "dvdrip", "dvd", "hdrip", "x264", "x265", "h264", "h265", "hevc",
    "avc", "av1", "aac", "aac2", "ac3", "eac3", "ddp", "ddp5", "dts", "flac", "opus", "atmos",
    "10bit", "8bit", "proper", "repack", "extended", "unrated", "uncut", "remastered", "imax",
    "multi", "dual", "audio", "subbed", "dubbed", "eng", "english", "jpn", "jap", "japanese",
    "esub", "esubs", "subs", "batch", "complete", "amzn", "nf", "cr", "dsnp", "hmax", "atvp",
    "hulu", "max", "itunes", "v2", "v3", "uncensored", "raw", "mkv", "mp4",
}
# Words that say what kind of file it is rather than which show it's from.
_KIND_WORDS = {"ova": "ova", "ovas": "ova", "oav": "ova", "oad": "ova", "special": "special",
               "specials": "special", "sp": "special", "recap": "special", "movie": "movie",
               "film": "movie", "gekijouban": "movie"}
# Openings, endings, trailers and the like: part of a show, but not an episode.
_EXTRA = re.compile(r"^(?:nc)?(?:op|ed)\d{0,2}[a-z]?$|^(?:ncop|nced|creditless|opening|ending|pv|cm|"
                    r"menu|menus|preview|previews|trailer|trailers|teaser|teasers|bonus|making|"
                    r"interview|promo|featurette)\d{0,2}$")
_EXTRA_DIRS = {"extras", "extra", "bonus", "bonuses", "nc", "ncop", "nced", "ncoped", "creditless",
               "featurettes", "menus", "menu", "pv", "pvs", "cm", "cms", "trailers", "previews"}
_KIND_DIRS = {"ova": "ova", "ovas": "ova", "oad": "ova", "oads": "ova", "specials": "special",
              "special": "special", "sp": "special", "sps": "special", "season 0": "special",
              "season 00": "special", "s00": "special", "movie": "movie", "movies": "movie",
              "film": "movie", "films": "movie"}
_GENERIC = {"", "episode", "ep", "video", "movie", "film", "extras", "extra", "bonus",
            "specials", "special", "featurettes", "sample", "subs", "subtitles", "season", "ova"}
_MARKERS = [
    re.compile(r"\bs\d{1,2}\s*e\d{1,4}\b"),
    re.compile(r"\b\d{1,2}x\d{1,3}\b"),
    re.compile(r"\bseason\s*\d{1,2}\s*(?:episode|ep)\s*\d"),
    re.compile(r"\s-\s+(?:sp\s*)?\d{1,4}(?:v\d)?\b"),
    re.compile(r"\b(?:episode|ep|e)\s*\d{1,4}\b"),
    re.compile(r"第\d{1,4}[話话]"),
]
_YEAR = re.compile(r"\b(19[3-9]\d|20[0-4]\d)\b")
_SEASON_TAIL = re.compile(
    r"\s+(?:season\s*(\d{1,2})|(\d{1,2})(?:st|nd|rd|th)\s+season|s(\d{1,2})|part\s*(\d{1,2})"
    r"|cour\s*(\d{1,2})|(\d))$")
_SEASON_DIR = re.compile(r"^(?:season|series|s)\s*\d{1,2}$|^(?:specials?|extras?|subs?|subtitles?|"
                         r"ovas?|sps?|bonus|nc|movies?)$")
_SP_NUMBER = re.compile(r"\b(?:sp|special|ova|oad)\s*(\d{1,3})\b")
_RES = re.compile(r"\b(2160|1440|1080|720|576|480)p\b", re.I)
_STOP = {"the", "a", "an", "no", "of", "to", "and", "in", "on", "wa", "ga", "de", "ni", "wo"}


def norm(text):
    """Lower-case, no accents or punctuation: "Frieren: Beyond Journey's End" -> "frieren beyond journeys end"."""
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = text.replace("&", " and ").replace("'", "").replace("’", "")
    return " ".join(re.sub(r"[^\w]+", " ", text).replace("_", " ").split())


def split_season(name):
    """"spy x family season 2" -> ("spy x family", 2). Plain names give (name, None)."""
    m = _SEASON_TAIL.search(name)
    if m and m.start() > 0:
        number = next(int(g) for g in m.groups() if g)
        return name[:m.start()].strip(), number
    return name, None


def _guess_from(text):
    """(title without kind words, title with them, year) from one file or folder name."""
    # Look for episode numbers and years before punctuation is stripped:
    # " - 05" only means "episode 5" while the dash is still there.
    cleaned = " ".join(scanner._clean(text).lower().split())
    cut = len(cleaned)
    for pattern in _MARKERS:
        m = pattern.search(cleaned)
        if m and m.start() < cut:
            cut = m.start()
    year = None
    for m in _YEAR.finditer(cleaned):
        if 0 < m.start() <= cut:
            year = int(m.group(1))
            cut = min(cut, m.start())
            break
    words = [w for w in norm(cleaned[:cut]).split() if w not in _JUNK]
    while len(words) > 1 and _EXTRA.match(words[-1]):
        words.pop()
    full = " ".join(words)
    stripped = [w for i, w in enumerate(words) if i == 0 or (w not in _KIND_WORDS and not _EXTRA.match(w))]
    return " ".join(stripped).strip(), full.strip(), year


def guess(rel_parts):
    """What a file is called, going by its name and then its folders:
    (title, year, season-in-the-name)."""
    g = guess_full(rel_parts)
    return g["title"], g["year"], g["title_season"]


def guess_full(rel_parts):
    title, full, year = _guess_from(Path(rel_parts[-1]).stem)
    if title in _GENERIC or title.isdigit():
        for folder in reversed(rel_parts[:-1]):
            if _SEASON_DIR.match(norm(folder)) or norm(folder) in _EXTRA_DIRS:
                continue
            title, full, folder_year = _guess_from(folder)
            year = year or folder_year
            if title not in _GENERIC and not title.isdigit():
                break
        else:
            title = full = ""
    base, season = split_season(title)
    full_base, _ = split_season(full)
    return {"title": base, "full": full_base, "year": year, "title_season": season}


def file_kind(rel, season, episodes, size):
    """episode / ova / special / movie / extra, from a file's name, folders and size."""
    parts = Path(rel).parts
    words = [w for w in norm(scanner._clean(Path(parts[-1]).stem)).split() if w not in _JUNK]
    dirs = [norm(d) for d in parts[:-1]]
    if any(_EXTRA.match(w) for w in words[1:]) or any(d in _EXTRA_DIRS for d in dirs):
        return "extra"
    if season == 0:
        return "special"
    kinds = {_KIND_WORDS[w] for w in words[1:] if w in _KIND_WORDS}
    kinds |= {_KIND_DIRS[d] for d in dirs if d in _KIND_DIRS}
    # "Haikyuu!! Movies", "Haikyuu OVAs & OADs": a folder of films or OVAs (not the pack's own folder).
    kinds |= {_KIND_DIRS[d.split()[-1]] for d in dirs[1:] if d and d.split()[-1] in _KIND_DIRS}
    for kind in ("ova", "special", "movie"):
        if kind in kinds:
            return kind
    if not episodes and size >= 900 * 1024 * 1024 and not any(_SEASON_DIR.match(d) for d in dirs):
        return "movie"
    return "episode"


def quality(v):
    """Higher is better: resolution first, then file size (for picking between copies)."""
    m = _RES.search(v["name"])
    return (int(m.group(1)) if m else 0, v.get("size") or 0)


def item_kind(item):
    """What a title in your list is: tv / ova / special / movie (ONAs count as tv)."""
    mt = (item.get("media_type") or "").lower()
    if mt in ("ova",):
        return "ova"
    if mt in ("special", "tv_special", "music"):
        return "special"
    if mt == "movie" or item.get("format") == "movie":
        return "movie"
    return "tv"


def item_names(item):
    """{(name, season-in-the-name)} for a title in your list."""
    cached = item.get("_names")
    if cached is not None:
        return cached
    names = set()
    for raw in [item.get("title"), item.get("original_title"), *(item.get("aliases") or [])]:
        n = norm(raw)
        if not n:
            continue
        for variant in {n, n.removeprefix("the ").strip()}:
            names.add(split_season(variant))
    item["_names"] = names
    return names


def score_item(item, title, title_season, file_season, year, has_episodes):
    """How sure we are that a file's title belongs to `item` (0-100).

    title_season: a number at the end of the file's title ("Show Season 2",
    "Toy Story 3"); file_season: the season from "S02E05"-style numbering."""
    if not title:
        return 0
    best = 0
    one_season_anime = item["type"] == "anime" and len([s for s in item.get("seasons") or [] if s["number"] > 0]) <= 1
    titles = {title} | ({f"{title} {year}"} if year else set())
    for name, name_season in item_names(item):
        if name in titles:
            season = title_season or file_season
            if name_season is not None:
                # "spy x family season 2": the file must be season 2.
                s = 100 if season == name_season else 90 if season is None else 0
            elif title_season is not None:
                if item["format"] == "movie":
                    s = 0      # "Toy Story 3" isn't "Toy Story"
                elif one_season_anime:
                    s = 100 if title_season == 1 else 30
                else:
                    s = 100    # a TV show on TMDB covers all its seasons
            elif one_season_anime and file_season not in (None, 0, 1):
                s = 30         # a later season, which MyAnimeList lists as a separate title
            else:
                s = 100
        elif name.startswith(title + " ") or name.endswith(" " + title):
            # "Frieren - 01.mkv" for "Frieren: Beyond Journey's End": likely, but ask.
            s = 85
        elif title.startswith(name + " ") and len(name) >= 4:
            # "Frieren Directors Cut" for "Frieren": possibly, so ask.
            s = 80
        else:
            # Small spelling differences: compare whole names, and the file's title
            # with the same number of words from the start of the name.
            words = len(title.split())
            ratio = max(max(difflib.SequenceMatcher(None, t, name).ratio(),
                            difflib.SequenceMatcher(None, t, " ".join(name.split()[:words])).ratio())
                        for t in titles)
            s = int(ratio * 85) if ratio >= 0.84 else 0
        best = max(best, s)
    if not best:
        return 0
    if item["format"] == "movie" and has_episodes:
        best -= 40
    if item["format"] == "series" and not has_episodes and year:
        best -= 10
    if item["format"] == "movie" and year and item.get("year") and abs(item["year"] - year) > 1:
        best -= 40
    return max(best, 0)


def score_file(item, g, kind):
    """score_item, plus whether the kind of file fits the kind of title."""
    s = max(score_item(item, g["title"], g["title_season"], g["file_season"], g["year"], g["has_eps"]),
            score_item(item, g["full"], g["title_season"], g["file_season"], g["year"], g["has_eps"])
            if g["full"] != g["title"] else 0)
    if not s:
        return 0
    have = item_kind(item)
    if kind in ("ova", "special", "movie"):
        if have == kind:
            s += 5
        elif kind == "special" and item["type"] == "tv":
            pass   # TMDB keeps a TV show's specials in "season 0" of the show itself
        elif kind == "movie" and have != "movie":
            s -= 40
        elif have in ("tv", "movie") or (kind == "ova" and have == "special"):
            s -= 35  # an OVA isn't "episode 13" of the TV series
    elif kind == "episode" and have in ("ova", "special") and (item.get("episodes_total") or 1) <= 1:
        s -= 20
    return max(0, min(s, 100))


class _Index:
    """Finds the few titles worth comparing a file's name with, so sorting stays
    fast with a long list: names that match exactly, plus the titles that share
    the most three-letter pieces with it (which still catches typos)."""

    def __init__(self, items):
        self.items = items
        self.exact = {}
        self.grams = {}
        for item in items:
            for name, _ in item_names(item):
                self.exact.setdefault(name, set()).add(item["id"])
                for g in self._grams(name):
                    self.grams.setdefault(g, set()).add(item["id"])
        self.by_id = {i["id"]: i for i in items}

    @staticmethod
    def _grams(text):
        text = f" {text} "
        return {text[i:i + 3] for i in range(len(text) - 2)}

    def candidates(self, *names, limit=12):
        found = set()
        counts = Counter()
        for name in names:
            if not name:
                continue
            found |= self.exact.get(name, set())
            for g in self._grams(name):
                counts.update(self.grams.get(g, ()))
        found |= {i for i, _ in counts.most_common(limit)}
        return [self.by_id[i] for i in found]


class Library:
    def __init__(self, app):
        self.app = app
        self._lock = threading.RLock()
        self.answers_path = app.data_dir / "library.json"
        answers = read_json(self.answers_path, {})
        self.answers = {"titles": dict(answers.get("titles") or {}),
                        "files": dict(answers.get("files") or {}),
                        # Decisions made by Sort now (rules, searching, AI), for undo.
                        "auto": list(answers.get("auto") or []),
                        # Ones you undid: never decided automatically again.
                        "rejected": list(answers.get("rejected") or []),
                        # What searching MyAnimeList / TMDB found for a guessed title.
                        "lookups": dict(answers.get("lookups") or {}),
                        # Folders worked out as a whole franchise (packs.py): not again unless they change.
                        "packs": dict(answers.get("packs") or {}),
                        # 1: from before Sort now (what was sorted then is written down once).
                        # A new install starts at 2: nothing is sorted until you say so.
                        "v": answers.get("v") or (1 if app.settings.get()["downloads_folder"] else 2)}
        self.folder = ""
        self.videos = []        # every video found, with what it's connected to
        self.subs = []
        self._walked = None
        self._walked_at = 0
        self._matched_for = None
        self.version = 0
        self.by_item = {}
        self.subs_for = {}      # video path -> paired subtitle files, best first
        self.questions = []
        self.error = ""
        self.busy = ""          # what Sort now is doing right now
        self.gemini_error = ""

    def save_answers(self):
        write_json(self.answers_path, self.answers, compact=True)

    # ---- scanning

    def scan(self, force=False):
        """Walk the downloads folder (at most once a minute unless forced) and match."""
        folder = self.app.settings.get()["downloads_folder"]
        stale = force or folder != self.folder or time.time() - self._walked_at > RESCAN_EVERY
        if stale:
            # Walking a big folder happens outside the lock, so the app stays responsive.
            videos, subs, error = [], [], ""
            if folder:
                try:
                    found = scanner.scan_folder(folder)
                    videos, subs = found["videos"], found["subs"]
                except (FileNotFoundError, OSError) as e:
                    error = f"The downloads folder isn't available ({e})."
            for v in videos:
                self.describe(v)
            for s in subs:
                g = guess_full(Path(s["rel"]).parts)
                s["guess"], s["year"], s["name_season"] = g["title"], g["year"], g["title_season"]
                s["season"], eps = scanner.parse_episode(Path(s["rel"]).parts)
                s["episodes"] = list(eps)
            walked = [(v["path"], v["size"]) for v in videos] + [(s["path"], 0) for s in subs]
            with self._lock:
                self.folder, self.error = folder, error
                if walked != self._walked:
                    self._matched_for = None
                self._walked = walked
                self.videos, self.subs = videos, subs
                self._walked_at = time.time()
        with self._lock:
            self._match_if_needed()

    @staticmethod
    def describe(v):
        """Add the guessed title, year and kind to a video found on disk."""
        g = guess_full(Path(v["rel"]).parts)
        v["guess"], v["full"], v["year"], v["name_season"] = g["title"], g["full"], g["year"], g["title_season"]
        if v["season"] and v["season"] > 1 and v["episodes"] and min(v["episodes"]) >= ABSOLUTE_FROM \
                and not scanner.season_in_name(Path(v["rel"]).name):
            # "Season 12/Naruto Shippuden - 250": a number that high is counted from
            # the start of the show (as MyAnimeList does), so the folder's season
            # mustn't decide which title or season it is.
            v["folder_season"], v["season"] = v["season"], None
        v["kind"] = file_kind(v["rel"], v["season"], v["episodes"], v.get("size") or 0)
        if v["kind"] in ("special", "ova") and not v["episodes"]:
            m = _SP_NUMBER.search(norm(scanner._clean(Path(v["rel"]).stem)))
            if m:
                v["episodes"] = [int(m.group(1))]
        return v

    def _match_if_needed(self):
        items = self.app.store.list()
        # Only what matters for matching: playing (which saves your place every few
        # seconds) mustn't trigger sorting the whole folder again.
        key = tuple(sorted((i["id"], i["title"], i.get("original_title"), tuple(i.get("aliases") or ()),
                            i["type"], i["format"], i.get("media_type"), i.get("year"),
                            tuple((s["number"], s["episodes"]) for s in i.get("seasons") or []))
                           for i in items))
        if key == self._matched_for:
            return
        self._matched_for = key
        self._match(items)
        self.version += 1

    def rematch(self):
        with self._lock:
            self._matched_for = None
            self._match_if_needed()

    def _scorer(self, index):
        scored_for = {}

        def scored(v):
            g = {"title": v["guess"], "full": v["full"], "title_season": v["name_season"],
                 "file_season": v["season"], "year": v["year"], "has_eps": bool(v["episodes"])}
            key = (tuple(g.values()), v["kind"])
            if key not in scored_for:
                found = sorted(((score_file(i, g, v["kind"]), i["id"])
                                for i in index.candidates(g["title"], g["full"])), reverse=True)
                scored_for[key] = [(s, i) for s, i in found if s >= 25]
            return scored_for[key]
        return scored

    @staticmethod
    def _is_sure(found):
        return bool(found) and found[0][0] >= 90 and (len(found) == 1 or found[1][0] < found[0][0] - 5)

    def _migrate(self, items):
        """Before version 2 the app sorted downloads by itself, again on every scan.
        Now only answers count (yours, or Sort now's), so everything the old way had
        sorted is written down once, as it is, and stays exactly where it was."""
        by_id = {i["id"]: i for i in items}
        scored = self._scorer(_Index(items))
        files, titles = self.answers["files"], self.answers["titles"]
        placed = {}
        later = []
        for v in self.videos:
            answer = files.get(v["rel"])
            if isinstance(answer, dict) and (answer.get("item") in by_id or answer.get("item") == "ignore"):
                if answer.get("item") in by_id:
                    placed.setdefault(str(Path(v["path"]).parent), Counter())[answer["item"]] += 1
                continue
            kind = "extra" if isinstance(answer, dict) and answer.get("item") == "extra" else v["kind"]
            title = titles.get(self.title_key(v))
            if title == "ignore":
                files[v["rel"]] = {"item": "ignore", "by": "before"}
                continue
            owner = title if title in by_id else None
            if not owner:
                found = scored(v)
                if self._is_sure(found):
                    owner = found[0][1]
                elif kind == "extra":
                    later.append((v, found))
                    continue
            if owner:
                files[v["rel"]] = {"item": owner, "by": "before", **({"kind": "extra"} if kind == "extra" else {})}
                placed.setdefault(str(Path(v["path"]).parent), Counter())[owner] += 1
        for v, found in later:
            folder = Path(v["path"]).parent
            owner = next((placed[str(d)].most_common(1)[0][0] for d in (folder, folder.parent) if placed.get(str(d))),
                         None) or (found[0][1] if found and found[0][0] >= 60 else None)
            if owner:
                files[v["rel"]] = {"item": owner, "by": "before", "kind": "extra"}
        self.answers["v"] = 2
        self.save_answers()

    def _match(self, items):
        by_id = {i["id"]: i for i in items}
        if self.answers.get("v", 1) < 2 and self.videos:
            self._migrate(items)
        scored = self._scorer(_Index(items))
        titles, files = self.answers["titles"], self.answers["files"]
        rejected = set(self.answers["rejected"])
        by_item, questions, groups, extras_waiting = {}, [], {}, []

        def add_to_group(v, reason, found, match=None, extra=None):
            key = self.title_key(v)
            group = groups.setdefault(key, {
                "kind": "title", "key": key, "guess": v["guess"],
                "season": v["name_season"] or v["season"], "file_kind": v["kind"],
                "year": v["year"], "has_eps": bool(v["episodes"]),
                "files": [], "candidates": [i for _, i in found[:5]], "reason": reason, "match": match})
            if extra:
                group.update(extra)
            group["files"].append(v["rel"])

        for v in self.videos:
            v["item"], v["how"] = None, ""
            answer = files.get(v["rel"])
            target = answer.get("item") if isinstance(answer, dict) else None
            if target == "ignore":
                v["how"] = "ignored"
                continue
            if target in by_id:
                v["item"], v["how"] = target, answer.get("by") or "you"
                if answer.get("episode"):
                    v["season"] = answer.get("season") if answer.get("season") is not None else 1
                    v["episodes"] = [answer["episode"]]
                if answer.get("kind"):
                    v["kind"] = answer["kind"]
                by_item.setdefault(v["item"], []).append(v)
                continue
            if target == "extra":
                v["kind"] = "extra"
            found = scored(v)
            if target == "unsorted" or (target and target not in by_id and target != "extra"):
                # You took it out of a title (or removed the title): Sort now leaves it to you.
                was = answer.get("was") if target == "unsorted" else target
                add_to_group(v, "held", found, extra={"was": (by_id.get(was) or {}).get("title", "")})
                continue
            v["how"] = "waiting"
            title = titles.get(self.title_key(v))
            if self.title_key(v) in rejected:
                add_to_group(v, "undone", found)
            elif title == "ignore":
                add_to_group(v, "ignore", found)
            elif title in by_id:
                add_to_group(v, "known", found, match=title)
            elif self._is_sure(found):
                add_to_group(v, "sure", found, match=found[0][1])
            elif v["kind"] == "extra":
                extras_waiting.append((v, found))
            else:
                add_to_group(v, "unsure" if found else "unknown", found)

        # Extras (openings, endings...) without a clear name: likely the show whose
        # episodes are in the same folder, or the folder above (sorted, or about to be).
        placed = {}
        clear = {rel: g["match"] for g in groups.values() if g.get("match") for rel in g["files"]}
        for v in self.videos:
            owner = v.get("item") or clear.get(v["rel"])
            if owner and v["kind"] != "extra":
                placed.setdefault(str(Path(v["path"]).parent), Counter())[owner] += 1
        for v, found in extras_waiting:
            folder = Path(v["path"]).parent
            owner = next((placed[str(d)].most_common(1)[0][0] for d in (folder, folder.parent) if placed.get(str(d))),
                         None) or (found[0][1] if found and found[0][0] >= 60 else None)
            if owner:
                add_to_group(v, "sure", found, match=owner)
            else:
                add_to_group(v, "unsure" if found else "unknown", found)

        questions.extend(groups.values())

        # Episodes the app couldn't number, in shows where other files are numbered.
        for item_id, vids in by_item.items():
            item = by_id[item_id]
            if item["format"] != "series":
                continue
            numbered = [v for v in vids if v["episodes"] and v["kind"] != "extra"]
            if not numbered:
                continue
            for v in vids:
                if not v["episodes"] and v["kind"] == "episode":
                    questions.append({"kind": "episode", "key": v["rel"], "item": item_id,
                                      "files": [v["rel"]], "guess": v["guess"]})

        # Subtitles: paired with the video they belong to, or asked about.
        subs_for = {}
        videos_by_dir = {}
        for v in self.videos:
            videos_by_dir.setdefault(str(Path(v["path"]).parent), []).append(v)
        by_rel = {v["rel"]: v for v in self.videos}
        for s in self.subs:
            answer = files.get(s["rel"])
            if isinstance(answer, dict) and answer.get("video"):
                if answer["video"] == "ignore":
                    continue
                target = by_rel.get(answer["video"])
                if target:
                    subs_for.setdefault(target["path"], []).append(dict(s, score=200, english=False))
                    continue
            nearby = []
            folder = Path(s["path"]).parent
            for d in (folder, folder.parent, folder.parent.parent):
                nearby += videos_by_dir.get(str(d), [])
            best, best_score = None, 0
            for v in nearby:
                if v.get("how") == "ignored":
                    continue
                siblings = videos_by_dir.get(str(Path(v["path"]).parent), [])
                found = scanner.subtitles_for(v, [s], siblings)
                # Pairing by episode number alone ("Show - 05.srt" next to "Show - 05.mkv")
                # only counts if they're the same show: a downloads folder mixes shows.
                if found and found[0]["score"] < 80 and s["guess"] and s["guess"] != v["guess"]:
                    found = []
                if found and found[0]["score"] > best_score:
                    best, best_score = (v, found[0]), found[0]["score"]
            if best:
                subs_for.setdefault(best[0]["path"], []).append(best[1])
                continue
            # Same show and episode by name, even if it's in another folder.
            matched = None
            if s["episodes"]:
                for item_id, vids in by_item.items():
                    if score_item(by_id[item_id], s["guess"], s["name_season"], s["season"],
                                  s["year"], True) >= 90:
                        matched = next((v for v in vids if s["episodes"][0] in v["episodes"]
                                        and (s["season"] in (None, v["season"]))), None)
                        if matched:
                            break
            if matched:
                subs_for.setdefault(matched["path"], []).append(dict(s, score=60, english=False))
                continue
            choices = [v["rel"] for v in nearby if v.get("item")][:50]
            questions.append({"kind": "subtitle", "key": s["rel"], "files": [s["rel"]],
                              "guess": s["guess"], "choices": choices})

        for path in subs_for:
            subs_for[path].sort(key=lambda x: -x["score"])
        # Questions already looked up show what the search found.
        for q in questions:
            found = self.answers["lookups"].get(q["key"]) or {}
            for field in ("suggest", "hint", "hint_item"):
                if found.get(field):
                    q[field] = found[field]
            if q.get("hint_item") not in by_id:
                q.pop("hint_item", None)
            if q.get("match") not in by_id:
                q.pop("match", None)
        self.by_item, self.subs_for, self.questions = by_item, subs_for, questions

    @staticmethod
    def title_key(v):
        season = v["name_season"] or (v["season"] if v["season"] and v["season"] > 1 else None)
        kind = v.get("kind") if v.get("kind") in ("ova", "special", "movie") else ""
        return f"{v['guess']}|{season or ''}" + (f"|{kind}" if kind else "")

    # ---- reading

    def state(self):
        with self._lock:
            self._match_if_needed()
            items = {}
            titles = {i["id"]: i for i in self.app.store.list()}
            for item_id, vids in self.by_item.items():
                main = [v for v in vids if v["kind"] != "extra"]
                if item_id in titles:
                    # Numbered the way the title page and the player number them
                    # ("Naruto - 150" is season 3 when the title has seasons).
                    pairs = scanner.episode_map(titles[item_id], main) if titles[item_id]["format"] == "series" else {}
                    eps = [f"{s}:{e}" for s, e in sorted(pairs)]
                else:
                    eps = sorted({f"{v['season'] if v['season'] is not None else 1}:{e}"
                                  for v in main for e in v["episodes"]})
                items[item_id] = {"files": len(main), "extras": len(vids) - len(main), "episodes": eps}
            unknown = sum(len(q["files"]) for q in self.questions if q.get("reason") == "unknown")
            waiting = sum(1 for v in self.videos if v.get("how") == "waiting")
            return {"folder": self.folder, "error": self.error, "items": items,
                    "questions": self.questions, "unknown_files": unknown, "waiting": waiting,
                    "auto": self.answers["auto"][-60:], "busy": self.busy,
                    "gemini_error": self.gemini_error,
                    "sort_now": dict(getattr(getattr(self.app, "sorter", None), "now", None) or {}),
                    "videos": len(self.videos), "scanned_at": self._walked_at}

    def videos_for(self, item_id):
        with self._lock:
            return [dict(v) for v in self.by_item.get(item_id, [])]

    def _show_folder(self, path):
        """The folder a show's files live in: past "Season 1", "Extras", "Specials",
        "Movies"... folders. None when that's the downloads folder itself (which
        mixes everything)."""
        folder = Path(path).parent
        while folder != folder.parent and (_SEASON_DIR.match(norm(folder.name)) or norm(folder.name) in _EXTRA_DIRS
                                          or norm(folder.name) in _KIND_DIRS):
            folder = folder.parent
        if not self.folder or folder == Path(self.folder) or Path(self.folder) not in [folder, *folder.parents]:
            return None
        return folder

    def family(self, item_id):
        """Other files in the same show folder as this title's files: the show's
        films, OVAs and specials (sorted to their own entries), extras, and files
        not sorted yet. [{"path", "name", "rel", "kind", "item"}]"""
        with self._lock:
            roots = {self._show_folder(v["path"]) for v in self.by_item.get(item_id, [])} - {None}
            if not roots:
                return []
            out = []
            for v in self.videos:
                if v.get("item") == item_id or v.get("how") == "ignored":
                    continue
                if any(root in Path(v["path"]).parents for root in roots):
                    out.append({"path": v["path"], "name": v["name"], "rel": v["rel"], "kind": v["kind"],
                                "item": v.get("item")})
            return out

    def unsorted_match(self, item, season, episode):
        """A download not sorted yet that looks like this episode of `item`
        (for watch parties: the other person is playing it, so it's wanted now)."""
        best = None
        with self._lock:
            for v in self.videos:
                if v.get("item") or v.get("how") == "ignored" or v.get("kind") == "extra":
                    continue
                g = {"title": v["guess"], "full": v["full"], "title_season": v["name_season"],
                     "file_season": v["season"], "year": v["year"], "has_eps": bool(v["episodes"])}
                score = score_file(dict(item), g, v["kind"])
                if score < 70:
                    continue
                if item["format"] == "series":
                    if episode not in v["episodes"] or v["season"] not in (None, season):
                        continue
                if not best or score > best[0]:
                    best = (score, v)
        return dict(best[1]) if best else None

    def subtitles_for(self, video_path):
        with self._lock:
            return [dict(s) for s in self.subs_for.get(video_path, [])]

    def contains(self, path):
        with self._lock:
            return any(v["path"] == path for v in self.videos)

    def pending(self):
        with self._lock:
            return [dict(q) for q in self.questions]

    # ---- answers

    def _group(self, key):
        return next((q for q in self.questions if q["kind"] == "title" and q["key"] == key), None)

    def answer(self, body, by="you"):
        """Remember an answer about a file or a group of files. A group's files are
        sorted there now; new files with the same name will be suggested there."""
        kind = body.get("kind")
        key = str(body.get("key") or "")
        target = str(body.get("item") or "")
        with self._lock:
            if kind == "title":
                if target != "ignore" and target not in {i["id"] for i in self.app.store.list()}:
                    raise ValueError("pick a title from your list")
                group = self._group(key)
                self.answers["titles"][key] = target
                if key in self.answers["rejected"]:
                    self.answers["rejected"].remove(key)
                for rel in (group or {}).get("files", []):
                    self.answers["files"][rel] = {"item": target, "by": by}
            elif kind in ("file", "episode"):
                entry = {"item": target, "by": by}
                if body.get("episode"):
                    entry["season"] = max(0, int(body.get("season") if body.get("season") is not None else 1))
                    entry["episode"] = max(1, int(body["episode"]))
                if body.get("file_kind"):
                    entry["kind"] = body["file_kind"]
                self.answers["files"][key] = entry
            elif kind == "subtitle":
                self.answers["files"][key] = {"video": str(body.get("video") or "ignore")}
            else:
                raise ValueError("unknown kind of answer")
            self.save_answers()
            self._matched_for = None
            self._match_if_needed()

    def assign(self, key, rels, item_id, by, label="", why="", added=False):
        """Sort now's decision: these files belong to `item_id` (listed under
        Sorted for you, where it can be undone)."""
        self.assign_many([{"key": key, "files": rels, "item": item_id, "by": by,
                           "label": label, "why": why, "added": added}])

    def assign_many(self, decisions):
        """Several of Sort now's decisions, saved together."""
        with self._lock:
            for d in decisions:
                for rel in d["files"]:
                    current = self.answers["files"].get(rel)
                    if isinstance(current, dict) and current.get("item") and current["item"] != "extra":
                        # Sorted already (by you or before), or held by you: never changed. Only
                        # a pack's matching may correct what Sort now did by itself before.
                        if not (d.get("replace") and current.get("by") not in ("you", None)
                                and current["item"] not in ("unsorted", "ignore")):
                            continue
                    extra = isinstance(current, dict) and current.get("item") == "extra"
                    entry = {"item": d["item"], "by": d["by"], **({"kind": "extra"} if extra else {})}
                    ep = (d.get("episodes") or {}).get(rel)
                    if ep and ep[1]:
                        entry.update(season=ep[0], episode=ep[1])
                    if (d.get("kinds") or {}).get(rel):
                        entry["kind"] = d["kinds"][rel]
                    self.answers["files"][rel] = entry
                self.answers["auto"].append({"key": d["key"], "kind": "title", "item": d["item"],
                                             "files": list(d["files"]), "by": d["by"], "at": time.time(),
                                             "label": d.get("label", ""), "why": d.get("why", ""),
                                             "added": bool(d.get("added"))})
            self.answers["auto"] = self.answers["auto"][-300:]
            self.save_answers()
            self._matched_for = None
            self._match_if_needed()

    def route(self, rel, item, season=None, episode=None, kind=None):
        """You moved one file: to a title (and episode), out of its title
        ("unsorted": Sort now leaves it to you), or ignored."""
        with self._lock:
            if rel not in {v["rel"] for v in self.videos}:
                raise ValueError("that file isn't in your downloads any more")
            current = self.answers["files"].get(rel) or {}
            if item == "unsorted":
                self.answers["files"][rel] = {"item": "unsorted", "was": current.get("item")}
            elif item == "ignore":
                self.answers["files"][rel] = {"item": "ignore", "by": "you"}
            else:
                if item not in {i["id"] for i in self.app.store.list()}:
                    raise ValueError("pick a title from your list")
                entry = {"item": item, "by": "you"}
                if episode:
                    entry["season"] = max(0, int(season if season is not None else 1))
                    entry["episode"] = max(1, int(episode))
                if kind in ("episode", "ova", "special", "movie", "extra"):
                    entry["kind"] = kind
                self.answers["files"][rel] = entry
            self.save_answers()
            self._matched_for = None
            self._match_if_needed()

    def forget_item(self, item_id):
        """A title was removed from your list: its files wait for you (Sort now
        doesn't put them anywhere by itself)."""
        with self._lock:
            for rel, answer in list(self.answers["files"].items()):
                if isinstance(answer, dict) and answer.get("item") == item_id:
                    self.answers["files"][rel] = {"item": "unsorted", "was": item_id}
            for key, target in list(self.answers["titles"].items()):
                if target == item_id:
                    del self.answers["titles"][key]
            self.save_answers()
            self._matched_for = None

    def reassign(self, old_id, new_id, renumber=None):
        """Files sorted to one title now belong to another (or the same title, with
        its episodes numbered differently): renumber(season, episode) gives the
        new numbers of files sorted to a particular episode."""
        with self._lock:
            for rel, answer in self.answers["files"].items():
                if isinstance(answer, dict) and answer.get("item") == old_id:
                    answer["item"] = new_id
                    if renumber and answer.get("season") and answer.get("episode"):
                        answer["season"], answer["episode"] = renumber(answer["season"], answer["episode"])
            for key, target in self.answers["titles"].items():
                if target == old_id:
                    self.answers["titles"][key] = new_id
            for entry in self.answers["auto"]:
                if isinstance(entry, dict) and entry.get("item") == old_id:
                    entry["item"] = new_id
            self.save_answers()
            self._matched_for = None

    def remember_lookup(self, key, found):
        """Keep what the automatic sorter found for a question (shown with it)."""
        with self._lock:
            lookups = self.answers["lookups"]
            lookups[key] = {**(lookups.get(key) or {}), **found}
            # Forget lookups for files that are long gone.
            if len(lookups) > 400:
                waiting = {q["key"] for q in self.questions}
                for k in sorted((k for k in lookups if k not in waiting),
                                key=lambda k: max(lookups[k].get("at", 0), lookups[k].get("gemini_at", 0)))[:100]:
                    del lookups[k]
            self.save_answers()
            self._matched_for = None
            self._match_if_needed()

    def undo(self, key):
        """Undo something Sort now did: its files wait for you again, and Sort now
        leaves them to you from then on. Returns the decisions undone."""
        with self._lock:
            undone = [a for a in self.answers["auto"] if a["key"] == key]
            for a in undone:
                for rel in a.get("files") or []:
                    answer = self.answers["files"].get(rel)
                    if isinstance(answer, dict) and answer.get("item") == a["item"] and answer.get("by") != "you":
                        del self.answers["files"][rel]
            self.answers["titles"].pop(key, None)
            self.answers["files"].pop(key, None)
            self.answers["auto"] = [a for a in self.answers["auto"] if a["key"] != key]
            if key not in self.answers["rejected"]:
                self.answers["rejected"].append(key)
            self.save_answers()
            self._matched_for = None
            self._match_if_needed()
            return undone

    def uses(self, item_id):
        """Whether any answer still points at a title."""
        with self._lock:
            return (item_id in self.answers["titles"].values()
                    or any(isinstance(a, dict) and a.get("item") == item_id for a in self.answers["files"].values()))


def watch(app, stop_event=None):
    """Keep the downloads folder up to date in the background. Nothing is sorted
    here: new files wait until you press Sort now."""
    while not (stop_event and stop_event.is_set()):
        try:
            app.library.scan()
        except Exception:  # noqa: BLE001 - never let the background thread die
            pass
        time.sleep(10)
