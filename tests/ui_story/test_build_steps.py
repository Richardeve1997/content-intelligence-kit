"""The build steps between a clean base and the graphics layer: layout, captions, gap trims, times.

Every step reads spoken-word cues, never typed seconds, so these tests feed each script a small word
list and check what it writes.
"""

import json
import re
import shutil
import subprocess
import sys

import numpy as np
import pytest
from kit import telemetry
from uistory_helpers import ROOT, ffmpeg, load, run_kit

scaffold = load("scaffold")

LINE = "fresh basil in march took ten minutes and the secret is the pot not the seeds".split()


def word_file(tmp_path, words=LINE, start=0.5, step=0.4, gaps=None):
    """A base.words.json: one word every `step` seconds, with extra silence after the indexes in `gaps`."""
    out, t = [], start
    for i, w in enumerate(words):
        out.append({"w": w, "s": round(t, 3), "e": round(t + 0.3, 3)})
        t += step + (gaps or {}).get(i, 0.0)
    path = tmp_path / "base.words.json"
    path.write_text(json.dumps({"text": " ".join(words), "dur": round(t + 0.5, 3), "words": out}))
    return path, round(t + 0.5, 3)


def resolve(tmp_path, layout, words=None, hf=None):
    words, duration = words or word_file(tmp_path)
    (tmp_path / "layout.json").write_text(json.dumps(layout))
    args = ["--layout", tmp_path / "layout.json", "--words", words, "--duration", duration,
            "--out", tmp_path / "layout.resolved.json"]
    if hf:
        args += ["--hf", hf]
    return run_kit("layout.py", *args)


# --- layout ------------------------------------------------------------------------------------------


def test_layout_turns_cues_into_frame_snapped_windows(tmp_path):
    hf = tmp_path / "hf"
    done = resolve(tmp_path, {"lead": 0.12, "windows": [
        {"mode": "split", "t": 0},
        {"mode": "face", "at": "the secret", "zooms": [{"kind": "punch", "at": "secret", "z": 1.14}]},
        {"mode": "canvas", "at": "not the seeds"},
    ]}, hf=hf)
    assert done.returncode == 0, done.stderr
    resolved = json.loads((tmp_path / "layout.resolved.json").read_text())
    starts = [w["t0"] for w in resolved["windows"]]
    # "the secret" starts at word 8 -> 0.5 + 8 * 0.4 = 3.7, minus the 0.12 lead, snapped to 60 fps
    assert starts == [0, round(round((3.7 - 0.12) * 60) / 60, 4), round(round((0.5 + 13 * 0.4 - 0.12) * 60) / 60, 4)]
    assert [w["mode"] for w in resolved["windows"]] == ["split", "face", "canvas"]
    assert resolved["counts"] == {"punch": 1, "slow": 0}
    assert (hf / "lib" / "layout.js").read_text().startswith("window.UIS_LAYOUT=")


def test_a_zoom_outside_a_face_window_is_refused(tmp_path):
    done = resolve(tmp_path, {"windows": [
        {"mode": "split", "t": 0, "zooms": [{"kind": "punch", "at": "basil", "z": 1.1}]},
    ]})
    assert done.returncode != 0
    assert "FACE windows only" in done.stdout + done.stderr


def test_a_cue_that_is_not_spoken_is_named(tmp_path):
    done = resolve(tmp_path, {"windows": [{"mode": "split", "t": 0}, {"mode": "face", "at": "parsley"}]})
    assert done.returncode != 0
    assert "parsley" in done.stdout + done.stderr


def test_the_first_window_starts_at_zero(tmp_path):
    done = resolve(tmp_path, {"windows": [{"mode": "split", "at": "basil"}]})
    assert done.returncode != 0


LOOKUP = """
const fs = require('fs'), vm = require('vm');
globalThis.window = globalThis;
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
vm.runInThisContext(fs.readFileSync(process.argv[2], 'utf8'));
const out = {};
for (const key of JSON.parse(process.argv[3])) {
  try { out[String(key)] = UIS.win(key); } catch (e) { out[String(key)] = 'error: ' + e.message; }
}
out.second = window.UIS_LAYOUT[1];
console.log(JSON.stringify(out));
"""


def lookup(hf, keys):
    """UIS.win for each key, run in Node on the project's lib/layout.js and the kit's uistory.js."""
    done = subprocess.run(["node", "-e", LOOKUP, str(hf / "lib" / "layout.js"), str(ROOT / ".claude" / "skills" / "ui-story"
                           / "kit" / "uistory.js"), json.dumps(keys)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")
def test_a_window_is_found_by_its_name_or_a_time_after_another_is_inserted_before_it(tmp_path):
    """A timeline that reads windows by their place in lib/layout.js breaks when a window is added before them.
    UIS.win finds a window by its name in layout.json, by the phrase it starts at, or by a time inside it."""
    hf = tmp_path / "hf"
    windows = [{"mode": "split", "t": 0}, {"mode": "face", "at": "the secret", "name": "payoff"},
               {"mode": "canvas", "at": "not the seeds"}]
    assert resolve(tmp_path, {"windows": windows}, hf=hf).returncode == 0
    before = lookup(hf, ["payoff", "The Secret", "not the seeds", 4.2])
    assert before["payoff"] == {"mode": "face", "t0": before["payoff"]["t0"], "t1": before["payoff"]["t1"],
                                "name": "payoff", "at": "the secret"}, before
    assert before["The Secret"] == before["payoff"], "a named window answers to the phrase it starts at too"
    assert before["4.2"] == before["payoff"], "a time inside the window finds it too"

    windows.insert(1, {"mode": "canvas", "at": "took ten minutes"})  # a window added before the one looked up
    assert resolve(tmp_path, {"windows": windows}, hf=hf).returncode == 0
    after = lookup(hf, ["payoff", "The Secret", "not the seeds", 4.2, "parsley"])
    assert after["second"][0] == "canvas", "the old index now points at the inserted window"
    assert after["payoff"] == after["The Secret"] == after["4.2"] == before["payoff"], (before, after)
    assert after["not the seeds"] == before["not the seeds"] and after["not the seeds"]["mode"] == "canvas"
    assert after["parsley"].startswith("error: ") and "parsley" in after["parsley"], "a name no window has is an error"


def test_two_windows_with_one_name_are_refused(tmp_path):
    done = resolve(tmp_path, {"windows": [{"mode": "split", "t": 0, "name": "list"},
                                          {"mode": "canvas", "at": "the secret", "name": "list"}]})
    assert done.returncode != 0
    assert "list" in done.stdout + done.stderr and "name" in done.stdout + done.stderr


# --- karaoke captions --------------------------------------------------------------------------------------


@pytest.fixture
def hf_with_layout(tmp_path, example_me):
    hf = tmp_path / "hf"
    scaffold.scaffold(hf, 8.0)
    done = resolve(tmp_path, {"windows": [{"mode": "split", "t": 0}, {"mode": "canvas", "at": "and the secret"}]},
                   hf=hf)
    assert done.returncode == 0, done.stderr
    return hf


def test_captions_are_karaoke_groups_of_three_words_at_most(tmp_path, hf_with_layout):
    words, _ = word_file(tmp_path)
    done = run_kit("build_captions.py", "--words", words, "--hf", hf_with_layout,
                   "--layout", tmp_path / "layout.resolved.json", "--captions-dir", tmp_path / "captions")
    assert done.returncode == 0, done.stderr
    groups = json.loads((hf_with_layout / "lib" / "captions.json").read_text())
    assert groups and all(len(g["text"].split()) <= 3 and len(g["text"]) <= 18 for g in groups)
    html = (hf_with_layout / "index.html").read_text()
    block = html.split("<!-- CAPTIONS:BEGIN -->")[1].split("<!-- CAPTIONS:END -->")[0]
    assert block.count('class="clip cap k') == len(groups)
    capwords = (hf_with_layout / "lib" / "capwords.js").read_text()
    assert capwords.startswith("window.UIS_CAPWORDS=")
    assert len(json.loads(capwords.split("=", 1)[1].rstrip(";\n"))) == len(LINE)
    transcript = json.loads((tmp_path / "captions" / "transcript.json").read_text())
    assert " ".join(c["text"] for c in transcript["captions"]).split() == LINE


def test_caption_clips_carry_the_class_hyperframes_reads_as_captions(tmp_path, hf_with_layout):
    """HyperFrames' lint warns about any track with more than three timed clips, except clips it can tell are
    captions by their class. The caption track holds one clip a group, so every clip says it is a caption."""
    words, _ = word_file(tmp_path)
    run_kit("build_captions.py", "--words", words, "--hf", hf_with_layout, "--layout", tmp_path / "layout.resolved.json")
    classes = re.findall(r'<div id="cap\d+" class="([^"]+)"', (hf_with_layout / "index.html").read_text())
    assert len(classes) > 3
    assert all({"clip", "cap", "k", "caption"} <= set(c.split()) for c in classes), classes


def test_the_caption_track_gives_no_lint_warning(tmp_path, hf_with_layout):
    """Every build used to print a timeline_track_too_dense warning for the caption track, a warning about the
    kit's own design that a creator would learn to ignore."""
    hyperframes = ROOT / "node_modules" / ".bin" / "hyperframes"
    if not hyperframes.exists() or shutil.which("node") is None:
        pytest.skip("needs HyperFrames: npm ci")
    words, _ = word_file(tmp_path)
    run_kit("build_captions.py", "--words", words, "--hf", hf_with_layout, "--layout", tmp_path / "layout.resolved.json")
    done = subprocess.run(
        [str(hyperframes), "lint", str(hf_with_layout)], capture_output=True, text=True, timeout=120, env=telemetry.env()
    )
    said = done.stdout + done.stderr
    assert "Linting" in said, said
    assert "timeline_track_too_dense" not in said, said


def test_touching_caption_groups_swap_on_one_frame(tmp_path, hf_with_layout):
    """A clip shows while data-start <= t < data-start + data-duration (HyperFrames 0.8.68), so groups that
    touch must share an edge, and every edge sits a quarter frame before a frame."""
    words, _ = word_file(tmp_path, step=0.3)  # words 0.3 s long, no gaps: every group touches the next
    run_kit("build_captions.py", "--words", words, "--hf", hf_with_layout, "--layout", tmp_path / "layout.resolved.json")
    html = (hf_with_layout / "index.html").read_text()
    clips = [(float(s), float(d)) for s, d in re.findall(r'class="clip cap k[^"]*" data-start="([\d.]+)" data-duration="([\d.]+)"', html)]
    assert len(clips) > 3
    for (s1, d1), (s2, _) in zip(clips, clips[1:]):
        assert abs((s1 + d1) - s2) < 2e-4, "a frame with no caption, or two at once"
    for start, _ in clips[1:]:
        assert abs((start * 60) % 1 - 0.75) < 0.01, start


def test_captions_over_the_canvas_take_the_light_theme(tmp_path, hf_with_layout):
    words, _ = word_file(tmp_path)
    run_kit("build_captions.py", "--words", words, "--hf", hf_with_layout,
            "--layout", tmp_path / "layout.resolved.json")
    groups = json.loads((hf_with_layout / "lib" / "captions.json").read_text())
    cut = json.loads((tmp_path / "layout.resolved.json").read_text())["windows"][1]["t0"]
    for g in groups:
        assert g["theme"] == ("light" if g["start"] >= cut - 1e-6 else "dark"), g
        assert not (g["start"] < cut < g["end"] - 1e-6), f"a group crosses the layout cut: {g}"


def test_corrections_in_the_captions_folder_are_used_by_default(tmp_path, hf_with_layout):
    words, _ = word_file(tmp_path, words="we planted the basel seeds in march".split())
    captions = tmp_path / "captions"
    captions.mkdir()
    (captions / "corrections.json").write_text(json.dumps({"basel": "basil", "in march": "in March"}))
    done = run_kit("build_captions.py", "--words", words, "--hf", hf_with_layout,
                   "--layout", tmp_path / "layout.resolved.json", "--captions-dir", captions)
    assert done.returncode == 0, done.stderr
    block = (hf_with_layout / "index.html").read_text().split("<!-- CAPTIONS:BEGIN -->")[1]
    assert ">basil<" in block and ">basel<" not in block
    assert ">March<" in block
    transcript = json.loads((captions / "transcript.json").read_text())
    assert "basil" in " ".join(c["text"] for c in transcript["captions"])
    # the words the cues match stay as they were heard
    assert '"basel"' in (hf_with_layout / "lib" / "words.js").read_text()


def test_caption_size_is_measured_with_the_creators_display_font(tmp_path, example_me):
    build_captions = load("build_captions")
    from kit import brand, config

    assert build_captions.display_font_file() == brand.font_file(config.load_profile().fonts.display)
    wide = build_captions.width("WWWWWWWWWWWWWWWWWW", 84)
    narrow = build_captions.width("iiiiiiiiiiiiiiiiii", 84)
    assert wide > narrow > 0


# --- gap trims -----------------------------------------------------------------------------------------------


def speech_audio(path, words_path, ring_after=()):
    """A tone burst under each word and silence in the gaps, so the energy guard has something to hear.
    After each word index in `ring_after` the room rings on: noise decaying from -20 dB, above the guard's
    -42 dB floor for about 0.55 s."""
    data = json.loads(words_path.read_text())
    sr = 48000
    audio = np.zeros(int(data["dur"] * sr), np.float32)
    for i, w in enumerate(data["words"]):
        a, b = int(w["s"] * sr), int(w["e"] * sr)
        t = np.arange(b - a) / sr
        # a voiced sound: a 180 Hz pitch with even harmonics up to 1.8 kHz, like a vowel
        voice = sum(np.sin(2 * np.pi * 180 * k * t) for k in range(1, 11))
        audio[a:b] = 0.3 * voice / np.abs(voice).max()
        if i in ring_after:
            n = int(0.6 * sr)
            tail = np.random.default_rng(i).standard_normal(n) * 0.1 * np.exp(-np.arange(n) / sr / 0.217)
            audio[b : b + n] += tail.astype(np.float32)[: len(audio) - b]
    raw = path.with_suffix(".f32")
    raw.write_bytes(audio.tobytes())
    ffmpeg("-f", "f32le", "-ar", sr, "-ac", 1, "-i", raw, "-c:a", "pcm_s16le", path)
    raw.unlink()
    return path


def test_make_blocks_cuts_long_gaps_tight_and_the_trim_check_passes(tmp_path):
    words, duration = word_file(tmp_path, gaps={3: 0.9, 9: 0.6})
    audio = speech_audio(tmp_path / "clean.wav", words)
    (tmp_path / "layout.resolved.json").write_text(json.dumps(
        {"duration": duration, "windows": [{"mode": "split", "t0": 0, "t1": duration}]}))
    blocks = tmp_path / "blocks.json"
    done = run_kit("make_blocks.py", audio, words, tmp_path / "layout.resolved.json", blocks)
    assert done.returncode == 0, done.stderr
    kept = json.loads(blocks.read_text())["A"]
    assert len(kept) >= 3, "the two long gaps and the 0.1 s gaps between words become cuts"
    # what is left of each long gap is about 0.15 s
    data = json.loads(words.read_text())["words"]
    for i in (3, 9):
        gap_start, gap_end = data[i]["e"], data[i + 1]["s"]
        removed = sum(max(0.0, min(gap_end, s2) - max(gap_start, e1))
                      for (s1, e1), (s2, e2) in zip(kept, kept[1:]))
        assert (gap_end - gap_start) - removed == pytest.approx(0.15, abs=0.08)
    check = run_kit("check_trims.py", "--audio", audio, "--blocks", blocks)
    assert check.returncode == 0, check.stdout
    assert "ringing tail" not in done.stdout, "no gap was kept, so there is nothing to explain"


def test_make_blocks_says_once_why_it_kept_a_ringing_tail(tmp_path):
    """A breathy take rings on after a word, above the guard's floor, so the guard leaves that gap long. The
    report says why, once, instead of leaving the creator to wonder why a pause survived."""
    words, duration = word_file(tmp_path, gaps={3: 0.5, 6: 0.5, 9: 0.6})
    audio = speech_audio(tmp_path / "clean.wav", words, ring_after=(3, 6))
    (tmp_path / "layout.resolved.json").write_text(json.dumps(
        {"duration": duration, "windows": [{"mode": "split", "t0": 0, "t1": duration}]}))
    blocks = tmp_path / "blocks.json"
    done = run_kit("make_blocks.py", audio, words, tmp_path / "layout.resolved.json", blocks)
    assert done.returncode == 0, done.stderr
    why = [line for line in done.stdout.splitlines() if "ringing tail" in line]
    assert len(why) == 1, done.stdout
    assert why[0].strip().startswith("2 of 3 gaps over 0.22 s"), why[0]
    assert "-42 dB" in why[0] and "never cuts" in why[0], why[0]
    lines = done.stdout.splitlines()
    assert lines.index(why[0]) > next(i for i, line in enumerate(lines) if line.startswith("blocks ")), \
        "it is said where the trim is reported"


def test_check_trims_catches_a_cut_through_a_word(tmp_path):
    words, duration = word_file(tmp_path)
    audio = speech_audio(tmp_path / "clean.wav", words)
    first = json.loads(words.read_text())["words"][2]
    middle = round((first["s"] + first["e"]) / 2, 3)
    blocks = tmp_path / "blocks.json"
    blocks.write_text(json.dumps({"A": [[0, round(middle - 0.08, 3)], [round(middle + 0.08, 3), "end"]]}))
    check = run_kit("check_trims.py", "--audio", audio, "--blocks", blocks)
    assert check.returncode == 1
    assert "SPEECH-LIKE" in check.stdout


# --- the restart check ------------------------------------------------------------------------------------


def restart_check(tmp_path, monkeypatch, words, heard):
    """Run check_restarts.py on a cut heard as one piece, `heard`, with `words` as its words file."""
    restarts = load("check_restarts")
    monkeypatch.setattr(restarts.transcribe, "audio_samples", lambda path: np.zeros(16000 * 5, np.float32))
    monkeypatch.setattr(restarts.transcribe, "speech_chunks", lambda samples, **options: [(0.4, 4.6)])
    monkeypatch.setattr(restarts.transcribe, "chunk_text", lambda samples: heard)
    cut = tmp_path / "cut.wav"
    cut.write_bytes(b"")
    path, _ = word_file(tmp_path, words=words)
    monkeypatch.setattr(sys, "argv", ["check_restarts.py", str(cut), "--words", str(path)])
    assert restarts.main() == 0
    return path


def test_check_restarts_flags_a_word_said_twice_in_a_row(tmp_path, monkeypatch, capsys):
    """A restart after a pause too short to split on is heard as one clean sentence, but the words file has
    the restarted word twice in a row."""
    words = "booking booking forms work better when you know the steps".split()
    path = restart_check(tmp_path, monkeypatch, words, "Booking forms work better when you know the steps.")
    printed = capsys.readouterr().out
    second = json.loads(path.read_text())["words"][1]
    assert "REPEATED WORD 'booking'" in printed
    assert f"{second['s']:.2f}s" in printed, "it says where the clean restart starts"
    assert printed.strip().endswith("1 possible restart(s)")


def test_check_restarts_passes_words_that_never_repeat(tmp_path, monkeypatch, capsys):
    restart_check(tmp_path, monkeypatch, LINE, " ".join(LINE))
    assert capsys.readouterr().out.strip().endswith("0 possible restart(s)")


# --- authored times --------------------------------------------------------------------------------------------


def test_fill_times_replaces_cue_expressions_with_snapped_seconds(tmp_path):
    words, duration = word_file(tmp_path)
    (tmp_path / "layout.resolved.json").write_text(json.dumps(
        {"duration": duration, "windows": [{"mode": "split", "t0": 0, "t1": duration}]}))
    src = tmp_path / "index.src.html"
    src.write_text('<div data-start="{{ c("the secret") }}" data-end="{{ e("the pot") }}" data-w="{{ w(1.0) }}"></div>')
    out = tmp_path / "index.html"
    done = run_kit("fill_times.py", src, words, tmp_path / "layout.resolved.json", out)
    assert done.returncode == 0, done.stderr
    html = out.read_text()
    values = [float(v) for v in re.findall(r'"([\d.]+)"', html)]
    assert values[0] == pytest.approx(3.7 - 0.12, abs=1 / 60)
    assert values[1] == pytest.approx(0.5 + 12 * 0.4 + 0.3, abs=1 / 60)
    assert values[2] == 0.0, "the window holding 1.0 s starts at 0"
    assert all(abs(v * 60 - round(v * 60)) < 0.01 for v in values), "every time sits on a frame"


def fill_window_times(tmp_path, windows, expressions):
    """Resolve `windows` with layout.py, then fill one attribute per {{ expression }}; returns the values, or the
    finished run when it failed."""
    assert resolve(tmp_path, {"windows": windows}).returncode == 0
    src, out = tmp_path / "index.src.html", tmp_path / "index.html"
    src.write_text("<i " + " ".join(f'v{n}="{{{{ {x} }}}}"' for n, x in enumerate(expressions)) + "></i>")
    done = run_kit("fill_times.py", src, tmp_path / "base.words.json", tmp_path / "layout.resolved.json", out)
    if done.returncode != 0:
        return done
    return [float(v) for v in re.findall(r'"([\d.]+)"', out.read_text())]


def test_fill_times_finds_a_window_by_name_phrase_or_time_after_another_is_inserted_before_it(tmp_path):
    """{{ w(i) }} once read a window by its place in the list, so a window added before it moved every later
    time without a word. w() finds a window by its name, the phrase it starts at, or a time inside it."""
    windows = [{"mode": "split", "t": 0}, {"mode": "face", "at": "the secret", "name": "payoff"},
               {"mode": "canvas", "at": "not the seeds"}]
    asks = ['w("payoff")', 'w("The Secret")', 'w("not the seeds")', "w(4.2)"]
    before = fill_window_times(tmp_path, windows, asks)
    windows.insert(1, {"mode": "canvas", "at": "took ten minutes"})  # a window added before the ones looked up
    after = fill_window_times(tmp_path, windows, asks)
    assert after == before, (before, after)
    face_start = round(round((3.7 - 0.12) * 60) / 60, 4)
    assert before[0] == before[1] == before[3] == face_start
    assert before[2] == round(round((0.5 + 13 * 0.4 - 0.12) * 60) / 60, 4)


def test_fill_times_refuses_a_window_by_its_place_and_names_the_ways_that_last(tmp_path):
    windows = [{"mode": "split", "t": 0}, {"mode": "face", "at": "the secret"}]
    done = fill_window_times(tmp_path, windows, ["w(1)"])
    said = done.stdout + done.stderr
    assert done.returncode != 0 and "Traceback" not in said, said
    assert "w(1)" in said and "place" in said and "name" in said and "phrase" in said and "w(31.0)" in said, said
    done = fill_window_times(tmp_path, windows, ['w("parsley")'])
    said = done.stdout + done.stderr
    assert done.returncode != 0 and "Traceback" not in said and "parsley" in said, said



def test_fill_times_help_lists_the_window_lookups():
    said = run_kit("fill_times.py", "--help").stdout
    assert 'w("name")' in said and "w(31.0)" in said and "refused" in said, said


def test_seg_starts_finds_each_join(tmp_path):
    """Two takes of different brightness joined by an EDL: the jump cut is found near the EDL time."""
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    ffmpeg("-f", "lavfi", "-i", "color=c=gray:s=180x320:r=60:d=1.0", "-c:v", "libx264", "-pix_fmt", "yuv420p", a)
    ffmpeg("-f", "lavfi", "-i", "color=c=white:s=180x320:r=60:d=1.0", "-c:v", "libx264", "-pix_fmt", "yuv420p", b)
    base = tmp_path / "base.mp4"
    ffmpeg("-i", a, "-i", b, "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]", "-map", "[v]", "-c:v", "libx264",
           "-pix_fmt", "yuv420p", base)
    edl = tmp_path / "edl.json"
    edl.write_text(json.dumps({"keep": [[0, 1.0], [5.0, 6.0]]}))
    done = run_kit("seg_starts.py", "--base", base, "--edl", edl, "--out", tmp_path / "seg.json")
    assert done.returncode == 0, done.stderr
    starts = json.loads((tmp_path / "seg.json").read_text())["actual_starts"]
    assert starts[0] == 0.0 and starts[1] == pytest.approx(1.0, abs=2 / 60)


def test_measure_crowns_reads_the_crown_at_each_sample_time(tmp_path):
    """The crowns are kept or dropped by the time of each sample, so each sample must be the frame at that time."""
    measure = load("measure_crowns")
    matte = tmp_path / "matte.mp4"  # the head sinks 100 px a second, so every moment has its own crown row
    ffmpeg("-f", "lavfi", "-i", "color=c=black:s=1080x1920:r=60:d=2",
           "-vf", "geq=lum='if(gte(Y\\,200+100*T)\\,255\\,0)':cb=128:cr=128", "-c:v", "libx264", "-qp", "0",
           "-pix_fmt", "yuv420p", matte)
    found = measure.crowns(matte, 0.5)
    assert [t for t, _ in found] == [0.0, 0.5, 1.0, 1.5]
    for t, row in found:
        assert abs(row - (200 + 100 * t)) <= 2, f"the sample at {t}s has the crown of a later frame (row {row})"


def test_measure_crowns_starts_its_samples_at_zero_when_the_picture_starts_late(tmp_path):
    """In a file whose sound starts before its picture, the picture keeps its late start. The first samples are
    the first frame, standing in for the time before it, not a frame from a sample later."""
    measure = load("measure_crowns")
    matte = tmp_path / "late.mov"  # the picture starts 0.3 s after the sound; the head sinks 100 px a second
    ffmpeg("-f", "lavfi", "-i", "color=c=black:s=1080x1920:r=60:d=2", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
           "-filter_complex", "[0:v]geq=lum='if(gte(Y\\,200+100*T)\\,255\\,0)':cb=128:cr=128,setpts=PTS+0.3/TB[v]",
           "-map", "[v]", "-map", "1:a", "-t", "2.3", "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p",
           "-c:a", "pcm_s16le", matte)
    found = dict(measure.crowns(matte, 0.5))
    assert abs(found[0.0] - 200) <= 2, f"the sample at 0s is a later frame (row {found[0.0]})"
    assert abs(found[1.0] - 270) <= 2, "the frame shown at 1.0 s was drawn 0.7 s in"


def test_every_kit_script_answers_help():
    from uistory_helpers import KIT

    for script in sorted(KIT.glob("*.py")):
        if script.name in {"browser.py"}:
            continue
        done = subprocess.run([sys.executable, str(script), "--help"], capture_output=True, text=True, timeout=120)
        assert done.returncode == 0, f"{script.name}: {done.stderr[-400:]}"
        assert "usage" in done.stdout.lower() or "Usage" in done.stdout, script.name
