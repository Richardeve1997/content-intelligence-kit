"""The HyperFrames project scaffold and the gates that run before and after a render."""

import json
import re

import pytest
from uistory_helpers import KIT, ROOT, ffmpeg, load, run_kit

from kit import config

scaffold = load("scaffold")


# --- scaffold --------------------------------------------------------------------------------------------


def test_scaffold_builds_a_self_contained_project(tmp_path, example_me):
    hf = tmp_path / "hf"
    scaffold.scaffold(hf, 12.5)
    for name in ("index.html", "uistory.css", "uistory.js", "karaoke.css", "tokens.css", "vendor/gsap.min.js",
                 "package.json", "hyperframes.json"):
        assert (hf / name).is_file(), name
    html = (hf / "index.html").read_text()
    assert 'data-duration="12.5"' in html
    assert html.index("tokens.css") < html.index("uistory.css") < html.index("karaoke.css")
    assert "<!-- CAPTIONS:BEGIN -->" in html and "UIS.captions(tl)" in html


def test_scaffold_copies_the_fonts_into_the_project(tmp_path, example_me):
    """HyperFrames will not load a font from outside the project folder, so the fonts travel with it."""
    hf = tmp_path / "hf"
    scaffold.scaffold(hf, 5)
    css = (hf / "tokens.css").read_text()
    urls = re.findall(r'url\("([^"]+)"\)', css)
    assert urls, "the tokens name their fonts"
    for url in urls:
        assert not url.startswith(("/", "..")), url
        assert (hf / url).is_file(), url
    profile = config.load_profile()
    assert f"--accent: {profile.colours.accent};" in css
    assert f"--canvas: {profile.colours.canvas};" in css


def test_scaffold_never_overwrites_an_authored_composition(tmp_path, example_me):
    hf = tmp_path / "hf"
    scaffold.scaffold(hf, 5)
    (hf / "index.html").write_text("<!-- my scenes -->")
    scaffold.scaffold(hf, 5)
    assert (hf / "index.html").read_text() == "<!-- my scenes -->"


def test_scaffold_says_how_to_get_gsap_when_it_is_missing(tmp_path, example_me, monkeypatch):
    monkeypatch.setattr(scaffold, "GSAP", tmp_path / "missing" / "gsap.min.js")
    with pytest.raises(SystemExit) as caught:
        scaffold.scaffold(tmp_path / "hf", 5)
    assert "bin/fetch_gsap.sh" in str(caught.value)


def test_the_kit_css_uses_tokens_for_every_colour():
    for name in ("uistory.css", "karaoke.css"):
        css = (KIT / name).read_text()
        assert "--accent:" not in css, f"{name} must not define the accent; tokens.css does"
        assert "var(--accent)" in css or name == "karaoke.css"
    assert "var(--font-display)" in (KIT / "karaoke.css").read_text()


# --- gates on the composition ---------------------------------------------------------------------------


def test_check_scenes_catches_a_scene_nested_by_an_unclosed_div(tmp_path):
    page = tmp_path / "index.html"
    page.write_text('<body><div id="root"><div class="scene" id="s1"><div>open<div class="scene" id="s2"></div>'
                    "</div></div></body>")
    done = run_kit("check_scenes.py", page)
    assert done.returncode == 1 and "#s2" in done.stdout
    page.write_text('<body><div id="root"><div class="scene" id="s1"></div><div class="scene" id="s2"></div></div></body>')
    assert run_kit("check_scenes.py", page).returncode == 0


def test_check_cues_names_a_cue_that_was_never_spoken(tmp_path):
    words = tmp_path / "words.json"
    words.write_text(json.dumps({"words": [{"w": w, "s": i, "e": i + 0.5} for i, w in enumerate(
        "the basil and the basil again".split())]}))
    page = tmp_path / "index.html"
    page.write_text("<script>UIS.pop(tl, '#a', C('basil again')); UIS.pop(tl, '#b', C('parsley'));"
                    " UIS.pop(tl, '#c', C('basil'));</script>")
    done = run_kit("check_cues.py", page, words)
    assert done.returncode == 1
    assert "parsley" in done.stdout
    assert "WARN 'basil' occurs 2x" in done.stdout


def basil_words(tmp_path):
    words = tmp_path / "words.json"
    words.write_text(json.dumps({"words": [{"w": w, "s": i, "e": i + 0.5} for i, w in enumerate(
        "the basil and the basil again".split())]}))
    return words


def test_check_cues_fails_when_the_scenes_have_no_cue_it_can_read(tmp_path):
    """Cues passed through a helper are invisible to the check. Checking nothing is not a pass."""
    page = tmp_path / "index.html"
    page.write_text('<div id="root"><div class="scene" id="s1"></div></div>'
                    "<script>const at = (p, n) => UIS.snap(C(p, n)); UIS.show(tl, '#s1', at('parsley'));</script>")
    done = run_kit("check_cues.py", page, basil_words(tmp_path))
    assert done.returncode == 1
    assert "no cue" in done.stdout.lower()
    assert "C(p, n)" in done.stdout, "it names the call that hides the phrases"


def test_check_cues_warns_about_a_helper_next_to_cues_it_can_read(tmp_path):
    page = tmp_path / "index.html"
    page.write_text('<div id="root"><div class="scene" id="s1"></div></div>'
                    "<script>const at = (p) => C(p); UIS.show(tl, '#s1', C('basil again'));</script>")
    done = run_kit("check_cues.py", page, basil_words(tmp_path))
    assert done.returncode == 0
    assert "WARN" in done.stdout and "C(p)" in done.stdout


# --- gates on the finished video --------------------------------------------------------------------------


def _caption_video(path, blank_frame=None):
    """60 frames of a light caption bar at y 1570, optionally blanked for one frame."""
    vf = "drawbox=x=300:y=1540:w=480:h=60:color=white:t=fill"
    if blank_frame is not None:
        vf += f":enable=not(eq(n\\,{blank_frame}))"
    ffmpeg("-f", "lavfi", "-i", "color=c=black:s=1080x1920:r=60:d=1", "-vf", vf, "-c:v", "libx264", "-pix_fmt",
           "yuv420p", path)
    return path


def test_caption_blink_check_finds_a_one_frame_hole(tmp_path):
    assert run_kit("caption_blink_check.py", _caption_video(tmp_path / "ok.mp4")).returncode == 0
    done = run_kit("caption_blink_check.py", _caption_video(tmp_path / "blink.mp4", blank_frame=30))
    assert done.returncode == 1 and "FAIL" in done.stdout


def test_check_static_fails_a_canvas_that_holds_still(tmp_path, example_me):
    canvas = config.load_profile().colours.canvas.lstrip("#")
    still = tmp_path / "still.mp4"
    ffmpeg("-f", "lavfi", "-i", f"color=c=0x{canvas}:s=1080x1920:r=30:d=5",
           "-vf", "drawbox=x=200:y=400:w=600:h=300:color=gray:t=fill", "-c:v", "libx264", "-pix_fmt", "yuv420p",
           still)
    done = run_kit("check_static.py", still, "--max", "3")
    assert done.returncode == 1 and "STATIC" in done.stdout
    moving = tmp_path / "moving.mp4"
    ffmpeg("-f", "lavfi", "-i", f"color=c=0x{canvas}:s=1080x1920:r=30:d=5",
           "-f", "lavfi", "-i", "color=c=gray:s=300x300:r=30:d=5",
           "-filter_complex", "[0:v][1:v]overlay=x='200+mod(t*140\\,300)':y=400", "-c:v", "libx264",
           "-pix_fmt", "yuv420p", moving)
    assert run_kit("check_static.py", moving, "--max", "3").returncode == 0


def test_check_static_samples_the_frame_at_each_time_it_reports(tmp_path):
    """A still run is reported by the times of its samples, so each sample must be the frame at that time."""
    import subprocess

    import numpy as np

    static = load("check_static")
    ramp = tmp_path / "ramp.mp4"  # brighter every frame, so each moment has its own grey
    ffmpeg("-f", "lavfi", "-i", "color=c=black:s=270x480:r=60:d=2", "-vf", "geq=lum='16+100*T':cb=128:cr=128",
           "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p", ramp)
    samples = static.frames(ramp, 5)
    for k in range(5):
        exact = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{k / 5:.3f}", "-i", str(ramp), "-frames:v", "1",
                                "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
        grey = np.frombuffer(exact, np.uint8).astype(np.float32).mean()
        assert abs(float(samples[k].mean()) - grey) < 4, f"the sample at {k / 5}s shows a later frame"


def test_check_static_starts_its_samples_at_zero_when_the_picture_starts_late(tmp_path):
    """In a file whose sound starts before its picture, the first sample is the first frame, not a later one."""
    import subprocess

    import numpy as np

    static = load("check_static")
    clip = tmp_path / "late.mov"  # the picture starts 0.3 s after the sound, brighter every frame
    ffmpeg("-f", "lavfi", "-i", "color=c=black:s=270x480:r=60:d=2", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
           "-filter_complex", "[0:v]geq=lum='16+100*T':cb=128:cr=128,setpts=PTS+0.3/TB[v]", "-map", "[v]",
           "-map", "1:a", "-t", "2.3", "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", clip)
    first = subprocess.run(["ffmpeg", "-v", "error", "-i", str(clip), "-map", "0:v", "-frames:v", "1", "-f", "rawvideo",
                            "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    grey = np.frombuffer(first, np.uint8).astype(np.float32).mean()
    samples = static.frames(clip, 5)
    assert abs(float(samples[0].mean()) - grey) < 4, "the sample at 0s shows a later frame"


def test_ig_safe_check_writes_the_marked_frames(tmp_path):
    clip = tmp_path / "final.mp4"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=1080x1920:r=30:d=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", clip)
    done = run_kit("ig_safe_check.py", clip, "--at", "0.5", "1.5", "--mark", "--out", tmp_path / "safe")
    assert done.returncode == 0, done.stderr
    assert sorted(p.name for p in (tmp_path / "safe").iterdir()) == ["t0.5.png", "t1.5.png"]
    assert "CROPPED PER SIDE" in done.stdout


# --- what ships in the kit folder ---------------------------------------------------------------------------


RETIRED = {"build_track.py", "composite.sh", "face_frame.py", "sheets.py", "measure_ref.py"}
HOME_ROOT = "/" + "Users" + "/"  # built from pieces so this file never holds a home path
RAW_TAKE_NAME = re.compile(r"\b" + "IM" + r"G_\d")


def test_no_retired_or_machine_specific_file_ships():
    names = {p.name for p in KIT.iterdir()}
    assert not names & RETIRED
    assert "_v1" not in names
    for path in KIT.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            assert HOME_ROOT not in text and "/System/Volumes/Data" not in text, path
            assert not RAW_TAKE_NAME.search(text), path


def test_no_font_or_gsap_binary_is_vendored_in_the_skill():
    assert not [p for p in KIT.rglob("*") if p.suffix.lower() in {".ttf", ".otf", ".woff", ".woff2"}]
    assert not list(KIT.rglob("gsap*.js"))


def test_heavy_steps_take_a_slot():
    """Every step that encodes, renders, transcribes or mattes holds a heavy-job slot while it runs."""
    for name in ("cut_base.py", "composite_v2.py", "check_restarts.py"):
        assert "media.heavy_slot(" in (KIT / name).read_text(), name
    for module in ("matte.py", "transcribe.py", "finish.py"):
        assert "media.heavy_slot(" in (ROOT / "kit" / module).read_text(), module


def test_contrast_of_the_light_caption_plate(example_me):
    """The spoken word sits on a plate in the canvas colour with ink type: it must read."""
    from kit import brand

    profile = config.load_profile()
    assert brand.contrast(profile.colours.ink, profile.colours.canvas) >= 4.5
    css = (KIT / "karaoke.css").read_text()
    assert "var(--canvas)" in css and "var(--ink)" in css


def test_check_static_reads_the_canvas_colour_from_the_tokens():
    text = (KIT / "check_static.py").read_text()
    assert "load_profile" in text or "colour_tokens" in text


# --- the reading-load audit -----------------------------------------------------------------------------------


def _audit_page(tmp_path, scene, timeline, seconds=5.0):
    """A scaffolded project with one scene and its timeline lines, plus the trims and words the audit reads."""
    pytest.importorskip("playwright.sync_api")
    hf = tmp_path / "hf"
    scaffold.scaffold(hf, seconds)
    page = (hf / "index.html").read_text()
    page = page.replace("  <!-- CAPTIONS:BEGIN -->", f"  {scene}\n  <!-- CAPTIONS:BEGIN -->")
    page = page.replace("// ---- scene tweens (absolute composition time) ----",
                        "// ---- scene tweens (absolute composition time) ----\n" + timeline)
    (hf / "index.html").write_text(page)
    blocks, words = tmp_path / "blocks.json", tmp_path / "base.words.json"
    blocks.write_text(json.dumps({"A": [[0, "end"]]}))
    words.write_text(json.dumps({"dur": seconds, "words": []}))
    done = run_kit("audit_dom.py", hf / "index.html", blocks, words)
    if done.returncode != 0 and "No browser" in done.stdout:
        pytest.skip(done.stdout.strip())
    assert done.returncode == 0, done.stdout + done.stderr
    return [line for line in done.stdout.splitlines() if line.startswith("| s-")]


def test_the_reading_load_audit_counts_typed_text_by_its_words(tmp_path, example_me):
    """Typed text arrives one character at a time, so the audit once kept only its first letter and dropped
    the row as too short to count. The whole typed line is what the viewer reads."""
    scene = ('<div class="scene" id="s-typed"><div id="typed" style="position:absolute;left:120px;top:400px;'
             'font-size:48px"></div></div>')
    timeline = "UIS.show(tl, '#s-typed', 0, 5.0);\nUIS.type(tl, '#typed', 'fix the payments step', 1.0, 22);"
    rows = _audit_page(tmp_path, scene, timeline)
    typed = [row for row in rows if "fix the payments step" in row]
    assert len(typed) == 1, rows
    cells = [cell.strip() for cell in typed[0].strip("|").split("|")]
    assert cells[2] == "4", "four words, counted from the whole typed line"
    assert float(cells[3]) >= 3.0 and cells[5] == "yes", cells


def test_the_reading_load_audit_labels_a_count_by_the_number_it_lands_on(tmp_path, example_me):
    """A count's first value is a number nobody reads; the row names the one it lands on."""
    scene = ('<div class="scene" id="s-count"><div id="stars" style="position:absolute;left:120px;top:600px;'
             'font-size:60px"></div></div>')
    timeline = "UIS.show(tl, '#s-count', 0, 5.0);\nUIS.count(tl, '#stars', 80000, 2.0, {suffix: '+'});"
    rows = _audit_page(tmp_path, scene, timeline)
    counts = [row for row in rows if "(counter)" in row]
    assert len(counts) == 1 and "(counter) 80,000+" in counts[0], rows


def test_a_count_to_a_single_digit_is_a_count_not_typed_text(tmp_path, example_me):
    """Each value of a count to 8 is one character, the same shape as typed text. The helpers mark which is which."""
    scene = ('<div class="scene" id="s-small"><div id="steps" style="position:absolute;left:120px;top:600px;'
             'font-size:60px"></div></div>')
    timeline = "UIS.show(tl, '#s-small', 0, 5.0);\nUIS.count(tl, '#steps', 8, 1.0);"
    rows = _audit_page(tmp_path, scene, timeline)
    assert not [row for row in rows if "(typed)" in row], rows
    counts = [row for row in rows if "(counter)" in row]
    assert len(counts) == 1 and "(counter) 8 " in counts[0] + " ", rows
