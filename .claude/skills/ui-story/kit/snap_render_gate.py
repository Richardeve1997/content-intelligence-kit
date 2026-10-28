#!/usr/bin/env python3
"""snap_render_gate.py: compare `hyperframes snapshot` frames with the RENDERED graphics layer (gfx.webm).

The renderer seeks the timeline out of order before it captures, which can undo a fromTo() whose opacity
lives only in its FROM values; `snapshot` seeks forward once and never sees it. This gate runs after the
graphics render and before the composite: any snapshot the render does not reproduce stops the ship.

How: one VP9-with-alpha decode per probe pulls the render frames k-1, k and k+1 for every
`frame-NN-at-<t>s.png` in the snapshot folder. Both sides are flattened over mid grey, a pixel differs when
any channel is off by more than --tol, the difference mask is eroded 3x3 (dropping encoder edge noise and
keeping solid shapes), and a probe fails when more than --max-px pixels survive against ALL three render
frames (a probe on a scene or caption swap can resolve a frame apart in the two seek paths; a missing
element differs in all three).

The caption band (y 1490 to 1660 by default) is left out: captions have their own gate on the finished
file (caption_blink_check.py).

    python snap_render_gate.py <snap dir> <gfx.webm> [--fps 60] [--tol 48] [--max-px 1500] [--ignore-y 1490-1660] [--out <dir>]

Exit 1 on any failing probe; with --out it writes a side-by-side and the mask for each failure.
"""
import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile

from PIL import Image, ImageChops, ImageFilter


def flat(image):
    image = image.convert("RGBA")
    grey = Image.new("RGBA", image.size, (128, 128, 128, 255))
    return Image.alpha_composite(grey, image).convert("RGB")


def main():
    ap = argparse.ArgumentParser(description="Snapshots against the rendered graphics layer.")
    ap.add_argument("snaps")
    ap.add_argument("gfx")
    ap.add_argument("--fps", type=float, default=60)
    ap.add_argument("--tol", type=int, default=48)
    ap.add_argument("--max-px", type=int, default=1500)
    ap.add_argument("--out", default=None)
    ap.add_argument("--ignore-y", default="1490-1660", help="the y band to skip (captions), '' for none")
    a = ap.parse_args()
    snaps = []
    for path in glob.glob(os.path.join(a.snaps, "frame-*-at-*s.png")):
        found = re.search(r"-at-([\d.]+)s\.png$", path)
        if found:
            snaps.append((float(found.group(1)), path))
    snaps.sort()
    if not snaps:
        print("snap_render_gate: no snapshots in", a.snaps)
        sys.exit(1)
    index = [int(round(t * a.fps)) for t, _ in snaps]
    if a.out:
        os.makedirs(a.out, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="srg-", dir=a.out or None)
    frames = {}
    try:
        for k in sorted(set(index)):
            k0 = max(0, k - 1)
            pattern = os.path.join(tmp, f"g{k}_%d.png")
            cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{(k0 - 0.5) / a.fps:.6f}" if k0 else "0",
                   "-c:v", "libvpx-vp9", "-i", a.gfx, "-frames:v", str(k + 2 - k0), "-pix_fmt", "rgba", pattern]
            done = subprocess.run(cmd, capture_output=True, text=True)
            if done.returncode != 0:
                print("snap_render_gate: ffmpeg failed:", done.stderr[-800:])
                sys.exit(1)
            for i in range(k + 2 - k0):
                frame = pattern % (i + 1)
                if os.path.exists(frame):
                    frames[k0 + i] = frame
            if k not in frames:
                print(f"snap_render_gate: could not decode render frame {k}")
                sys.exit(1)
        bad = 0
        for (t, snap_path), k in zip(snaps, index):
            snap_image = flat(Image.open(snap_path))
            best = None
            for j in (k, k - 1, k + 1):
                if j not in frames:
                    continue
                rendered = flat(Image.open(frames[j]))
                if snap_image.size != rendered.size:
                    rendered = rendered.resize(snap_image.size)
                channels = ImageChops.difference(snap_image, rendered).split()
                mask = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
                mask = mask.point(lambda v: 255 if v > a.tol else 0).filter(ImageFilter.MinFilter(3))
                if a.ignore_y:
                    y0, y1 = (int(v) for v in a.ignore_y.split("-"))
                    mask.paste(0, (0, y0, mask.width, y1))
                count = mask.histogram()[255]
                if best is None or count < best[0]:
                    best = (count, mask, rendered, j)
            count, mask, rendered, kk = best
            box = mask.getbbox()
            flag = "FAIL" if count > a.max_px else "ok"
            print(f"{flag:4} t={t:7.2f}s frame {k:5d} (best {kk - k:+d}): {count:6d} px differ" + (f" in {box}" if box and count else ""))
            if count > a.max_px:
                bad += 1
                if a.out:
                    w, h = snap_image.width // 2, snap_image.height // 2
                    sheet = Image.new("RGB", (w * 3, h), "white")
                    sheet.paste(snap_image.resize((w, h)), (0, 0))
                    sheet.paste(rendered.resize((w, h)), (w, 0))
                    sheet.paste(mask.convert("RGB").resize((w, h)), (2 * w, 0))
                    sheet.save(os.path.join(a.out, f"srg-fail-{t:.2f}.png"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"snap_render_gate: {len(snaps)} snapshot(s) against the render, {bad} failing (tol {a.tol}, max {a.max_px} px after a 3x3 erode)")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
