#!/usr/bin/env python3
"""install.py: the HyperFrames project for one mini me Reel, in the creator's brand.

    python install.py <video>          the project goes in work/<video>/build/hf

It reads work/<video>/vo.m4a, words.json and beats.json, the creator's profile, and their packed character
(me/character/: manifest.js, strips/ and faces/), and writes:

  index.html          the composition. Made again on every run: change lib/scenes.js, never this.
  stage.js, stage.css the style, from the kit (refreshed every run)
  tokens.css, fonts/  the creator's colours and fonts. HyperFrames loads fonts only from inside the project,
                      so the font files travel with it.
  vendor/gsap.min.js  GSAP, from kit/vendor (bin/fetch_gsap.sh downloads it)
  character/          character.js and the packed character: manifest.js, strips/, faces/ (nothing else of
                      me/character/ is copied, never the photos)
  assets/vo.m4a       the voice-over, so the preview plays it
  assets/logo.*       the creator's logo, the closing card's picture (their initial when there is none)
  lib/data.js         the words, the pages, the closing card's name and handle, the lip-sync envelope and
                      the measured outline of every pose (how the stage keeps the character inside the safe lines)
  lib/scenes.js       this Reel's scenes. Written once from a starter, then yours: never overwritten.
  hyperframes.json, package.json

The duration is the voice-over's, cut down to a whole frame at 60 fps, and the same number is used everywhere.
Run it again whenever the voice-over, the words, beats.json, the profile or the character change.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
from urllib.parse import quote
import subprocess
import sys
from pathlib import Path

from kit import brand, config, media, transcribe
from kit.config import KitError

KIT = Path(__file__).resolve().parent
GSAP = Path(config.__file__).resolve().parent / "vendor" / "gsap.min.js"  # kit/vendor, beside the kit's own code
FPS = 60
ENVELOPE_FPS = 24
KIT_FILES = ("stage.js", "stage.css")

SCENES_STARTER = """/* Scenes for this Reel. The stage builds every page of beats.json by itself: the field, the headline, the
 * character in the page's pose and the page's prop. Add here what each page needs on top of that: the receipts,
 * stamps and objects that arrive because of what the character does. Pages are numbered from 1, the numbers
 * beats.py and check.py print.
 * Every time comes from a spoken word, c.T("word") or c.keyAt (when the pose lands), never a typed second.
 * The full list of what c holds is at the top of stage.js.
 *
 * Stage.page(2, {
 *   build: function (c) {
 *     Stage.card(c, { img: "assets/receipt.png" });   // arrives from the side the character points to
 *     Stage.stamp(c, "FREE", { t: c.T("free") });     // lands on its word, with a thud
 *   }
 * });
 */
"""

INDEX = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=1080, height=1920">
<!-- Made by install.py from beats.json, words.json and the profile. Edit lib/scenes.js, not this file. -->
<link rel="stylesheet" href="tokens.css">
<link rel="stylesheet" href="stage.css">
<script src="vendor/gsap.min.js"></script>
<script src="character/manifest.js"></script>
<script src="character/character.js"></script>
<script src="lib/data.js"></script>
<script src="stage.js"></script>
<script src="lib/scenes.js"></script>
<style>
  html, body { margin: 0; width: 1080px; height: 1920px; overflow: hidden; background: var(--field-dark); }
  #root { position: relative; width: 1080px; height: 1920px; overflow: hidden; background: var(--field-dark); }
</style>
</head>
<body>
<div id="root" data-composition-id="main" data-start="0" data-duration="__DURATION__" data-width="1080" data-height="1920">
  <div id="mm"></div>
  <audio id="mm-vo" src="assets/vo.m4a" data-start="0" data-duration="__DURATION__" data-track-index="9" data-volume="1"></audio>
</div>
<script>
  (async () => {
    await Stage.boot("#mm");
    window.__timelines = window.__timelines || {};
    window.__timelines["main"] = Stage.tl;   // registered only once the timeline is built
    if (window.__hfForceTimelineRebind) window.__hfForceTimelineRebind();
  })();
</script>
</body>
</html>
"""


def frame_duration(seconds: float) -> float:
    """Seconds cut down to a whole frame at 60 fps: 23.4567 becomes 23.45."""
    return math.floor(seconds * FPS + 1e-6) / FPS


def written(duration: float) -> str:
    """The duration as written into the composition: four decimals, rounded down, so it never reaches into the
    next frame (18.566666... is written 18.5666, which is still frame 1114 at 60 fps, where 18.5667 would round
    up to an extra frame)."""
    return f"{math.floor(duration * 10000) / 10000:.4f}"


def project_tokens(hf: Path) -> str:
    """tokens.css for the project folder, with every font it names copied into <hf>/fonts/."""
    css = brand.tokens_css(config.load_profile(), hf)
    fonts = hf / "fonts"
    fonts.mkdir(parents=True, exist_ok=True)

    def local(match: re.Match) -> str:
        source = brand.url_file(match.group(1), hf)  # the URL is percent-encoded: decode it before reading it
        if not source.is_file():
            raise KitError(f"The font file {source.name} named by your profile is missing. Run onboarding again.")
        target = fonts / source.name
        if not target.is_file() or target.stat().st_size != source.stat().st_size:
            shutil.copy2(source, target)
        return f'url("fonts/{quote(source.name)}")'

    return re.sub(r'url\("([^"]+)"\)', local, css)


def head_weight(profile) -> int:
    """The heaviest weight of the display font: the top of its range for a font that comes with the kit."""
    found = config.bundled_font(profile.fonts.display)
    if found:
        numbers = [int(n) for n in re.findall(r"\d+", str(found[1].get("weight", "900")))]
        if numbers:
            return max(numbers)
    return 900


def profile_card(profile) -> dict:
    """What the closing card shows: the creator's name and their Instagram handle, written with its @.

    Only the Instagram handle is shown, because the card is Instagram's profile row. With none, the card shows
    the name alone rather than a handle from another platform.
    """
    handle = (profile.handles.instagram or "").strip()
    if handle and not handle.startswith("@"):
        handle = "@" + handle
    avatar = None
    if profile.logo:
        avatar = f"assets/logo{Path(profile.logo).suffix.lower() or '.png'}"
    initial = next((ch.upper() for ch in profile.name if ch.isalnum()), "")
    return {"name": profile.name, "handle": handle, "avatar": avatar, "initial": initial}


def envelope(audio: Path, fps: int = ENVELOPE_FPS) -> dict:
    """The voice-over's loudness, 0 to 1, `fps` times a second, for the character's mouth.

    A window of two frames around each frame, scaled so the quiet syllables read as a half-open mouth and the
    stressed ones as open (the voiced frames' 10th to 95th percentile spread onto 0 to 1), silence closed,
    then a fast attack and a slower release so the mouth does not flutter.
    """
    import numpy as np

    rate = 16000
    raw = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(audio), "-ac", "1", "-ar", str(rate),
                          "-f", "f32le", "-"], capture_output=True).stdout
    x = np.frombuffer(raw, np.float32).astype(np.float64)
    if not len(x):
        return {"fps": fps, "duration": 0.0, "envelope": []}
    hop = rate / fps
    count = int(np.ceil(len(x) / hop))
    rms = np.array([float(np.sqrt((x[int(k * hop) : int(min(len(x), (k + 2) * hop))] ** 2).mean() or 0.0))
                    for k in range(count)])
    voiced = rms > rms.max() * 0.02
    lo, hi = (np.percentile(rms[voiced], 10), np.percentile(rms[voiced], 95)) if voiced.any() else (0.0, 1.0)
    level = np.clip((rms - lo) / max(hi - lo, 1e-9), 0.0, 1.0)
    level = np.where(rms < rms.max() * 0.02, 0.0, level)
    level = np.where(level < 0.04, 0.0, level)
    out, current = [], 0.0
    for value in level:
        current += (value - current) * (0.6 if value > current else 0.25)
        out.append(round(float(current), 3))
    return {"fps": fps, "duration": round(len(x) / rate, 3), "envelope": out}


def pose_outlines(character: Path, manifest: dict) -> dict:
    """For every move and pose, each canvas row's left and right edge, measured from the strip's pixels.

    Edges are in art pixels relative to the entry's head column (ref_col), over every drawing of the strip,
    so the stage can place the character inside the safe lines at any scale. A row with nothing in it is None.
    """
    import numpy as np
    from PIL import Image

    width = int(manifest["canvas"][0])
    entries = [(name, e) for name, e in manifest["moves"].items() if e.get("kind") != "pose"]
    entries += [(f"pose:{name}", e) for name, e in manifest["poses"].items()]
    out = {}
    for key, entry in entries:
        alpha = np.asarray(Image.open(character / entry["strip"]).convert("RGBA"))[..., 3] > 0
        union = np.zeros((alpha.shape[0], width), bool)
        for frame in range(int(entry["frames"])):
            union |= alpha[:, frame * width : (frame + 1) * width]
        ref = float(entry["ref_col"])
        rows = []
        for row in union:
            cols = np.nonzero(row)[0]
            rows.append(None if not len(cols) else [round(float(cols[0]) - ref, 1), round(float(cols[-1]) + 1 - ref, 1)])
        filled = [i for i, r in enumerate(rows) if r is not None]
        out[key] = {"top": filled[0] if filled else 0, "rows": rows}
    return out


def figure(outlines: dict, baseline: int) -> dict:
    """The character's proportions, measured from the idle drawing (arms down): top, neck, waist and feet rows.

    The neck is the narrowest row between the head and the body. With the arms down, the silhouette is
    widest where the hands hang, so the waist is the first row under the last row that wide. A big-headed
    character has its waist far below the halfway point, so it is measured, never guessed. A drawing without
    that shape (hands out of sight) falls back to halfway between the neck and the feet, with waist_measured
    false; the stage's audit (check.py) then prints a note to check the frames, because the crop may be off.
    """
    idle = outlines.get("pose:idle") or next(iter(outlines.values()))
    rows, top = idle["rows"], idle["top"]
    drawn = [i for i, row in enumerate(rows) if row is not None]
    feet = max(drawn[-1], baseline - 1) if drawn else baseline - 1
    spans = [(row[1] - row[0]) if row is not None else 0.0 for row in rows]
    height = max(1, feet - top)
    window = range(top + int(0.2 * height), top + int(0.6 * height) + 1)
    neck = min(window, key=lambda i: (spans[i] if spans[i] > 0 else float("inf"), i))
    limit = neck + int(0.8 * (feet - neck))
    widest = max(spans[neck : limit + 1])
    hands = max(i for i in range(neck, limit + 1) if spans[i] >= widest - 4)
    waist, measured = hands + 1, True
    if not neck + 0.25 * (feet - neck) <= waist <= neck + 0.8 * (feet - neck):
        waist, measured = round(neck + 0.5 * (feet - neck)), False
    return {"top": top, "neck": neck, "waist": waist, "feet": feet, "waist_measured": measured}


def _character(me: Path) -> tuple[Path, dict]:
    character = me / "character"
    manifest_path = character / "manifest.json"
    if not manifest_path.is_file() or not (character / "manifest.js").is_file():
        raise KitError("There is no packed character yet. Say: make my character, and the character skill builds it. "
                       "Then run this again.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return character, manifest


def _copy_character(character: Path, manifest: dict, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    (target / "strips").mkdir(parents=True)
    (target / "faces").mkdir(parents=True)
    shutil.copy2(KIT / "character" / "character.js", target / "character.js")
    shutil.copy2(character / "manifest.js", target / "manifest.js")
    strips = {e["strip"] for e in list(manifest["moves"].values()) + list(manifest["poses"].values())}
    for strip in sorted(strips):
        source = character / strip
        if not source.is_file():
            raise KitError(f"The character is missing {strip}. Pack it again: uv run python -m kit.character pack")
        shutil.copy2(source, target / strip)
    for name in manifest["faces"]:
        source = character / "faces" / f"{name}.png"
        if not source.is_file():
            raise KitError(f"The character is missing faces/{name}.png. Pack it again: uv run python -m kit.character pack")
        shutil.copy2(source, target / "faces" / f"{name}.png")


def install(slug: str) -> Path:
    """Write the HyperFrames project for work/<slug>/ and return its folder."""
    from beats import check  # the beats.py beside this file

    folder = config.work_dir(slug)
    profile = config.load_profile()
    me = config.me_dir()
    vo, words_path, beats_path = folder / "vo.m4a", folder / "words.json", folder / "beats.json"
    for path, how in ((vo, "python -m kit.voice auto " + slug), (words_path, "python -m kit.voice auto " + slug),
                      (beats_path, "beats.py " + slug)):
        if not path.is_file():
            raise KitError(f"work/{slug}/{path.name} is missing. Make it first: uv run {how}")
    beats = json.loads(beats_path.read_text(encoding="utf-8"))
    errors = [p for p in check(beats) if not p.startswith("note:")]
    if errors:
        raise KitError("beats.json needs fixing first:\n" + "\n".join(f"- {p}" for p in errors))
    if not GSAP.is_file():
        raise KitError("GSAP is not downloaded yet. Run: bash bin/fetch_gsap.sh, then run this again.")
    character, manifest = _character(me)
    duration = frame_duration(media.probe(vo).duration)

    hf = folder / "build" / "hf"
    for name in ("vendor", "lib", "assets"):
        (hf / name).mkdir(parents=True, exist_ok=True)
    shutil.copy2(GSAP, hf / "vendor" / "gsap.min.js")
    for name in KIT_FILES:
        shutil.copy2(KIT / name, hf / name)
    (hf / "tokens.css").write_text(project_tokens(hf), encoding="utf-8")
    _copy_character(character, manifest, hf / "character")
    shutil.copy2(vo, hf / "assets" / "vo.m4a")
    card = profile_card(profile)
    if profile.logo:
        shutil.copy2(me / profile.logo, hf / card["avatar"])

    outlines = pose_outlines(character, manifest)
    words = [{"w": w.text, "s": w.start, "e": w.end} for w in transcribe.load(words_path)]
    data = {
        "duration": float(written(duration)),
        "fps": FPS,
        "words": words,
        "pages": beats["pages"],
        "profile": card,
        "cta": {"type": profile.cta.type, "keyword": profile.cta.keyword if profile.cta.type == "comment" else None},
        "head": {"weight": head_weight(profile)},
        "envelope": envelope(vo),
        "baseline": int(manifest["baseline_row"]),
        "figure": figure(outlines, int(manifest["baseline_row"])),
        "extents": outlines,
    }
    (hf / "lib" / "data.js").write_text(
        "/* Made by install.py from the voice-over, words.json, beats.json, the profile and the character. */\n"
        "window.MM_DATA = " + json.dumps(data, separators=(",", ":"), ensure_ascii=False) + ";\n", encoding="utf-8")
    scenes = hf / "lib" / "scenes.js"
    if not scenes.exists():
        scenes.write_text(SCENES_STARTER, encoding="utf-8")
    (hf / "index.html").write_text(INDEX.replace("__DURATION__", written(duration)), encoding="utf-8")
    if not (hf / "package.json").exists():
        (hf / "package.json").write_text(json.dumps({"name": f"{slug}-mini-me", "private": True, "type": "module"}) + "\n")
    if not (hf / "hyperframes.json").exists():
        (hf / "hyperframes.json").write_text(json.dumps({
            "$schema": "https://hyperframes.heygen.com/schema/hyperframes.json",
            "paths": {"blocks": "compositions", "components": "compositions/components", "assets": "assets"},
        }, indent=2) + "\n")
    return hf


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="The HyperFrames project for one mini me Reel.")
    ap.add_argument("slug", help="the video name (a folder in work/)")
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        hf = install(a.slug)
        pages = len(json.loads((config.work_dir(a.slug) / "beats.json").read_text(encoding="utf-8"))["pages"])
        seconds = frame_duration(media.probe(config.work_dir(a.slug) / "vo.m4a").duration)
    except (KitError, ValueError) as problem:
        print(problem)
        return 1
    print(f"installed {hf} ({seconds:g} s, {pages} pages). "
          "Next: write this Reel's scenes in lib/scenes.js, then run check.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
