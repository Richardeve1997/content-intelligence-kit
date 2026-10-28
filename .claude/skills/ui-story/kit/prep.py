#!/usr/bin/env python3
"""prep.py: from a raw take in work/<video>/take/ to everything the layout and graphics are built on.

    python prep.py <video> --edl     transcribe the raw take and propose build/edl.json (then review it)
    python prep.py <video>           cut the base and make the clean voice, the words, the restart check,
                                     the matte and the PIP geometry

Steps and what they write, all in work/<video>/build/:
  --edl     raw.words.json, raw.lines.txt (the take, sentence by sentence) and edl.json (keep segments)
  base      base.mp4: 1080x1920, 60 fps, the keep segments joined, the opening push-in   (cut_base.py)
  voice     clean.wav: the light voice chain                                             (light_voice.py)
  words     base.words.json and base.lines.txt: aligned word times                      (kit.transcribe)
  restarts  restarts.log: hidden false starts to cut                                    (check_restarts.py)
  matte     matte.mp4: the person matte of the base                                      (kit.matte)
  crowns    geometry.json: the PIP geometry for this take                                (measure_crowns.py)

A step is skipped when its output is newer than what it is made from, so a second run picks up where the
first stopped. --force runs every step again. Heavy steps each take a heavy-job slot and check for 5 GB
of free disk. The raw take is only read, through its link in take/; it is never moved, copied or changed.
"""
import argparse
import subprocess
import sys
from pathlib import Path

from kit import config

KIT = Path(__file__).resolve().parent
VIDEO = {".mov", ".mp4", ".m4v", ".mkv"}


def run(*cmd):
    print("$", " ".join(str(c) for c in cmd), flush=True)
    done = subprocess.run([str(c) for c in cmd])
    if done.returncode:
        raise SystemExit(f"That step failed (exit {done.returncode}). Fix it, then run prep again; finished steps are kept.")


def fresh(out: Path, *sources: Path) -> bool:
    return out.is_file() and all(out.stat().st_mtime >= s.stat().st_mtime for s in sources if s.exists())


def find_take(folder: Path, name: str | None) -> Path:
    take = folder / "take"
    files = sorted(p for p in take.iterdir() if p.suffix.lower() in VIDEO) if take.is_dir() else []
    if name:
        files = [p for p in files if p.name == name]
    if not files:
        raise SystemExit(f"There is no take in work/{folder.name}/take/. Link the raw file there (ln -s), never copy it.")
    if len(files) > 1 and not name:
        raise SystemExit("There is more than one take in take/. Name the one to use with --take.")
    if not files[0].exists():
        raise SystemExit(f"take/{files[0].name} points to a file that is not there any more. Where is the raw take now?")
    return files[0]


def main() -> int:
    ap = argparse.ArgumentParser(description="From a raw take to the base, the voice, the words and the matte.")
    ap.add_argument("slug")
    ap.add_argument("--edl", action="store_true", help="transcribe the raw take and propose the EDL, then stop")
    ap.add_argument("--take", help="the file in take/ to use when there is more than one")
    ap.add_argument("--force", action="store_true", help="run every step again")
    ap.add_argument("--no-matte", action="store_true", help="skip the matte and the geometry")
    ap.add_argument("--no-restarts", action="store_true", help="skip the false-start check")
    a = ap.parse_args()
    try:
        folder = config.work_dir(a.slug)
    except ValueError as problem:
        print(problem)
        return 1
    build = folder / "build"
    build.mkdir(parents=True, exist_ok=True)
    raw = find_take(folder, a.take)
    py = sys.executable

    if a.edl:
        words = build / "raw.words.json"
        if a.force or not fresh(words, raw):
            run(py, "-m", "kit.transcribe", raw, "--out", words, "--no-align", "--lines")
        run(py, KIT / "propose_edl.py", words, build / "edl.json")
        print(f"Next: read build/raw.lines.txt, delete false starts and repeats from build/edl.json, then: prep.py {a.slug}")
        return 0

    edl = build / "edl.json"
    if not edl.is_file():
        print(f"build/edl.json is missing. Run: prep.py {a.slug} --edl")
        return 1
    base, clean, words = build / "base.mp4", build / "clean.wav", build / "base.words.json"
    if a.force or not fresh(base, edl, raw):
        run(py, KIT / "cut_base.py", raw, edl, base)
    if a.force or not fresh(clean, base):
        run(py, KIT / "light_voice.py", base, clean)
    if a.force or not fresh(words, base):
        run(py, "-m", "kit.transcribe", base, "--out", words, "--lines")
    if not a.no_restarts and (a.force or not fresh(build / "restarts.log", base)):
        done = subprocess.run([py, str(KIT / "check_restarts.py"), str(base), "--words", str(words)],
                              capture_output=True, text=True)
        (build / "restarts.log").write_text(done.stdout + done.stderr)
        print(done.stdout.strip().splitlines()[-1] if done.stdout.strip() else "restart check gave no output")
    if not a.no_matte:
        matte = build / "matte.mp4"
        if a.force or not fresh(matte, base):
            run(py, "-m", "kit.matte", base, matte)
        if a.force or not fresh(build / "geometry.json", matte):
            run(py, KIT / "measure_crowns.py", "--matte", matte, "--out", build / "geometry.json")
    print(f"prep done for {a.slug}. Next: write build/layout.json from build/base.lines.txt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
