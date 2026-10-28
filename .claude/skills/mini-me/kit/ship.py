#!/usr/bin/env python3
"""ship.py: the sound and the finish, from the rendered picture to work/<video>/final.mp4.

    python ship.py <video> [--music auto|none|<file>] [--mood <folder>] [--under 18]

It reads build/picture.mp4 (the HyperFrames render), vo.m4a and words.json, and build/hits.json (written by
check.py). A render older than beats.json, scenes.js or vo.m4a does not show the latest changes, so it stops and
asks for a new render. A hits.json that is missing or older than beats.json or scenes.js is made again by the page
check (check.py). Where the page check cannot run (no browser), it says so in one line: an older hits.json is used
as it is, and with none the hits come from beats.json. Then:

  1. The sound effects. kit.sfx makes the whoosh and the thud from numbers, and every hit is placed so its
     loudest moment lands on its time: a whoosh on each field flip, a thud on each stamp and button press.
  2. The edit: the picture (copied, never encoded again), with the voice-over and the hits mixed under it,
     cut to the picture's length, as build/edit.mov.
  3. kit.finish at speed 1.0 (the voice-over was made at its final pace, so nothing is sped up here). The bed
     is set --under dB under the voice (18 by default, so it stays at least 15 under once levelled), fades in
     over 0.3 s and out over the last 1.2 s; the mix lands at -13 LUFS with the true peak at or below -1.5.
     --music auto keeps the bed this video already has, or picks one from me/music (kit.music.for_video);
     with no music in me/music the Reel has no bed.
  4. kit.qa_captions listens to the result against words.json. A lost word stops the ship.
  5. Only a render that passed becomes final.mp4. The review's pass marker (review/review.json) is deleted
     first, because it described the previous final. A render that failed stays as build/final.next.mp4.
     Once it passed, the bed is logged in me/music/MUSIC.md and build/mix.json records the levels: the voice,
     and the bed as measured through the finish's own levelling, so gates.sh checks a measurement.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from kit import config, finish, media, music, qa_captions, sfx, transcribe, voice
from kit.config import KitError

UNDER_DB = 18.0  # the bed's level under the voice; the floor is 15, the rest is room for the levelling
MIN_UNDER_DB = 15.0
STAMP_DELAY = 0.27  # when a stamp lands after its page starts, for hits made from beats.json alone


def need(path: Path, made_by: str) -> Path:
    if not path.is_file():
        raise KitError(f"{path.name} is missing. Make it with {made_by} first.")
    return path


def newer(target: Path, sources: list[Path]) -> list[Path]:
    """The sources changed after `target` was made."""
    made = target.stat().st_mtime
    return [path for path in sources if path.is_file() and path.stat().st_mtime > made]


def refresh_hits(slug: str) -> bool:
    """Make build/hits.json again with the page check. False when no browser or project can run it."""
    import check  # check.py beside this file

    try:
        code = check.run(slug, [])
    except SystemExit:
        return False  # no browser on this machine
    if code != 0 and not (config.work_dir(slug) / "build" / "hf" / "index.html").is_file():
        return False
    if code != 0:
        raise KitError(f"The page check failed, so the sound cannot follow the page. Run check.py {slug} and fix it.")
    return True


def hits_for(folder: Path, slug: str | None = None) -> list[sfx.Hit]:
    """The hits from build/hits.json (check.py writes it from the page), else from beats.json.

    With a video name, a hits.json that is missing or older than beats.json or scenes.js is made again with the
    page check first. When the page check cannot run here (no browser), one line says so: an older hits.json is
    then used as it is, and with none the hits come from beats.json (a whoosh per flip, a thud per stamp page).
    """
    written = folder / "build" / "hits.json"
    if slug is not None:
        sources = [folder / "beats.json", folder / "build" / "hf" / "lib" / "scenes.js"]
        stale = newer(written, sources) if written.is_file() else []
        if not written.is_file() or stale:
            what = f"build/hits.json is older than {stale[0].name}" if stale else "build/hits.json is missing"
            if refresh_hits(slug):
                print(f"{what}, so the page check made it again.")
            elif stale:
                print(f"{what} and the page check could not run here, so the sound follows the page as it was at the last check.")
            else:
                print(f"{what} and the page check could not run here, so the sound comes from beats.json: "
                      "a whoosh on each flip and a thud on each stamp page.")
    if written.is_file():
        data = json.loads(written.read_text(encoding="utf-8"))
        return [sfx.Hit(h["kind"], float(h["t"]), float(h.get("gain", 0.4))) for h in data.get("hits", [])]
    beats = json.loads(need(folder / "beats.json", "beats.py").read_text(encoding="utf-8"))
    pages, out = beats["pages"], []
    for previous, page in zip(pages, pages[1:]):
        if page["field"] != previous["field"]:
            out.append(sfx.Hit("whoosh", float(page["t"]), 0.36))
    for page in pages:
        if str(page["prop"]).split(":")[0].strip().lower() == "stamp":
            out.append(sfx.Hit("thud", float(page["t"]) + STAMP_DELAY, 0.5))
    return sorted(out, key=lambda h: h.t)


def edit(folder: Path, picture: Path, hits: list[sfx.Hit], out: Path) -> Path:
    """The picture copied, with the voice-over and the hits mixed under it, cut to the picture's length."""
    length = media.probe(picture).duration
    with tempfile.TemporaryDirectory(prefix="mm-hits-", dir=out.parent) as scratch:
        track = sfx.track(hits, length, Path(scratch) / "hits.wav")
        graph = (f"[1:a]aresample=48000,aformat=channel_layouts=stereo,apad[v];"
                 f"[2:a]aresample=48000,aformat=channel_layouts=stereo[h];"
                 f"[v][h]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
                 f"atrim=0:{length:.4f},asetpts=PTS-STARTPTS[a]")
        done = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(picture), "-i", str(folder / "vo.m4a"),
                               "-i", str(track), "-filter_complex", graph, "-map", "0:v:0", "-map", "[a]", "-c:v", "copy",
                               "-c:a", "pcm_s24le", "-ar", "48000", str(out)], capture_output=True, text=True)
    if done.returncode != 0:
        tail = "\n".join(done.stderr.strip().splitlines()[-4:])
        raise KitError(f"ffmpeg could not mix the voice-over and the sound effects. What it said:\n{tail}")
    return out


def bed_level(length: float, bed: Path, music_lufs: float, scratch: Path) -> float:
    """The bed's loudness as the finish levels it: the bed alone, under a silent voice, through kit.finish's own mix."""
    silent = scratch / "silent.wav"
    done = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                           "-t", f"{length:.4f}", "-c:a", "pcm_s16le", str(silent)], capture_output=True, text=True)
    if done.returncode != 0:
        raise KitError("ffmpeg could not make the silence the bed is measured under.")
    return finish.mix_level(silent, speed=1.0, music=bed, music_lufs=music_lufs, fade_in=0.0, fade_out=0.0)


def choose_bed(slug: str, choice: str, mood: str | None) -> Path | None:
    if choice == "none":
        return None
    if choice != "auto":
        return Path(choice)
    bed = music.for_video(slug, mood)
    if bed is None:
        print("No track in me/music, so this Reel has no bed. Add tracks to a mood folder to give it one.")
    return bed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="The sound, the finish and the speech gate for one mini me Reel.")
    ap.add_argument("slug")
    ap.add_argument("--music", default="auto", help="auto (the video's bed, or a pick from me/music), none, or a file")
    ap.add_argument("--mood", help="the me/music folder to pick from")
    ap.add_argument("--under", type=float, default=UNDER_DB, help="how far under the voice the bed sits, in dB (15 or more)")
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    if a.under < MIN_UNDER_DB:
        print(f"The bed sits at least {MIN_UNDER_DB:g} dB under the voice in this style. Use --under {MIN_UNDER_DB:g} or more.")
        return 1
    try:
        folder = config.work_dir(a.slug)
    except ValueError as problem:
        print(problem)
        return 1
    build = folder / "build"
    try:
        picture = need(build / "picture.mp4", "the render (see the skill)")
        need(folder / "vo.m4a", "python -m kit.voice auto " + a.slug)
        words = transcribe.load(need(folder / "words.json", "python -m kit.voice auto " + a.slug))
        changed = newer(picture, [folder / "beats.json", build / "hf" / "lib" / "scenes.js", folder / "vo.m4a"])
        if changed:
            raise KitError(f"build/picture.mp4 is older than {changed[0].name}, so it does not show the latest changes. "
                           f"Render it again first: render.py {a.slug}")
        hits = hits_for(folder, a.slug)
        cut = edit(folder, picture, hits, build / "edit.mov")
        bed = choose_bed(a.slug, a.music, a.mood)
        voice_lufs = finish.measure(folder / "vo.m4a").integrated
        bed_lufs = round(voice_lufs - a.under, 1)
        candidate, final = build / "final.next.mp4", folder / "final.mp4"
        finish.finish(cut, candidate, speed=voice.FINISH_SPEED, music=bed, music_lufs=bed_lufs,
                      mix_peak=finish.HOUSE["mix_peak"], fade_in=finish.HOUSE["fade_in"], fade_out=finish.HOUSE["fade_out"])
        problems = qa_captions.check(candidate, words, speed=voice.FINISH_SPEED, fallback=cut)
        if not problems and bed is not None:
            with tempfile.TemporaryDirectory(prefix="mm-bed-", dir=build) as scratch:
                measured = bed_level(media.probe(cut).duration, bed, bed_lufs, Path(scratch))
    except (KitError, ValueError) as problem:
        print(problem)
        return 1
    if problems:
        print(f"FAIL: the speech gate found {len(problems)} lost word run(s):")
        for line in problems:
            print(f"- {line}")
        print(f"The failed render is {candidate}; final.mp4 was left as it was.")
        return 1
    print("PASS: the speech gate heard every word of the voice-over.")
    if bed is not None:
        music.log(bed, a.slug)  # one row per video: a re-ship with the same bed adds nothing
    (build / "mix.json").write_text(json.dumps({
        "voice_lufs": round(voice_lufs, 1),
        "bed": bed.name if bed is not None else None,
        "bed_lufs_set": bed_lufs if bed is not None else None,
        "bed_lufs": round(measured, 1) if bed is not None else None,
        "under_db": round(voice_lufs - measured, 1) if bed is not None else None,
        "hits": len(hits), "speed": voice.FINISH_SPEED,
    }, indent=1) + "\n", encoding="utf-8")
    (folder / "review" / "review.json").unlink(missing_ok=True)  # it passed the previous final, not this one
    os.replace(candidate, final)
    print(f"shipped {final}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
