"""Word timings for a take: what was said, and when each word starts and ends.

`words(audio)` returns a list of Word(text, start, end), in seconds on the file's own timeline.

How it works, and why:

* The audio is split at real pauses first (quieter than -45 dB for at least 0.28 s). Whole-file speech
  models drift on long takes, and a word placed a second early makes a later cut or trim land inside
  speech. A chunk that starts and ends on real speech keeps every word close to where it was said.
* Each chunk is transcribed on its own: mlx-whisper (large-v3-turbo) on a Mac with Apple silicon,
  faster-whisper everywhere else. KIT_ASR_MODEL names another model.
* With align=True (the default) each chunk is then force-aligned with WhisperX (wav2vec2), which puts
  word edges within a few hundredths of a second. wav2vec2 cannot read digits, so numbers and dotted
  names are aligned in their spoken form ("37" as "thirty seven", "21st.dev" as "twenty first dot dev")
  and folded back into the word as written. Without WhisperX, or for a language it has no model for,
  the speech model's own word times are used and a note says so.

Transcription is heavy, so it runs inside a heavy-job slot (kit.media.heavy_slot).

Command line (writes the words file the UI story kit reads):

    python -m kit.transcribe <media> --out base.words.json [--no-align] [--lines]
        [--drop S E ...] [--split T ...] [--language en]

`--drop S E` leaves out words that start between S and E (a retake that will be cut), and `--split T`
forces a chunk boundary at T seconds (a restart that the pause finder merged into one chunk).
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import platform
import re
import subprocess
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from kit import media
from kit.config import KitError

__all__ = [
    "NO_SPEECH_MODEL",
    "Word",
    "audio_samples",
    "chunk_text",
    "load",
    "main",
    "speech_chunks",
    "spoken_form",
    "words",
    "write_json",
]

SR = 16000
MLX_MODEL = "mlx-community/whisper-large-v3-turbo"
FASTER_MODEL = "small"
# --inexact keeps the other groups: without it uv removes every group the command does not name.
NO_SPEECH_MODEL = "No speech model is installed. Install the video tools with: uv sync --group video --inexact"
_FRAME = 0.02  # seconds per energy frame
_FLOOR_DB = -45.0
_MIN_GAP = 0.28
_PAD = 0.12


@dataclass(frozen=True)
class Word:
    text: str
    start: float
    end: float


# --- audio -------------------------------------------------------------------------------------------


def audio_samples(path: Path):
    """The file's sound as 16 kHz mono float32 samples."""
    import numpy as np

    done = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
        capture_output=True,
    )
    if done.returncode != 0:
        raise KitError(f"{Path(path).name} has no sound ffmpeg can read.")
    return np.frombuffer(done.stdout, np.float32).copy()


def _duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    ).stdout.strip()
    try:
        return round(float(out), 3)
    except ValueError:
        return 0.0


def speech_chunks(
    samples, sr: int = SR, *, floor_db: float = _FLOOR_DB, min_gap: float = _MIN_GAP, pad: float = _PAD,
    min_length: float = 0.25,
) -> list[tuple[float, float]]:
    """Stretches of speech, as (start, end) seconds: loud runs joined across pauses shorter than `min_gap`."""
    import numpy as np

    hop = int(_FRAME * sr)
    if len(samples) < hop:
        return []
    count = len(samples) // hop
    frames = samples[: count * hop].reshape(count, hop)
    level = 20 * np.log10(np.sqrt((frames**2).mean(axis=1)) + 1e-9)
    loud = level > floor_db
    duration = len(samples) / sr
    chunks, i = [], 0
    while i < count:
        if not loud[i]:
            i += 1
            continue
        j = i
        while j < count:
            if loud[j]:
                j += 1
                continue
            k = j
            while k < count and not loud[k]:
                k += 1
            if (k - j) * _FRAME < min_gap and k < count:
                j = k
                continue
            break
        start, end = max(0.0, i * _FRAME - pad), min(duration, j * _FRAME + pad)
        if end - start > min_length:
            chunks.append((round(start, 3), round(end, 3)))
        i = j
    return chunks


# --- the speech models ----------------------------------------------------------------------------------


def _use_mlx() -> bool:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        return False
    try:
        import importlib.util

        return importlib.util.find_spec("mlx_whisper") is not None
    except (ImportError, ValueError):
        return False


@functools.lru_cache(maxsize=2)
def _faster_model(name: str):
    from faster_whisper import WhisperModel

    return WhisperModel(name, device="cpu", compute_type="int8")


def _asr(samples, *, word_times: bool, language: str | None = None) -> dict:
    """{"text", "language", "words": [(word, start, end)]} for a stretch of 16 kHz samples.

    Third-party models print deprecation notes the creator cannot act on, so warnings are silenced here.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if _use_mlx():
            import mlx_whisper

            result = mlx_whisper.transcribe(
                samples, path_or_hf_repo=os.environ.get("KIT_ASR_MODEL") or MLX_MODEL, language=language,
                temperature=0.0, condition_on_previous_text=False, word_timestamps=word_times, verbose=None,
            )
            found = [
                (w["word"].strip(), float(w["start"]), float(w["end"]))
                for segment in result.get("segments", [])
                for w in segment.get("words", [])
            ]
            return {"text": result.get("text", "").strip(), "language": result.get("language"), "words": found}
        try:
            model = _faster_model(os.environ.get("KIT_ASR_MODEL") or FASTER_MODEL)
        except ImportError:
            raise KitError(NO_SPEECH_MODEL) from None
        segments, info = model.transcribe(samples, language=language, word_timestamps=word_times, vad_filter=False,
                                          condition_on_previous_text=False)
        segments = list(segments)
        found = [(w.word.strip(), float(w.start), float(w.end)) for s in segments for w in (s.words or [])]
        return {"text": " ".join(s.text.strip() for s in segments).strip(), "language": info.language, "words": found}


@functools.lru_cache(maxsize=4)
def _aligner(language: str):
    import whisperx

    return whisperx.load_align_model(language_code=language, device="cpu")


def _align(samples, text: str, language: str):
    """WhisperX word times for `text` inside `samples`, or None when alignment is not available."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            import whisperx

            model, metadata = _aligner(language)
            result = whisperx.align(
                [{"start": 0.0, "end": len(samples) / SR, "text": text}], model, metadata, samples, "cpu",
                return_char_alignments=False,
            )
        except ImportError:
            return None
        except Exception as problem:  # no model for this language, or a download that failed
            print(f"Word alignment is not available here ({problem}). Using the speech model's own word times.")
            return None
    return [w for segment in result["segments"] for w in segment.get("words", [])]


# --- spoken forms ------------------------------------------------------------------------------------------


def spoken_form(token: str) -> list[str]:
    """The words a speaker says for a written token, so wav2vec2 can align it ("37" -> thirty, seven)."""
    from num2words import num2words

    core = re.sub(r"[^\w.\-']", "", token).strip(".")
    parts: list[str] = []
    for piece in [p for p in core.split("-") if p]:
        if "." in piece and not re.fullmatch(r"\d+\.\d+", piece):
            bits = [b for b in piece.split(".") if b]
            for index, bit in enumerate(bits):
                parts.append(bit)
                if index < len(bits) - 1:
                    parts.append("dot")
        else:
            parts.append(piece)
    out: list[str] = []
    for part in parts:
        ordinal = re.fullmatch(r"(\d+)(st|nd|rd|th)", part)
        if part == "dot":
            out.append("dot")
        elif ordinal:
            out += num2words(int(ordinal.group(1)), to="ordinal").replace("-", " ").split()
        elif re.fullmatch(r"(19|20)\d\d", part):
            out += num2words(int(part), to="year").replace("-", " ").split()
        elif re.fullmatch(r"\d+\.\d+", part):
            whole, fraction = part.split(".")
            out += num2words(int(whole)).replace("-", " ").split() + ["point"] + [num2words(int(d)) for d in fraction]
        elif re.fullmatch(r"\d+", part):
            out += num2words(int(part)).replace("-", " ").replace(",", "").split()
        else:
            out.append(part)
    return out or [token]


# --- the public calls ------------------------------------------------------------------------------------


def chunk_text(samples) -> str:
    """The plain transcript of one stretch of 16 kHz samples, heard on its own with no context."""
    return _asr(samples, word_times=False)["text"]


def _words_in(samples, offset: float, *, align: bool, language: str | None) -> tuple[list[Word], str | None]:
    if not align:
        heard = _asr(samples, word_times=True, language=language)
        return [Word(w, round(offset + s, 3), round(offset + e, 3)) for w, s, e in heard["words"] if w], heard["language"]
    heard = _asr(samples, word_times=False, language=language)
    text, found_language = heard["text"], heard["language"] or language or "en"
    if not text:
        return [], found_language
    tokens = text.split()
    spoken = [spoken_form(token) for token in tokens]
    aligned = _align(samples, " ".join(" ".join(s) for s in spoken), found_language)
    if aligned is None:
        return _words_in(samples, offset, align=False, language=found_language)
    out, k = [], 0
    for token, said in zip(tokens, spoken):
        group = aligned[k : k + len(said)]
        k += len(said)
        timed = [w for w in group if "start" in w and "end" in w]
        if timed:
            out.append(Word(token, round(offset + timed[0]["start"], 3), round(offset + timed[-1]["end"], 3)))
    return out, found_language


def words(
    audio: Path, *, align: bool = True, language: str | None = None,
    drop: Sequence[tuple[float, float]] = (), split: Iterable[float] = (),
) -> list[Word]:
    """Every spoken word in `audio` with its start and end in seconds, in order."""
    audio = Path(audio)
    if not audio.is_file():
        raise KitError(f"{audio} does not exist, so there is nothing to transcribe.")
    with media.heavy_slot(label=f"transcribe {audio.name}"):
        samples = audio_samples(audio)
        chunks = speech_chunks(samples)
        for cut in sorted(split):
            for index, (a, b) in enumerate(chunks):
                if a < cut < b:
                    chunks[index : index + 1] = [(a, cut), (cut, b)]
                    break
        chunks = [c for c in chunks if not any(d0 <= c[0] and c[1] <= d1 for d0, d1 in drop)]
        found: list[Word] = []
        for a, b in chunks:
            piece, language = _words_in(samples[int(a * SR) : int(b * SR)], a, align=align, language=language)
            found += [w for w in piece if not any(d0 <= w.start <= d1 for d0, d1 in drop)]
    return sorted(found, key=lambda w: w.start)


def load(path: Path) -> list[Word]:
    """Read a words file in any of the shapes the kit writes or meets.

    {"words": [{"w", "s", "e"}]}, segments or captions holding "words" lists, and plain
    [[word, start, end]] lists all work. Keys may be w/s/e, word/start/end or text/start/end.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        if isinstance(data.get("words"), list):
            data = data["words"]
        else:
            data = data.get("captions") or data.get("segments") or data.get("phrases") or []
    out: list[Word] = []

    def one(item):
        if isinstance(item, dict):
            text = item.get("w", item.get("word", item.get("text")))
            start = item.get("s", item.get("start"))
            end = item.get("e", item.get("end"))
        else:
            text, start, end = item[0], item[1], item[2]
        if text is not None and str(text).strip():
            out.append(Word(str(text).strip(), float(start), float(end)))

    for item in data:
        if isinstance(item, dict) and isinstance(item.get("words"), list):
            for word in item["words"]:
                one(word)
        else:
            one(item)
    return out


def write_json(audio: Path, out: Path, *, align: bool = True, lines: bool = False, **options) -> Path:
    """Transcribe `audio` and write {"text", "dur", "words": [{"w", "s", "e"}]} to `out`."""
    found = words(audio, align=align, **options)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "text": " ".join(w.text for w in found),
        "dur": _duration(audio),
        "words": [{"w": w.text, "s": w.start, "e": w.end} for w in found],
    }
    out.write_text(json.dumps(data, indent=0), encoding="utf-8")
    if lines:
        rows, current = [], []
        for word in found:
            current.append(word)
            if word.text.endswith((".", "?", "!")) or len(current) >= 14:
                rows.append(f"{current[0].start:7.2f}  " + " ".join(w.text for w in current))
                current = []
        if current:
            rows.append(f"{current[0].start:7.2f}  " + " ".join(w.text for w in current))
        out.with_name(out.name.replace(".words.json", "") + ".lines.txt").write_text("\n".join(rows) + "\n")
    return out


def _pairs(values: list[float]) -> list[tuple[float, float]]:
    return list(zip(values[0::2], values[1::2]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m kit.transcribe", description="Word timings for a take.")
    parser.add_argument("media", type=Path)
    parser.add_argument("--out", type=Path, required=True, help="the words file to write")
    parser.add_argument("--no-align", action="store_true", help="skip WhisperX; use the speech model's word times")
    parser.add_argument("--lines", action="store_true", help="also write <name>.lines.txt, one sentence per line")
    parser.add_argument("--language", help="the spoken language code, such as en (found by itself if left out)")
    parser.add_argument("--drop", type=float, nargs="+", default=[], metavar="S E", help="leave out words in S..E")
    parser.add_argument("--split", type=float, nargs="+", default=[], metavar="T", help="force a chunk boundary")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if len(args.drop) % 2:
        parser.error("--drop takes pairs of numbers: a start and an end")
    try:
        out = write_json(args.media, args.out, align=not args.no_align, lines=args.lines, language=args.language,
                         drop=_pairs(args.drop), split=args.split)
    except KitError as problem:
        print(problem)
        return 1
    count = len(json.loads(out.read_text())["words"])
    print(f"{count} words -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
