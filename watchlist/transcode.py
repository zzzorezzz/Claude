"""Turning a video into something a phone's browser plays (Use on your phone).

An iPhone's Safari plays MP4 (H.264, or HEVC on newer ones, with AAC sound) and HLS; most
downloads are MKV, often with 10-bit pictures, several audio tracks and styled subtitles.
So each video is looked at first (ffprobe) and played the cheapest way that works:

  - an MP4 the phone can play as it is: sent as it is (with byte ranges, so it seeks);
  - a picture the phone can play in another box (MKV): repackaged as HLS (fragmented MP4)
    without touching the picture - fast, nothing lost;
  - anything else: the picture converted to H.264 - by the graphics card when there is one
    (NVENC, Quick Sync, AMF, VideoToolbox), else x264 - no bigger than Settings → Phone quality;
  - sound: AAC is copied, anything else becomes AAC; every audio track is kept, each one an
    HLS rendition of its own (chosen in the player);
  - subtitles: text ones written as WebVTT alongside, as the video goes (or burned into the
    picture, styled, when you ask for that).

A job writes its HLS into a folder of its own, from where the phone asked to start; a seek
past what's ready starts it again from there (when it can't simply wait: see webplay.py).
"""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

SEGMENT = 4                    # seconds per HLS piece
# ffmpeg seeks 3/23 s before where it's asked in a picture with B-frames: asked this far past a key
# frame, it lands on that key frame (not the one before).
SEEK_PAST = 0.15
TEXT_SUBS = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text"}
PHONE_AUDIO = {"aac"}          # copied as it is; anything else becomes AAC
MAX_AUDIO = 8


def _no_window():
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def find():
    """(ffmpeg, ffprobe): the ones bundled with the app, or installed ones. ("", "") when missing."""
    exe = ".exe" if sys.platform == "win32" else ""
    places = []
    if os.environ.get("WATCHLIST_FFMPEG"):
        places.append(Path(os.environ["WATCHLIST_FFMPEG"]))
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        places += [base / "ffmpeg", Path(sys.executable).resolve().parent.parent / "Resources" / "ffmpeg"]
    places.append(Path(__file__).resolve().parent.parent / "packaging" / "ffmpeg")
    for folder in places:
        f, p = folder / f"ffmpeg{exe}", folder / f"ffprobe{exe}"
        if f.is_file() and p.is_file():
            return str(f), str(p)
    f, p = shutil.which("ffmpeg"), shutil.which("ffprobe")
    return (f, p) if f and p else ("", "")


def _header_args(headers):
    if not headers:
        return []
    return ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]


def probe(source, headers=None, timeout=40):
    """What a video holds: {"duration", "format", "video", "audio": [...], "subs": [...]}.
    Raises RuntimeError when it can't be read."""
    _, ffprobe = find()
    if not ffprobe:
        raise RuntimeError("ffmpeg is missing from this copy of the app")
    cmd = [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", *_header_args(headers), source]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=timeout, **_no_window())
    except subprocess.TimeoutExpired:
        raise RuntimeError("the video took too long to open") from None
    if out.returncode != 0:
        raise RuntimeError((out.stderr or "the video couldn't be read").strip().splitlines()[-1][:200])
    data = json.loads(out.stdout or "{}")
    fmt = data.get("format") or {}
    info = {"duration": float(fmt.get("duration") or 0) or None, "format": fmt.get("format_name") or "",
            "video": None, "audio": [], "subs": []}
    a = s = 0
    for st in data.get("streams") or []:
        tags = {k.lower(): v for k, v in (st.get("tags") or {}).items()}
        disp = st.get("disposition") or {}
        kind = st.get("codec_type")
        if kind == "video" and info["video"] is None and not disp.get("attached_pic"):
            pix = st.get("pix_fmt") or ""
            num, _, den = str(st.get("avg_frame_rate") or st.get("r_frame_rate") or "24/1").partition("/")
            try:
                fps = float(num) / float(den or 1)
            except (ValueError, ZeroDivisionError):
                fps = 24.0
            info["video"] = {"codec": st.get("codec_name") or "", "pix_fmt": pix, "profile": st.get("profile") or "",
                             "fps": fps if 5 < fps < 121 else 24.0,
                             "width": st.get("width") or 0, "height": st.get("height") or 0,
                             "bits": 10 if ("10" in pix or "p010" in pix) else 12 if "12" in pix else 8}
        elif kind == "audio":
            info["audio"].append({"n": a, "codec": st.get("codec_name") or "", "channels": st.get("channels") or 2,
                                  "lang": tags.get("language") or "", "title": tags.get("title") or "",
                                  "default": bool(disp.get("default"))})
            a += 1
        elif kind == "subtitle":
            codec = st.get("codec_name") or ""
            info["subs"].append({"n": s, "codec": codec, "text": codec in TEXT_SUBS, "lang": tags.get("language") or "",
                                 "title": tags.get("title") or "", "default": bool(disp.get("default")),
                                 "forced": bool(disp.get("forced")), "styled": codec in ("ass", "ssa")})
            s += 1
    if not info["video"]:
        raise RuntimeError("there's no picture in that file")
    return info


def keyframe_before(source, t, headers=None, timeout=20):
    """Where the key frame at or before `t` is (seconds from the video's start), or None. A copied
    picture can only start at one: a video repackaged from part way really starts there."""
    _, ffprobe = find()
    if not ffprobe or t <= 0:
        return None
    cmd = [ffprobe, "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey", "-show_entries",
           "frame=best_effort_timestamp_time:format=start_time", "-read_intervals", f"{t:.3f}%+0.05", "-of", "json",
           *_header_args(headers), source]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=timeout, **_no_window())
        data = json.loads(out.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    first = float((data.get("format") or {}).get("start_time") or 0)
    times = [float(f["best_effort_timestamp_time"]) - first for f in data.get("frames") or []
             if f.get("best_effort_timestamp_time") not in (None, "N/A")]
    before = [x for x in times if x <= t + 0.01]
    return max(0.0, max(before)) if before else None


def video_fits(v, caps):
    """Whether the phone plays this picture as it is."""
    if v["codec"] == "h264":
        return v["bits"] == 8 and v["pix_fmt"] in ("yuv420p", "yuvj420p") and "4:4:4" not in v["profile"] \
            and "4:2:2" not in v["profile"]
    if v["codec"] == "hevc":
        return bool((caps or {}).get("hevc")) and v["bits"] in (8, 10) and "420" in (v["pix_fmt"] or "yuv420p")
    return False


QUALITY = {"original": None, "1080": 1080, "720": 720, "480": 480}


def plan(info, caps=None, quality="1080", burn=None, local=True):
    """How to play it: {"direct", "video" ("copy" / "encode"), "height" (when made smaller), "audio"}."""
    v = info["video"]
    cap = QUALITY.get(str(quality), 1080)
    smaller = bool(cap and v["height"] and v["height"] > cap + 8)
    fits = video_fits(v, caps)
    encode = not fits or burn is not None or smaller
    audio = ["copy" if a["codec"] in PHONE_AUDIO else "aac" for a in info["audio"][:MAX_AUDIO]]
    mp4 = any(x in info["format"] for x in ("mp4", "mov"))
    direct = (local and mp4 and not encode and v["codec"] == "h264" and len(info["audio"]) <= 1
              and all(x == "copy" for x in audio))
    return {"direct": direct, "video": "encode" if encode else "copy", "height": cap if smaller else None,
            "audio": audio, "burn": burn}


def keep_as_is(info, caps=None):
    """Whether a phone can keep and play this file as it is (an MP4 of a picture it plays, with
    sound it plays) - nothing to make."""
    v = info["video"]
    mp4 = any(x in info["format"] for x in ("mp4", "mov"))
    # (HEVC is always repackaged: Apple's players want it marked "hvc1", which a file may not be)
    return mp4 and v["codec"] == "h264" and video_fits(v, caps) and all(a["codec"] in PHONE_AUDIO for a in info["audio"][:MAX_AUDIO])


class Mp4:
    """The video made into one MP4 for a phone to keep (Save to this iPad): the picture copied
    when the phone plays it (else converted, by the graphics card when there is one), every
    sound as AAC, its index at the start (it plays at once, and from anywhere)."""

    def __init__(self, source, info, out, caps=None, quality="1080"):
        self.source, self.info, self.out = source, info, Path(out)
        self.how = plan(info, caps, quality, local=True)
        self.progress, self.error, self.done = 0.0, "", False
        self.proc, self.stuck = None, False

    def command(self):
        ffmpeg, _ = find()
        audio = self.info["audio"][:MAX_AUDIO]
        cmd = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", self.source, "-map", "0:v:0"]
        for a in audio:
            cmd += ["-map", f"0:a:{a['n']}"]
        if self.how["video"] == "copy":
            cmd += ["-c:v", "copy"] + (["-tag:v", "hvc1"] if self.info["video"]["codec"] == "hevc" else [])
        else:
            vf, args = _encode_args(encoder(), self.how.get("height"), self.info["video"].get("fps") or 24.0)
            cmd += (["-vf", ",".join(vf)] if vf else []) + args
        for i, a in enumerate(audio):
            if self.how["audio"][i] == "copy":
                cmd += [f"-c:a:{i}", "copy"]
            else:
                cmd += [f"-c:a:{i}", "aac", f"-ac:a:{i}", "2", f"-b:a:{i}", "160k"]
            cmd += [f"-metadata:s:a:{i}", f"title={track_name(a, i)}", f"-metadata:s:a:{i}", f"language={(a.get('lang') or 'und')[:3]}"]
        return cmd + ["-sn", "-dn", "-map_chapters", "-1", "-movflags", "+faststart", "-progress", "pipe:1", "-nostats",
                      "-f", "mp4", str(self.out)]

    STUCK = 120          # seconds without getting any further: a damaged file it can't get past

    def run(self):
        """Makes it (blocks); progress 0-1 as it goes. Raises RuntimeError when it can't."""
        self.out.parent.mkdir(parents=True, exist_ok=True)
        total = float(self.info.get("duration") or 0)
        log = self.out.with_suffix(".log")
        # (what ffmpeg says goes to a file: a damaged video can make it say more than a pipe holds,
        # and it would wait forever for that to be read)
        with open(log, "wb") as err_out:
            self.proc = subprocess.Popen(self.command(), stdout=subprocess.PIPE, stderr=err_out, text=True,
                                         encoding="utf-8", errors="replace", **_no_window())
            moved = [time.monotonic()]

            def watch():
                while self.proc.poll() is None:
                    if time.monotonic() - moved[0] > self.STUCK:
                        self.stuck = True
                        self.proc.kill()
                        return
                    time.sleep(1)
            self.stuck = False
            threading.Thread(target=watch, daemon=True).start()
            for line in self.proc.stdout:
                if line.startswith("out_time_us=") and total:
                    try:
                        got = min(0.99, int(line.split("=", 1)[1]) / 1e6 / total)
                    except ValueError:
                        continue
                    if got > self.progress:
                        self.progress, moved[0] = got, time.monotonic()
            code = self.proc.wait()
            self.proc.stdout.close()
        try:
            err = log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            err = ""
        log.unlink(missing_ok=True)
        if code != 0 or self.stuck:
            self.out.unlink(missing_ok=True)
            if self.stuck:
                raise RuntimeError("the video stopped getting any further (it may be damaged)")
            raise RuntimeError((err or "the video couldn't be made").strip().splitlines()[-1][:200])
        self.progress, self.done = 1.0, True

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()


# ---- the encoder: the graphics card's when there is one

_encoder = None
_encoder_lock = threading.Lock()
CANDIDATES = {"win32": ["h264_nvenc", "h264_qsv", "h264_amf"], "darwin": ["h264_videotoolbox"],
              "linux": ["h264_nvenc", "h264_vaapi"]}


def encoder():
    """The H.264 encoder to use here: the first one of the graphics card's that really works
    (tried once on a tiny clip), else x264."""
    global _encoder
    with _encoder_lock:
        if _encoder:
            return _encoder
        ffmpeg, _ = find()
        _encoder = "libx264"
        for name in CANDIDATES.get(sys.platform, CANDIDATES["linux"]):
            if name == "h264_vaapi":
                continue
            try:
                r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                                    "testsrc=duration=0.2:size=320x240:rate=25", "-c:v", name, "-f", "null", "-"],
                                   capture_output=True, timeout=20, **_no_window())
            except (OSError, subprocess.SubprocessError):
                continue
            if r.returncode == 0:
                _encoder = name
                break
        return _encoder


def _encode_args(name, height, fps=24.0):
    vf = [f"scale=-2:'min(ih,{height})'"] if height else []
    gop = str(max(12, round(fps * SEGMENT)))
    args = {"h264_nvenc": ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "23", "-b:v", "0", "-forced-idr", "1"],
            "h264_qsv": ["-c:v", "h264_qsv", "-preset", "veryfast", "-global_quality", "23"],
            "h264_amf": ["-c:v", "h264_amf", "-quality", "speed", "-rc", "cqp", "-qp_i", "22", "-qp_p", "24"],
            "h264_videotoolbox": ["-c:v", "h264_videotoolbox", "-b:v", "6M" if (height or 1080) >= 1080 else "3M"],
            }.get(name, ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-sc_threshold", "0"])
    # A key frame every SEGMENT seconds: the pieces are all that long.
    return vf, args + ["-pix_fmt", "yuv420p", "-profile:v", "high", "-g", gop,
                       "-force_key_frames", f"expr:gte(t,n_forced*{SEGMENT})"]


def _filter_path(path):
    """A file's name as ffmpeg's filters want it written (C\\:/...)."""
    return str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def _safe_name(text, taken):
    name = re.sub(r"[^A-Za-z0-9]+", "_", text or "").strip("_")[:24] or "Audio"
    base, n = name, 2
    while name in taken:
        name, n = f"{base}_{n}", n + 1
    taken.add(name)
    return name


LANGS = {"jpn": "Japanese", "ja": "Japanese", "eng": "English", "en": "English", "spa": "Spanish", "es": "Spanish",
         "fre": "French", "fra": "French", "fr": "French", "ger": "German", "deu": "German", "de": "German",
         "ita": "Italian", "it": "Italian", "por": "Portuguese", "pt": "Portuguese", "rus": "Russian", "ru": "Russian",
         "kor": "Korean", "ko": "Korean", "chi": "Chinese", "zho": "Chinese", "zh": "Chinese", "ara": "Arabic",
         "hin": "Hindi", "urd": "Urdu"}


def track_name(t, n, kind="Audio"):
    lang = LANGS.get((t.get("lang") or "").lower(), (t.get("lang") or "").upper())
    title = t.get("title") or ""
    if title and lang and lang.lower() not in title.lower():
        return f"{lang} - {title}"
    return title or lang or f"{kind} {n + 1}"


# ---- a job: one video for one phone, from a point in it

class Job:
    def __init__(self, source, info, how, folder, start=0.0, headers=None, subs=None):
        """`how` from plan(); `subs`: the text subtitle tracks (their n) written as WebVTT alongside."""
        self.source, self.info, self.how, self.folder = source, info, how, Path(folder)
        self.start, self.headers = max(0.0, float(start or 0)), headers or {}
        self.subs = [s for s in (subs or []) if any(x["n"] == s and x["text"] for x in info["subs"])]
        self.proc, self.error, self.used = None, "", time.time()
        self.audio = []                    # [{"name", "label", "lang", "default"}]
        self.past = 0.0                    # how far past `start` (a key frame) ffmpeg is asked to seek
        self._log = None

    def command(self):
        ffmpeg, _ = find()
        info, how = self.info, self.how
        cmd = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
        if self.start:
            cmd += ["-ss", f"{self.start + self.past:.3f}"]
        cmd += _header_args(self.headers) + ["-i", self.source, "-map", "0:v:0"]
        audio = info["audio"][:MAX_AUDIO]
        for a in audio:
            cmd += ["-map", f"0:a:{a['n']}"]
        if how["video"] == "copy":
            cmd += ["-c:v", "copy"]
            if info["video"]["codec"] == "hevc":
                cmd += ["-tag:v", "hvc1"]
        else:
            vf, args = _encode_args(encoder(), how.get("height"), info["video"].get("fps") or 24.0)
            if how.get("burn") is not None:
                # Styled subtitles drawn into the picture (their own fonts, from the file); their
                # timing follows the video's own clock, also after starting part way.
                b = how["burn"]
                where = f"filename='{_filter_path(b['path'] or self.source)}'" + (f":si={b['n']}" if b.get("n") is not None else "")
                vf = [f"setpts=PTS+{self.start:.3f}/TB", f"subtitles={where}", "setpts=PTS-STARTPTS"] + vf
            elif self.start:
                vf = ["setpts=PTS-STARTPTS"] + vf      # (picture and sound both start at 0, together)
            if vf:
                cmd += ["-vf", ",".join(vf)]
            cmd += args
        reset = bool(self.start) and how["video"] != "copy"
        for i, a in enumerate(audio):
            if how["audio"][i] == "copy" and not reset:
                cmd += [f"-c:a:{i}", "copy"]
            else:
                cmd += [f"-c:a:{i}", "aac", f"-ac:a:{i}", "2", f"-b:a:{i}", "160k"]
                if reset:
                    cmd += [f"-filter:a:{i}", "asetpts=PTS-STARTPTS"]
        if self.start and how["video"] == "copy":
            cmd += ["-avoid_negative_ts", "make_zero"]
        taken, streams = set(), ["v:0,agroup:aud,name:video" if audio else "v:0,name:video"]
        default = next((i for i, a in enumerate(audio) if a["default"]), 0)
        self.audio = []
        for i, a in enumerate(audio):
            label = track_name(a, i)
            name = _safe_name(label, taken)
            lang = (a.get("lang") or "und")[:3]
            streams.append(f"a:{i},agroup:aud,name:{name},language:{lang}" + (",default:yes" if i == default else ""))
            self.audio.append({"name": name, "label": label, "lang": lang, "default": i == default})
        cmd += ["-f", "hls", "-hls_time", str(SEGMENT), "-hls_playlist_type", "event", "-hls_segment_type", "fmp4",
                "-hls_flags", "independent_segments+temp_file", "-hls_fmp4_init_filename", "init.mp4",
                "-hls_segment_filename", "%v/s%05d.m4s", "-master_pl_name", "master.m3u8",
                "-var_stream_map", " ".join(streams), "%v/index.m3u8"]
        for n in self.subs:
            cmd += ["-map", f"0:s:{n}", "-c:s", "webvtt", "-flush_packets", "1"]
            if self.past:
                cmd += ["-output_ts_offset", f"{self.past:.3f}"]      # (from the key frame, as the picture)
            cmd += ["-f", "webvtt", f"sub{n}.vtt"]
        return cmd

    def run(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        if self.start and self.how["video"] == "copy":
            # (from the key frame it really starts at: the phone's place and the subtitles' times
            # are counted from there)
            key = keyframe_before(self.source, self.start, self.headers)
            if key is not None and self.start - key < 30:
                self.start, self.past = key, (SEEK_PAST if key else 0.0)
        self._log = open(self.folder / "ffmpeg.log", "wb")
        self.proc = subprocess.Popen(self.command(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=self._log, cwd=str(self.folder), **_no_window())
        return self

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def failed(self):
        """Why it stopped early ("" while it's fine)."""
        if self.proc is None or self.proc.poll() in (None, 0):
            return ""
        try:
            text = (self.folder / "ffmpeg.log").read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            text = ""
        return (text.splitlines()[-1] if text else f"ffmpeg stopped ({self.proc.returncode})")[:240]

    def wait_for(self, rel, timeout=25.0):
        """Wait until the job has written `rel` (its playlist, a piece). False when it never comes."""
        path = self.folder / rel
        end = time.time() + timeout
        while time.time() < end:
            if path.is_file() and path.stat().st_size > 0:
                return True
            if self.proc is not None and self.proc.poll() is not None:
                return path.is_file()
            time.sleep(0.15)
        return False

    def ready_until(self):
        """How far (seconds from where it started) the pieces written so far go."""
        try:
            text = (self.folder / "video" / "index.m3u8").read_text(encoding="utf-8")
        except OSError:
            return 0.0
        return sum(float(x) for x in re.findall(r"#EXTINF:([\d.]+)", text))

    def done(self):
        try:
            return "#EXT-X-ENDLIST" in (self.folder / "video" / "index.m3u8").read_text(encoding="utf-8")
        except OSError:
            return False

    def stop(self, remove=True):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        if self._log:
            self._log.close()
            self._log = None
        if remove:
            shutil.rmtree(self.folder, ignore_errors=True)


# ---- subtitles as WebVTT

_TIME = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")


def srt_to_vtt(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    out = ["WEBVTT", ""]
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [x for x in block.split("\n")]
        i = next((k for k, x in enumerate(lines) if "-->" in x), None)
        if i is None:
            continue
        times = _TIME.sub(lambda m: f"{int(m.group(1)):02d}:{m.group(2)}:{m.group(3)}.{m.group(4).ljust(3, '0')}", lines[i])
        body = [re.sub(r"\{\\[^}]*\}", "", x) for x in lines[i + 1:]]       # ({\an8}-style codes)
        out += [times.split(" X1:")[0]] + body + [""]
    return "\n".join(out)


def clean_vtt(text):
    """A WebVTT file as sites serve it, made safe to show: its cues kept, nothing else."""
    text = text.replace("\r\n", "\n").lstrip("﻿")
    if not text.lstrip().startswith("WEBVTT"):
        return srt_to_vtt(text)
    return text


def to_vtt(source, headers=None, stream=None, timeout=60):
    """Any subtitle file (or a video's subtitle track `stream`) as WebVTT text, via ffmpeg."""
    ffmpeg, _ = find()
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", *_header_args(headers), "-i", source]
    if stream is not None:
        cmd += ["-map", f"0:s:{stream}"]
    cmd += ["-c:s", "webvtt", "-f", "webvtt", "-"]
    r = subprocess.run(cmd, capture_output=True, timeout=timeout, **_no_window())
    if r.returncode != 0:
        raise RuntimeError((r.stderr or b"").decode("utf-8", "replace").strip()[-200:] or "couldn't read the subtitles")
    return r.stdout.decode("utf-8", "replace")
