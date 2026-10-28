"""Shared helpers for the UI story tests (imported by the test files and conftest.py).

The skill's scripts live in .claude/skills/ui-story/kit/, which is a folder of scripts and not a Python
package, so tests load them by path. Media fixtures are made with ffmpeg at test time.
"""

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / ".claude" / "skills" / "ui-story"
KIT = SKILL / "kit"
REVIEW = ROOT / ".claude" / "skills" / "review"


def load(name: str):
    """Import .claude/skills/ui-story/kit/<name>.py as a module."""
    path = KIT / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"uistory_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run_kit(script: str, *args, cwd=ROOT, env=None, timeout=300) -> subprocess.CompletedProcess:
    """Run a kit script with this interpreter, the way the skill runs it."""
    return subprocess.run(
        [sys.executable, str(KIT / script), *map(str, args)],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout,
    )


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *map(str, args)], check=True)


needs_say = pytest.mark.skipif(shutil.which("say") is None, reason="needs the macOS say command")
