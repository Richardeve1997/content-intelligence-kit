"""Sound effects for the mini me style, made from numbers: a whoosh for every field flip, a thud for every stamp.

The kit ships no audio files. Both sounds are synthesised here with numpy from a fixed seed, so the same
call always writes the same file, and nothing comes from a sample library whose licence is unknown.

* whoosh: air moving past. Two bands of noise, a low "whoo" and a brighter "sh", cross over as it
  passes, it swells to its loudest moment and dies away, and it travels from left to right.
* thud: a stamp landing. A low sine that drops in pitch (a kick drum's body), a soft harmonic and a
  short click for the impact, all gone in about a quarter of a second.

Each file is 48 kHz, 16-bit stereo PCM, normalised to just under full scale. PEAK says where in the file
the loudest moment sits, so a sound can be placed with that moment on a cut or a landing: `track()` does
that for a whole Reel and writes one WAV of every hit, ready to mix under the voice.

Command line:

    python -m kit.sfx whoosh <out.wav>
    python -m kit.sfx thud <out.wav>
"""

from __future__ import annotations

import sys
import wave
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

__all__ = ["LEVEL", "PEAK", "SOUNDS", "SR", "Hit", "samples", "thud", "track", "whoosh", "main"]

SR = 48000
LEVEL = 0.89  # the loudest sample, about -1 dBFS
PEAK = {"whoosh": 0.16, "thud": 0.008}  # seconds from the start of each file to its loudest moment
SOUNDS = tuple(PEAK)
_SEED = {"whoosh": 1307, "thud": 2711}


@dataclass(frozen=True)
class Hit:
    """One sound in a track: which one, the time its loudest moment lands on, and its level (1.0 = as made)."""

    kind: str
    t: float
    gain: float = 1.0


# --- the two sounds ------------------------------------------------------------------------------------------


def _band(noise, low_hz: float, width_octaves: float):
    """`noise` filtered to a soft band around low_hz (a bell on a log-frequency scale), at unit RMS."""
    import numpy as np

    spectrum = np.fft.rfft(noise)
    freqs = np.fft.rfftfreq(len(noise), 1 / SR)
    octaves = np.log2(np.maximum(freqs, 1.0) / low_hz)
    shaped = np.fft.irfft(spectrum * np.exp(-0.5 * (octaves / width_octaves) ** 2), len(noise))
    return shaped / (np.sqrt(np.mean(shaped**2)) + 1e-12)


def _fade_ends(signal, seconds: float = 0.01):
    import numpy as np

    size = min(len(signal) // 4, int(seconds * SR))
    ramp = np.linspace(0.0, 1.0, size)
    signal[:size] *= ramp[:, None] if signal.ndim == 2 else ramp
    signal[-size:] *= ramp[::-1, None] if signal.ndim == 2 else ramp[::-1]
    return signal


def _normalise(signal):
    import numpy as np

    return signal * (LEVEL / (np.abs(signal).max() + 1e-12))


def _whoosh():
    import numpy as np

    length = 0.42
    n = int(length * SR)
    t = np.arange(n) / SR
    noise = np.random.default_rng(_SEED["whoosh"]).standard_normal(n)
    low = _band(noise, 420.0, 0.9)
    high = _band(noise, 3000.0, 0.8) * 0.8
    peak = PEAK["whoosh"]
    # a swell up to the loudest moment, then a quick fall
    envelope = np.where(t < peak, (t / peak) ** 2, np.exp(-(t - peak) / 0.06))
    # the bright band takes over as the air passes: an equal-power crossfade centred on the peak
    blend = 1.0 / (1.0 + np.exp(-(t - peak) / 0.05))
    mono = envelope * (low * np.cos(blend * np.pi / 2) + high * np.sin(blend * np.pi / 2))
    # it travels from the left to the right (equal-power pan from -0.6 to +0.6)
    pan = np.linspace(-0.6, 0.6, n)
    angle = (pan + 1.0) * np.pi / 4
    stereo = np.stack([mono * np.cos(angle), mono * np.sin(angle)], axis=1)
    return _normalise(_fade_ends(stereo))


def _thud():
    import numpy as np

    length = 0.3
    n = int(length * SR)
    t = np.arange(n) / SR
    pitch = 48.0 + (110.0 - 48.0) * np.exp(-t / 0.04)  # the body drops from 110 Hz to 48 Hz
    phase = 2 * np.pi * np.cumsum(pitch) / SR
    attack = 1.0 - np.exp(-t / 0.002)
    body = np.sin(phase) * attack * np.exp(-t / 0.07)
    harmonic = 0.25 * np.sin(2 * phase) * attack * np.exp(-t / 0.035)
    noise = np.random.default_rng(_SEED["thud"]).standard_normal(n)
    spectrum = np.fft.rfft(noise)
    freqs = np.fft.rfftfreq(n, 1 / SR)
    click = np.fft.irfft(spectrum * (freqs < 2000.0), n)
    click = 0.35 * click / (np.abs(click).max() + 1e-12) * np.exp(-t / 0.004)
    mono = body + harmonic + click
    stereo = np.stack([mono, mono], axis=1)
    return _normalise(_fade_ends(stereo, 0.015))


_MAKERS = {"whoosh": _whoosh, "thud": _thud}


@lru_cache(maxsize=None)
def _made(kind: str):
    if kind not in _MAKERS:
        raise ValueError(f'"{kind}" is not a sound the kit makes. Use whoosh or thud.')
    made = _MAKERS[kind]()
    made.setflags(write=False)
    return made


def samples(kind: str):
    """The sound as a float array of shape (samples, 2) at SR, peaking at LEVEL."""
    return _made(kind).copy()


# --- writing files ----------------------------------------------------------------------------------------------


def _write(signal, out: Path) -> Path:
    import numpy as np

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(np.round(signal * 32767.0), -32768, 32767).astype("<i2")
    with wave.open(str(out), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.writeframes(pcm.tobytes())
    return out


def whoosh(out: Path) -> Path:
    """Write the whoosh to `out` (a WAV file) and return its path. Its loudest moment is PEAK["whoosh"] in."""
    return _write(_made("whoosh"), out)


def thud(out: Path) -> Path:
    """Write the thud to `out` (a WAV file) and return its path. Its loudest moment is PEAK["thud"] in."""
    return _write(_made("thud"), out)


def track(hits: Iterable[Hit], length: float, out: Path) -> Path:
    """One WAV of `length` seconds with every hit placed so its loudest moment lands on its time.

    A hit that starts before 0 s loses its head and still peaks on time; one past the end is left out.
    When hits overlap and the sum would clip, the whole track is turned down to stay under full scale.
    """
    import numpy as np

    total = int(round(length * SR))
    mix = np.zeros((total, 2))
    for hit in hits:
        sound = _made(hit.kind)  # checks the name before anything is placed
        start = int(round((hit.t - PEAK[hit.kind]) * SR))
        a, b = max(0, start), min(total, start + len(sound))
        if b <= a:
            continue
        mix[a:b] += hit.gain * sound[a - start : b - start]
    loudest = np.abs(mix).max() if total else 0.0
    if loudest > 0.99:
        mix *= 0.99 / loudest
    return _write(mix, out)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[0] not in SOUNDS:
        print("Use: python -m kit.sfx whoosh|thud <out.wav>")
        return 2
    made = whoosh(Path(args[1])) if args[0] == "whoosh" else thud(Path(args[1]))
    print(f"{args[0]} -> {made}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
