"""The prompts that ask an image tool for the character, its poses and its in-between frames.

Each prompt is a template in kit/character/prompts/. A template holds only three placeholders, filled from
the creator's own words: {description} (who the character is), {outfit} (what they wear) and
{palette_hint} (the colours to use). Nothing in a template describes any real person.

Two more files hold the plain directions for each pose and each move. The pose and move steps add the
directions they need to the end of a template, numbered in the order the figures should be drawn.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping, Sequence

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
PLACEHOLDERS = ("description", "outfit", "palette_hint")
TEMPLATES = ("sheet-photos", "sheet-description", "sheet-redraw", "poses", "inbetweens", "loop")
_PLACEHOLDER = re.compile(r"\{(" + "|".join(PLACEHOLDERS) + r")\}")

__all__ = [
    "PROMPTS_DIR", "PLACEHOLDERS", "TEMPLATES", "render", "palette_hint", "pose_directions", "move_directions",
    "pose_list",
]  # fmt: skip


def render(name: str, *, description: str, outfit: str, palette_hint: str) -> str:
    """Template `name` with the creator's words put in. Braces inside their words are left as they are."""
    if name not in TEMPLATES:
        raise ValueError(f"There is no prompt template called {name}. The templates are: {', '.join(TEMPLATES)}.")
    template = (PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")
    values = {"description": description.strip(), "outfit": outfit.strip(), "palette_hint": palette_hint.strip()}
    return _PLACEHOLDER.sub(lambda match: values[match.group(1)], template).rstrip("\n") + "\n"


def palette_hint(locked: Sequence[str] | None, *, colours: int = 32) -> str:
    """The colour line of a prompt: the locked palette once the sheet is approved, else a limit."""
    if locked:
        return "use only these colours from the approved sheet, and no others: " + ", ".join(locked) + "."
    return f"a small palette of at most {int(colours)} flat colours that suit the character."


def _directions(file_name: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in (PROMPTS_DIR / file_name).read_text(encoding="utf-8").splitlines():
        name, sep, text = line.partition(":")
        if sep and name.strip() and text.strip():
            found[name.strip()] = text.strip()
    return found


def pose_directions() -> dict[str, str]:
    """What each pose looks like, in plain words, keyed by pose name."""
    return _directions("pose-directions.txt")


def move_directions() -> dict[str, str]:
    """What changes between the frames of each move, keyed by move name."""
    return _directions("move-directions.txt")


def pose_list(names: Sequence[str], notes: Mapping[str, str] | None = None) -> str:
    """The numbered pose lines that end a pose prompt. A creator's note for a pose follows its direction."""
    directions = pose_directions()
    lines = []
    for number, name in enumerate(names, 1):
        line = f"{number}. {name}: {directions[name]}"
        note = (notes or {}).get(name)
        if note:
            line += f" {note.strip()}"
        lines.append(line)
    return "\n".join(lines) + "\n"
