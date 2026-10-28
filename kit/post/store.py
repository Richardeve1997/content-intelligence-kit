"""The small files the posting code keeps for a video: a receipt for each platform and the record of the
upload in work/<slug>/post/, and the creator's approval of the captions beside captions.json.

A receipt is written before anything is sent, so a run that stops half way can pick up where it was.
Every write replaces the whole file in one step, and a file is readable by its owner only.
"""

from __future__ import annotations

import contextlib
import json
import os
import uuid
from pathlib import Path

from kit.post.errors import PostError

try:  # file locks exist on macOS and Linux
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

__all__ = ["read", "write", "write_text", "locked"]


def read(path: Path) -> dict:
    """The record in a file, or {} when there is none. A damaged record is an error, never a guess."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise PostError(f"The file {path.name} could not be read ({exc.strerror or type(exc).__name__}).") from None
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise PostError(
            f"The file {path.name} in the post folder is damaged, so the kit cannot tell what it already sent. "
            "Check Zernio for a post for this video before you delete the file and run the command again."
        )
    return data


def write(path: Path, data: dict) -> None:
    """Replace a record in one step, so a run that stops never leaves half a file."""
    write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def write_text(path: Path, text: str) -> None:
    """Replace a file's text in one step. The file ends up readable by its owner only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(text, encoding="utf-8")
        temp.chmod(0o600)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


@contextlib.contextmanager
def locked(lock_file: Path):
    """Hold a lock on `lock_file`. Two runs for the same video wait for each other instead of posting twice."""
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    with lock_file.open("a") as handle:
        if fcntl is not None:
            fcntl.flock(handle, fcntl.LOCK_EX)
        yield
