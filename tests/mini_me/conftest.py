"""Fixtures for the mini me tests. The helpers themselves are in mini_me_helpers.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from kit import config, media

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


@pytest.fixture(autouse=True)
def private_slots(tmp_path, monkeypatch):
    """Heavy-job slots in a temp folder, so a test never waits on a real render."""
    monkeypatch.setattr(media, "_slots_dir", lambda: tmp_path / "slots")
    monkeypatch.delenv(media.SLOT_ENV, raising=False)
    monkeypatch.setattr(media, "MIN_FREE_GB", 0.0)


@pytest.fixture
def kit_home(tmp_path, example_me, monkeypatch) -> Path:
    """A throwaway kit root: work/ and .env live under it, and me/ is a copy of me.example."""
    root = tmp_path / "kit"
    root.mkdir()
    monkeypatch.setattr(config, "kit_root", lambda: root)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    return root
