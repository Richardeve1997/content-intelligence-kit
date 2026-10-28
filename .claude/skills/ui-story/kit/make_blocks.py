#!/usr/bin/env python3
"""make_blocks.py: blocks.json, the gap trims composite_v2.py applies, with a guard so no trim cuts speech.

Tight is the standard: every gap between words longer than 0.22 s is cut down to about 0.15 s (0.08 s kept
after the word, 0.07 s before the next). Long pauses make a Reel feel slow and cost viewers. --loose keeps
more air (gaps over 0.40 s cut to 0.30 s) for a Reel that should breathe. The head is the first word minus
0.08 s, on the frame grid.

The guard: a cut never removes the voiced tail of the word before a gap or the attack of the word after it
(the run of sound touching each word), and it shrinks around anything that sounds like speech (pitched,
with most of its energy between 300 and 3000 Hz), the same test check_trims.py uses. A lone click or breath
in the middle of a gap is cut. Feed it the CLEAN voice (light_voice.py's output), not the base: the clean
chain lifts quiet word tails, so a guard measured on the base can miss them.

--cut S E forces a cut (a spoken retake inside a keep segment). --keep S E protects a window from any trim
(a quoted clip that must stay in sync).

    python make_blocks.py <clean.wav> <base.words.json> <layout.resolved.json> <out blocks.json>
        [--speed 1.15] [--loose] [--gap 0.22] [--tail 0.08] [--lead 0.07] [--min-cut 0.08] [--floor -42]
        [--cut S E ...] [--keep S E ...]

It also prints the layout share after the trims and the length the Reel ships at.
"""
import argparse
import json
import subprocess
import sys

import numpy as np

FPS, SR = 60, 16000
snap = lambda t: round(round(t * FPS) / FPS, 4)


def pairs(values, flag):
    if len(values) % 2:
        raise SystemExit(f"{flag} takes pairs of numbers: a start and an end")
    return list(zip(values[0::2], values[1::2]))


def main():
    ap = argparse.ArgumentParser(description="Gap trims with a speech guard.")
    ap.add_argument("audio", help="the clean voice")
    ap.add_argument("words")
    ap.add_argument("layout")
    ap.add_argument("out")
    ap.add_argument("--speed", type=float, default=1.15)
    ap.add_argument("--loose", action="store_true", help="keep more air: gaps over 0.40 s cut to 0.30 s")
    ap.add_argument("--gap", type=float)
    ap.add_argument("--tail", type=float)
    ap.add_argument("--lead", type=float)
    ap.add_argument("--min-cut", type=float)
    ap.add_argument("--floor", type=float, default=-42.0, help="dB: a 20 ms frame above this is sound to keep")
    ap.add_argument("--cut", type=float, nargs="+", default=[], metavar="S E")
    ap.add_argument("--keep", type=float, nargs="+", default=[], metavar="S E")
    a = ap.parse_args()

    pick = lambda value, tight, loose: value if value is not None else (loose if a.loose else tight)
    GAP, TAIL, LEAD, MINCUT = (pick(a.gap, 0.22, 0.40), pick(a.tail, 0.08, 0.20), pick(a.lead, 0.07, 0.15),
                               pick(a.min_cut, 0.08, 0.20))
    FLOOR = a.floor
    forced, protect = pairs(a.cut, "--cut"), pairs(a.keep, "--keep")

    words = json.load(open(a.words))["words"]
    lay = json.load(open(a.layout))
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", a.audio, "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True).stdout
    audio = np.frombuffer(raw, np.float32)
    hop = int(0.02 * SR)
    env = np.array([20 * np.log10(np.sqrt((audio[i : i + hop] ** 2).mean()) + 1e-9) for i in range(0, len(audio) - hop, hop)])

    win = int(0.04 * SR)
    freqs = np.fft.rfftfreq(win, 1 / SR)
    in_band = (freqs > 300) & (freqs < 3000)
    lo_lag, hi_lag = int(SR / 400), int(SR / 70)
    n_frames = max(0, (len(audio) - win) // hop)
    rms = np.array([np.sqrt((audio[k * hop : k * hop + win] ** 2).mean() + 1e-12) for k in range(n_frames)])
    speech_level = np.percentile(rms, 90) if n_frames else 1.0

    def speechlike_frames(t0, t1):
        """Start times of 40 ms frames inside [t0, t1] that are loud (over -30 dB against speech) and speech-like."""
        out = []
        for k in range(max(0, int(t0 / 0.02)), min(n_frames, int(t1 / 0.02))):
            if 20 * np.log10(rms[k] / speech_level + 1e-12) <= -30:
                continue
            frame = audio[k * hop : k * hop + win] - audio[k * hop : k * hop + win].mean()
            ac = np.correlate(frame, frame, "full")[len(frame) - 1 :]
            pitch = float(ac[lo_lag:hi_lag].max() / (ac[0] + 1e-12))
            power = np.abs(np.fft.rfft(frame * np.hanning(len(frame)))) ** 2
            if pitch >= 0.3 and float(power[in_band].sum() / (power.sum() + 1e-12)) >= 0.35:
                out.append(k * 0.02)
        return out

    cuts = []
    for p, q in zip(words, words[1:]):
        if q["s"] - p["e"] <= GAP:
            continue
        c0, c1 = p["e"] + TAIL, q["s"] - LEAD
        if any(f0 <= c0 + 0.3 and c1 - 0.3 <= f1 for f0, f1 in forced):
            continue
        # keep the tail of the word before the gap and the attack of the word after it
        k = int(p["e"] / 0.02)
        while k + 1 < len(env) and (env[k] > FLOOR or env[k + 1] > FLOOR):
            k += 1
        c0 = max(c0, k * 0.02 + 0.02 + 0.03)
        k = min(len(env) - 1, int(q["s"] / 0.02) + 1)
        while k > 0 and env[k] > FLOOR:
            k -= 1
        c1 = min(c1, (k + 1) * 0.02 - 0.04)
        voiced = speechlike_frames(c0, c1)
        if voiced:
            before = [t for t in voiced if t < (c0 + c1) / 2]
            after = [t for t in voiced if t >= (c0 + c1) / 2]
            if before:
                c0 = max(c0, before[-1] + 0.04 + 0.03)
            if after:
                c1 = min(c1, after[0] - 0.03)
        if any(not (c1 <= k0 or c0 >= k1) for k0, k1 in protect):
            print(f"  (protected window: no trim at {c0:.3f}-{c1:.3f})")
            continue
        if c1 - c0 >= MINCUT:  # a smaller trim saves nothing and risks a word edge
            cuts.append([c0, c1])
    cuts += [list(f) for f in forced]
    cuts.sort()
    blocks, cur = [], snap(max(0.0, words[0]["s"] - 0.08))
    for c0, c1 in cuts:
        blocks.append([cur, snap(c0)])
        cur = snap(c1)
    blocks.append([cur, "end"])
    json.dump({"A": blocks}, open(a.out, "w"), indent=0)

    D = lay["duration"]

    def kept(t0, t1):
        return sum(max(0.0, min(t1, D if b1 == "end" else b1) - max(t0, b0)) for b0, b1 in blocks)

    total = kept(0, D)
    share = {}
    for w in lay["windows"]:
        share[w["mode"]] = share.get(w["mode"], 0) + kept(w["t0"], w["t1"])
    print(f"blocks {len(blocks)}  trimmed {total:.2f}s  ships at {a.speed}x as {total / a.speed:.2f}s")
    print("trimmed share %:", {k: round(100 * v / total, 1) for k, v in share.items()} if total else {})
    for c0, c1 in cuts:
        print(f"  cut {c0:7.3f}-{c1:7.3f} ({c1 - c0:.2f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
