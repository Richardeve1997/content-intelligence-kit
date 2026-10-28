"""Fixtures for the character tests.

Most of the character pipeline needs the optional `character` dependency group (numpy, Pillow, scipy,
scikit-image, scikit-learn). A test module that needs it calls `pytest.importorskip` for each package
first, so a machine without the group skips those tests instead of failing them.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


@pytest.fixture
def me(tmp_path, monkeypatch) -> Path:
    """An empty creator folder, made the active one for this test. Returns its character folder."""
    folder = tmp_path / "me"
    (folder / "character").mkdir(parents=True)
    monkeypatch.setenv("KIT_ME", str(folder))
    return folder / "character"
