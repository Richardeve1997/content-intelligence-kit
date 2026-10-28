"""The core poses: drawn in small batches, snapped onto the locked palette and stood on one canvas.

Every pose sheet the image tool draws starts with a copy of the approved front view on its left. That copy
sets the pixel size, so each pose is snapped at the same scale as the front view, onto the colours of the
approved sheet. Each pose then stands on the shared canvas (245 x 284 art pixels, feet on row 280).

The face. The mouth and blink patches are cut from the front view, so they only fit a pose whose face
is the front view's face. For poses that look straight at the viewer ("head" poses) the front view's whole
head is matched to the pose's head and pasted over it, so the face is the same in every pose and the
patches fit exactly. Poses whose expression is the point of the pose (thinking, mind blown, celebrate,
facepalm) keep their own face and get no mouth or blink. When a head cannot be matched, that pose keeps its
own face too, and the board says so.

The creator approves poses one by one. Rejected poses are drawn again, up to three to a picture, each
with the creator's note added to its direction, and the others stay as they are. A pose can also be snapped
again from the picture already drawn, which costs no image call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
from scipy import ndimage
from scipy.signal import fftconvolve

from kit.character import art, prompts, providers, snap, store
from kit.character.faces import FaceBox, skin_ramp
from kit.character.providers import Provider, WaitingForImage
from kit.config import KitError

POSE_NAMES = (
    "idle", "talking", "point_left", "point_right", "point_up", "thumbs_up", "shrug", "thinking", "mind_blown",
    "celebrate", "hold_card", "typing", "facepalm", "wave",
)  # fmt: skip
MOVE_NAMES = ("talking", "point_left", "point_right", "point_up")  # the poses that get in-between drawings
LABELS = {name: name.replace("_", " ").capitalize() for name in POSE_NAMES}
FACE_MODE = {name: "head" for name in POSE_NAMES} | {
    "idle": "canon", "thinking": "own", "mind_blown": "own", "celebrate": "own", "facepalm": "own",
}  # fmt: skip
HAND_RULE = {"talking": "up", "point_left": "left", "point_right": "right", "point_up": "up", "thumbs_up": "up",
             "wave": "up", "hold_card": "up", "celebrate": "up"}  # fmt: skip
BATCHES = (
    ("talking", "point_up", "wave", "thumbs_up"),
    ("point_left", "point_right", "thinking"),
    ("shrug", "mind_blown", "facepalm"),
    ("celebrate", "hold_card", "typing"),
)
REDRAW_GROUP = 3  # poses drawn again are batched in threes
MATCH_DE = 20.0  # the mean colour distance under which a pose's head counts as the front view's head
AIR_SLACK = 3  # a figure this close to the ground line is standing on it
HAND_MIN_CELLS = 12  # a patch of bare skin smaller than this is a speck or a bit of ear, not a hand
CARD_MIN = (12, 8)  # the clear face of a held card is at least this many cells wide and tall, so words fit on it
CARD_LIGHT = 70.0  # Lab lightness: a card is light enough for ink to read on
CARD_CHROMA = 6.0  # Lab chroma: a card is paper, nearly colourless; even very pale skin sits near 8, cream paper too
CARD_SAME = 0.5  # a light patch the idle drawing has in the same place (a light top) is not a card

__all__ = [
    "POSE_NAMES", "MOVE_NAMES", "LABELS", "FACE_MODE", "BATCHES", "Result", "Match", "match_face", "head_lock",
    "front_view", "fix_figure", "snap_strip", "hand_patches", "hand_anchor", "held_card", "outline_card", "make", "approve",
    "redraw", "board",
]  # fmt: skip


@dataclass
class Result:
    """What a pose or move step did: the names it finished, the pictures it is waiting for, and notes."""

    done: list[str] = field(default_factory=list)
    waiting: list[WaitingForImage] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    board: Path | None = None


# --- the canonical head --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Match:
    de: float  # mean Lab distance between the face and the pose at the best spot
    ox: int  # the front view's (x, y) lands at (x + ox, y + oy) on the pose canvas
    oy: int


def _correlate(image: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    return fftconvolve(image, kernel[::-1, ::-1], mode="valid")


def match_face(canvas: np.ndarray, idle: np.ndarray, face: FaceBox) -> Match | None:
    """Where the front view's face sits on a pose canvas, searched over the top half of the figure.

    Each spot is scored on the cells that are opaque in both the face and the pose (a redrawn head is often a
    cell narrower or wider at the edges), and only spots that cover at least 90% of the face count."""
    template = idle[face.y : face.y1, face.x : face.x1]
    mask = (template[..., 3] > 0).astype(np.float64)
    if mask.sum() < 4:
        return None
    t_lab = art.lab_of(template[..., :3])
    solid = (canvas[..., 3] > 0).astype(np.float64)
    a_lab = art.lab_of(canvas[..., :3]) * solid[..., None]
    th, tw = mask.shape
    if canvas.shape[0] < th or canvas.shape[1] < tw:
        return None
    t_sq = (t_lab**2).sum(2) * mask
    ssd = _correlate((a_lab**2).sum(2), mask) + _correlate(solid, t_sq)
    for c in range(3):
        ssd -= 2 * _correlate(a_lab[..., c], mask * t_lab[..., c])
    overlap = _correlate(solid, mask)
    usable = overlap >= 0.9 * mask.sum()
    box = art.content_box(canvas)
    if box is None:
        return None
    top, height = box[1], box[3] - box[1]
    rows = np.arange(usable.shape[0])[:, None]
    usable &= (rows >= top - 2) & (rows <= top + int(0.5 * height))
    if not usable.any():
        return None
    scores = np.where(usable, ssd / np.maximum(overlap, 1.0), np.inf)
    wy, wx = np.unravel_index(int(np.argmin(scores)), scores.shape)
    both = (mask > 0) & (solid[wy : wy + th, wx : wx + tw] > 0)
    window = a_lab[wy : wy + th, wx : wx + tw]
    de = float(np.linalg.norm(window - t_lab, axis=2)[both].mean())
    return Match(round(de, 2), int(wx - face.x), int(wy - face.y))


def head_lock(canvas: np.ndarray, idle: np.ndarray, face: FaceBox, ox: int, oy: int) -> None:
    """Paste the front view's head over the pose's own head, in place, with the face landing at (ox, oy).

    The pose's head is cleared first: the shape under the face that sits inside the head band, plus loose
    bits that never reach the neck, but only cells in the head's own colours (hair, skin, outline, eyes) and
    only within a few cells of where the new head goes. A sleeve or an arm beside the head keeps its cells.
    Small pieces of the old outline left afterwards are dropped."""
    height = art.BASELINE - face.top
    reach_cells = max(2, round(0.015 * height))
    head = idle[: face.neck].copy()
    head_colours = {tuple(int(v) for v in c) for c in head[..., :3][head[..., 3] > 0]}
    mask = np.zeros(canvas.shape[:2] + (4,), np.uint8)
    art.paste(mask, head, ox, oy)
    landing = mask[..., 3] > 0
    reach = ndimage.binary_dilation(landing, iterations=reach_cells)
    band = np.zeros(canvas.shape[:2], bool)
    band[: max(0, min(canvas.shape[0], oy + face.neck))] = True
    shapes, count = ndimage.label((canvas[..., 3] > 0) & band, structure=np.ones((3, 3)))
    cx, cy = face.centre[0] + ox, face.centre[1] + oy
    under = shapes[cy, cx] if 0 <= cy < canvas.shape[0] and 0 <= cx < canvas.shape[1] else 0
    if under == 0:
        window = shapes[max(0, face.y + oy) : face.y1 + oy, max(0, face.x + ox) : face.x1 + ox]
        window = window[window > 0]
        under = int(np.bincount(window).argmax()) if len(window) else 0
    neck_row = oy + face.neck - 1
    loose = []
    if 0 <= neck_row < canvas.shape[0]:
        reaching = set(np.unique(shapes[neck_row]))
        loose = [k for k in range(1, count + 1) if k != under and k not in reaching]
    clear = np.isin(shapes, [under] + loose) & reach & (shapes > 0) & art.colour_mask(canvas, head_colours)
    canvas[clear] = 0
    art.paste(canvas, head, ox, oy)
    pieces, n_pieces = ndimage.label(canvas[..., 3] > 0, structure=np.ones((3, 3)))
    if n_pieces > 1:
        sizes = ndimage.sum(np.ones_like(pieces), pieces, index=np.arange(1, n_pieces + 1))
        smallest_kept = max(8, round(20 * (height / 216) ** 2))
        for k in range(1, n_pieces + 1):
            if sizes[k - 1] < smallest_kept:
                canvas[pieces == k] = 0


def hand_patches(canvas: np.ndarray, skin: set, face: FaceBox, offset: Sequence[int] | None) -> tuple[np.ndarray, int]:
    """(labelled patches of bare skin away from the face, how many). The patch that holds the face and the neck
    is left out."""
    is_skin = art.colour_mask(canvas, skin)
    if offset is not None:
        ox, oy = offset
    else:  # an own-face pose: line the front view's head up with the top of this figure
        box = art.content_box(canvas)
        ox, oy = 0, (box[1] - face.top) if box else 0
    patches, count = ndimage.label(is_skin)
    zone = np.zeros(is_skin.shape, bool)  # the face and ears, down to just under the neck
    zone[max(0, face.y + oy - 2) : max(0, face.neck + oy + 2), max(0, face.x + ox - 2) : max(0, face.x1 + ox + 2)] = True
    if count:
        sizes = ndimage.sum(np.ones_like(patches), patches, index=np.arange(1, count + 1))
        inside = ndimage.sum(zone, patches, index=np.arange(1, count + 1))
        for k in np.nonzero(inside >= 0.5 * sizes)[0] + 1:  # mostly face: a cheek, an ear, the neck
            patches[patches == k] = 0
    return patches, count


def hand_anchor(canvas: np.ndarray, rule: str | None, skin: set, face: FaceBox, offset: Sequence[int] | None,
                min_cells: int = HAND_MIN_CELLS) -> list[int] | None:  # fmt: skip
    """The fingertip or raised hand of a pose: its skin cell furthest left, right or up.

    Hands are found by colour (the shades the face is painted with). The patch that holds the face is left
    out, and so is any patch smaller than `min_cells` (a bit of ear or neck between two outlines)."""
    if rule is None or not skin:
        return None
    patches, count = hand_patches(canvas, skin, face, offset)
    if count:
        sizes = ndimage.sum(np.ones_like(patches), patches, index=np.arange(1, count + 1))
        for k in np.nonzero(sizes < min_cells)[0] + 1:
            patches[patches == k] = 0
    ys, xs = np.nonzero(patches)
    if not len(ys):
        return None
    pick = {"left": int(np.argmin(xs)), "right": int(np.argmax(xs)), "up": int(np.argmin(ys))}[rule]
    return [int(xs[pick]), int(ys[pick])]


def _largest_rectangle(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    """The largest rectangle of true cells in a grid, as (x, y, w, h), or None when there is none."""
    rows, cols = mask.shape
    heights = np.zeros(cols, int)
    best = (0, 0, 0, 0, 0)  # area, x, y, w, h
    for y in range(rows):
        heights = np.where(mask[y], heights + 1, 0)
        stack: list[int] = []  # columns whose heights rise
        for x in range(cols + 1):
            current = int(heights[x]) if x < cols else 0
            while stack and int(heights[stack[-1]]) >= current:
                top = stack.pop()
                height = int(heights[top])
                left = stack[-1] + 1 if stack else 0
                if height * (x - left) > best[0]:
                    best = (height * (x - left), left, y - height + 1, x - left, height)
            stack.append(x)
    return None if best[0] == 0 else best[1:]


def held_card(canvas: np.ndarray, idle: np.ndarray, face: FaceBox, offset: Sequence[int] | None) -> list[int] | None:
    """The clear face of the card a pose holds up, as [x, y, w, h] in canvas cells, or None when the drawing holds
    no card worth writing on.

    A card is a patch of one paper-like colour (light, CARD_LIGHT, and nearly colourless, CARD_CHROMA, so a raised
    hand or any skin never counts) below the head that the idle drawing does not have in the same place (the place
    is read through the pose's offset from the front view), so a light top is never taken for one. Its clear face is
    the largest rectangle inside the patch, which leaves out the thumbs holding it where they cut into its edges.
    The biggest clear face wins, when it is at least CARD_MIN cells wide and tall."""
    solid = canvas[..., 3] > 0
    if not solid.any():
        return None
    colours, inverse = np.unique(canvas[..., :3].reshape(-1, 3), axis=0, return_inverse=True)
    grid = np.where(solid, inverse.reshape(canvas.shape[:2]), -1)
    labs = art.lab_of(colours.astype(np.float64))
    if offset is not None:
        ox, oy = int(offset[0]), int(offset[1])
    else:  # an own-face pose: line the front view's head up with the top of this figure
        box = art.content_box(canvas)
        ox, oy = 0, (box[1] - face.top) if box else 0
    head_end = max(0, face.neck + oy)
    best, best_area = None, 0
    paper = (labs[:, 0] >= CARD_LIGHT) & (np.hypot(labs[:, 1], labs[:, 2]) <= CARD_CHROMA)
    for index in np.nonzero(paper)[0]:
        cells = grid == index
        cells[:head_end] = False
        if cells.sum() < CARD_MIN[0] * CARD_MIN[1]:
            continue
        patches, count = ndimage.label(cells)
        for k in range(1, count + 1):
            patch = patches == k
            if patch.sum() < CARD_MIN[0] * CARD_MIN[1]:
                continue
            found = _largest_rectangle(patch)
            if found is None:
                continue
            x, y, w, h = found
            if w < CARD_MIN[0] or h < CARD_MIN[1] or w * h <= best_area:
                continue
            if _share_of(idle, colours[index], x - ox, y - oy, w, h) >= CARD_SAME:
                continue
            best, best_area = [int(x), int(y), int(w), int(h)], w * h
    return best


def _share_of(idle: np.ndarray, colour: np.ndarray, x: int, y: int, w: int, h: int) -> float:
    """The share of the w x h window at (x, y) on the idle drawing that is `colour`; outside the drawing counts as not."""
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(idle.shape[1], x + w), min(idle.shape[0], y + h)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    there = idle[y0:y1, x0:x1]
    same = (there[..., :3] == colour).all(2) & (there[..., 3] > 0)
    return float(same.sum()) / float(w * h)


def outline_card(tile: np.ndarray, card: Sequence[int], origin: tuple[int, int]) -> None:
    """Draw a one-cell outline in ink just inside the clear face of a card on a board tile, in place. `origin` is the
    canvas cell at the tile's top-left corner."""
    x, y, w, h = (int(v) for v in card)
    x0, y0 = x - origin[0], y - origin[1]
    x1, y1 = x0 + w - 1, y0 + h - 1
    rows, cols = tile.shape[:2]
    ink = np.array(art.INK, np.uint8)
    for row in (y0, y1):
        if 0 <= row < rows:
            tile[row, max(0, x0) : min(cols, x1 + 1), :3] = ink
    for col in (x0, x1):
        if 0 <= col < cols:
            tile[max(0, y0) : min(rows, y1 + 1), col, :3] = ink


# --- drawing and fixing a pose ----------------------------------------------------------------------------------------


def _palette(state: dict[str, Any]) -> list[tuple[int, int, int]]:
    return [art.from_hex(c) for c in state["palette"]]


def front_view(folder: Path, state: dict[str, Any]) -> tuple[np.ndarray, FaceBox, set]:
    """(the idle pose on the canvas, its measured face, the skin shades its face is painted with)."""
    idle = art.load_rgba(folder / "poses" / "idle.png")
    face = FaceBox.from_dict(state["face"])
    return idle, face, skin_ramp(idle, face)


def fix_figure(figure: np.ndarray, *, name: str, air: int, idle: np.ndarray, face: FaceBox, skin: set) -> tuple[np.ndarray, dict]:
    """Stand one snapped pose on the canvas and give it the front view's head when it should have it."""
    canvas, _, clipped = art.place(figure, air=air)
    record: dict[str, Any] = {"face": FACE_MODE[name], "offset": None, "match": None, "air": int(air),
                              "clipped": clipped or None, "note": None}  # fmt: skip
    if FACE_MODE[name] == "head":
        found = match_face(canvas, idle, face)
        if found is not None and found.de <= MATCH_DE:
            head_lock(canvas, idle, face, found.ox, found.oy)
            record.update(offset=[found.ox, found.oy], match=found.de)
        else:
            record["face"] = "own"
            record["note"] = "kept its own face, because its head did not match the front view's closely enough"
    art.despur(canvas)
    record["hand"] = hand_anchor(canvas, HAND_RULE.get(name), skin, face, record["offset"])
    return canvas, record


def snap_strip(raw: Path, state: dict[str, Any], *, expect: int, reference_height: int) -> list[snap.Figure]:
    """Snap a pose sheet or move strip whose first figure is a copy of a known sprite. That copy's height in
    the picture, against its known height in art pixels, sets the pixel size, so every figure lands at the
    approved scale."""
    rgb, opaque = snap.load_picture(raw)
    slots = snap.split_figures(rgb, expect=expect, opaque=opaque)
    if len(slots) != expect:
        raise snap.SnapError(
            f"{raw.name} shows {len(slots)} figures where {expect} were asked for (the size reference first). "
            "Draw it again."
        )
    pitch = slots[0].height / reference_height
    phx, phy = snap.phase_for(rgb, pitch)
    palette = _palette(state)
    snapped = snap.snap_array(rgb, pitch, phx, phy, colours=len(palette), palette=palette, opaque=opaque)
    return snap.figures(snapped, slots)


def _air(floor: int, figure: snap.Figure) -> int:
    air = floor - figure.bottom
    return 0 if abs(air) <= AIR_SLACK else max(0, air)


def _batches(names: Sequence[str], state: dict[str, Any]) -> list[tuple[str, list[str]]]:
    """(raw picture stem, poses) for each request. First drawings follow BATCHES; redraws go in threes."""
    first = [n for n in names if not state["poses"].get(n, {}).get("round")]
    again = [n for n in names if n not in first]
    requests = []
    for number, batch in enumerate(BATCHES, 1):
        chosen = [n for n in batch if n in first]
        if chosen:
            requests.append((f"poses-{number}-r1", chosen))
    for start in range(0, len(again), REDRAW_GROUP):
        group = again[start : start + REDRAW_GROUP]
        round_ = max(state["poses"][n]["round"] for n in group) + 1
        requests.append((f"pose-{'-'.join(group)}-r{round_}", group))
    return requests


def _check_names(names: Iterable[str]) -> list[str]:
    wanted = list(dict.fromkeys(names))
    unknown = [n for n in wanted if n not in POSE_NAMES[1:]]
    if unknown:
        raise KitError(
            f"{', '.join(unknown)} is not one of the poses. The idle pose comes from the sheet; the others are: "
            f"{', '.join(POSE_NAMES[1:])}."
        )
    return wanted


def _drawn_batch(state: dict[str, Any], name: str) -> list[str]:
    """The poses drawn in the same picture as `name`, in the order they were asked for."""
    entry = state["poses"][name]
    if entry.get("batch"):
        return list(entry["batch"])
    stem = Path(entry["raw"]).stem
    if stem.startswith("pose-"):
        return stem[len("pose-") :].rsplit("-r", 1)[0].split("-")
    number = int(stem.split("-")[1])
    return [n for n in BATCHES[number - 1] if state["poses"].get(n, {}).get("raw") == entry["raw"]]


def _process(
    folder: Path, stem: str, batch: list[str], notes: dict[str, str | None], result: Result, only: set[str] | None = None
) -> None:
    """Snap one pose picture and stand each of its poses on the canvas (or only the poses in `only`).
    Problems become notes, not failures."""
    state = store.load(folder)
    idle, face, skin = front_view(folder, state)
    raw = folder / "raw" / f"{stem}.png"
    round_ = int(stem.rsplit("-r", 1)[1])
    try:
        figs = snap_strip(raw, state, expect=len(batch) + 1, reference_height=int(state["sheet"]["height"]))
    except snap.SnapError as problem:
        result.notes.append(str(problem))
        for name in batch:
            entry = state["poses"].setdefault(name, {})
            entry.update(round=round_, raw=f"raw/{stem}.png", batch=batch, approved=False, redraw=True)
        store.save(folder, state)
        return
    floor = figs[0].bottom
    for figure, name in zip(figs[1:], batch):
        if only is not None and name not in only:
            continue
        canvas, record = fix_figure(figure.rgba, name=name, air=_air(floor, figure), idle=idle, face=face, skin=skin)
        art.save_rgba(canvas, folder / "poses" / f"{name}.png")
        art.save_reference(art.crop(canvas), folder / "poses" / "refs" / f"{name}-ref.png")
        entry = {"round": round_, "raw": f"raw/{stem}.png", "batch": batch, "approved": False, **record}
        if notes.get(name):
            entry["last_note"] = notes[name]
        state["poses"][name] = entry
        result.done.append(name)
        if record["note"]:
            result.notes.append(f"{LABELS[name]} {record['note']}.")
        if record["clipped"]:
            cut = ", ".join(f"{n} cells at the {side}" for side, n in record["clipped"].items())
            result.notes.append(f"{LABELS[name]} did not fit the canvas and lost {cut}. Draw it again if that shows.")
    store.save(folder, state)


def make(folder: Path, tool: Provider | None, names: Iterable[str] | None = None, *, redraw: bool = True) -> Result:
    """Draw the poses that need drawing: those never drawn and those marked for a redraw, or `names`.

    With `redraw=False` nothing is drawn: the pictures already drawn for `names` (or for every drawn pose)
    are snapped and fixed again, with no image call, and those poses wait for approval again."""
    state = store.load(folder)
    if not state.get("sheet", {}).get("approved"):
        raise KitError("Approve the character sheet first. The poses are drawn from it.")
    result = Result()
    if not redraw:
        drawn = [n for n in POSE_NAMES[1:] if state["poses"].get(n, {}).get("raw")]
        wanted = _check_names(names) if names is not None else drawn
        missing = [n for n in wanted if n not in drawn]
        if missing:
            raise KitError(f"These poses have no picture to snap again yet: {', '.join(missing)}.")
        requests: dict[str, list[str]] = {}
        for name in wanted:
            requests.setdefault(Path(state["poses"][name]["raw"]).stem, _drawn_batch(state, name))
        for stem, batch in requests.items():
            notes = {n: state["poses"].get(n, {}).get("last_note") for n in batch}
            _process(folder, stem, batch, notes, result, only=set(wanted))
        result.board = board(folder)
        return result
    if names is None:
        wanted = [n for n in POSE_NAMES[1:] if not state["poses"].get(n, {}).get("round") or state["poses"][n].get("redraw")]
    else:
        wanted = _check_names(names)
    hint = prompts.palette_hint(state["palette"])
    refs = [folder / "sheet" / "sheet-ref.png", folder / "sheet" / "front-ref.png"]
    for stem, batch in _batches(wanted, state):
        notes = {n: state["poses"].get(n, {}).get("redraw_note") for n in batch}
        prompt = prompts.render("poses", description=state["description"], outfit=state["outfit"], palette_hint=hint)
        prompt += prompts.pose_list(batch, {k: v for k, v in notes.items() if v})
        try:
            providers.draw(folder, tool, prompt, refs, folder / "raw" / f"{stem}.png")
        except WaitingForImage as waiting:
            result.waiting.append(waiting)
            continue
        _process(folder, stem, batch, notes, result)
    result.board = board(folder)
    return result


def approve(folder: Path, names: Iterable[str]) -> list[str]:
    """Mark poses as approved. "all" approves every pose that has been drawn."""
    state = store.load(folder)
    asked = list(names)
    drawn = [n for n in POSE_NAMES if state["poses"].get(n, {}).get("round") and (folder / "poses" / f"{n}.png").is_file()]
    chosen = drawn if "all" in asked else asked
    missing = [n for n in chosen if n not in drawn]
    if missing:
        raise KitError(f"These poses have not been drawn yet, so they cannot be approved: {', '.join(missing)}.")
    for name in chosen:
        entry = state["poses"][name]
        entry["approved"] = True
        entry.pop("redraw", None)
        entry.pop("redraw_note", None)
    store.save(folder, state)
    board(folder)
    return chosen


def redraw(folder: Path, name: str, note: str | None = None) -> None:
    """Mark one pose to be drawn again on its own, with the creator's note added to its direction."""
    state = store.load(folder)
    if name == "idle":
        sheet = state.get("sheet", {})
        if not sheet.get("round"):
            raise KitError("The idle pose is the front view of the character sheet, and no sheet is drawn yet. Draw "
                           "it with: uv run python -m kit.character sheet")  # fmt: skip
        if not sheet.get("approved"):
            raise KitError(
                "The idle pose is the front view of the sheet, and the sheet is not approved yet, so draw the sheet "
                "again instead: put the change into the description or the outfit with uv run python -m "
                "kit.character describe, then run uv run python -m kit.character sheet"
            )
        raise KitError(
            "The idle pose is the front view of the approved sheet, and an approved sheet is not drawn again. The only "
            "way to change it is a new character: move the me/character folder somewhere else and run start again. "
            "That draws a new sheet and all 13 other poses, so it costs the whole character's image calls again "
            "(about 9 when nothing needs drawing twice)."
        )
    if name not in POSE_NAMES or not state["poses"].get(name, {}).get("round"):
        raise KitError(f"{name} has not been drawn yet, so there is nothing to draw again.")
    entry = state["poses"][name]
    entry.update(approved=False, redraw=True)
    if note and note.strip():
        entry["redraw_note"] = " ".join(note.split())
    store.save(folder, state)


def board(folder: Path) -> Path:
    """The pose board: every pose drawn so far on one page, with what it is waiting for."""
    state = store.load(folder)
    drawn = [n for n in POSE_NAMES if (folder / "poses" / f"{n}.png").is_file()]
    canvases = {n: art.load_rgba(folder / "poses" / f"{n}.png") for n in drawn}
    boxes = [art.content_box(c) for c in canvases.values()]
    boxes = [b for b in boxes if b]
    x0 = max(0, min(b[0] for b in boxes) - 4) if boxes else 0
    y0 = max(0, min(b[1] for b in boxes) - 4) if boxes else 0
    x1 = min(art.CANVAS_W, max(b[2] for b in boxes) + 4) if boxes else art.CANVAS_W
    cells = []
    for name in drawn:
        entry = state["poses"].get(name, {})
        if entry.get("approved"):
            status = "approved"
        elif entry.get("redraw"):
            status = "to draw again"
        else:
            status = "waiting for your look"
        extra = []
        if entry.get("face") == "own" and FACE_MODE[name] == "head":
            extra.append("own face")
        if entry.get("clipped"):
            extra.append("cut off")
        tile = art.flatten(canvases[name][y0 : art.CANVAS_H, x0:x1], art.BOARD_BG)
        if name == "hold_card" and state.get("face") and "idle" in canvases:
            # the clear face of the card it holds up, outlined, so a wrong measure is seen before any render
            card = held_card(canvases[name], canvases["idle"], FaceBox.from_dict(state["face"]), entry.get("offset"))
            if card:
                outline_card(tile, card, (x0, y0))
                extra.append(f"card {card[2]} x {card[3]} cells")
            else:
                extra.append("no plain card found")
        note = status + (f", {', '.join(extra)}" if extra else "")
        cells.append((tile, LABELS[name], note))
    path = folder / "poses" / "board.png"
    if cells:
        art.board(cells, title="Poses: say which to keep and which to draw again", columns=5, scale=2).save(path)
    return path
