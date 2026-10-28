"""Shared helpers for heavy media work: the video encoder, the free-disk check and the heavy-job slots.

Renders, encodes, transcription and matting are heavy. On a laptop, running four of them at once fills
the memory and the machine stalls, so every heavy job runs inside `heavy_slot()`: at most HEAVY_SLOTS
(2 unless the environment says otherwise) run at once, across every process. Each slot is a lock file in
the lock folder work/.slots, held with the operating system's file lock. The system gives the lock to one
process at a time and drops it the moment that process ends, even when it crashes, so a slot can never be
held twice and a dead job never keeps one.

A slot is held by a process, not by a call or a thread: threads in one process share its one slot. A heavy
step inside another one (a render inside a build) reuses the slot it is already in, and so does any program
it starts, because the slot's path travels in the KIT_HEAVY_SLOT environment variable.

Command line:

    python -m kit.media run -- <command...>    run a command inside a heavy slot
    python -m kit.media free                   print the free disk space
    python -m kit.media encoder                print the video encoder arguments
    python -m kit.media probe <file>           print a file's size, frame rate, duration and frame count
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from kit import config
from kit.config import KitError

__all__ = [
    "HEAVY_SLOTS",
    "MIN_FREE_GB",
    "SLOT_ENV",
    "MediaInfo",
    "NotEnoughDisk",
    "SlotTimeout",
    "encoder_args",
    "free_gb",
    "heavy_slot",
    "main",
    "probe",
    "require_free",
]

HEAVY_SLOTS = 2  # the default when the HEAVY_SLOTS environment variable is not set
MIN_FREE_GB = 5.0  # a heavy job does not start with less free disk than this
SLOT_ENV = "KIT_HEAVY_SLOT"
_GIB = 1024**3


class NotEnoughDisk(KitError):
    """There is not enough free disk to start a heavy job."""


class SlotTimeout(KitError):
    """No heavy-job slot came free in the time allowed."""


# --- the encoder -----------------------------------------------------------------------------------


@functools.lru_cache(maxsize=None)
def _has_encoder(name: str) -> bool:
    """True when this machine's ffmpeg was built with the named encoder."""
    try:
        listing = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=30
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return any(line.split()[1:2] == [name] for line in listing.splitlines() if line.strip())


def _apple_silicon() -> bool:
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def encoder_args(*, bitrate: str = "30M", crf: int = 14) -> list[str]:
    """The ffmpeg video encoder arguments for this machine.

    On a Mac with Apple silicon it is the hardware encoder, h264_videotoolbox, at `bitrate`. Everywhere
    else, or when KIT_FORCE_X264=1, it is libx264 at `crf`. Both write 8-bit 4:2:0, which every
    platform plays.
    """
    if os.environ.get("KIT_FORCE_X264", "").strip() != "1" and _apple_silicon() and _has_encoder("h264_videotoolbox"):
        return ["-c:v", "h264_videotoolbox", "-b:v", bitrate, "-profile:v", "high", "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p"]


# --- free disk -------------------------------------------------------------------------------------


def free_gb(path: Path | None = None) -> float:
    """Free space, in GB (1024**3 bytes), on the disk that holds `path` (the kit folder by default)."""
    target = Path(path) if path is not None else config.kit_root()
    while not target.exists() and target != target.parent:
        target = target.parent
    return shutil.disk_usage(target).free / _GIB


def require_free(gb: float = 5.0, path: Path | None = None) -> None:
    """Stop with a plain sentence when less than `gb` GB of disk is free."""
    free = free_gb(path)
    if free < gb:
        raise NotEnoughDisk(
            f"Only {free:.1f} GB free on this disk, and a heavy job needs at least {gb:g} GB. "
            "Free some space (empty the Trash, or delete old renders in work/), then try again."
        )


# --- heavy slots -----------------------------------------------------------------------------------


def _slots_dir() -> Path:
    return config.kit_root() / "work" / ".slots"


def _limit() -> int:
    raw = os.environ.get("HEAVY_SLOTS", "").strip()
    try:
        return max(1, int(raw)) if raw else HEAVY_SLOTS
    except ValueError:
        return HEAVY_SLOTS


if os.name == "nt":  # Windows is best effort: the same idea with msvcrt's byte lock
    import msvcrt

    def _lock(fd: int) -> bool:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass

else:
    import fcntl

    def _lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


def _try_take(folder: Path, limit: int, label: str) -> tuple[Path, int] | None:
    """The first free slot, locked and labelled, as (its lock file, the open descriptor). None when all are busy."""
    folder.mkdir(parents=True, exist_ok=True)
    for number in range(1, limit + 1):
        path = folder / f"slot{number}.lock"
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        if not _lock(fd):
            os.close(fd)
            continue
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()} {time.strftime('%H:%M:%S')} {label}\n".encode("utf-8", "replace"))
        return path, fd
    return None


@contextmanager
def heavy_slot(*, label: str = "", timeout: float | None = None, poll: float = 5.0) -> Iterator[Path]:
    """Hold one of the shared heavy-job slots while the block runs, then give it back.

    It waits for a slot when all of them are busy (printing one line to say so), and raises SlotTimeout
    if `timeout` seconds pass first. Once it has a slot it checks for MIN_FREE_GB of free disk, and
    raises NotEnoughDisk (after giving the slot back) when there is less. The slot belongs to the process:
    a nested call, another thread of the same process, and any program it starts all share it.
    """
    held = os.environ.get(SLOT_ENV)
    if held:
        yield Path(held)
        return

    folder, limit = _slots_dir(), _limit()
    label = label or " ".join(sys.argv[:1] + sys.argv[1:4])
    started, told = time.monotonic(), False
    taken = _try_take(folder, limit, label)
    while taken is None:
        if timeout is not None and time.monotonic() - started >= timeout:
            raise SlotTimeout(
                f"All {limit} heavy-job slots stayed busy for {timeout:g} seconds. "
                "Wait for the running render or encode to finish, then try again."
            )
        if not told:
            print(f"note: waiting for a heavy-job slot, because {limit} heavy jobs are already running.", flush=True)
            told = True
        time.sleep(poll if timeout is None else max(0.01, min(poll, timeout)))
        taken = _try_take(folder, limit, label)

    slot, fd = taken
    os.environ[SLOT_ENV] = str(slot)
    try:
        require_free(MIN_FREE_GB)
        yield slot
    finally:
        os.environ.pop(SLOT_ENV, None)
        try:
            os.ftruncate(fd, 0)
            _unlock(fd)
        finally:
            os.close(fd)


# --- probing ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class MediaInfo:
    width: int
    height: int
    fps: float
    duration: float
    frames: int | None
    has_audio: bool


def _rate(text: str) -> float:
    if "/" in text:
        top, bottom = text.split("/", 1)
        return float(top) / float(bottom) if float(bottom) else 0.0
    return float(text or 0)


def probe(path: Path) -> MediaInfo:
    """Width, height, frame rate, duration, frame count and whether there is sound, from ffprobe.

    Width and height are as displayed: a phone video stored sideways with a rotation flag comes back
    upright, the way ffmpeg decodes it.
    """
    path = Path(path)
    if not path.is_file():
        raise KitError(f"{path} does not exist, so it cannot be read.")
    done = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    if done.returncode != 0:
        raise KitError(f"{path.name} is not a video or sound file ffmpeg can read.")
    data = json.loads(done.stdout)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = any(s.get("codec_type") == "audio" for s in data.get("streams", []))
    duration = float(data.get("format", {}).get("duration") or 0.0)
    if video is None:
        return MediaInfo(0, 0, 0.0, duration, None, audio)
    width, height = int(video.get("width", 0)), int(video.get("height", 0))
    rotation = 0
    for side in video.get("side_data_list", []) or []:
        if "rotation" in side:
            rotation = int(float(side["rotation"]))
    rotation = rotation or int(float((video.get("tags") or {}).get("rotate", 0) or 0))
    if abs(rotation) % 180 == 90:
        width, height = height, width
    frames = video.get("nb_frames")
    return MediaInfo(
        width=width,
        height=height,
        fps=_rate(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0"),
        duration=duration,
        frames=int(frames) if str(frames or "").isdigit() else None,
        has_audio=audio,
    )


# --- command line -----------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m kit.media", description="Heavy-job slots, free disk and the encoder.")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run a command inside a heavy-job slot")
    run.add_argument("argv", nargs=argparse.REMAINDER, help="-- then the command and its arguments")
    sub.add_parser("free", help="print the free disk space in GB")
    sub.add_parser("encoder", help="print the video encoder arguments for this machine")
    probe_cmd = sub.add_parser("probe", help="print a file's size, frame rate, duration and frame count")
    probe_cmd.add_argument("file", type=Path)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    try:
        if args.command == "free":
            print(f"{free_gb():.1f} GB free")
            return 0
        if args.command == "encoder":
            print(" ".join(encoder_args()))
            return 0
        if args.command == "probe":
            info = probe(args.file)
            print(f"{info.width}x{info.height}, {info.fps:g} fps, {info.duration:.3f} s, "
                  f"{info.frames if info.frames is not None else 'unknown'} frames, "
                  f"{'with' if info.has_audio else 'no'} sound")
            return 0
        command = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
        if not command:
            print("Give the command to run after --, such as: python -m kit.media run -- npx hyperframes render ...")
            return 2
        with heavy_slot(label=" ".join(command[:3])):
            return subprocess.run(command).returncode
    except KitError as problem:
        print(problem)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
