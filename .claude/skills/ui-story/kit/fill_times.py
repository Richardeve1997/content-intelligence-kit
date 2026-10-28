#!/usr/bin/env python3
"""fill_times.py: index.src.html -> index.html, with every {{ expression }} replaced by frame-snapped seconds.

Inside {{ }} these are available:
  c("phrase", nth=1)   when the phrase starts, minus the 0.12 s lead
  e("phrase", nth=1)   when the phrase ends
  w("name")            when the layout window with that name, or starting at that phrase, starts
  w(31.0)              when the layout window holding that time (in seconds) starts
  snap(t), round(x), ZB (the split zone bottom from --zb)

A window is never found by its place in the list (layout.resolved.json): that place moves whenever a window is
added before it, so w(<whole number>) is refused. Write a time with a decimal point.

Run build_captions.py after it: it rewrites the caption block between the CAPTIONS markers.

    python fill_times.py <index.src.html> <base.words.json> <layout.resolved.json> <out index.html> [--zb 712]
"""
import argparse
import json
import re
from pathlib import Path

FPS, LEAD = 60, 0.12


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("words")
    ap.add_argument("layout")
    ap.add_argument("out")
    ap.add_argument("--zb", type=float, default=735.0, help="the split zone bottom in px, available as ZB")
    a = ap.parse_args()

    words = json.loads(Path(a.words).read_text())["words"]
    windows = json.loads(Path(a.layout).read_text())["windows"]
    norm = lambda s: re.sub(r"[^a-z0-9#]", "", str(s).lower())
    snap = lambda t: round(round(t * FPS) / FPS, 4)

    def find(phrase, nth):
        tokens = [norm(x) for x in phrase.split() if norm(x)]
        hit = 0
        for i in range(len(words) - len(tokens) + 1):
            if all(norm(words[i + k]["w"]) == tokens[k] for k in range(len(tokens))):
                hit += 1
                if hit == nth:
                    return i, i + len(tokens) - 1
        raise SystemExit(f"fill_times.py: the cue {phrase!r} (occurrence {nth}) is not in the words")

    def c(phrase, nth=1):
        return snap(words[find(phrase, nth)[0]]["s"] - LEAD)

    def e(phrase, nth=1):
        return snap(words[find(phrase, nth)[1]]["e"])

    def phrase(text):
        return " ".join(t for t in (norm(x) for x in str(text).split()) if t)

    def w(key):
        """When a layout window starts: the window with this name or starting phrase, or the one holding this time."""
        if isinstance(key, int):
            raise SystemExit(f"fill_times.py: w({key!r}) would find a window by its place in the list, and that place "
                             "moves when a window is added before it. Find it by name, w('its name'), by the phrase it "
                             "starts at, w('the phrase'), or by a time inside it with a decimal point, w(31.0).")
        if isinstance(key, float):
            for n, window in enumerate(windows):
                if window["t0"] <= key < window["t1"] or (n == len(windows) - 1 and window["t0"] <= key <= window["t1"]):
                    return snap(window["t0"])
            raise SystemExit(f"fill_times.py: no layout window holds {key} s.")
        wanted = phrase(key)
        hits = [window for window in windows if wanted and wanted in
                (phrase(window.get("name") or ""), phrase(window["at"]) if isinstance(window.get("at"), str) else "")]
        if len(hits) == 1:
            return snap(hits[0]["t0"])
        if not hits:
            raise SystemExit(f"fill_times.py: no layout window is named {key!r} or starts at that phrase.")
        raise SystemExit(f"fill_times.py: {len(hits)} layout windows answer to {key!r}. Give one a \"name\" in "
                         "layout.json and use that.")

    count = 0

    def replace(match):
        nonlocal count
        count += 1
        value = eval(match.group(1), {"__builtins__": {}}, {"c": c, "e": e, "w": w, "snap": snap, "ZB": a.zb, "round": round})
        return f"{float(value):.4f}"

    html = re.sub(r"\{\{(.+?)\}\}", replace, Path(a.src).read_text())
    Path(a.out).write_text(html)
    print(f"filled {count} time expressions -> {a.out}")


if __name__ == "__main__":
    main()
