#!/usr/bin/env python3
"""render.py: render the composition to build/picture.mp4, inside a heavy-job slot.

    python render.py <video> [--quality delivery]

It renders work/<video>/build/hf with the kit's own pinned HyperFrames (node_modules/.bin/hyperframes, from npm ci)
at 60 fps, one worker, into work/<video>/build/picture.mp4. It never lets npx fetch another version.
The render is heavy, so it waits for one of the shared slots (at most two heavy jobs run at once) and checks for
5 GB of free disk first. HyperFrames' telemetry is turned off for the run. A failed render leaves no picture
behind and prints the last lines HyperFrames wrote.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from kit import config, media
from kit.config import KitError


def hyperframes() -> Path:
    """The kit's own HyperFrames command, installed by npm ci in the kit folder."""
    tool = Path(config.__file__).resolve().parents[1] / "node_modules" / ".bin" / "hyperframes"
    if shutil.which("node") is None:
        raise KitError("Node is not installed, so HyperFrames cannot render. Install Node 22 or newer, then run npm ci.")
    if not tool.exists():
        raise KitError("HyperFrames is not installed in this kit yet. Run npm ci in the kit folder, then render again.")
    return tool


def render(slug: str, quality: str = "delivery") -> Path:
    folder = config.work_dir(slug)
    hf = folder / "build" / "hf"
    if not (hf / "index.html").is_file():
        raise KitError(f"{hf / 'index.html'} is missing. Make the project first: install.py {slug}")
    tool = hyperframes()
    out = folder / "build" / "picture.mp4"
    partial = out.with_name("picture.part.mp4")
    partial.unlink(missing_ok=True)
    env = {**os.environ, "HYPERFRAMES_NO_TELEMETRY": "1", "HYPERFRAMES_SKIP_SKILLS": "1"}
    with media.heavy_slot(label=f"render {slug}"):
        done = subprocess.run([str(tool), "render", "--fps", "60", "--workers", "1", "--quality", quality, "--quiet",
                               "--output", str(partial), str(hf)], cwd=tool.parents[2], env=env, capture_output=True, text=True)
    if done.returncode != 0 or not partial.is_file():
        partial.unlink(missing_ok=True)
        tail = "\n".join((done.stderr or done.stdout).strip().splitlines()[-8:])
        raise KitError(f"HyperFrames could not render {slug}. What it said:\n{tail}")
    os.replace(partial, out)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Render the composition to build/picture.mp4.")
    ap.add_argument("slug", help="the video name (a folder in work/)")
    ap.add_argument("--quality", default="delivery", choices=("draft", "looks", "standard", "delivery"))
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        out = render(a.slug, a.quality)
    except (KitError, ValueError) as problem:
        print(problem)
        return 1
    info = media.probe(out)
    print(f"rendered {out} ({info.width}x{info.height}, {info.fps:g} fps, {info.duration:.2f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
