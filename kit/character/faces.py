"""The character's face: measured on the creator's own sprite, checked on a board, then the mouth and blink states.

locate() reads the face from the picture rather than from fixed rectangles, so it works for any character:

1. The outline ink is the commonest colour on the edge of the figure. No colour as dark as the ink, and no
   near-neutral white (eye whites, teeth), ever counts as skin.
2. The skin is found by looking down the middle of the head from the top: each new colour there (a hat,
   then the hair, then the forehead) is tried as the skin in turn, and the first that gives a believable
   face wins. So a hat or tall hair of any height is passed over.
3. The skin and its shades are the colours near the skin, within a reach that grows with how light the skin
   is (shades of dark skin sit closer together). The face is the patch of those colours joined to the middle
   of the head, joined across lines up to two cells thick so the frames of glasses cannot cut it in two. It
   runs up and down from its widest row and stops at the chin, where it narrows to the neck.
4. In each row of the face, whatever is not skin between its first and last skin cell is a feature: eyes,
   brows, glasses, the mouth. The eye on each side is the biggest solid patch of features in the upper
   face, once lines two cells thick or thinner (brows, frames, lashes) are opened away; light cells right
   beside it (the white, highlights) join it.
5. The mouth is the widest mark below the eyes and between them, keeping to the lowest when there are a
   nose and a mouth. The search reaches down into a beard; a colour that fills a large area there (the skin,
   the beard) is not the mouth. A beard can be any colour, the outline's included: the sides of its first row
   set its colours, and it ends where the figure narrows to the neck, at the chin line, or where a row holds
   none of its colours (a top or a collar, however close in colour). When the mouth is drawn in the skin,
   whatever sits under the chin is clothing, not a beard. A beard still going at the end of the search has no
   known end: the mouth inside it is still measured, the head ends at the skin, and the board asks for the
   neck to be marked.
6. The neck is the narrowest row below the face. Everything above it is the head that the pose step copies
   onto other drawings, so the face stays the same in every pose.

make() draws the face board: the head with a coordinate grid, the measured boxes, a line where the head ends,
and the face states. When a box or the line is in the wrong place, or no face is found, the creator marks the
eyes, the mouth and the neck row by hand; the marks are kept in the character record and every later step
uses them.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from kit.character import art, store
from kit.config import KitError

SKIN_RANGE = 22.0  # the widest Lab distance from the skin that still counts as one of its shades
NEUTRAL_CHROMA = 6.0  # a light colour greyer than this is a white (eye whites, teeth), never skin
CHIN_NARROWING = 0.6  # the face ends where the skin is narrower than this share of its widest row, a beard where the figure is
CHIN_LINE = 0.4  # under a beard, a row whose cells inside the face are mostly outline (fewer than this share are not) is the chin line
BEARD_TRACE = 0.1  # under a beard, a row holding fewer of its colours than this share of the face's width ends it
EYE_AREA_SHARE = 0.15  # an eye covers at most this share of the face; a bigger "eye" means a wrong guess
AREA_SHARE = 0.25  # below the eyes, a colour covering more than this share fills an area (skin, a beard)
TRIES = 10  # how many colours down the middle of the head are tried as the skin, longest run first
BOARD = "faces/board.png"
FIX = (
    "Draw the sheet again with the whole face in view (a smaller hat or none, no hair over the eyes), or mark "
    f"the face by hand: read the boxes off me/character/{BOARD} and run "
    "uv run python -m kit.character faces --eyes x,y,w,h --mouth x,y,w,h"
)

__all__ = [
    "Box", "FaceBox", "FaceNotFound", "FaceStates", "FIX", "locate", "face_states", "skin_colours", "skin_ramp",
    "parse_box", "front_canvas", "measure", "apply_marks", "board_notes", "make",
]  # fmt: skip


class FaceNotFound(KitError):
    """The face could not be found on a sprite."""


@dataclass(frozen=True)
class Box:
    x: int
    y: int
    w: int
    h: int

    @property
    def x1(self) -> int:
        return self.x + self.w

    @property
    def y1(self) -> int:
        return self.y + self.h

    def shifted(self, dx: int, dy: int) -> "Box":
        return Box(self.x + dx, self.y + dy, self.w, self.h)

    def grown(self, left: int, top: int, right: int, bottom: int) -> "Box":
        return Box(self.x - left, self.y - top, self.w + left + right, self.h + top + bottom)

    def text(self) -> str:
        return f"{self.x},{self.y},{self.w},{self.h}"


def _box(x0: int, y0: int, x1: int, y1: int) -> Box:
    """A box from inclusive start and exclusive end coordinates."""
    return Box(int(x0), int(y0), int(x1 - x0), int(y1 - y0))


def _union(a: Box, b: Box) -> Box:
    return _box(min(a.x, b.x), min(a.y, b.y), max(a.x1, b.x1), max(a.y1, b.y1))


@dataclass(frozen=True)
class FaceBox:
    """Where the face is on a sprite. x, y, w and h cover the face: the skin around the eyes, and the mouth."""

    x: int
    y: int
    w: int
    h: int
    left_eye: Box
    right_eye: Box
    mouth: Box
    top: int  # the top row of the figure
    neck: int  # the first row below the head: the head is rows top to neck - 1
    skin: tuple[int, int, int]

    @property
    def x1(self) -> int:
        return self.x + self.w

    @property
    def y1(self) -> int:
        return self.y + self.h

    @property
    def box(self) -> Box:
        return Box(self.x, self.y, self.w, self.h)

    @property
    def eyes(self) -> Box:
        return _union(self.left_eye, self.right_eye)

    @property
    def centre(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2

    def shifted(self, dx: int, dy: int) -> "FaceBox":
        return FaceBox(
            self.x + dx, self.y + dy, self.w, self.h, self.left_eye.shifted(dx, dy), self.right_eye.shifted(dx, dy),
            self.mouth.shifted(dx, dy), self.top + dy, self.neck + dy, self.skin,
        )  # fmt: skip

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FaceBox":
        return cls(
            data["x"], data["y"], data["w"], data["h"], Box(**data["left_eye"]), Box(**data["right_eye"]),
            Box(**data["mouth"]), data["top"], data["neck"], tuple(data["skin"]),
        )  # fmt: skip


def _as_rgba(sprite: Path | np.ndarray) -> np.ndarray:
    if isinstance(sprite, np.ndarray):
        rgba = sprite.copy()
        rgba[..., 3] = np.where(rgba[..., 3] > 127, 255, 0)
        return rgba
    return art.load_rgba(Path(sprite))


def _colour_grid(rgba: np.ndarray) -> tuple[np.ndarray, list[tuple[int, int, int]]]:
    """(index of each cell's colour or -1 for clear, the colours) for an RGBA sprite."""
    solid = rgba[..., 3] > 0
    colours, inverse = np.unique(rgba[..., :3].reshape(-1, 3), axis=0, return_inverse=True)
    return np.where(solid, inverse.reshape(rgba.shape[:2]), -1), [tuple(int(v) for v in c) for c in colours]


# --- which colours are skin ---------------------------------------------------------------------------------------


def _outline_ink(rgba: np.ndarray) -> tuple[int, int, int] | None:
    """The commonest colour on the edge of the figure: the outline."""
    solid = rgba[..., 3] > 0
    edge = solid & ~ndimage.binary_erosion(solid)
    cells = rgba[..., :3][edge]
    if not len(cells):
        return None
    colours, counts = np.unique(cells, axis=0, return_counts=True)
    return tuple(int(v) for v in colours[int(np.argmax(counts))])


def _lightness(colour: tuple[int, int, int]) -> float:
    return float(art.lab_of(np.array([colour]))[0][0])


def _never_skin(colours: list[tuple[int, int, int]], ink: tuple[int, int, int] | None) -> np.ndarray:
    """For each colour: true when it can never be skin, because it is as dark as the outline ink or it is a
    near-neutral white."""
    labs = art.lab_of(np.array(colours, dtype=np.float64))
    chroma = np.hypot(labs[:, 1], labs[:, 2])
    never = (labs[:, 0] >= 80) & (chroma < NEUTRAL_CHROMA)
    if ink is not None:
        never |= labs[:, 0] <= _lightness(ink) + 3
    return never


def _skin_distance(labs: np.ndarray, goal: np.ndarray) -> np.ndarray:
    """How far each Lab colour is from the skin, with a change of hue counting twice. A shade of skin is the
    skin lighter or darker; a warm cloth or hair colour near it usually has a different hue."""
    chroma = np.hypot(labs[:, 1], labs[:, 2])
    goal_chroma = float(np.hypot(goal[1], goal[2]))
    hue_gap = np.abs(np.angle(np.exp(1j * (np.arctan2(labs[:, 2], labs[:, 1]) - np.arctan2(goal[2], goal[1])))))
    hue = 2 * np.sqrt(np.maximum(chroma * goal_chroma, 0.0)) * np.sin(hue_gap / 2)
    return np.sqrt((labs[:, 0] - goal[0]) ** 2 + (chroma - goal_chroma) ** 2 + (2.0 * hue) ** 2)


def _family(colours: list[tuple[int, int, int]], skin: tuple[int, int, int], ink) -> set[tuple[int, int, int]]:
    """The skin and its shades among `colours`.

    A colour belongs when it is within reach of the skin, with a change of hue counting twice (see
    _skin_distance). The reach grows with the skin's lightness (from 10 up to SKIN_RANGE), because the shades of
    dark skin sit closer together and dark hair is close to them too. Whites and colours as dark as the outline
    never belong, unless the skin itself is that dark."""
    if not colours:
        return {tuple(skin)}
    labs = art.lab_of(np.array(colours, dtype=np.float64))
    goal = art.lab_of(np.array([skin]))[0]
    reach = min(SKIN_RANGE, max(10.0, 0.3 * goal[0] + 6.0))
    chroma = np.hypot(labs[:, 1], labs[:, 2])
    keep = _skin_distance(labs, goal) <= reach
    keep &= ~((labs[:, 0] >= 80) & (chroma < NEUTRAL_CHROMA))
    if ink is not None and goal[0] > _lightness(ink) + 6:
        keep &= labs[:, 0] > _lightness(ink) + 3
    return {c for c, k in zip(colours, keep) if k} | {tuple(skin)}


def skin_colours(rgba: np.ndarray, skin: tuple[int, int, int]) -> set[tuple[int, int, int]]:
    """The sprite's colours that are the skin or one of its shades (see _family)."""
    rgba = _as_rgba(rgba)
    present = [tuple(int(v) for v in c) for c in np.unique(rgba[..., :3][rgba[..., 3] > 0], axis=0)]
    return _family(present, skin, _outline_ink(rgba))


def skin_ramp(sprite: np.ndarray, face: FaceBox, share: float = 0.005) -> set[tuple[int, int, int]]:
    """The skin shades the face is painted with: shades of the skin that cover at least `share` of the face box.

    Hands are found with this set. Colours near the skin can include hair or clothes of a similar warm colour,
    which the face itself does not use."""
    rgba = _as_rgba(sprite)
    region = rgba[max(0, face.y) : face.y1, max(0, face.x) : face.x1]
    colours, counts = np.unique(region[..., :3][region[..., 3] > 0], axis=0, return_counts=True)
    family = skin_colours(rgba, face.skin)
    total = max(1, int(counts.sum()))
    ramp = {tuple(int(v) for v in c) for c, n in zip(colours, counts) if n / total >= share}
    return {c for c in ramp if c in family} | {tuple(face.skin)}


# --- measuring ---------------------------------------------------------------------------------------------------


@dataclass
class _Sprite:
    rgba: np.ndarray
    grid: np.ndarray
    colours: list[tuple[int, int, int]]
    solid: np.ndarray
    ink: tuple[int, int, int] | None
    top: int
    bottom: int
    height: int
    cx: int
    half: int
    head_w: int
    head_end: int


def _read(rgba: np.ndarray) -> _Sprite:
    bounds = art.content_box(rgba)
    if bounds is None:
        raise FaceNotFound("The front view is empty, so there is no face to measure.")
    _, top, _, bottom = bounds
    height = bottom - top
    solid = rgba[..., 3] > 0
    grid, colours = _colour_grid(rgba)
    upper = solid[top : top + max(4, height // 2)]
    cx = int(np.median(np.nonzero(upper)[1]))
    cols = np.nonzero(upper.any(0))[0]
    head_w = int(cols[-1] - cols[0] + 1)
    return _Sprite(rgba, grid, colours, solid, _outline_ink(rgba), top, bottom, height, cx,
                   max(1, int(0.08 * head_w)), head_w, top + max(4, int(round(0.65 * height))))  # fmt: skip


def _eye(cells: np.ndarray, light: np.ndarray, *, solid_only: bool) -> Box | None:
    """The eye among a half-face's feature cells: the biggest solid patch once thin lines are opened away, with
    the parts the opening split from it and the light cells beside it. On a small sprite, where an eye can be a
    dot or a short line, the thin cells are tried too when nothing solid is left (`solid_only` turns that off)."""
    opened = ndimage.binary_opening(cells, structure=np.ones((3, 3)))
    for candidate in (opened,) if solid_only else (opened, cells):
        labels, count = ndimage.label(candidate, structure=np.ones((3, 3)))
        if not count:
            continue
        sizes = ndimage.sum(np.ones_like(labels), labels, index=np.arange(1, count + 1))
        best = int(np.argmax(sizes)) + 1
        where = ndimage.find_objects(labels)
        chosen = where[best - 1]
        box = _box(chosen[1].start, chosen[0].start, chosen[1].stop, chosen[0].stop)
        for k, other in enumerate(where, 1):
            if k != best and other is not None:
                near = _box(other[1].start, other[0].start, other[1].stop, other[0].stop)
                if near.x <= box.x1 + 1 and near.x1 >= box.x - 1 and near.y <= box.y1 + 1 and near.y1 >= box.y - 1:
                    box = _union(box, near)
        around = np.zeros_like(cells)
        around[max(0, box.y - 2) : box.y1 + 2, max(0, box.x - 2) : box.x1 + 2] = True
        lit, _ = ndimage.label(cells & light, structure=np.ones((3, 3)))
        for k in np.unique(lit[around & (lit > 0)]):
            ys, xs = np.nonzero(lit == k)
            box = _union(box, _box(xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))
        return box
    return None


def _gap(s: _Sprite) -> int:
    """How many rows of something else (a line, the skin above a moustache) a run of rows may step over: three on
    a small sprite, more on a finer one."""
    return max(3, int(round(0.03 * s.height)))


def _plain(s: _Sprite, family_idx: list[int]) -> list[int]:
    """The skin's colours and the outline's: what a face is made of, as opposed to hair, a beard or clothes."""
    return list(family_idx) + ([s.colours.index(s.ink)] if s.ink in s.colours else [])


def _beard_window(s: _Sprite, skin_end: int) -> int:
    """The row where the search for a beard under the skin stops (exclusive): 15% of the figure below the skin."""
    return min(s.bottom, skin_end + max(4, int(0.15 * s.height)))


def _ink_beard(s: _Sprite, x0: int, x1: int, skin_end: int, ink: int) -> bool:
    """Whether the outline colour is a beard here: rows under the skin that are mostly ink across the face, for
    more rows running than a line is thick (a chin line is one row, or two on a finer sprite). A black beard
    can snap onto the very colour of the outline, and the outline itself is never this wide for this long."""
    if ink < 0:
        return False
    width = x1 - x0
    need = max(3, int(round(s.height / 40)))
    run = 0
    for y in range(skin_end, _beard_window(s, skin_end)):
        run = run + 1 if int((s.grid[y, x0:x1] == ink).sum()) >= 0.4 * width else 0
        if run >= need:
            return True
    return False


def _beard(s: _Sprite, family_idx: list[int], x0: int, x1: int, skin_end: int, face_cx: float) -> tuple[int, set[int]]:
    """(the row below a beard, the beard's colours). With no beard that is (`skin_end`, no colours). With a beard
    whose end was not found it is (`skin_end`, its colours): the head then ends at the skin, the mouth is still
    looked for inside the beard, and the creator marks the neck.

    A beard row is mostly a colour that is neither skin nor the outline, across the face and in the middle of the
    chin. The beard can be any colour: its first row sets its colours, read off the sides of that row (a mouth
    sits in the middle, so a mouth on the beard's first row is never one of its colours), and later rows count
    only cells in exactly those colours. The snap merges colours closer than 4 Lab, so a colour the palette kept
    apart is something else however close it looks, and a top or a collar near the beard's colour ends it as
    surely as one in another colour. A beard one or two rows old whose next row is mostly something else takes
    that row's colours as well: its first row was a lighter edge, or a row blending into the skin. The outline
    colour counts as a beard colour when rows of it as wide as the face run deeper than a line (_ink_beard). A
    few rows with skin in the middle but the beard at the sides (the cheeks above a moustache, the lower lip)
    do not end it. The beard also ends where the figure narrows to the neck, at a row that is mostly outline
    inside the face (the chin line, with the neck under it; not when the beard itself is ink), and at a row
    holding almost none of its colours (clothing right under the chin, whatever its colour). A beard still going
    on the last row searched is one whose end was not found: a beard hanging well below the chin, or a top in
    exactly its colour with no neck and no chin line between."""
    width = x1 - x0
    half = max(1, int(0.15 * width))
    quarter = width / 4  # the beard's colours are read outside the middle half of a row, where a mouth sits
    ink = s.colours.index(s.ink) if s.ink in s.colours else -1
    inked = _ink_beard(s, x0, x1, skin_end, ink)
    plain = list(family_idx) if inked else _plain(s, family_idx)
    columns = np.arange(x0, x1)
    own = np.zeros(len(s.colours), bool)  # for every colour: whether it is one of the beard's
    painted = np.zeros(len(s.colours), int)  # how many cells of each colour the beard rows hold
    first, last, misses = None, None, 0
    for y in range(skin_end, _beard_window(s, skin_end)):
        if s.solid[y, x0:x1].sum() < CHIN_NARROWING * width:
            break  # the figure narrows to the neck: the head, and any beard on it, ends here
        cells_in_row = s.grid[y, x0:x1]
        solid = cells_in_row >= 0
        row, at = cells_in_row[solid], columns[solid]
        if last is not None and not inked and int((row != ink).sum()) < CHIN_LINE * width:
            break  # the row is mostly outline: the chin line, with the neck's skin showing under it
        middle = s.grid[y, max(0, int(face_cx) - half) : int(face_cx) + half + 1]
        middle = middle[middle >= 0]
        foreign = np.isin(row, plain, invert=True)
        other = row[foreign]
        sides = row[foreign & ((at < face_cx - quarter) | (at > face_cx + quarter))]
        across = len(middle) > 0 and np.isin(middle, plain, invert=True).mean() >= 0.5 and len(other) >= 0.4 * width
        if across and last is None:
            own[np.unique(sides)] = True  # the first beard row sets the beard's colours
        cells = int(own[other].sum())
        if across and last is not None and last - first < 2 and cells < 0.4 * width:
            own[np.unique(sides)] = True  # a young beard going on in other colours: its first row was an edge
            cells = int(own[other].sum())
        if last is not None and cells < BEARD_TRACE * width:
            break  # almost nothing of the beard in this row: clothing under the chin, in any colour
        if across and cells >= 0.4 * width:
            if first is None:
                first = y
            last, misses = y, 0
            painted += np.bincount(other, minlength=len(s.colours))
        else:
            misses += 1
            if misses > _gap(s):
                break
    else:
        if last is not None and misses == 0:  # still going on the last row searched: its end was not found
            return skin_end, {int(i) for i in np.nonzero(own & (painted > 0))[0]}
    if last is None:
        return skin_end, set()
    # the colours the beard is drawn in: those read off the sides of its rows, so a mouth inside it is never one
    return last + 1, {int(i) for i in np.nonzero(own & (painted > 0))[0]}


def _mouth(s: _Sprite, family_idx: list[int], x0: int, x1: int, eyes_bottom: int, limit: int, left: Box, right: Box,
           beard: set[int]) -> Box | None:  # fmt: skip
    """The mouth: the widest mark from below the eyes down to `limit` (the chin, or the bottom of a beard) that sits
    between the eyes, keeping to the lowest of the marks near that width when a nose and a mouth are both there
    (a nose can be drawn bigger than a thin mouth, but not wider). The beard's own colours are never the mouth."""
    region = np.zeros_like(s.solid)
    region[eyes_bottom:limit, x0:x1] = True
    region &= s.solid & ndimage.binary_erosion(s.solid)  # the edge of the figure (a chin outline) is not a mouth
    if not region.any():
        return None
    counts = np.bincount(s.grid[region], minlength=len(s.colours))
    fills = set(np.nonzero(counts > AREA_SHARE * region.sum())[0].tolist())
    marks = region & ~np.isin(s.grid, list(set(family_idx) | fills | beard))
    labels, count = ndimage.label(marks, structure=np.ones((3, 3)))
    between = (left.x1 - 2, right.x + 2)  # a mouth sits between the eyes, not under one of them
    found = []
    for k, where in enumerate(ndimage.find_objects(labels), 1):
        if where is None:
            continue
        box = _box(where[1].start, where[0].start, where[1].stop, where[0].stop)
        if between[0] <= box.x + box.w / 2 <= between[1]:
            found.append(box)
    if not found:
        return None
    widest = max(box.w for box in found)
    return max((box for box in found if box.w >= 0.4 * widest), key=lambda box: box.y)


def _measure(s: _Sprite, skin: tuple[int, int, int], seed_row: int) -> tuple[FaceBox, str | None]:
    """(the face measured with `skin` as the skin colour, seeded at `seed_row` on the middle of the head; what was
    made of a beard: "found", "dropped" when one was found but not where it ends, or None for none)."""
    family = _family(s.colours, skin, s.ink)
    family_idx = [i for i, c in enumerate(s.colours) if c in family]
    is_skin = np.isin(s.grid, family_idx) & s.solid
    is_skin[s.head_end :] = False
    bridge = 1 if s.height < 180 else 2  # lines up to two cells thick are bridged, four on a finer sprite
    joined = ndimage.binary_dilation(is_skin, structure=np.ones((3, 3)), iterations=bridge)
    patches, _ = ndimage.label(joined)
    seed_end = seed_row + max(3, int(0.08 * s.height))
    seeds = np.where(is_skin, patches, 0)[seed_row:seed_end, max(0, s.cx - s.half) : s.cx + s.half + 1]
    seeds = seeds[seeds > 0]
    if not len(seeds):
        raise FaceNotFound("no skin in the middle of the head")
    face = (patches == np.bincount(seeds).argmax()) & is_skin
    spans: dict[int, tuple[int, int]] = {}
    widths = np.zeros(face.shape[0], int)
    for y in np.nonzero(face.any(1))[0]:
        xs = np.nonzero(face[y])[0]
        spans[int(y)] = (int(xs[0]), int(xs[-1]) + 1)
        widths[y] = xs[-1] - xs[0] + 1
    widest_row = int(np.argmax(widths))
    widest = int(widths[widest_row])
    if widest < 4:
        raise FaceNotFound("the skin patch is too small to be a face")

    def wide(y: int) -> bool:
        return 0 <= y < len(widths) and widths[y] >= CHIN_NARROWING * widest

    gap = _gap(s)
    y0 = widest_row
    while any(wide(y0 - step) for step in range(1, gap + 1)):
        y0 -= 1
    y1 = widest_row + 1
    while any(wide(y1 - 1 + step) for step in range(1, gap + 1)):
        y1 += 1
    span = np.zeros_like(face)
    for y in range(y0, y1):
        if y not in spans:  # a row a line crosses: it spans what the rows beside it span
            near = [spans[r] for d in range(1, gap + 1) for r in (y - d, y + d) if r in spans]
            if not near:
                continue
            spans[y] = (min(a for a, _ in near), max(b for _, b in near))
        span[y, spans[y][0] : spans[y][1]] = True
    cols = np.nonzero(span[y0:y1].any(0))[0]
    x0, x1 = int(cols[0]), int(cols[-1]) + 1
    features = span & ~face & s.solid
    face_cx = (x0 + x1) / 2
    beard_end, beard = _beard(s, family_idx, x0, x1, y1, face_cx)
    state = "found" if beard_end > y1 else ("dropped" if beard else None)
    # the eyes sit in the upper part of the face and beard, and the mouth is looked for down through the beard. A
    # beard whose end was not found still holds the mouth: the search goes as far as the beard search did
    searched = _beard_window(s, y1) if state == "dropped" else max(y1, beard_end)
    columns = np.arange(features.shape[1])[None, :]
    lightness = art.lab_of(np.array(s.colours, dtype=np.float64))[:, 0]
    light = np.where(s.grid >= 0, lightness[np.maximum(s.grid, 0)], 0.0) >= 80
    solid_only = s.height >= 150  # on a full-size sprite an eye is always a solid patch, never a strand of hair

    def eyes_above(split_y: int) -> tuple[Box | None, Box | None]:
        upper = features.copy()
        upper[split_y:] = False
        return (_eye(upper & (columns < face_cx), light, solid_only=solid_only),
                _eye(upper & (columns >= face_cx), light, solid_only=solid_only))  # fmt: skip

    def doubtful(left: Box | None, right: Box | None) -> bool:
        """Missing, touching, or not level: what a mouth taken for an eye looks like."""
        if left is None or right is None or right.x - left.x1 < 1:
            return True
        return abs((left.y + left.h / 2) - (right.y + right.h / 2)) > max(2.0, 0.25 * (y1 - y0))

    split_skin = y0 + int(0.62 * (y1 - y0))
    left_eye, right_eye = eyes_above(y0 + int(0.62 * (searched - y0)))
    if searched > y1 and doubtful(left_eye, right_eye):
        # what was followed under the chin lowered the split, and a mouth as big as an eye was taken for one:
        # the eyes are then looked for above the skin alone
        left_eye, right_eye = eyes_above(split_skin)
    if left_eye is None or right_eye is None:
        raise FaceNotFound("two eyes could not be told apart in the face")

    eyes_bottom = max(left_eye.y1, right_eye.y1)
    if state == "dropped":
        # what was followed past the search may be a top in the beard's colour: a mouth drawn in the skin says so
        # (a mark narrower than a mouth is a nose), and only when there is none is the mouth looked for down
        # through the rows the beard search covered
        mouth = _mouth(s, family_idx, x0, x1, eyes_bottom, y1, left_eye, right_eye, set())
        if mouth is None or mouth.w < 0.15 * (x1 - x0):
            mouth = _mouth(s, family_idx, x0, x1, eyes_bottom, searched, left_eye, right_eye, beard)
    else:
        mouth = _mouth(s, family_idx, x0, x1, eyes_bottom, searched, left_eye, right_eye, beard)
    if mouth is not None and mouth.y1 <= y1 and state is not None:
        # the mouth is drawn in the skin, so what sits under the chin is a collar, a scarf or a top, not a beard
        beard_end, beard, state = y1, set(), None
    reached = max(y1, beard_end)
    if mouth is None:  # no mouth drawn as a separate mark: assume a short line in the usual place
        width = max(3, int(0.3 * (x1 - x0)))
        row = eyes_bottom + int(0.55 * (y1 - eyes_bottom))
        mouth = _box(int(face_cx - width / 2), row, int(face_cx - width / 2) + width, row + 1)
    # between the eyes and down to the mouth (the bridge of the nose) a face is mostly skin, and the eyes do not
    # touch. Otherwise the "eyes" were parts of a larger area (the real face, seen from the hair around it) and
    # this colour is not the skin.
    if right_eye.x - left_eye.x1 < 1:
        raise FaceNotFound("the two eyes found touch each other")
    eye_row = int(min(left_eye.y + left_eye.h / 2, right_eye.y + right_eye.h / 2))
    middle = (slice(eye_row, max(eye_row + 1, mouth.y)), slice(left_eye.x1, right_eye.x))
    opaque = s.solid[middle]
    if opaque.any() and is_skin[middle][opaque].mean() < 0.4:
        raise FaceNotFound("the middle of what looked like a face is not skin")
    bottom_of_face = max(y1, mouth.y1)

    # the neck: the narrowest row of the silhouette below the face (and any beard), before the shoulders
    start = max(bottom_of_face, reached)
    neck = start
    search_end = min(s.bottom, start + max(3, int(0.12 * s.height)))
    row_widths = s.solid[:, max(0, x0 - 4) : min(s.rgba.shape[1], x1 + 4)].sum(1)
    if search_end > start:
        rows = row_widths[start:search_end]
        narrow = int(np.argmin(rows))
        if rows[narrow] < 0.8 * (x1 - x0):
            neck = start + narrow
    return FaceBox(x0, y0, x1 - x0, bottom_of_face - y0, left_eye, right_eye, mouth, int(s.top), int(neck),
                   tuple(int(v) for v in skin)), state  # fmt: skip


def _believable(face: FaceBox, head_w: int) -> bool:
    """A face is wide enough to be one and a fair share of the head, has two small eyes about level, and a mouth
    below them."""
    area = max(1, face.w * face.h)
    head_area = max(1, head_w * (face.neck - face.top))
    left, right = face.left_eye, face.right_eye
    level = abs((left.y + left.h / 2) - (right.y + right.h / 2)) <= max(2.0, 0.25 * face.h)
    small = all(eye.w * eye.h <= EYE_AREA_SHARE * area for eye in (left, right))
    return (face.w >= 0.3 * head_w and area >= 0.2 * head_area and level and small and left.x < right.x
            and face.mouth.y >= min(left.y1, right.y1))  # fmt: skip


def locate(sprite: Path | np.ndarray, details: dict | None = None) -> FaceBox:
    """Measure the face on a front-facing sprite (a path or an RGBA array). Raises FaceNotFound.

    `details`, when given, gets a "beard" entry: "found" when a beard was measured under the face, "dropped" when
    one was found but not where it ends, and None when there is none."""
    s = _read(_as_rgba(sprite))
    never = _never_skin(s.colours, s.ink)
    # the colour of each row down the middle of the head, and each colour's longest unbroken run of rows: the skin
    # of the forehead and nose makes a long run, while hair shades come and go in short ones
    runs: dict[int, tuple[int, int]] = {}  # colour index -> (longest run, the row it starts on)
    current, length, start = -1, 0, s.top
    for y in range(s.top, s.head_end + 1):
        index = -1
        if y < s.head_end:
            band = s.grid[y, max(0, s.cx - s.half) : s.cx + s.half + 1]
            band = band[band >= 0]
            index = int(np.bincount(band).argmax()) if len(band) else -1
        if index == current:
            length += 1
            continue
        if current >= 0 and not never[current] and length > runs.get(current, (0, 0))[0]:
            runs[current] = (length, start)
        current, length, start = index, 1, y
    order = sorted(runs.items(), key=lambda item: (-item[1][0], item[1][1]))[:TRIES]
    reasons: list[str] = []
    for index, (_, row) in order:
        try:
            face, beard = _measure(s, s.colours[index], row)
        except FaceNotFound as problem:
            reasons.append(str(problem))
            continue
        if _believable(face, s.head_w):
            if details is not None:
                details["beard"] = beard
            return face
        reasons.append("what was found did not look like a face")
    why = reasons[0] if reasons else "no colour down the middle of the head could be the skin"
    raise FaceNotFound(f"The face on the front view could not be measured ({why}).")


# --- marking the face by hand ----------------------------------------------------------------------------------------


def parse_box(text: str) -> Box:
    """A box written as x,y,w,h (whole numbers, in the canvas coordinates of the face board)."""
    parts = [p.strip() for p in str(text).split(",")]
    if len(parts) != 4 or not all(p.lstrip("-").isdigit() for p in parts):
        raise KitError(f"{text!r} is not a box. Write it as four whole numbers, x,y,w,h, for example 96,120,40,12.")
    x, y, w, h = (int(p) for p in parts)
    if w < 1 or h < 1:
        raise KitError("A box needs a width and a height of at least 1.")
    return Box(x, y, w, h)


def _split_eyes(canvas: np.ndarray, eyes: Box, family: set) -> tuple[Box, Box]:
    """One box round both eyes, split in two: each half tightened to the eye drawn in it."""
    middle = eyes.x + eyes.w // 2
    boxes = []
    for x0, x1 in ((eyes.x, middle), (middle, eyes.x1)):
        region = canvas[max(0, eyes.y) : eyes.y1, max(0, x0) : x1]
        cells = ~art.colour_mask(region, family) & (region[..., 3] > 0)
        opened = ndimage.binary_opening(cells, structure=np.ones((3, 3)))
        use = opened if opened.any() else cells
        if use.any():
            ys, xs = np.nonzero(use)
            boxes.append(_box(max(0, x0) + xs.min(), max(0, eyes.y) + ys.min(), max(0, x0) + xs.max() + 1, max(0, eyes.y) + ys.max() + 1))
        else:
            boxes.append(_box(x0, eyes.y, x1, eyes.y1))
    return boxes[0], boxes[1]


def _face_from_marks(canvas: np.ndarray, eyes: Box, mouth: Box) -> FaceBox:
    """A face built from the creator's marks alone, for a sprite whose face could not be measured."""
    s = _read(canvas)
    area = _union(eyes, mouth).grown(max(1, eyes.w // 4), eyes.h, max(1, eyes.w // 4), mouth.h)
    area = _box(max(0, area.x), max(0, area.y), min(canvas.shape[1], area.x1), min(canvas.shape[0], area.y1))
    region = s.grid[area.y : area.y1, area.x : area.x1].copy()
    for mark in (eyes, mouth):
        region[max(0, mark.y - area.y) : max(0, mark.y1 - area.y), max(0, mark.x - area.x) : max(0, mark.x1 - area.x)] = -1
    never = _never_skin(s.colours, s.ink)
    values = region[region >= 0]
    values = values[~never[values]] if len(values) else values
    if not len(values):
        raise FaceNotFound("There is no skin around the marked eyes and mouth. Check the boxes on the face board.")
    skin = s.colours[int(np.bincount(values).argmax())]
    left, right = _split_eyes(canvas, eyes, _family(s.colours, skin, s.ink))
    neck = mouth.y1 + 1
    search_end = min(s.bottom, mouth.y1 + max(3, int(0.15 * s.height)))
    row_widths = s.solid[:, max(0, area.x - 4) : min(canvas.shape[1], area.x1 + 4)].sum(1)
    if search_end > mouth.y1:
        neck = mouth.y1 + int(np.argmin(row_widths[mouth.y1 : search_end]))
    return FaceBox(area.x, area.y, area.w, area.h, left, right, mouth, s.top, max(neck, mouth.y1), skin)


def apply_marks(canvas: np.ndarray, face: FaceBox | None, marks: dict | None) -> FaceBox | None:
    """The face with the creator's marks in place of what was measured: one box round both eyes, the mouth's
    box, and the neck row (the first row under the head). With no measured face, both the eyes and the mouth
    must be marked; with only one of them the result is None. The head always reaches down past the mouth."""
    marks = marks or {}
    eyes = Box(*marks["eyes"]) if marks.get("eyes") else None
    mouth = Box(*marks["mouth"]) if marks.get("mouth") else None
    neck = marks.get("neck")
    if face is None:
        if not (eyes and mouth):
            return None
        face = _face_from_marks(canvas, eyes, mouth)
        return face if neck is None else replace(face, neck=max(int(neck), face.mouth.y1))
    if eyes:
        left, right = _split_eyes(canvas, eyes, skin_colours(canvas, face.skin))
    else:
        left, right = face.left_eye, face.right_eye
    mouth = mouth or face.mouth
    box = _union(_union(face.box, _union(left, right)), mouth)
    neck = max(face.neck if neck is None else int(neck), mouth.y1)
    return FaceBox(box.x, box.y, box.w, box.h, left, right, mouth, face.top, neck, face.skin)


def front_canvas(folder: Path, state: dict[str, Any]) -> np.ndarray:
    """The front view stood on the shared canvas: the idle pose once the sheet is approved, else the sheet's
    front view placed the same way."""
    idle = folder / "poses" / "idle.png"
    if state.get("sheet", {}).get("approved") and idle.is_file():
        return art.load_rgba(idle)
    canvas, _, _ = art.place(art.load_rgba(folder / "sheet" / "front.png"))
    return canvas


def measure(canvas: np.ndarray, marks: dict | None, details: dict | None = None) -> tuple[FaceBox | None, str | None]:
    """(the face, measured and then corrected by any marks, or None; why it could not be measured, or None).
    `details` is passed on to locate()."""
    try:
        face, reason = locate(canvas, details), None
    except FaceNotFound as problem:
        face, reason = None, str(problem)
    if marks:
        face = apply_marks(canvas, face, marks)
        if face is None and reason:
            reason += " Mark both the eyes and the mouth when the face cannot be measured."
    return face, reason


# --- the face states ---------------------------------------------------------------------------------------------


@dataclass
class FaceStates:
    patches: dict[str, np.ndarray]  # name -> opaque RGBA crop
    rects: dict[str, Box]  # name -> where the patch goes on the sprite it came from


def _colour_set(rgba: np.ndarray) -> list[tuple[int, int, int]]:
    present = np.unique(rgba[..., :3][rgba[..., 3] > 0], axis=0)
    return [tuple(int(v) for v in c) for c in present]


def face_states(sprite: np.ndarray, face: FaceBox, *, open_rows: int = 3) -> FaceStates:
    """Mouth closed, half open and open, eyes open and closed, as patches cut from `sprite` and changed.

    The closed mouth and the open eyes are the sprite as drawn. For the open mouths the drawn mouth is painted
    over with whatever surrounds it (skin, or a beard) and an opening is drawn on its middle: the inside in the
    sprite's darkest colour, teeth in its lightest, lips and tongue in the mouth's own colour. For the blink
    the eyes go back to skin under a lash line. Strokes are as thick as the mouth and the eyes are drawn, and
    every colour is one the sprite has."""
    rgba = _as_rgba(sprite)
    colours = _colour_set(rgba)
    labs = art.lab_of(np.array(colours))
    darkest = colours[int(np.argmin(labs[:, 0]))]
    lightest = colours[int(np.argmax(labs[:, 0]))]
    family = skin_colours(rgba, face.skin)
    shades = sorted(family, key=lambda c: art.lab_of(np.array([c]))[0][0])
    shadow = shades[0] if shades and shades[0] != face.skin else face.skin
    m = face.mouth
    mouth_cells = rgba[max(0, m.y) : m.y1, max(0, m.x) : m.x1]
    lip_pick = [tuple(int(v) for v in c) for c in mouth_cells[..., :3][mouth_cells[..., 3] > 0]]
    lip_pick = [c for c in lip_pick if c not in family]
    lip = max(set(lip_pick), key=lip_pick.count) if lip_pick else shadow
    if lip == darkest:
        lip = shadow
    # what surrounds the drawn mouth: the colour the closed mouth is painted over with when it opens
    ring = np.zeros(rgba.shape[:2], bool)
    ring[max(0, m.y - 1) : m.y1 + 1, max(0, m.x - 1) : m.x1 + 1] = True
    ring[max(0, m.y) : m.y1, max(0, m.x) : m.x1] = False
    around = [tuple(int(v) for v in c) for c in rgba[..., :3][ring & (rgba[..., 3] > 0)]]
    # the mouth's own dark edge is not what surrounds it, unless the darkest colour is most of the ring (a beard
    # drawn in the outline colour)
    if around.count(darkest) < 0.6 * len(around):
        around = [c for c in around if c != darkest] or [face.skin]
    surround = max(set(around), key=around.count)

    open_rows = int(np.clip(open_rows, 1, 4))
    unit = max(1, int(round(m.h / 2)))  # a mouth drawn two cells thick gets strokes two cells thick
    lash_unit = max(1, int(round(min(face.left_eye.h, face.right_eye.h) / 7)))
    centre = (m.x + m.x1) // 2
    top = m.y + max(0, (m.h - 2 * unit) // 2)  # the opening sits on the middle of the drawn mouth
    half_w = max(unit, int(round(m.w * 0.36)))
    eyes_rect = face.eyes.grown(lash_unit, lash_unit, lash_unit, lash_unit)
    mouth_rect = _union(m.grown(unit, unit, unit, unit),
                        _box(centre - half_w - unit, top - unit, centre + half_w + unit, top + (open_rows + 2) * unit))  # fmt: skip
    if mouth_rect.y < eyes_rect.y1:  # a small face: the mouth patch starts below the eye patch
        mouth_rect = _box(mouth_rect.x, eyes_rect.y1, mouth_rect.x1, max(mouth_rect.y1, eyes_rect.y1 + 1))

    def cut(source: np.ndarray, rect: Box) -> np.ndarray:
        """A straight copy of the rectangle; any part outside the sprite stays clear."""
        out = np.zeros((rect.h, rect.w, 4), np.uint8)
        sy0, sx0 = max(0, rect.y), max(0, rect.x)
        sy1, sx1 = min(source.shape[0], rect.y1), min(source.shape[1], rect.x1)
        if sy1 > sy0 and sx1 > sx0:
            out[sy0 - rect.y : sy1 - rect.y, sx0 - rect.x : sx1 - rect.x] = source[sy0:sy1, sx0:sx1]
        return out

    def band(canvas: np.ndarray, row: int, half: int, colour: tuple[int, int, int]) -> None:
        """Paint `unit` rows from `row`, `half` cells either side of the mouth's middle, inside the mouth patch."""
        x0, x1 = max(centre - half, mouth_rect.x, 0), min(centre + half + (m.w % 2), mouth_rect.x1, canvas.shape[1])
        y0, y1 = max(row, mouth_rect.y, 0), min(row + unit, mouth_rect.y1, canvas.shape[0])
        if x1 > x0 and y1 > y0:
            canvas[y0:y1, x0:x1, :3] = colour
            canvas[y0:y1, x0:x1, 3] = 255

    def unmouthed() -> np.ndarray:
        """The sprite with the drawn mouth painted over with what surrounds it."""
        bare = rgba.copy()
        region = bare[max(0, m.y) : m.y1, max(0, m.x) : m.x1]
        drawn = ~art.colour_mask(region, family | {surround}) & (region[..., 3] > 0)
        region[..., :3][drawn] = surround
        return bare

    narrow = max(1, half_w - unit)
    mid = unmouthed()
    band(mid, top, narrow, darkest)
    band(mid, top + unit, half_w, darkest)
    band(mid, top + 2 * unit, narrow, lip)

    wide = unmouthed()
    band(wide, top, narrow, lightest)  # top teeth
    for row in range(1, open_rows + 1):
        band(wide, top + row * unit, half_w if row < open_rows else narrow, darkest)
    band(wide, top + open_rows * unit, max(1, half_w // 2), lip)  # a hint of tongue
    band(wide, top + (open_rows + 1) * unit, narrow, lip)

    shut = rgba.copy()
    for eye in (face.left_eye, face.right_eye):
        # close the eye itself: its solid cells that are not skin, and its light cells (the white and any
        # highlight). Thin lines in the box, such as the frame of glasses, are left as they are.
        region = shut[max(0, eye.y) : eye.y1, max(0, eye.x) : eye.x1]
        open_cells = ~art.colour_mask(region, family) & (region[..., 3] > 0)
        solid_cells = ndimage.binary_opening(open_cells, structure=np.ones((3, 3)))
        light_cells = open_cells & (art.lab_of(region[..., :3])[..., 0] >= 80)
        region[..., :3][(solid_cells | light_cells) if solid_cells.any() else open_cells] = face.skin
        lash = eye.y + (2 * eye.h) // 3 if eye.h > 1 else eye.y
        end_w = lash_unit if eye.w >= 4 * lash_unit else 0
        for x in range(max(0, eye.x), min(shut.shape[1], eye.x1)):
            end = end_w and (x < eye.x + end_w or x >= eye.x1 - end_w)
            row = lash - lash_unit if end else lash
            shut[max(row, 0) : max(row + lash_unit, 0), x, :3] = darkest

    patches = {
        "mouth_closed": cut(rgba, mouth_rect),
        "mouth_mid": cut(mid, mouth_rect),
        "mouth_open": cut(wide, mouth_rect),
        "eyes_open": cut(rgba, eyes_rect),
        "eyes_closed": cut(shut, eyes_rect),
    }
    rects = {name: (mouth_rect if name.startswith("mouth") else eyes_rect) for name in patches}
    return FaceStates(patches, rects)


# --- the face board and the faces step ----------------------------------------------------------------------------------


def _outline(draw: ImageDraw.ImageDraw, box: Box, k: int, origin: tuple[int, int], margin: int, label: str) -> None:
    """A box drawn black with a white edge inside, so it shows on any colour, with its name above it. `origin` is
    the canvas cell at the board's top-left corner, past a `margin` of pixels for the grid numbers."""
    ox, oy = origin
    x0, y0 = margin + (box.x - ox) * k, margin + (box.y - oy) * k
    x1, y1 = margin + (box.x1 - ox) * k - 1, margin + (box.y1 - oy) * k - 1
    draw.rectangle([x0 - 2, y0 - 2, x1 + 2, y1 + 2], outline=art.INK, width=3)
    draw.rectangle([x0, y0, x1, y1], outline=(255, 255, 255), width=1)
    _label(draw, x0 - 2, max(0, y0 - 27), label)


def _label(draw: ImageDraw.ImageDraw, x: int, y: int, text: str) -> None:
    """A name on a small white label with its top-left corner at (x, y), so it reads over any colour."""
    font = art.label_font(16)
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    draw.rectangle([x, y, x + (right - left) + 10, y + 23], fill=(255, 255, 255), outline=art.INK)
    draw.text((x + 5 - left, y + 3 - top + 3), text, fill=art.INK, font=font)


def _neck_line(draw: ImageDraw.ImageDraw, row: int, k: int, origin: tuple[int, int], margin: int, right: int) -> None:
    """A line across the board on top of the neck row, black with a white edge, named at its right end on a
    white label so the name reads over hair or clothes of any colour."""
    ox, oy = origin
    y = margin + (row - oy) * k
    draw.line([margin, y + 1, right, y + 1], fill=art.INK, width=3)
    draw.line([margin, y - 1, right, y - 1], fill=(255, 255, 255), width=1)
    label = f"neck: row {row}"
    left, _, end, _ = draw.textbbox((0, 0), label, font=art.label_font(16))
    _label(draw, right - (end - left) - 16, y - 27, label)


def board_notes(face: FaceBox, *, marked: bool, beard: str | None) -> list[str]:
    """The lines under a face board with a measured face: the boxes and the neck row, how to correct them, and a
    word on the beard when one was found under the face (`beard` is "found") or found but not where it ends
    ("dropped"). A beardless character gets no beard advice."""
    lines = [
        f"Eyes: {face.eyes.text()}    Mouth: {face.mouth.text()}    Neck: row {face.neck}" + ("    (marked by hand)" if marked else ""),
        "The head is everything above the neck line, and it should end just under the chin. If a box or the line is in "
        "the wrong place,",
        "mark it by hand: uv run python -m kit.character faces --eyes x,y,w,h --mouth x,y,w,h --neck row",
    ]  # fmt: skip
    if beard == "found":
        lines.append("The beard was measured down to the neck line. If it hangs lower than the line, mark the neck by hand.")
    elif beard == "dropped":
        lines.append("A beard was found but not where it ends: it may hang well below the chin, or the top under it may be "
                     "in its colour. Check the mouth box, and mark the neck by hand.")  # fmt: skip
    return lines


def _board(canvas: np.ndarray, face: FaceBox | None, states: FaceStates | None, *, marked: bool, note: str | None,
           beard: str | None = None) -> Image.Image:  # fmt: skip
    bounds = art.content_box(canvas)
    if face is not None:
        x0, y0 = max(0, face.x - 14), max(0, face.top - 4)
        # down past the neck line, and at least half way down the figure, so a beard or collar below the chin shows
        lowest = max(face.neck + 12, bounds[1] + (bounds[3] - bounds[1]) // 2)
        x1, y1 = min(canvas.shape[1], face.x1 + 14), min(canvas.shape[0], lowest)
    else:
        x0, y0, x1 = bounds[0], bounds[1], bounds[2]
        y1 = bounds[1] + int(0.6 * (bounds[3] - bounds[1]))
    k = max(4, min(8, 900 // max(1, x1 - x0)))
    margin = 44
    crop = art.flatten(art.upscale(canvas[y0:y1, x0:x1], k), art.BOARD_BG)
    head = Image.new("RGB", (crop.shape[1] + margin, crop.shape[0] + margin), art.BOARD_BG)
    head.paste(Image.fromarray(crop, "RGB"), (margin, margin))
    draw = ImageDraw.Draw(head)
    small = art.label_font(13)
    for x in range((x0 // 10 + 1) * 10 if x0 % 10 else x0, x1, 10):  # a grid every 10 cells, in canvas coordinates
        px = margin + (x - x0) * k
        draw.line([px, margin, px, head.height], fill=(190, 190, 190), width=1)
        draw.text((px + 2, margin - 18), str(x), fill=art.MUTED, font=small)
    for y in range((y0 // 10 + 1) * 10 if y0 % 10 else y0, y1, 10):
        py = margin + (y - y0) * k
        draw.line([margin, py, head.width, py], fill=(190, 190, 190), width=1)
        draw.text((4, py + 2), str(y), fill=art.MUTED, font=small)
    if face is not None:
        _outline(draw, face.eyes, k, (x0, y0), margin, "eyes")
        _outline(draw, face.mouth, k, (x0, y0), margin, "mouth")
        _neck_line(draw, face.neck, k, (x0, y0), margin, head.width)
    heads = []
    if states is not None:
        hx0, hy0, hx1, hy1 = x0, y0, x1, y1
        for label, mouth, eyes in (("Closed", "mouth_closed", "eyes_open"), ("Half open", "mouth_mid", "eyes_open"),
                                   ("Open", "mouth_open", "eyes_open"), ("Blink", "mouth_closed", "eyes_closed")):  # fmt: skip
            frame = canvas.copy()
            for name in (mouth, eyes):
                rect = states.rects[name]
                art.paste(frame, states.patches[name], rect.x, rect.y)
            heads.append((art.flatten(frame[hy0:hy1, hx0:hx1], art.BOARD_BG), label, ""))
    lines = board_notes(face, marked=marked, beard=beard) if face is not None else []
    if note:
        lines.append(note)
    if face is None:
        lines.append("Mark it by hand: read the boxes off this grid (one box round both eyes) and run")
        lines.append("uv run python -m kit.character faces --eyes x,y,w,h --mouth x,y,w,h")
    title = ("Face check: the eyes, the mouth and the neck line" + (" as marked" if marked else " as measured")
             + ", and the face states" if face is not None else "Face check: the face could not be measured")  # fmt: skip
    states_img = art.board(heads, title="", columns=4, scale=max(2, k // 2)) if heads else None
    measure_text = ImageDraw.Draw(head)
    widest = max([measure_text.textlength(title, font=art.label_font(26))]
                 + [measure_text.textlength(line, font=art.label_font(17)) for line in lines])  # fmt: skip
    width = int(max(head.width + (states_img.width + 24 if states_img else 0) + 48, widest + 48, 900))
    height = 70 + max(head.height, states_img.height if states_img else 0) + 30 * len(lines) + 40
    page = Image.new("RGB", (width, height), art.BOARD_BG)
    pen = ImageDraw.Draw(page)
    pen.text((24, 20), title, fill=art.INK, font=art.label_font(26))
    page.paste(head, (24, 64))
    if states_img is not None:
        page.paste(states_img, (24 + head.width + 24, 64))
    y = 64 + max(head.height, states_img.height if states_img else 0) + 16
    for line in lines:
        pen.text((24, y), line, fill=art.INK, font=art.label_font(17))
        y += 30
    return page


def make(folder: Path, *, eyes: Box | None = None, mouth: Box | None = None, neck: int | None = None,
         clear: bool = False, open_rows: int | None = None) -> Path:  # fmt: skip
    """Measure the face on the front view, apply the creator's marks, build the face states and the face board.

    It works as soon as there is a sheet, before it is approved, so the creator can check the face at that
    gate. `eyes` (one box round both eyes), `mouth` and `neck` (the first row under the head) are kept as marks
    in the character record and used by every later step; `clear` drops them. Returns the board. Raises
    FaceNotFound, with what to do, when there is no face to use."""
    state = store.load(folder)
    if not state.get("sheet", {}).get("round"):
        raise KitError("Draw the sheet first. The face is measured on its front view.")
    canvas = front_canvas(folder, state)
    marks = {} if clear else dict(state.get("face_marks") or {})
    for name, box in (("eyes", eyes), ("mouth", mouth)):
        if box is None:
            continue
        if box.x < 0 or box.y < 0 or box.x1 > canvas.shape[1] or box.y1 > canvas.shape[0]:
            raise KitError(f"The {name} box {box.text()} runs off the {art.CANVAS_W} x {art.CANVAS_H} canvas. "
                           "Read the numbers off the grid on the face board.")  # fmt: skip
        marks[name] = [box.x, box.y, box.w, box.h]
    if neck is not None:
        if not 0 < neck < canvas.shape[0]:
            raise KitError(f"Row {neck} is off the {art.CANVAS_W} x {art.CANVAS_H} canvas. Read the neck row off the "
                           "grid on the face board.")  # fmt: skip
        marks["neck"] = int(neck)
    if "eyes" in marks and "mouth" in marks and Box(*marks["mouth"]).y < Box(*marks["eyes"]).y1:
        raise KitError("The mouth box must sit below the eyes box. Check the rows on the face board.")

    details: dict[str, Any] = {}
    face, reason = measure(canvas, marks, details)
    if face is not None and marks.get("neck") is not None and marks["neck"] < face.mouth.y1 and (neck or mouth):
        raise KitError(f"The neck row must be below the mouth, whose last row is {face.mouth.y1 - 1}, so row "
                       f"{marks['neck']} is too high. Mark the first row under the chin (row {face.mouth.y1} or lower), "
                       "read off the face board.")  # fmt: skip
    state["face_marks"] = marks or None
    faces_state = state.setdefault("faces", {})
    if open_rows is not None:
        faces_state["open_rows"] = int(np.clip(open_rows, 1, 4))
    rows = int(faces_state.get("open_rows", 3))
    store.save(folder, state)

    out = folder / "faces"
    out.mkdir(parents=True, exist_ok=True)
    board_path = folder / BOARD
    if face is None:
        _board(canvas, None, None, marked=bool(marks), note=reason).save(board_path)
        raise FaceNotFound(f"{reason} {FIX}")
    states = face_states(canvas, face, open_rows=rows)
    meta = {"source": "the front view on the canvas", "open_rows": rows, "marked": bool(marks), "patches": {}}
    for name, patch in states.patches.items():
        art.save_rgba(patch, out / f"{name}.png")
        rect = states.rects[name]
        meta["patches"][name] = {"x": rect.x, "y": rect.y, "w": rect.w, "h": rect.h}
    (out / "faces.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    _board(canvas, face, states, marked=bool(marks), note=None, beard=details.get("beard")).save(board_path)
    state = store.load(folder)
    if state["sheet"].get("approved"):
        state["face"] = face.to_dict()
        state.setdefault("faces", {}).update(built=True, open_rows=rows)
        store.save(folder, state)
    return board_path
