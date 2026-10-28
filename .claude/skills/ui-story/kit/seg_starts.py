#!/usr/bin/env python3
"""seg_starts.py: measure where each EDL keep segment really starts in a cut_base.py base.

Why: cut_base.py seeks into each keep window and its 60 fps conversion leaves each segment about a frame
longer than the EDL says, so segment k starts about k frames after its EDL time. cut_base places the face
zooms by EDL time, so a zoom written in base seconds lands late by that drift. layout.py --seg-starts
corrects for it.

Method: the drift only grows (about 1 to 1.5 frames per segment), so for join k it searches from the EDL
time minus 2 frames to the EDL time plus (k + 2) x 1.5 frames, decodes that stretch small and grey, and takes
the frame that differs most from the one before it (a change of take is a jump cut). A wider window would
let a fast hand gesture win over the real join.

    python seg_starts.py --base <plain base.mp4> --edl <edl.json> --out seg_starts.json
"""
import argparse
import json
import subprocess
from pathlib import Path

import numpy as np

W, H, FPS = 90, 160, 60


def frames(base, t0, dur):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0, t0):.4f}", "-i", str(base), "-t", f"{dur:.4f}",
                          "-vf", f"scale={W}:{H},format=gray", "-f", "rawvideo", "-"],
                         capture_output=True).stdout
    n = len(raw) // (W * H)
    return np.frombuffer(raw[: n * W * H], np.uint8).reshape(n, H, W).astype(np.int16)


def main():
    ap = argparse.ArgumentParser(description="Where each EDL keep segment really starts in the base.")
    ap.add_argument("--base", required=True)
    ap.add_argument("--edl", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--per-seg", type=float, default=1.5, help="the largest drift per segment, in frames")
    a = ap.parse_args()
    keep = json.loads(Path(a.edl).read_text())["keep"]
    durs = [(float(k["e"]) - float(k["s"])) if isinstance(k, dict) else (float(k[1]) - float(k[0])) for k in keep]
    cum, acc = [], 0.0
    for d in durs:
        cum.append(acc)
        acc += d
    starts = [0.0]
    for i in range(1, len(cum)):
        lo = cum[i] - 3.0 / FPS  # one frame of context before the earliest candidate
        hi = cum[i] + (i + 2) * a.per_seg / FPS
        f = frames(a.base, lo, hi - lo + 2.0 / FPS)
        diff = np.abs(np.diff(f, axis=0)).mean(axis=(1, 2))
        k = int(np.argmax(diff)) + 1
        t = round((round(max(0, lo) * FPS) + k) / FPS, 4)  # the seek point on the frame grid, plus k frames
        ratio = float(diff.max() / (np.median(diff) + 1e-6))
        starts.append(t)
        print(f"  segment {i:2d}: EDL {cum[i]:8.3f}  actual {t:8.3f}  drift {t - cum[i]:+.3f}s  (jump x{ratio:.1f}"
              f"{'  LOW, check it by eye' if ratio < 4 else ''})")
    Path(a.out).write_text(json.dumps({"edl_starts": [round(c, 4) for c in cum], "actual_starts": starts,
                                       "durations": [round(d, 4) for d in durs]}, indent=1))
    print(f"-> {a.out}")


if __name__ == "__main__":
    main()
