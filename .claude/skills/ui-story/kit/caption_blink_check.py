#!/usr/bin/env python3
"""caption_blink_check.py: fail a render whose burned-in captions blank for a single frame between groups.

It reads the caption band (x 90 to 990, y 1460 to 1700 by default), measures its contrast on every frame and
counts frames whose contrast drops below 60% of BOTH neighbours: a one-frame hole in the captions, which
reads as a flicker. Real silences blank for many frames and are not counted.

    python caption_blink_check.py <render.mp4> [--max 0] [--band y0,y1]

Exit 0 when the count is at most --max, else 1.
"""
import argparse
import subprocess
import sys

import numpy as np

W = 225  # the 900 px band, a quarter of its width
BAND = (1460, 1700)


def blink_times(video, band=BAND):
    y0, y1 = band
    h = (y1 - y0) // 4
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", video, "-vf", f"crop=900:{h * 4}:90:{y0},scale={W}:{h},format=gray",
         "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, h * W).astype(np.float32)
    rate = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate",
                           "-of", "csv=p=0", video], capture_output=True, text=True, check=True).stdout.strip()
    num, den = (rate.split("/") + ["1"])[:2]
    fps = float(num) / float(den)
    sd = frames.std(axis=1)
    hits = [i for i in range(1, len(sd) - 1)
            if min(sd[i - 1], sd[i + 1]) > 12 and sd[i] < 0.6 * min(sd[i - 1], sd[i + 1])]
    return fps, len(sd), [round(i / fps, 3) for i in hits]


def main():
    ap = argparse.ArgumentParser(description="Fail on one-frame holes in the captions.")
    ap.add_argument("video")
    ap.add_argument("--max", type=int, default=0, help="blank frames allowed (default 0)")
    ap.add_argument("--band", default=None, help="y0,y1 of the band to read (default 1460,1700)")
    a = ap.parse_args()
    band = BAND
    if a.band:
        try:
            y0, y1 = (int(v) for v in a.band.split(","))
        except ValueError:
            ap.error("--band wants y0,y1, such as 1460,1700")
        if not 0 <= y0 < y1 <= 1920 or y1 - y0 < 8:
            ap.error("--band must lie inside 0..1920 and be at least 8 px tall")
        band = (y0, y1)
    fps, n, hits = blink_times(a.video, band)
    status = "PASS" if len(hits) <= a.max else "FAIL"
    where = f" (y {band[0]}-{band[1]})" if band != BAND else ""
    print(f"{status} caption blink{where}: {len(hits)} one-frame blanks in {n} frames at {fps:g} fps"
          + (f" at {hits[:12]}" if hits else ""))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
