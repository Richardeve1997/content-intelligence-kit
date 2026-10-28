"""kit.transcribe (word timings) and kit.qa_captions (does the final still say every word?).

The fast tests swap the speech model for a stand-in. The tests marked `mac` make real speech with the
macOS `say` command and run the real models, so they prove the gate on audio.
"""

import json
import subprocess
import sys

import pytest
from uistory_helpers import ROOT, ffmpeg, needs_say

from kit import qa_captions, transcribe
from kit.transcribe import Word

SENTENCE = "Plant the basil seeds in a sunny window box before the frost"
WITHOUT_SEEDS = "Plant the basil in a sunny window box before the frost"


def words_of(text, start=0.4, step=0.35):
    return [Word(w, round(start + i * step, 3), round(start + i * step + 0.3, 3)) for i, w in enumerate(text.split())]


# --- spoken forms and chunks (no model) -------------------------------------------------------------------


@pytest.mark.parametrize(
    "token,spoken",
    [
        ("37", ["thirty", "seven"]),
        ("21st.dev", ["twenty", "first", "dot", "dev"]),
        ("2026", ["twenty", "twenty", "six"]),
        ("4.6", ["four", "point", "six"]),
        ("basil,", ["basil"]),
        ("don't", ["don't"]),
    ],
)
def test_digits_and_dotted_names_are_spoken_out_for_alignment(token, spoken):
    assert transcribe.spoken_form(token) == spoken


def test_chunks_split_at_real_pauses():
    import numpy as np

    sr = 16000
    burst = 0.3 * np.sin(2 * np.pi * 220 * np.arange(int(0.6 * sr)) / sr)
    gap = np.zeros(int(0.5 * sr))
    short_gap = np.zeros(int(0.1 * sr))
    audio = np.concatenate([gap, burst, short_gap, burst, gap, burst, gap]).astype(np.float32)
    chunks = transcribe.speech_chunks(audio, sr)
    assert len(chunks) == 2, chunks  # the 0.1 s gap does not split a chunk
    assert chunks[0][0] == pytest.approx(0.5 - 0.12, abs=0.03)


def test_words_writes_the_shape_the_kit_reads(tmp_path, monkeypatch):
    monkeypatch.setattr(transcribe, "words", lambda audio, *, align=True: words_of("Fresh basil in March"))
    monkeypatch.setattr(transcribe, "_duration", lambda path: 3.0)
    out = tmp_path / "base.words.json"
    transcribe.write_json(tmp_path / "take.mp4", out)
    data = json.loads(out.read_text())
    assert data["text"] == "Fresh basil in March"
    assert data["dur"] == 3.0
    assert data["words"][0] == {"w": "Fresh", "s": 0.4, "e": 0.7}


def test_load_reads_every_words_shape(tmp_path):
    shapes = [
        {"words": [{"w": "a", "s": 0, "e": 0.2}]},
        {"segments": [{"words": [{"word": "a", "start": 0, "end": 0.2}]}]},
        {"captions": [{"text": "a", "start": 0, "end": 0.2, "words": [{"w": "a", "s": 0, "e": 0.2}]}]},
        [["a", 0, 0.2]],
    ]
    for i, shape in enumerate(shapes):
        path = tmp_path / f"w{i}.json"
        path.write_text(json.dumps(shape))
        assert transcribe.load(path) == [Word("a", 0.0, 0.2)], shape


# --- the gate, with a stand-in for the speech model ---------------------------------------------------------


def heard(text):
    return lambda path, **kwargs: [Word(w, 0, 0) for w in text.split()]


def test_qa_passes_when_every_word_is_heard(monkeypatch, tmp_path):
    monkeypatch.setattr(qa_captions, "_heard_words", heard(SENTENCE.lower()))
    assert qa_captions.check(tmp_path / "final.mp4", words_of(SENTENCE)) == []


def test_qa_flags_a_dropped_content_word(monkeypatch, tmp_path):
    monkeypatch.setattr(qa_captions, "_heard_words", heard(WITHOUT_SEEDS))
    problems = qa_captions.check(tmp_path / "final.mp4", words_of(SENTENCE))
    assert len(problems) == 1
    assert "seeds" in problems[0] and "1.45" in problems[0]


def test_qa_lets_a_mishearing_and_a_lost_small_word_through(monkeypatch, tmp_path):
    # "basil" heard as "basel" is the model, not the edit; a lost "a" is too small to hear either way
    monkeypatch.setattr(qa_captions, "_heard_words", heard("Plant the basel seeds in sunny window box before the frost"))
    assert qa_captions.check(tmp_path / "final.mp4", words_of(SENTENCE)) == []


def test_qa_treats_a_lost_number_as_content(monkeypatch, tmp_path):
    monkeypatch.setattr(qa_captions, "_heard_words", heard("Water it every days"))
    problems = qa_captions.check(tmp_path / "final.mp4", words_of("Water it every 2 days"))
    assert len(problems) == 1 and '"2"' in problems[0]


def test_qa_rechecks_a_flag_against_the_slower_copy(monkeypatch, tmp_path):
    def by_file(path, **kwargs):
        return [Word(w, 0, 0) for w in (WITHOUT_SEEDS if path.name == "final.mp4" else SENTENCE).split()]

    monkeypatch.setattr(qa_captions, "_heard_words", by_file)
    assert qa_captions.check(tmp_path / "final.mp4", words_of(SENTENCE)) != []
    assert qa_captions.check(tmp_path / "final.mp4", words_of(SENTENCE), fallback=tmp_path / "composite.mov") == []


def test_qa_slows_a_sped_up_final_before_listening(monkeypatch, tmp_path):
    seen = {}

    def spy(path, **kwargs):
        seen.update(kwargs)
        return [Word(w, 0, 0) for w in SENTENCE.split()]

    monkeypatch.setattr(qa_captions, "_heard_words", spy)
    qa_captions.check(tmp_path / "final.mp4", words_of(SENTENCE), speed=1.15)
    assert seen["speed"] == 1.15


def test_qa_command_line_exits_one_on_a_problem(tmp_path):
    words = tmp_path / "words.json"
    words.write_text(json.dumps({"words": [{"w": w.text, "s": w.start, "e": w.end} for w in words_of(SENTENCE)]}))
    code = (
        "import sys\n"
        "from kit import qa_captions\n"
        "from kit.transcribe import Word\n"
        f"qa_captions._heard_words = lambda path, **k: [Word(w, 0, 0) for w in {WITHOUT_SEEDS!r}.split()]\n"
        f"sys.argv = ['qa', {str(tmp_path / 'final.mp4')!r}, '--words', {str(words)!r}]\n"
        "raise SystemExit(qa_captions.main())\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT)
    assert done.returncode == 1
    assert "seeds" in done.stdout


# --- the real thing (macOS say + the real speech models) ------------------------------------------------------


def say(text, out):
    aiff = out.with_suffix(".aiff")
    subprocess.run(["say", "-r", "170", "-o", str(aiff), text], check=True)
    ffmpeg("-i", aiff, "-ar", "48000", "-ac", "2", out)
    aiff.unlink()
    return out


@pytest.mark.mac
@needs_say
def test_qa_flags_dropped_word(tmp_path):
    """Fixture audio with one word removed fails the gate, and the full sentence passes it."""
    full = say(SENTENCE, tmp_path / "full.wav")
    reference = transcribe.words(full)
    assert [w.text.strip(".,").lower() for w in reference][:4] == ["plant", "the", "basil", "seeds"]

    cut = say(WITHOUT_SEEDS, tmp_path / "cut.wav")
    problems = qa_captions.check(cut, reference)
    assert len(problems) == 1 and "seeds" in problems[0], problems
    assert qa_captions.check(full, reference) == []


@pytest.mark.mac
@needs_say
def test_words_align_to_the_audio(tmp_path):
    clip = say("Water the basil every morning", tmp_path / "clip.wav")
    found = transcribe.words(clip, align=True)
    assert [w.text.strip(".,").lower() for w in found] == ["water", "the", "basil", "every", "morning"]
    assert all(b.start >= a.start for a, b in zip(found, found[1:]))
    assert all(w.end > w.start for w in found)
    quick = transcribe.words(clip, align=False)
    assert len(quick) == 5
