"""kit.character.snap: an image model's "pixel art" onto a true pixel grid with a small palette."""

from __future__ import annotations

import json

import pytest

for _name in ("numpy", "PIL", "scipy", "skimage", "sklearn"):
    pytest.importorskip(_name, reason="needs the character group: uv sync --group character")

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from character_sprites import CANVAS_H, CANVAS_W, draw_figure, model_like, on_grey  # noqa: E402
from kit.character import snap  # noqa: E402


def _raw(tmp_path, *, scale=5, blur=0.7, noise=2.0):
    figure, _ = draw_figure("idle")
    path = tmp_path / "raw.png"
    model_like(on_grey(figure), scale, blur=blur, noise=noise).save(path)
    return path


def _load(path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGBA"))


def _opaque_colours(rgba: np.ndarray) -> set[tuple[int, int, int]]:
    solid = rgba[..., 3] == 255
    return {tuple(int(v) for v in c) for c in rgba[..., :3][solid]}


@pytest.mark.parametrize("pitch", [5.0, None], ids=["given pitch", "found pitch"])
def test_snap_palette_and_alpha(tmp_path, pitch):
    raw = _raw(tmp_path)
    out = tmp_path / "snapped.png"

    written = snap.snap(raw, out, colours=8, pitch=pitch)

    assert written == out
    with Image.open(out) as image:
        assert image.mode == "RGBA"
        assert image.size == (CANVAS_W, CANVAS_H)  # one image pixel per art pixel
        rgba = np.asarray(image)
    assert set(np.unique(rgba[..., 3]).tolist()) <= {0, 255}
    assert 2 <= len(_opaque_colours(rgba)) <= 8
    # the figure is solid and the background around it is clear
    assert rgba[..., 3].mean() > 0.1 * 255
    assert rgba[0, 0, 3] == 0 and rgba[-1, -1, 3] == 0

    meta = json.loads(out.with_suffix(".json").read_text())
    assert meta["pitch"] == pytest.approx(5.0, abs=0.05)
    assert meta["size"] == [CANVAS_W, CANVAS_H]
    assert len(meta["palette"]) == len(_opaque_colours(rgba))


def test_snap_keeps_the_figure_where_it_was_drawn(tmp_path):
    figure, _ = draw_figure("idle")
    rgba = _load(snap.snap(_raw(tmp_path), tmp_path / "s.png", colours=12, pitch=5))
    drawn = figure[..., 3] > 0
    found = rgba[..., 3] > 0
    agree = (drawn == found).mean()
    assert agree > 0.98, f"only {agree:.1%} of cells agree on what is figure and what is background"


def test_snap_onto_a_locked_palette_uses_only_its_colours(tmp_path):
    palette = [(34, 30, 38), (104, 62, 40), (232, 186, 156), (64, 116, 178), (72, 74, 88), (250, 250, 248)]
    out = snap.snap(_raw(tmp_path), tmp_path / "locked.png", colours=len(palette), pitch=5, palette=palette)
    found = _opaque_colours(_load(out))
    assert found and found <= set(palette)


def test_a_small_light_pocket_inside_the_figure_stays_solid(tmp_path):
    """Eye whites are close to a light grey background. They must not become holes in the face."""
    figure, info = draw_figure("idle")
    rgba = _load(snap.snap(_raw(tmp_path), tmp_path / "s.png", colours=16, pitch=5))
    x0, y0, x1, y1 = info["left_eye"]
    assert (rgba[y0 : y1 + 1, x0 : x1 + 1, 3] == 255).all()


def test_split_figures_finds_each_figure_left_to_right(tmp_path):
    from character_sprites import compose_row

    row = compose_row([draw_figure("idle")[:1] + (0,), draw_figure("wave")[:1] + (0,), draw_figure("shrug")[:1] + (0,)])
    big = np.asarray(model_like(row, 3))
    slots = snap.split_figures(big)
    assert len(slots) == 3
    assert [s.x0 for s in slots] == sorted(s.x0 for s in slots)
    assert all(abs(s.height - 111 * 3) <= 3 for s in slots[:1])


def test_a_picture_with_its_own_transparency_keeps_every_opaque_part(tmp_path):
    """Some image tools return a transparent background. Its alpha decides what is background, so a patch of
    the figure in nearly the grey the picture is laid on (here the eye whites) stays solid, a real hole in the
    figure stays clear, and a speck of a hole is filled."""
    figure, info = draw_figure("idle")
    figure[95:101, 52:58] = 0  # a 6 x 6 hole through the shirt
    figure[110:112, 62:64] = 0  # a 2 x 2 speck
    x0, y0, x1, y1 = info["left_eye"]
    near_grey = (231, 231, 229)
    for eye in ("left_eye", "right_eye"):
        a0, b0, a1, b1 = info[eye]
        patch = figure[b0 : b1 + 1, a0 : a1 + 1]
        white = (patch[..., :3] == (250, 250, 248)).all(2)
        patch[white, :3] = near_grey
    big = np.kron(figure, np.ones((5, 5, 1), np.uint8))
    raw = tmp_path / "alpha.png"
    Image.fromarray(big, "RGBA").save(raw)

    rgba = _load(snap.snap(raw, tmp_path / "s.png", colours=16, pitch=5))

    assert (rgba[y0 : y1 + 1, x0 : x1 + 1, 3] == 255).all(), "the eye white became a hole"
    assert (rgba[95:101, 52:58, 3] == 0).all(), "the hole through the shirt was filled"
    assert (rgba[110:112, 62:64, 3] == 255).all(), "the speck was left as a hole"
    assert ((rgba[..., 3] > 0) == (figure[..., 3] > 0)).mean() > 0.995
