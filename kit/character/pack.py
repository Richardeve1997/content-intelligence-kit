"""Pack the approved character for the HyperFrames runtime (window.Character).

pack(folder) writes, inside the character folder:

  strips/<move>.png        one horizontal strip per move: 245 x 284 art pixels per drawing, feet on row 280,
                           hard pixels with alpha 0 or 255. The runtime scales strips with CSS at whole
                           numbers only.
  strips/pose_<name>.png   one drawing per core pose, for holding a pose (`pose:<name>`)
  manifest.json            the moves (kind, fps, loop or one-shot, sequence of [drawing, ticks], the hold
                           entry, per-drawing mouth, eye and hand anchors), the poses, the face patch sizes
                           and the locked palette
  manifest.js              the same data as window.CHARACTER_MANIFEST, because a HyperFrames page loads
                           data with a script tag, not a fetch

The face patches themselves stay in faces/. An anchor is where the top-left of a patch goes on a drawing.
It exists only on drawings that carry the front view's face, so the runtime never moves a mouth that is
not there.

The heavy imports wait inside pack(), so `import kit.character` works without the character group.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PACKED_FPS = 24
NOTES = {
    "idle": "the approved front view, a still drawing; blinks only",
    "talking": "a loop of four drawings with the raised hand gesturing; the mouth moves by patches",
    "point_left": "the arm rises from idle to point at the viewer's left, holds on the key, and settles back",
    "point_right": "the arm rises from idle to point at the viewer's right, holds on the key, and settles back",
    "point_up": "the arm rises from idle to point straight up, holds on the key, and settles back",
}
RULES = [
    "whole-number scales only, with image-rendering: pixelated",
    "idle is a still drawing with blinks only: no breathing, bobbing or bounce",
    "the mouth moves only on drawings that carry a mouth anchor",
    "a blink never lands on an open mouth",
]


def _missing(folder: Path, state: dict[str, Any]) -> list[str]:
    from kit.character.poses import MOVE_NAMES, POSE_NAMES

    if not state.get("sheet", {}).get("approved"):
        return ["draw and approve the character sheet"]
    todo = []
    waiting = [n for n in POSE_NAMES if not state["poses"].get(n, {}).get("approved")]
    if waiting:
        todo.append("approve these poses: " + ", ".join(waiting))
    if not (folder / "faces" / "faces.json").is_file():
        todo.append("build the face states")
    for move in MOVE_NAMES:
        entry = state["moves"].get(move, {})
        if not entry.get("built"):
            todo.append(f"draw the in-betweens for {move}")
        elif entry.get("key_round") != state["poses"].get(move, {}).get("round"):
            todo.append(f"draw the in-betweens for {move} again, because its pose changed")
    return todo


def pack(me_character: Path) -> Path:
    """Pack the character in `me_character` (normally me/character/) and return the manifest.json path.

    Raises KitError naming every step still to do when the character is not finished."""
    import numpy as np

    from kit.character import art, store
    from kit.character.faces import FaceBox
    from kit.character.poses import LABELS, MOVE_NAMES, POSE_NAMES
    from kit.config import KitError

    folder = Path(me_character)
    state = store.load(folder)
    todo = _missing(folder, state)
    if todo:
        raise KitError("The character is not ready to pack yet. Still to do: " + "; ".join(todo) + ".")

    rects = json.loads((folder / "faces" / "faces.json").read_text(encoding="utf-8"))["patches"]
    face = FaceBox.from_dict(state["face"])

    def anchors(offset, hand) -> dict[str, Any]:
        if offset is None:
            return {"mouth": None, "eyes": None, "hand": hand}
        ox, oy = offset
        return {
            "mouth": [rects["mouth_closed"]["x"] + ox, rects["mouth_closed"]["y"] + oy],
            "eyes": [rects["eyes_open"]["x"] + ox, rects["eyes_open"]["y"] + oy],
            "hand": hand,
        }

    def ref_col(canvas: np.ndarray, offset) -> float:
        """The head-centre column: the face's middle when the drawing has the front view's head."""
        if offset is not None:
            return round(face.x + face.w / 2 + offset[0], 1)
        box = art.content_box(canvas)
        band = canvas[box[1] : box[1] + max(4, (box[3] - box[1]) // 5), :, 3] > 0
        return round(float(np.nonzero(band)[1].mean()), 1)

    strips = folder / "strips"
    strips.mkdir(parents=True, exist_ok=True)
    for old in strips.glob("*.png"):
        old.unlink()

    def entry(kind, frames, sequence, hold, info, strip, label, notes, extra=None) -> dict[str, Any]:
        art.save_rgba(np.concatenate(frames, axis=1), folder / strip)
        return {
            "kind": kind, "frames": len(frames), "fps": PACKED_FPS, "strip": strip,
            "ref_col": ref_col(frames[0], info[0]["offset"]), "label": label, "status": "approved",
            "notes": notes,
            "sequence": sequence, "hold": hold, "loop": None, "travel_px_per_frame": 0,
            "frame_info": [anchors(i["offset"], i.get("hand")) for i in info], **(extra or {}),
        }  # fmt: skip

    poses: dict[str, Any] = {}
    for name in POSE_NAMES:
        record = state["poses"][name]
        canvas = art.load_rgba(folder / "poses" / f"{name}.png")
        info = [{"offset": record.get("offset"), "hand": record.get("hand")}]
        poses[name] = entry("pose", [canvas], [[0, 1]], 0, info, f"strips/pose_{name}.png", LABELS[name],
                            "a still drawing, held until the next item", {"face": record.get("face"), "approved": True})  # fmt: skip

    moves: dict[str, Any] = {}
    art.save_rgba(art.load_rgba(folder / "poses" / "idle.png"), strips / "idle.png")
    moves["idle"] = {key: value for key, value in poses["idle"].items() if key not in ("face", "approved")}
    moves["idle"].update(strip="strips/idle.png", notes=NOTES["idle"])
    for move in MOVE_NAMES:
        doc = json.loads((folder / "moves" / move / f"{move}.json").read_text(encoding="utf-8"))
        frames = [art.load_rgba(folder / "moves" / move / f"frame_{n:02d}.png") for n in range(1, doc["frames"] + 1)]
        moves[move] = entry(doc["kind"], frames, doc["sequence"], doc["hold"], doc["frame_info"], f"strips/{move}.png",
                            LABELS[move], NOTES[move])  # fmt: skip

    manifest = {
        "character": state.get("name") or "character",
        "canvas": [art.CANVAS_W, art.CANVAS_H],
        "baseline_row": art.BASELINE,
        "native_fps": PACKED_FPS,
        "palette": list(state["palette"]),
        "faces": {name: [r["w"], r["h"]] for name, r in rects.items()},
        "rules": RULES,
        "ref_col_note": "ref_col is the head-centre column of a move's first drawing; the x given to Character.mount "
        "and Character.script is where that column sits on screen.",
        "moves": moves,
        "poses": poses,
    }
    path = folder / "manifest.json"
    path.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    (folder / "manifest.js").write_text(
        "/* Written by kit.character.pack. Do not edit: run the pack step again. */\n"
        "window.CHARACTER_MANIFEST = " + json.dumps(manifest) + ";\n",
        encoding="utf-8",
    )
    return path
