"""Synthetic pixel figures for the character tests, drawn with numpy and Pillow.

No photo of any person is used. A figure is a small block-built character on a 120 x 170 canvas with a
transparent background: hair, a skin face with two eyes and a mouth, a shirt, trousers and shoes, with a
one-pixel dark outline around the whole silhouette. Its pose only moves the arms (and lifts the whole
figure for a jump), so the head is the same in every drawing, the way a real pose sheet keeps the face.

`FakeProvider` stands in for an image model: it reads what a prompt asks for and returns a sheet or a
strip of these figures, scaled up on a flat grey background, the way a model returns pixel art.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

CANVAS_W, CANVAS_H = 120, 170
OX, OY = 32, 40  # where the body's own (0, 0) sits on the canvas

GREY = (226, 226, 222)
OUTLINE = (34, 30, 38)
HAIR = (104, 62, 40)
SKIN = (232, 186, 156)
BLUSH = (214, 162, 134)
EYE_WHITE = (250, 250, 248)
PUPIL = (22, 22, 30)
MOUTH = (158, 70, 70)
SHIRT = (64, 116, 178)
SHIRT_SHADE = (50, 94, 150)
TROUSERS = (72, 74, 88)
SHOES = (122, 72, 50)
CARD = (247, 247, 247)
LAPTOP = (92, 92, 98)

# The face as drawn: skin from x 11 to 44 and y 9 to 36 (inclusive) in body coordinates.
FACE_BODY = (11, 9, 44, 36)
LEFT_EYE_BODY = (16, 17, 21, 20)
RIGHT_EYE_BODY = (34, 17, 39, 20)
MOUTH_BODY = (24, 29, 31, 30)

POSE_NAMES = (
    "idle", "talking", "point_left", "point_right", "point_up", "thumbs_up", "shrug", "thinking",
    "mind_blown", "celebrate", "hold_card", "typing", "facepalm", "wave",
)  # fmt: skip


def _box(body, x0, y0, x1, y1, colour, dx=0, dy=0):
    """Fill body cells x0..x1, y0..y1 (inclusive, body coordinates) with a colour."""
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            body[(x + dx, y + dy)] = colour


def _arms(body, pose: str, step: float, skin=SKIN, sleeve=SHIRT_SHADE) -> None:
    """The arms for a pose. `step` runs 0 to 1 for the in-betweens of the three points (1 is the full point)."""
    left_down = lambda: (_box(body, 10, 43, 14, 72, sleeve), _box(body, 10, 73, 14, 78, skin))  # noqa: E731
    right_down = lambda: (_box(body, 41, 43, 45, 72, sleeve), _box(body, 41, 73, 45, 78, skin))  # noqa: E731
    if pose in ("idle", "side", "back"):
        left_down(), right_down()
    elif pose == "talking":
        left_down()
        lift = int(round(2 * step))
        _box(body, 41, 43, 54, 50, sleeve)
        _box(body, 50, 31 - lift, 55, 37 - lift, skin)
        _box(body, 50, 38 - lift, 54, 42, sleeve)
    elif pose == "point_left":
        right_down()
        reach = int(round(22 * step))
        _box(body, 10 - reach, 43, 14, 48, sleeve)
        _box(body, 4 - reach, 43, 9 - reach, 47, skin)
    elif pose == "point_right":
        left_down()
        reach = int(round(22 * step))
        _box(body, 41, 43, 45 + reach, 48, sleeve)
        _box(body, 46 + reach, 43, 51 + reach, 47, skin)
    elif pose == "point_up":
        left_down()
        rise = int(round(46 * step))
        _box(body, 41, 43, 54, 48, sleeve)
        _box(body, 50, 43 - rise, 54, 48, sleeve)
        _box(body, 50, 36 - rise, 54, 42 - rise, skin)
    elif pose == "thumbs_up":
        left_down()
        _box(body, 41, 43, 45, 56, sleeve)
        _box(body, 46, 50, 54, 56, sleeve)
        _box(body, 50, 38, 54, 49, sleeve)
        _box(body, 50, 30, 56, 37, skin)
    elif pose == "shrug":
        _box(body, 2, 45, 14, 51, sleeve)
        _box(body, 41, 45, 53, 51, sleeve)
        _box(body, -3, 40, 2, 46, skin)
        _box(body, 53, 40, 58, 46, skin)
    elif pose == "thinking":
        left_down()
        _box(body, 36, 43, 45, 50, sleeve)
        _box(body, 30, 33, 37, 42, skin)
    elif pose == "mind_blown":
        _box(body, 3, 21, 8, 44, sleeve)
        _box(body, 47, 21, 52, 44, sleeve)
        _box(body, 2, 12, 8, 20, skin)
        _box(body, 47, 12, 53, 20, skin)
    elif pose == "celebrate":
        _box(body, 5, -10, 10, 44, sleeve)
        _box(body, 45, -10, 50, 44, sleeve)
        _box(body, 4, -16, 11, -11, skin)
        _box(body, 44, -16, 51, -11, skin)
    elif pose == "hold_card":
        left_down()
        _box(body, 41, 43, 45, 60, sleeve)
        _box(body, 44, 38, 66, 58, CARD)
        _box(body, 42, 56, 47, 61, skin)
    elif pose == "typing":
        _box(body, 10, 43, 14, 58, sleeve)
        _box(body, 41, 43, 45, 58, sleeve)
        _box(body, 12, 59, 43, 64, LAPTOP)
        _box(body, 15, 57, 19, 60, skin)
        _box(body, 36, 57, 40, 60, skin)
    elif pose == "facepalm":
        left_down()
        _box(body, 38, 22, 45, 44, sleeve)
        _box(body, 14, 15, 40, 22, skin)
    elif pose == "wave":
        left_down()
        _box(body, 41, 43, 54, 48, sleeve)
        _box(body, 50, 14, 54, 42, sleeve)
        _box(body, 50, 5, 57, 13, skin)
    else:
        raise ValueError(f"unknown pose {pose}")


def draw_figure(
    pose: str = "idle",
    *,
    step: float = 1.0,
    glasses: bool = False,
    skin: tuple[int, int, int] | None = None,
    hair: tuple[int, int, int] | None = None,
    mouth: tuple[int, int, int] | None = None,
    beard: tuple[int, int, int] | None = None,
    shirt: tuple[int, int, int] | None = None,
    hat: int = 0,
    hat_colour: tuple[int, int, int] = (176, 44, 44),
    neck: int = 5,
    collar: tuple[int, int, int] | None = None,
    beard_cheeks: bool = True,
    beard_length: int = 0,
    beard_end: int = 36,
    beard_start: int = 26,
    beard_edge: tuple[int, int, int] | None = None,
    mouth_wide: bool = False,
    mouth_size: tuple[int, int] | None = None,
    nose: tuple[int, int] | None = None,
    scarf: tuple[int, int, int] | None = None,
    scarf_rows: int = 3,
    top_rows: int | None = None,
) -> tuple[np.ndarray, dict]:
    """One figure on a 120 x 170 transparent canvas. Returns (RGBA uint8 array, info).

    info holds "face", "left_eye", "right_eye", "mouth" and "body" (the top, from the shoulders down) as
    (x0, y0, x1, y1) inclusive canvas boxes, and "air", how far the figure is lifted off the ground. The face
    box is the skin drawn around the eyes and mouth.

    Options draw other looks:
    - `skin`, `hair` and `mouth` colours (the cheeks are a shade of the skin) and `shirt`, the colour of the top;
      `mouth_wide` draws a mouth 14 cells wide instead of 8, and `mouth_size` one of (width, height) cells,
      centred on the face from row 29 down; `nose`, an outline-coloured block of (width, height) cells between
      the eyes, from row 22 down;
    - `glasses`, dark frames round both eyes joined by a bridge and running out to the hair, so the eyes are not
      closed in by skin;
    - `beard`, a beard in that colour over the jaw and chin with a moustache and the mouth showing through. With
      `beard_cheeks` False it starts across the whole face in one row, with no skin cheeks above the moustache,
      and `beard_length` rows of it hang below the chin, in front of the neck and the top. `beard_end` is the
      last face row it covers (36, the chin; a smaller row leaves the skin bare below it, like a moustache with
      mutton chops) and `beard_start` the first (26, the cheeks; a larger row leaves the skin bare above it, so a
      beard can start on the mouth's rows or under them). `beard_edge` paints the beard's first full row, the
      one the measurement meets first, in that colour (a lighter edge, a row blending into the skin);
    - `hat`, a hat that many rows tall on top of the hair, with a brim, in `hat_colour`;
    - `neck`, how many rows of neck show between the chin and the top (5, or fewer for a short neck, 0 for none:
      the body moves up to meet the head), and `collar`, a high collar in that colour over the neck, wider than
      the neck and touching the chin, like a turtleneck;
    - `scarf`, a scarf in that colour, `scarf_rows` tall and nearly as wide as the face, hanging from the chin
      over the neck and the top;
    - `top_rows`, a top only that many rows tall: the rest of it is in the trousers' colour.
    """
    body: dict[tuple[int, int], tuple[int, int, int]] = {}
    air = 10 if pose == "celebrate" else 0
    # the module's colours are read here, at the call, so a test can also change them on the module
    blush = BLUSH if skin is None else tuple(int(c * 0.92) for c in skin)
    skin = SKIN if skin is None else skin
    hair = HAIR if hair is None else hair
    mouth = MOUTH if mouth is None else mouth
    sleeve = SHIRT_SHADE if shirt is None else tuple(int(c * 0.85) for c in shirt)
    shirt = SHIRT if shirt is None else shirt
    mouth_box = (21, 29, 34, 30) if mouth_wide else MOUTH_BODY
    if mouth_size is not None:
        mouth_box = (28 - mouth_size[0] // 2, 29, 27 + mouth_size[0] - mouth_size[0] // 2, 28 + mouth_size[1])
    # head
    _box(body, 8, 0, 47, 8, hair)
    _box(body, 8, 9, 10, 26, hair)
    _box(body, 45, 9, 47, 26, hair)
    _box(body, *FACE_BODY, skin)
    if pose != "back":
        brow = 12 if glasses else 14  # above the frames of glasses, with skin between
        _box(body, 16, brow, 21, brow + 1, hair)  # brows
        _box(body, 34, brow, 39, brow + 1, hair)
        _box(body, *LEFT_EYE_BODY, EYE_WHITE)
        _box(body, *RIGHT_EYE_BODY, EYE_WHITE)
        _box(body, 18, 18, 19, 19, PUPIL)
        _box(body, 36, 18, 37, 19, PUPIL)
        _box(body, 14, 25, 16, 26, blush)
        _box(body, 39, 25, 41, 26, blush)
        _box(body, *mouth_box, mouth)
        if glasses:
            for x0, x1 in ((15, 22), (33, 40)):
                _box(body, x0, 16, x1, 16, OUTLINE)
                _box(body, x0, 21, x1, 21, OUTLINE)
                _box(body, x0, 16, x0, 21, OUTLINE)
                _box(body, x1, 16, x1, 21, OUTLINE)
            _box(body, 23, 18, 32, 18, OUTLINE)  # the bridge
            _box(body, 11, 18, 14, 18, OUTLINE)  # the arms, out to the hair
            _box(body, 41, 18, 44, 18, OUTLINE)
        if beard is not None:
            for y in range(beard_start, beard_end + 1):
                for x in range(11, 45):
                    if beard_cheeks and y <= 27 and 17 < x < 38:
                        continue  # the cheeks above the moustache stay skin
                    body[(x, y)] = beard
            if beard_start <= 28 <= beard_end:
                _box(body, 22, 28, 33, 28, beard)  # the moustache
            if beard_edge is not None:  # the first full row of the beard, in another colour
                first = max(beard_start, 28) if beard_cheeks else beard_start
                for x in range(11, 45):
                    if body.get((x, first)) == beard:
                        body[(x, first)] = beard_edge
            _box(body, *mouth_box, mouth)  # the mouth shows through the beard
        if nose is not None:
            _box(body, 28 - nose[0] // 2, 22, 27 + nose[0] - nose[0] // 2, 21 + nose[1], OUTLINE)
    else:
        _box(body, *FACE_BODY, hair)
    if hat:
        _box(body, 6, -hat, 49, 1, hat_colour)  # the crown, over the top of the hair
        _box(body, 2, 2, 53, 3, hat_colour)  # the brim
    # body: the neck under the chin, then everything from the shoulders down, lifted to meet a shorter neck
    if neck > 0:
        _box(body, 24, 37, 31, 36 + neck, skin)
        if collar is not None:
            _box(body, 16, 37, 39, 36 + neck, collar)
    lower: dict[tuple[int, int], tuple[int, int, int]] = {}
    _box(lower, 15, 42, 40, 77, shirt)
    _arms(lower, pose, step, skin, sleeve)
    if top_rows is not None:  # a short top: below its last row the shirt and the sleeves are trousers
        for (x, y), colour in list(lower.items()):
            if y >= 42 + top_rows and colour in (shirt, sleeve):
                lower[(x, y)] = TROUSERS
    _box(lower, 16, 78, 39, 101, TROUSERS)
    for y in range(88, 102):
        lower.pop((27, y), None)
        lower.pop((28, y), None)
    _box(lower, 14, 102, 26, 109, SHOES)
    _box(lower, 29, 102, 41, 109, SHOES)
    lift = 5 - neck
    for (x, y), colour in lower.items():
        body[(x, y - lift)] = colour
    if beard is not None and beard_length and pose != "back":
        _box(body, 16, 37, 39, 36 + beard_length, beard)  # hangs from the chin, in front of the neck and the top
    if scarf is not None:
        _box(body, 12, 37, 43, 36 + scarf_rows, scarf)  # hangs from the chin over the neck and the top

    rgba = np.zeros((CANVAS_H, CANVAS_W, 4), np.uint8)
    for (x, y), colour in body.items():
        cx, cy = x + OX, y + OY - air
        rgba[cy, cx, :3] = colour
        rgba[cy, cx, 3] = 255
    # a one-pixel outline just outside the silhouette
    solid = rgba[..., 3] > 0
    grown = solid.copy()
    grown[1:] |= solid[:-1]
    grown[:-1] |= solid[1:]
    grown[:, 1:] |= solid[:, :-1]
    grown[:, :-1] |= solid[:, 1:]
    edge = grown & ~solid
    rgba[edge, :3] = OUTLINE
    rgba[edge, 3] = 255

    def canvas_box(box):
        x0, y0, x1, y1 = box
        return (x0 + OX, y0 + OY - air, x1 + OX, y1 + OY - air)

    info = {
        "face": canvas_box(FACE_BODY),
        "left_eye": canvas_box(LEFT_EYE_BODY),
        "right_eye": canvas_box(RIGHT_EYE_BODY),
        "mouth": canvas_box(mouth_box),
        "body": canvas_box((15, 42 - lift, 40, 77 - lift)),
        "air": air,
    }
    return rgba, info


def crop(rgba: np.ndarray) -> np.ndarray:
    ys, xs = np.nonzero(rgba[..., 3])
    return rgba[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]


def on_grey(rgba: np.ndarray, grey=GREY) -> np.ndarray:
    out = np.empty(rgba.shape[:2] + (3,), np.uint8)
    out[:] = grey
    solid = rgba[..., 3] > 0
    out[solid] = rgba[..., :3][solid]
    return out


def model_like(rgb: np.ndarray, scale: int, *, blur: float = 0.0, noise: float = 0.0, seed: int = 3) -> Image.Image:
    """Scale an art-pixel image up the way a model draws pixel art: big square pixels, a little softness and noise."""
    big = np.kron(rgb, np.ones((scale, scale, 1), np.uint8))
    image = Image.fromarray(big, "RGB")
    if blur:
        image = image.filter(ImageFilter.GaussianBlur(blur))
    if noise:
        rng = np.random.default_rng(seed)
        values = np.asarray(image).astype(np.float64) + rng.normal(0.0, noise, big.shape)
        image = Image.fromarray(np.clip(values, 0, 255).round().astype(np.uint8), "RGB")
    return image


def compose_row(figures: list[tuple[np.ndarray, int]], *, gap: int = 16, margin: int = 20) -> np.ndarray:
    """Figures (cropped RGBA, air) left to right on one ground line, on a flat grey background, as RGB."""
    crops = [crop(f) for f, _ in figures]
    tallest = max(c.shape[0] + air for c, (_, air) in zip(crops, figures))
    width = margin * 2 + sum(c.shape[1] for c in crops) + gap * (len(crops) - 1)
    height = margin * 2 + tallest
    out = np.empty((height, width, 3), np.uint8)
    out[:] = GREY
    x = margin
    ground = height - margin
    for c, (_, air) in zip(crops, figures):
        y = ground - air - c.shape[0]
        solid = c[..., 3] > 0
        out[y : y + c.shape[0], x : x + c.shape[1]][solid] = c[..., :3][solid]
        x += c.shape[1] + gap
    return out


class FakeProvider:
    """An image tool for tests. It draws what the prompt asks for and counts its calls."""

    name = "fake"

    def __init__(self, scale: int = 4):
        self.scale = scale
        self.calls: list[tuple[str, int]] = []

    def _figure(self, pose: str, step: float = 1.0) -> tuple[np.ndarray, int]:
        rgba, info = draw_figure(pose, step=step)
        return rgba, info["air"]

    def generate(self, prompt: str, refs: list[Path], out: Path) -> Path:
        self.calls.append((out.name, len(refs)))
        stem = out.stem
        if stem.startswith("sheet"):
            row = [self._figure("idle"), self._figure("side"), self._figure("back")]
        elif stem.startswith("pose"):
            names = re.findall(r"^\d+\. ([a-z_]+):", prompt, re.M)
            assert names, "the pose prompt lists no poses"
            row = [self._figure("idle")] + [self._figure(name) for name in names]
        elif stem.startswith("move-"):
            move = re.match(r"move-([a-z_]+)-r\d+", stem).group(1)
            if move == "talking":
                row = [self._figure("talking", 1.0), self._figure("talking", 0.5),
                       self._figure("talking", 0.0), self._figure("talking", 0.5)]  # fmt: skip
            else:
                row = [self._figure("idle"), self._figure(move, 1 / 3), self._figure(move, 2 / 3), self._figure(move)]
        else:
            raise AssertionError(f"unexpected request {out.name}")
        image = model_like(compose_row(row), self.scale, noise=1.0)
        out.parent.mkdir(parents=True, exist_ok=True)
        image.save(out)
        return out


def image_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.size


def image_format(path: Path) -> str:
    with Image.open(path) as image:
        return image.format


def load_rgba(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGBA"))
