#!/usr/bin/env python3
"""layout.py: turn layout.json (windows keyed to spoken words) into base-second windows, the face zooms
for cut_base.py, and lib/layout.js for the graphics layer.

The three layouts:
  split   the grid canvas with cards above, the creator in the rounded PIP below, the head breaking out
  canvas  the grid canvas only (grids, lists, timelines, diagrams, a big product clip)
  face    the creator full screen, with emphasis punch-ins and slow pushes cut from the 4K take

layout.json (cues are phrases of the BASE transcript, matched like UIS.cue: lower case, [a-z0-9#] only):
  {"lead": 0.12,
   "windows": [
     {"mode": "split", "t": 0},
     {"mode": "face", "at": "one big problem", "nth": 1, "zooms": [
         {"kind": "punch", "at": "big problem", "z": 1.14, "until": "you're stuck"},
         {"kind": "slow", "z": 1.06}]},
     {"mode": "canvas", "at": "four things", "name": "list"}]}
  A window starts at cue(at) - lead (or at "t" seconds) and ends where the next one starts.
  "name" is optional and must not repeat. The timeline finds a window with UIS.win: by its name, by the
  phrase it starts at, or by a time inside it, never by its place in the list, which moves whenever a
  window is added before it.
  punch: eases in over 0.35 s from cue(at) - 0.05, holds, and cuts back at cue(until) - lead or the window end.
  slow:  a sine ease across [cue(at) - lead or the window start, the window end], 1.00 drifting to z.
  "fy" on a zoom is the row of the take held still, as a fraction of its height (default 0.385, the eyes).

    python layout.py --layout layout.json --words base.words.json --duration <base seconds> \\
        --out layout.resolved.json [--hf <hf dir>] [--edl-in edl.json --edl-out edl-zoom.json]
        [--seg-starts seg_starts.json]

--seg-starts (from seg_starts.py) moves the zooms from base seconds into the EDL time cut_base.py uses to
place them (each base segment runs about a frame long, so without it later zooms land late).
Zooms are allowed only inside face windows: the PIP's head matte is cut from the plain base, so a zoom under
a split window would make the cut-out slip.
"""
import argparse
import json
import re
import sys
from pathlib import Path

FPS = 60
MODES = {"split", "canvas", "face"}
FACE_ROW = 0.385
PUNCH_EASE = 0.35


def norm(word):
    return re.sub(r"[^a-z0-9#]", "", str(word).lower())


def norm_phrase(phrase):
    """A phrase as UIS.win compares it: each word lower case, [a-z0-9#] only."""
    return " ".join(t for t in (norm(x) for x in str(phrase).split()) if t)


def load_words(path):
    data = json.loads(Path(path).read_text())
    if isinstance(data, dict) and "words" in data:
        data = data["words"]
    if isinstance(data, dict):
        data = data.get("captions") or data.get("phrases") or data.get("segments") or []
    out = []
    for item in data:
        if isinstance(item, dict) and isinstance(item.get("words"), list):
            for w in item["words"]:
                out.append((w.get("w", w.get("word")), float(w.get("s", w.get("start"))), float(w.get("e", w.get("end")))))
        elif isinstance(item, dict):
            out.append((item.get("w", item.get("word")), float(item.get("s", item.get("start"))),
                        float(item.get("e", item.get("end")))))
        else:
            out.append((item[0], float(item[1]), float(item[2])))
    return [(str(w).strip(), s, e) for w, s, e in out if str(w).strip()]


def cue(words, phrase, nth=1, which="start"):
    tokens = [t for t in (norm(x) for x in str(phrase).split()) if t]
    hit = 0
    for i in range(len(words) - len(tokens) + 1):
        if all(norm(words[i + k][0]) == tokens[k] for k in range(len(tokens))):
            hit += 1
            if hit == nth:
                return words[i + len(tokens) - 1][2] if which == "end" else words[i][1]
    raise SystemExit(f'layout.py: the cue "{phrase}" (occurrence {nth}) is not in the words. Use the words as spoken.')


def snap(t):
    return round(round(t * FPS) / FPS, 4)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--layout", required=True)
    ap.add_argument("--words", required=True)
    ap.add_argument("--duration", type=float, required=True, help="base duration in seconds")
    ap.add_argument("--out", required=True)
    ap.add_argument("--hf")
    ap.add_argument("--edl-in")
    ap.add_argument("--edl-out")
    ap.add_argument("--seg-starts", help="seg_starts.py output: moves the zooms into cut_base's EDL time")
    a = ap.parse_args()

    spec = json.loads(Path(a.layout).read_text())
    words = load_words(a.words)
    lead = float(spec.get("lead", 0.12))
    D = a.duration
    wins = spec["windows"]
    starts, named = [], {}
    for i, w in enumerate(wins):
        if w.get("mode") not in MODES:
            raise SystemExit(f"layout.py: window {i} has the mode {w.get('mode')!r}; use split, canvas or face")
        if "name" in w:
            name = w["name"]
            if not isinstance(name, str) or not name.strip():
                raise SystemExit(f"layout.py: window {i} has the name {name!r}; a name is a few words of text")
            if norm_phrase(name) in named:
                raise SystemExit(f"layout.py: windows {named[norm_phrase(name)]} and {i} are both named {name!r}; "
                                 "give each window its own name")
            named[norm_phrase(name)] = i
        t = float(w["t"]) if "t" in w else cue(words, w["at"], int(w.get("nth", 1))) - lead
        starts.append(snap(max(0.0, t)))
    if starts[0] != 0:
        raise SystemExit("layout.py: the first window must start at 0, so frame 0 has a layout")
    for i in range(1, len(starts)):
        if starts[i] <= starts[i - 1]:
            raise SystemExit(f"layout.py: window {i} ({wins[i].get('at')!r}) starts at {starts[i]}s, "
                             f"not after window {i - 1} ({starts[i - 1]}s)")
    ends = starts[1:] + [snap(D)]

    out_w, emph, warn = [], [], []
    for i, w in enumerate(wins):
        t0, t1 = starts[i], ends[i]
        out_w.append({"mode": w["mode"], "t0": t0, "t1": t1, "at": w.get("at", w.get("t")), "name": w.get("name")})
        if t1 - t0 < 0.5:
            warn.append(f"window {i} ({w['mode']} at {t0}s) is only {t1 - t0:.2f}s long")
        for z in w.get("zooms", []):
            if w["mode"] != "face":
                raise SystemExit(f"layout.py: a zoom in a {w['mode']} window at {t0}s; zooms belong to FACE windows only")
            row = float(z.get("fy", FACE_ROW))
            if z["kind"] == "punch":
                z0 = snap(cue(words, z["at"], int(z.get("nth", 1))) - 0.05)
                z1 = snap(cue(words, z["until"], int(z.get("until_nth", 1))) - lead) if z.get("until") else t1
                ease = PUNCH_EASE
            elif z["kind"] == "slow":
                z0 = snap(cue(words, z["at"], int(z.get("nth", 1))) - lead) if z.get("at") else t0
                z1 = t1
                ease = round(max(z1 - z0, 1.001), 3)  # longer than 1 s selects cut_base's sine slow push
                if z1 - z0 < 1.5:
                    warn.append(f"the slow push {z0}-{z1}s is under 1.5s; it barely moves (use a punch or a longer window)")
            else:
                raise SystemExit(f"layout.py: the zoom kind {z['kind']!r} is unknown; use punch or slow")
            if not (t0 <= z0 < z1 <= t1):
                raise SystemExit(f"layout.py: the {z['kind']} zoom {z0}-{z1}s falls outside its face window {t0}-{t1}s")
            emph.append([z0, z1, float(z["z"]), row, ease])
    emph.sort()
    for x, y in zip(emph, emph[1:]):
        if y[0] < x[1]:
            warn.append(f"zooms overlap at {y[0]}s (the zoom factors add)")

    share = {m: round(100 * sum(w["t1"] - w["t0"] for w in out_w if w["mode"] == m) / D, 1) for m in sorted(MODES)}
    resolved = {"duration": D, "lead": lead, "windows": out_w, "emphasis": emph, "share_pct": share,
                "counts": {"punch": sum(1 for e in emph if e[4] <= 1), "slow": sum(1 for e in emph if e[4] > 1)}}
    Path(a.out).write_text(json.dumps(resolved, indent=1))

    if a.hf:
        lib = Path(a.hf) / "lib"
        lib.mkdir(parents=True, exist_ok=True)
        # [mode, t0, t1, name]: the name UIS.win finds it by is its own name, else the phrase it starts at
        entries = [[w["mode"], w["t0"], w["t1"], w["name"] or (w["at"] if isinstance(w["at"], str) else None)]
                   for w in out_w]
        (lib / "layout.js").write_text("window.UIS_LAYOUT=" + json.dumps(entries) + ";\n")
    if a.edl_in and a.edl_out:
        edl = json.loads(Path(a.edl_in).read_text())
        out_emph = emph
        if a.seg_starts:
            seg = json.loads(Path(a.seg_starts).read_text())
            actual, cum, dur = seg["actual_starts"], seg["edl_starts"], seg["durations"]
            out_emph = []
            for z0, z1, zf, row, ease in emph:
                i = max(k for k in range(len(actual)) if actual[k] <= z0 + 1e-6)
                if i + 1 < len(actual) and z1 > actual[i + 1] + 1e-6:
                    warn.append(f"the zoom {z0}-{z1}s crosses the take join at {actual[i + 1]}s; it now ends there")
                    z1 = actual[i + 1]
                e0 = round(cum[i] + (z0 - actual[i]), 4)
                e1 = round(min(cum[i] + dur[i], cum[i] + (z1 - actual[i])), 4)
                out_emph.append([e0, e1, zf, row, ease])
                if abs(e0 - z0) > 1e-3:
                    print(f"  zoom {z0:.3f}s (base) -> {e0:.3f}s (EDL time, segment {i}, drift {z0 - e0:+.3f}s)")
        edl["emphasis"] = out_emph
        Path(a.edl_out).write_text(json.dumps(edl, indent=1))

    for w in out_w:
        print(f"  {w['t0']:7.2f}-{w['t1']:7.2f}  {w['mode']:6s}  {w['at']}")
    print(f"share %: {share}  | punch-ins {resolved['counts']['punch']}, slow pushes {resolved['counts']['slow']}")
    for e in emph:
        print(f"  zoom {e[0]:.2f}-{e[1]:.2f}s x{e[2]} {'slow' if e[4] > 1 else 'punch'}")
    for message in warn:
        print("WARN:", message, file=sys.stderr)


if __name__ == "__main__":
    main()
