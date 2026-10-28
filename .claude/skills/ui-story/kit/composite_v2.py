#!/usr/bin/env python3
"""composite_v2.py: the UI story compositor. Canvas, rounded PIP, head break-out and graphics in one pass.

Layers, bottom to top, all 1080x1920 at 60 fps, timed in BASE seconds (before the gap trims):
  1. the base, full frame. This is what face windows show; their punch-ins and slow pushes are already
     in the zoomed base made by cut_base.py.
  2. canvas windows: the grid canvas over everything below.
  3. split windows: the grid canvas, a soft shadow, and the rounded, bottom-flush PIP of the base
     (x inset..1080-inset, top corners rounded).
  4. the HyperFrames graphics layer (alpha): cards, product footage, chips, captions.
  5. split windows: the matted HEAD, breaking out above the PIP's top edge. It is painted after the
     graphics, so a card that reaches into that band tucks behind the head and the face always wins.
     Only the band above the PIP edge is painted again, so captions and chips inside the PIP stay on top.

The canvas is the creator's canvas colour with a 2 px grid every 90 px in their accent at 9%, read from
their profile. It is drawn here, not in HyperFrames: the graphics layer is transparent everywhere, which
is what lets one render serve all three layouts.

    python composite_v2.py --base <zbase.mp4> --matte <matte.mp4> --layout <layout.resolved.json>
        (--geometry <geometry.json> | --pip-top 1000 --zoom 1.0 --crop-y 300 [--overhang 320])
        [--gfx <gfx.webm|snapshot.png>] [--audio <clean.wav>] [--blocks <blocks.json>]
        [--from S --to S] --out <composite.mov | test.mp4 | frame.png>

geometry.json comes from measure_crowns.py. --crown-src Y prints where a crown at source row Y lands in the
output (split card ink must stay above that minus 35). An output ending in .png renders ONE frame at
--from, in about two seconds, from a HyperFrames snapshot PNG as --gfx: check a layout that way before
rendering the graphics. A .mov keeps the sound as PCM, so the finish makes the only lossy delivery encode.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from kit import brand, config, media

FPS = 60
SHADOW_BLUR, SHADOW_ALPHA, SHADOW_DY = 30, 0.16, 10
GRID_ALPHA = 0.09


def sh(cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()


def duration(path):
    return float(sh(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]))


def nb_frames(path):
    value = sh(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=nb_frames",
                "-of", "csv=p=0", str(path)])
    return int(value) if value.isdigit() else None


def rgb(colour):
    colour = colour.lstrip("#")
    if len(colour) == 3:
        colour = "".join(ch * 2 for ch in colour)
    return tuple(int(colour[i : i + 2], 16) for i in (0, 2, 4))


def ffmpeg_colour(colour, alpha=None):
    """#rrggbb -> 0xrrggbb[@alpha], the form ffmpeg's colour options take."""
    value = "0x" + "".join(f"{c:02x}" for c in rgb(colour))
    return f"{value}@{alpha}" if alpha is not None else value


def build_masks(w, h, radius, ink, out_dir: Path):
    from PIL import Image, ImageDraw, ImageFilter

    out_dir.mkdir(parents=True, exist_ok=True)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h + radius], radius=radius, fill=255)
    mask_path = out_dir / f"pipmask_{w}x{h}_r{radius}.png"
    mask.save(mask_path)
    pad = SHADOW_BLUR * 3
    shadow = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([pad, pad, pad + w - 1, pad + h + radius], radius=radius,
                                             fill=(*rgb(ink), int(255 * SHADOW_ALPHA)))
    shadow = shadow.filter(ImageFilter.GaussianBlur(SHADOW_BLUR))
    shadow_path = out_dir / f"pipshadow_{w}x{h}_r{radius}.png"
    shadow.save(shadow_path)
    return mask_path, shadow_path


def mode_expr(windows, mode, offset):
    """Half-open [t0, t1) per window, shifted by the test-window offset and moved half a frame early, so an
    exact frame time never sits on a boundary."""
    early = 0.5 / FPS
    parts = [f"gte(t,{w['t0'] - offset - early:.4f})*lt(t,{w['t1'] - offset - early:.4f})"
             for w in windows if w["mode"] == mode and w["t1"] - offset > 0]
    return "+".join(parts) if parts else "0"


def brand_colours():
    tokens = brand.colour_tokens(config.load_profile())
    return tokens["canvas"], tokens["accent"], tokens["ink"]


def main():
    ap = argparse.ArgumentParser(description="Canvas, rounded PIP, head break-out and graphics in one pass.")
    ap.add_argument("--base", required=True)
    ap.add_argument("--matte")
    ap.add_argument("--layout", required=True)
    ap.add_argument("--gfx")
    ap.add_argument("--audio")
    ap.add_argument("--blocks")
    ap.add_argument("--out", required=True)
    ap.add_argument("--geometry", help="geometry.json from measure_crowns.py (the flags below override it)")
    ap.add_argument("--pip-top", type=int)
    ap.add_argument("--inset", type=int)
    ap.add_argument("--radius", type=int)
    ap.add_argument("--zoom", type=float)
    ap.add_argument("--crop-y", type=int, help="source row at the top of the PIP layer, overhang included")
    ap.add_argument("--overhang", type=int)
    ap.add_argument("--matte-blur", type=float, default=0.8)
    ap.add_argument("--no-head", action="store_true", help="keep the creator boxed inside the rounded card")
    ap.add_argument("--grid", type=int, default=90)
    ap.add_argument("--grid-t", type=int, default=2)
    ap.add_argument("--from", dest="t_from", type=float, default=0.0)
    ap.add_argument("--to", dest="t_to", type=float)
    ap.add_argument("--crown-src", type=float, action="append", default=[])
    ap.add_argument("--work", default=None, help="folder for the mask PNGs (default: next to --out)")
    a = ap.parse_args()

    geometry = json.loads(Path(a.geometry).read_text()) if a.geometry else {}
    pick = lambda flag, key, default: flag if flag is not None else geometry.get(key, default)
    pip_top = int(pick(a.pip_top, "pip_top", 1000))
    inset = int(pick(a.inset, "inset", 90))
    radius = int(pick(a.radius, "radius", 56))
    zoom = float(pick(a.zoom, "zoom", 1.0))
    overhang = int(pick(a.overhang, "overhang", 320))
    crop_y_wanted = pick(a.crop_y, "crop_y", None)
    if crop_y_wanted is None:
        raise SystemExit("Give --crop-y, or --geometry with the file measure_crowns.py wrote.")

    try:
        canvas, accent, ink = brand_colours()
    except config.KitError as problem:
        raise SystemExit(str(problem)) from None

    info = media.probe(Path(a.base))
    if (info.width, info.height) != (1080, 1920):
        raise SystemExit(f"The base is {info.width}x{info.height}; the compositor needs a 1080x1920 base from cut_base.py.")
    windows = json.loads(Path(a.layout).read_text())["windows"]
    D = duration(a.base)
    png_out = a.out.lower().endswith(".png")
    t0 = a.t_from
    t1 = a.t_to if a.t_to is not None else (t0 + 1.0 / FPS if png_out else D)
    L = max(1.0 / FPS, t1 - t0)
    windowed = t0 > 0 or t1 < D - 1e-3

    pip_w = 1080 - 2 * inset
    pip_h = 1920 - pip_top
    ov = 0 if a.no_head else overhang
    crop_w = int(round(1080 / zoom))
    crop_w -= crop_w % 2
    crop_x = (1080 - crop_w) // 2
    scale = pip_w / crop_w
    scaled_h = pip_h + ov
    crop_h = int(round(scaled_h / scale))
    crop_y = max(0, min(int(crop_y_wanted), 1920 - crop_h))
    if crop_y != int(crop_y_wanted):
        print(f"note: crop-y moved from {crop_y_wanted} to {crop_y} so the PIP crop ({crop_w}x{crop_h}) fits the frame",
              file=sys.stderr)
    to_out = lambda y: pip_top - ov + (y - crop_y) * scale
    print(f"[composite] pip {pip_w}x{pip_h} at ({inset},{pip_top}) r{radius} | zoom {zoom} scale {scale:.3f} "
          f"| source crop {crop_w}x{crop_h} at ({crop_x},{crop_y}) | head band y {pip_top - ov}..{pip_top}")
    for crown in a.crown_src:
        print(f"[composite] source crown y{crown:.0f} -> output y{to_out(crown):.0f}  "
              f"(split card ink limit about {to_out(crown) - 35:.0f})")

    if not a.no_head:
        if not a.matte:
            raise SystemExit("--matte is needed for the head break-out (or pass --no-head)")
        frames_base, frames_matte = nb_frames(a.base), nb_frames(a.matte)
        if frames_base and frames_matte and abs(frames_base - frames_matte) > 2:
            raise SystemExit(f"The matte does not match the base: {frames_base} base frames, {frames_matte} matte frames. "
                             "A matte belongs to one cut; make it again from this base.")

    work = Path(a.work) if a.work else Path(a.out).resolve().parent
    mask_p, shadow_p = build_masks(pip_w, pip_h, radius, ink, work / "_masks")

    ss = ["-ss", f"{t0:.4f}"] if t0 > 0 else []
    tt = ["-t", f"{L:.4f}"]
    cmd = ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-stats"]
    cmd += ss + tt + ["-i", a.base]
    idx = {"base": 0}
    n = 1
    if not a.no_head:
        cmd += ss + tt + ["-i", a.matte]
        idx["matte"] = n
        n += 1
    cmd += ["-loop", "1", "-framerate", str(FPS), "-t", f"{L:.4f}", "-i", str(mask_p)]
    idx["mask"] = n
    n += 1
    cmd += ["-loop", "1", "-framerate", str(FPS), "-t", f"{L:.4f}", "-i", str(shadow_p)]
    idx["shadow"] = n
    n += 1
    if a.gfx:
        if a.gfx.lower().endswith(".png"):
            cmd += ["-loop", "1", "-framerate", str(FPS), "-t", f"{L:.4f}", "-i", a.gfx]
        else:
            decoder = ["-c:v", "libvpx-vp9"] if a.gfx.lower().endswith(".webm") else []  # the native vp9 decoder drops alpha
            cmd += decoder + ss + tt + ["-i", a.gfx]
        idx["gfx"] = n
        n += 1
    if a.audio and not png_out:
        cmd += ss + tt + ["-i", a.audio]
        idx["audio"] = n
        n += 1

    SPLIT = mode_expr(windows, "split", t0)
    CANVAS = mode_expr(windows, "canvas", t0)
    g = [f"[{idx['base']}:v]setpts=PTS-STARTPTS,fps={FPS},format=rgba,split=2[full][pipsrc]",
         f"[pipsrc]crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale={pip_w}:{scaled_h}:flags=lanczos,setsar=1"
         + (",split=2[pcard][phead]" if ov else "[pcard]"),
         f"[pcard]crop={pip_w}:{pip_h}:0:{ov}[cardwin]",
         f"[{idx['mask']}:v]format=gray[pipmask]",
         "[cardwin][pipmask]alphamerge[faceA]"]
    if ov:
        g.append(f"[phead]crop={pip_w}:{ov}:0:0[headwin]")
        g.append(f"[{idx['matte']}:v]setpts=PTS-STARTPTS,fps={FPS},crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"
                 f"scale={pip_w}:{scaled_h}:flags=lanczos,format=gray,gblur=sigma={a.matte_blur},"
                 f"crop={pip_w}:{ov}:0:0[headmask]")
        g.append("[headwin][headmask]alphamerge[headA]")
    g.append(f"color=c={ffmpeg_colour(canvas)}:s=1080x1920:r={FPS}:d={L:.4f},"
             f"drawgrid=w={a.grid}:h={a.grid}:t={a.grid_t}:c={ffmpeg_colour(accent, GRID_ALPHA)},format=rgba,split=2[cv1][cv2]")
    sx, sy = inset - SHADOW_BLUR * 3, pip_top - SHADOW_BLUR * 3 + SHADOW_DY
    g.append(f"[{idx['shadow']}:v]format=rgba[shadow]")
    g.append(f"[cv1][shadow]overlay=x={sx}:y={sy}[cvs]")
    g.append(f"[cvs][faceA]overlay=x={inset}:y={pip_top}[splitbg]")
    g.append(f"[full][cv2]overlay=0:0:enable='{CANVAS}'[b1]")
    g.append(f"[b1][splitbg]overlay=0:0:enable='{SPLIT}'[b2]")
    last = "[b2]"
    if "gfx" in idx:
        g.append(f"[{idx['gfx']}:v]setpts=PTS-STARTPTS,fps={FPS},format=yuva444p[gl]")
        g.append(f"{last}[gl]overlay=0:0:format=auto:eof_action=pass[b3]")
        last = "[b3]"
    if ov:
        g.append(f"{last}[headA]overlay=x={inset}:y={pip_top - ov}:enable='{SPLIT}'[b4]")
        last = "[b4]"
    g.append(f"{last}format=yuv420p[vc]")
    has_audio = "audio" in idx
    if has_audio:
        g.append(f"[{idx['audio']}:a]asetpts=PTS-STARTPTS,aformat=sample_rates=48000:channel_layouts=stereo[ac]")

    if a.blocks and not windowed and not png_out:
        kept = json.loads(Path(a.blocks).read_text())["A"]
        snap = lambda t: round(round(float(t) * FPS) / FPS, 6)
        pieces = [(snap(s), D if e == "end" else snap(e)) for s, e in kept]
        k = len(pieces)
        g.append(f"[vc]split={k}" + "".join(f"[vs{i}]" for i in range(k)))
        if has_audio:
            g.append(f"[ac]asplit={k}" + "".join(f"[as{i}]" for i in range(k)))
        labels = ""
        for i, (s, e) in enumerate(pieces):
            g.append(f"[vs{i}]trim={s}:{e},setpts=PTS-STARTPTS[v{i}]")
            labels += f"[v{i}]"
            if has_audio:
                g.append(f"[as{i}]atrim={s}:{e},asetpts=PTS-STARTPTS[a{i}]")
                labels += f"[a{i}]"
        g.append(f"{labels}concat=n={k}:v=1:a={1 if has_audio else 0}[v]" + ("[a]" if has_audio else ""))
        print(f"[composite] gap trims: kept {sum(e - s for s, e in pieces):.3f}s of {D:.3f}s in {k} pieces")
    else:
        g.append("[vc]null[v]")
        if has_audio:
            g.append("[ac]anull[a]")

    cmd += ["-filter_complex", ";\n".join(g), "-map", "[v]"]
    if png_out:
        cmd += ["-frames:v", "1", "-update", "1", a.out]
        rc = subprocess.run(cmd).returncode
        if rc == 0:
            print(f"[composite] frame t={t0:.3f}s -> {a.out}")
        return rc

    if has_audio:
        cmd += ["-map", "[a]"]
    cmd += [*media.encoder_args(bitrate="40M", crf=12), "-r", str(FPS)]
    if has_audio:
        cmd += (["-c:a", "pcm_s16le", "-ar", "48000"] if a.out.lower().endswith(".mov")
                else ["-c:a", "aac", "-b:a", "256k"])
    cmd += [a.out]
    try:
        with media.heavy_slot(label=f"composite {Path(a.out).name}"):
            rc = subprocess.run(cmd).returncode
    except config.KitError as problem:
        raise SystemExit(str(problem)) from None
    if rc == 0:
        print(f"[composite] -> {a.out} ({duration(a.out):.3f}s)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
