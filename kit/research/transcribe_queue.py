"""Gets the words spoken in the videos that did well, in the cheapest way that works.

For each recent video from this run, in order of its score:

1. A transcript an earlier run already kept is used as it is.
2. YouTube captions and TikTok subtitles are free, so they are fetched next.
3. What is left is transcribed on this machine with kit.transcribe, one video at a time, each inside
   kit.media.heavy_slot() so a research run never piles onto other heavy work. The video is downloaded to
   a folder under research/, its sound is turned into text, and the download is deleted straight after.
   Only the strongest few are transcribed this way, and the disk is checked before the first one.

A step that cannot run (the speech tools are missing, the disk is nearly full) leaves a plain sentence
and the run carries on with the transcripts it has. The speech tools come with the video group, so a
research-only install does not have them: that is checked before the first download, and a first answer
of "not installed" from the tools stops the queue after one video, not after twelve. A file the tools
cannot read is that one video's failure, and the rest carry on. Three failed transcriptions in a row stop
the queue, and so do three failed downloads in a row, since each download can wait out a network timeout.
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

from kit.config import KitError
from kit.research import common
from kit.research.common import is_usable

LOCAL_LIMIT = 12  # videos transcribed on this machine in one run
FREE_LIMIT = 40  # caption files fetched in one run
MAX_FAILS_IN_A_ROW = 3
CAPTION_BYTES = 2_000_000
SKIPPED = "Speech to text skipped:"
INSTALL_VIDEO_TOOLS = f"{SKIPPED} install the video tools with uv sync --group video --inexact"
NOT_IN_THIS_COPY = f"{SKIPPED} the kit's transcription tools are not available in this copy"


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
    return common.clause(str(exc).strip() or type(exc).__name__)


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


def _speech_tools():
    """(kit.transcribe, kit.media) when the speech tools can be imported, else (None, the sentence to show).

    The real kit.transcribe imports fine without numpy and only reaches for it once it holds a video, so
    numpy is looked for here, before anything is downloaded. A missing piece of the kit itself is a
    different problem from a missing library, and gets its own sentence.
    """
    try:
        transcribe = importlib.import_module("kit.transcribe")
        media = importlib.import_module("kit.media")
        importlib.import_module("numpy")
    except ImportError as exc:
        if (getattr(exc, "name", None) or "kit").split(".")[0] == "kit":
            return None, NOT_IN_THIS_COPY
        return None, INSTALL_VIDEO_TOOLS
    return (transcribe, media), None


def _transcribe_here(todo: list[dict], workdir: Path, fetch, outcome: Outcome) -> None:
    tools, problem = _speech_tools()
    if problem:
        outcome.notices.append(problem)
        return
    transcribe, media = tools
    try:
        media.require_free()
    except Exception as exc:
        outcome.notices.append(f"{SKIPPED} {_sentence(exc)}")
        return

    shutil.rmtree(workdir, ignore_errors=True)  # anything here is left over from a run that was stopped
    workdir.mkdir(parents=True, exist_ok=True)
    working = False  # becomes True once the speech tools have turned one video into text
    one_file = getattr(transcribe, "AudioProblem", ())  # the error for a single file the tools cannot read
    failed = in_a_row = no_download = downloads_in_a_row = 0
    last_reason = ""
    for video in todo:
        try:
            audio = fetch(video, workdir)
        except Exception:
            audio = None
        if audio is None:
            no_download += 1  # a video that cannot be had says nothing about the speech tools
            downloads_in_a_row += 1
            if downloads_in_a_row >= MAX_FAILS_IN_A_ROW:
                outcome.notices.append(f"Speech to text stopped after {MAX_FAILS_IN_A_ROW} videos in a row could not be downloaded")
                return
            continue
        downloads_in_a_row = 0
        try:
            with media.heavy_slot():
                spoken = transcribe.words(audio, align=False)
            text = " ".join(word.text.strip() for word in spoken if word.text.strip())
        except (ImportError, KitError) as exc:
            if not working and not isinstance(exc, one_file):
                # the very first answer was "not installed", or "no room": it will be the same for every video
                outcome.notices.append(INSTALL_VIDEO_TOOLS if isinstance(exc, ImportError) else f"{SKIPPED} {_sentence(exc)}")
                return
            failed, in_a_row, last_reason = failed + 1, in_a_row + 1, common.clause(common.why(exc))
        except Exception as exc:
            failed, in_a_row, last_reason = failed + 1, in_a_row + 1, common.clause(common.why(exc))
        else:
            working, in_a_row = True, 0
            if is_usable(text):
                video["transcript"], video["transcript_source"] = text, "local transcription"
                outcome.local += 1
        finally:
            Path(audio).unlink(missing_ok=True)
        if in_a_row >= MAX_FAILS_IN_A_ROW:
            outcome.notices.append(f"Speech to text stopped after {MAX_FAILS_IN_A_ROW} videos in a row failed ({last_reason})")
            return
    if failed:
        outcome.notices.append(f"Speech to text failed for {common.plural(failed, 'video')} ({last_reason})")
    if no_download:
        outcome.notices.append(f"{common.plural(no_download, 'video')} could not be downloaded")


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
