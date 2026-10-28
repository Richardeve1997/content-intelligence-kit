"""Gets the words spoken in the videos that did well, in the cheapest way that works.

For each recent video from this run, in order of its score:

1. A transcript an earlier run already kept is used as it is.
2. YouTube captions and TikTok subtitles are free, so they are fetched next.
3. What is left is transcribed on this machine with kit.transcribe, one video at a time, each inside
   kit.media.heavy_slot() so a research run never piles onto other heavy work. The video is downloaded to
   a folder under research/, its sound is turned into text, and the download is deleted straight after.
   Only the strongest few are transcribed this way, and the disk is checked before the first one.

A step that cannot run (the speech tools are missing, the disk is nearly full) leaves a plain sentence
and the run carries on with the transcripts it has.
"""

from __future__ import annotations

import concurrent.futures as futures
import importlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from kit.research import common
from kit.research.common import is_usable

LOCAL_LIMIT = 12  # videos transcribed on this machine in one run
FREE_LIMIT = 40  # caption files fetched in one run
MAX_FAILS_IN_A_ROW = 3
CAPTION_BYTES = 2_000_000


@dataclass
class Outcome:
    brain: int = 0  # transcripts taken from earlier runs
    captions: int = 0  # fetched from YouTube or TikTok
    local: int = 0  # transcribed on this machine
    missing: int = 0  # recent videos that still have none
    notices: list[str] = field(default_factory=list)


# --- reading caption files -------------------------------------------------------------------------------


def _clean(text: str) -> str:
    text = re.sub(r"\[(music|applause|laughter)\]", " ", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def parse_json3(raw: bytes) -> str:
    """The text of a YouTube caption file."""
    data = json.loads(raw)
    parts = ["".join(seg.get("utf8", "") for seg in event.get("segs", [])) for event in data.get("events", []) if event.get("segs")]
    return _clean(" ".join(parts))


def parse_vtt(raw: bytes) -> str:
    """The text of a WebVTT subtitle file, with the timings and repeated lines taken out."""
    lines, last = [], None
    for line in raw.decode("utf-8", "ignore").splitlines():
        line = line.strip()
        if not line or line == "WEBVTT" or "-->" in line or line.isdigit() or line.startswith(("NOTE", "Kind:", "Language:")):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        if line != last:
            lines.append(line)
        last = line
    return _clean(" ".join(lines))


# --- fetching the sound ------------------------------------------------------------------------------------


def _with_ytdlp(item: dict, workdir: Path, stem: str) -> Path | None:
    try:
        yt_dlp = importlib.import_module("yt_dlp")
    except ImportError:
        return None
    options = {
        "format": "bestaudio/best",
        "outtmpl": str(workdir / f"{stem}.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}],
    }
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            downloader.download([item["url"]])
    except Exception:
        for leftover in workdir.glob(f"{stem}.*"):
            leftover.unlink(missing_ok=True)
        return None
    wav = workdir / f"{stem}.wav"
    for leftover in workdir.glob(f"{stem}.*"):
        if leftover != wav:
            leftover.unlink(missing_ok=True)
    return wav if wav.exists() else None


def fetch_audio(item: dict, workdir) -> Path | None:
    """Download one video's sound as a 16 kHz mono wav file in `workdir`. None when it cannot be had.

    A direct link (Instagram gives one) is downloaded and converted with ffmpeg. Otherwise yt-dlp fetches the sound.
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    stem = common.safe_name(item["id"])
    wav = workdir / f"{stem}.wav"
    direct = item.get("video_url")
    if direct:
        video = workdir / f"{stem}.mp4"
        try:
            common.http_download(direct, video, timeout=60, limit=common.VIDEO_BYTES)
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(wav)],
                check=True, capture_output=True, timeout=180,
            )  # fmt: skip
            return wav
        except (common.HttpError, OSError, subprocess.SubprocessError):
            wav.unlink(missing_ok=True)
        finally:
            video.unlink(missing_ok=True)
    return _with_ytdlp(item, workdir, stem)


# --- the queue -----------------------------------------------------------------------------------------------


def _sentence(exc: BaseException) -> str:
    return str(exc).strip().rstrip(".") or type(exc).__name__


def _order(video: dict) -> float:
    return -(video.get("outlier") or 0.0)


def _has(video: dict) -> bool:
    return is_usable(video.get("transcript"))


def _fetch_caption(video: dict) -> tuple[dict, str, str] | None:
    if video["platform"] == "youtube" and video.get("caption_url"):
        link, parse, source = video["caption_url"], parse_json3, "YouTube captions"
    elif video["platform"] == "tiktok" and video.get("subtitle_url"):
        link, parse, source = video["subtitle_url"], parse_vtt, "TikTok subtitles"
    else:
        return None
    try:
        text = parse(common.http_bytes(link, timeout=15, limit=CAPTION_BYTES))
    except Exception:
        return None
    return (video, text, source) if is_usable(text) else None


def _transcribe_here(todo: list[dict], workdir: Path, fetch, outcome: Outcome) -> None:
    try:
        transcribe = importlib.import_module("kit.transcribe")
        media = importlib.import_module("kit.media")
    except ImportError:
        outcome.notices.append("Speech to text skipped: the kit's transcription tools are not available in this copy")
        return
    try:
        media.require_free()
    except Exception as exc:
        outcome.notices.append(f"Speech to text skipped: {_sentence(exc)}")
        return

    shutil.rmtree(workdir, ignore_errors=True)  # anything here is left over from a run that was stopped
    workdir.mkdir(parents=True, exist_ok=True)
    failed, in_a_row, last_reason = 0, 0, ""
    for video in todo:
        audio = None
        try:
            audio = fetch(video, workdir)
            if audio is None:
                raise _CouldNotFetch("the video could not be downloaded")
            with media.heavy_slot():
                spoken = transcribe.words(audio, align=False)
            text = " ".join(word.text.strip() for word in spoken if word.text.strip())
            if is_usable(text):
                video["transcript"], video["transcript_source"] = text, "local transcription"
                outcome.local += 1
            in_a_row = 0
        except Exception as exc:
            failed, in_a_row = failed + 1, in_a_row + 1
            last_reason = str(exc) if isinstance(exc, _CouldNotFetch) else common.why(exc)
            if in_a_row >= MAX_FAILS_IN_A_ROW:
                outcome.notices.append(f"Speech to text stopped after {MAX_FAILS_IN_A_ROW} videos in a row failed ({last_reason})")
                return
        finally:
            if audio is not None:
                Path(audio).unlink(missing_ok=True)
    if failed:
        outcome.notices.append(f"Speech to text failed for {common.plural(failed, 'video')} ({last_reason})")


class _CouldNotFetch(Exception):
    """The sound of a video could not be had."""


def attach(videos: list[dict], known: dict, *, local: bool = True, limit: int = LOCAL_LIMIT, workdir, fetch=None) -> Outcome:
    """Give the recent videos from this run their transcripts, changing the video dicts in place.

    `known` maps a video id to {"text", "source"} from earlier runs. `workdir` is where downloads go
    while they are being transcribed; it is emptied after each one and removed at the end.
    """
    fetch = fetch or fetch_audio
    workdir = Path(workdir)
    outcome = Outcome()
    eligible = sorted((v for v in videos if v.get("source") == "run" and v.get("in_window")), key=_order)

    for video in eligible:
        earlier = known.get(video["id"])
        if not _has(video) and earlier and is_usable(earlier.get("text")):
            video["transcript"], video["transcript_source"] = earlier["text"], earlier.get("source") or "an earlier run"
            outcome.brain += 1

    wanting = [v for v in eligible if not _has(v) and (v.get("caption_url") or v.get("subtitle_url"))][:FREE_LIMIT]
    if wanting:
        with futures.ThreadPoolExecutor(max_workers=8) as pool:
            for got in pool.map(_fetch_caption, wanting):
                if got:
                    video, text, source = got
                    video["transcript"], video["transcript_source"] = text, source
                    outcome.captions += 1

    todo = [v for v in eligible if not _has(v)][:limit]
    if todo and local:
        try:
            _transcribe_here(todo, workdir, fetch, outcome)
        finally:
            try:
                workdir.rmdir()  # only removes the folder when it is empty
            except OSError:
                pass
    outcome.missing = sum(1 for v in eligible if not _has(v))
    return outcome
