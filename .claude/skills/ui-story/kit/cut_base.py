#!/usr/bin/env python3
"""cut_base.py: a clean 1080x1920, 60 fps base from the keep segments of a raw take, in one encode.

It seeks into each keep window (so only the kept seconds are decoded), joins them with the concat filter
(one encode, constant frame rate, which the matte needs), and makes every zoom as a crop of the full
resolution take, never an upscale: a 4K take leaves room for a 3% push or a 1.18x punch-in with real pixels.

Opening push-in: the first segment eases from 1.00 to 1.03 over 3 s (cubic ease-out), anchored at the TOP
of the frame. A top anchor pushes the head down, which keeps the room above it that the split layout needs.
Every later segment is the whole filmed frame at 1.00.

A take that is not 9:16 is cropped to 9:16 first (from the centre, or from the top for a frame taller
than 9:16). A phone's HDR take (HLG or PQ) is tone-mapped to standard range when ffmpeg can. That needs the
zscale filter, which Homebrew's plain ffmpeg lacks. Without it the cut still goes ahead, with the HDR frames
left as they are and one sentence saying that their colours may look washed out and how to fix that.

EDL JSON: {"keep": [[s, e], ...]}, seconds on the raw take, in playback order. A keep entry may also be
{"raw": "<other take>", "s": s, "e": e} to take that segment from another file.
Optional keys:
  "pushin": false            no push-in anywhere
  "pushin_segments": [0, 1]  segments that each start with their own 3 s push-in (default [0])
  "emphasis": [[t0, t1, z, fy, ease], ...]   face zooms on the OUTPUT timeline (layout.py writes them).
      A punch-in eases in over `ease` seconds (default 0.35, cubic ease-out) to z, holds, and cuts back
      at t1. An ease longer than 1 s becomes a slow push: a sine ease from 1.00 to z across the window.
      fy is the row of the take held still, as a fraction of its height (0.385, about the eyes, by default).
      Keep zooms out of split windows: the matte is cut from the plain base, so a zoom there would make
      the head cut-out slip.

    python cut_base.py <raw take> <edl.json> <out.mp4> [--no-hwaccel]

The frames go to a part file beside the output (base.part.mp4 for base.mp4), which takes the output's place
only when ffmpeg finishes without an error. A cut that stops part way (ctrl-C, a crash, an ffmpeg that fails)
leaves the earlier base as it was, or none, and never half a base that prep.py would take for a finished one.

Check after: avg_frame_rate 60/1, nb_frames = round(duration x 60), and a frame reads upright.
"""
import argparse
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

from kit import config, media

RAMP_Z = "1+0.03*(1-pow(1-min(on/180\\,1)\\,3))"
FACE_ROW = 0.385
PUNCH_EASE = 0.35
FULL = "scale=1080:1920:flags=lanczos"
HDR_TRANSFERS = ("arib-std-b67", "smpte2084")  # HLG (iPhone) and PQ (HDR10)
# What the creator hears when an HDR take meets an ffmpeg without zscale. The cut goes on without the colour fix.
NO_ZSCALE = (
    "This take is HDR and this ffmpeg cannot convert HDR colours (it has no zscale filter), so they may look "
    "washed out; film again with HDR video turned off on the phone, or install the full ffmpeg (python3 "
    "bin/doctor.py shows the command), and until then the cut goes on without the colour fix."
)


def ramp():
    return f"zoompan=z='{RAMP_Z}':d=1:x='iw/2-(iw/zoom/2)':y='0':s=1080x1920:fps=60"


def _ease(t0, ease):
    """0 to 1 progress for a window starting at t0: a cubic ease-out over `ease` s (a punch-in), or a sine
    ease-in-out when ease is over 1 s (a slow push)."""
    x = f"min((on/60-{t0:.3f})/{ease:.3f}\\,1)"
    if ease > 1.0:
        return f"(1-cos(PI*{x}))/2"
    return f"(1-pow(1-{x}\\,3))"


def emphasis_zoompan(base_zoom, windows, hold=1.0):
    """zoompan whose zoom is the push-in (a function of `on`) times the eased zoom windows (segment seconds).
    The face row stays put; below `hold` (the push-in's end zoom) the frame stays anchored at the top."""
    terms = "+".join(
        f"between(on/60\\,{w[0]:.3f}\\,{w[1]:.3f})*({w[2] - 1:.4f})*{_ease(w[0], w[4])}" for w in windows)
    row = windows[0][3]
    return (f"zoompan=z='({base_zoom})*(1+{terms})':d=1:x='iw/2-(iw/zoom/2)'"
            f":y='max(0\\,{row:.4f}*ih*(1-{hold}/zoom))':s=1080x1920:fps=60")


def _probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=color_transfer",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.strip().lower()
    info = media.probe(Path(path))
    return info.width, info.height, out


def _filter_listing():
    """ffmpeg's own list of its filters, or nothing when ffmpeg cannot be asked."""
    try:
        return subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _has_filter(name):
    return any(line.split()[1:2] == [name] for line in _filter_listing().splitlines() if line.strip())


def prefix_for(raw):
    """Filters that make a take 9:16 and standard range before the zooms."""
    width, height, transfer = _probe(raw)
    parts = []
    if any(t in transfer for t in HDR_TRANSFERS):
        if _has_filter("zscale"):
            parts.append("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
                         "zscale=t=bt709:m=bt709:r=tv,format=yuv420p")
        else:
            print(NO_ZSCALE)  # no tone map in the chain: a filter ffmpeg does not have would stop the whole cut
    ratio = width / height if height else 9 / 16
    if ratio > 9 / 16 + 0.01:
        parts.append("crop=trunc(ih*9/16/2)*2:ih")
    elif ratio < 9 / 16 - 0.01:
        parts.append("crop=iw:trunc(iw*16/9/2)*2:0:0")
    return ",".join(parts) + ("," if parts else "")


def main(argv=None):
    ap = argparse.ArgumentParser(description="A clean 1080x1920 60 fps base from the keep segments of a take.")
    ap.add_argument("raw")
    ap.add_argument("edl")
    ap.add_argument("out")
    ap.add_argument("--no-hwaccel", action="store_true", help="decode on the CPU (Macs decode in hardware by default)")
    a = ap.parse_args(argv)

    edl = json.loads(Path(a.edl).read_text())
    keep = []
    for k in edl["keep"]:
        if isinstance(k, dict):
            keep.append((round(float(k["s"]), 3), round(float(k["e"]), 3), k.get("raw", a.raw)))
        else:
            keep.append((round(float(k[0]), 3), round(float(k[1]), 3), a.raw))
    keep = [k for k in keep if k[1] > k[0]]
    if not keep:
        print("The EDL has no keep segments.")
        return 1
    pushin = edl.get("pushin", True)
    ramps = set(edl.get("pushin_segments", [0]))
    emph = []
    for e in edl.get("emphasis", []):
        row = float(e[3]) if len(e) > 3 else FACE_ROW
        if not 0.0 < row <= 1.0:
            print(f"The zoom row {row} must be a fraction of the frame height, such as {FACE_ROW}.")
            return 1
        emph.append((float(e[0]), float(e[1]), float(e[2]), row, float(e[4]) if len(e) > 4 else PUNCH_EASE))
    seg_t0, acc = [], 0.0
    for s0, e0, _ in keep:
        seg_t0.append(acc)
        acc += e0 - s0

    hwaccel = not a.no_hwaccel and platform.system() == "Darwin"
    prefixes = {raw: prefix_for(raw) for raw in {k[2] for k in keep}}
    cmd = ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-stats"]
    for s, e, raw in keep:
        if hwaccel:
            cmd += ["-hwaccel", "videotoolbox"]
        cmd += ["-ss", f"{s:.3f}", "-t", f"{e - s:.3f}", "-i", raw]

    parts, labels = [], ""
    for i, (s, e, raw) in enumerate(keep):
        vf = ramp() if (pushin and i in ramps) else FULL
        local = [(max(0.0, t0 - seg_t0[i]), min(e - s, t1 - seg_t0[i]), z, row, ease)
                 for t0, t1, z, row, ease in emph if t1 > seg_t0[i] and t0 < seg_t0[i] + (e - s)]
        local = [w for w in local if w[1] - w[0] >= 1 / 60]  # a sliver at a join is float noise, not a zoom
        if local:
            ramped = pushin and i in ramps
            vf = emphasis_zoompan(RAMP_Z if ramped else "1", local, 1.03 if ramped else 1.0)
            print(f"[cut_base] segment {i}: zooms " + ", ".join(
                f"{w[0]:.2f}-{w[1]:.2f}s x{w[2]}" + (" slow" if w[4] > 1 else "") for w in local))
        parts.append(f"[{i}:v]setpts=PTS-STARTPTS,fps=60,{prefixes[raw]}{vf},setsar=1[v{i}]")
        fade_at = max(0.0, (e - s) - 0.015)
        parts.append(f"[{i}:a]asetpts=PTS-STARTPTS,afade=t=in:st=0:d=0.015,afade=t=out:st={fade_at:.3f}:d=0.015[a{i}]")
        labels += f"[v{i}][a{i}]"
    parts.append(f"{labels}concat=n={len(keep)}:v=1:a=1[vc][a]")
    parts.append("[vc]format=yuv420p[v]")

    total = sum(e - s for s, e, _ in keep)
    print(f"[cut_base] {len(keep)} segments, {total:.2f}s kept, push-in {pushin} on segments {sorted(ramps)} -> {a.out}")
    out = Path(a.out)
    part = out.with_name(f"{out.stem}.part{out.suffix}")
    cmd += ["-filter_complex", "; ".join(parts), "-map", "[v]", "-map", "[a]", "-r", "60", "-fps_mode", "cfr",
            *media.encoder_args(bitrate="30M", crf=14), "-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", str(part)]
    rc = 1
    try:
        with media.heavy_slot(label=f"cut_base {out.name}"):
            rc = subprocess.run(cmd).returncode
    except config.KitError as problem:
        print(problem)
        return 1
    finally:
        if rc != 0:  # stopped, crashed or failed: the earlier base stays, and no half one is left beside it
            part.unlink(missing_ok=True)
    if rc:
        print(f"ffmpeg stopped with status {rc}, so {out.name} was not replaced.")
        return rc
    os.replace(part, out)
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=avg_frame_rate,nb_frames,width,height:format=duration", "-of", "default=nw=1", a.out],
                           capture_output=True, text=True).stdout
    print(probe.strip().replace("\n", " "))
    return 0


if __name__ == "__main__":
    sys.exit(main())
