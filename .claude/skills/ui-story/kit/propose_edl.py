#!/usr/bin/env python3
"""propose_edl.py: a first EDL for a raw take, from its words: every stretch of speech becomes a keep segment.

Speech separated by a pause of --gap seconds or more becomes separate segments, each padded a little
(--pad-in before the first word, --pad-out after the last). It prints the segments, numbered, with their
words, so the false starts, repeats and dead air are easy to spot. Delete those segments from the file (or
trim a segment's start to the clean restart), then cut the base. Keep a natural sign-off at the end.

    python propose_edl.py <raw.words.json> <edl.json> [--gap 0.8] [--pad-in 0.12] [--pad-out 0.2] [--duration D]
"""
import argparse
import json
import sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(description="A first EDL from the words of a raw take.")
    ap.add_argument("words")
    ap.add_argument("out")
    ap.add_argument("--gap", type=float, default=0.8, help="a pause this long or longer splits two segments")
    ap.add_argument("--pad-in", type=float, default=0.12)
    ap.add_argument("--pad-out", type=float, default=0.2)
    ap.add_argument("--duration", type=float, help="the raw take's length, so the last pad stays inside it")
    a = ap.parse_args()
    data = json.loads(Path(a.words).read_text())
    words = data["words"]
    if not words:
        print("The take has no words, so there is nothing to keep.")
        return 1
    limit = a.duration or data.get("dur") or words[-1]["e"] + a.pad_out
    runs, current = [], [words[0]]
    for word in words[1:]:
        if word["s"] - current[-1]["e"] >= a.gap:
            runs.append(current)
            current = []
        current.append(word)
    runs.append(current)
    keep = []
    for run in runs:
        start = max(0.0, run[0]["s"] - a.pad_in)
        end = min(limit, run[-1]["e"] + a.pad_out)
        if keep and start < keep[-1][1]:
            start = keep[-1][1]
        keep.append([round(start, 3), round(end, 3)])
    Path(a.out).write_text(json.dumps({"keep": keep}, indent=1) + "\n")
    for i, (run, (s, e)) in enumerate(zip(runs, keep)):
        print(f"{i:3d}  {s:7.2f}-{e:7.2f}  {' '.join(w['w'] for w in run)}")
    print(f"{len(keep)} keep segments, {sum(e - s for s, e in keep):.1f}s -> {a.out}")
    print("Delete the segments that are false starts or repeats, then cut the base.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
