#!/usr/bin/env python3
"""review_frames.py: a contact sheet of a finished video, for the review step to look at.

    uv run python bin/review_frames.py <final.mp4> [--fps 1] [--cols 8] [--out <folder>]

It writes two images into the video's review/ folder (work/<video>/review/ for work/<video>/final.mp4):
  sheet.jpg   one frame every 1/fps seconds, tiled left to right, each tile stamped with its time
  open.jpg    the frames at 0.2, 1, 2, 3, 4 and 5 seconds side by side: the open as a viewer with the sound
              off meets it

Times are on the finished video's own timeline, after the speed-up.
"""

from __future__ import annotations

import argparse
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

OPEN_TIMES = (0.2, 1, 2, 3, 4, 5)
TILE_W = 180  # a 1080x1920 frame at a sixth of its size
MAX_HEIGHT = 4800


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout.strip()
    return float(out or 0)


def stamp(t: float, fps: float = 1.0) -> str:
    """A tile's time, never shared with another tile at this rate: whole seconds as "3s", others as "1.5s"."""
    places = 1 if fps <= 10 else 2
    return f"{t:.{places}f}".rstrip("0").rstrip(".") + "s"


def label(tile, text):
    from PIL import ImageDraw, ImageFont

    try:
        font = ImageFont.load_default(size=max(14, tile.width // 9))
    except TypeError:  # an older Pillow without sized default fonts
        font = ImageFont.load_default()
    draw = ImageDraw.Draw(tile)
    box = draw.textbbox((0, 0), text, font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    draw.rectangle([4, 4, 4 + w + 10, 4 + h + 10], fill=(0, 0, 0))
    draw.text((9, 9 - box[1]), text, fill=(255, 255, 255), font=font)
    return tile


def frames_at_rate(video: Path, fps: float, folder: Path) -> list[Path]:
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(video), "-vf", f"fps={fps},scale={TILE_W}:-2",
                    "-q:v", "3", str(folder / "f%04d.jpg")], check=True)
    return sorted(folder.glob("f*.jpg"))


def frame_at(video: Path, t: float, out: Path, width: int) -> Path | None:
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
                    "-vf", f"scale={width}:-2", str(out)], check=True)
    return out if out.is_file() else None


def sheet(images: list[Path], times: list[float], cols: int, out: Path, fps: float = 1.0):
    from PIL import Image

    tiles = [label(Image.open(p).convert("RGB"), stamp(t, fps)) for p, t in zip(images, times)]
    w, h = tiles[0].size
    rows = math.ceil(len(tiles) / cols)
    scale = min(1.0, MAX_HEIGHT / (rows * h))
    if scale < 1.0:
        w, h = int(w * scale), int(h * scale)
        tiles = [t.resize((w, h)) for t in tiles]
    board = Image.new("RGB", (cols * w + (cols + 1) * 4, rows * h + (rows + 1) * 4), (24, 24, 24))
    for i, tile in enumerate(tiles):
        board.paste(tile, (4 + (i % cols) * (w + 4), 4 + (i // cols) * (h + 4)))
    board.save(out, quality=88)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="A contact sheet of a finished video for review.")
    ap.add_argument("video", type=Path)
    ap.add_argument("--fps", type=float, default=1.0, help="frames per second of video on the sheet (default 1)")
    ap.add_argument("--cols", type=int, default=8)
    ap.add_argument("--out", type=Path, help="the folder to write into (default: review/ beside the video)")
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    if not a.video.is_file():
        print(f"{a.video} does not exist, so there is nothing to review.")
        return 1
    if a.fps <= 0:
        print("--fps must be above 0, such as 1.")
        return 1
    out = a.out or a.video.resolve().parent / "review"
    out.mkdir(parents=True, exist_ok=True)
    length = duration(a.video)
    with tempfile.TemporaryDirectory(prefix="review-") as tmp:
        folder = Path(tmp)
        tiles = frames_at_rate(a.video, a.fps, folder)
        if not tiles:
            print(f"No frames could be read from {a.video}.")
            return 1
        times = [i / a.fps for i in range(len(tiles))]
        sheet(tiles, times, a.cols, out / "sheet.jpg", a.fps)
        opens, open_times = [], []
        for t in OPEN_TIMES:
            if t < length:
                frame = frame_at(a.video, t, folder / f"open-{t}.jpg", 360)
                if frame:
                    opens.append(frame)
                    open_times.append(t)
        if opens:
            sheet(opens, open_times, len(opens), out / "open.jpg")
    print(f"{len(tiles)} frames on {out / 'sheet.jpg'}")
    if opens:
        print(f"the open: {out / 'open.jpg'}")
    return 0


if __name__ == "__main__":
    if shutil.which("ffmpeg") is None:
        print("ffmpeg is not installed. Install it (on a Mac: brew install ffmpeg), then try again.")
        sys.exit(1)
    sys.exit(main())
