#!/usr/bin/env python3
"""test_clip.py: a short clip of the creator's character saying one line under a headline page.

    python test_clip.py --out <clip.mp4> [--voice <recording>] [--line "<words>"] [--keep]

Onboarding runs it once the character is built, so the creator sees their mini me in their own brand. The
line is said with the first voice there is:
  1. --voice: the creator's own recording of the line (kit.voice.from_recording)
  2. ElevenLabs, when the profile chose it (voice_over elevenlabs or clone) and ELEVENLABS_API_KEY is set:
     their saved copy of their voice, else a stock voice
  3. macOS say, as a scratch voice
The clip is one headline page with the character waving and talking: the same install, render and finish as
a full Reel, in a scratch video folder (work/mini-me-test-clip/) that is deleted afterwards unless --keep.
It should run 5 to 10 seconds; a line much longer or shorter is said anyway, with a note.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from kit import config, media, voice
from kit.config import KitError

SLUG = "mini-me-test-clip"
HEADLINE = "MEET MY MINI ME"


def default_line(profile) -> str:
    return f"Hi, this is {profile.name}. Meet my mini me. It tells my stories while I stay off camera."


def make_voice(line: str, recording: Path | None, profile) -> tuple[voice.VO, str]:
    if recording is not None:
        return voice.from_recording(recording, SLUG), "your recording"
    if profile.voice_over in ("elevenlabs", "clone") and config.env(voice.KEY_NAME):
        saved = voice.saved_voice() if profile.voice_over == "clone" else None
        return voice.from_elevenlabs(line, SLUG, saved), "ElevenLabs"
    if shutil.which("say"):
        return voice.from_say(line, SLUG), "a scratch voice (macOS say)"
    raise KitError("There is no voice for the test clip on this machine. Record the line on your phone and run this "
                   "again with --voice <the file>.")


def clip(out: Path, *, line: str | None = None, recording: Path | None = None, keep: bool = False) -> Path:
    import install
    import render
    import ship

    profile = config.load_profile()
    folder = config.work_dir(SLUG)
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    try:
        said = line or default_line(profile)
        (folder / "script.md").write_text(f"# Test clip\n\n## Narration\n\n{said}\n", encoding="utf-8")
        vo, source = make_voice(said, recording, profile)
        seconds = media.probe(vo.audio).duration
        if not 5.0 <= seconds <= 10.0:
            print(f"note: the line runs {seconds:.1f} s; a test clip is best at 5 to 10 s.")
        beats = {"duration": round(seconds, 3),
                 "pages": [{"t": 0.0, "text": HEADLINE, "pose": "wave", "field": "dark", "prop": "none"}]}
        (folder / "beats.json").write_text(json.dumps(beats, indent=1) + "\n", encoding="utf-8")
        install.install(SLUG)
        render.render(SLUG)
        if ship.main([SLUG, "--music", "none"]) != 0:
            raise KitError("The test clip did not pass its speech check. Listen to work/mini-me-test-clip/build/"
                           "final.next.mp4, then try again with --keep to look closer.")
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(folder / "final.mp4", out)
        print(f"test clip {out} ({media.probe(out).duration:.1f} s, voice: {source})")
        return out
    finally:
        if not keep:
            shutil.rmtree(folder, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="A short clip of the creator's character saying one line.")
    ap.add_argument("--out", type=Path, required=True, help="where to save the clip, such as work/test-clips/mini-me.mp4")
    ap.add_argument("--voice", type=Path, help="the creator's recording of the line")
    ap.add_argument("--line", help="the words to say (default: a short hello with the creator's name)")
    ap.add_argument("--keep", action="store_true", help="keep the scratch video folder to look at")
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        clip(a.out, line=a.line, recording=a.voice, keep=a.keep)
    except (KitError, ValueError) as problem:
        print(problem)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
