#!/usr/bin/env python3
"""margin_scan.py: look at every frame for anything inside the 90 px side margins.

    python margin_scan.py <final.mp4 | frame.png ...> [--skip 3.2-3.6,10-11]

A tall phone crops about 45 px off each side, and anything within 90 px of an edge reads as cut off. The
mini me fields are flat colours that run the full width, so a row is clean when its margins match the field
at the very edge: every pixel in x 40 to 89 is compared with x 30, and every pixel in x 991 to 1039 with
x 1050, on rows 150 to 1900 (the big character and everything around him). A difference of more than 28 grey
levels is content that crossed a margin. The dot grid stops inside the safe lines, so it never counts.

It prints "flagged 0" when every frame is clean, and otherwise the first frames with the x they reached and
every run of flagged frames. It exits 1 when anything is flagged. --skip leaves out time windows (seconds).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

W, H, THRESHOLD = 1080, 1920, 28
TOP, BOTTOM = 150, 1900
IMAGES = {".png", ".jpg", ".jpeg", ".webp"}


def flagged(frame) -> tuple[int | None, int | None]:
    """(the leftmost x on the left, the rightmost x on the right) of content inside the margins, or Nones."""
    import numpy as np

    rows = frame[TOP:BOTTOM].astype(int)
    left = np.abs(rows[:, 40:90] - rows[:, 30:31])
    right = np.abs(rows[:, 991:1040] - rows[:, 1050:1051])
    lx = int(np.nonzero(left.max(0) > THRESHOLD)[0].min() + 40) if left.max() > THRESHOLD else None
    rx = int(np.nonzero(right.max(0) > THRESHOLD)[0].max() + 991) if right.max() > THRESHOLD else None
    return lx, rx


def frames_of(path: Path):
    """(time in seconds or None, grey frame) for a video, or for one image."""
    import numpy as np

    if path.suffix.lower() in IMAGES:
        from PIL import Image

        image = Image.open(path).convert("L")
        if image.size != (W, H):
            raise SystemExit(f"{path.name} is {image.size[0]}x{image.size[1]}, not 1080x1920.")
        yield None, np.asarray(image)
        return
    rate = 60.0
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=avg_frame_rate",
                            "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.strip()
    if "/" in probe:
        top, bottom = probe.split("/")
        rate = float(top) / float(bottom) if float(bottom) else rate
    reader = subprocess.Popen(["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-vf", f"scale={W}:{H}",
                               "-f", "rawvideo", "-pix_fmt", "gray", "-"], stdout=subprocess.PIPE)
    n = 0
    try:
        while True:
            data = reader.stdout.read(W * H)
            if len(data) < W * H:
                break
            yield n / rate, np.frombuffer(data, np.uint8).reshape(H, W)
            n += 1
    finally:
        reader.stdout.close()
        reader.wait()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Find anything inside the 90 px side margins.")
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--skip", default="", help="time windows to leave out, such as 3.2-3.6,10-11")
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    skip = [tuple(float(x) for x in part.split("-")) for part in a.skip.split(",") if part.strip()]
    total, hits = 0, []
    for path in a.files:
        if not path.is_file():
            print(f"{path} does not exist.")
            return 2
        for n, (t, frame) in enumerate(frames_of(path)):
            if t is not None and any(lo <= t < hi for lo, hi in skip):
                continue
            total += 1
            lx, rx = flagged(frame)
            if lx is not None or rx is not None:
                hits.append((path.name, n, t, lx, rx))
    print(f"frames {total} flagged {len(hits)}")
    for name, n, t, lx, rx in hits[:12]:
        when = f"{t:.2f}s" if t is not None else name
        print(f"frame {n} ({when}) left x {lx} right x {rx}")
    runs: list[list] = []
    for name, n, t, lx, rx in hits:
        if runs and runs[-1][0] == name and n == runs[-1][2] + 1:
            runs[-1][2], runs[-1][4] = n, t
        else:
            runs.append([name, n, n, t, t])
    for name, first, last, start, end in runs:
        if start is not None:
            print(f"run frames {first}-{last} ({start:.2f}s to {end:.2f}s) in {name}")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
