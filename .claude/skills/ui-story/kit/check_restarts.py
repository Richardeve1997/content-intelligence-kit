#!/usr/bin/env python3
"""check_restarts.py: find hidden false starts in a cut that whole-take transcription smooths over.

People often restart a sentence after a short pause without saying so. A speech model that hears the
aborted try and the clean read in one stretch writes the sentence once, and the aborted words vanish from
the transcript while staying in the audio. This splits the cut at short pauses (0.28 s), transcribes each
piece on its own with no context, and flags a piece whose opening words repeat at the start of the next.
The words file shows the same problem from the other side: a word that lasts oddly long (over 0.9 s), or
the same word twice in a row. A restart after a pause too short to split on stays inside one piece, and that
piece is heard as one clean sentence, but the aligned words still have the restarted word twice.

    python check_restarts.py <cut.mp4 | cut.wav> [--words base.words.json]

Flags: REPEATED START, PREFIX OF NEXT, LONG WORD and REPEATED WORD. A deliberate repeat ("For one, X. For
one, Y.", "very very") also flags, so judge by the text. Fix a real one by moving the keep segment in the
EDL to the clean restart, then cut again.
"""
import argparse
import json
import re
import sys
from pathlib import Path

from kit import media, transcribe


def norm(text):
    return re.findall(r"[a-z0-9']+", text.lower().replace("’", "'"))


def word_flags(words):
    """What the words file says about restarts: a word over 0.9 s, and the same word twice in a row."""
    flags = []
    for word in words:
        if word["e"] - word["s"] > 0.9:
            flags.append(f"LONG WORD {word['w']!r} at {word['s']:.2f}s lasts {word['e'] - word['s']:.2f}s: likely a restart")
    for first, second in zip(words, words[1:]):
        if norm(first["w"]) and norm(first["w"]) == norm(second["w"]):
            flags.append(f"REPEATED WORD {second['w'].strip(',.?!').lower()!r} at {first['s']:.2f}s, then again at "
                         f"{second['s']:.2f}s: likely a restart, and the clean one starts at {second['s']:.2f}s")
    return flags


def main():
    ap = argparse.ArgumentParser(description="Find hidden false starts in a cut.")
    ap.add_argument("audio", type=Path)
    ap.add_argument("--words", type=Path,
                    help="the words file of the same cut, to flag words over 0.9 s and a word said twice in a row")
    a = ap.parse_args()
    if not a.audio.is_file():
        print(f"{a.audio} does not exist.")
        return 1
    with media.heavy_slot(label=f"restarts {a.audio.name}"):
        samples = transcribe.audio_samples(a.audio)
        rows = []
        for start, end in transcribe.speech_chunks(samples, min_gap=0.28, pad=0.08):
            rows.append((start, end, transcribe.chunk_text(samples[int(start * transcribe.SR) : int(end * transcribe.SR)])))
    flagged = 0
    print(f"{a.audio.name}: {len(rows)} pieces")
    for i, (start, end, text) in enumerate(rows):
        flag = ""
        if i + 1 < len(rows):
            x, y = norm(text), norm(rows[i + 1][2])
            k = min(len(x), len(y), 3)
            if k >= 2 and x[:k] == y[:k]:
                flag = "   <<< REPEATED START"
            elif len(x) >= 2 and len(y) >= len(x) and y[: len(x)] == x:
                flag = "   <<< PREFIX OF NEXT"
        flagged += bool(flag)
        print(f"[{start:7.2f}-{end:7.2f}] {text}{flag}")
    if a.words and a.words.is_file():
        for line in word_flags(json.loads(a.words.read_text())["words"]):
            flagged += 1
            print(line)
    print(f"{flagged} possible restart(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
