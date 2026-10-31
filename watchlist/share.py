"""Sharing a collection with someone else who has Watch Anything.

A shared collection is its name and, for each title, where its info comes
from (TMDB or MyAnimeList), with the title, year and poster so it can be
looked at before anything is added. It travels as a share code you can paste
in a chat ("WLC1." then compressed text) or as a small file holding the same.
Nothing personal goes in it: no files, folders, progress or keys.
"""

import base64
import json
import re
import zlib

PREFIX = "WLC1."
EXTENSION = ".wlcollection"
MAX_TITLES = 500


class ShareError(ValueError):
    pass


def pack(name, items, by=""):
    """The shareable data for a collection and its titles."""
    rows = [{"t": i["title"], "y": i.get("year"), "ty": i["type"], "s": i["source"], "i": str(i["source_id"]),
             "p": i.get("poster") or "", "r": i.get("rating")}
            for i in items if i.get("source") in ("tmdb", "mal") and i.get("source_id")][:MAX_TITLES]
    return {"v": 1, "name": str(name)[:80], "by": str(by or "")[:40], "items": rows}


# The short form (WLC2): each title as [type, where from + id, name, year, poster] with the
# poster's address shortened to what's particular to it.
SHORT = "WLC2."
TYPES = {"tv": "t", "anime": "a", "movie": "m"}
POSTERS = ["https://image.tmdb.org/t/p/", "https://cdn.myanimelist.net/images/anime/", "https://"]


def _poster_short(url):
    for n, prefix in enumerate(POSTERS):
        if url.startswith(prefix):
            return f"{n}{url[len(prefix):]}"
    return ""


def _poster_long(text):
    if not text or not text[0].isdigit() or int(text[0]) >= len(POSTERS):
        return ""
    return POSTERS[int(text[0])] + text[1:]


def to_code(data):
    rows = [[TYPES[r["ty"]], ("T" if r["s"] == "tmdb" else "M") + r["i"], r["t"], r.get("y") or 0, _poster_short(r.get("p") or "")]
            for r in data["items"]]
    packed = [data["name"], data.get("by") or "", rows]
    raw = zlib.compress(json.dumps(packed, ensure_ascii=False, separators=(",", ":")).encode(), 9)
    return SHORT + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _from_short(body):
    packed = json.loads(zlib.decompress(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)), bufsize=1 << 20))
    kinds = {v: k for k, v in TYPES.items()}
    name, by, rows = packed
    return {"name": name, "by": by,
            "items": [{"t": r[2], "y": r[3] or None, "ty": kinds.get(r[0], ""), "s": "tmdb" if r[1][:1] == "T" else "mal",
                       "i": r[1][1:], "p": _poster_long(r[4])} for r in rows if isinstance(r, list) and len(r) >= 5]}


def to_file_text(data):
    """A file you can open in any text editor; the code inside is what's read."""
    return json.dumps({"watchlist_collection": to_code(data), "name": data["name"],
                       "titles": [r["t"] for r in data["items"]]}, ensure_ascii=False, indent=1)


def read(text):
    """A share code, or the contents of a shared-collection file -> the data."""
    text = (text or "").strip()
    if text.startswith("{"):
        try:
            text = json.loads(text).get("watchlist_collection", "")
        except (ValueError, AttributeError) as e:
            raise ShareError("that file isn't a shared Watch Anything collection") from e
    short = re.search(re.escape(SHORT) + r"([A-Za-z0-9_-]+)", text)
    m = re.search(re.escape(PREFIX) + r"([A-Za-z0-9_-]+)", text)
    if not (short or m):
        raise ShareError("that isn't a shared collection - it should start with " + SHORT)
    try:
        if short:
            data = _from_short(short.group(1))
        else:
            body = m.group(1)
            data = json.loads(zlib.decompress(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)), bufsize=1 << 20))
    except (ValueError, zlib.error, TypeError, IndexError) as e:
        raise ShareError("that share code is incomplete - copy the whole thing") from e
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ShareError("that share code is damaged")
    items = []
    for r in data["items"][:MAX_TITLES]:
        if not isinstance(r, dict) or r.get("s") not in ("tmdb", "mal") or r.get("ty") not in ("tv", "anime", "movie"):
            continue
        poster = str(r.get("p") or "")
        items.append({"title": str(r.get("t") or "")[:200], "year": r.get("y") if isinstance(r.get("y"), int) else None,
                      "type": r["ty"], "source": r["s"], "source_id": str(r.get("i") or "")[:40],
                      "poster": poster if poster.startswith("https://") else "",
                      "rating": r.get("r") if isinstance(r.get("r"), (int, float)) else None})
    return {"name": str(data.get("name") or "Shared collection")[:80], "by": str(data.get("by") or "")[:40],
            "items": [i for i in items if i["source_id"]]}


def file_name(name):
    safe = re.sub(r'[\\/:*?"<>|]+', " ", name).strip() or "collection"
    return f"Watch Anything - {safe[:60]}{EXTENSION}"
