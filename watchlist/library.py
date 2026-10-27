"""Your downloads folder, sorted into the titles in your list automatically.

Every video's name (and, if that's not enough, its folder's name) is turned
into a guessed title, season and episode, and matched against the titles,
original titles and alternative names of everything in your list. Files the
app is sure about are connected straight away; files it isn't sure about are
put in front of you as a question, and your answer is remembered (for that
file, and for every other file with the same guessed title).
"""

import difflib
import re
import threading
import time
import unicodedata
from pathlib import Path

from . import scanner
from .config import read_json, write_json

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
# Openings, endings and other extras: part of the show, but not an episode.
_EXTRAS = {"ncop", "nced", "op", "ed", "creditless", "opening", "ending", "pv", "cm", "menu",
           "preview", "trailer", "teaser"}
_GENERIC = {"", "episode", "ep", "video", "movie", "film", "extras", "extra", "bonus",
            "specials", "special", "featurettes", "sample", "subs", "subtitles", "season"}
_MARKERS = [
    re.compile(r"\bs\d{1,2}\s*e\d{1,4}\b"),
    re.compile(r"\b\d{1,2}x\d{1,3}\b"),
    re.compile(r"\bseason\s*\d{1,2}\s*(?:episode|ep)\s*\d"),
    re.compile(r"\s-\s+\d{1,4}(?:v\d)?\b"),
    re.compile(r"\b(?:episode|ep|e)\s*\d{1,4}\b"),
    re.compile(r"第\d{1,4}[話话]"),
]
_YEAR = re.compile(r"\b(19[3-9]\d|20[0-4]\d)\b")
_SEASON_TAIL = re.compile(
    r"\s+(?:season\s*(\d{1,2})|(\d{1,2})(?:st|nd|rd|th)\s+season|s(\d{1,2})|part\s*(\d{1,2})"
    r"|cour\s*(\d{1,2})|(\d))$")
_SEASON_DIR = re.compile(r"^(?:season|series|s)\s*\d{1,2}$|^(?:specials?|extras?|subs?|subtitles?)$")


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
    """(title, year) from one file or folder name."""
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
    while len(words) > 1 and (words[-1] in _EXTRAS or re.fullmatch(r"(?:ncop|nced|op|ed)\d{0,2}", words[-1])):
        words.pop()
    return " ".join(words).strip(), year


def guess(rel_parts):
    """What a file is called, going by its name and then its folders:
    (title, year, season-in-the-name)."""
    title, year = _guess_from(Path(rel_parts[-1]).stem)
    if title in _GENERIC or title.isdigit():
        for folder in reversed(rel_parts[:-1]):
            if _SEASON_DIR.match(norm(folder)):
                continue
            title, folder_year = _guess_from(folder)
            year = year or folder_year
            if title not in _GENERIC and not title.isdigit():
                break
        else:
            title = ""
    base, season = split_season(title)
    return base, year, season


def item_names(item):
    """{(name, season-in-the-name)} for a title in your list."""
    names = set()
    for raw in [item.get("title"), item.get("original_title"), *(item.get("aliases") or [])]:
        n = norm(raw)
        if not n:
            continue
        for variant in {n, n.removeprefix("the ").strip()}:
            names.add(split_season(variant))
    return names


def score_item(item, title, title_season, file_season, year, has_episodes):
    """How sure we are that a file belongs to `item` (0-100).

    title_season: a number at the end of the file's title ("Show Season 2",
    "Toy Story 3"); file_season: the season from "S02E05"-style numbering."""
    if not title:
        return 0
    best = 0
    one_season_anime = item["type"] == "anime" and len(item.get("seasons") or []) <= 1
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
            elif one_season_anime and file_season not in (None, 1):
                s = 30         # a later season, which MyAnimeList lists as a separate title
            else:
                s = 100
        elif name.startswith(title + " ") or name.endswith(" " + title):
            # "Frieren - 01.mkv" for "Frieren: Beyond Journey's End": likely, but ask.
            s = 85
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


class Library:
    def __init__(self, app):
        self.app = app
        self._lock = threading.RLock()
        self.answers_path = app.data_dir / "library.json"
        answers = read_json(self.answers_path, {})
        self.answers = {"titles": dict(answers.get("titles") or {}),
                        "files": dict(answers.get("files") or {})}
        self.folder = ""
        self.videos = []        # every video found, with what it's connected to
        self.subs = []
        self._walked_at = 0
        self._matched_for = None
        self.version = 0
        self.by_item = {}
        self.subs_for = {}      # video path -> paired subtitle files, best first
        self.questions = []
        self.error = ""

    # ---- scanning

    def scan(self, force=False):
        """Walk the downloads folder (at most once a minute unless forced) and match."""
        folder = self.app.settings.get()["downloads_folder"]
        with self._lock:
            stale = force or folder != self.folder or time.time() - self._walked_at > RESCAN_EVERY
            if stale:
                self.folder = folder
                self.error = ""
                videos, subs = [], []
                if folder:
                    try:
                        found = scanner.scan_folder(folder)
                        videos, subs = found["videos"], found["subs"]
                    except (FileNotFoundError, OSError) as e:
                        self.error = f"The downloads folder isn't available ({e})."
                for v in videos:
                    v["guess"], v["year"], v["name_season"] = guess(Path(v["rel"]).parts)
                for s in subs:
                    s["guess"], s["year"], s["name_season"] = guess(Path(s["rel"]).parts)
                    s["season"], eps = scanner.parse_episode(Path(s["rel"]).parts)
                    s["episodes"] = list(eps)
                walked = [(v["path"], v["size"]) for v in videos] + [(s["path"], 0) for s in subs]
                changed = walked != getattr(self, "_walked", None)
                self._walked = walked
                self.videos, self.subs = videos, subs
                self._walked_at = time.time()
                if changed:
                    self._matched_for = None
            self._match_if_needed()

    def _match_if_needed(self):
        items = self.app.store.list()
        key = tuple(sorted((i["id"], i.get("modified", 0)) for i in items))
        if key == self._matched_for:
            return
        self._matched_for = key
        self._match(items)
        self.version += 1

    def _match(self, items):
        by_id = {i["id"]: i for i in items}
        titles, files = self.answers["titles"], self.answers["files"]
        by_item, questions, groups = {}, [], {}

        for v in self.videos:
            v["item"], v["how"] = None, ""
            answer = files.get(v["rel"])
            if isinstance(answer, dict) and answer.get("item"):
                if answer["item"] == "ignore":
                    v["how"] = "ignored"
                    continue
                if answer["item"] in by_id:
                    v["item"], v["how"] = answer["item"], "you"
                    if answer.get("episode"):
                        v["season"] = answer.get("season") or 1
                        v["episodes"] = [answer["episode"]]
            if not v["item"]:
                key = self._title_key(v)
                answer = titles.get(key)
                if answer == "ignore":
                    v["how"] = "ignored"
                    continue
                if answer in by_id:
                    v["item"], v["how"] = answer, "you"
                else:
                    season = v["name_season"] or v["season"]
                    scored = sorted(((score_item(i, v["guess"], v["name_season"], v["season"], v["year"],
                                                 bool(v["episodes"])), i["id"]) for i in items), reverse=True)
                    scored = [(s, i) for s, i in scored if s >= 25]
                    sure = scored and scored[0][0] >= 90 and (len(scored) == 1 or scored[1][0] < scored[0][0] - 5)
                    if sure:
                        v["item"], v["how"] = scored[0][1], "auto"
                    else:
                        group = groups.setdefault(key, {
                            "kind": "title", "key": key, "guess": v["guess"],
                            "season": season, "files": [],
                            "candidates": [i for _, i in scored[:5]],
                            "reason": "unsure" if scored else "unknown"})
                        group["files"].append(v["rel"])
                        continue
            by_item.setdefault(v["item"], []).append(v)

        questions.extend(groups.values())

        # Episodes the app couldn't number, in shows where other files are numbered.
        for item_id, vids in by_item.items():
            item = by_id[item_id]
            if item["format"] != "series" or not any(v["episodes"] for v in vids):
                continue
            for v in vids:
                if not v["episodes"]:
                    questions.append({"kind": "episode", "key": v["rel"], "item": item_id,
                                      "files": [v["rel"]], "guess": v["guess"]})

        # Subtitles: paired with the video they belong to, or asked about.
        subs_for = {}
        videos_by_dir = {}
        for v in self.videos:
            videos_by_dir.setdefault(str(Path(v["path"]).parent), []).append(v)
        for s in self.subs:
            answer = files.get(s["rel"])
            if isinstance(answer, dict) and answer.get("video"):
                if answer["video"] == "ignore":
                    continue
                target = next((v for v in self.videos if v["rel"] == answer["video"]), None)
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
        self.by_item, self.subs_for, self.questions = by_item, subs_for, questions

    @staticmethod
    def _title_key(v):
        season = v["name_season"] or (v["season"] if v["season"] and v["season"] > 1 else None)
        return f"{v['guess']}|{season or ''}"

    # ---- reading

    def state(self):
        with self._lock:
            self._match_if_needed()
            items = {}
            for item_id, vids in self.by_item.items():
                eps = sorted({f"{v['season'] or 1}:{e}" for v in vids for e in v["episodes"]})
                items[item_id] = {"files": len(vids), "episodes": eps}
            unknown = sum(len(q["files"]) for q in self.questions if q.get("reason") == "unknown")
            return {"folder": self.folder, "error": self.error, "items": items,
                    "questions": self.questions, "unknown_files": unknown,
                    "videos": len(self.videos), "scanned_at": self._walked_at}

    def videos_for(self, item_id):
        with self._lock:
            return [dict(v) for v in self.by_item.get(item_id, [])]

    def subtitles_for(self, video_path):
        with self._lock:
            return [dict(s) for s in self.subs_for.get(video_path, [])]

    def contains(self, path):
        with self._lock:
            return any(v["path"] == path for v in self.videos)

    # ---- answers

    def answer(self, body):
        """Remember what you told us about a file (or a group of files)."""
        kind = body.get("kind")
        key = str(body.get("key") or "")
        target = str(body.get("item") or "")
        with self._lock:
            if kind == "title":
                if target != "ignore" and target not in {i["id"] for i in self.app.store.list()}:
                    raise ValueError("pick a title from your list")
                self.answers["titles"][key] = target
            elif kind in ("file", "episode"):
                entry = {"item": target}
                if body.get("episode"):
                    entry["season"] = max(1, int(body.get("season") or 1))
                    entry["episode"] = max(1, int(body["episode"]))
                self.answers["files"][key] = entry
            elif kind == "subtitle":
                self.answers["files"][key] = {"video": str(body.get("video") or "ignore")}
            else:
                raise ValueError("unknown kind of answer")
            write_json(self.answers_path, self.answers)
            self._matched_for = None
            self._match_if_needed()


def watch(app, stop_event=None):
    """Keep the downloads folder up to date in the background."""
    while not (stop_event and stop_event.is_set()):
        try:
            app.library.scan()
        except Exception:  # noqa: BLE001 - never let the background thread die
            pass
        time.sleep(10)
