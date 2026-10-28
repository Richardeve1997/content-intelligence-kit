#!/usr/bin/env python3
"""scaffold.py: a new HyperFrames project for one UI story Reel, in the creator's brand.

    python scaffold.py <hf_dir> <duration_seconds>
    python scaffold.py --slug <video> <duration_seconds>      (the project goes in work/<video>/build/hf)

It writes:
  tokens.css        the creator's colours and fonts (kit.brand.tokens_css), with the font files copied
                    into fonts/ beside it. HyperFrames will not load a font from outside the project
                    folder, so the fonts travel with the project.
  uistory.css, karaoke.css, uistory.js   the kit's look and helpers (refreshed on every run)
  vendor/gsap.min.js                      GSAP, from kit/vendor (bin/fetch_gsap.sh downloads it)
  index.html        from template.html, with data-duration set. An existing index.html is never touched.
  assets/logo.*     the creator's logo, when the profile has one (the closer uses it)
  lib/words.js, lib/layout.js, lib/capwords.js   empty until layout.py and build_captions.py fill them
  package.json, hyperframes.json

The duration is the base's duration plus two frames: HyperFrames renders the last partial frame blank.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from urllib.parse import quote
import sys
from pathlib import Path

from kit import brand, config

KIT = Path(__file__).resolve().parent
GSAP = config.kit_root() / "kit" / "vendor" / "gsap.min.js"
KIT_FILES = ("uistory.css", "uistory.js", "karaoke.css")
EMPTY_LIBS = {
    "words.js": "window.UIS_WORDS=[];\n",
    "layout.js": "window.UIS_LAYOUT=[];\n",
    "capwords.js": "window.UIS_CAPWORDS=[];\n",
}


def project_tokens(hf: Path) -> str:
    """tokens.css for a project folder, with every font it names copied into <hf>/fonts/."""
    css = brand.tokens_css(config.load_profile(), hf)
    fonts = hf / "fonts"
    fonts.mkdir(parents=True, exist_ok=True)

    def local(match: re.Match) -> str:
        source = brand.url_file(match.group(1), hf)  # the URL is percent-encoded: decode it before reading it
        if not source.is_file():
            raise SystemExit(f"The font file {source.name} named by your profile is missing. Run onboarding again.")
        target = fonts / source.name
        if not target.is_file() or target.stat().st_size != source.stat().st_size:
            shutil.copy2(source, target)
        return f'url("fonts/{quote(source.name)}")'

    return re.sub(r'url\("([^"]+)"\)', local, css)


def scaffold(hf: Path, duration: float) -> Path:
    hf = Path(hf)
    if not GSAP.is_file():
        raise SystemExit("GSAP is not downloaded yet. Run: bash bin/fetch_gsap.sh, then scaffold again.")
    for folder in ("vendor", "lib", "assets"):
        (hf / folder).mkdir(parents=True, exist_ok=True)
    shutil.copy2(GSAP, hf / "vendor" / "gsap.min.js")
    for name in KIT_FILES:
        shutil.copy2(KIT / name, hf / name)
    (hf / "tokens.css").write_text(project_tokens(hf), encoding="utf-8")
    profile = config.load_profile()
    if profile.logo:  # the closer's avatar for a follow CTA
        logo = config.me_dir() / profile.logo
        shutil.copy2(logo, hf / "assets" / f"logo{logo.suffix.lower()}")
    index = hf / "index.html"
    if not index.exists():
        text = (KIT / "template.html").read_text(encoding="utf-8").replace("__DURATION__", f"{float(duration):g}")
        index.write_text(text, encoding="utf-8")
    for name, empty in EMPTY_LIBS.items():
        if not (hf / "lib" / name).exists():
            (hf / "lib" / name).write_text(empty, encoding="utf-8")
    if not (hf / "package.json").exists():
        (hf / "package.json").write_text(json.dumps({"name": hf.name, "private": True, "type": "module"}) + "\n")
    if not (hf / "hyperframes.json").exists():
        (hf / "hyperframes.json").write_text(json.dumps({
            "$schema": "https://hyperframes.heygen.com/schema/hyperframes.json",
            "paths": {"blocks": "compositions", "components": "compositions/components", "assets": "assets"},
        }, indent=2) + "\n")
    return hf


def main() -> int:
    parser = argparse.ArgumentParser(description="A new HyperFrames project for one UI story Reel.")
    parser.add_argument("target", help="the project folder, or the video name with --slug")
    parser.add_argument("duration", type=float, help="the base duration in seconds plus two frames")
    parser.add_argument("--slug", action="store_true", help="treat the target as a video name in work/")
    args = parser.parse_args()
    try:
        hf = config.work_dir(args.target) / "build" / "hf" if args.slug else Path(args.target)
        scaffold(hf, args.duration)
    except ValueError as problem:
        print(problem)
        return 1
    except config.KitError as problem:
        print(problem)
        return 1
    print(f"scaffolded {hf} ({args.duration:g} s). Next: layout.py --hf {hf}, then build_captions.py, "
          "then the scenes in index.html.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
