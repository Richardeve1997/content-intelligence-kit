"""Small picture helpers shared by the character steps: loading, placing, cleaning and the review boards.

Every sprite is an RGBA array with one array cell per art pixel and alpha either 0 or 255. Poses and
move frames share one canvas, 245 x 284 art pixels, with the feet on row 280: the lowest opaque row of a
standing figure is row 279, and its feet are centred on column 122.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage
from skimage.color import rgb2lab

CANVAS_W, CANVAS_H = 245, 284
BASELINE = 280  # the feet stand on this row: a standing figure's lowest opaque row is BASELINE - 1
FEET_COL = 122  # the column the feet are centred on
REF_SCALE = 4  # reference pictures sent to an image tool are drawn at this many screen pixels per art pixel

BOARD_BG = (236, 236, 233)
REF_BG = (226, 226, 226)  # the flat light grey the prompts ask for
DARK_BG = (40, 40, 44)
INK = (30, 30, 30)
MUTED = (110, 110, 110)

__all__ = [
    "CANVAS_W", "CANVAS_H", "BASELINE", "FEET_COL", "load_rgba", "save_rgba", "content_box", "crop", "paste",
    "feet_centre", "place", "despur", "to_hex", "from_hex", "upscale", "flatten", "lab_of",
    "colour_mask", "save_reference", "board", "label_font",
]  # fmt: skip


def load_rgba(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        rgba = np.asarray(image.convert("RGBA")).copy()
    rgba[..., 3] = np.where(rgba[..., 3] > 127, 255, 0)
    return rgba


def save_rgba(rgba: np.ndarray, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.ascontiguousarray(rgba, dtype=np.uint8), "RGBA").save(path)
    return path


def content_box(rgba: np.ndarray) -> tuple[int, int, int, int] | None:
    """(x0, y0, x1, y1) of the opaque cells, with x1 and y1 one past the end. None for an empty sprite."""
    solid = rgba[..., 3] > 0
    if not solid.any():
        return None
    ys = np.nonzero(solid.any(1))[0]
    xs = np.nonzero(solid.any(0))[0]
    return int(xs[0]), int(ys[0]), int(xs[-1]) + 1, int(ys[-1]) + 1


def crop(rgba: np.ndarray) -> np.ndarray:
    box = content_box(rgba)
    if box is None:
        return rgba[:0, :0].copy()
    x0, y0, x1, y1 = box
    return rgba[y0:y1, x0:x1].copy()


def paste(dst: np.ndarray, src: np.ndarray, x: int, y: int) -> None:
    """The opaque cells of `src` onto `dst` with src's top-left at (x, y). Whatever falls outside is dropped."""
    h, w = src.shape[:2]
    y0, x0 = max(0, -y), max(0, -x)
    y1, x1 = min(h, dst.shape[0] - y), min(w, dst.shape[1] - x)
    if y1 <= y0 or x1 <= x0:
        return
    part = src[y0:y1, x0:x1]
    target = dst[y + y0 : y + y1, x + x0 : x + x1]
    solid = part[..., 3] > 0
    target[solid] = part[solid]


def feet_centre(rgba: np.ndarray) -> tuple[float, int]:
    """(the centre column of the lowest rows, the row just below the figure). The lowest rows are the feet."""
    box = content_box(rgba)
    if box is None:
        raise ValueError("an empty sprite has no feet")
    _, top, _, bottom = box
    rows = max(4, round(0.05 * (bottom - top)))
    cols = np.nonzero(rgba[max(top, bottom - rows) : bottom, :, 3].any(0))[0]
    return (cols[0] + cols[-1] + 1) / 2, bottom


def place(figure: np.ndarray, *, air: int = 0) -> tuple[np.ndarray, tuple[int, int], dict[str, int]]:
    """Stand a cropped figure on the shared canvas: feet centred on FEET_COL, lowest row on BASELINE - 1.

    `air` lifts a jumping figure off the ground. When the figure does not fit, the air is given up first,
    then whatever still overhangs is cut off and reported in the returned dict ({"top": rows, ...}).
    Returns (canvas, (x, y) where the figure's top-left landed, what was cut)."""
    cx, bottom = feet_centre(figure)
    h, w = figure.shape[:2]
    x = int(round(FEET_COL - cx))
    y = BASELINE - max(0, int(air)) - bottom
    if y < 0:
        # give up as much air as the overhang needs; a figure taller than the canvas is then cut at the top
        y = min(0, BASELINE - bottom)
    clipped = {side: n for side, n in (("top", -y), ("left", -x), ("right", x + w - CANVAS_W)) if n > 0}
    canvas = np.zeros((CANVAS_H, CANVAS_W, 4), np.uint8)
    paste(canvas, figure, x, y)
    return canvas, (x, y), clipped


def despur(canvas: np.ndarray, passes: int = 2) -> int:
    """Drop opaque cells with at most one opaque neighbour: one-cell spikes and lone specks on the outline."""
    removed = 0
    for _ in range(passes):
        solid = canvas[..., 3] > 0
        neighbours = ndimage.convolve(solid.astype(int), np.ones((3, 3), int), mode="constant") - solid
        spur = solid & (neighbours <= 1)
        canvas[spur] = 0
        removed += int(spur.sum())
    return removed


def to_hex(rgb: Sequence[int]) -> str:
    return "#" + "".join(f"{int(v):02x}" for v in rgb[:3])


def from_hex(text: str) -> tuple[int, int, int]:
    digits = text.lstrip("#")
    return int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16)


def upscale(rgba: np.ndarray, k: int) -> np.ndarray:
    """Nearest-neighbour upscale by a whole number, so every art pixel stays a hard square."""
    return np.kron(rgba, np.ones((k, k, 1), np.uint8))


def flatten(rgba: np.ndarray, background: Sequence[int]) -> np.ndarray:
    """The sprite over a flat colour, as RGB."""
    out = np.empty(rgba.shape[:2] + (3,), np.uint8)
    out[:] = background
    solid = rgba[..., 3] > 0
    out[solid] = rgba[..., :3][solid]
    return out


def lab_of(colours: np.ndarray) -> np.ndarray:
    """Lab values for an (..., 3) array of 0..255 RGB colours."""
    arr = np.asarray(colours, dtype=np.float64)
    return rgb2lab(arr.reshape(-1, 1, 3) / 255.0).reshape(arr.shape)


def colour_mask(rgba: np.ndarray, colours: Iterable[Sequence[int]]) -> np.ndarray:
    """True where an opaque cell has one of `colours`."""
    codes = (rgba[..., 0].astype(np.int64) << 16) | (rgba[..., 1].astype(np.int64) << 8) | rgba[..., 2].astype(np.int64)
    wanted = [(int(c[0]) << 16) | (int(c[1]) << 8) | int(c[2]) for c in colours]
    return np.isin(codes, wanted) & (rgba[..., 3] > 0)


def save_reference(rgba: np.ndarray, path: Path) -> Path:
    """A reference picture for an image tool: the sprite at REF_SCALE on the flat light grey of the prompts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    margin = 8
    padded = np.zeros((rgba.shape[0] + 2 * margin, rgba.shape[1] + 2 * margin, 4), np.uint8)
    paste(padded, rgba, margin, margin)
    Image.fromarray(flatten(upscale(padded, REF_SCALE), REF_BG), "RGB").save(path)
    return path


def label_font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # an older Pillow without sized default fonts
        return ImageFont.load_default()


def board(
    cells: Sequence[tuple[np.ndarray, str, str]],
    *,
    title: str,
    columns: int,
    scale: int,
    background: Sequence[int] = BOARD_BG,
) -> Image.Image:
    """A review board: each cell is (RGBA or RGB image, label, small note), drawn at `scale` in a grid."""
    gap, margin, title_h, label_h = 16, 24, 56, 48
    cell_w = max(c[0].shape[1] for c in cells) * scale
    cell_h = max(c[0].shape[0] for c in cells) * scale
    rows = (len(cells) + columns - 1) // columns
    cols = min(columns, len(cells))
    width = margin * 2 + cols * cell_w + (cols - 1) * gap
    height = margin * 2 + title_h + rows * (cell_h + label_h) + (rows - 1) * gap
    page = Image.new("RGB", (width, height), tuple(background))
    draw = ImageDraw.Draw(page)
    draw.text((margin, margin), title, fill=INK, font=label_font(28))
    for i, (image, label, note) in enumerate(cells):
        r, c = divmod(i, columns)
        x = margin + c * (cell_w + gap)
        y = margin + title_h + r * (cell_h + label_h + gap)
        big = np.kron(image, np.ones((scale, scale, 1), np.uint8))
        if big.shape[2] == 4:
            tile = Image.fromarray(big, "RGBA")
            page.paste(tile, (x, y), tile)
        else:
            page.paste(Image.fromarray(big, "RGB"), (x, y))
        draw.text((x, y + big.shape[0] + 6), label, fill=INK, font=label_font(20))
        if note:
            draw.text((x, y + big.shape[0] + 28), note, fill=MUTED, font=label_font(15))
    return page
