"""Subtitles made by AI, for an episode that has none anywhere (No subtitles? Make them with AI).

The episode's sound is taken with ffmpeg from where you are - speech only, one channel, small
(a minute is about 250 KB) - and sent, a piece at a time, to a speech-to-text AI: Groq's free
Whisper (your Groq key, Settings → AI), or OpenAI's (an OpenAI helper of your own). What isn't
in English is translated to English by the same AI as it's written down. The lines are kept in a
WebVTT file that grows as the pieces come back (the first in about 10-20 seconds), saved for the
episode, so watching it again, on another device or in a watch party uses it without asking again.

This is the one thing the app sends that isn't a name: the episode's sound. It's said so where
it's asked for.
"""

import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from . import transcode

FIRST = 60                     # seconds in the first piece (lines show soon)
PIECE = 300                    # ...and in each after it
AHEAD = 600                    # how far past where you are it goes before waiting for you
TIMEOUT = 120
# Lines Whisper makes up over music and silence.
_MADE_UP = re.compile(r"^(?:thank(?:s| you) (?:so much )?for watching[.!]*|subtitles? by\b.*|please subscribe\b.*|"
                      r"\(?(?:music|applause|silence)\)?|♪+|\.+|you)$", re.I)


class AISubError(Exception):
    pass


def providers(settings):
    """[{"name", "url", "key", "model"}] that can turn speech into text, the free one first."""
    out = []
    if settings.get("groq_api_key"):
        out.append({"name": "Groq", "url": "https://api.groq.com/openai/v1", "key": settings["groq_api_key"],
                    "model": "whisper-large-v3"})
    for x in settings.get("ai_custom") or []:
        host = urllib.parse.urlsplit(str(x.get("url") or "")).netloc.lower()
        if x.get("key") and host.endswith("api.openai.com"):
            out.append({"name": x.get("name") or "OpenAI", "url": str(x["url"]).rstrip("/"), "key": x["key"],
                        "model": "whisper-1"})
    return out


def audio(source, start, length, headers=None, timeout=TIMEOUT):
    """`length` seconds of the video's sound from `start`, as a small MP3 (bytes; b"" past the end)."""
    ffmpeg, _ = transcode.find()
    if not ffmpeg:
        raise AISubError("ffmpeg is missing from this copy of the app")
    web = str(source).lower().startswith(("http://", "https://"))
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-ss", f"{max(0.0, start):.3f}",
           *(transcode._header_args(headers) if web else []), "-i", source, "-t", f"{length:.3f}", "-vn", "-sn", "-dn",
           "-ac", "1", "-ar", "16000", "-b:a", "32k", "-f", "mp3", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, **transcode._no_window())
    except subprocess.TimeoutExpired:
        raise AISubError("the episode's sound took too long to read") from None
    if r.returncode != 0 and not r.stdout:
        why = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        raise AISubError(f"the episode's sound couldn't be read ({why[-1][:160] if why else 'ffmpeg failed'})")
    return r.stdout if len(r.stdout) > 1024 else b""       # (past the end: a header with no sound in it)


def _multipart(fields, file_name, data):
    boundary = uuid.uuid4().hex
    body = b""
    for k, v in fields.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode("utf-8")
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{file_name}\"\r\n"
             "Content-Type: audio/mpeg\r\n\r\n").encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode("utf-8")
    return body, f"multipart/form-data; boundary={boundary}"


def transcribe(provider, sound, translate=False, timeout=TIMEOUT):
    """{"language", "segments": [{"start", "end", "text"}]} for a piece of sound. `translate`: into
    English (what isn't). Raises AISubError."""
    if not sound:
        return {"language": "", "segments": []}
    what = "translations" if translate else "transcriptions"
    body, ctype = _multipart({"model": provider["model"], "response_format": "verbose_json", "temperature": "0"},
                             "sound.mp3", sound)
    req = urllib.request.Request(f"{provider['url']}/audio/{what}", data=body, method="POST",
                                 headers={"Authorization": f"Bearer {provider['key']}", "Content-Type": ctype,
                                          "User-Agent": "WatchAnything"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise AISubError(f"{provider['name']} turned the key down - check it in Settings → AI") from None
        if e.code == 429:
            raise AISubError(f"{provider['name']} has had enough for now (its free limit) - try again later") from None
        raise AISubError(f"{provider['name']} answered {e.code}") from None
    except (OSError, ValueError) as e:
        raise AISubError(f"{provider['name']} couldn't be reached ({e})") from None
    segments = []
    for s in data.get("segments") or []:
        try:
            start, end, text = float(s["start"]), float(s["end"]), " ".join(str(s.get("text") or "").split())
        except (KeyError, TypeError, ValueError):
            continue
        if not text or _MADE_UP.match(text) or float(s.get("no_speech_prob") or 0) > 0.6:
            continue
        segments.append({"start": start, "end": max(end, start + 0.5), "text": text})
    return {"language": str(data.get("language") or "").lower(), "segments": segments}


def _stamp(t):
    t = max(0.0, t)
    return f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{t % 60:06.3f}"


def to_vtt(lines):
    out = ["WEBVTT", ""]
    for n, x in enumerate(sorted(lines, key=lambda x: x["start"]), 1):
        out += [str(n), f"{_stamp(x['start'])} --> {_stamp(x['end'])}", x["text"], ""]
    return "\n".join(out)


def english(language):
    return language in ("", "en", "english")


class Job:
    """One episode's subtitles being made: from `start`, a piece at a time, kept in `path` (WebVTT)
    as they come. `position` (a function): where you are now - it doesn't go more than AHEAD past it.
    `on_lines(job)`: called after each piece is written (the player reloads it)."""

    def __init__(self, settings, source, path, start=0.0, duration=None, headers=None, position=None, on_lines=None):
        self.settings, self.source, self.path = settings, source, Path(path)
        self.start, self.duration, self.headers = max(0.0, float(start or 0)), duration, headers or {}
        self.position = position or (lambda: None)
        self.on_lines = on_lines or (lambda job: None)
        self.lines, self.done_until, self.error, self.running, self.stopped = [], self.start, "", False, False
        self.translate, self.language, self.by = False, "", ""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_file():
            # (made before, from some point on: what's there is kept and added to)
            self.lines = _read_vtt(self.path.read_text(encoding="utf-8", errors="replace"))

    def run_soon(self):
        self.running = True
        threading.Thread(target=self.run, daemon=True).start()
        return self

    def stop(self):
        self.stopped = True

    def run(self):
        self.running = True
        try:
            found = providers(self.settings)
            if not found:
                raise AISubError("add a free Groq key (Settings → AI) - or an OpenAI helper - to make subtitles with AI")
            at, length = self.start, FIRST
            while not self.stopped and (self.duration is None or at < self.duration - 0.5):
                now = self.position()
                if now is not None and at - now > AHEAD:
                    time.sleep(2)                    # (far enough ahead: waits for you)
                    continue
                sound = audio(self.source, at, length, self.headers)
                if not sound:
                    break                            # (the end)
                got = None
                for provider in found:
                    try:
                        got = transcribe(provider, sound, self.translate)
                        self.by = provider["name"]
                        break
                    except AISubError as e:
                        self.error = str(e)
                if got is None:
                    raise AISubError(self.error)
                if not self.translate and not english(got["language"]):
                    # (another language: this piece again, and the rest, put into English)
                    self.translate, self.language = True, got["language"]
                    continue
                self.language = self.language or got["language"]
                self.lines = [x for x in self.lines if not at <= x["start"] < at + length]
                self.lines += [dict(x, start=x["start"] + at, end=x["end"] + at) for x in got["segments"]]
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(to_vtt(self.lines), encoding="utf-8")
                os.replace(tmp, self.path)
                self.error, self.done_until = "", at + length
                self.on_lines(self)
                at, length = at + length, PIECE
        except AISubError as e:
            self.error = str(e)
        finally:
            self.running = False
            self.on_lines(self)


_CUE = re.compile(r"(\d+):(\d{2}):(\d{2}(?:\.\d+)?)\s*-->\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")


def _read_vtt(text):
    out = []
    for block in re.split(r"\n\s*\n", text.replace("\r", "")):
        lines = block.strip().split("\n")
        for i, line in enumerate(lines):
            m = _CUE.search(line)
            if m:
                h1, m1, s1, h2, m2, s2 = m.groups()
                body = " ".join(lines[i + 1:]).strip()
                if body:
                    out.append({"start": int(h1) * 3600 + int(m1) * 60 + float(s1),
                                "end": int(h2) * 3600 + int(m2) * 60 + float(s2), "text": body})
                break
    return out
