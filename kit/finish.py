"""The delivery pass for a finished edit: speed, music bed, loudness, one picture encode.

`finish(src, out, ...)` does four things, in this order:

1. Picture. The edit is sped up by `speed` (1.15 for a Reel) and encoded once, with the hardware
   encoder on Apple silicon. At speed 1.0 the picture is not touched at all. Either way the delivered
   file gets the picture by stream copy, so nothing downstream of this encode re-encodes it.
2. Mix. The voice is sped up with its pitch kept (atempo). The bed, when there is one, is levelled to
   `music_lufs` (-24 LUFS sits it well under a voice at -13), looped to the length of the edit, faded
   in over `fade_in` seconds and out over the last `fade_out` seconds, then mixed in once.
3. Loudness. The mix is brought to -13 LUFS integrated with a plain gain, and a limiter running at four
   times the sample rate holds the peaks under `mix_peak` (a linear ceiling: 0.74 is about -2.6 dBFS).
   The gain is solved again after the limiter until the level sits within 0.05 LU of the target. Only
   gain and peak limiting are used, so the voice keeps its own dynamics and the bed keeps its place
   under it. Its loudness range is measured and reported against the 11 LU target, not forced.
4. Delivery. One AAC encode at 192 kb/s, 48 kHz, muxed with the picture. The delivered file is
   measured, and if the encode pushed the true peak above -1.5 dBTP the ceiling is lowered and the
   sound alone is made again.

Command line:

    python -m kit.finish <src> <out> [--speed 1.15] [--music auto|none|<file>] [--mood <folder>]
        [--music-lufs -24] [--mix-peak 0.74] [--fade-in 0.3] [--fade-out 1.2]
    python -m kit.finish measure <file>

`--music auto` takes a bed from the creator's library with kit.music.pick (skipping the last one used)
and logs it, or finishes with no bed when the library is empty.
"""

from __future__ import annotations

import argparse
import functools
import math
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from kit import media, music
from kit.config import KitError

__all__ = ["HOUSE", "TARGET", "KitError", "Loudness", "finish", "main", "measure", "mix_level"]

TARGET = {"I": -13.0, "TP": -1.5, "LRA": 11.0}
HOUSE = {"speed": 1.15, "music_lufs": -24.0, "mix_peak": 0.74, "fade_in": 0.3, "fade_out": 1.2}
_TOLERANCE = 0.05  # LU, how close the solved gain gets the level to the target
_SR = 48000


@dataclass(frozen=True)
class Loudness:
    integrated: float  # LUFS
    true_peak: float  # dBTP
    lra: float  # LU


def _run(cmd: list[str], what: str) -> subprocess.CompletedProcess:
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0:
        tail = "\n".join(done.stderr.strip().splitlines()[-6:])
        raise KitError(f"ffmpeg could not {what}. What it said:\n{tail}")
    return done


def measure(path: Path) -> Loudness:
    """Integrated loudness, true peak and loudness range of a file's sound (EBU R128, via ffmpeg)."""
    done = _run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af", "ebur128=peak=true",
         "-f", "null", "-"],
        f"measure the loudness of {Path(path).name}",
    )
    text = done.stderr
    summary = text[text.rfind("Summary:") :] if "Summary:" in text else text

    def number(pattern: str) -> float:
        found = re.search(pattern, summary)
        if not found:
            return -math.inf
        value = found.group(1)
        return -math.inf if value == "-inf" else float(value)

    return Loudness(
        integrated=number(r"I:\s+(-?[\d.]+|-inf) LUFS"),
        true_peak=number(r"Peak:\s+(-?[\d.]+|-inf) dBFS"),
        lra=max(0.0, number(r"LRA:\s+(-?[\d.]+) LU")),
    )


def _atempo(speed: float) -> str:
    """atempo takes 0.5 to 2.0 per step, so larger or smaller factors are chained."""
    steps, left = [], speed
    while left > 2.0:
        steps.append(2.0)
        left /= 2.0
    while left < 0.5:
        steps.append(0.5)
        left /= 0.5
    steps.append(left)
    return ",".join(f"atempo={step:.6f}" for step in steps)


@functools.lru_cache(maxsize=1)
def _aac() -> list[str]:
    """Apple's AAC encoder where ffmpeg has it (cleaner peaks), otherwise ffmpeg's own."""
    listing = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    name = "aac_at" if any(line.split()[1:2] == ["aac_at"] for line in listing.splitlines() if line.strip()) else "aac"
    return ["-c:a", name, "-b:a", "192k", "-ar", str(_SR), "-ac", "2"]


def _check(src: Path, music_file: Path | None, speed: float, mix_peak: float | None) -> None:
    if not Path(src).is_file():
        raise KitError(f"{src} does not exist, so there is nothing to finish.")
    if music_file is not None and not Path(music_file).is_file():
        raise KitError(f"The music bed {music_file} does not exist. Pick another track, or finish with no bed.")
    if not speed or speed <= 0:
        raise KitError(f"The speed must be above 0, such as 1.15. It was {speed}.")
    if mix_peak is not None and not 0.0625 <= mix_peak <= 1.0:
        raise KitError(f"The mix peak is a linear ceiling between 0.0625 and 1, such as 0.74. It was {mix_peak}.")


def _mix(src: Path, out: Path, *, speed: float, music_file: Path | None, music_lufs: float, fade_in: float,
         fade_out: float, has_voice: bool, length: float) -> Path:
    """The voice (sped up) and the bed (levelled, looped, faded), mixed once into a float WAV."""
    inputs = ["-i", str(src)]
    graph = []
    if has_voice:
        graph.append(f"[0:a]aformat=sample_rates={_SR}:channel_layouts=stereo,{_atempo(speed)},"
                     f"atrim=0:{length:.4f},asetpts=PTS-STARTPTS[voice]")
    else:
        graph.append(f"anullsrc=r={_SR}:cl=stereo,atrim=0:{length:.4f}[voice]")
    labels = "[voice]"
    if music_file is not None:
        inputs += ["-stream_loop", "-1", "-i", str(music_file)]
        fade_from = max(0.0, length - fade_out)
        bed = (f"[1:a]aformat=sample_rates={_SR}:channel_layouts=stereo,atrim=0:{length + 4:.4f},asetpts=PTS-STARTPTS,"
               f"loudnorm=I={music_lufs}:TP=-1.5:LRA=11,aresample={_SR},atrim=0:{length:.4f},asetpts=PTS-STARTPTS")
        if fade_in > 0:
            bed += f",afade=t=in:st=0:d={fade_in}"
        if fade_out > 0:
            bed += f",afade=t=out:st={fade_from:.4f}:d={fade_out}"
        graph.append(bed + "[bed]")
        labels += "[bed]"
        graph.append(f"{labels}amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix]")
    else:
        graph.append("[voice]anull[mix]")
    _run(["ffmpeg", "-nostdin", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(graph), "-map", "[mix]",
          "-c:a", "pcm_f32le", "-ar", str(_SR), str(out)], "mix the voice and the bed")
    return out


def _levelled(mix: Path, out: Path, gain_db: float, ceiling: float) -> Path:
    chain = (f"volume={gain_db:.4f}dB,aresample={_SR * 4},alimiter=limit={ceiling:.5f}:level=0:latency=1,"
             f"aresample={_SR}")
    _run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(mix), "-af", chain, "-c:a", "pcm_f32le",
          str(out)], "level the mix")
    return out


def _solve(mix: Path, work: Path, ceiling: float) -> Path:
    """Gain then limit, re-solving the gain until the result sits on the loudness target."""
    level = measure(mix).integrated
    if not math.isfinite(level):
        return _levelled(mix, work / "levelled.wav", 0.0, ceiling)  # silence: nothing to normalise
    gain = TARGET["I"] - level
    out = work / "levelled.wav"
    for _ in range(5):
        _levelled(mix, out, gain, ceiling)
        reached = measure(out).integrated
        if abs(reached - TARGET["I"]) <= _TOLERANCE:
            break
        gain += TARGET["I"] - reached
    return out


def mix_level(src: Path, *, speed: float, music: Path | None, music_lufs: float, fade_in: float,
              fade_out: float) -> float:
    """The integrated loudness of the voice and bed mix before the final gain (LUFS)."""
    _check(src, music, speed, None)
    info = media.probe(src)
    with tempfile.TemporaryDirectory(prefix="kit-mix-") as folder:
        mix = _mix(Path(src), Path(folder) / "mix.wav", speed=speed, music_file=music, music_lufs=music_lufs,
                   fade_in=fade_in, fade_out=fade_out, has_voice=info.has_audio, length=info.duration / speed)
        return measure(mix).integrated


def finish(
    src: Path, out: Path, *, speed: float = HOUSE["speed"], music: Path | None = None,
    music_lufs: float = HOUSE["music_lufs"], mix_peak: float = HOUSE["mix_peak"], fade_in: float = HOUSE["fade_in"],
    fade_out: float = HOUSE["fade_out"],
) -> Path:
    """Speed, bed, loudness and delivery for `src`, written to `out`. Returns `out`."""
    src, out = Path(src), Path(out)
    _check(src, music, speed, mix_peak)
    if src.resolve() == out.resolve():
        raise KitError("The finished file must be a different file from the edit it is made from.")
    info = media.probe(src)
    length = info.duration / speed
    out.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".finish-", dir=out.parent))
    try:
        with media.heavy_slot(label=f"finish {out.name}"):
            if abs(speed - 1.0) < 1e-6:
                picture = src
            else:
                picture = work / "picture.mp4"
                rate = f"{info.fps:.3f}".rstrip("0").rstrip(".") if info.fps else "60"
                _run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(src), "-an",
                      "-vf", f"setpts=PTS/{speed},fps={rate}", *media.encoder_args(), "-movflags", "+faststart",
                      str(picture)], "speed up the picture")
            mix = _mix(src, work / "mix.wav", speed=speed, music_file=music, music_lufs=music_lufs, fade_in=fade_in,
                       fade_out=fade_out, has_voice=info.has_audio, length=length)
            ceiling = mix_peak
            for _ in range(3):
                levelled = _solve(mix, work, ceiling)
                _run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(picture), "-i", str(levelled),
                      "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", *_aac(), "-movflags", "+faststart",
                      str(out)], "write the finished file")
                reading = measure(out)
                if reading.true_peak <= TARGET["TP"]:
                    break
                ceiling *= 10 ** ((TARGET["TP"] - reading.true_peak - 0.3) / 20)  # the encode overshot: go lower
            else:
                raise KitError(
                    f"The finished sound still peaks at {reading.true_peak:.1f} dBTP, above the "
                    f"{TARGET['TP']} dBTP limit. Try a lower --mix-peak, such as 0.6."
                )
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if abs(reading.integrated - TARGET["I"]) > 0.5:
        raise KitError(
            f"The finished file measures {reading.integrated:.1f} LUFS, more than 0.5 LU from {TARGET['I']:g}."
        )
    note = f", loudness range {reading.lra:.1f} LU" + (" (wider than 11)" if reading.lra > TARGET["LRA"] else "")
    print(f"finished {out} at {speed:g}x: {reading.integrated:.1f} LUFS, true peak {reading.true_peak:.1f} dBTP{note}"
          + (f", bed {Path(music).name}" if music else ", no music bed"))
    return out


def main(argv: list[str] | None = None) -> int:
    args_in = sys.argv[1:] if argv is None else argv
    if args_in[:1] == ["measure"]:
        parser = argparse.ArgumentParser(prog="python -m kit.finish measure")
        parser.add_argument("file", type=Path)
        args = parser.parse_args(args_in[1:])
        try:
            reading = measure(args.file)
        except KitError as problem:
            print(problem)
            return 1
        print(f"{reading.integrated:.1f} LUFS integrated, true peak {reading.true_peak:.1f} dBTP, "
              f"loudness range {reading.lra:.1f} LU")
        return 0

    parser = argparse.ArgumentParser(prog="python -m kit.finish", description="Speed, music bed, loudness, delivery.")
    parser.add_argument("src", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--speed", type=float, default=HOUSE["speed"])
    parser.add_argument("--music", default="auto", help="auto (pick from the library), none, or a file")
    parser.add_argument("--mood", help="the music folder to pick from with --music auto")
    parser.add_argument("--slug", help="the video name to log the bed under (default: the output's folder name)")
    parser.add_argument("--music-lufs", type=float, default=HOUSE["music_lufs"])
    parser.add_argument("--mix-peak", type=float, default=HOUSE["mix_peak"])
    parser.add_argument("--fade-in", type=float, default=HOUSE["fade_in"])
    parser.add_argument("--fade-out", type=float, default=HOUSE["fade_out"])
    args = parser.parse_args(args_in)

    bed: Path | None
    if args.music == "none":
        bed = None
    elif args.music == "auto":
        bed = music.pick(args.mood)
        if bed is None:
            where = f"me/music/{args.mood}/" if args.mood else "me/music/"
            print(f"No track in {where}, so this Reel has no music bed. Add tracks there to give it one.")
    else:
        bed = Path(args.music)
    try:
        finish(args.src, args.out, speed=args.speed, music=bed, music_lufs=args.music_lufs, mix_peak=args.mix_peak,
               fade_in=args.fade_in, fade_out=args.fade_out)
        if bed is not None and args.music == "auto":
            music.log(bed, args.slug or args.out.resolve().parent.name)
    except KitError as problem:
        print(problem)
        return 1
    except ValueError as problem:
        print(problem)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
