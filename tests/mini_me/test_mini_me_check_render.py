"""check.py and render.py, on a real project: the headless page check and the HyperFrames render.

The page check opens its own browser, so it runs in a module of its own. The real render is a heavy job of about
half a minute and carries the heavy mark; the failure paths are checked without rendering.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from mini_me_helpers import PAGES, ROOT, SECONDS, build_project, load

from kit import config, media
from kit.config import KitError

check = load("check")
render = load("render")
GSAP = ROOT / "kit" / "vendor" / "gsap.min.js"
needs_gsap = pytest.mark.skipif(not GSAP.is_file(), reason="needs GSAP: bash bin/fetch_gsap.sh")


@pytest.fixture
def reel(kit_home, example_me):
    """work/example-reel, installed for the example creator with the stand-in character."""
    pytest.importorskip("playwright.sync_api")
    hf = build_project(kit_home, example_me)
    return config.work_dir("example-reel"), hf


def run_check(*args):
    try:
        return check.run("example-reel", list(args))
    except SystemExit as reason:  # no browser on this machine
        pytest.skip(str(reason))


@needs_gsap
def test_check_passes_and_writes_the_hits_from_the_page(reel, capsys):
    folder, _ = reel
    assert run_check() == 0
    out = capsys.readouterr().out
    assert "PASS" in out
    hits = json.loads((folder / "build" / "hits.json").read_text())["hits"]
    assert [h["t"] for h in hits if h["kind"] == "whoosh"] == [p["t"] for p in PAGES[1:]]
    assert sum(h["kind"] == "thud" for h in hits) == 2, "the stamp and the press on the closing card"
    for n in range(1, len(PAGES) + 1):
        assert f"\n{n:>3} " in out, "every page is listed by its number, from 1"


@needs_gsap
def test_check_fails_a_hold_card_page_with_a_card_when_no_card_was_measured(kit_home, example_me, capsys):
    """A pose override in scenes.js that pairs hold_card with a card, on a character with no measured card, is a
    failure the page check prints, not a note it passes."""
    pytest.importorskip("playwright.sync_api")
    pages = [{"t": 0.0, "text": "BASIL *DIES*", "pose": "point_up", "field": "dark", "prop": "card: Week three"},
             {"t": 1.6, "text": "THE PLAN", "pose": "point_left", "field": "light", "prop": "card: Water early"},
             {"t": 3.4, "text": "COMMENT GROW", "pose": "point_up", "field": "dark", "prop": "cta"}]
    spoken = "Basil dies in week three. Here is the plan. Comment GROW and I'll send you the checklist."
    hf = build_project(kit_home, example_me, spoken=spoken, build="big_head", pages=pages)
    (hf / "lib" / "scenes.js").write_text('Stage.page(2, { char: { pose: "hold_card" } });\n')
    assert run_check() == 1
    out = capsys.readouterr().out
    assert "FAIL page 2" in out and "hold_card" in out and "PASS" not in out


@needs_gsap
def test_a_cue_the_narration_never_says_fails_by_name(reel, capsys):
    folder, hf = reel
    (hf / "lib" / "scenes.js").write_text('Stage.page(2, { build: function (c) { Stage.stamp(c, "NO", { t: c.T("moonwalk") }); } });\n')
    assert run_check() == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and "moonwalk" in out
    assert "never say" in out


@needs_gsap
def test_check_saves_full_size_frames_when_asked(reel):
    folder, _ = reel
    assert run_check(0.5, 6.0) == 0
    for name in ("0.50.png", "6.00.png"):
        frame = folder / "build" / "check" / name
        assert frame.is_file()
        info = media.probe(frame)
        assert (info.width, info.height) == (1080, 1920)


def test_check_without_a_project_says_what_to_run(kit_home, capsys):
    config.work_dir("example-reel").mkdir(parents=True)
    assert check.main(["example-reel"]) == 1
    assert "install.py example-reel" in capsys.readouterr().out


# --- render.py --------------------------------------------------------------------------------------------------------


def test_render_without_a_project_says_what_to_run(kit_home):
    config.work_dir("example-reel").mkdir(parents=True)
    with pytest.raises(KitError, match="install.py example-reel"):
        render.render("example-reel")


def test_a_failed_render_leaves_no_picture_and_says_why(kit_home, monkeypatch):
    hf = config.work_dir("example-reel") / "build" / "hf"
    hf.mkdir(parents=True)
    (hf / "index.html").write_text("<div></div>")
    seen = {}

    def failing(cmd, **kwargs):
        seen["cmd"], seen["env"] = cmd, kwargs["env"]
        partial = cmd[cmd.index("--output") + 1]
        Path(partial).write_bytes(b"half a video")
        return subprocess.CompletedProcess(cmd, 1, "", "Error: the composition never registered its timeline\n")

    monkeypatch.setattr(render, "hyperframes", lambda: ROOT / "node_modules" / ".bin" / "hyperframes")
    monkeypatch.setattr(render.subprocess, "run", failing)
    with pytest.raises(KitError, match="never registered its timeline"):
        render.render("example-reel")
    build = config.work_dir("example-reel") / "build"
    assert not (build / "picture.mp4").exists() and not (build / "picture.part.mp4").exists()
    assert seen["cmd"][0].endswith("node_modules/.bin/hyperframes") and seen["cmd"][1] == "render", "the kit's own pinned copy"
    assert seen["cmd"][2:4] == ["--fps", "60"] and "--workers" in seen["cmd"]
    assert seen["env"]["HYPERFRAMES_NO_TELEMETRY"] == "1"


def test_render_says_when_hyperframes_is_not_installed(kit_home, monkeypatch):
    hf = config.work_dir("example-reel") / "build" / "hf"
    hf.mkdir(parents=True)
    (hf / "index.html").write_text("<div></div>")
    monkeypatch.setattr(render.config, "__file__", str(kit_home / "kit" / "config.py"))  # a kit with no node_modules
    with pytest.raises(KitError, match="npm ci"):
        render.render("example-reel")


@pytest.mark.heavy
@needs_gsap
@pytest.mark.skipif(shutil.which("npx") is None, reason="needs Node")
def test_a_real_render_matches_the_composition(reel):
    folder, _ = reel
    out = render.render("example-reel")
    assert out == folder / "build" / "picture.mp4"
    info = media.probe(out)
    assert (info.width, info.height, round(info.fps)) == (1080, 1920, 60)
    frames = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-count_frames", "-show_entries",
                             "stream=nb_read_frames", "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout
    assert int(frames) == int(SECONDS * 60), "exactly the composition's frames, none extra"
