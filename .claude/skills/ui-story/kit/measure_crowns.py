#!/usr/bin/env python3
"""measure_crowns.py: measure the top of the head off the matte and work out the PIP geometry for THIS take.

Every take sits differently in frame, so the split layout is solved per take, never copied from another
one. It samples the matte every --step seconds (inside the split windows when --layout is given), finds the
crown (the highest row with more than 6 solid matte pixels), and then:

  crop_y       the source row at the top of the PIP layer, chosen so the HIGHEST crown sits --headroom px
               below the top of the head band (the band above the PIP edge where the head breaks out)
  crown_out    where that crown lands in the output frame
  zone_bottom  crown_out - 35: split card ink must stay above this line. Put it in the composition as
               :root{--zone-bottom: <zone_bottom>px}

Output geometry: x = inset..1080-inset, scale = (1080 - 2 x inset) / (1080 / zoom), and a source row y lands
at (pip_top - overhang) + (y - crop_y) x scale. composite_v2.py --geometry reads the file this writes.

    python measure_crowns.py --matte matte.mp4 --out geometry.json [--layout layout.resolved.json]
        [--pip-top 1000] [--zoom 1.0] [--overhang 320] [--headroom 60] [--step 0.5]
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

W, H = 1080, 1920
INSET, RADIUS = 90, 56


def crowns(matte, step):
    """(time, crown row) for one matte frame every `step` seconds, each the frame at its time, since the samples
    are kept or dropped by that time. ffmpeg's fps filter rounds to the nearest slot by default and keeps the last
    frame of each, which would make every sample nearly half a step later than its time."""
    proc = subprocess.Popen(["ffmpeg", "-nostdin", "-v", "error", "-i", str(matte), "-vf",
                             f"fps=1/{step}:round=up,format=gray", "-f", "rawvideo", "-"], stdout=subprocess.PIPE)
    out, k = [], 0
    try:
        while True:
            buf = proc.stdout.read(W * H)
            if len(buf) < W * H:
                break
            frame = np.frombuffer(buf, np.uint8).reshape(H, W)
            rows = np.where((frame > 128).sum(axis=1) > 6)[0]
            if len(rows):
                out.append((round(k * step, 3), int(rows[0])))
            k += 1
    finally:
        proc.stdout.close()
        proc.wait()
    return out


def main():
    ap = argparse.ArgumentParser(description="Crowns off the matte, and the PIP geometry for this take.")
    ap.add_argument("--matte", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--layout", help="layout.resolved.json: measure inside the split windows only")
    ap.add_argument("--pip-top", type=int, default=1000)
    ap.add_argument("--zoom", type=float, default=1.0)
    ap.add_argument("--overhang", type=int, default=320)
    ap.add_argument("--headroom", type=int, default=60, help="output px between the head band top and the highest crown")
    ap.add_argument("--step", type=float, default=0.5)
    a = ap.parse_args()

    found = crowns(a.matte, a.step)
    if a.layout:
        windows = [w for w in json.loads(Path(a.layout).read_text())["windows"] if w["mode"] == "split"]
        found = [(t, c) for t, c in found if any(w["t0"] <= t < w["t1"] for w in windows)] or found
    if not found:
        print("No person found in the matte. Check the matte frames, then measure again.")
        return 1
    rows = [c for _, c in found]
    top, median, low = min(rows), int(np.median(rows)), max(rows)
    crop_w = int(round(W / a.zoom))
    crop_w -= crop_w % 2
    scale = (W - 2 * INSET) / crop_w
    band_top = a.pip_top - a.overhang
    crop_y = max(0, int(round(top - a.headroom / scale)))
    crop_h = int(round(((H - a.pip_top) + a.overhang) / scale))
    crop_y = min(crop_y, H - crop_h)
    to_out = lambda y: band_top + (y - crop_y) * scale
    crown_out = int(round(to_out(top)))
    geometry = {"pip_top": a.pip_top, "zoom": a.zoom, "crop_y": crop_y, "overhang": a.overhang, "inset": INSET,
                "radius": RADIUS, "crown_src_min": top, "crown_src_median": median, "crown_src_max": low,
                "crown_out": crown_out, "zone_bottom": crown_out - 35, "samples": len(rows)}
    Path(a.out).write_text(json.dumps(geometry, indent=1))
    print(f"crowns over {len(rows)} samples: highest y{top}, median y{median}, lowest y{low} (source rows)")
    print(f"geometry: pip top {a.pip_top}, zoom {a.zoom}, crop-y {crop_y}, overhang {a.overhang} -> {a.out}")
    print(f"the highest crown lands at y{crown_out}; split card ink stays above y{crown_out - 35}")
    print(f"put this in the composition: :root{{--zone-bottom: {crown_out - 35}px}}")
    if to_out(low) > a.pip_top - 40:
        print(f"WARN: on the lowest frames the crown lands at y{to_out(low):.0f}, close to the PIP edge at y{a.pip_top}; "
              "the head barely breaks out there. Raise the overhang or lower --pip-top.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
