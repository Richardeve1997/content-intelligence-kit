#!/usr/bin/env python3
"""check_js.py: load the composition headless and fail on any page error, a missing main timeline, or a
duplicate element id.

One bad UIS.cue phrase throws inside the timeline script, so window.__timelines.main never registers:
lint and check_scenes.py both pass and every snapshot shows only the first scene. A duplicate id (an id made
in a loop that collides with another) can leave an element hidden in the render while every snapshot looks
right. Ids made by script count too, which a text search cannot see. Run this before snapshots or a render.

    python check_js.py <hf dir>/index.html   (exit 1 on a page error, no main timeline, or a duplicate id)
"""
import argparse
import sys
import time
from pathlib import Path

from browser import NoBrowser, launch
from playwright.sync_api import sync_playwright


def main():
    ap = argparse.ArgumentParser(description="Load the composition and fail on page errors.")
    ap.add_argument("index")
    a = ap.parse_args()
    page_path = Path(a.index).resolve()
    errors = []
    try:
        with sync_playwright() as p:
            browser = launch(p)
            page = browser.new_page()
            page.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
            page.on("console", lambda m: errors.append(f"console {m.type}: {m.text}") if m.type == "error" else None)
            page.add_init_script("window.__timelines = window.__timelines || {};")
            page.goto(page_path.as_uri())
            time.sleep(2)
            ok = page.evaluate("!!(window.__timelines && window.__timelines.main)")
            dups = page.evaluate("""() => { const c = {}; document.querySelectorAll('[id]').forEach(e => { c[e.id] = (c[e.id] || 0) + 1; });
                return Object.entries(c).filter(([k, n]) => n > 1).map(([k, n]) => k + ' x' + n); }""")
            browser.close()
    except NoBrowser as problem:
        print(problem)
        sys.exit(1)
    for e in errors[:20]:
        print("FAIL", e)
    for d in dups[:20]:
        print("FAIL duplicate id", d)
    print(f"main timeline registered: {ok}; {len(errors)} error(s); {len(dups)} duplicate id(s)")
    sys.exit(0 if ok and not errors and not dups else 1)


if __name__ == "__main__":
    main()
