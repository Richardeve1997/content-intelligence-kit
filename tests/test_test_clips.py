"""The test clips that setup makes: one short clip in the creator's brand for each style they chose.

The UI story clip script (.claude/skills/ui-story/kit/test_clip.py) is tested in pieces here, and as a
whole in the heavy test at the end. The runner in kit.onboarding (the `clips` command) is tested with
stand-in scripts. The mini me clip script arrives with the mini me style; until then the runner says so.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from kit import config, finish, media, onboarding, qa_captions
from kit.config import KitError
from kit.media import MediaInfo
from kit.transcribe import Word

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / ".claude" / "skills" / "ui-story" / "kit" / "test_clip.py"
EM_DASH, EN_DASH = chr(0x2014), chr(0x2013)  # written as code points so this file holds no dash


def load_script():
    spec = importlib.util.spec_from_file_location("ui_story_test_clip_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SCRIPT_MODULE = load_script()


@pytest.fixture
def clip():
    return SCRIPT_MODULE


def speech(text: str, start: float = 0.5, step: float = 0.4) -> list[Word]:
    """One word every `step` seconds, each 0.3 s long."""
    return [Word(w, round(start + i * step, 3), round(start + i * step + 0.3, 3)) for i, w in enumerate(text.split())]


# --- choosing the part of the take ----------------------------------------------------------------------------------------


def test_nothing_said_means_no_window(clip):
    assert clip.pick_window([], 20.0) is None


def test_the_window_ends_on_the_last_sentence_that_fits(clip):
    text = " ".join(f"word{n}{'.' if n % 8 == 0 else ''}" for n in range(1, 41))  # a full stop every eight words
    words = speech(text)
    start, end = clip.pick_window(words, 20.0)
    assert start == pytest.approx(0.25), "a moment before the first word"
    last = max((w for w in words if w.text.endswith(".") and w.end - start <= clip.SOURCE_MAX), key=lambda w: w.end)
    assert end == pytest.approx(last.end + clip.TAIL, abs=0.002)
    assert clip.SOURCE_MIN <= end - start <= clip.SOURCE_MAX + clip.TAIL


def test_with_no_full_stops_it_ends_on_the_last_word_that_fits(clip):
    words = speech(" ".join(f"w{n}" for n in range(40)))
    start, end = clip.pick_window(words, 30.0)
    fits = [w for w in words if w.end - start <= clip.SOURCE_MAX]
    assert end == pytest.approx(fits[-1].end + clip.TAIL, abs=0.002)


def test_short_speech_runs_on_to_the_shortest_clip(clip):
    start, end = clip.pick_window(speech("only a few words"), 20.0)
    assert end - start == pytest.approx(clip.SOURCE_MIN)


def test_a_take_shorter_than_the_shortest_clip_is_used_whole(clip):
    start, end = clip.pick_window(speech("a few words here"), 4.0)
    assert (start, end) == (0.25, 4.0)


def test_the_window_starts_at_the_first_word_not_at_zero(clip):
    start, _ = clip.pick_window(speech("hello there everyone", start=3.0), 20.0)
    assert start == pytest.approx(2.75)
    start, _ = clip.pick_window(speech("hello there everyone", start=0.1), 20.0)
    assert start == 0.0, "never before the start of the take"


def test_one_very_long_first_word_still_makes_a_window(clip):
    start, end = clip.pick_window([Word("looooong", 0.5, 14.0)], 20.0)
    assert 0 <= start < end <= 20.0


def test_the_finished_clip_lands_between_5_and_10_seconds_for_every_window_the_picker_can_make(clip):
    for words in (speech(" ".join(f"w{n}." for n in range(60))), speech("a b c d e f g h i j k l m n o p q r s t u v")):
        start, end = clip.pick_window(words, 60.0)
        assert clip.MIN_SECONDS <= (end - start) / clip.SPEED <= clip.MAX_SECONDS


def test_the_words_of_a_window_are_counted_from_its_start(clip):
    words = speech("one two three four five six")
    kept = clip.window_words(words, 1.3, 2.9)  # the words start at 0.5, 0.9, 1.3, 1.7, 2.1 and 2.5
    assert [w.text for w in kept] == ["three", "four", "five", "six"]
    assert kept[0].start == 0.0 and kept[-1].end == pytest.approx(1.5)
    assert all(0 <= w.start < w.end <= 1.6 for w in kept)
    assert clip.window_words(words, 1.3, 2.9) == kept, "the same words give the same answer"


# --- the take --------------------------------------------------------------------------------------------------------------


def test_the_take_is_the_video_in_the_take_folder(clip, tmp_path):
    (tmp_path / "take").mkdir()
    (tmp_path / "take" / ".hidden.mov").write_bytes(b"x")
    (tmp_path / "take" / "notes.txt").write_text("not a video")
    assert clip.find_take(tmp_path) == (None, None), "nothing usable there"
    raw = tmp_path / "raw.mov"
    raw.write_bytes(b"x")
    (tmp_path / "take" / "take-01.mov").symlink_to(raw)
    assert clip.find_take(tmp_path) == (tmp_path / "take" / "take-01.mov", None)


def test_there_is_no_take_when_there_is_no_folder(clip, tmp_path):
    assert clip.find_take(tmp_path / "nowhere") == (None, None)


def test_a_take_link_that_points_nowhere_is_named_and_the_sample_card_is_used(clip, tmp_path):
    (tmp_path / "take").mkdir()
    (tmp_path / "take" / "take-01.mov").symlink_to(tmp_path / "moved-away.mov")
    found, note = clip.find_take(tmp_path)
    assert found is None
    assert "take/take-01.mov points to a file that is not there any more" in note and "sample card" in note


def test_a_take_given_by_path_must_be_there(clip, tmp_path):
    with pytest.raises(KitError, match="is not there"):
        clip.find_take(tmp_path, tmp_path / "nope.mov")
    raw = tmp_path / "raw.mov"
    raw.write_bytes(b"x")
    assert clip.find_take(tmp_path, raw) == (raw, None)


# --- the scratch voice -----------------------------------------------------------------------------------------------------


def test_the_voice_is_slowed_down_or_sped_up_to_land_on_the_target(clip):
    assert clip.rate_for(175, 6.0, 7.5) == 140, "a line that ran short is read slower"
    assert clip.rate_for(175, 9.0, 7.5) == 210, "a line that ran long is read faster"
    assert clip.rate_for(175, 0.1, 7.5) == 110 and clip.rate_for(175, 90.0, 7.5) == 320, "kept to a sane range"


def test_the_sample_clip_is_about_seven_seconds_for_the_sample_line(clip):
    seconds = clip.SAMPLE_TARGET * clip.SPEED - clip.SAMPLE_TAIL
    assert clip.sample_clip_seconds(seconds) == pytest.approx(clip.SAMPLE_TARGET)
    assert clip.MIN_SECONDS < clip.SAMPLE_TARGET < clip.MAX_SECONDS


def test_the_sample_line_is_plain_words_with_no_dashes(clip):
    line = clip.SAMPLE_LINE
    assert EM_DASH not in line and EN_DASH not in line
    assert 20 <= len(line.split()) <= 40


def test_a_mac_without_the_say_command_is_told_what_to_do(clip, tmp_path, monkeypatch):
    monkeypatch.setattr(clip.shutil, "which", lambda name: None)
    with pytest.raises(KitError) as caught:
        clip.speak("hello", tmp_path / "v.wav", 175)
    assert "take" in str(caught.value) and "work/onboarding-test/take/" in str(caught.value)


# --- the card --------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["split", "canvas"])
def test_the_card_names_the_creator_and_draws_only_with_tokens(clip, example_me, mode):
    profile = config.load_profile()
    page = clip.card_html(profile, mode)
    assert "Example Studio" in page and "@examplestudio" in page
    for token in ("canvas", "ink", "accent", "field-dark", "field-light"):
        assert f"var(--{token})" in page, "one swatch for each colour of the look"
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", page), "no colour is written into the card"
    assert "http" not in page and "<script" not in page
    assert f"tc-{mode}" in page
    assert ("zone-split" in page) == (mode == "split") and ("zone-full" in page) == (mode == "canvas")
    assert EM_DASH not in page and EN_DASH not in page


def test_the_card_style_uses_tokens_and_no_colour_of_its_own(clip):
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", clip.CARD_STYLE)
    assert "var(--accent)" in clip.CARD_STYLE and "var(--font-display)" in clip.CARD_STYLE


def test_the_name_in_the_card_is_escaped(clip, example_me):
    profile = config.load_profile()
    page = clip.card_html(SimpleNamespace(**{**profile.__dict__, "name": "<b>Café & Co</b>"}), "split")
    assert "&lt;b&gt;Café &amp; Co&lt;/b&gt;" in page and "<b>Café" not in page


def test_the_logo_is_the_avatar_when_there_is_one_and_the_first_letter_when_there_is_not(clip, example_me):
    profile = config.load_profile()
    assert '<div class="avatar">E</div>' in clip.card_html(profile, "split")
    with_logo = SimpleNamespace(**{**profile.__dict__, "logo": "brand/logo/logo.PNG"})
    assert 'src="assets/logo.png"' in clip.card_html(with_logo, "split")


def test_a_longer_name_is_set_smaller_and_the_canvas_card_is_bigger(clip):
    sizes = [clip.name_size(name, "split") for name in ("Ana", "Example Studio", "Example Garden Studio", "A very long studio name indeed")]
    assert sizes == sorted(sizes, reverse=True) and len(set(sizes)) == len(sizes)
    assert clip.name_size("Example Studio", "canvas") > clip.name_size("Example Studio", "split")


def test_the_card_never_stands_still(clip):
    js = clip.card_js(7.25)
    assert "tl.to('#tc-bar', {scaleX: 1, duration: 7.250" in js, "the bar fills for the whole clip"
    assert "UIS.fill(tl, '#tc-swatches .sw'" in js and "UIS.chip(tl, '#tc-pill'" in js
    assert "fromTo" not in js, "an entrance that only shows in a snapshot is a known trap"


# --- the clip, step by step, with the heavy parts stood in for ----------------------------------------------------------


@pytest.fixture
def stand_ins(clip, tmp_path, monkeypatch, example_me):
    """Everything heavy is replaced by a stand-in that leaves a small file. Scaffold, layout and captions run for real."""
    monkeypatch.setattr(media, "_slots_dir", lambda: tmp_path / "slots")
    monkeypatch.delenv(media.SLOT_ENV, raising=False)
    monkeypatch.setattr(media, "MIN_FREE_GB", 0.0)
    state = SimpleNamespace(
        commands=[], words=speech("welcome to my channel today I will show you how I grow basil. It only takes five minutes."),
        problems=[], seconds=9.0, spoken=[], said=[], said_by_cut_base="",
    )
    real_run = clip.run

    def fake_run(doing, command, *, cwd=None):
        command = [str(part) for part in command]
        script = Path(command[1]).name
        state.commands.append((script, command[2:]))
        if script == "cut_base.py":
            Path(command[4]).write_bytes(b"base")
            return state.said_by_cut_base
        if script == "light_voice.py":
            Path(command[3]).write_bytes(b"wav")
            return ""
        if script == "composite_v2.py":
            Path(command[command.index("--out") + 1]).write_bytes(b"composite")
            return ""
        return real_run(doing, command, cwd=cwd)

    def sample_voice(line, out):
        state.spoken.append(line)
        Path(out).write_bytes(b"wav")
        return Path(out)

    def blank_base(profile, seconds, out):
        state.said.append(seconds)
        Path(out).write_bytes(b"blank")
        return Path(out)

    def render_graphics(hf, out):
        state.commands.append(("render", [str(hf)]))
        Path(out).write_bytes(b"webm")
        return Path(out)

    def finishing(src, out, **options):
        state.finish = options
        Path(out).write_bytes(b"final")
        return Path(out)

    monkeypatch.setattr(clip, "run", fake_run)
    monkeypatch.setattr(clip, "sample_voice", sample_voice)
    monkeypatch.setattr(clip, "blank_base", blank_base)
    monkeypatch.setattr(clip, "render_graphics", render_graphics)
    monkeypatch.setattr(clip.finish, "finish", finishing)
    monkeypatch.setattr(clip.qa_captions, "check", lambda *args, **kwargs: list(state.problems))
    monkeypatch.setattr(clip.transcribe, "words", lambda audio, **kw: state.words)
    monkeypatch.setattr(
        clip.media, "probe", lambda path: MediaInfo(1080, 1920, 60.0, state.seconds, None, True)
    )
    state.work = tmp_path / "work" / "onboarding-test"
    return state


def with_take(state, tmp_path):
    (state.work / "take").mkdir(parents=True)
    raw = tmp_path / "raw-take.mov"
    raw.write_bytes(b"the raw take")
    (state.work / "take" / "take-01.mov").symlink_to(raw)
    return raw


def scripts_run(state):
    return [name for name, _ in state.commands]


def test_with_a_take_the_clip_is_cut_from_it_and_set_in_the_picture(clip, stand_ins, tmp_path):
    raw = with_take(stand_ins, tmp_path)
    out = tmp_path / "out" / "ui-story.mp4"
    assert clip.clip(out, work=stand_ins.work, keep=True) == out
    assert out.read_bytes() == b"final"
    assert scripts_run(stand_ins) == [
        "cut_base.py", "light_voice.py", "scaffold.py", "layout.py", "build_captions.py", "render", "composite_v2.py",
    ]
    cut = json.loads((stand_ins.work / "build" / "edl.json").read_text())
    assert cut["keep"][0] == list(clip.pick_window(stand_ins.words, stand_ins.seconds))
    layout = json.loads((stand_ins.work / "build" / "layout.json").read_text())
    assert layout == {"windows": [{"mode": "split", "t": 0}]}
    args = dict(stand_ins.commands)["composite_v2.py"]
    assert "--no-head" in args, "no matte, so the head stays in the picture"
    assert args[args.index("--pip-top") + 1] == str(clip.PIP_TOP) and args[args.index("--crop-y") + 1] == str(clip.CROP_Y)
    assert stand_ins.finish["speed"] == clip.SPEED and stand_ins.finish["music"] is None
    assert raw.read_bytes() == b"the raw take", "the take is only read"
    assert (stand_ins.work / "take" / "take-01.mov").is_symlink()


def test_the_words_the_captions_use_are_the_ones_inside_the_window(clip, stand_ins, tmp_path):
    with_take(stand_ins, tmp_path)
    stand_ins.words = speech(" ".join(f"w{n}." if n % 6 == 0 else f"w{n}" for n in range(1, 60)))
    clip.clip(tmp_path / "o.mp4", work=stand_ins.work, keep=True)
    written = json.loads((stand_ins.work / "build" / "base.words.json").read_text())
    start, end = clip.pick_window(stand_ins.words, stand_ins.seconds)
    assert written["words"][0]["s"] >= 0 and written["words"][0]["s"] < 0.6, "counted from the window start"
    assert written["words"][-1]["e"] <= end - start + 0.05
    captions = json.loads((stand_ins.work / "build" / "captions" / "transcript.json").read_text())
    assert captions["captions"], "the speech gate reads these"


def test_without_a_take_the_card_sits_on_the_canvas_with_a_scratch_voice(clip, stand_ins, tmp_path):
    out = tmp_path / "o.mp4"
    clip.clip(out, work=stand_ins.work, keep=True)
    assert out.is_file()
    assert scripts_run(stand_ins) == ["scaffold.py", "layout.py", "build_captions.py", "render", "composite_v2.py"]
    assert stand_ins.spoken == [clip.SAMPLE_LINE]
    layout = json.loads((stand_ins.work / "build" / "layout.json").read_text())
    assert layout["windows"][0]["mode"] == "canvas"
    assert stand_ins.said and stand_ins.said[0] == pytest.approx(stand_ins.seconds + clip.SAMPLE_TAIL)


def test_a_line_of_their_own_is_what_the_scratch_voice_reads(clip, stand_ins, tmp_path):
    clip.clip(tmp_path / "o.mp4", line="Hello from the test.", work=stand_ins.work)
    assert stand_ins.spoken == ["Hello from the test."]


def test_the_composition_has_the_card_then_the_captions_then_the_timeline(clip, stand_ins, tmp_path):
    clip.clip(tmp_path / "o.mp4", work=stand_ins.work, keep=True)
    page = (stand_ins.work / "build" / "hf" / "index.html").read_text()
    assert page.index('id="testcard"') < page.index("<!-- CAPTIONS:BEGIN -->") < page.index('class="clip cap k')
    assert page.index("UIS.pop(tl, '#brandcard'") < page.index("UIS.captions(tl);")
    assert f"--zone-bottom: {clip.PIP_TOP - 50}px" in page
    assert "@@" not in page
    assert 'data-duration="' in page
    assert (stand_ins.work / "build" / "hf" / "tokens.css").is_file(), "the creator's tokens travel with the project"


def test_a_logo_travels_with_the_project_and_becomes_the_avatar(clip, stand_ins, tmp_path, example_me):
    from PIL import Image

    (example_me / "brand" / "logo").mkdir(parents=True)
    Image.new("RGB", (8, 8), "white").save(example_me / "brand" / "logo" / "logo.png")
    profile = json.loads((example_me / "profile.json").read_text(encoding="utf-8"))
    profile["logo"] = "brand/logo/logo.png"
    (example_me / "profile.json").write_text(json.dumps(profile), encoding="utf-8")
    clip.clip(tmp_path / "o.mp4", work=stand_ins.work, keep=True)
    hf = stand_ins.work / "build" / "hf"
    assert (hf / "assets" / "logo.png").is_file(), "HyperFrames only loads files inside the project folder"
    assert 'src="assets/logo.png"' in (hf / "index.html").read_text(encoding="utf-8")


def test_the_scratch_files_are_deleted_unless_asked_to_stay(clip, stand_ins, tmp_path):
    clip.clip(tmp_path / "a.mp4", work=stand_ins.work)
    assert not (stand_ins.work / "build").exists()
    clip.clip(tmp_path / "b.mp4", work=stand_ins.work, keep=True)
    assert (stand_ins.work / "build").is_dir()


def test_a_failed_speech_gate_saves_nothing_and_says_which_words_were_lost(clip, stand_ins, tmp_path):
    stand_ins.problems = ['"basil" (at 3.00 s in the edit, before the speed-up) is missing from the final audio.']
    out = tmp_path / "o.mp4"
    with pytest.raises(KitError) as caught:
        clip.clip(out, work=stand_ins.work)
    assert "basil" in str(caught.value) and "not saved" in str(caught.value)
    assert not out.exists()
    assert not (stand_ins.work / "build").exists(), "cleaned up even when it fails"


def test_a_take_with_nothing_said_falls_back_to_the_sample_card(clip, stand_ins, tmp_path, monkeypatch, capsys):
    with_take(stand_ins, tmp_path)
    calls = []

    def words(audio, **kw):
        calls.append(Path(audio).name)
        return [] if len(calls) == 1 else stand_ins.words  # the take is silent, the scratch voice is not

    monkeypatch.setattr(clip.transcribe, "words", words)
    clip.clip(tmp_path / "o.mp4", work=stand_ins.work)
    assert "Nothing can be heard in the take" in capsys.readouterr().out
    assert stand_ins.spoken == [clip.SAMPLE_LINE]
    assert calls == ["take-01.mov", "clean.wav"]


def test_an_hdr_sentence_from_the_base_cut_reaches_the_creator(clip, stand_ins, tmp_path, capsys):
    with_take(stand_ins, tmp_path)
    stand_ins.said_by_cut_base = "[cut_base] 1 segments\nThis take is HDR and this ffmpeg has no zscale filter, so its colours may look washed out.\n"
    clip.clip(tmp_path / "o.mp4", work=stand_ins.work)
    printed = capsys.readouterr().out
    assert "This take is HDR" in printed and "[cut_base]" not in printed


def test_a_take_link_that_points_nowhere_is_said_once_and_the_sample_card_is_made(clip, stand_ins, tmp_path, capsys):
    (stand_ins.work / "take").mkdir(parents=True)
    (stand_ins.work / "take" / "take-01.mov").symlink_to(tmp_path / "gone.mov")
    clip.clip(tmp_path / "o.mp4", work=stand_ins.work)
    printed = capsys.readouterr().out
    assert printed.count("points to a file that is not there any more") == 1
    assert stand_ins.spoken == [clip.SAMPLE_LINE]


def test_the_clip_needs_a_finished_setup(clip, tmp_path, monkeypatch):
    monkeypatch.setenv("KIT_ME", str(tmp_path / "no-me-yet"))
    assert clip.main(["--out", str(tmp_path / "o.mp4"), "--work", str(tmp_path / "w")]) == 1


def test_the_clip_script_prints_a_sentence_and_not_a_traceback(clip, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KIT_ME", str(tmp_path / "no-me-yet"))
    assert clip.main(["--out", str(tmp_path / "o.mp4")]) == 1
    printed = capsys.readouterr().out
    assert "set me up" in printed and "Traceback" not in printed


def test_a_step_that_fails_stops_the_clip_with_what_it_said(clip, tmp_path):
    with pytest.raises(KitError) as caught:
        clip.run("testing a failure", [sys.executable, "-c", "import sys; print('one'); print('two', file=sys.stderr); sys.exit(3)"])
    sentence = str(caught.value)
    assert "while testing a failure (exit 3)" in sentence and "one" in sentence and "two" in sentence


def test_the_script_names_no_machine_and_no_brand():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "/" + "Users" + "/" not in text and "System/Volumes" not in text
    assert EM_DASH not in text and EN_DASH not in text
    assert not re.search(r"#[0-9a-fA-F]{6}\b", text)


# --- the runner: kit.onboarding clips ---------------------------------------------------------------------------------------

FAKE_SCRIPT = """\
import argparse, pathlib, sys
ap = argparse.ArgumentParser()
ap.add_argument("--out", required=True)
ap.add_argument("--work")
a = ap.parse_args()
log = pathlib.Path(__file__).with_name("calls.log")
with log.open("a") as handle:
    handle.write(pathlib.Path(__file__).parent.parent.name + " " + a.out + " " + str(a.work) + "\\n")
MODE
"""
OK = 'pathlib.Path(a.out).write_bytes(b"clip"); print("made it")'
FAILS = 'print("There is no voice for the test clip on this machine."); sys.exit(1)'
CRASHES = 'raise RuntimeError("boom")'


@pytest.fixture
def kit_folder(tmp_path, monkeypatch, example_me):
    """A kit folder with a stand-in test clip script for each style, and plenty of disk."""
    root = tmp_path / "kit"
    for style in ("ui-story", "mini-me"):
        (root / ".claude" / "skills" / style / "kit").mkdir(parents=True)
    monkeypatch.setattr(config, "kit_root", lambda: root)
    monkeypatch.setattr(media, "free_gb", lambda path=None: 50.0)

    def script(style, mode=OK):
        path = root / ".claude" / "skills" / style / "kit" / "test_clip.py"
        path.write_text(FAKE_SCRIPT.replace("MODE", mode), encoding="utf-8")
        return path

    def calls():
        found = []
        for style in ("ui-story", "mini-me"):
            log = root / ".claude" / "skills" / style / "kit" / "calls.log"
            found += [line.split() for line in log.read_text().splitlines()] if log.exists() else []
        return found

    def built(character=True):
        if character:
            (example_me / "character").mkdir(exist_ok=True)
            (example_me / "character" / "manifest.json").write_text("{}")

    return SimpleNamespace(root=root, script=script, calls=calls, built=built, me=example_me)


def clips(capsys, *argv):
    code = onboarding.main(["clips", *argv])
    out = capsys.readouterr()
    assert "Traceback" not in out.out + out.err
    assert EM_DASH not in out.out + out.err and EN_DASH not in out.out + out.err
    return code, out.out, out.err


def test_clips_is_a_command_of_the_onboarding_module():
    assert "clips" in onboarding.COMMANDS and "accounts" in onboarding.COMMANDS


def test_each_style_the_creator_chose_gets_a_clip_one_after_the_other(kit_folder, capsys):
    kit_folder.script("ui-story")
    kit_folder.script("mini-me")
    kit_folder.built()
    code, out, err = clips(capsys)
    assert (code, err) == (0, "")
    folder = kit_folder.root / "work" / "onboarding-test"
    assert [c[0] for c in kit_folder.calls()] == ["ui-story", "mini-me"]
    assert (folder / "ui-story.mp4").is_file() and (folder / "mini-me.mp4").is_file()
    assert f"UI story test clip: {folder / 'ui-story.mp4'}" in out
    assert f"mini me test clip: {folder / 'mini-me.mp4'}" in out


def test_the_ui_story_script_is_told_where_the_take_and_the_scratch_files_are(kit_folder, capsys):
    kit_folder.script("ui-story")
    kit_folder.script("mini-me")
    kit_folder.built()
    clips(capsys)
    folder = kit_folder.root / "work" / "onboarding-test"
    ui = next(c for c in kit_folder.calls() if c[0] == "ui-story")
    mini = next(c for c in kit_folder.calls() if c[0] == "mini-me")
    assert ui[1:] == [str(folder / "ui-story.mp4"), str(folder)]
    assert mini[1:] == [str(folder / "mini-me.mp4"), "None"], "the mini me script has no --work"


def test_a_style_the_creator_did_not_choose_gets_no_clip(kit_folder, capsys):
    profile = json.loads((kit_folder.me / "profile.json").read_text())
    profile["styles"] = ["ui-story"]
    (kit_folder.me / "profile.json").write_text(json.dumps(profile))
    kit_folder.script("ui-story")
    kit_folder.script("mini-me")
    code, out, err = clips(capsys)
    assert code == 0 and [c[0] for c in kit_folder.calls()] == ["ui-story"]
    assert "mini me" not in out


def test_a_script_that_is_not_in_this_copy_of_the_kit_is_said_plainly_and_the_rest_goes_on(kit_folder, capsys):
    kit_folder.script("ui-story")
    kit_folder.built()
    code, out, err = clips(capsys)
    assert (code, err) == (0, "")
    assert "The mini me test clip is not in this copy of the kit yet" in out
    assert ".claude/skills/mini-me/kit/test_clip.py" in out and "An update adds it." in out
    assert [c[0] for c in kit_folder.calls()] == ["ui-story"]
    assert "UI story test clip:" in out


def test_without_a_finished_character_there_is_no_mini_me_clip_and_the_sentence_says_what_to_do(kit_folder, capsys):
    kit_folder.script("ui-story")
    kit_folder.script("mini-me")
    code, out, err = clips(capsys)
    assert (code, err) == (0, "")
    assert "mini me character is not built yet" in out and "character skill" in out
    assert [c[0] for c in kit_folder.calls()] == ["ui-story"], "the mini me script is not run on a character that is not there"


def test_a_script_that_fails_says_what_it_said_and_the_next_style_still_runs(kit_folder, capsys):
    kit_folder.script("ui-story", FAILS)
    kit_folder.script("mini-me")
    kit_folder.built()
    code, out, err = clips(capsys)
    assert code == 1
    assert "The UI story test clip did not finish." in out
    assert "There is no voice for the test clip on this machine." in out
    assert "mini me test clip:" in out, "one failure does not stop the others"


def test_a_script_that_crashes_is_a_sentence_and_never_a_traceback(kit_folder, capsys):
    kit_folder.script("ui-story", CRASHES)
    kit_folder.script("mini-me")
    kit_folder.built()
    code, out, err = clips(capsys)
    assert code == 1
    assert "The UI story test clip did not finish." in out and "RuntimeError: boom" in out
    assert "line " not in out.split("did not finish.")[1].split("mini me")[0], "no stack frames"


def test_clips_before_setup_is_finished_says_so(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KIT_ME", str(tmp_path / "no-me-yet"))
    code, out, err = clips(capsys)
    assert code == 1 and "set me up" in err


def test_a_disk_that_is_nearly_full_stops_before_any_clip_is_started(kit_folder, monkeypatch, capsys):
    kit_folder.script("ui-story")
    monkeypatch.setattr(media, "free_gb", lambda path=None: 2.0)
    code, out, err = clips(capsys)
    assert code == 1 and "Only 2.0 GB free" in err and "at least 5 GB" in err
    assert kit_folder.calls() == []


def test_the_clips_go_in_the_folder_next_has_to_skip(kit_folder):
    assert onboarding.CLIP_FOLDER == "onboarding-test"
    skill = (ROOT / ".claude" / "skills" / "next" / "SKILL.md").read_text(encoding="utf-8")
    assert f'SCRATCH = ("{onboarding.CLIP_FOLDER}",)' in skill


def test_the_skill_and_the_runner_name_the_same_scripts(capsys):
    skill = (ROOT / ".claude" / "skills" / "onboard" / "SKILL.md").read_text(encoding="utf-8")
    step = skill.split("### Step 9: Test clip")[1].split("### Step 10")[0]
    for style, (label, relative) in onboarding._CLIPS.items():
        assert f"`{relative}`" in step, f"the skill does not name the {style} script"
    assert (ROOT / onboarding._CLIPS["ui-story"][1]).is_file(), "the UI story script ships with the kit"


def test_every_command_is_listed_in_the_module_docstring():
    for command in onboarding.COMMANDS:
        assert f"python -m kit.onboarding {command}" in onboarding.__doc__, command


def test_the_runner_keeps_its_own_words_free_of_dashes(kit_folder, capsys):
    kit_folder.script("ui-story", FAILS)
    clips(capsys)  # the helper checks the printed text


# --- both clips, for real -----------------------------------------------------------------------------------------------------

SPOKEN = {
    "ui-story": lambda profile: SCRIPT_MODULE.SAMPLE_LINE,
    "mini-me": lambda profile: f"Hi, this is {profile.name}. Meet my mini me. It tells my stories while I stay off camera.",
}
SPEED = {"ui-story": finish.HOUSE["speed"], "mini-me": 1.0}


@pytest.mark.heavy
@pytest.mark.parametrize(
    "style",
    [
        "ui-story",
        pytest.param("mini-me", marks=pytest.mark.skip(reason="mini me test clip arrives with the mini me style")),
    ],
)
def test_both_clips_render(style, tmp_path, example_me, capsys):
    """For the example creator the clip exists, runs 5 to 10 seconds and says every word its captions show."""
    media.require_free()  # a heavy job needs 5 GB free
    folder = tmp_path / "onboarding-test"
    assert onboarding.make_clips(folder=folder, styles=[style]) == 0, capsys.readouterr().out
    made = folder / f"{style}.mp4"
    assert made.is_file(), f"no {style} clip at {made}"

    seconds = media.probe(made).duration
    assert 5.0 <= seconds <= 10.0, f"the {style} clip runs {seconds:.1f} s"
    profile = config.load_profile()
    spoken = SPOKEN[style](profile).split()
    words = [Word(text, round(i * 0.3, 3), round(i * 0.3 + 0.25, 3)) for i, text in enumerate(spoken)]
    assert qa_captions.check(made, words, speed=SPEED[style]) == [], f"the {style} clip lost a caption word"
    assert not (folder / "build").exists(), "the scratch files are cleaned up"
