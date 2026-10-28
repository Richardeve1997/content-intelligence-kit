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
