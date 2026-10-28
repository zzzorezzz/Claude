"""Sharing a collection with someone else who has Watchlist.

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


def to_code(data):
    raw = zlib.compress(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode(), 9)
    return PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")


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
            raise ShareError("that file isn't a shared Watchlist collection") from e
    m = re.search(re.escape(PREFIX) + r"([A-Za-z0-9_-]+)", text)
    if not m:
        raise ShareError("that isn't a shared collection - it should start with " + PREFIX)
    body = m.group(1)
    try:
        data = json.loads(zlib.decompress(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)), bufsize=1 << 20))
    except (ValueError, zlib.error) as e:
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
    return f"Watchlist - {safe[:60]}{EXTENSION}"
