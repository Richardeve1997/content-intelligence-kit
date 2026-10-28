#!/usr/bin/env python3
"""light_voice.py: the light voice chain, from the base's sound to the clean voice the Reel ships with.

A high-pass at 70 Hz (rumble and desk thumps), a gentle compressor with no make-up gain, then a two-pass
linear loudness pass to -13 LUFS, true peak -1.5 dBTP, range 11 LU. It is light on purpose: heavier noise
reduction and EQ make a quiet take sound processed. The output is a WAV, so the only lossy step left is the
delivery encode in the finish.

    python light_voice.py <base.mp4> <clean.wav>
"""
import argparse
import json
import subprocess
import sys

CHAIN = "highpass=f=70,acompressor=threshold=-22dB:ratio=3:attack=10:release=200:knee=6,"
TARGET = "I=-13:TP=-1.5:LRA=11"


def main():
    ap = argparse.ArgumentParser(description="The light voice chain: high-pass, gentle compression, loudness.")
    ap.add_argument("base")
    ap.add_argument("out")
    a = ap.parse_args()
    measured = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-i", a.base, "-vn", "-af", f"{CHAIN}loudnorm={TARGET}:print_format=json",
         "-f", "null", "-"], capture_output=True, text=True).stderr
    block = measured[measured.rfind("{") : measured.rfind("}") + 1]
    try:
        m = json.loads(block)
    except json.JSONDecodeError:
        print(f"The loudness of {a.base} could not be measured. Does it have sound?")
        return 1
    second = (f"{CHAIN}loudnorm={TARGET}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
              f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true,"
              "aresample=48000")
    done = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", a.base, "-vn", "-af", second,
                           "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", a.out])
    if done.returncode:
        return done.returncode
    print(f"light voice -> {a.out} (the base measured {m['input_i']} LUFS)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
