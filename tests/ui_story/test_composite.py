"""composite_v2.py: the canvas, the rounded PIP with the head breaking out, and the graphics on top.

Its one-frame PNG mode is the quick layout check. The canvas, the grid and every accent come from the
creator's tokens, so a preview made for the example creator shows their colours, not any fixed ones.
"""

import json
import re

import numpy as np
import pytest
from PIL import Image
from uistory_helpers import ffmpeg, load, run_kit

from kit import brand, config

scaffold = load("scaffold")

CHIP = (300, 1320, 480, 76)  # x, y, w, h: a chip on the PIP, below the head band


def hex_rgb(colour):
    colour = colour.lstrip("#")
    return tuple(int(colour[i : i + 2], 16) for i in (0, 2, 4))


@pytest.fixture
def take(tmp_path):
    """A grey 1080x1920 base, and a matte whose 'person' is a tall white block in the middle."""
    base, matte = tmp_path / "base.mp4", tmp_path / "matte.mp4"
    ffmpeg("-f", "lavfi", "-i", "color=c=gray:s=1080x1920:r=60:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", base)
    ffmpeg("-f", "lavfi", "-i", "color=c=black:s=1080x1920:r=60:d=1",
           "-vf", "drawbox=x=380:y=200:w=320:h=1720:color=white:t=fill", "-c:v", "libx264", "-pix_fmt", "yuv420p",
           matte)
    layout = tmp_path / "layout.resolved.json"
    layout.write_text(json.dumps({"duration": 1.0, "windows": [{"mode": "split", "t0": 0, "t1": 1.0}]}))
    return base, matte, layout


def chip_layer(tmp_path) -> "object":
    """The graphics layer as HyperFrames would draw it: the kit's CSS, the creator's tokens, one chip."""
    playwright = pytest.importorskip("playwright.sync_api")
    hf = tmp_path / "hf"
    scaffold.scaffold(hf, 1.0)
    x, y, w, h = CHIP
    page_html = (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<link rel="stylesheet" href="tokens.css"><link rel="stylesheet" href="uistory.css">'
        "<style>html,body{margin:0;background:transparent}</style></head><body>"
        '<div id="root" style="width:1080px;height:1920px">'
        f'<div class="chip" style="left:{x}px;top:{y}px;width:{w}px;height:{h}px">FREE</div>'
        "</div></body></html>"
    )
    (hf / "chip.html").write_text(page_html)
    out = tmp_path / "gfx.png"
    browser_module = load("browser")
    try:
        with playwright.sync_playwright() as p:
            browser = browser_module.launch(p)
            page = browser.new_page(viewport={"width": 1080, "height": 1920})
            page.goto((hf / "chip.html").as_uri())
            page.wait_for_timeout(200)
            page.screenshot(path=str(out), omit_background=True)
            browser.close()
    except browser_module.NoBrowser as reason:
        pytest.skip(str(reason))
    return out


def composite(take, out, gfx=None):
    base, matte, layout = take
    args = ["--base", base, "--matte", matte, "--layout", layout, "--pip-top", 1000, "--zoom", 1.0,
            "--crop-y", 300, "--overhang", 320, "--from", 0.5, "--out", out]
    if gfx:
        args += ["--gfx", gfx]
    done = run_kit("composite_v2.py", *args)
    assert done.returncode == 0, done.stdout + done.stderr
    return np.asarray(Image.open(out).convert("RGB")).astype(int)


def test_preview_frame_uses_accent(example_me, take, tmp_path):
    accent = hex_rgb(config.load_profile().colours.accent)
    frame = composite(take, tmp_path / "frame.png", gfx=chip_layer(tmp_path))
    x, y, w, h = CHIP
    sample = frame[y + h // 2, x + 8]  # inside the chip's padding, left of its label
    assert all(abs(int(a) - b) <= 6 for a, b in zip(sample, accent)), (tuple(sample), accent)


def test_preview_canvas_and_grid_come_from_the_tokens(example_me, take, tmp_path):
    tokens = brand.colour_tokens(config.load_profile())
    canvas, accent = hex_rgb(tokens["canvas"]), hex_rgb(tokens["accent"])
    frame = composite(take, tmp_path / "frame.png")
    assert all(abs(int(a) - b) <= 6 for a, b in zip(frame[45, 45], canvas)), (tuple(frame[45, 45]), canvas)
    # a grid line is the accent at 9% over the canvas
    expected = [round(0.09 * a + 0.91 * c) for a, c in zip(accent, canvas)]
    assert all(abs(int(v) - e) <= 6 for v, e in zip(frame[45, 90], expected)), (tuple(frame[45, 90]), expected)


def test_the_pip_shows_the_take_and_the_head_breaks_out(example_me, take, tmp_path):
    frame = composite(take, tmp_path / "frame.png")
    grey = frame[1500, 200]  # inside the rounded PIP: the base shows
    assert abs(int(grey[0]) - int(grey[2])) <= 6 and 100 < grey[0] < 160
    head = frame[800, 540]  # above the PIP edge, inside the matte: the head breaks out
    assert 100 < head[0] < 160
    beside = frame[800, 150]  # above the PIP edge, outside the matte: canvas
    canvas = hex_rgb(config.load_profile().colours.canvas)
    assert all(abs(int(a) - b) <= 8 for a, b in zip(beside, canvas))


def test_composite_prints_where_a_crown_lands(example_me, take, tmp_path):
    base, matte, layout = take
    done = run_kit("composite_v2.py", "--base", base, "--matte", matte, "--layout", layout, "--pip-top", 1000,
                   "--crop-y", 300, "--overhang", 320, "--crown-src", 420, "--from", 0.5,
                   "--out", tmp_path / "x.png")
    assert done.returncode == 0, done.stderr
    found = re.search(r"source crown y420 -> output y(\d+)", done.stdout)
    assert found, done.stdout
    # output y = (pip_top - overhang) + (crown - crop_y) * scale, scale = 900 / 1080
    assert int(found.group(1)) == round(680 + 120 * 900 / 1080)


def test_composite_refuses_a_matte_from_another_cut(example_me, take, tmp_path):
    base, matte, layout = take
    other = tmp_path / "short-matte.mp4"
    ffmpeg("-f", "lavfi", "-i", "color=c=black:s=1080x1920:r=60:d=0.5", "-c:v", "libx264", "-pix_fmt", "yuv420p", other)
    done = run_kit("composite_v2.py", "--base", base, "--matte", other, "--layout", layout, "--pip-top", 1000,
                   "--crop-y", 300, "--out", tmp_path / "x.png")
    assert done.returncode != 0
    assert "matte" in (done.stdout + done.stderr).lower()
