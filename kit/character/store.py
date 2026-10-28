"""Where the character lives in the creator's folder, and the record of how far it has got.

Everything sits in me/character/:

  character.json   this record: the description, the image tool, the locked palette, the measured face,
                   and for the sheet, each pose and each move its round, its raw picture and whether the
                   creator approved it
  photos/          the creator's reference photos, if they gave some
  inbox/           prompts waiting to be pasted by hand, copies of the pictures to attach to them (deleted
                   once the picture is back), and the pictures dropped back in
  raw/             every picture an image tool returned, untouched
  sheet/ poses/ faces/ moves/   the snapped and fixed results, each with a board to look at
  strips/ manifest.json manifest.js   what the pack step writes for the runtime

The record is plain JSON and is written in one step, so a stopped run never leaves half a file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from kit import config
from kit.config import KitError

FILE = "character.json"
VERSION = 1

__all__ = ["FILE", "character_dir", "exists", "load", "save", "record_call"]


def character_dir() -> Path:
    """The creator's character folder, me/character/."""
    return config.me_dir() / "character"


def exists(folder: Path) -> bool:
    return (folder / FILE).is_file()


def load(folder: Path) -> dict[str, Any]:
    """The record for the character in `folder`. A missing or broken record is a plain KitError."""
    path = folder / FILE
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise KitError("There is no character yet. Say: make my character.") from None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise KitError(f"{path.name} in the character folder cannot be read. Fix it, or start the character again.") from None
    if not isinstance(state, dict) or state.get("version") != VERSION:
        raise KitError(f"{path.name} in the character folder is not a record this kit can read.")
    return state


def save(folder: Path, state: dict[str, Any]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / FILE
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temp, path)


def record_call(folder: Path, request: str, tool: str, ok: bool, seconds: float) -> None:
    """Count one image call. The count is what the creator pays for, so failed calls count too."""
    state = load(folder)
    state["calls"] = int(state.get("calls", 0)) + 1
    state.setdefault("call_log", []).append(
        {"request": request, "tool": tool, "ok": bool(ok), "seconds": round(float(seconds), 1)}
    )
    save(folder, state)
