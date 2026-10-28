#!/usr/bin/env python3
"""check_static.py: fail when a split or canvas graphic zone sits unchanged for more than --max seconds.

A card that is "on screen long enough" can still bore a viewer when nothing in it moves. Lint, the scene
checks and the reading-load audit cannot see that; this can. Face windows are skipped (the speaker is the
motion there).

Method: it samples the video at --fps and sorts each frame into face, split or canvas: the top-left corner
shows the creator's canvas colour in split and canvas, and the lower middle (below the caption band) shows
footage in split and canvas in canvas. It then compares the graphic zone of each frame with the one before.
A zone counts as still while it shows neither a mean change of 0.35 grey levels nor at least 12 pixels that
changed by more than 24 (so a cursor, a click ring or a pulsing tick counts as motion). It prints every
still run longer than --max and exits 1 if there is any. A run is allowed only when the speech binds it.

    python check_static.py <final.mp4> [--max 3.0] [--fps 5]
"""
import argparse
import subprocess
import sys

import numpy as np

W, H = 270, 480  # a quarter of 1080x1920: a moving cursor or a pulsing tick is still a few pixels here
NEAR = 18  # how far from the canvas colour a pixel can be and still count as canvas


def canvas_rgb():
    """The creator's canvas colour from their tokens, or None to fall back on 'a light, plain corner'."""
    try:
        from kit import brand, config

        value = brand.colour_tokens(config.load_profile())["canvas"].lstrip("#")
        return np.array([int(value[i : i + 2], 16) for i in (0, 2, 4)], dtype=np.int16)
    except Exception:
        return None


def frames(video, fps):
    """The video sampled `fps` times a second at a quarter of its size, as an (n, H, W, 3) array. Sample k is the
    frame at k/fps, the time a still run is reported at. ffmpeg's fps filter rounds to the nearest slot by default
    and keeps the last frame of each, which would put every sample nearly half a slot later than its time."""
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(video), "-vf", f"fps={fps}:round=up,scale={W}:{H}",
                             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    buf = proc.stdout.read()
    proc.stdout.close()
    proc.wait()
    n = len(buf) // (W * H * 3)
    return np.frombuffer(buf[: n * W * H * 3], dtype=np.uint8).reshape(n, H, W, 3).astype(np.int16)


def main():
    ap = argparse.ArgumentParser(description="Fail on a graphic zone that holds still too long.")
    ap.add_argument("video")
    ap.add_argument("--max", type=float, default=3.0)
    ap.add_argument("--fps", type=int, default=5)
    a = ap.parse_args()
    canvas = canvas_rgb()

    F = frames(a.video, a.fps)
    n = len(F)

    def is_canvas(pixels):
        if canvas is None:
            mean = pixels.reshape(-1, 3).mean(0)
            return mean.min() > 215 and (mean.max() - mean.min()) < 14
        return np.abs(pixels.reshape(-1, 3).mean(0) - canvas).max() < NEAR

    def mode(f):
        if not is_canvas(f[4:20, 4:20]):
            return "FACE"
        lower = f[415:450, 80:190].reshape(-1, 3)
        if canvas is None:
            off = ((lower.max(1) - lower.min(1)) > 25) | (lower.mean(1) < 150)
        else:
            off = np.abs(lower - canvas).max(1) > 30
        return "SPLIT" if off.mean() > 0.5 else "CANVAS"

    cls = [mode(f) for f in F]
    moved = np.zeros(n)
    for i in range(1, n):
        if cls[i] == "SPLIT":
            x, y = F[i, 38:164, 22:248], F[i - 1, 38:164, 22:248]
        elif cls[i] == "CANVAS":
            x, y = F[i, 38:380, 22:248], F[i - 1, 38:380, 22:248]
        else:
            continue
        d = np.abs(x - y).max(axis=2)
        moved[i] = 1.0 if (d.mean() >= 0.35 or int((d > 24).sum()) >= 12) else 0.0

    bad, i = [], 0
    while i < n:
        if cls[i] in ("SPLIT", "CANVAS") and i > 0 and moved[i] < 0.5 and cls[i - 1] == cls[i]:
            j = i
            while j + 1 < n and cls[j + 1] == cls[i] and moved[j + 1] < 0.5:
                j += 1
            length = (j - i + 1) / a.fps
            if length > a.max:
                bad.append((round(i / a.fps, 1), round((j + 1) / a.fps, 1), cls[i], round(length, 1)))
            i = j + 1
        else:
            i += 1

    for start, end, layout, length in bad:
        print(f"STATIC {layout} {start}s..{end}s ({length}s with nothing moving)")
    print(f"{'FAIL' if bad else 'PASS'} static-hold check: {len(bad)} run(s) over {a.max}s in {n / a.fps:.1f}s of video")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
