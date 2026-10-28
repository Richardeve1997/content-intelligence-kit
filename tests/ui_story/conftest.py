"""Fixtures for the UI story tests. The helpers themselves are in uistory_helpers.py."""

import pytest

from kit import media


@pytest.fixture(autouse=True)
def private_slots(tmp_path, monkeypatch):
    """Heavy-job slots in a temp folder, so a test never waits on a real render."""
    monkeypatch.setattr(media, "_slots_dir", lambda: tmp_path / "slots")
    monkeypatch.delenv(media.SLOT_ENV, raising=False)
    monkeypatch.setattr(media, "MIN_FREE_GB", 0.0)
