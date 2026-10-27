"""Optional help from Google Gemini, with a free API key from aistudio.google.com.

Without a key the app's own rules and searches do everything; with one, Gemini
gets a say on the downloads those couldn't place, and finds TV spin-offs.
Only names are sent (file names, folder names and the titles in your list),
never the files themselves.
"""

import json
import re
import threading
import time
import urllib.error
import urllib.request

from .metadata import ssl_context

API = "https://generativelanguage.googleapis.com/v1beta"
TIMEOUT = 60
MODEL_TTL = 24 * 3600

# Flash models that aren't for plain text answers.
_NOT_TEXT = re.compile(r"lite|image|tts|audio|live|thinking|embed|learnlm|gemma|robotics|computer", re.I)
_VERSION = re.compile(r"gemini-(\d+(?:\.\d+)?)-flash")

_models = {}            # key -> (model names best first, when)
_lock = threading.Lock()


class AIError(Exception):
    pass


def _request(key, path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"{API}{path}", data=data, method="POST" if data else "GET",
                                 headers={"x-goog-api-key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ssl_context()) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8")).get("error", {}).get("message", "")
        except (ValueError, OSError):
            detail = ""
        if e.code in (400, 401, 403) and ("key" in detail.lower() or e.code != 400):
            raise AIError("Gemini rejected your key - check it in Settings") from e
        if e.code == 404:
            raise AIError("that Gemini model isn't available") from e
        if e.code == 429:
            raise AIError("Gemini's free limit is used up for now - it will try again later") from e
        raise AIError(f"Gemini returned an error (HTTP {e.code}) {detail[:120]}".strip()) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise AIError(f"couldn't reach Gemini ({getattr(e, 'reason', e)}) - are you online?") from e
    except ValueError as e:
        raise AIError("Gemini sent an unreadable reply") from e


def rank_models(names):
    """The usable "flash" models, newest first; a stable model before a preview of
    the same version."""
    found = []
    for name in names:
        short = name.removeprefix("models/")
        if "flash" not in short or _NOT_TEXT.search(short):
            continue
        m = _VERSION.search(short)
        version = float(m.group(1)) if m else 0.0
        preview = bool(re.search(r"preview|exp", short))
        found.append((-version, preview, short != f"gemini-{m.group(1)}-flash" if m else True, short))
    return [f[-1] for f in sorted(found)]


class Gemini:
    def __init__(self, key):
        self.key = (key or "").strip()
        if not self.key:
            raise AIError("no Gemini key")

    def models(self):
        with _lock:
            cached = _models.get(self.key)
            if cached and time.time() - cached[1] < MODEL_TTL:
                return cached[0]
        data = _request(self.key, "/models?pageSize=200")
        names = [m["name"] for m in data.get("models", [])
                 if "generateContent" in (m.get("supportedGenerationMethods") or [])]
        ranked = rank_models(names)
        if not ranked:
            raise AIError("no Gemini Flash model is available to your key")
        with _lock:
            _models[self.key] = (ranked, time.time())
        return ranked

    def model(self):
        return self.models()[0]

    def ask(self, prompt):
        """Gemini's answer to `prompt`, as JSON. Tries the next model if one is
        unavailable or over its free limit."""
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1}}
        last = None
        for name in self.models()[:3]:
            try:
                data = _request(self.key, f"/models/{name}:generateContent", body)
            except AIError as e:
                last = e
                if "isn't available" in str(e) or "free limit" in str(e):
                    continue
                raise
            try:
                text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
            except (KeyError, IndexError, TypeError):
                raise AIError("Gemini didn't answer (it may have refused)") from None
            return parse_json(text)
        with _lock:
            _models.pop(self.key, None)
        raise last or AIError("Gemini didn't answer")


def parse_json(text):
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    try:
        return json.loads(text)
    except ValueError:
        raise AIError("Gemini sent an unreadable reply") from None


def test_key(key):
    """{"ok": True, "model": ...} or {"ok": False, "error": ...}."""
    try:
        g = Gemini(key)
        reply = g.ask('Reply with exactly this JSON: {"ok": true}')
        if not isinstance(reply, dict):
            raise AIError("Gemini sent an unexpected reply")
        return {"ok": True, "model": g.model()}
    except AIError as e:
        return {"ok": False, "error": str(e)}


def _short(items, limit=500):
    return [{"id": i["id"], "type": i["type"], "title": i["title"],
             **({"original": i["original_title"]} if i.get("original_title") else {}),
             "year": i.get("year"), "kind": (i.get("media_type") or i.get("format") or "").lower()}
            for i in items[:limit]]


def sort_downloads(key, items, questions):
    """Gemini's view on downloads the app couldn't place.

    questions: [{"key", "files", "guess", "season", "file_kind", "found"}]
    Returns {key: {"decision", "item_id", "add", "confidence", "why"}}."""
    prompt = (
        "You sort a person's downloaded video files into their watchlist of TV shows, anime "
        "and films. For each question below, decide what the files are.\n"
        "- If they belong to a title already in the list, answer decision \"in_list\" with its id. "
        "Seasons, OVAs, specials and films of an anime are usually separate entries on "
        "MyAnimeList, so only pick a list entry that is that exact season or OVA/film.\n"
        "- If they're a real show/anime/film that isn't in the list, answer decision \"add\" with "
        "add: {type: anime|tv|movie, title: the official title as on MyAnimeList (anime) or TMDB "
        "(tv/movie), year}. Use the 'found' search results where they fit.\n"
        "- If they're not a show or film (home video, game clip...), answer \"not_a_show\".\n"
        "- Otherwise \"unsure\".\n"
        "confidence is 0 to 1: how sure you are. why is a few words for the person.\n"
        'Reply as JSON: {"answers": [{"key": ..., "decision": ..., "item_id": ..., "add": ..., '
        '"confidence": ..., "why": ...}]}\n\n'
        f"The watchlist:\n{json.dumps(_short(items), ensure_ascii=False)}\n\n"
        f"Questions:\n{json.dumps(questions, ensure_ascii=False)}")
    reply = Gemini(key).ask(prompt)
    rows = reply.get("answers") if isinstance(reply, dict) else reply
    out = {}
    for row in rows or []:
        if isinstance(row, dict) and row.get("key"):
            try:
                row["confidence"] = float(row.get("confidence") or 0)
            except (TypeError, ValueError):
                row["confidence"] = 0.0
            out[str(row["key"])] = row
    return out


def related_titles(key, item):
    """Spin-offs, sequels, prequels and films connected to a TV show or film,
    as Gemini knows them: [{"title", "year", "type": tv|movie, "relation"}]."""
    what = "film" if item.get("format") == "movie" else "TV series"
    prompt = (
        f"List the officially related titles of the {what} \"{item['title']}\" "
        f"({item.get('year') or 'year unknown'}): sequels, prequels, spin-offs, films, TV films, "
        "specials and remakes that share its story or characters. Only real, released or "
        "announced titles; no fan works, no unrelated titles with similar names.\n"
        'Reply as JSON: {"related": [{"title": official English title as on TMDB, "year": first '
        'release year, "type": "tv" or "movie", "relation": e.g. "Spin-off", "Prequel", "Sequel", '
        '"Film", "Remake"}]}')
    reply = Gemini(key).ask(prompt)
    rows = reply.get("related") if isinstance(reply, dict) else reply
    out = []
    for row in rows or []:
        if isinstance(row, dict) and row.get("title") and row.get("type") in ("tv", "movie"):
            out.append({"title": str(row["title"]), "year": row.get("year"),
                        "type": row["type"], "relation": str(row.get("relation") or "Related")})
    return out[:20]


def recap(key, item, season, episode):
    """A short spoiler-safe "previously on" for where you're up to."""
    prompt = (
        f"Write a short spoiler-safe recap of \"{item['title']}\" ({item.get('year') or ''}, "
        f"{item['type']}) covering everything up to but NOT including season {season} episode "
        f"{episode}. Nothing that happens from that episode on. 4 to 7 short bullet points. "
        'If you don\'t know the show well, say so instead of guessing. '
        'Reply as JSON: {"known": true or false, "points": ["..."]}')
    reply = Gemini(key).ask(prompt)
    if not isinstance(reply, dict):
        raise AIError("Gemini sent an unexpected reply")
    return {"known": bool(reply.get("known")), "points": [str(p) for p in reply.get("points") or []][:8]}
