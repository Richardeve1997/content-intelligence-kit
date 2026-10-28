"""kit.voice: the mini me voice-over, from the creator's own phone recording or from ElevenLabs.

Either way the result is work/<video>/vo.m4a at -13 LUFS and work/<video>/words.json with every word timed.
The speech model is replaced by a stand-in here (its own tests cover it), and so is the ElevenLabs API: no
test calls the network, and the fake key is a plain word.
"""

from __future__ import annotations

import json
import subprocess

import pytest

np = pytest.importorskip("numpy")

from mini_me_helpers import (  # noqa: E402
    decode, duration, loudness, needs_say, phone_recording, quiet_runs, speech_samples, write_wav,
)

from kit import config, transcribe, voice  # noqa: E402
from kit.config import KitError  # noqa: E402
from kit.transcribe import Word  # noqa: E402

FAKE_KEY = "not-a-real-key"
NO_KEY = "No ElevenLabs key, so record the voice-over on your phone and drop it in work/example-reel/take/"


@pytest.fixture
def heard(monkeypatch):
    """A stand-in speech model: one word every 0.4 s of the file it is given. It records each call."""
    calls = []

    def fake_words(audio, *, align=True, **_):
        length = duration(audio)
        calls.append({"audio": audio, "align": align})
        return [Word(f"word{i}", round(0.12 + 0.4 * i, 3), round(0.12 + 0.4 * i + 0.3, 3))
                for i in range(int((length - 0.4) / 0.4))]

    monkeypatch.setattr(transcribe, "words", fake_words)
    return calls


@pytest.fixture
def recording(kit_home):
    """Five seconds of speech with two one-second pauses, dropped in work/example-reel/take/ like a phone file."""
    pattern = [("gap", 0.4), ("talk", 1.2), ("gap", 1.0), ("talk", 1.4), ("gap", 1.0), ("talk", 1.0), ("gap", 0.3)]
    return phone_recording(config.work_dir("example-reel") / "take" / "take-01.m4a", pattern)


# --- from a recording -----------------------------------------------------------------------------------------------


def test_from_recording_writes_words(recording, heard):
    before = recording.read_bytes()
    vo = voice.from_recording(recording, "example-reel")
    folder = config.work_dir("example-reel")
    assert vo.audio == folder / "vo.m4a" and vo.audio.is_file()
    saved = json.loads((folder / "words.json").read_text())
    assert [w["w"] for w in saved["words"]] == [w.text for w in vo.words] and vo.words
    assert transcribe.load(folder / "words.json") == vo.words, "the kit's own reader reads it back"
    assert len(heard) == 1 and heard[0]["align"] is True, "one aligned transcription"
    # the stand-in times one word per 0.4 s of what it hears: the count proves it heard the finished voice-over
    assert len(vo.words) == int((duration(vo.audio) - 0.4) / 0.4) != int((duration(recording) - 0.4) / 0.4)
    assert recording.read_bytes() == before, "the recording is read in place and never changed"


def test_the_voice_over_is_at_the_house_loudness(recording, heard):
    vo = voice.from_recording(recording, "example-reel")
    integrated, peak = loudness(vo.audio)
    assert abs(integrated - voice.TARGET_LUFS) <= 1.0 and peak <= -1.5, (integrated, peak)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,sample_rate,channels",
                            "-of", "csv=p=0", str(vo.audio)], capture_output=True, text=True).stdout.strip()
    assert probe == "aac,48000,2"


def test_long_pauses_are_tightened_and_the_pace_lifted(recording, heard):
    vo = voice.from_recording(recording, "example-reel")
    gaps = quiet_runs(decode(vo.audio))
    assert gaps and max(gaps) <= 0.42, gaps
    # 3.6 s of talking at 1.15x is about 3.1 s; with short pauses and the pads it stays well under the 5.3 s source
    assert duration(vo.audio) < duration(recording) - 1.2


def test_tempo_and_tightening_can_be_turned_off(recording, heard):
    vo = voice.from_recording(recording, "example-reel", tempo=1.0, tighten=False)
    gaps = quiet_runs(decode(vo.audio))
    assert max(gaps) >= 0.9, "the pauses are kept"
    assert duration(vo.audio) >= duration(recording) - 0.1


def test_a_missing_or_silent_recording_is_a_plain_sentence(kit_home, heard, tmp_path):
    with pytest.raises(KitError, match="does not exist"):
        voice.from_recording(tmp_path / "nothing.m4a", "example-reel")
    silent = tmp_path / "silent.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1", str(silent)],
                   check=True)
    with pytest.raises(KitError, match="no sound"):
        voice.from_recording(silent, "example-reel")


def test_a_bad_video_name_is_refused_before_any_work(recording, heard):
    with pytest.raises(ValueError, match="cannot be used as a video name"):
        voice.from_recording(recording, "../escape")
    assert heard == []


def test_a_failed_transcription_keeps_the_previous_voice(recording, heard, monkeypatch):
    voice.from_recording(recording, "example-reel")
    folder = config.work_dir("example-reel")
    old_audio, old_words = (folder / "vo.m4a").read_bytes(), (folder / "words.json").read_text()

    def broken(*args, **kwargs):
        raise KitError("No speech model is installed.")

    monkeypatch.setattr(transcribe, "words", broken)
    with pytest.raises(KitError, match="No speech model"):
        voice.from_recording(recording, "example-reel")
    assert (folder / "vo.m4a").read_bytes() == old_audio and (folder / "words.json").read_text() == old_words
    leftovers = [p.name for p in folder.iterdir() if p.name not in {"take", "vo.m4a", "words.json", "vo.json"}]
    assert leftovers == [], "no temporary files are left behind"


# --- choosing the voice ---------------------------------------------------------------------------------------------


def set_voice_over(me, value):
    path = me / "profile.json"
    profile = json.loads(path.read_text())
    profile["voice_over"] = value
    path.write_text(json.dumps(profile))


def test_no_elevenlabs_uses_recording(kit_home, example_me, heard, capsys):
    set_voice_over(example_me, "elevenlabs")
    with pytest.raises(voice.NoElevenLabsKey) as caught:
        voice.from_elevenlabs("Fresh basil in March.", "example-reel")
    assert str(caught.value) == NO_KEY
    script = config.work_dir("example-reel") / "script.md"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("# Basil\n\n## Narration\n\nFresh basil in March.\n")
    assert voice.main(["auto", "example-reel"]) == voice.WAITING
    assert capsys.readouterr().out.strip() == NO_KEY
    assert (config.work_dir("example-reel") / "take").is_dir(), "the folder to drop the recording in is ready"
    assert heard == []


def test_a_dropped_recording_wins_over_elevenlabs(kit_home, example_me, recording, heard, monkeypatch):
    set_voice_over(example_me, "elevenlabs")
    monkeypatch.setenv("ELEVENLABS_API_KEY", FAKE_KEY)
    monkeypatch.setattr(voice, "_request", lambda *a, **k: pytest.fail("no ElevenLabs call when a recording is there"))
    assert voice.main(["auto", "example-reel"]) == 0
    assert (config.work_dir("example-reel") / "vo.m4a").is_file()


def test_a_voice_named_for_a_video_with_a_recording_says_the_recording_is_used(kit_home, example_me, recording, heard,
                                                                               monkeypatch, capsys):
    monkeypatch.setenv("ELEVENLABS_API_KEY", FAKE_KEY)
    set_voice_over(example_me, "elevenlabs")
    monkeypatch.setattr(voice, "_request", lambda *a, **k: pytest.fail("a recording needs no ElevenLabs call"))
    assert voice.main(["auto", "example-reel", "--voice", "Ash"]) == 0
    said = [line for line in capsys.readouterr().out.splitlines() if "--voice" in line]
    assert len(said) == 1 and "work/example-reel/take/" in said[0], said


def test_record_with_nothing_dropped_asks_for_the_recording(kit_home, capsys, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", FAKE_KEY)  # a key alone does not override the creator's choice
    assert voice.main(["auto", "example-reel"]) == voice.WAITING
    out = capsys.readouterr().out
    assert "record the voice-over on your phone" in out.lower() and "work/example-reel/take/" in out


def test_the_narration_is_read_from_its_section():
    text = "# Basil\n\n## Narration\n\nFresh basil in March.\nIt took ten minutes.\n\n## Notes\n\nNot this.\n"
    assert voice.narration(text) == "Fresh basil in March. It took ten minutes."
    dash = chr(0x2014)  # an em dash, written as a code point so this file holds none
    assert voice.narration(f"Just the words, with a dash {dash} here.") == "Just the words, with a dash, here."


# --- ElevenLabs ----------------------------------------------------------------------------------------------------


class FakeElevenLabs:
    """Answers the calls the kit makes: the voice list, the model list and the speech itself."""

    def __init__(self, models=("eleven_multilingual_v2", "eleven_v4"), status=200):
        self.calls, self.models, self.status = [], models, status

    def __call__(self, method, path, key, *, params=None, json_body=None, files=None, data=None, timeout=None):
        self.calls.append({"method": method, "path": path, "key": key, "params": params, "json": json_body,
                           "files": files, "data": data})
        if self.status != 200:
            return self.status, {"detail": {"status": "invalid_api_key", "message": "Invalid API key"}}, b""
        if path == "/v1/voices":
            return 200, {"voices": [
                {"voice_id": "zz-premade", "name": "Zed - Calm, Low", "category": "premade",
                 "labels": {"gender": "male", "age": "old", "accent": "british", "use_case": "narrative_story"},
                 "preview_url": "https://previews.example.com/zed.mp3"},
                {"voice_id": "aa-premade", "name": "Ash - Bright, Clear", "category": "premade",
                 "labels": {"gender": "female", "age": "middle_aged", "accent": "irish", "descriptive": "warm"},
                 "preview_url": "https://previews.example.com/ash.mp3"},
                {"voice_id": "mine", "name": "My clone", "category": "cloned"},
                {"voice_id": "studio-pro", "name": "Studio Pro", "category": "professional"},
            ]}, b""
        if path == "/v1/models":
            return 200, [{"model_id": m, "can_do_text_to_speech": True} for m in self.models], b""
        if path.startswith("/v1/text-to-speech/"):
            pcm = (np.clip(speech_samples([("gap", 0.2), ("talk", 2.0), ("gap", 0.2)]), -1, 1) * 32767).astype("<i2")
            # the API sends 44.1 kHz PCM; the fixture is 48 kHz, which only makes it play a little slower
            return 200, None, pcm.tobytes()
        if path == "/v1/voices/add":
            return 200, {"voice_id": "fake-voice-for-tests", "requires_verification": False}, b""
        raise AssertionError(f"unexpected call {method} {path}")


def test_from_elevenlabs_uses_a_stock_voice_when_there_is_no_clone(kit_home, heard, monkeypatch, capsys):
    monkeypatch.setenv("ELEVENLABS_API_KEY", FAKE_KEY)
    api = FakeElevenLabs()
    monkeypatch.setattr(voice, "_request", api)
    vo = voice.from_elevenlabs("Fresh basil in March, and it took ten minutes.", "example-reel")
    speech = [c for c in api.calls if c["path"].startswith("/v1/text-to-speech/")]
    assert len(speech) == 1
    assert speech[0]["path"] == "/v1/text-to-speech/aa-premade", "a stock voice, picked the same way every time"
    body = speech[0]["json"]
    assert body["model_id"] == "eleven_v4" and body["voice_settings"]["speed"] == voice.SYNTH_SPEED == 1.15
    assert all(c["key"] == FAKE_KEY for c in api.calls)
    assert vo.audio.is_file() and vo.words and (config.work_dir("example-reel") / "words.json").is_file()
    shown = capsys.readouterr()
    assert FAKE_KEY not in shown.out + shown.err


def test_a_named_voice_skips_the_stock_choice(kit_home, heard, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", FAKE_KEY)
    api = FakeElevenLabs()
    monkeypatch.setattr(voice, "_request", api)
    voice.from_elevenlabs("Fresh basil.", "example-reel", voice_id="mine")
    assert [c["path"] for c in api.calls if "text-to-speech" in c["path"]] == ["/v1/text-to-speech/mine"]
    assert not [c for c in api.calls if c["path"] == "/v1/voices"]


def test_a_model_without_speed_gets_the_same_pace_from_the_tempo(kit_home, heard, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", FAKE_KEY)
    api = FakeElevenLabs(models=("eleven_multilingual_v2", "eleven_v3"))
    monkeypatch.setattr(voice, "_request", api)
    voice.from_elevenlabs("Fresh basil.", "example-reel")
    body = [c for c in api.calls if "text-to-speech" in c["path"]][0]["json"]
    assert body["model_id"] == "eleven_v3" and "speed" not in body["voice_settings"]
    meta = json.loads((config.work_dir("example-reel") / "vo.json").read_text())
    assert meta["tempo"] == pytest.approx(voice.SYNTH_SPEED)


def test_a_rejected_key_is_a_plain_sentence_without_the_key(kit_home, heard, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", FAKE_KEY)
    monkeypatch.setattr(voice, "_request", FakeElevenLabs(status=401))
    with pytest.raises(KitError) as caught:
        voice.from_elevenlabs("Fresh basil.", "example-reel")
    message = str(caught.value)
    assert "ELEVENLABS_API_KEY" in message and ".env" in message and FAKE_KEY not in message
    assert not (config.work_dir("example-reel") / "vo.m4a").exists()


# --- a copy of the creator's voice ------------------------------------------------------------------------------------


def samples(tmp_path, count=2):
    found = []
    for i in range(count):
        found.append(write_wav(speech_samples([("talk", 20.0)], seed=10 + i), tmp_path / "samples" / f"s{i}.wav"))
    return found


def test_clone_needs_consent(kit_home, example_me, tmp_path, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", FAKE_KEY)
    monkeypatch.setattr(voice, "_request", lambda *a, **k: pytest.fail("nothing is sent without consent"))
    set_voice_over(example_me, "elevenlabs")
    with pytest.raises(KitError, match="consent"):
        voice.clone("Example Studio", samples(tmp_path))


def test_clone_with_consent_returns_the_voice_id(kit_home, example_me, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ELEVENLABS_API_KEY", FAKE_KEY)
    set_voice_over(example_me, "clone")
    api = FakeElevenLabs()
    monkeypatch.setattr(voice, "_request", api)
    voice_id = voice.clone("Example Studio", samples(tmp_path))
    assert voice_id == "fake-voice-for-tests"
    sent = [c for c in api.calls if c["path"] == "/v1/voices/add"]
    assert len(sent) == 1 and len(sent[0]["files"]) == 2 and sent[0]["data"]["name"] == "Example Studio"
    saved = json.loads((example_me / "voice" / "elevenlabs.json").read_text())
    assert saved["voice_id"] == voice_id
    assert voice.saved_voice() == voice_id, "later voice-overs use the copy"
    shown = capsys.readouterr()
    assert FAKE_KEY not in shown.out + shown.err


def test_clone_needs_enough_of_the_voice(kit_home, example_me, tmp_path, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", FAKE_KEY)
    set_voice_over(example_me, "clone")
    monkeypatch.setattr(voice, "_request", lambda *a, **k: pytest.fail("too little to send"))
    short = write_wav(speech_samples([("talk", 5.0)]), tmp_path / "short.wav")
    with pytest.raises(KitError, match="30 seconds"):
        voice.clone("Example Studio", [short])


# --- the scratch voice ------------------------------------------------------------------------------------------------


@needs_say
def test_a_scratch_voice_from_say(kit_home, heard):
    vo = voice.from_say("Fresh basil in March, and it took ten minutes.", "example-reel")
    assert vo.audio.is_file() and duration(vo.audio) > 1.0 and vo.words
    meta = json.loads((config.work_dir("example-reel") / "vo.json").read_text())
    assert meta["source"] == "say"


# --- the pace line, and the consent the saved voice depends on ---------------------------------------------------------


def test_a_paced_recording_says_so_once(recording, heard, capsys):
    voice.from_recording(recording, "example-reel")
    out = capsys.readouterr().out
    assert out.count("sped up to 1.15x") == 1 and "--keep-pace" in out
    voice.from_recording(recording, "example-reel", tempo=1.0, tighten=False)
    assert "sped up" not in capsys.readouterr().out, "nothing to say when the pace is kept"


def test_auto_can_keep_the_recordings_own_pace(recording, heard):
    assert voice.main(["auto", "example-reel", "--keep-pace"]) == 0
    folder = config.work_dir("example-reel")
    assert duration(folder / "vo.m4a") >= duration(recording) - 0.1
    meta = json.loads((folder / "vo.json").read_text())
    assert meta["pace"] == {"tempo": 1.0, "tightened": False}


def save_copy(me, voice_id="fake-voice-for-tests"):
    (me / "voice").mkdir(parents=True, exist_ok=True)
    (me / "voice" / "elevenlabs.json").write_text(json.dumps({"name": "Example Studio", "voice_id": voice_id}))


def test_the_saved_copy_is_used_only_while_the_profile_says_clone(kit_home, example_me, heard, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", FAKE_KEY)
    save_copy(example_me)
    script = config.work_dir("example-reel") / "script.md"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("# Basil\n\n## Narration\n\nFresh basil in March.\n")
    for setting, expected in (("elevenlabs", "/v1/text-to-speech/aa-premade"), ("clone", "/v1/text-to-speech/fake-voice-for-tests")):
        set_voice_over(example_me, setting)
        api = FakeElevenLabs()
        monkeypatch.setattr(voice, "_request", api)
        assert voice.main(["elevenlabs", "example-reel"]) == 0
        assert [c["path"] for c in api.calls if "text-to-speech" in c["path"]] == [expected], setting


def test_clone_with_no_copy_yet_stops_instead_of_using_a_stock_voice(kit_home, example_me, heard, monkeypatch, capsys):
    monkeypatch.setenv("ELEVENLABS_API_KEY", FAKE_KEY)
    set_voice_over(example_me, "clone")
    monkeypatch.setattr(voice, "_request", lambda *a, **k: pytest.fail("no stock voice in place of the copy"))
    script = config.work_dir("example-reel") / "script.md"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("# Basil\n\n## Narration\n\nFresh basil in March.\n")
    assert voice.main(["auto", "example-reel"]) == voice.WAITING
    out = capsys.readouterr().out
    assert "copy of your voice" in out and "python -m kit.voice clone" in out and "work/example-reel/take/" in out


def a_narration():
    script = config.work_dir("example-reel") / "script.md"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("# Basil\n\n## Narration\n\nFresh basil in March.\n")


def spoken_by(api):
    return [c["path"].rsplit("/", 1)[-1] for c in api.calls if "text-to-speech" in c["path"]]


def test_with_no_pick_the_voice_step_says_which_voice_it_used_and_how_to_choose(kit_home, example_me, heard, monkeypatch,
                                                                                  capsys):
    """With no voice picked, the first stock voice by name reads it, as before, and one sentence says so and how
    to choose another. A voice given by its id is named as such."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", FAKE_KEY)
    set_voice_over(example_me, "elevenlabs")
    a_narration()
    monkeypatch.setattr(voice, "_request", FakeElevenLabs())
    assert voice.main(["auto", "example-reel"]) == 0
    said = [line for line in capsys.readouterr().out.splitlines() if line.startswith("Read by")]
    assert len(said) == 1 and "Read by ElevenLabs in the stock voice Ash - Bright, Clear" in said[0]
    assert "python -m kit.voice voices" in said[0] and "python -m kit.voice pick" in said[0]
    monkeypatch.setattr(voice, "_request", FakeElevenLabs())
    assert voice.main(["elevenlabs", "example-reel", "--voice-id", "mine"]) == 0
    assert "Read by ElevenLabs in the voice you named." in capsys.readouterr().out


def test_voices_lists_the_stock_voices_with_labels_and_previews_and_nothing_else(kit_home, example_me, monkeypatch,
                                                                                capsys):
    """Each stock voice by name, with what ElevenLabs says about it and a link to hear it. No voice id, and never a
    copy of a voice or a professional one."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", FAKE_KEY)
    monkeypatch.setattr(voice, "_request", FakeElevenLabs())
    assert voice.main(["voices"]) == 0
    out = capsys.readouterr().out
    assert "Ash - Bright, Clear: female, middle aged, irish, warm" in out and "https://previews.example.com/ash.mp3" in out
    assert "Zed - Calm, Low: male, old, british, narrative story" in out and "https://previews.example.com/zed.mp3" in out
    assert out.index("Ash") < out.index("Zed")
    for hidden in ("aa-premade", "zz-premade", "My clone", "mine", "Studio Pro", "studio-pro", FAKE_KEY):
        assert hidden not in out, hidden


def test_a_picked_voice_is_saved_by_name_and_read_in_from_then_on(kit_home, example_me, heard, monkeypatch, capsys):
    monkeypatch.setenv("ELEVENLABS_API_KEY", FAKE_KEY)
    set_voice_over(example_me, "elevenlabs")
    monkeypatch.setattr(voice, "_request", FakeElevenLabs())
    assert voice.main(["pick", "zed"]) == 0, "the short name works, in any case"
    assert "Zed - Calm, Low" in capsys.readouterr().out
    saved = json.loads((example_me / "voice" / "elevenlabs.json").read_text())
    assert saved == {"stock": "Zed - Calm, Low"}, "saved by name, never by id"
    a_narration()
    api = FakeElevenLabs()
    monkeypatch.setattr(voice, "_request", api)
    assert voice.main(["auto", "example-reel"]) == 0
    assert spoken_by(api) == ["zz-premade"]
    said = [line for line in capsys.readouterr().out.splitlines() if line.startswith("Read by")]
    assert said == ["Read by ElevenLabs in the stock voice Zed - Calm, Low."]
    assert json.loads((config.work_dir("example-reel") / "vo.json").read_text())["voice"] == "Zed - Calm, Low"


def test_a_voice_named_for_one_video_wins_over_the_pick(kit_home, example_me, heard, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", FAKE_KEY)
    set_voice_over(example_me, "elevenlabs")
    (example_me / "voice").mkdir(parents=True, exist_ok=True)
    (example_me / "voice" / "elevenlabs.json").write_text(json.dumps({"stock": "Zed - Calm, Low"}))
    a_narration()
    for command in (["auto", "example-reel", "--voice", "Ash"], ["elevenlabs", "example-reel", "--voice", "ash - bright, clear"]):
        api = FakeElevenLabs()
        monkeypatch.setattr(voice, "_request", api)
        assert voice.main(command) == 0
        assert spoken_by(api) == ["aa-premade"], command
    assert json.loads((example_me / "voice" / "elevenlabs.json").read_text()) == {"stock": "Zed - Calm, Low"}


@pytest.mark.parametrize("name", ["Nobody", "My clone", "Studio Pro"])
def test_only_a_stock_voice_in_the_account_can_be_picked(kit_home, example_me, monkeypatch, capsys, name):
    monkeypatch.setenv("ELEVENLABS_API_KEY", FAKE_KEY)
    monkeypatch.setattr(voice, "_request", FakeElevenLabs())
    assert voice.main(["pick", name]) == 1
    out = capsys.readouterr().out
    assert "no stock voice called" in out and "python -m kit.voice voices" in out
    assert not (example_me / "voice" / "elevenlabs.json").exists()


def test_a_saved_pick_the_account_no_longer_has_stops_with_a_sentence(kit_home, example_me, heard, monkeypatch, capsys):
    monkeypatch.setenv("ELEVENLABS_API_KEY", FAKE_KEY)
    set_voice_over(example_me, "elevenlabs")
    (example_me / "voice").mkdir(parents=True, exist_ok=True)
    (example_me / "voice" / "elevenlabs.json").write_text(json.dumps({"stock": "Gone - Retired"}))
    a_narration()
    api = FakeElevenLabs()
    monkeypatch.setattr(voice, "_request", api)
    assert voice.main(["auto", "example-reel"]) == 1
    assert "no stock voice called" in capsys.readouterr().out and spoken_by(api) == []


def test_a_copy_of_the_voice_keeps_the_stock_pick(kit_home, example_me, tmp_path, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", FAKE_KEY)
    set_voice_over(example_me, "clone")
    (example_me / "voice").mkdir(parents=True, exist_ok=True)
    (example_me / "voice" / "elevenlabs.json").write_text(json.dumps({"stock": "Zed - Calm, Low"}))
    monkeypatch.setattr(voice, "_request", FakeElevenLabs())
    voice.clone("Example Studio", samples(tmp_path))
    saved = json.loads((example_me / "voice" / "elevenlabs.json").read_text())
    assert saved["stock"] == "Zed - Calm, Low" and saved["voice_id"] == "fake-voice-for-tests"
