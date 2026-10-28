"""The character sheet: the first drawing, which every later drawing copies.

The image tool draws three full-body views side by side: front, side and back. The front view is the
leftmost figure. The sheet is snapped onto a pixel grid chosen so the front view stands about 216 art
pixels tall (the shared canvas is 284 tall, which leaves room above the head for raised arms and jumps).
When the picture has a clear pixel grid of its own, the snap follows that grid or an even split of it,
so the pixels stay square.

On approval the sheet's colours become the locked palette, the front view becomes the idle pose on the
shared canvas, and the face is measured on it. Until then the creator can ask for changes: the
description is updated and the sheet is drawn again from the last one.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from PIL import Image, ImageDraw

from kit.character import art, faces, prompts, providers, snap, store
from kit.character.providers import Provider
from kit.config import KitError

TARGET_HEIGHT = 216  # art pixels from the top of the head to the soles
HEIGHT_RANGE = (192, 232)  # a grid split that lands in this range is used instead of the exact target
DEFAULT_COLOURS = 32
PHOTOS = (3, 5)

__all__ = ["TARGET_HEIGHT", "DEFAULT_COLOURS", "start", "describe", "make", "approve"]


def _new_state(description: str, outfit: str, photos: list[str], colours: int, tool: str | None) -> dict[str, Any]:
    return {
        "version": store.VERSION,
        "description": description,
        "outfit": outfit,
        "photos": photos,
        "colours": colours,
        "tool": tool,
        "palette": None,
        "face": None,
        "sheet": {},
        "poses": {},
        "faces": {},
        "moves": {},
        "calls": 0,
        "call_log": [],
    }


def start(
    folder: Path,
    *,
    description: str,
    outfit: str,
    photos: Sequence[Path] = (),
    colours: int = DEFAULT_COLOURS,
    tool: str | None = None,
) -> dict[str, Any]:
    """Begin a character in `folder` from the creator's words and, optionally, 3 to 5 photos of themselves.

    The photos are copied into the folder's photos/. The only other copies are the manual tool's, in inbox/
    for the creator to attach, and it deletes them once the picture is back. A character whose sheet is
    already approved is never overwritten: move the folder aside to start a new one."""
    description, outfit = " ".join(str(description).split()), " ".join(str(outfit).split())
    if not description:
        raise KitError("Describe the character in a sentence or two first: who they are and how they look.")
    if not outfit:
        raise KitError("Say what the character wears, for example: a navy hoodie, jeans and white sneakers.")
    if not 8 <= int(colours) <= 64:
        raise KitError("The palette can have between 8 and 64 colours.")
    if tool is not None and tool not in providers.NAMES:
        raise KitError(f"{tool!r} is not an image tool this kit knows. Use one of: {', '.join(providers.NAMES)}.")
    photos = [Path(p) for p in photos]
    if photos and not PHOTOS[0] <= len(photos) <= PHOTOS[1]:
        raise KitError(f"Give {PHOTOS[0]} to {PHOTOS[1]} photos of yourself, or none and a description instead.")
    for photo in photos:
        try:
            with Image.open(photo) as image:
                image.verify()
        except (OSError, SyntaxError):
            raise KitError(f"{photo.name} is not a photo this kit can open.") from None
    if store.exists(folder):
        old = store.load(folder)
        if old.get("sheet", {}).get("approved"):
            raise KitError(
                "This character's sheet is already approved. To start a new character, move the character folder "
                "somewhere else first, so nothing of the old one is lost."
            )
    folder.mkdir(parents=True, exist_ok=True)
    kept = []
    if photos:
        # copy into a fresh folder first and swap it in afterwards, so photos given from the old folder (a
        # second start with the same photos) are read before anything there is replaced
        target = folder / "photos"
        fresh = folder / ".photos-new"
        stale = folder / ".photos-old"
        for leftover in (fresh, stale):
            if leftover.exists():
                shutil.rmtree(leftover)
        fresh.mkdir()
        for number, photo in enumerate(photos, 1):
            name = f"photo-{number}{photo.suffix.lower()}"
            shutil.copyfile(photo, fresh / name)
            kept.append(f"photos/{name}")
        if target.exists():
            target.rename(stale)
        fresh.rename(target)
        if stale.exists():
            shutil.rmtree(stale)
    state = _new_state(description, outfit, kept, int(colours), tool)
    if store.exists(folder):
        old = store.load(folder)
        state["sheet"] = old.get("sheet", {})
        state["calls"], state["call_log"] = old.get("calls", 0), old.get("call_log", [])
    store.save(folder, state)
    return state


def describe(folder: Path, *, description: str | None = None, outfit: str | None = None) -> dict[str, Any]:
    """Change the words the sheet is drawn from, before a redraw. Only allowed until the sheet is approved."""
    state = store.load(folder)
    if state["sheet"].get("approved"):
        raise KitError("The sheet is approved, so its description is fixed. Start a new character to change it.")
    if description is not None and description.strip():
        state["description"] = " ".join(description.split())
    if outfit is not None and outfit.strip():
        state["outfit"] = " ".join(outfit.split())
    store.save(folder, state)
    return state


def _choose_pitch(rgb: np.ndarray, front_height: int) -> float:
    """Raw pixels per art pixel for the sheet: an even split of the picture's own grid that puts the front
    view inside HEIGHT_RANGE, or else exactly TARGET_HEIGHT."""
    exact = front_height / TARGET_HEIGHT
    try:
        grid = snap.detect_grid(rgb, 2.0, 12.0)
    except ValueError:
        return exact
    options = [grid.pitch / k for k in (1, 2, 3, 4)]
    fitting = [p for p in options if p >= 1.0 and HEIGHT_RANGE[0] <= front_height / p <= HEIGHT_RANGE[1]]
    return min(fitting, key=lambda p: abs(front_height / p - TARGET_HEIGHT)) if fitting else exact


def _sheet_board(raw: Path, sheet_rgba: np.ndarray, front: np.ndarray, palette: list[str], round_: int) -> Image.Image:
    scale = 3
    margin, gap, title_h = 24, 24, 64
    tall = front.shape[0] * scale
    original = Image.fromarray(np.round(snap.load_rgb(raw) * 255).astype(np.uint8), "RGB")
    original.thumbnail((10_000, tall))
    light = Image.fromarray(art.flatten(art.upscale(front, scale), art.BOARD_BG), "RGB")
    dark = Image.fromarray(art.flatten(art.upscale(front, scale), art.DARK_BG), "RGB")
    sheet_scale = max(1, min(2, (1800 // max(1, sheet_rgba.shape[1]))))
    whole = Image.fromarray(art.flatten(art.upscale(sheet_rgba, sheet_scale), art.BOARD_BG), "RGB")
    swatch, per_row = 36, 16
    rows = (len(palette) + per_row - 1) // per_row
    top_w = original.width + light.width + dark.width + 2 * gap
    width = margin * 2 + max(top_w, whole.width, per_row * (swatch + 6))
    height = margin * 2 + title_h + tall + gap + whole.height + gap + 40 + rows * (swatch + 6)
    page = Image.new("RGB", (width, height), art.BOARD_BG)
    draw = ImageDraw.Draw(page)
    draw.text((margin, margin), f"Character sheet, round {round_}: the picture, the front view snapped to pixels "
                                "(light and dark), and the whole sheet", fill=art.INK, font=art.label_font(22))  # fmt: skip
    y = margin + title_h
    x = margin
    for part in (original, light, dark):
        page.paste(part, (x, y))
        x += part.width + gap
    y += tall + gap
    page.paste(whole, (margin, y))
    y += whole.height + gap
    draw.text((margin, y), f"The palette: {len(palette)} colours", fill=art.INK, font=art.label_font(20))
    y += 40
    for i, colour in enumerate(palette):
        r, c = divmod(i, per_row)
        sx, sy = margin + c * (swatch + 6), y + r * (swatch + 6)
        draw.rectangle([sx, sy, sx + swatch, sy + swatch], fill=art.from_hex(colour), outline=art.MUTED)
    return page


def make(folder: Path, tool: Provider | None, *, redraw: bool = True) -> Path:
    """Draw the sheet (or draw it again from the last one), snap it and write the board. Returns the board.

    With `redraw=False` no picture is drawn: the last one is snapped again, which costs no image call (after
    a change to the palette size, say). With the manual tool this raises WaitingForImage until the creator
    has saved the picture."""
    state = store.load(folder)
    if state["sheet"].get("approved"):
        raise KitError("The sheet is already approved. The next step is the poses.")
    if redraw:
        round_ = int(state["sheet"].get("round", 0)) + 1
        photos = [folder / p for p in state["photos"]]
        hint = prompts.palette_hint(None, colours=state["colours"])
        previous = folder / "sheet" / "sheet-ref.png"
        if round_ > 1 and previous.is_file():
            template, refs = "sheet-redraw", [previous, *photos]
        else:
            template, refs = ("sheet-photos" if photos else "sheet-description"), photos
        prompt = prompts.render(template, description=state["description"], outfit=state["outfit"], palette_hint=hint)
        raw = folder / "raw" / f"sheet-r{round_}.png"
        providers.draw(folder, tool, prompt, refs, raw)
    else:
        round_ = int(state["sheet"].get("round", 0))
        raw = folder / "raw" / f"sheet-r{round_}.png"
        if not round_ or not raw.is_file():
            raise KitError("There is no sheet picture to snap again yet. Draw the sheet first.")

    rgb, opaque = snap.load_picture(raw)
    slots = snap.split_figures(rgb, opaque=opaque)
    if not slots:
        raise snap.SnapError("No figure was found in the sheet. Draw it again.")
    pitch = _choose_pitch(rgb, slots[0].height)
    phx, phy = snap.phase_for(rgb, pitch)
    snapped = snap.snap_array(rgb, pitch, phx, phy, colours=state["colours"], opaque=opaque)
    figs = snap.figures(snapped, slots)
    front = figs[0].rgba
    if not 120 <= front.shape[0] <= art.BASELINE:
        raise snap.SnapError(
            f"The front view came out {front.shape[0]} pixels tall after snapping, which does not fit. Draw the sheet again."
        )
    palette = [art.to_hex(c) for c in snapped.used_colours()]
    out = folder / "sheet"
    rgba = snapped.rgba()
    art.save_rgba(rgba, out / "sheet.png")
    art.save_rgba(front, out / "front.png")
    art.save_reference(art.crop(rgba), out / "sheet-ref.png")
    meta = {"round": round_, "raw": f"raw/{raw.name}", "pitch": round(pitch, 3), "phase": [phx, phy],
            "front": [int(front.shape[1]), int(front.shape[0])], "palette": palette}  # fmt: skip
    (out / "sheet.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    board_path = out / "board.png"
    _sheet_board(raw, art.crop(rgba), front, palette, round_).save(board_path)

    state = store.load(folder)
    state["sheet"] = {"round": round_, "raw": f"raw/{raw.name}", "approved": False, "pitch": round(pitch, 3),
                      "height": int(front.shape[0]), "width": int(front.shape[1]), "colours": len(palette)}  # fmt: skip
    if redraw:
        state["face_marks"] = None  # marks belong to the picture they were read off
    store.save(folder, state)
    # the face check belongs to this gate: measure the face now, so the creator sees it before approving
    try:
        faces.make(folder)
        note = None
    except faces.FaceNotFound as problem:
        note = str(problem)
    state = store.load(folder)
    state["sheet"]["face_note"] = note
    store.save(folder, state)
    return board_path


def approve(folder: Path) -> dict[str, Any]:
    """Lock the sheet: its colours become the palette, the front view the idle pose, and its face (as measured,
    or as the creator marked it) the face every later step uses. The face states and the face board are built
    here too. With no face to use, nothing is approved and the error says what to do."""
    state = store.load(folder)
    if not state["sheet"].get("round"):
        raise KitError("There is no sheet to approve yet. Draw it first.")
    if state["sheet"].get("approved"):
        return {"palette": len(state["palette"]), "height": state["sheet"]["height"], "face_board": str(folder / faces.BOARD)}
    front = art.load_rgba(folder / "sheet" / "front.png")
    canvas, _, clipped = art.place(front)
    if clipped:
        raise KitError("The front view does not fit the shared canvas. Draw the sheet again.")
    face, reason = faces.measure(canvas, state.get("face_marks"))
    if face is None:
        try:
            faces.make(folder)  # draws the board with a grid to read the boxes off
        except faces.FaceNotFound:
            pass
        raise faces.FaceNotFound(f"{reason} {faces.FIX}")
    meta = json.loads((folder / "sheet" / "sheet.json").read_text(encoding="utf-8"))
    art.save_rgba(canvas, folder / "poses" / "idle.png")
    art.save_reference(front, folder / "sheet" / "front-ref.png")
    art.save_reference(front, folder / "poses" / "refs" / "idle-ref.png")
    state["palette"] = meta["palette"]
    state["face"] = face.to_dict()
    state["sheet"]["approved"] = True
    state["poses"]["idle"] = {"round": state["sheet"]["round"], "raw": state["sheet"]["raw"], "approved": True,
                              "face": "canon", "offset": [0, 0], "match": 0.0, "air": 0, "clipped": None,
                              "note": None, "hand": None}  # fmt: skip
    store.save(folder, state)
    board = faces.make(folder)
    return {"palette": len(state["palette"]), "height": state["sheet"]["height"], "face_board": str(board)}
