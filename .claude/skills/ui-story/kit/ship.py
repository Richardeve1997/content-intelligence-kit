#!/usr/bin/env python3
"""ship.py: the composite, the finish and the speech gate, from work/<video>/build/ to work/<video>/final.mp4.

    python ship.py <video> [--music auto|none|<file>] [--mood <folder>] [--speed 1.15]
        [--music-lufs -24] [--mix-peak 0.74] [--fade-in 0.3] [--fade-out 1.2]
    python ship.py <video> --test FROM TO      a composite of base seconds FROM..TO, to check before the full run

It reads from build/: zbase.mp4 (or base.mp4 when there are no face zooms), matte.mp4, layout.resolved.json,
geometry.json, gfx.webm (the rendered graphics), clean.wav and blocks.json (the gap trims). Then:
  1. composite_v2.py -> build/composite.mov (picture plus PCM sound, the gap trims applied)
  2. kit.finish -> build/final.next.mp4 at 1.15x, the bed at -24 LUFS under the voice, peaks held at 0.74,
     -13 LUFS. --music auto keeps the bed this video already has (kit.music.for_video), or picks one from
     me/music and logs it; the ledger keeps one row per video, so every re-ship sounds the same.
  3. kit.qa_captions on that file against build/captions/transcript.json, slowed back to normal speed,
     with the composite as the double-check. A lost word stops the ship.
  4. Only a render that passed becomes final.mp4. The review's pass marker (review/review.json) is deleted
     first, because it described the previous final. A failed render stays as build/final.next.mp4 to
     listen to, and the previous final.mp4 is left as it was.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

from kit import config, finish, music, qa_captions, transcribe

KIT = Path(__file__).resolve().parent


def need(path: Path, made_by: str) -> Path:
    if not path.is_file():
        raise SystemExit(f"{path.relative_to(path.parents[1])} is missing. Make it with {made_by} first.")
    return path


def choose_bed(slug: str, choice: str, mood: str | None) -> Path | None:
    """The bed for this video: none, a named file, or (auto) the bed it already has or a fresh pick."""
    if choice == "none":
        return None
    if choice != "auto":
        return Path(choice)
    bed = music.for_video(slug, mood)
    if bed is None:
        print("No track in me/music, so this Reel ships with no bed. Add tracks to a mood folder to give it one.")
    return bed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Composite, finish and check one UI story Reel.")
    ap.add_argument("slug")
    ap.add_argument("--test", type=float, nargs=2, metavar=("FROM", "TO"), help="composite a short window only")
    ap.add_argument("--music", default="auto", help="auto (the video's bed, or a pick from me/music), none, or a file")
    ap.add_argument("--mood", help="the me/music folder to pick from")
    for flag, value in finish.HOUSE.items():
        ap.add_argument("--" + flag.replace("_", "-"), type=float, default=value)
    a = ap.parse_args(argv)
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

    transcript = need(build / "captions" / "transcript.json", "build_captions.py --captions-dir build/captions")
    composite = build / "composite.mov"
    done = subprocess.run([py, str(KIT / "composite_v2.py"), *map(str, common),
                           "--blocks", str(need(build / "blocks.json", "make_blocks.py")), "--out", str(composite)])
    if done.returncode:
        return done.returncode

    candidate, final = build / "final.next.mp4", folder / "final.mp4"
    try:
        bed = choose_bed(a.slug, a.music, a.mood)
        finish.finish(composite, candidate, speed=a.speed, music=bed, music_lufs=a.music_lufs, mix_peak=a.mix_peak,
                      fade_in=a.fade_in, fade_out=a.fade_out)
        if bed is not None:
            music.log(bed, a.slug)  # one row per video: a re-ship with the same bed adds nothing
        problems = qa_captions.check(candidate, transcribe.load(transcript), speed=a.speed, fallback=composite)
    except config.KitError as problem:
        print(problem)
        return 1
    if problems:
        print(f"FAIL: the speech gate found {len(problems)} lost word run(s):")
        for line in problems:
            print(f"- {line}")
        print(f"The failed render is {candidate}; final.mp4 was left as it was.")
        return 1
    print("PASS: the speech gate heard every caption word.")
    (folder / "review" / "review.json").unlink(missing_ok=True)  # it passed the previous final, not this one
    os.replace(candidate, final)
    print(f"shipped {final}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
