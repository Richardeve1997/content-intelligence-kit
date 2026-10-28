#!/usr/bin/env python3
"""ig_safe_check.py: show frames of a Reel the way a tall phone shows them.

A 1080x1920 Reel is the right upload size, but a phone taller than 9:16 fills the screen HEIGHT and crops
the extra width off both sides: about 45 px of the 1080 on each side on a 1179x2556 screen. Nothing in the
upload can stop that, so all content stays inside a 90 px margin (twice the loss), in the cards and in the
footage alike.

    python ig_safe_check.py <render.mp4> --at 3 8 13 [--mark] [--screen 1179x2556] [--out <dir>]

Without --mark each frame is cropped to what the phone shows. With --mark the lost margins are tinted red,
the 90 px safe lines are drawn, and the top band under the status bar is shaded.
"""
import argparse
import subprocess
import sys
from pathlib import Path

W, H = 1080, 1920
VIDEO_FILL = 0.894  # the share of the screen height the video is drawn at
SAFE_X = 90
SAFE_TOP = 150  # the phone status bar sits over about this much of the video


def crop_px(screen_w, screen_h):
    """The pixels cropped from each side, in source pixels, and the display scale, for a screen size."""
    shown_h = screen_h * VIDEO_FILL
    scale = shown_h / H
    over = max(0.0, W * scale - screen_w)
    return int(round(over / 2 / scale)), scale


def main():
    ap = argparse.ArgumentParser(description="Frames of a Reel as a tall phone shows them.")
    ap.add_argument("video", type=Path)
    ap.add_argument("--at", type=float, nargs="+", required=True, help="times in seconds")
    ap.add_argument("--screen", default="1179x2556", help="the phone screen, WxH")
    ap.add_argument("--mark", action="store_true", help="tint the lost margins instead of cropping them off")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()

    sw, sh = (int(v) for v in a.screen.lower().split("x"))
    c, scale = crop_px(sw, sh)
    out = a.out or a.video.parent / f"ig-safe-{a.video.stem}"
    out.mkdir(parents=True, exist_ok=True)
    print(f"screen {sw}x{sh}: video drawn at {scale:.3f}x ({W * scale:.0f}x{H * scale:.0f})")
    print(f"CROPPED PER SIDE : {c}px of {W}   (visible x {c}..{W - c}, usable {W - 2 * c}px)")
    print(f"SAFE_X margin    : {SAFE_X}px ({'OK' if SAFE_X >= c else 'TOO SMALL'}, {SAFE_X - c}px past the loss)")
    for t in a.at:
        target = out / f"t{t:g}.png"
        if a.mark:
            vf = (f"drawbox=x=0:y=0:w={c}:h={H}:color=red@0.45:t=fill,"
                  f"drawbox=x={W - c}:y=0:w={c}:h={H}:color=red@0.45:t=fill,"
                  f"drawbox=x={SAFE_X}:y=0:w=2:h={H}:color=lime@0.9:t=fill,"
                  f"drawbox=x={W - SAFE_X - 2}:y=0:w=2:h={H}:color=lime@0.9:t=fill,"
                  f"drawbox=x=0:y=0:w={W}:h={SAFE_TOP}:color=orange@0.30:t=fill")
        else:
            vf = f"crop={W - 2 * c}:{H}:{c}:0"
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(a.video),
                        "-frames:v", "1", "-vf", vf, str(target)], check=True)
    print(f"{len(a.at)} frame(s) -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
