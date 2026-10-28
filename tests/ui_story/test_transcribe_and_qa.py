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


# --- word alignment and the licences of its models -----------------------------------------------------------


def _whisperx_default_aligners() -> dict:
    """WhisperX's own table of default alignment models, {language: model}, read from its source without importing
    it (an import loads torch)."""
    import ast
    import importlib.util
    from pathlib import Path

    spec = importlib.util.find_spec("whisperx")
    if spec is None or not spec.submodule_search_locations:
        pytest.skip("WhisperX is not installed (it comes with the video tools)")
    source = Path(next(iter(spec.submodule_search_locations))) / "alignment.py"
    table = {}
    for node in ast.parse(source.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") in (
            "DEFAULT_ALIGN_MODELS_TORCH", "DEFAULT_ALIGN_MODELS_HF",
        ):
            table.update(ast.literal_eval(node.value))
    assert table, "WhisperX's table of default alignment models was not found"
    return table


# The default alignment models whose licence is known to allow commercial use, each checked against its own source:
# torchaudio's notes for the English bundle (MIT), and each model's card on Hugging Face for the rest (Apache-2.0,
# except Turkish, CC BY 4.0, and Swedish, CC0). Galician's card gives Apache-2.0 in its text. Alignment runs for
# these languages and no others.
ALIGNED = {
    "en": "WAV2VEC2_ASR_BASE_960H",
    "ja": "jonatasgrosman/wav2vec2-large-xlsr-53-japanese",
    "zh": "jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn",
    "nl": "jonatasgrosman/wav2vec2-large-xlsr-53-dutch",
    "uk": "Yehor/wav2vec2-xls-r-300m-uk-with-small-lm",
    "pt": "jonatasgrosman/wav2vec2-large-xlsr-53-portuguese",
    "ar": "jonatasgrosman/wav2vec2-large-xlsr-53-arabic",
    "cs": "comodoro/wav2vec2-xls-r-300m-cs-250",
    "ru": "jonatasgrosman/wav2vec2-large-xlsr-53-russian",
    "pl": "jonatasgrosman/wav2vec2-large-xlsr-53-polish",
    "hu": "jonatasgrosman/wav2vec2-large-xlsr-53-hungarian",
    "fi": "jonatasgrosman/wav2vec2-large-xlsr-53-finnish",
    "fa": "jonatasgrosman/wav2vec2-large-xlsr-53-persian",
    "el": "jonatasgrosman/wav2vec2-large-xlsr-53-greek",
    "tr": "mpoyraz/wav2vec2-xls-r-300m-cv7-turkish",
    "ko": "kresnik/wav2vec2-large-xlsr-korean",
    "ur": "kingabzpro/wav2vec2-large-xls-r-300m-Urdu",
    "te": "anuragshas/wav2vec2-large-xlsr-53-telugu",
    "ca": "softcatala/wav2vec2-large-xlsr-catala",
    "ml": "gvs/wav2vec2-large-xlsr-malayalam",
    "no": "NbAiLab/nb-wav2vec2-1b-bokmaal-v2",
    "nn": "NbAiLab/nb-wav2vec2-1b-nynorsk",
    "sk": "comodoro/wav2vec2-xls-r-300m-sk-cv8",
    "sl": "anton-l/wav2vec2-large-xlsr-53-slovenian",
    "ro": "gigant/romanian-wav2vec2",
    "eu": "stefan-it/wav2vec2-large-xlsr-53-basque",
    "gl": "ifrz/wav2vec2-large-xlsr-galician",
    "ka": "xsway/wav2vec2-large-xlsr-georgian",
    "lv": "jimregan/wav2vec2-large-xlsr-latvian-cv",
    "tl": "Khalsuu/filipino-wav2vec2-l-xls-r-300m-official",
    "sv": "KBLab/wav2vec2-large-voxrex-swedish",
    "id": "cahya/wav2vec2-large-xlsr-indonesian",
}
# The rest of WhisperX's table, which keeps the speech model's own word times: CC BY-NC 4.0 (the VoxPopuli bundles
# and the Vietnamese model), the Danish Parliament's own terms, and three cards that give no licence at all.
NOT_ALIGNED = {
    "fr": "VOXPOPULI_ASR_BASE_10K_FR",
    "de": "VOXPOPULI_ASR_BASE_10K_DE",
    "es": "VOXPOPULI_ASR_BASE_10K_ES",
    "it": "VOXPOPULI_ASR_BASE_10K_IT",
    "vi": "nguyenvulebinh/wav2vec2-base-vi-vlsp2020",
    "da": "saattrupdan/wav2vec2-xls-r-300m-ftspeech",
    "he": "imvladikon/wav2vec2-xls-r-300m-hebrew",
    "hi": "theainerd/Wav2Vec2-large-xlsr-hindi",
    "hr": "classla/wav2vec2-xls-r-parlaspeech-hr",
}


def test_alignment_runs_only_where_the_default_models_licence_allows_commercial_use():
    table = _whisperx_default_aligners()
    assert set(transcribe.ALIGNED_LANGUAGES) == set(ALIGNED), "the kit aligns exactly the languages checked here"
    assert {code: table.get(code) for code in ALIGNED} == ALIGNED, "a changed default needs its licence checked again"
    assert {code: table.get(code) for code in NOT_ALIGNED} == NOT_ALIGNED
    new = sorted(set(table) - set(ALIGNED) - set(NOT_ALIGNED))
    assert not new, f"WhisperX has a new default alignment model for {new}: check its licence, then add it to one list"


def _speech_model_says(monkeypatch, language: str, text: str = "Le basilic aime le soleil"):
    """A stand-in speech model that hears `text` in `language`, with its own word times, and an aligner that fails
    the test if anything loads it. Returns the list of word_times flags the speech model was asked with."""
    import contextlib

    import numpy as np

    asked = []

    def asr(samples, *, word_times, language=None):
        asked.append(word_times)
        found = [(w, 0.5 + i * 0.4, 0.8 + i * 0.4) for i, w in enumerate(text.split())] if word_times else []
        return {"text": text, "language": heard_language, "words": found}

    heard_language = language

    def no_aligner(*args, **kwargs):
        raise AssertionError("an alignment model was loaded for a language whose model is not for commercial use")

    monkeypatch.setattr(transcribe, "audio_samples", lambda path: np.zeros(6 * transcribe.SR, np.float32))
    monkeypatch.setattr(transcribe, "speech_chunks", lambda samples: [(0.0, 2.5), (3.0, 5.5)])
    monkeypatch.setattr(transcribe.media, "heavy_slot", lambda **kwargs: contextlib.nullcontext())
    monkeypatch.setattr(transcribe, "_asr", asr)
    monkeypatch.setattr(transcribe, "_align", no_aligner)
    monkeypatch.setattr(transcribe, "_aligner", no_aligner)
    return asked


@pytest.mark.parametrize(
    "language,name",
    [("fr", "French"), ("de", "German"), ("es", "Spanish"), ("it", "Italian"), ("vi", "Vietnamese"), ("da", "Danish"),
     ("he", "Hebrew"), ("hi", "Hindi"), ("hr", "Croatian"), ("sw", "(sw)")],
)
def test_a_language_not_known_to_allow_commercial_alignment_keeps_the_speech_models_word_times(
    tmp_path, monkeypatch, capsys, language, name
):
    audio = tmp_path / "take.wav"
    audio.write_bytes(b"stand-in")
    _speech_model_says(monkeypatch, language)

    found = transcribe.words(audio)

    assert [w.text for w in found] == "Le basilic aime le soleil".split() * 2
    assert found[0] == Word("Le", 0.5, 0.8) and found[5] == Word("Le", 3.5, 3.8), "the speech model's own times"
    notes = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(notes) == 1, "one note for the whole take, not one for each stretch of speech"
    assert notes[0].startswith("note: ") and name in notes[0] and "commercial" in notes[0]


def test_a_language_on_the_list_is_aligned_with_no_note(tmp_path, monkeypatch, capsys):
    import contextlib

    import numpy as np

    audio = tmp_path / "take.wav"
    audio.write_bytes(b"stand-in")
    text = "Water the basil daily"
    monkeypatch.setattr(transcribe, "audio_samples", lambda path: np.zeros(4 * transcribe.SR, np.float32))
    monkeypatch.setattr(transcribe, "speech_chunks", lambda samples: [(1.0, 3.0)])
    monkeypatch.setattr(transcribe.media, "heavy_slot", lambda **kwargs: contextlib.nullcontext())
    monkeypatch.setattr(transcribe, "_asr", lambda samples, *, word_times, language=None: {
        "text": text, "language": "nl", "words": [(w, 9.0, 9.5) for w in text.split()] if word_times else []})
    aligned_for = []

    def align(samples, said, language):
        aligned_for.append(language)
        return [{"word": w, "start": 0.2 + i * 0.3, "end": 0.4 + i * 0.3} for i, w in enumerate(said.split())]

    monkeypatch.setattr(transcribe, "_align", align)
    found = transcribe.words(audio)
    assert aligned_for == ["nl"] and found[0] == Word("Water", 1.2, 1.4), "the aligner's times, not the speech model's"
    assert "note:" not in capsys.readouterr().out


def test_a_language_named_up_front_is_never_aligned_either(tmp_path, monkeypatch, capsys):
    audio = tmp_path / "take.wav"
    audio.write_bytes(b"stand-in")
    asked = _speech_model_says(monkeypatch, "fr")
    found = transcribe.words(audio, language="fr")
    assert len(found) == 10 and asked == [True, True], "with the language known, each stretch is heard once"
    assert capsys.readouterr().out.count("note: ") == 1


def test_an_alignment_that_is_not_available_says_so_in_a_note(monkeypatch, capsys):
    import types

    import numpy as np

    def fails(language):
        raise ValueError(f"No default align-model for language: {language}")

    monkeypatch.setitem(sys.modules, "whisperx", types.SimpleNamespace(align=None))
    monkeypatch.setattr(transcribe, "_aligner", fails)
    assert transcribe._align(np.zeros(transcribe.SR, np.float32), "hello", "xx") is None
    out = capsys.readouterr().out
    assert out.startswith("note: ") and out.count("\n") == 1, out


def test_the_licence_notes_say_alignment_runs_only_where_its_licence_allows_commercial_use():
    notes = (ROOT / "THIRD_PARTY.md").read_text(encoding="utf-8")
    speech = notes.split("## Speech to text", 1)[1].split("\n## ", 1)[0]
    assert "only where" in speech and "commercial use" in speech
    for licence in ("MIT", "Apache-2.0", "CC BY 4.0", "CC0"):
        assert licence in speech, licence
    for name in ("English", "Japanese", "Galician", "Turkish", "Swedish"):
        assert name in speech, f"{name} is aligned, so the notes name it"
    for name in ("French", "German", "Spanish", "Italian", "Vietnamese", "Danish", "Hebrew", "Hindi", "Croatian"):
        assert name in speech, f"{name} keeps the speech model's times, so the notes name it"


def _stand_in_models(monkeypatch, request, tmp_path, here: str):
    """A stand-in for the model hub and mlx-whisper. `here` is what this Mac holds of the model: "whole" (its config
    and weights), "half" (a download that stopped after the config) or "none". It records how the model was looked
    up and which folder mlx-whisper was handed, and clears the kit's cached answer again when the test ends, so no
    later test is handed this stand-in's folder."""
    import types

    seen = {"lookups": [], "model": None}
    on_this_mac, downloaded = tmp_path / "hub" / "on-this-mac", tmp_path / "hub" / "downloaded"
    held = {"whole": ("config.json", "weights.safetensors"), "half": ("config.json",), "none": ()}[here]
    for folder, files in ((on_this_mac, held), (downloaded, ("config.json", "weights.npz"))):
        folder.mkdir(parents=True)
        for name in files:
            (folder / name).write_text("{}")

    class NotHereYet(Exception):
        pass

    def snapshot_download(repo_id, local_files_only=False, **kwargs):
        seen["lookups"].append(local_files_only)
        if not local_files_only:
            return str(downloaded)
        if here == "none":
            raise NotHereYet(repo_id)
        return str(on_this_mac)

    def mlx_transcribe(samples, *, path_or_hf_repo, **kwargs):
        seen["model"] = path_or_hf_repo
        return {"text": "", "language": "en", "segments": []}

    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace(snapshot_download=snapshot_download))
    monkeypatch.setitem(sys.modules, "mlx_whisper", types.SimpleNamespace(transcribe=mlx_transcribe))
    monkeypatch.setattr(transcribe, "_use_mlx", lambda: True)
    monkeypatch.delenv("KIT_ASR_MODEL", raising=False)
    transcribe._mlx_model.cache_clear()
    request.addfinalizer(transcribe._mlx_model.cache_clear)
    return seen, str(on_this_mac), str(downloaded)


def _listen_once(times=1):
    import numpy as np

    for _ in range(times):
        transcribe._asr(np.zeros(transcribe.SR, np.float32), word_times=True)


def test_a_speech_model_already_here_loads_with_no_download_bar(monkeypatch, request, tmp_path, capsys):
    """Given a model's name, mlx-whisper looks it up online and draws a "Fetching 4 files" bar on every run. The
    kit hands it the folder of the copy already on this Mac, found without going online, and says nothing."""
    seen, on_this_mac, _ = _stand_in_models(monkeypatch, request, tmp_path, "whole")
    _listen_once()
    assert seen["model"] == on_this_mac and seen["lookups"] == [True]
    assert capsys.readouterr().out == ""


def test_a_speech_model_not_here_yet_is_downloaded_once_with_one_sentence(monkeypatch, request, tmp_path, capsys):
    seen, _, downloaded = _stand_in_models(monkeypatch, request, tmp_path, "none")
    _listen_once(times=2)
    assert seen["model"] == downloaded and seen["lookups"] == [True, False]
    out = capsys.readouterr().out
    assert out.count("\n") == 1 and "speech model" in out and "once" in out
    # a note, so on a first run it never takes the place of the line that says which step stopped
    assert out.startswith("note: ")


def test_a_download_that_stopped_halfway_is_finished_not_used(monkeypatch, request, tmp_path, capsys):
    """A first download cut off after the config leaves a folder the hub still finds on this Mac. Without its
    weights mlx-whisper cannot load it, so the kit downloads the rest instead of handing it over."""
    seen, _, downloaded = _stand_in_models(monkeypatch, request, tmp_path, "half")
    _listen_once()
    assert seen["model"] == downloaded and seen["lookups"] == [True, False]
    out = capsys.readouterr().out
    assert "speech model" in out and out.startswith("note: ")


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
