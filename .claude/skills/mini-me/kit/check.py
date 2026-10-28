#!/usr/bin/env python3
"""check.py: load the composition in a headless browser, the way the renderer does, before any render.

    python check.py <video> [--at 1.2 3.4 ...]

It loads work/<video>/build/hf/index.html, waits for the stage to build, and fails (exit 1) on:
  - a page error, such as a cue phrase the narration never says (Stage.T throws)
  - a timeline that never registers as window.__timelines.main
  - a headline outside x 90 to 990 or below y 620, or a pose too wide to fit between the safe lines
  - a repeated element id (an id made in a loop can hide an element in the render)
Lines starting "note:" are worth a look and do not fail the check.

It prints every page (its time, field, pose, the character's scale and the headline sizes), and writes
build/hits.json: a whoosh on every field flip and a thud on every stamp and button press, read from the page
itself, so the sound in the finish follows what is on screen. --at also saves full-size frames to
build/check/<t>.png to look at.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from kit import config

READY = "!!(window.Stage && window.Stage.ready && window.__timelines && window.__timelines.main)"


def launch(playwright):
    """Playwright's own Chromium (uv run playwright install chromium), else an installed Chrome."""
    try:
        return playwright.chromium.launch(args=["--allow-file-access-from-files"])
    except Exception:
        try:
            return playwright.chromium.launch(channel="chrome", args=["--allow-file-access-from-files"])
        except Exception:
            raise SystemExit("No browser is available for the page check. Install one with: "
                             "uv run playwright install chromium") from None


def run(slug: str, at: list[float]) -> int:
    from playwright.sync_api import TimeoutError as PageTimeout
    from playwright.sync_api import sync_playwright

    folder = config.work_dir(slug)
    hf = folder / "build" / "hf"
    index = hf / "index.html"
    if not index.is_file():
        print(f"{index} is missing. Make the project first: install.py {slug}")
        return 1
    errors: list[str] = []
    with sync_playwright() as p:
        browser = launch(p)
        page = browser.new_page(viewport={"width": 1080, "height": 1920})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(index.as_uri())
        try:
            page.wait_for_function(READY, timeout=30000)
        except PageTimeout:
            browser.close()
            for line in errors[:10]:
                print(f"FAIL {line}")
            print("FAIL the stage never finished building, so the timeline was never registered.")
            return 1
        report = page.evaluate("Stage.audit()")
        dups = page.evaluate("""() => { const c = {}; document.querySelectorAll('[id]').forEach(e => { c[e.id] = (c[e.id] || 0) + 1; });
            return Object.entries(c).filter(([k, n]) => n > 1).map(([k, n]) => k + ' x' + n); }""")
        shots = []
        if at:
            out = folder / "build" / "check"
            out.mkdir(parents=True, exist_ok=True)
            for t in at:
                page.evaluate(f"(() => {{ window.__timelines.main.seek({float(t)}, false); return true; }})()")
                path = out / f"{float(t):.2f}.png"
                page.screenshot(path=str(path))
                shots.append(path)
        browser.close()

    print(f"{'#':>3} {'t':>7} {'field':5}  {'pose':11} {'scale':>5} {'crown':>5}  headline sizes")
    for info in report["pages"]:
        sizes = "/".join(str(line["size"]) for line in info["lines"])
        print(f"{info['n']:>3} {info['t0']:7.2f} {info['field']:5}  {info['pose']:11} {info['char']['scale']:>5} "
              f"{info['char']['crown']:>5}  {sizes}")
    hits = report.get("hits", [])
    (folder / "build" / "hits.json").write_text(json.dumps({"hits": hits}, indent=1) + "\n", encoding="utf-8")
    print(f"{sum(h['kind'] == 'whoosh' for h in hits)} whooshes and {sum(h['kind'] == 'thud' for h in hits)} thuds "
          f"-> {folder / 'build' / 'hits.json'}")
    for path in shots:
        print(f"frame {path}")
    failures = errors + [f"duplicate id {d}" for d in dups] + [p for p in report["problems"] if not p.startswith("note:")]
    for line in report["problems"]:
        if line.startswith("note:"):
            print(f"  {line}")
    for line in failures:
        print(f"FAIL {line}")
    print("PASS" if not failures else f"{len(failures)} problem(s)")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Load the composition headless and check it before a render.")
    ap.add_argument("slug", help="the video name (a folder in work/)")
    ap.add_argument("--at", type=float, nargs="+", default=[], help="also save frames at these times")
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return run(a.slug, a.at)
    except ValueError as problem:
        print(problem)
        return 1


if __name__ == "__main__":
    sys.exit(main())
