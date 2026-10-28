"""Scan a title's folder for episodes and subtitle files."""

import os
import re
from pathlib import Path

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".m4v", ".webm", ".flv", ".mpg",
              ".mpeg", ".ts", ".m2ts", ".ogv", ".3gp", ".divx", ".rmvb"}
SUB_EXTS = {".srt", ".ass", ".ssa", ".vtt", ".sub"}
SKIP_DIRS = {"$recycle.bin", "system volume information", "@eadir", ".trash",
             "watchlist torrents"}  # streamed episodes are the torrent's, not your downloads
MAX_FILES = 20000

_SXE = re.compile(r"\bs(\d{1,2})[ ._-]*((?:e\d{1,4}(?:[ ._-]*)?)+)\b", re.I)
_EPS = re.compile(r"e(\d{1,4})", re.I)
_NXN = re.compile(r"\b(\d{1,2})x(\d{1,3})\b", re.I)
_SEASON_EP_WORDS = re.compile(r"\bseason[ ._-]*(\d{1,2})[ ._-]*(?:episode|ep)[ ._-]*(\d{1,4})\b", re.I)
_ABS = [
    re.compile(r"\b(?:episode|ep|e)[ ._-]*(\d{1,4})(?:v\d)?\b", re.I),
    re.compile(r"\s-\s(\d{1,4})(?:v\d)?\b"),
    re.compile(r"第(\d{1,4})[話话]"),
]
_LEADING = re.compile(r"^\s*(\d{1,4})(?:v\d)?(?:\s|$|[-.])")
_SEASON_DIR = re.compile(r"^(?:season|series|s)[ ._-]*(\d{1,2})\b", re.I)
# A season said anywhere in a folder's or file's name: "Haikyuu!! (Season 2)",
# "Haikyuu!! S04", "Haikyuu!! 4th Season", "Haikyuu!! Second Season".
_SEASON_ANY = [
    re.compile(r"\bseason[ ._-]*(\d{1,2})\b", re.I),
    re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)[ ._-]*season\b", re.I),
    re.compile(r"\bs(\d{1,2})\b(?![ ._-]*e\d)", re.I),
]
_SEASON_WORDS = {"second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8}
_SEASON_WORD = re.compile(r"\b(" + "|".join(_SEASON_WORDS) + r")[ ._-]*season\b", re.I)
# A folder holding several seasons ("S01+02+03+04", "Season 1-4", "Complete"): it says no one season.
_MANY_SEASONS = re.compile(r"\bs\d{1,2}[ ]*[-+~&,][ ]*s?\d{1,2}\b|\bseasons?[ ]*\d{1,2}[ ]*[-+~&,][ ]*\d{1,2}\b"
                           r"|\bcomplete\b|\bbatch\b|\bcollection\b|\ball seasons\b", re.I)
# "Haikyuu!! S2 - 05", "Season 2 - 05": a season, then the episode after a dash.
_SEASON_DASH = re.compile(r"\b(?:s|season[ ._-]*)(\d{1,2})[ ._]*-[ ._]*(\d{1,4})(?:v\d)?\b", re.I)
_YEAR = re.compile(r"^(19|20)\d{2}$")
_LANG_EN = re.compile(r"(?:^|[ ._\-\[(])(en|eng|english)(?:$|[ ._\-\])])", re.I)


def _clean(stem: str) -> str:
    s = re.sub(r"\[[^\]]*\]", " ", stem)          # [Group], [ABCD1234]
    s = re.sub(r"[()]", " ", s)
    s = re.sub(r"\b(\d{3,4}p|[xh][ .]?26[45]|hevc|avc|10bit|8bit)\b", " ", s, flags=re.I)
    s = s.replace("_", " ").replace(".", " ")
    return re.sub(r"\s+", " ", s).strip()


def season_of(name):
    """The season a folder's (or file's) name says, or None. A name listing several
    seasons ("S01+02+03+04", "Season 1-4", "Complete") says none."""
    text = _clean(name)
    if _MANY_SEASONS.search(name) or _MANY_SEASONS.search(text):
        return None
    m = _SEASON_DIR.match(text)
    if m:
        return int(m.group(1))
    for pattern in _SEASON_ANY:
        m = pattern.search(text)
        if m:
            return int(m.group(1))
    m = _SEASON_WORD.search(text)
    if m:
        return _SEASON_WORDS[m.group(1).lower()]
    return None


def parse_episode(rel_parts):
    """(season or None, tuple of episode numbers) from a path relative to the folder."""
    stem = _clean(Path(rel_parts[-1]).stem)
    dir_season = None
    for part in reversed(rel_parts[:-1]):
        if _MANY_SEASONS.search(part):
            break                       # the pack's own folder: nothing above says more
        dir_season = season_of(part)
        if dir_season is not None:
            break

    m = _SXE.search(stem)
    if m:
        eps = tuple(int(e) for e in _EPS.findall(m.group(2)))
        return int(m.group(1)), eps
    for pattern in (_SEASON_EP_WORDS, _NXN, _SEASON_DASH):
        m = pattern.search(stem)
        if m:
            return int(m.group(1)), (int(m.group(2)),)
    # "Haikyuu!! Second Season - 05" / "4th Season - 05": the name's season, then the episode.
    name_season = season_of(re.split(r"\s-\s", stem)[0]) if re.search(r"\s-\s", stem) else None
    if name_season is not None:
        dir_season = name_season
    for pattern in _ABS:
        m = pattern.search(stem)
        if m and not _YEAR.match(m.group(1)):
            return dir_season, (int(m.group(1)),)
    m = _LEADING.match(stem)
    if m and not _YEAR.match(m.group(1)):
        return dir_season, (int(m.group(1)),)
    return dir_season, ()


def season_in_name(name):
    """Whether a file's own name says its season ("S02E05", "2x05", "Season 2 Episode 5")."""
    stem = _clean(Path(name).stem)
    return any(p.search(stem) for p in (_SXE, _SEASON_EP_WORDS, _NXN))


def natural_key(text):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", text)]


def scan_folder(folder):
    """All videos and subtitle files under a folder (recursively)."""
    root = Path(folder)
    if not root.is_dir():
        raise FileNotFoundError(f"folder not found: {folder}")
    videos, subs = [], []
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(".") and d.lower() not in SKIP_DIRS]
        for name in filenames:
            count += 1
            if count > MAX_FILES:
                break
            path = Path(dirpath) / name
            ext = path.suffix.lower()
            if ext not in VIDEO_EXTS and ext not in SUB_EXTS:
                continue
            rel = path.relative_to(root).parts
            if ext in SUB_EXTS:
                subs.append({"path": str(path), "name": name, "rel": "/".join(rel)})
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if re.search(r"\bsample\b", name, re.I) and size < 300 * 1024 * 1024:
                continue
            season, episodes = parse_episode(rel)
            videos.append({"path": str(path), "name": name, "rel": "/".join(rel),
                           "size": size, "season": season, "episodes": list(episodes)})
    videos.sort(key=lambda v: (v["season"] or 0, v["episodes"][:1] or [0], natural_key(v["rel"])))
    subs.sort(key=lambda s: natural_key(s["rel"]))
    return {"videos": videos, "subs": subs}


def _from_absolute(counts, number):
    """Absolute episode number -> (season, episode) using the season sizes."""
    remaining = number
    for season in sorted(n for n in counts if n > 0):  # season 0 holds specials
        size = counts[season]
        if not size or remaining <= size:
            return season, remaining
        remaining -= size
    return 1, number


def episode_map(item, videos):
    """{(season, episode): video} for every episode found in a folder."""
    counts = {s["number"]: s["episodes"] for s in item.get("seasons", []) if s["number"] > 0}
    # Extras (openings, endings...) are never episodes.
    videos = [v for v in videos if v.get("kind") != "extra"]
    numbered = [v for v in videos if v["episodes"]]
    # A one-season entry (most MyAnimeList anime): its files all go in season 1,
    # whatever season their names or folders say ("S02E05", or "Season 12/250"
    # for Naruto Shippuden), unless the same episode number turns up twice.
    regular = [v for v in numbered if v["season"] != 0]
    numbers = [ep for v in regular for ep in v["episodes"]]
    single = len(counts) <= 1 and (len({v["season"] for v in regular}) == 1 or len(numbers) == len(set(numbers)))
    found = {}
    for v in numbered:
        if v["season"] is not None:
            for ep in v["episodes"]:
                if v["season"] == 0:
                    key = (0, ep)
                elif single:
                    key = (1, ep)
                elif counts.get(v["season"]) and ep > counts[v["season"]]:
                    key = _from_absolute(counts, ep)      # "Season 12/250": counted from the very start
                else:
                    key = (v["season"], ep)
                found.setdefault(key, v)
    for v in numbered:
        if v["season"] is None:
            for ep in v["episodes"]:
                folder = v.get("folder_season")
                if single:
                    key = (1, ep)
                elif folder and counts.get(folder) and ep <= counts[folder]:
                    key = (folder, ep)            # a long season, numbered within it
                else:
                    key = _from_absolute(counts, ep)
                found.setdefault(key, v)
    if not numbered:
        # No episode numbers at all: go by the files' order.
        for i, v in enumerate(sorted(videos, key=lambda v: natural_key(v["rel"]))):
            found[(1, i + 1)] = v
    return found


def find_episode(item, videos, season, episode):
    """The video file for an episode, or None."""
    return episode_map(item, videos).get((season, episode))


def episode_queue(videos, first, limit=50):
    """`first` followed by the episodes after it, for VLC's playlist."""
    try:
        start = videos.index(first)
    except ValueError:
        return [first]
    return videos[start:start + limit]


def find_movie(item, videos):
    if not videos:
        return None
    words = set(re.sub(r"[^a-z0-9]+", " ", item.get("title", "").lower()).split())

    def score(v):
        name = set(re.sub(r"[^a-z0-9]+", " ", v["name"].lower()).split())
        return (len(words & name), v["size"])

    return max(videos, key=score)


def subtitles_for(video, subs, videos):
    """Subtitle files that belong to a video, best match first."""
    vpath = Path(video["path"])
    vstem = vpath.stem.lower()
    vdir = vpath.parent
    alone = sum(1 for v in videos if Path(v["path"]).parent == vdir) == 1
    found = []
    for s in subs:
        spath = Path(s["path"])
        sstem = spath.stem.lower()
        score = 0
        if spath.parent == vdir and sstem == vstem:
            score = 100
        elif spath.parent == vdir and sstem.startswith(vstem + "."):
            score = 90
        elif vstem in [p.lower() for p in spath.parent.parts[-2:]]:
            score = 80                      # Subs/<video name>/English.srt
        elif video["episodes"]:
            season, eps = parse_episode(Path(s["rel"]).parts)
            if eps and eps[0] == video["episodes"][0] and season in (None, video["season"]):
                score = 70
        if not score and alone and (spath.parent == vdir or spath.parent.parent == vdir):
            score = 50                      # the only video here, subs beside it
        if not score:
            continue
        english = bool(_LANG_EN.search(spath.stem)) or sstem in ("english", "en", "eng")
        found.append({"path": s["path"], "name": s["name"], "rel": s["rel"],
                      "english": english, "score": score + (5 if english else 0)})
    found.sort(key=lambda s: (-s["score"], natural_key(s["name"])))
    return found
