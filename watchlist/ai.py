"""Optional AI helpers: three with free keys - Google Gemini (aistudio.google.com),
Groq (console.groq.com) and OpenRouter's free models (openrouter.ai) - and any
of your own: a service with the OpenAI-style API (OpenAI, DeepSeek, Mistral,
xAI, Cerebras, Together, or one on your computer such as Ollama or LM Studio)
or Anthropic's Claude. You choose the order they're asked in.

Without any key the app's own rules and searches do everything. With keys,
they're asked in that order: when one has run out for the day, is overloaded
or can't be reached, the next one answers instead, and the one that failed
is left alone for a while. They get a say on downloads the app couldn't
place, find TV spin-offs and write recaps. Only names are sent (file names,
folder names and the titles in your list), never the files themselves.
"""

import json
import re
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from . import net

PROVIDERS = ("gemini", "groq", "openrouter")
NAMES = {"gemini": "Gemini", "groq": "Groq", "openrouter": "OpenRouter"}
KEY_SETTINGS = {"gemini": "gemini_api_key", "groq": "groq_api_key", "openrouter": "openrouter_api_key"}
MODEL_TTL = 24 * 3600
BAD_MODEL_FOR = 3600      # a model over its limit is skipped for an hour
BUSY_RETRY_AFTER = 3      # seconds: "high demand" spikes are usually short
# How long a helper is left alone after failing as a whole, by kind of failure.
REST = {"daily": 3 * 3600, "credit": 6 * 3600, "key": 3600, "region": 6 * 3600, "limit": 1800,
        "busy": 300, "timeout": 300, "offline": 300, "model": 1800}

_lock = threading.Lock()
_models = {}              # (provider, key) -> (ranked model names, when)
_good = {}                # (provider, key) -> the model that last answered
_bad = {}                 # (provider, key, model) -> skip until
_resting = {}             # (provider, key) -> (until, why, kind)
SHORT_RESTS = {"busy", "timeout", "offline", "limit", "model"}   # skipped when you press Sort now


class AIError(Exception):
    """kind: key, credit, region, daily, limit, busy, timeout, model, toolarge, offline,
    refused, other. The kinds that only concern one model (limit, busy, timeout,
    model, toolarge) move on to the next model; the rest move on to the next helper."""

    def __init__(self, message, kind="other"):
        super().__init__(message)
        self.kind = kind


NEXT_MODEL = {"limit", "busy", "timeout", "model", "toolarge"}


# Your own helpers: Settings → AI helpers → Add your own. "kind" is the API it speaks.
CUSTOM_KINDS = ("openai", "anthropic")
CUSTOM_PRESETS = [
    {"name": "OpenAI (ChatGPT)", "kind": "openai", "url": "https://api.openai.com/v1", "keys": "platform.openai.com/api-keys"},
    {"name": "Claude (Anthropic)", "kind": "anthropic", "url": "https://api.anthropic.com/v1", "keys": "console.anthropic.com"},
    {"name": "DeepSeek", "kind": "openai", "url": "https://api.deepseek.com/v1", "keys": "platform.deepseek.com"},
    {"name": "Mistral", "kind": "openai", "url": "https://api.mistral.ai/v1", "keys": "console.mistral.ai"},
    {"name": "xAI (Grok)", "kind": "openai", "url": "https://api.x.ai/v1", "keys": "console.x.ai"},
    {"name": "Cerebras", "kind": "openai", "url": "https://api.cerebras.ai/v1", "keys": "cloud.cerebras.ai"},
    {"name": "Together AI", "kind": "openai", "url": "https://api.together.xyz/v1", "keys": "api.together.ai"},
    {"name": "Ollama (on this computer)", "kind": "openai", "url": "http://localhost:11434/v1", "keys": ""},
    {"name": "LM Studio (on this computer)", "kind": "openai", "url": "http://localhost:1234/v1", "keys": ""},
]


def custom_helpers(settings):
    return {f"custom:{c['id']}": c for c in settings.get("ai_custom") or []}


def key_of(settings, provider):
    if provider in KEY_SETTINGS:
        return (settings.get(KEY_SETTINGS[provider]) or "").strip()
    conf = custom_helpers(settings).get(provider)
    if not conf:
        return ""
    # A service on your own computer may need no key: its address stands in for one.
    return (conf.get("key") or "").strip() or (conf["url"] if _is_local(conf["url"]) else "")


def _is_local(url):
    return re.match(r"https?://(localhost|127\.0\.0\.1|\[::1\])[:/]", url or "") is not None


def order(settings):
    """Every helper, in the order they're asked (Settings → AI helpers)."""
    known = list(PROVIDERS) + list(custom_helpers(settings))
    wanted = [p for p in settings.get("ai_order") or [] if p in known]
    return wanted + [p for p in known if p not in wanted]


def available(settings):
    """The helpers that are set up, in the order they're tried."""
    for pid, conf in custom_helpers(settings).items():
        NAMES[pid] = conf["name"]
    return [p for p in order(settings) if key_of(settings, p)]


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
    probe_size = 3            # models tried at once by the Settings test

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

    def _order(self):
        """Models to try: the one that last answered first, then the ranking,
        leaving out ones over their limit for now."""
        ranked = self.models()
        with _lock:
            good = _good.get((self.id, self.key))
            skip = {m for (p, k, m), until in _bad.items() if p == self.id and k == self.key and until > time.time()}
        order = ([good] if good in ranked else []) + [m for m in ranked if m != good and m not in skip]
        return order or ranked[:1]

    def _try(self, model, prompt, timeout):
        """(reply, None) or (None, AIError) from one model."""
        try:
            return parse_json(self._call(model, prompt, timeout)), None
        except net.NetError as e:
            return None, self._error(e, model)
        except AIError as e:
            return None, e

    def _worked(self, model):
        with _lock:
            _good[(self.id, self.key)] = model

    def _failed(self, model, err):
        if err.kind == "limit":
            with _lock:
                _bad[(self.id, self.key, model)] = time.time() + BAD_MODEL_FOR

    def ask(self, prompt, timeout=60, budget=None, log=None):
        """The answer to `prompt` as JSON. Models that are over their limit, busy
        or slow are skipped for the next one, within `budget` seconds; when they
        were all just busy, the first busy one gets one more go."""
        started = time.monotonic()
        budget = budget or timeout * 3
        last, busy = None, []
        for model in self._order()[:5]:
            left = budget - (time.monotonic() - started)
            if left < 5:
                break
            reply, err = self._try(model, prompt, min(timeout, left))
            if not err:
                self._worked(model)
                if log is not None:
                    log.append(f"{model}: answered")
                return reply, model
            if log is not None:
                log.append(f"{model}: {err}")
            self._failed(model, err)
            last = err
            if err.kind == "busy":
                busy.append(model)
            if err.kind not in NEXT_MODEL:
                raise err
        left = budget - (time.monotonic() - started)
        if busy and left > BUSY_RETRY_AFTER + 5:
            time.sleep(BUSY_RETRY_AFTER)
            reply, err = self._try(busy[0], prompt, min(timeout, left - BUSY_RETRY_AFTER))
            if log is not None:
                log.append(f"{busy[0]} (again): {'answered' if not err else err}")
            if not err:
                self._worked(busy[0])
                return reply, busy[0]
            last = err
        raise last or AIError(f"{self.name} didn't answer in time", "timeout")

    def probe(self, prompt, timeout=30, log=None):
        """Ask several models at once and keep the first good answer: under high
        demand some models answer in seconds while others don't at all. The
        winner is remembered and asked first from then on."""
        order = self._order()
        candidates = order[:self.probe_size]
        lite = next((m for m in order if "lite" in m), None)
        if lite and lite not in candidates and len(candidates) > 2:
            candidates[-1:] = [lite]
        pool = ThreadPoolExecutor(len(candidates))
        futures = {pool.submit(self._try, m, prompt, timeout): m for m in candidates}
        errors = []
        try:
            pending = set(futures)
            while pending:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                results = {f: f.result() for f in done}
                winner = next((f for f, (_r, err) in results.items() if not err), None)
                for f, (_reply, err) in results.items():
                    if err:
                        self._failed(futures[f], err)
                        errors.append(err)
                        if log is not None:
                            log.append(f"{futures[f]}: {err}")
                if winner:
                    model = futures[winner]
                    self._worked(model)
                    if log is not None:
                        log.append(f"{model}: answered first")
                        log.extend(f"{futures[p]}: still waiting" for p in pending)
                    return results[winner][0], model
                for f, (_reply, err) in results.items():
                    if err.kind not in NEXT_MODEL:
                        raise err
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        if errors and all(e.kind == "busy" for e in errors):
            raise AIError(f"{self.name} is overloaded right now (usually only for a few minutes) - "
                          "try again soon", "busy")
        kinds = [e.kind for e in errors]
        for kind in ("limit", "busy", "timeout", "model"):
            if kind in kinds:
                raise errors[kinds.index(kind)]
        raise AIError(f"{self.name} didn't answer", "timeout")


class Gemini(_Provider):
    id = "gemini"
    probe_size = 5
    API = "https://generativelanguage.googleapis.com/v1beta"
    _SKIP = re.compile(r"image|tts|audio|live|embed|learnlm|robotics|computer|omni|transcribe|"
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
        first, then previews, then the Lite ones, then the open Gemma models
        (also free on the same key, and usually less busy)."""
        found = []
        for name in names:
            short = name.removeprefix("models/")
            if cls._SKIP.search(short):
                continue
            if short.startswith("gemma-") and short.endswith("-it"):
                found.append((4, 0, -_version(short, r"-(\d+(?:\.\d+)?)b"), len(short), short))
                continue
            if "flash" not in short:
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
        if model.startswith("gemma-"):
            config = {"temperature": 0.2}      # Gemma answers in plain text (JSON is asked for in the prompt)
        else:
            config = {"responseMimeType": "application/json"}
            version = _version(model, r"gemini-(\d+(?:\.\d+)?)")
            if version and version < 3:
                config.update(temperature=0.1, thinkingConfig={"thinkingBudget": 0})
            else:
                config["thinkingConfig"] = {"thinkingLevel": "low"}   # quick answers for small jobs
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": config}
        url = f"{self.API}/models/{model}:generateContent"
        headers = {"x-goog-api-key": self.key}
        for _ in range(3):
            try:
                data = net.request_json(url, "Gemini", headers, body, timeout=timeout)
                break
            except net.NetError as e:
                d = (e.detail or "").lower()
                # A model that doesn't take an option: drop it and ask again.
                if e.status == 400 and "think" in d and "thinkingConfig" in config:
                    config.pop("thinkingConfig")
                elif e.status == 400 and ("json" in d or "mime" in d) and "responseMimeType" in config:
                    config.pop("responseMimeType")
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
    """The OpenAI-style chat API that Groq and OpenRouter both offer."""
    API = ""
    signup = ""

    def _headers(self):
        return {"Authorization": f"Bearer {self.key}"}

    def _list(self):
        data = net.request_json(f"{self.API}/models", self.name, self._headers(), timeout=20)
        return [m.get("id", "") for m in data.get("data", []) if self._usable(m)]

    def _usable(self, m):
        return True

    def _extra(self, model):
        return {}

    def _call(self, model, prompt, timeout):
        body = {"model": model, "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"}, "temperature": 0.2, **self._extra(model)}
        url = f"{self.API}/chat/completions"
        for _ in range(4):
            try:
                data = net.request_json(url, self.name, self._headers(), body, timeout=timeout)
                break
            except net.NetError as e:
                d = (e.detail or "").lower()
                # Some models don't take every option: drop it and ask again.
                dropped = next((k for k in ("reasoning_effort", "response_format", "temperature")
                                if k in body and (k in d or (k == "response_format" and "json" in d))), None)
                if e.status == 400 and dropped:
                    body.pop(dropped)
                    continue
                raise
        if isinstance(data.get("error"), dict):   # some services answer errors with HTTP 200
            raise AIError(f"{self.name}: {data['error'].get('message', 'error')}"[:200], "busy")
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise AIError(f"{self.name} didn't answer", "refused") from None

    def _error(self, e, model=""):
        d = (e.detail or "").lower()
        where = f" ({model})" if model else ""
        if e.status is None:
            return AIError(str(e), "timeout" if e.kind == "timeout" else "offline")
        if e.status == 401 or (e.status in (400, 403) and ("api key" in d or "invalid key" in d)):
            return AIError(f"{self.name} rejected your key - check it in Settings", "key")
        if e.status == 429:
            return self._rate_limited(d, where)
        if e.status == 402 or "insufficient credit" in d:
            return AIError(f"{self.name} says this account has no credit left for that", "credit")
        if e.status == 403 and ("country" in d or "region" in d or "territory" in d or "location" in d):
            return AIError(f"{self.name} isn't available in your country", "region")
        if e.status == 413 or "too large" in d or "context length" in d or "maximum context" in d:
            return AIError(f"that request is too big for this model's free limit{where}", "toolarge")
        if e.status in (403, 404) or (e.status == 400 and "model" in d):
            return AIError(f"that model isn't available{where}", "model")
        if e.status >= 500:
            return AIError(f"{self.name} is busy right now{where}", "busy")
        return AIError(f"{self.name} returned an error (HTTP {e.status}) {e.detail[:120]}".strip(), "other")

    def _rate_limited(self, d, where):
        return AIError(f"the free limit is used up for now{where}", "limit")


class Groq(_OpenAIStyle):
    """Free open models on Groq's fast chips (no card needed; limits are per model)."""
    id = "groq"
    API = "https://api.groq.com/openai/v1"
    _SKIP = re.compile(r"whisper|tts|guard|playai|orpheus|distil|embed|vision|compound|allam|saba", re.I)
    # Best first: big models that know a lot about shows, then smaller quick ones.
    PREFERRED = ("gpt-oss-120b", "llama-3.3-70b", "kimi-k2", "qwen3-32b", "llama-4-maverick",
                 "llama-4-scout", "gpt-oss-20b", "llama-3.1-8b")

    def _usable(self, m):
        return m.get("active", True) is not False

    @classmethod
    def rank(cls, names):
        def key(name):
            pos = next((i for i, p in enumerate(cls.PREFERRED) if p in name.lower()), len(cls.PREFERRED))
            return (pos, -_version(name.lower(), r"(\d+(?:\.\d+)?)b\b"), name)
        return sorted((n for n in names if n and not cls._SKIP.search(n)), key=key)

    def _extra(self, model):
        return {"reasoning_effort": "low"} if "gpt-oss" in model else {}

    def _rate_limited(self, d, where):
        # Groq's free limits are per model (per minute and per day): another model may still answer.
        return AIError(f"the free limit for this model is used up for now{where}", "limit")


class OpenRouter(_OpenAIStyle):
    """OpenRouter's free models (the ones whose name ends in ":free")."""
    id = "openrouter"
    probe_size = 2            # its free requests per day are shared by all its models
    API = "https://openrouter.ai/api/v1"
    _SKIP = re.compile(r"vision|-vl|coder|embed|guard|image|audio|tts|ocr|math", re.I)
    PREFERRED = ("gpt-oss-120b", "deepseek", "llama-3.3-70b", "qwen3-235b", "kimi", "glm", "gemini",
                 "llama-4", "mistral-small", "qwen3", "gemma", "gpt-oss-20b")

    def _headers(self):
        return {"Authorization": f"Bearer {self.key}", "X-Title": "Watch Anything"}

    def _usable(self, m):
        free = str(m.get("id", "")).endswith(":free")
        outputs = (m.get("architecture") or {}).get("output_modalities") or ["text"]
        return free and "text" in outputs

    @classmethod
    def rank(cls, names):
        def key(name):
            low = name.lower()
            pos = next((i for i, p in enumerate(cls.PREFERRED) if p in low), len(cls.PREFERRED))
            return (pos, -(_version(low, r"(\d+(?:\.\d+)?)b\b") or 100), name)
        return sorted((n for n in names if n.endswith(":free") and not cls._SKIP.search(n)), key=key)

    def _rate_limited(self, d, where):
        if "per-day" in d or "per day" in d or "free-models-per-day" in d:
            return AIError("today's free OpenRouter requests are used up - they come back tomorrow", "daily")
        return AIError(f"this free model is busy right now{where}", "limit")


class Custom(_OpenAIStyle):
    """Your own helper with the OpenAI-style chat API."""

    def __init__(self, conf, key):
        self.conf = conf
        self.id = f"custom:{conf['id']}"
        self.key = key
        self.name = conf.get("name") or "Your AI"
        self.API = conf["url"].rstrip("/")

    def _headers(self):
        key = (self.conf.get("key") or "").strip()
        return {"Authorization": f"Bearer {key}"} if key else {}

    def models(self):
        if (self.conf.get("model") or "").strip():
            return [self.conf["model"].strip()]
        return super().models()

    def _usable(self, m):
        return not re.search(r"embed|whisper|tts|audio|image|dall-e|moderation|vision|transcribe|realtime|search",
                             str(m.get("id", "")), re.I)

    @classmethod
    def rank(cls, names):
        # Small, quick, cheap models first: these jobs are short.
        quick = ("mini", "flash", "small", "haiku", "chat", "8b", "lite", "instant", "turbo")
        return sorted(names, key=lambda n: (not any(q in n.lower() for q in quick), n))

    def _call(self, model, prompt, timeout):
        return super()._call(model, prompt + "\n\nAnswer with JSON only.", timeout)


class Anthropic(_Provider):
    """Anthropic's Claude, with your own key (console.anthropic.com)."""
    VERSION = "2023-06-01"

    def __init__(self, conf, key):
        self.conf = conf
        self.id = f"custom:{conf['id']}"
        self.key = key
        self.name = conf.get("name") or "Claude"
        self.API = (conf.get("url") or "https://api.anthropic.com/v1").rstrip("/")

    def _headers(self):
        return {"x-api-key": self.conf.get("key") or "", "anthropic-version": self.VERSION}

    def models(self):
        if (self.conf.get("model") or "").strip():
            return [self.conf["model"].strip()]
        return super().models()

    def _list(self):
        data = net.request_json(f"{self.API}/models?limit=100", self.name, self._headers(), timeout=20)
        return [m.get("id", "") for m in data.get("data", [])]

    @classmethod
    def rank(cls, names):
        # The quick Haiku models first (newest first, as they're listed), then the rest.
        return sorted(names, key=lambda n: ("haiku" not in n, "sonnet" not in n))

    def _call(self, model, prompt, timeout):
        body = {"model": model, "max_tokens": 4096,
                "messages": [{"role": "user", "content": prompt + "\n\nAnswer with JSON only."}]}
        data = net.request_json(f"{self.API}/messages", self.name, self._headers(), body, timeout=timeout)
        try:
            return "".join(b.get("text", "") for b in data.get("content") or [] if b.get("type") == "text")
        except (AttributeError, TypeError):
            raise AIError(f"{self.name} didn't answer", "refused") from None

    def _error(self, e, model=""):
        d = (e.detail or "").lower()
        where = f" ({model})" if model else ""
        if e.status is None:
            return AIError(str(e), "timeout" if e.kind == "timeout" else "offline")
        if e.status in (401, 403) and ("key" in d or "auth" in d or e.status == 401):
            return AIError(f"{self.name} rejected your key - check it in Settings", "key")
        if "credit balance" in d or e.status == 402:
            return AIError(f"{self.name} says this account has no credit left", "credit")
        if e.status == 429:
            return AIError(f"the rate limit is used up for now{where}", "limit")
        if e.status in (500, 502, 503, 529):
            return AIError(f"{self.name} is overloaded right now{where}", "busy")
        if e.status in (400, 404) and "model" in d:
            return AIError(f"that model isn't available{where}", "model")
        return AIError(f"{self.name} returned an error (HTTP {e.status}) {e.detail[:120]}".strip(), "other")


_CLASSES = {"gemini": Gemini, "groq": Groq, "openrouter": OpenRouter}


def helper(provider, key, settings=None):
    if provider in _CLASSES:
        return _CLASSES[provider](key)
    conf = custom_helpers(settings or {}).get(provider)
    if not conf:
        raise AIError("that AI helper isn't set up any more", "key")
    return (Anthropic if conf.get("kind") == "anthropic" else Custom)(conf, key)


def resting(settings):
    """{provider: (seconds left, why)} for helpers being left alone for now."""
    now = time.time()
    with _lock:
        return {p: (int(until - now), why) for (p, k), (until, why, _kind) in _resting.items()
                if until > now and key_of(settings, p) == k}


def _rest(provider, key, err):
    seconds = REST.get(err.kind)
    if seconds:
        with _lock:
            _resting[(provider, key)] = (time.time() + seconds, str(err), err.kind)


def ask(settings, prompt, timeout=60, budget=None, fresh=False):
    """(answer, provider) from the first helper that answers. A helper that
    fails as a whole (out for the day, overloaded, unreachable...) is left
    alone for a while, and the next one is asked instead. `fresh` (you pressed
    a button) gives helpers on a short rest another go."""
    errors = []
    now = time.time()
    for provider in available(settings):
        key = key_of(settings, provider)
        with _lock:
            until, why, kind = _resting.get((provider, key), (0, "", ""))
        if until > now and not (fresh and kind in SHORT_RESTS):
            errors.append(f"{NAMES[provider]}: {why} (resting for {max(1, int((until - now) / 60))} min)")
            continue
        try:
            reply, _model = helper(provider, key, settings).ask(prompt, timeout, budget)
            return reply, provider
        except AIError as e:
            _rest(provider, key, e)
            errors.append(f"{NAMES[provider]}: {e}")
    if not errors:
        raise AIError("no AI helper is set up", "key")
    raise AIError("; ".join(errors))


def test_key(provider, key, settings=None):
    """{"ok", "model" or "error", "tried": [...]} - quick enough for a Settings button.
    Several models are tried at once; the first to answer is used from then on."""
    log = []
    key = (key or "").strip()
    if settings is not None:
        available(settings)
    with _lock:
        _resting.pop((provider, key), None)   # you're checking it: give it a fresh chance
        for k in [k for k in _bad if k[0] == provider and k[1] == key]:
            del _bad[k]
    try:
        _reply, model = helper(provider, key, settings).probe('Reply with exactly this JSON: {"ok": true}', timeout=30, log=log)
        return {"ok": True, "model": model, "tried": log}
    except AIError as e:
        return {"ok": False, "error": str(e), "kind": e.kind, "tried": log}


# ------------------------------------------------------------- the jobs

def _relevant(items, questions, limit=150):
    """The titles in your list that could be what the files are (a whole big
    list would be too much for some free models)."""
    if len(items) <= limit:
        return items
    words = set()
    for q in questions:
        words |= {w for w in re.findall(r"[a-z0-9]+", f'{q.get("guess", "")} {" ".join(q.get("files", []))}'.lower())
                  if len(w) >= 4}
    def score(i):
        names = " ".join([i.get("title") or "", i.get("original_title") or "", *(i.get("aliases") or [])]).lower()
        return len(words & set(re.findall(r"[a-z0-9]+", names)))
    picked = sorted(items, key=score, reverse=True)
    return [i for i in picked[:limit] if score(i)] or picked[:limit]


def _short(items, limit=500):
    return [{"id": i["id"], "type": i["type"], "title": i["title"],
             **({"original": i["original_title"]} if i.get("original_title") else {}),
             "year": i.get("year"), "kind": (i.get("media_type") or i.get("format") or "").lower()}
            for i in items[:limit]]


def sort_downloads(settings, items, questions, fresh=False):
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
        f"The watchlist:\n{json.dumps(_short(_relevant(items, questions)), ensure_ascii=False)}\n\n"
        f"Questions:\n{json.dumps(questions, ensure_ascii=False)}")
    reply, provider = ask(settings, prompt, timeout=90, budget=180, fresh=fresh)
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


def connected_titles(settings, item):
    """Everything connected to a title, as the AI knows it:
    [{"title", "year", "kind": tv|movie|ova|ona|special, "relation", "group": main|other}]."""
    anime = item.get("type") == "anime"
    what = 'anime' if anime else 'TV series' if item.get('format') == 'series' else 'film'
    prompt = (
        f"List EVERYTHING connected to the {what} \"{item['title']}\" ({item.get('year') or 'year unknown'}).\n"
        + (f"Its summary: {item['overview'][:600]}\n" if item.get("overview") else "")
        + "Two groups:\n"
        "- main: the same story and continuity - every season, sequel, prequel, film, OVA, ONA, special, recap and side "
        "story; the show or film it is a spin-off of; and spin-offs of it.\n"
        "- other: the wider franchise - other versions, reboots, remakes, other continuities and universes, adaptations "
        "(live action / animated), and other series or films of the same franchise or characters.\n"
        "If it's new and you don't know it well, use its summary: a spin-off's summary usually names the show it comes from.\n"
        "Only real, released or announced titles; no fan works, no unrelated titles with similar names.\n"
        "For each give the exact title as listed on "
        + ("MyAnimeList" if anime else "TMDB (anime as on MyAnimeList)") +
        ', its first release year, kind (one of "tv", "movie", "ova", "ona", "special"), how it\'s related '
        '(e.g. "Season 2", "Sequel", "Spin-off of", "Spin-off", "Reboot", "Other continuity", "Animated series") and its group.\n'
        'Reply as JSON: {"connected": [{"title": ..., "year": ..., "kind": ..., "relation": ..., "group": "main" or "other"}]}')
    reply, _ = ask(settings, prompt, timeout=50, budget=90)
    rows = reply.get("connected") if isinstance(reply, dict) else reply
    out = []
    for row in rows or []:
        if isinstance(row, dict) and row.get("title"):
            kind = str(row.get("kind") or "").lower()
            out.append({"title": str(row["title"]), "year": row.get("year") if isinstance(row.get("year"), int) else None,
                        "kind": kind if kind in ("tv", "movie", "ova", "ona", "special") else "tv",
                        "relation": str(row.get("relation") or "Related"),
                        "group": "other" if row.get("group") == "other" else "main"})
    return out[:60]


def streams_on(settings, item, region, services):
    """Which of `services` (names) have a title to stream in a country, as the
    AI knows it: {name: True/False}. Best knowledge, not live data."""
    kind = "anime" if item.get("type") == "anime" else "TV series" if item.get("format") == "series" else "film"
    prompt = (
        f"Country code: {region}. For the {kind} \"{item['title']}\" ({item.get('year') or 'year unknown'}), "
        "say for each of these streaming services whether it is available to stream there right now "
        "(subscription, free or with ads; not rent or buy): " + ", ".join(json.dumps(n) for n in services) + ".\n"
        "If you are not sure, say false.\n"
        'Reply as JSON: {"available": {"<service name>": true or false, ...}}')
    reply, _ = ask(settings, prompt, timeout=30, budget=45)
    got = reply.get("available") if isinstance(reply, dict) else None
    got = got if isinstance(got, dict) else {}
    norm = {re.sub(r"\W", "", str(k)).lower(): v for k, v in got.items()}
    return {n: norm.get(re.sub(r"\W", "", n).lower()) is True for n in services}


GUIDE_CANON = ("canon", "anime original", "non-canon", "recap", "alternate version", "unknown")
GUIDE_IMPORTANCE = ("essential", "recommended", "optional", "skippable")


def story_guide(settings, item, entries):
    """How everything connected to a title fits its story. `entries` are the
    connected titles (facts from MyAnimeList / TMDB). Returns {"overview",
    "overview_spoilers", "story_order": [n...], "entries": {n: {"connects",
    "placement", "canon", "importance", "about", "spoilers"}}, "by"}, where n
    is 0 for the title itself and i + 1 for entries[i]."""
    anime = item.get("type") == "anime"

    def fact(n, x, this=False):
        listed = x.get("relation") or ""
        if x.get("via"):
            listed += f" (of {x['via']})"
        return {"n": n, "title": x.get("title"), "kind": x.get("media_type") or x.get("media_kind") or x.get("type"),
                "year": x.get("year"), "episodes": x.get("episodes_total"),
                "listed_as": "the title they're asking about" if this else listed,
                "summary": (x.get("overview") or "")[:280]}

    rows = [fact(0, item, True)] + [fact(i + 1, x) for i, x in enumerate(entries[:40])]
    prompt = (
        f"Someone has \"{item['title']}\" ({item.get('year') or 'year unknown'}) in their watchlist and wants to "
        "understand everything connected to it. Here is every connected title, from "
        + ("MyAnimeList" if anime else "TMDB") + ". The facts (kind, year, episodes, how it's listed) are "
        f"correct - rely on them:\n{json.dumps(rows, ensure_ascii=False)}\n\n"
        "For EVERY entry above (by n, including n=0) give:\n"
        "- connects: how it connects to the main story, 1-2 sentences, e.g. \"Direct sequel: picks up right "
        "after season 1's finale\", \"Prequel set ten years earlier\", \"Recap film of season 1 with a few new "
        "scenes\", \"Same story retold by a different studio\". No spoilers.\n"
        "- placement: when to watch it, e.g. \"After season 1, before season 2\", \"Any time after episode 12\", "
        "\"Last\".\n"
        f"- canon: one of {', '.join(GUIDE_CANON)}.\n"
        f"- importance: one of {', '.join(GUIDE_IMPORTANCE)} (for following the main story).\n"
        "- about: what it's about, 1-2 sentences, with NO spoilers for it or anything before it.\n"
        "- spoilers: 1-3 sentences on how it ties into the whole story, spoilers allowed.\n"
        "Also give overview: 3-4 sentences without spoilers on how the whole franchise fits together and the "
        "best way into it; overview_spoilers: the same with spoilers; and story_order: the n of every entry that "
        "is part of the story, in the order the story happens (its timeline), not release order.\n"
        "If you don't know an entry well, say so in connects and use \"unknown\" - never guess.\n"
        'Reply as JSON: {"overview": "...", "overview_spoilers": "...", "story_order": [0, ...], '
        '"entries": [{"n": 0, "connects": "...", "placement": "...", "canon": "...", "importance": "...", '
        '"about": "...", "spoilers": "..."}]}')
    reply, provider = ask(settings, prompt, timeout=90, budget=180)
    if not isinstance(reply, dict):
        raise AIError("the answer wasn't readable")
    count = len(rows)
    text = lambda v, n=600: str(v or "").strip()[:n]
    out = {}
    for e in reply.get("entries") or []:
        if not isinstance(e, dict) or not isinstance(e.get("n"), int) or not 0 <= e["n"] < count:
            continue
        canon = text(e.get("canon"), 40).lower()
        importance = text(e.get("importance"), 40).lower()
        out[e["n"]] = {"connects": text(e.get("connects")), "placement": text(e.get("placement"), 200),
                       "canon": canon if canon in GUIDE_CANON else "unknown",
                       "importance": importance if importance in GUIDE_IMPORTANCE else "",
                       "about": text(e.get("about")), "spoilers": text(e.get("spoilers"), 900)}
    order = [n for n in reply.get("story_order") or [] if isinstance(n, int) and 0 <= n < count]
    return {"overview": text(reply.get("overview"), 1200), "overview_spoilers": text(reply.get("overview_spoilers"), 1500),
            "story_order": list(dict.fromkeys(order)), "entries": out, "by": NAMES[provider]}


def mood_picks(settings, text, kinds, seed, my_list, trending):
    """Picks for I'm in the mood for: {"understood", "intro", "by", "from_list": [{"id", "why"}],
    "new": [{"title", "year", "type", "why"}]}. Never a question: the kind was chosen first."""
    today = time.strftime("%B %Y")
    types = {"films": "movie", "TV shows": "tv", "anime": "anime"}
    prompt = (
        f"You help someone choose something to watch. Today is {today}. They want: {' or '.join(kinds)}.\n"
        f'What they typed: "{text}". It may be a title, a word, a mood, a genre, an actor or a sentence, and it may '
        "have typos (for example 'indterstella' means Interstellar): read it generously.\n"
        + (f'It looks like they mean the title "{seed["title"]}" ({seed.get("year") or "?"}): recommend titles LIKE it '
           "(the same feel, themes and tone), not that title itself.\n" if seed else "")
        + ("New and popular right now (they may fit; you may know less about them):\n" + "\n".join(trending) + "\n"
           if trending else "")
        + "Their watchlist (id|title|how far they've got):\n" + ("\n".join(my_list) or "(empty)") + "\n\n"
        "Recommend now; never ask a question. Pick up to 5 titles from their watchlist that fit (by id), and 15 new "
        f"titles of the kind they want ({', '.join(kinds)}) that aren't in it: real, released titles (anime named as on "
        "MyAnimeList, TV shows and films as on TMDB), best fit first. Include recent ones (from the last two years) "
        "when they fit, like a new release with the same feel. Each with a short reason that refers to what they asked for.\n"
        'Reply as JSON: {"understood": "what you took them to mean, a few words", "intro": "one short sentence", '
        '"from_list": [{"id": ..., "why": ...}], "new": [{"title": ..., "year": ..., "type": '
        + " or ".join(f'"{types[k]}"' for k in kinds) + ', "why": ...}]}')
    reply, provider = ask(settings, prompt, timeout=40, budget=60, fresh=True)
    if not isinstance(reply, dict):
        raise AIError("the answer wasn't readable")
    pick = lambda rows, keys: [{k: r.get(k) for k in keys} for r in rows or [] if isinstance(r, dict)]
    return {"understood": str(reply.get("understood") or "")[:120], "intro": str(reply.get("intro") or "")[:300],
            "by": NAMES[provider], "from_list": pick(reply.get("from_list"), ("id", "why"))[:8],
            "new": [r for r in pick(reply.get("new"), ("title", "year", "type", "why"))
                    if r.get("title") and r.get("type") in ("anime", "tv", "movie")][:18]}


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
