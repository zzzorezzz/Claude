"""Optional AI helpers: Google Gemini (free key from aistudio.google.com),
ChatGPT (OpenAI API key) and Grok (xAI API key).

Without any key the app's own rules and searches do everything. With keys,
the first helper that answers is used: Gemini, then ChatGPT, then Grok. They
get a say on downloads the app couldn't place, find TV spin-offs and write
recaps. Only names are sent (file names, folder names and the titles in your
list), never the files themselves.
"""

import json
import re
import threading
import time

from . import net

PROVIDERS = ("gemini", "openai", "xai")
NAMES = {"gemini": "Gemini", "openai": "ChatGPT", "xai": "Grok"}
KEY_SETTINGS = {"gemini": "gemini_api_key", "openai": "openai_api_key", "xai": "xai_api_key"}
MODEL_TTL = 24 * 3600
BAD_MODEL_FOR = 3600      # a model over its limit is skipped for an hour

_lock = threading.Lock()
_models = {}              # (provider, key) -> (ranked model names, when)
_good = {}                # (provider, key) -> the model that last answered
_bad = {}                 # (provider, key, model) -> skip until


class AIError(Exception):
    """kind: key, credit, region, limit, busy, timeout, model, offline, refused, other.
    The kinds that only concern one model (limit, busy, timeout, model) move on to the next."""

    def __init__(self, message, kind="other"):
        super().__init__(message)
        self.kind = kind


NEXT_MODEL = {"limit", "busy", "timeout", "model"}


def available(settings):
    """The helpers that have a key, in the order they're tried."""
    return [p for p in PROVIDERS if (settings.get(KEY_SETTINGS[p]) or "").strip()]


def parse_json(text):
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    try:
        return json.loads(text)
    except ValueError:
        m = re.search(r"[\[{].*[\]}]", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except ValueError:
                pass
        raise AIError("the answer wasn't readable", "other") from None


def _version(name, pattern):
    m = re.search(pattern, name)
    return float(m.group(1)) if m else 0.0


# ------------------------------------------------------------- providers

class _Provider:
    id = ""
    host = ""

    def __init__(self, key):
        self.key = (key or "").strip()
        if not self.key:
            raise AIError(f"no {NAMES[self.id]} key", "key")
        self.name = NAMES[self.id]

    # Each provider: _list() -> model names, rank(names), _call(model, prompt, timeout) -> text,
    # and _error(NetError) -> AIError.

    def models(self):
        cache_key = (self.id, self.key)
        with _lock:
            cached = _models.get(cache_key)
            if cached and time.time() - cached[1] < MODEL_TTL:
                return cached[0]
        try:
            names = self._list()
        except net.NetError as e:
            raise self._error(e) from e
        ranked = self.rank(names)
        if not ranked:
            raise AIError(f"{self.name} didn't offer any model this app can use", "model")
        with _lock:
            _models[cache_key] = (ranked, time.time())
        return ranked

    def ask(self, prompt, timeout=60, budget=None, log=None):
        """The answer to `prompt` as JSON. Models that are over their limit, busy
        or slow are skipped for the next one, within `budget` seconds."""
        started = time.monotonic()
        budget = budget or timeout * 3
        ranked = self.models()
        with _lock:
            good = _good.get((self.id, self.key))
            skip = {m for (p, k, m), until in _bad.items() if p == self.id and k == self.key and until > time.time()}
        order = ([good] if good in ranked else []) + [m for m in ranked if m != good and m not in skip]
        if not order:
            order = ranked[:1]
        last = None
        for model in order[:5]:
            left = budget - (time.monotonic() - started)
            if left < 5:
                break
            try:
                text = self._call(model, prompt, min(timeout, left))
                reply = parse_json(text)
            except net.NetError as e:
                err = self._error(e, model)
            except AIError as e:
                err = e
            else:
                with _lock:
                    _good[(self.id, self.key)] = model
                if log is not None:
                    log.append(f"{model}: answered")
                return reply, model
            if log is not None:
                log.append(f"{model}: {err}")
            last = err
            if err.kind == "limit":
                with _lock:
                    _bad[(self.id, self.key, model)] = time.time() + BAD_MODEL_FOR
            if err.kind not in NEXT_MODEL:
                raise err
        raise last or AIError(f"{self.name} didn't answer in time", "timeout")


class Gemini(_Provider):
    id = "gemini"
    API = "https://generativelanguage.googleapis.com/v1beta"
    _SKIP = re.compile(r"image|tts|audio|live|embed|learnlm|gemma|robotics|computer|omni|transcribe|"
                       r"argon|nano|lyria|customtools|exp|thinking|research|antigravity", re.I)

    def _list(self):
        names, token = [], ""
        for _ in range(5):
            data = net.request_json(f"{self.API}/models?pageSize=200" + (f"&pageToken={token}" if token else ""),
                                    "Gemini", {"x-goog-api-key": self.key}, timeout=20)
            names += [m["name"] for m in data.get("models", [])
                      if "generateContent" in (m.get("supportedGenerationMethods") or [])]
            token = data.get("nextPageToken")
            if not token:
                break
        return names

    @classmethod
    def rank(cls, names):
        """Google's own "flash-latest" first, then stable Flash models newest
        first, then previews, then the Lite ones."""
        found = []
        for name in names:
            short = name.removeprefix("models/")
            if "flash" not in short or cls._SKIP.search(short):
                continue
            lite = "lite" in short
            preview = "preview" in short
            latest = short.endswith("-latest")
            version = _version(short, r"gemini-(\d+(?:\.\d+)?)-flash")
            if not latest and not version:
                continue
            group = (2 if lite else 0) + (1 if preview else 0)
            found.append((group, not latest, -version, len(short), short))
        return [f[-1] for f in sorted(found)]

    def _call(self, model, prompt, timeout):
        config = {"responseMimeType": "application/json"}
        version = _version(model, r"gemini-(\d+(?:\.\d+)?)")
        if version and version < 3:
            config.update(temperature=0.1, thinkingConfig={"thinkingBudget": 0})
        else:
            config["thinkingConfig"] = {"thinkingLevel": "low"}   # quick answers for small jobs
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": config}
        url = f"{self.API}/models/{model}:generateContent"
        headers = {"x-goog-api-key": self.key}
        try:
            data = net.request_json(url, "Gemini", headers, body, timeout=timeout)
        except net.NetError as e:
            if e.status == 400 and "think" in e.detail.lower():
                config.pop("thinkingConfig", None)
                data = net.request_json(url, "Gemini", headers, body, timeout=timeout)
            else:
                raise
        try:
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts if not p.get("thought"))
        except (KeyError, IndexError, TypeError):
            reason = ((data.get("promptFeedback") or {}).get("blockReason")
                      or ((data.get("candidates") or [{}])[0].get("finishReason")) or "no answer")
            raise AIError(f"Gemini didn't answer ({reason})", "refused") from None

    def _error(self, e, model=""):
        d = (e.detail or "").lower()
        where = f" ({model})" if model else ""
        if e.status is None:
            return AIError(str(e), "timeout" if e.kind == "timeout" else "offline")
        if "location" in d and "not supported" in d:
            return AIError("Google doesn't offer the Gemini API in your country", "region")
        if e.status in (401, 403) and "model" not in d or (e.status == 400 and "api key" in d):
            return AIError("Gemini rejected your key - check it in Settings", "key")
        if e.status == 429:
            return AIError(f"the free limit is used up for now{where}", "limit")
        if e.status in (404, 403) or (e.status == 400 and "model" in d):
            return AIError(f"that model isn't available to your key{where}", "model")
        if e.status >= 500:
            return AIError(f"Gemini is busy right now{where}", "busy")
        return AIError(f"Gemini returned an error (HTTP {e.status}) {e.detail[:120]}".strip(), "other")


class _OpenAIStyle(_Provider):
    """OpenAI's chat API, which xAI's Grok API copies."""
    API = ""
    billing = ""

    def _list(self):
        data = net.request_json(f"{self.API}/models", self.name, {"Authorization": f"Bearer {self.key}"}, timeout=20)
        return [m.get("id", "") for m in data.get("data", [])]

    def _extra(self, model):
        return {}

    def _call(self, model, prompt, timeout):
        body = {"model": model, "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"}, **self._extra(model)}
        url = f"{self.API}/chat/completions"
        headers = {"Authorization": f"Bearer {self.key}"}
        for _ in range(3):
            try:
                data = net.request_json(url, self.name, headers, body, timeout=timeout)
                break
            except net.NetError as e:
                d = (e.detail or "").lower()
                # Some models don't take every option: drop it and ask again.
                dropped = next((k for k in ("reasoning_effort", "response_format", "temperature")
                                if k in body and k in d), None)
                if e.status == 400 and dropped:
                    body.pop(dropped)
                    continue
                raise
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise AIError(f"{self.name} didn't answer", "refused") from None

    def _error(self, e, model=""):
        d = (e.detail or "").lower()
        where = f" ({model})" if model else ""
        if e.status is None:
            return AIError(str(e), "timeout" if e.kind == "timeout" else "offline")
        if "credit" in d or "insufficient_quota" in d or "billing" in d or "exceeded your current quota" in d:
            return AIError(f"your {self.name} account has no API credit - add some at {self.billing} "
                           f"({self.name}'s free plan only covers its own app)", "credit")
        if "country" in d or "region" in d or "territory" in d:
            return AIError(f"{self.name}'s API isn't offered in your country", "region")
        if e.status in (401,) or (e.status in (400, 403) and ("api key" in d or "incorrect" in d)):
            return AIError(f"{self.name} rejected your key - check it in Settings", "key")
        if e.status == 429:
            return AIError(f"{self.name}'s rate limit was reached{where}", "limit")
        if e.status in (403, 404) or (e.status == 400 and "model" in d):
            return AIError(f"that model isn't available to your key{where}", "model")
        if e.status >= 500:
            return AIError(f"{self.name} is busy right now{where}", "busy")
        return AIError(f"{self.name} returned an error (HTTP {e.status}) {e.detail[:120]}".strip(), "other")


class OpenAI(_OpenAIStyle):
    id = "openai"
    API = "https://api.openai.com/v1"
    billing = "platform.openai.com"
    _MODEL = re.compile(r"^gpt-(\d+(?:\.\d+)?)(?:-(mini|nano))?$")

    @classmethod
    def rank(cls, names):
        found = []
        for name in names:
            m = cls._MODEL.match(name)
            if m:
                size = {"mini": 0, "nano": 1, None: 2}[m.group(2)]
                found.append((size, -float(m.group(1)), name))
        ranked = [f[-1] for f in sorted(found, key=lambda f: (f[1], f[0]))]
        if "gpt-4o-mini" in names and "gpt-4o-mini" not in ranked:
            ranked.append("gpt-4o-mini")
        return ranked

    def _extra(self, model):
        return {"reasoning_effort": "low"} if _version(model, r"gpt-(\d+(?:\.\d+)?)") >= 5 else {}


class XAI(_OpenAIStyle):
    id = "xai"
    API = "https://api.x.ai/v1"
    billing = "console.x.ai"
    _SKIP = re.compile(r"image|vision|imagine|code|build|tts|voice|audio|embed|video", re.I)

    @classmethod
    def rank(cls, names):
        found = []
        for name in names:
            if not name.startswith("grok-") or cls._SKIP.search(name):
                continue
            version = _version(name, r"^grok-(\d+(?:\.\d+)?)")
            quick = 0 if "non-reasoning" in name else 1 if ("fast" in name or "mini" in name) else 2
            found.append((-version, quick, len(name), name))
        return [f[-1] for f in sorted(found)]


_CLASSES = {"gemini": Gemini, "openai": OpenAI, "xai": XAI}


def helper(provider, key):
    return _CLASSES[provider](key)


def ask(settings, prompt, timeout=60, budget=None):
    """(answer, provider) from the first helper that answers."""
    errors = []
    for provider in available(settings):
        try:
            reply, _model = helper(provider, settings[KEY_SETTINGS[provider]]).ask(prompt, timeout, budget)
            return reply, provider
        except AIError as e:
            errors.append(f"{NAMES[provider]}: {e}")
    if not errors:
        raise AIError("no AI helper is set up", "key")
    raise AIError("; ".join(errors))


def test_key(provider, key):
    """{"ok", "model" or "error", "tried": [...]} - quick enough for a Settings button."""
    log = []
    try:
        h = helper(provider, key)
        reply, model = h.ask('Reply with exactly this JSON: {"ok": true}', timeout=25, budget=50, log=log)
        return {"ok": True, "model": model, "tried": log}
    except AIError as e:
        return {"ok": False, "error": str(e), "kind": e.kind, "tried": log}


# ------------------------------------------------------------- the jobs

def _short(items, limit=500):
    return [{"id": i["id"], "type": i["type"], "title": i["title"],
             **({"original": i["original_title"]} if i.get("original_title") else {}),
             "year": i.get("year"), "kind": (i.get("media_type") or i.get("format") or "").lower()}
            for i in items[:limit]]


def sort_downloads(settings, items, questions):
    """The AI's view on downloads the app couldn't place: ({key: row}, provider).

    questions: [{"key", "files", "guess", "season", "file_kind", "found"}]
    row: {"decision", "item_id", "add", "confidence", "why"}"""
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
    reply, provider = ask(settings, prompt, timeout=90, budget=180)
    rows = reply.get("answers") if isinstance(reply, dict) else reply
    out = {}
    for row in rows or []:
        if isinstance(row, dict) and row.get("key"):
            try:
                row["confidence"] = float(row.get("confidence") or 0)
            except (TypeError, ValueError):
                row["confidence"] = 0.0
            out[str(row["key"])] = row
    return out, provider


def related_titles(settings, item):
    """Spin-offs, sequels, prequels and films connected to a TV show or film:
    [{"title", "year", "type": tv|movie, "relation"}]."""
    what = "film" if item.get("format") == "movie" else "TV series"
    prompt = (
        f"List the officially related titles of the {what} \"{item['title']}\" "
        f"({item.get('year') or 'year unknown'}): sequels, prequels, spin-offs, films, TV films, "
        "specials and remakes that share its story or characters. Only real, released or "
        "announced titles; no fan works, no unrelated titles with similar names.\n"
        'Reply as JSON: {"related": [{"title": official English title as on TMDB, "year": first '
        'release year, "type": "tv" or "movie", "relation": e.g. "Spin-off", "Prequel", "Sequel", '
        '"Film", "Remake"}]}')
    reply, _ = ask(settings, prompt, timeout=45, budget=90)
    rows = reply.get("related") if isinstance(reply, dict) else reply
    out = []
    for row in rows or []:
        if isinstance(row, dict) and row.get("title") and row.get("type") in ("tv", "movie"):
            out.append({"title": str(row["title"]), "year": row.get("year"),
                        "type": row["type"], "relation": str(row.get("relation") or "Related")})
    return out[:20]


def recap(settings, item, season, episode):
    """A short spoiler-safe "previously on" for where you're up to."""
    prompt = (
        f"Write a short spoiler-safe recap of \"{item['title']}\" ({item.get('year') or ''}, "
        f"{item['type']}) covering everything up to but NOT including season {season} episode "
        f"{episode}. Nothing that happens from that episode on. 4 to 7 short bullet points. "
        "If you don't know the show well, say so instead of guessing. "
        'Reply as JSON: {"known": true or false, "points": ["..."]}')
    reply, provider = ask(settings, prompt, timeout=45, budget=90)
    if not isinstance(reply, dict):
        raise AIError("the answer wasn't readable")
    return {"known": bool(reply.get("known")), "points": [str(p) for p in reply.get("points") or []][:8],
            "by": NAMES[provider]}
