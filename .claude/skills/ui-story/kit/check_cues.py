#!/usr/bin/env python3
"""check_cues.py: every cue phrase in a scene file must be in the base words, and ambiguous cues are named.

A contraction in a cue ("i think that" against the spoken "that's") throws in the page, so the timeline
never registers and the render shows only the first scene. A short cue with no nth resolves to the FIRST
time it was said and can fire before its scene opens. check_js.py catches the throw when the page runs; this
catches both before you render, and names the phrase.

It reads C('...') and E('...'), UIS.cue('...') and UIS.cueEnd('...') (index.html) and c("...") and e("...")
(fill_times.py sources), each with an optional nth.

    python check_cues.py <index.html | index.src.html> <base.words.json>   (exit 1 on a missing cue)
"""
import argparse
import json
import re
import sys


def norm(word):
    return re.sub(r"[^a-z0-9#]", "", str(word).lower())


def main():
    ap = argparse.ArgumentParser(description="Every cue phrase must be in the words.")
    ap.add_argument("index")
    ap.add_argument("words")
    a = ap.parse_args()
    html = open(a.index, encoding="utf-8").read()
    words = json.load(open(a.words))["words"]
    ws = [norm(w["w"]) for w in words]
    calls = re.findall(r"""(?:\bUIS\.cue(?:End)?|\b[CEce])\(\s*(['"])(.+?)\1\s*(?:,\s*(?:nth\s*=\s*)?(\d+))?\s*\)""", html)
    missing, ambiguous = [], set()
    for _, phrase, nth in calls:
        tokens = [norm(t) for t in phrase.split() if norm(t)]
        if not tokens:
            continue
        hits = [i for i in range(len(ws) - len(tokens) + 1) if ws[i : i + len(tokens)] == tokens]
        if len(hits) < int(nth or 1):
            missing.append(f"'{phrase}' nth={nth or 1}: {len(hits)} match(es)")
        elif len(hits) > 1 and not nth:
            ambiguous.add(f"'{phrase}' occurs {len(hits)}x at " + ", ".join(f"{words[i]['s']:.2f}s" for i in hits[:5])
                          + ": it resolves to the FIRST; pass nth or a longer phrase if you meant a later one")
    for m in missing:
        print("FAIL missing cue", m)
    for warning in sorted(ambiguous):
        print("WARN", warning)
    print(f"{len(calls)} cues checked, {len(missing)} missing, {len(ambiguous)} ambiguous")
    sys.exit(1 if missing else 0)


if __name__ == "__main__":
    main()
