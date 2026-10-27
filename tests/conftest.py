"""Fixtures shared by every test."""

import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "me.example"


@pytest.fixture
def example_me(tmp_path, monkeypatch) -> Path:
    """A throwaway copy of me.example/, made the active creator folder for one test.

    Tests can edit the copy freely. The committed example is never touched.
    """
    target = tmp_path / "me"
    shutil.copytree(EXAMPLE, target)
    monkeypatch.setenv("KIT_ME", str(target))
    return target
