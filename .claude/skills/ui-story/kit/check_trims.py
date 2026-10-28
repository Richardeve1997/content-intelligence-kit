#!/usr/bin/env python3
"""check_trims.py: prove that no gap trim in blocks.json cuts real speech, BEFORE the long composite.

The gap trims trust the aligned word ends, and the aligner cannot read digits, so "37 days" or "500 to
1,000" can be placed up to a second early and a trim then cuts into the spoken words. This measures the
audio instead of trusting the times: every removed span must be quiet.

A removed span is flagged only when a 40 ms window is both LOUD (above --floor-db against the speech level)
and SPEECH-LIKE: its pitch autocorrelation in the 70 to 400 Hz range is at least --voiced, and at least
--band of its energy is between 300 and 3000 Hz. Pitch alone is fooled by low rumble and thumps; the band
test is what separates words from breaths, clicks and thumps, which are reported as noise without failing.

    python check_trims.py --audio <clean.wav> --blocks blocks.json [--floor-db -30] [--voiced 0.3] [--band 0.35]

Exit 1 if any removed span holds speech-like audio.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

SR = 16000


def main():
    ap = argparse.ArgumentParser(description="Prove the gap trims cut no speech.")
    ap.add_argument("--audio", required=True)
    ap.add_argument("--blocks", required=True)
    ap.add_argument("--floor-db", type=float, default=-30.0, help="dB against the speech level")
    ap.add_argument("--voiced", type=float, default=0.3, help="pitch autocorrelation that counts as voiced")
    ap.add_argument("--band", type=float, default=0.35, help="smallest share of energy in 300-3000 Hz for a word")
    a = ap.parse_args()
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", a.audio, "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
                         capture_output=True).stdout
    x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
    D = len(x) / SR
    keep = json.loads(Path(a.blocks).read_text())["A"]
    keep = [(float(s), D if e == "end" else float(e)) for s, e in keep]
    cuts = []
    if keep[0][0] > 0:
        cuts.append((0.0, keep[0][0]))
    cuts += [(e0, s1) for (_, e0), (s1, _) in zip(keep, keep[1:]) if s1 > e0]
    if keep[-1][1] < D:
        cuts.append((keep[-1][1], D))
    win = int(0.04 * SR)
    frames = x[: len(x) // win * win].reshape(-1, win)
    rms = np.sqrt((frames**2).mean(axis=1) + 1e-12)
    speech = np.percentile(rms, 90)
    lo_lag, hi_lag = int(SR / 400), int(SR / 70)
    freqs = np.fft.rfftfreq(win, 1 / SR)
    in_band = (freqs > 300) & (freqs < 3000)

    def speechlike(frame):
        f0 = frame - frame.mean()
        ac = np.correlate(f0, f0, "full")[len(f0) - 1 :]
        pitch = float(ac[lo_lag:hi_lag].max() / (ac[0] + 1e-12))
        power = np.abs(np.fft.rfft(f0 * np.hanning(len(f0)))) ** 2
        return pitch >= a.voiced and float(power[in_band].sum() / (power.sum() + 1e-12)) >= a.band

    bad = 0
    for s, e in cuts:
        i0, i1 = int(s * SR / win), max(int(s * SR / win) + 1, int(e * SR / win))
        db = 20 * np.log10(rms[i0:i1] / speech + 1e-12)
        loud = [i0 + k for k, v in enumerate(db) if v > a.floor_db]
        voiced = [i for i in loud if speechlike(frames[i])]
        peak = float(db.max()) if len(db) else -99
        tag = ("   <-- SPEECH-LIKE at " + ", ".join(f"{i * win / SR:.2f}s" for i in voiced[:4]) + ": re-time or listen"
               if voiced else ("   (loud, not speech: a breath, click or thump)" if loud else ""))
        bad += bool(voiced)
        print(f"  cut {s:7.3f}-{e:7.3f} ({e - s:.2f}s)  loudest 40 ms {peak:6.1f} dB against speech{tag}")
    print(f"{len(cuts)} cuts, {bad} holding speech-like audio (floor {a.floor_db} dB, pitch >= {a.voiced}, "
          f"in band >= {a.band})")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
