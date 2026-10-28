"""The creator's music beds: me/music/<mood>/ folders of tracks, and the ledger that stops repeats.

The kit ships no music. The creator drops tracks they have the right to use into mood folders, such as
me/music/calm/ or me/music/upbeat/, and the folder name is how a bed is chosen. Every bed used is logged
in me/music/MUSIC.md, newest last, so `pick` never hands back the track the last Reel used while
another one is there.

The ledger holds one row per video. A video that is finished again (after a review fix, say) keeps the bed
it was given: `for_video` returns that track, and `log` leaves the row alone. When the video's bed really
changed, its row moves to the end with the new track, since that bed is now the newest use.

A track inside me/music is logged by its path there (calm/morning.mp3). A bed passed as a file from anywhere
else is logged by its whole path, so the video finds it again on its next finish.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from kit import config

__all__ = ["AUDIO", "chosen", "for_video", "log", "music_dir", "pick", "tracks", "used"]

AUDIO = {".mp3", ".m4a", ".aac", ".wav", ".aif", ".aiff", ".flac", ".ogg", ".opus"}
_LEDGER = (
    "# Music log\n\n"
    "Every track used on a Reel is logged here, newest last, so the same track is not picked twice in a row.\n\n"
    "| date | slug | track |\n| --- | --- | --- |\n"
)


def music_dir() -> Path:
    return config.me_dir() / "music"


def _name(track: Path) -> str:
    """How a track is written in the ledger: its path inside me/music, or its whole path for a file elsewhere."""
    track = Path(track).resolve()
    try:
        return track.relative_to(music_dir().resolve()).as_posix()
    except ValueError:
        return track.as_posix()


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


def _rows() -> list[tuple[str, str, str]]:
    """The ledger's rows, oldest first, as (date, slug, track)."""
    ledger = music_dir() / "MUSIC.md"
    if not ledger.is_file():
        return []
    rows = []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 3 and cells[0][:4].isdigit() and cells[2]:
            rows.append((cells[0], cells[1], cells[2]))
    return rows


def used() -> list[str]:
    """The tracks in the ledger, oldest first, as written in its track column."""
    return [track for _, _, track in _rows()]


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


def chosen(slug: str) -> Path | None:
    """The bed the ledger gives this video, when that track is still where the ledger says. Otherwise None."""
    names = [track for _, row_slug, track in _rows() if row_slug == slug]
    if not names:
        return None
    path = music_dir() / names[-1]
    return path if path.is_file() and path.suffix.lower() in AUDIO else None


def for_video(slug: str, mood: str | None = None) -> Path | None:
    """The bed for one video: the one it already has, or a fresh pick.

    A video that already has a bed in the ledger keeps it, so every finish of that video sounds the same.
    When `mood` names a folder and the video's bed is not in it, a new bed is picked from that folder.
    """
    config.work_dir(slug)  # refuses a name that is not a video name, with a plain sentence
    current = chosen(slug)
    if current is not None and (not mood or current.parent.name == mood):
        return current
    return pick(mood)


def log(track: Path, slug: str) -> None:
    """Record this video's bed in me/music/MUSIC.md, one row per video (the ledger is started if missing).

    Logging the same bed for the same video again changes nothing. A different bed for a video already
    in the ledger takes that video's row out and adds its new row at the end, so the ledger keeps one row per
    video, newest last, and `pick` skips the bed this video just took.
    """
    config.work_dir(slug)  # the same rule as a video name: refuses anything that would break the table
    ledger = music_dir() / "MUSIC.md"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    text = ledger.read_text(encoding="utf-8") if ledger.is_file() else _LEDGER
    if "| date | slug | track |" not in text:
        text = text.rstrip("\n") + "\n\n| date | slug | track |\n| --- | --- | --- |\n"
    if not text.endswith("\n"):
        text += "\n"
    name = _name(track)
    row = f"| {date.today().isoformat()} | {slug} | {name} |"
    lines = text.splitlines()
    for index, line in enumerate(lines):
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 3 and cells[0][:4].isdigit() and cells[1] == slug:
            if cells[2] == name:
                return
            del lines[index]  # the bed changed: this video's row moves to the end, as the newest use
            text = "\n".join(lines) + "\n"
            break
    ledger.write_text(text + row + "\n", encoding="utf-8")
