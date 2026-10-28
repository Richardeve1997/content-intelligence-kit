"""The creator's music beds: me/music/<mood>/ folders of tracks, and the ledger that stops repeats.

The kit ships no music. The creator drops tracks they have the right to use into mood folders, such as
me/music/calm/ or me/music/upbeat/, and the folder name is how a bed is chosen. Every bed used is logged
in me/music/MUSIC.md, newest last, so `pick` never hands back the track the last Reel used while
another one is there.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from kit import config

__all__ = ["AUDIO", "log", "music_dir", "pick", "tracks", "used"]

AUDIO = {".mp3", ".m4a", ".aac", ".wav", ".aif", ".aiff", ".flac", ".ogg", ".opus"}
_LEDGER = (
    "# Music log\n\n"
    "Every track used on a Reel is logged here, newest last, so the same track is not picked twice in a row.\n\n"
    "| date | slug | track |\n| --- | --- | --- |\n"
)


def music_dir() -> Path:
    return config.me_dir() / "music"


def _name(track: Path) -> str:
    """How a track is written in the ledger: its path inside me/music, or its file name."""
    track = Path(track)
    try:
        return track.resolve().relative_to(music_dir().resolve()).as_posix()
    except ValueError:
        return track.name


def tracks(mood: str | None = None) -> list[Path]:
    """The audio files in me/music/<mood>/, or in every mood folder when `mood` is None, by name."""
    root = music_dir()
    if mood:
        folders = [root / mood]
    else:
        folders = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")) if root.is_dir() else []
    found = []
    for folder in folders:
        if folder.is_dir():
            found += [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in AUDIO and not p.name.startswith(".")]
    return sorted(found, key=lambda p: _name(p))


def used() -> list[str]:
    """The tracks in the ledger, oldest first, as written in its track column."""
    ledger = music_dir() / "MUSIC.md"
    if not ledger.is_file():
        return []
    names = []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 3 and cells[0][:4].isdigit() and cells[2]:
            names.append(cells[2])
    return names


def pick(mood: str | None = None) -> Path | None:
    """A bed from me/music/<mood>/ (or any mood folder), skipping the track used last.

    A track never used comes first, then the one used longest ago. When the only track is the one used
    last, it is used again rather than leaving the Reel with no bed. None means there is no music.
    """
    candidates = tracks(mood)
    if not candidates:
        return None
    history = used()
    last = history[-1] if history else None
    fresh = [t for t in candidates if _name(t) != last] or candidates

    def when(track: Path) -> int:
        name = _name(track)
        return max((i for i, n in enumerate(history) if n == name), default=-1)

    return sorted(fresh, key=lambda t: (when(t), _name(t)))[0]


def log(track: Path, slug: str) -> None:
    """Add a row for this bed to me/music/MUSIC.md (the ledger is started if it is not there)."""
    config.work_dir(slug)  # the same rule as a video name: refuses anything that would break the table
    ledger = music_dir() / "MUSIC.md"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    text = ledger.read_text(encoding="utf-8") if ledger.is_file() else _LEDGER
    if "| date | slug | track |" not in text:
        text = text.rstrip("\n") + "\n\n| date | slug | track |\n| --- | --- | --- |\n"
    if not text.endswith("\n"):
        text += "\n"
    text += f"| {date.today().isoformat()} | {slug} | {_name(track)} |\n"
    ledger.write_text(text, encoding="utf-8")
