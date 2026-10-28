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


def test_a_chunk_heard_as_punctuation_alone_gives_no_word(tmp_path, monkeypatch):
    # A rustle before anyone speaks can come back from the speech model as a lone ".". It is not a word: kept,
    # it starts the setup test clip on a silent stretch, and a Reel would give it a caption of its own.
    import contextlib

    import numpy as np

    audio = tmp_path / "take.wav"
    audio.write_bytes(b"stand-in")
    monkeypatch.setattr(transcribe, "audio_samples", lambda path: np.zeros(10 * transcribe.SR, np.float32))
    monkeypatch.setattr(transcribe, "speech_chunks", lambda samples: [(1.0, 2.0), (5.0, 7.0)])
    monkeypatch.setattr(transcribe.media, "heavy_slot", lambda **kwargs: contextlib.nullcontext())
    heard = iter([
        ([Word(".", 1.3, 1.4)], "en"),
        ([Word("Fresh", 5.1, 5.5), Word("basil,", 5.6, 6.0), Word("...", 6.1, 6.2), Word("37", 6.3, 6.6)], "en"),
    ])
    monkeypatch.setattr(transcribe, "_words_in", lambda samples, offset, *, align, language: next(heard))
    assert [w.text for w in transcribe.words(audio)] == ["Fresh", "basil,", "37"]


def _stand_in_models(monkeypatch, cached: bool):
    """A stand-in for the model hub and mlx-whisper: records how the model was looked up and what was loaded."""
    import types

    seen = {"lookups": [], "model": None}

    class NotHereYet(Exception):
        pass

    def snapshot_download(repo_id, local_files_only=False, **kwargs):
        seen["lookups"].append(local_files_only)
        if local_files_only and not cached:
            raise NotHereYet(repo_id)
        return "/models/a-copy-on-this-mac"

    def mlx_transcribe(samples, *, path_or_hf_repo, **kwargs):
        seen["model"] = path_or_hf_repo
        return {"text": "", "language": "en", "segments": []}

    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace(snapshot_download=snapshot_download))
    monkeypatch.setitem(sys.modules, "mlx_whisper", types.SimpleNamespace(transcribe=mlx_transcribe))
    monkeypatch.setattr(transcribe, "_use_mlx", lambda: True)
    monkeypatch.delenv("KIT_ASR_MODEL", raising=False)
    getattr(transcribe, "_mlx_model", None) and transcribe._mlx_model.cache_clear()
    return seen


def test_a_speech_model_already_here_loads_with_no_download_bar(monkeypatch, capsys):
    """Given a model's name, mlx-whisper looks it up online and draws a "Fetching 4 files" bar on every run. The
    kit hands it the folder of the copy already on this Mac, found without going online, and says nothing."""
    import numpy as np

    seen = _stand_in_models(monkeypatch, cached=True)
    transcribe._asr(np.zeros(transcribe.SR, np.float32), word_times=True)
    assert seen["model"] == "/models/a-copy-on-this-mac" and seen["lookups"] == [True]
    assert capsys.readouterr().out == ""


def test_a_speech_model_not_here_yet_is_downloaded_once_with_one_sentence(monkeypatch, capsys):
    import numpy as np

    seen = _stand_in_models(monkeypatch, cached=False)
    transcribe._asr(np.zeros(transcribe.SR, np.float32), word_times=True)
    transcribe._asr(np.zeros(transcribe.SR, np.float32), word_times=True)
    assert seen["model"] == "/models/a-copy-on-this-mac" and seen["lookups"] == [True, False]
    out = capsys.readouterr().out
    assert out.count("\n") == 1 and "speech model" in out and "once" in out


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
