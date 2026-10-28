#!/usr/bin/env python3
"""postcheck.py: the gates on a finished Reel, and the review sheet. Read-only on final.mp4.

    python postcheck.py <video>

On work/<video>/final.mp4 it checks:
  format     1080x1920, 60 fps, H.264 picture and AAC sound
  loudness   -13 LUFS within 1 LU, true peak at or below -1.5 dBTP
  captions   no one-frame hole in the karaoke captions (caption_blink_check.py)
  static     no split or canvas graphic zone holding still over 3 s (check_static.py)
and writes review/safe/ (frames with the phone's crop marked, ig_safe_check.py) and review/sheet.jpg plus
review/open.jpg (bin/review_frames.py). Look at the sheet and the open strip before calling it done.
Exit 1 when a gate fails.
"""
import argparse
import subprocess
import sys
from pathlib import Path

from kit import config, finish, media

KIT = Path(__file__).resolve().parent


def gate(name, cmd):
    done = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    last = (done.stdout.strip().splitlines() or [done.stderr.strip()[-300:]])[-1]
    return name, done.returncode == 0, last


def main() -> int:
    ap = argparse.ArgumentParser(description="The gates on a finished Reel, and its review sheet.")
    ap.add_argument("slug")
    a = ap.parse_args()
    try:
        folder = config.work_dir(a.slug)
    except ValueError as problem:
        print(problem)
        return 1
    final = folder / "final.mp4"
    if not final.is_file():
        print(f"work/{a.slug}/final.mp4 is missing. Ship it first.")
        return 1
    py = sys.executable
    rows = []

    info = media.probe(final)
    shape_ok = (info.width, info.height) == (1080, 1920) and round(info.fps) == 60 and info.has_audio
    rows.append(("format", shape_ok, f"{info.width}x{info.height}, {info.fps:g} fps, {info.duration:.1f} s"))
    level = finish.measure(final)
    loud_ok = abs(level.integrated - finish.TARGET["I"]) <= 1.0 and level.true_peak <= finish.TARGET["TP"]
    rows.append(("loudness", loud_ok, f"{level.integrated:.1f} LUFS, true peak {level.true_peak:.1f} dBTP, "
                                      f"range {level.lra:.1f} LU"))
    rows.append(gate("captions", [py, KIT / "caption_blink_check.py", final]))
    rows.append(gate("static", [py, KIT / "check_static.py", final, "--max", "3.0"]))

    review = folder / "review"
    at = [str(round(info.duration * k / 13, 1)) for k in range(1, 13)]
    rows.append(gate("safe area", [py, KIT / "ig_safe_check.py", final, "--at", *at, "--mark", "--out", review / "safe"]))
    rows.append(gate("review sheet", [py, config.kit_root() / "bin" / "review_frames.py", final]))

    width = max(len(name) for name, _, _ in rows)
    for name, ok, detail in rows:
        print(f"{'PASS' if ok else 'FAIL'}  {name.ljust(width)}  {detail}")
    print(f"Look at {review / 'sheet.jpg'} and {review / 'open.jpg'} before calling it done.")
    return 0 if all(ok for _, ok, _ in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
