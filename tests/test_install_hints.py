"""Install hints: every one names its group with --inexact, so a sync never removes a group installed earlier.

`uv sync` makes the environment match exactly what it was asked for. Asked for one group, it removes the
packages of every other group. `--inexact` keeps them, so each hint the kit prints ends with it.
"""

import re
from pathlib import Path

import pytest

from kit import transcribe
from kit.config import KitError

ROOT = Path(__file__).resolve().parent.parent

# A command as written in code or prose: it runs to the end of the line or to a quote or backtick.
COMMAND = re.compile(r"uv sync(?P<rest>[^\n`\"']*)")


def hint_files() -> list[Path]:
    """Every file that can tell the creator what to run: the kit's code, the doctor, the skills and the guides."""
    found = [*sorted((ROOT / "kit").rglob("*.py")), ROOT / "bin" / "doctor.py"]
    found += sorted((ROOT / ".claude" / "skills").glob("*/SKILL.md"))
    found += [ROOT / name for name in ("ONBOARDING.md", "README.md", "CLAUDE.md", "AGENTS.md") if (ROOT / name).is_file()]
    return found


def test_the_missing_speech_model_sentence_keeps_the_other_groups(monkeypatch):
    monkeypatch.setattr(transcribe, "_use_mlx", lambda: False)

    def no_model(name):
        raise ImportError("faster_whisper is not installed")

    monkeypatch.setattr(transcribe, "_faster_model", no_model)
    with pytest.raises(KitError) as caught:
        transcribe._asr([0.0] * 16, word_times=False)
    assert str(caught.value) == "No speech model is installed. Install the video tools with: uv sync --group video --inexact"


def test_every_hint_that_names_a_group_also_says_inexact():
    seen = 0
    for path in hint_files():
        text = path.read_text(encoding="utf-8")
        for match in COMMAND.finditer(text):
            rest = match.group("rest")
            if "--group" not in rest:
                continue
            seen += 1
            assert "--inexact" in rest.split(), f"{path.relative_to(ROOT)} says: uv sync{rest.rstrip()}"
    assert seen >= 4, "the scan should find the hints in the doctor, the guide and the skills"


@pytest.mark.parametrize("name", ["bin/doctor.py", "ONBOARDING.md", ".claude/skills/onboard/SKILL.md"])
def test_setup_never_tells_the_creator_to_run_a_bare_sync(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    for match in COMMAND.finditer(text):
        assert "--group" in match.group("rest"), f"{name} says a bare: uv sync{match.group('rest').rstrip()}"
