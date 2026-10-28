#!/usr/bin/env python3
"""ship.py: the composite, the finish and the speech gate, from work/<video>/build/ to work/<video>/final.mp4.

    python ship.py <video> [--music auto|none|<file>] [--mood <folder>] [--speed 1.15]
        [--music-lufs -24] [--mix-peak 0.74] [--fade-in 0.3] [--fade-out 1.2]
    python ship.py <video> --test FROM TO      a composite of base seconds FROM..TO, to check before the full run

It reads from build/: zbase.mp4 (or base.mp4 when there are no face zooms), matte.mp4, layout.resolved.json,
geometry.json, gfx.webm (the rendered graphics), clean.wav and blocks.json (the gap trims). Then:
  1. composite_v2.py -> build/composite.mov (picture plus PCM sound, the gap trims applied)
  2. kit.finish -> final.mp4 at 1.15x, the bed at -24 LUFS under the voice, peaks held at 0.74, -13 LUFS.
     --music auto picks a bed from me/music (skipping the last one used) and logs it, or ships with none.
  3. kit.qa_captions on final.mp4 against build/captions/transcript.json, slowed back to normal speed,
     with the composite as the double-check. A lost word fails the ship.
The previous final.mp4, if there was one, is kept as build/final.previous.mp4 until the new one passes.
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from kit import config, finish, music, qa_captions, transcribe

KIT = Path(__file__).resolve().parent


def need(path: Path, made_by: str) -> Path:
    if not path.is_file():
        raise SystemExit(f"{path.relative_to(path.parents[1])} is missing. Make it with {made_by} first.")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description="Composite, finish and check one UI story Reel.")
    ap.add_argument("slug")
    ap.add_argument("--test", type=float, nargs=2, metavar=("FROM", "TO"), help="composite a short window only")
    ap.add_argument("--music", default="auto", help="auto (pick from me/music), none, or a file")
    ap.add_argument("--mood", help="the me/music folder to pick from")
    for flag, value in finish.HOUSE.items():
        ap.add_argument("--" + flag.replace("_", "-"), type=float, default=value)
    a = ap.parse_args()
    try:
        folder = config.work_dir(a.slug)
    except ValueError as problem:
        print(problem)
        return 1
    build = folder / "build"
    base = build / "zbase.mp4" if (build / "zbase.mp4").is_file() else build / "base.mp4"
    common = ["--base", need(base, "prep.py"), "--matte", need(build / "matte.mp4", "prep.py"),
              "--layout", need(build / "layout.resolved.json", "layout.py"),
              "--geometry", need(build / "geometry.json", "measure_crowns.py"),
              "--gfx", need(build / "gfx.webm", "the graphics render"),
              "--audio", need(build / "clean.wav", "prep.py")]
    py = sys.executable
    if a.test:
        out = build / f"test-{a.test[0]:g}-{a.test[1]:g}.mp4"
        done = subprocess.run([py, str(KIT / "composite_v2.py"), *map(str, common), "--from", str(a.test[0]),
                               "--to", str(a.test[1]), "--out", str(out)])
        if done.returncode == 0:
            print(f"test window -> {out}")
        return done.returncode

    composite = build / "composite.mov"
    done = subprocess.run([py, str(KIT / "composite_v2.py"), *map(str, common),
                           "--blocks", str(need(build / "blocks.json", "make_blocks.py")), "--out", str(composite)])
    if done.returncode:
        return done.returncode

    if a.music == "none":
        bed = None
    elif a.music == "auto":
        bed = music.pick(a.mood)
        if bed is None:
            print("No track in me/music, so this Reel ships with no bed. Add tracks to a mood folder to give it one.")
    else:
        bed = Path(a.music)
    final = folder / "final.mp4"
    previous = build / "final.previous.mp4"
    if final.is_file():
        shutil.move(str(final), str(previous))
    try:
        finish.finish(composite, final, speed=a.speed, music=bed, music_lufs=a.music_lufs, mix_peak=a.mix_peak,
                      fade_in=a.fade_in, fade_out=a.fade_out)
    except config.KitError as problem:
        print(problem)
        if previous.is_file() and not final.is_file():
            shutil.move(str(previous), str(final))
        return 1
    if bed is not None and a.music == "auto":
        music.log(bed, a.slug)

    transcript = build / "captions" / "transcript.json"
    if not transcript.is_file():
        print("build/captions/transcript.json is missing, so the speech gate could not run. Run build_captions.py "
              "with --captions-dir build/captions.")
        return 1
    problems = qa_captions.check(final, transcribe.load(transcript), speed=a.speed, fallback=composite)
    if problems:
        print(f"FAIL: the speech gate found {len(problems)} lost word run(s):")
        for line in problems:
            print(f"- {line}")
        return 1
    print("PASS: the speech gate heard every caption word.")
    previous.unlink(missing_ok=True)
    print(f"shipped {final}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
