"""Install hints: every `uv sync` the kit tells a creator to run keeps the groups installed earlier.

`uv sync` makes the environment match exactly what it was asked for. Asked for one group, or for none, it
removes the packages of every other group, which for this kit means the video tools or the research tools.
`--inexact` keeps them. So a hint reads `uv sync --group <name> --inexact`, wherever it is written: in the
kit's code, in the skills, in the guides, in the update notice and in the comments of pyproject.toml.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from kit import transcribe
from kit.config import KitError

ROOT = Path(__file__).resolve().parent.parent

# A command as written in code or prose: it runs to the end of the line, to a quote or a backtick, or to the end of its
# place in a chain (&&, ||, ; or |), so each command of a chain is checked on its own. One that is introduced as
# "a bare `uv sync`" is a warning about what not to run, not a hint to follow, so it is skipped.
COMMAND = re.compile(r"(?<!bare )(?<!bare `)uv sync(?P<rest>[^\n`\"';|&]*)", re.IGNORECASE)
INEXACT = re.compile(r"(?<![\w-])--inexact(?![\w-])")

# Not read by a creator as advice, so not checked: tests (they hold the expected text, and skip reasons for
# developers), the macOS smoke test (it starts from an empty environment, where there is nothing to remove),
# and the humanizer, which is a third party's skill kept as it came.
LEFT_OUT_FOLDERS = ("tests/", ".github/", ".claude/skills/humanizer/")
LOCKS = ("uv.lock", "package-lock.json")


def repo_files() -> list[Path]:
    """The files in the repo that are in git or would be, as paths. Ignored files (the environment, node_modules) are not."""
    if (ROOT / ".git").exists() and shutil.which("git"):
        listing = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z", "--cached", "--others", "--exclude-standard"], capture_output=True
        )
        if listing.returncode == 0:
            names = sorted(n for n in listing.stdout.decode("utf-8", "replace").split("\0") if n)
            return [ROOT / n for n in names if (ROOT / n).is_file()]
    return sorted(p for p in ROOT.rglob("*") if p.is_file() and ".venv" not in p.parts and "node_modules" not in p.parts)


def text_of(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None  # a font, an image or anything else that is not text


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def checked_files() -> list[Path]:
    return [
        path
        for path in repo_files()
        if not relative(path).startswith(LEFT_OUT_FOLDERS) and path.name not in LOCKS
    ]


def hints(path: Path) -> list[str]:
    """Every `uv sync ...` command in a file, as written, up to the end of its line, its quote or its place in a chain."""
    text = text_of(path)
    return ["uv sync" + match.group("rest").rstrip() for match in COMMAND.finditer(text)] if text else []


def test_the_missing_speech_model_sentence_keeps_the_other_groups(monkeypatch):
    monkeypatch.setattr(transcribe, "_use_mlx", lambda: False)

    def no_model(name):
        raise ImportError("faster_whisper is not installed")

    monkeypatch.setattr(transcribe, "_faster_model", no_model)
    with pytest.raises(KitError) as caught:
        transcribe._asr([0.0] * 16, word_times=False)
    assert str(caught.value) == "No speech model is installed. Install the video tools with: uv sync --group video --inexact"


def test_every_uv_sync_in_the_repo_says_inexact():
    seen, wrong = 0, []
    for path in checked_files():
        for command in hints(path):
            seen += 1
            if not INEXACT.search(command):
                wrong.append(f"{relative(path)}: {command}")
    assert seen >= 8, "the scan should find the hints in the doctor, the guides, the skills and pyproject.toml"
    assert not wrong, "these would remove the groups installed earlier:\n" + "\n".join(wrong)


@pytest.mark.parametrize(
    "name",
    [
        "bin/doctor.py",
        "bin/update_kit.py",
        "ONBOARDING.md",
        "pyproject.toml",
        ".claude/skills/onboard/SKILL.md",
        ".claude/skills/update-kit/SKILL.md",
        ".claude/skills/research/SKILL.md",
        "kit/transcribe.py",
    ],
)
def test_the_scan_reaches_the_files_that_name_a_sync(name):
    assert ROOT / name in checked_files(), f"{name} is not scanned"
    assert hints(ROOT / name), f"{name} no longer names a sync, so it can leave this list"


def test_the_scan_leaves_out_only_what_it_says():
    left = {relative(p) for p in repo_files()} - {relative(p) for p in checked_files()}
    assert all(n.startswith(LEFT_OUT_FOLDERS) or Path(n).name in LOCKS for n in left), "no single file is left out by name"


def test_the_detector_reads_a_command_to_the_end_of_its_line_or_quote(tmp_path):
    sample = tmp_path / "sample.md"
    sample.write_text(
        "run: uv sync --group video --group research --inexact, then npm install.\n"
        "Install them with `uv sync --group video` and carry on.\n"
        'x = "uv sync"\n'
        "then run uv sync, and more words on the line\n",
        encoding="utf-8",
    )
    assert [bool(INEXACT.search(c)) for c in hints(sample)] == [True, False, False, False]


def test_each_command_in_a_chain_is_checked_on_its_own(tmp_path):
    """A flag on the second command of a chain says nothing about the first, which still removes the other groups."""
    sample = tmp_path / "sample.md"
    sample.write_text(
        "uv sync --group video && uv sync --group research --inexact\n"
        "uv sync --group video --inexact; uv sync --group research\n"
        "`uv sync --group video || uv sync --group research --inexact`\n"
        "uv sync --group video --inexact | tail -n 1\n"
        "uv sync --group video --inexact && npm ci\n",
        encoding="utf-8",
    )
    found = hints(sample)
    assert [bool(INEXACT.search(c)) for c in found] == [False, True, True, False, False, True, True, True], found
    assert found[0] == "uv sync --group video", "the first command ends where the chain goes on"


def test_a_bare_command_named_in_a_warning_is_not_a_hint(tmp_path):
    """A skill may say what not to run, as "a bare `uv sync`". That names the command; it does not tell anyone to run it."""
    sample = tmp_path / "sample.md"
    sample.write_text(
        "A bare `uv sync` removes the group again, so write both.\n"
        "Never a bare uv sync here.\n"
        "Bare `uv sync` is the mistake.\n"
        "Run `uv sync --group character --inexact`. A bare `uv sync` would undo it.\n"
        "Run `uv sync --group character`. A bare `uv sync` would undo it.\n"
        "Run a bare-ish uv sync.\n",
        encoding="utf-8",
    )
    found = hints(sample)
    assert [bool(INEXACT.search(c)) for c in found] == [True, False, False], found
    assert found[1] == "uv sync --group character", "a hint on the same line as a warning is still checked"
    assert found[2].startswith("uv sync"), "only the word bare in front excuses a command"


def test_the_character_files_are_scanned_like_every_other():
    for name in ("kit/character/__main__.py", ".claude/skills/character/SKILL.md"):
        assert ROOT / name in checked_files(), f"{name} is not scanned"
        assert hints(ROOT / name), f"{name} no longer names a sync"
