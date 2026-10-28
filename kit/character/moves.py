"""In-between drawings for the four moves that need them: talking and the three points.

A point plays the idle pose, two in-betweens, then the approved key pose, holds it, and plays back to idle.
Talking is a loop of four drawings: the approved talking pose and three small changes of its raised hand.

The end drawings are the approved sprites themselves, never the image tool's copies of them. Each
in-between gets the front view's head (so the face cannot drift and the mouth patches fit), stands on the
same feet, and below the lowest hand its legs are copied from the move's first drawing, so the feet stay
planted and the trousers do not shimmer from frame to frame.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from PIL import Image
from scipy import ndimage

from kit.character import art, prompts, providers, snap, store
from kit.character.poses import HAND_MIN_CELLS, MOVE_NAMES, Result, front_view, hand_patches, fix_figure, snap_strip
from kit.character.providers import Provider, WaitingForImage
from kit.config import KitError

FPS = 24
POINT_SEQUENCE = [[0, 8], [1, 2], [2, 2], [3, 24], [2, 2], [1, 2], [0, 8]]  # [drawing, ticks at 24 fps]
POINT_HOLD = 3  # the entry of POINT_SEQUENCE that holds on the key pose
TALK_SEQUENCE = [[0, 4], [1, 4], [2, 4], [3, 4]]

__all__ = ["MOVE_NAMES", "FPS", "POINT_SEQUENCE", "POINT_HOLD", "TALK_SEQUENCE", "make", "approve", "redraw"]


def _lowest_hand(canvas: np.ndarray, skin: set, face, offset) -> int | None:
    """The lowest row of the hands: the bottom of the lowest patch of bare skin away from the face that is big
    enough to be a hand. Skin wholly in the bottom fifth of the figure is ankles or feet, not hands."""
    patches, count = hand_patches(canvas, skin, face, offset)
    box = art.content_box(canvas)
    if not count or box is None:
        return None
    knees = box[1] + int(0.8 * (box[3] - box[1]))
    lowest = None
    for k, where in enumerate(ndimage.find_objects(patches), 1):
        if where is None or where[0].start >= knees or int((patches[where] == k).sum()) < HAND_MIN_CELLS:
            continue
        lowest = max(lowest or 0, where[0].stop - 1)
    return lowest


def _needs_drawing(state: dict[str, Any], move: str) -> bool:
    entry = state["moves"].get(move, {})
    key_round = state["poses"].get(move, {}).get("round")
    return not entry.get("built") or entry.get("redraw") or entry.get("key_round") != key_round


def _check(names: Iterable[str]) -> list[str]:
    wanted = list(dict.fromkeys(names))
    unknown = [m for m in wanted if m not in MOVE_NAMES]
    if unknown:
        raise KitError(f"{', '.join(unknown)} has no in-betweens. The moves are: {', '.join(MOVE_NAMES)}.")
    return wanted


def make(folder: Path, tool: Provider | None, names: Iterable[str] | None = None, *, redraw: bool = True) -> Result:
    """Draw the in-betweens that are missing, marked for a redraw, or out of date with their key pose.

    With `redraw=False` nothing is drawn: the strips already drawn for `names` (or for every built move) are
    snapped and fixed again, with no image call."""
    state = store.load(folder)
    not_ready = [m for m in ("idle", *MOVE_NAMES) if not state["poses"].get(m, {}).get("approved")]
    if not_ready:
        raise KitError(f"Approve these poses first, because their moves are drawn from them: {', '.join(not_ready)}.")
    result = Result()
    if not redraw:
        built = [m for m in MOVE_NAMES if state["moves"].get(m, {}).get("raw")]
        wanted = _check(names) if names is not None else built
        missing = [m for m in wanted if m not in built]
        if missing:
            raise KitError(f"These moves have no strip to snap again yet: {', '.join(missing)}.")
        for move in wanted:
            _process(folder, move, Path(state["moves"][move]["raw"]).stem, result)
        return result
    wanted = _check(names) if names is not None else [m for m in MOVE_NAMES if _needs_drawing(state, m)]
    hint = prompts.palette_hint(state["palette"])
    directions = prompts.move_directions()
    for move in wanted:
        round_ = int(state["moves"].get(move, {}).get("round", 0)) + 1
        stem = f"move-{move}-r{round_}"
        template = "loop" if move == "talking" else "inbetweens"
        refs = [folder / "poses" / "refs" / f"{move}-ref.png"]
        if move != "talking":
            refs.insert(0, folder / "poses" / "refs" / "idle-ref.png")
        prompt = prompts.render(template, description=state["description"], outfit=state["outfit"], palette_hint=hint)
        note = state["moves"].get(move, {}).get("redraw_note")
        prompt += directions[move] + (f" {note}" if note else "") + "\n"
        try:
            providers.draw(folder, tool, prompt, refs, folder / "raw" / f"{stem}.png")
        except WaitingForImage as waiting:
            result.waiting.append(waiting)
            continue
        _process(folder, move, stem, result)
    return result


def _process(folder: Path, move: str, stem: str, result: Result) -> None:
    """Snap one move strip and turn it into the move's frames, its record and a preview."""
    state = store.load(folder)
    idle, face, skin = front_view(folder, state)
    round_ = int(stem.rsplit("-r", 1)[1])
    raw = folder / "raw" / f"{stem}.png"
    key = art.load_rgba(folder / "poses" / f"{move}.png")
    start = key if move == "talking" else idle
    start_box = art.content_box(start)
    try:
        figs = snap_strip(raw, state, expect=4, reference_height=start_box[3] - start_box[1])
    except snap.SnapError as problem:
        result.notes.append(str(problem))
        state["moves"].setdefault(move, {}).update(round=round_, raw=f"raw/{stem}.png", built=False, redraw=True)
        store.save(folder, state)
        return

    frames, info = [start.copy()], [_frame_info(state, move if move == "talking" else "idle")]
    for figure in figs[1:3] if move != "talking" else figs[1:4]:
        canvas, record = fix_figure(figure.rgba, name=move, air=0, idle=idle, face=face, skin=skin)
        frames.append(canvas)
        info.append({"offset": record["offset"], "hand": record["hand"], "face": record["face"]})
    if move != "talking":
        frames.append(key.copy())
        info.append(_frame_info(state, move))

    # leg lock: below every frame's lowest hand, each in-between takes the first drawing's legs
    lowest = [r for r in (_lowest_hand(f, skin, face, i.get("offset")) for f, i in zip(frames, info)) if r is not None]
    floor_row = start_box[1] + int(0.72 * (start_box[3] - start_box[1]))
    leg_row = max([floor_row] + [r + 3 for r in lowest])
    for canvas, entry in zip(frames[1:-1] if move != "talking" else frames[1:], info[1:]):
        if leg_row < art.CANVAS_H:
            canvas[leg_row:] = start[leg_row:]
        entry["legs_from_row"] = leg_row
        art.despur(canvas)
    out = folder / "moves" / move
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("frame_*.png"):
        old.unlink()
    for number, canvas in enumerate(frames, 1):
        art.save_rgba(canvas, out / f"frame_{number:02d}.png")
    sequence = TALK_SEQUENCE if move == "talking" else POINT_SEQUENCE
    doc = {"move": move, "kind": "loop" if move == "talking" else "oneshot", "fps": FPS, "frames": len(frames),
           "sequence": sequence, "hold": None if move == "talking" else POINT_HOLD, "frame_info": info}  # fmt: skip
    (out / f"{move}.json").write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    _preview(frames, sequence, out / f"{move}.gif")
    state["moves"][move] = {"round": round_, "raw": f"raw/{stem}.png", "built": True, "approved": False,
                            "key_round": state["poses"][move]["round"]}  # fmt: skip
    store.save(folder, state)
    result.done.append(move)


def _frame_info(state: dict[str, Any], pose: str) -> dict[str, Any]:
    """The anchors of an approved pose, for a move frame that is that pose itself."""
    entry = state["poses"][pose]
    return {"offset": entry.get("offset"), "hand": entry.get("hand"), "face": entry.get("face")}


def _preview(frames: list[np.ndarray], sequence: list[list[int]], path: Path) -> None:
    """An animated GIF of the move at its own timing, three screen pixels per art pixel, on light grey."""
    box = None
    for frame in frames:
        b = art.content_box(frame)
        if b:
            box = b if box is None else (min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3]))
    x0, y0, x1, y1 = (max(0, box[0] - 6), max(0, box[1] - 6), box[2] + 6, box[3] + 6) if box else (0, 0, art.CANVAS_W, art.CANVAS_H)
    pictures = [Image.fromarray(art.flatten(art.upscale(f[y0:y1, x0:x1], 3), art.BOARD_BG), "RGB") for f in frames]
    shown = [pictures[frame] for frame, _ in sequence]
    durations = [max(20, round(ticks * 1000 / FPS)) for _, ticks in sequence]
    shown[0].save(path, save_all=True, append_images=shown[1:], duration=durations, loop=0, disposal=1)


def approve(folder: Path, names: Iterable[str]) -> list[str]:
    state = store.load(folder)
    asked = list(names)
    built = [m for m in MOVE_NAMES if state["moves"].get(m, {}).get("built")]
    chosen = built if "all" in asked else asked
    missing = [m for m in chosen if m not in built]
    if missing:
        raise KitError(f"These moves have not been drawn yet: {', '.join(missing)}.")
    for move in chosen:
        state["moves"][move]["approved"] = True
        state["moves"][move].pop("redraw", None)
    store.save(folder, state)
    return chosen


def redraw(folder: Path, move: str, note: str | None = None) -> None:
    state = store.load(folder)
    if move not in MOVE_NAMES or not state["moves"].get(move, {}).get("built"):
        raise KitError(f"{move} has no in-betweens drawn yet.")
    entry = state["moves"][move]
    entry.update(approved=False, redraw=True)
    if note and note.strip():
        entry["redraw_note"] = " ".join(note.split())
    store.save(folder, state)
