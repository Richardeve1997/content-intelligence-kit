"""The speech-completeness gate: does the finished video still say every word the captions show?

Cuts, gap trims and the speed-up can clip the start of a word or drop one entirely. The captions were
made from the audio before those steps, so a caption word the final audio never says was lost in the
edit. `check(final, words)` listens to the final again and compares the two word lists.

Not every difference is a lost word. Two runs of a speech model disagree on spelling ("basil" heard as
"basel"), and a model often skips a short word like "a" or "it" that is clearly there. So the gate
fails only on a short run (four words or fewer) that was heard as nothing at all and holds a content
word: three letters or more, or any number. Everything else is ignored as model noise.

The speed-up makes speech harder to hear correctly, so a sped-up final (speed=1.15) is slowed back to
normal before listening. When something is flagged, a `fallback` file at normal speed (the composite
before the finish) is checked too, and a word heard there is not reported.

Command line:

    python -m kit.qa_captions <final.mp4> --words <words or transcript.json> [--speed 1.15] [--fallback F]

It prints PASS, or one line per lost word, and exits 1 when anything was lost.
"""

from __future__ import annotations

import argparse
import difflib
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from kit import transcribe
from kit.config import KitError
from kit.transcribe import Word

__all__ = ["check", "main"]

SIMILAR = 0.6  # a heard word this close to the caption word is a mishearing, not a loss
MAX_RUN = 4  # a longer run heard as nothing is two transcripts drifting apart, not a cut


def _norm(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower().replace("’", "'").replace("'", ""))


def _heard_words(path: Path, *, speed: float = 1.0) -> list[Word]:
    """What the speech model hears in `path`, slowed back to normal speed first when it was sped up."""
    path = Path(path)
    if not path.is_file():
        raise KitError(f"{path} does not exist, so it cannot be checked.")
    if abs(speed - 1.0) < 1e-3:
        return transcribe.words(path, align=False)
    with tempfile.TemporaryDirectory(prefix="kit-qa-") as folder:
        slowed = Path(folder) / "slowed.wav"
        subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(path), "-vn", "-filter:a", f"atempo={1.0 / speed:.6f}",
             "-ac", "1", "-ar", "16000", str(slowed)],
            check=True,
        )
        return transcribe.words(slowed, align=False)


def _losses(reference: list[Word], heard: list[Word]) -> list[tuple[int, list[str]]]:
    """(index of the first lost word in `reference`, the lost content words) for every real loss."""
    ref_tokens: list[str] = []
    owner: list[int] = []  # which reference word each normalised token came from
    for index, word in enumerate(reference):
        for token in _norm(word.text):
            ref_tokens.append(token)
            owner.append(index)
    got = [token for word in heard for token in _norm(word.text)]

    losses = []
    matcher = difflib.SequenceMatcher(a=ref_tokens, b=got, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag not in ("delete", "replace"):
            continue
        instead = got[j1:j2]
        unheard = [
            token for token in ref_tokens[i1:i2]
            if max((difflib.SequenceMatcher(a=token, b=other).ratio() for other in instead), default=0.0) < SIMILAR
        ]
        content = [token for token in unheard if len(token) >= 3 or any(ch.isdigit() for ch in token)]
        if instead or not content or len(unheard) > MAX_RUN:
            continue  # a mishearing, a dropped small word, or two transcripts drifting apart
        losses.append((owner[i1], content))
    return losses


def check(final: Path, words: list[Word], *, speed: float = 1.0, fallback: Path | None = None) -> list[str]:
    """Every caption word lost from the final audio, as plain sentences. An empty list means it passed.

    `words` are the caption words, timed on the edit before the speed-up. `speed` is the finish speed
    of `final`. `fallback` is an optional copy at normal speed, used to double-check anything flagged.
    """
    if not words:
        raise KitError("There are no caption words to check the final against.")
    losses = _losses(words, _heard_words(Path(final), speed=speed))
    if losses and fallback is not None:
        heard_there = " " + " ".join(token for w in _heard_words(Path(fallback), speed=1.0) for token in _norm(w.text)) + " "
        losses = [(index, lost) for index, lost in losses if f" {' '.join(lost)} " not in heard_there]
    problems = []
    for index, lost in losses:
        start = words[index].start
        said = " ".join(lost)
        problems.append(
            f'"{said}" (at {start:.2f} s in the edit, before the speed-up) is missing from the final audio. '
            "A cut or a gap trim there has clipped it."
        )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m kit.qa_captions", description=__doc__.splitlines()[0])
    parser.add_argument("final", type=Path)
    parser.add_argument("--words", type=Path, required=True, help="the caption words (a words or transcript file)")
    parser.add_argument("--speed", type=float, default=1.0, help="the finish speed of the final (1.15 for a Reel)")
    parser.add_argument("--fallback", type=Path, help="a copy at normal speed to double-check anything flagged")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        problems = check(args.final, transcribe.load(args.words), speed=args.speed, fallback=args.fallback)
    except KitError as problem:
        print(problem)
        return 1
    except (OSError, ValueError) as problem:
        print(f"The words file could not be read: {problem}")
        return 1
    if problems:
        print(f"FAIL: {len(problems)} lost word run(s)")
        for line in problems:
            print(f"- {line}")
        return 1
    print("PASS: the final audio says every caption word.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
