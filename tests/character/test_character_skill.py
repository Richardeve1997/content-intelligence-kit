"""The character skill follows the kit's shared contract, and its files carry nothing private."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / ".claude" / "skills" / "character" / "SKILL.md"
RUNTIME = ROOT / ".claude" / "skills" / "mini-me" / "kit" / "character"
EM_DASH, EN_DASH = chr(0x2014), chr(0x2013)  # written as code points so this file holds no dash
INTERNAL_WORDS = ["rul" + "ing", "contro" + "ller", "release " + "scan", "house " + "brand", "plan " + "task"]


def _authored() -> list[Path]:
    files = [SKILL, RUNTIME / "character.js", RUNTIME / "test_state.mjs"]
    files += sorted((ROOT / "kit" / "character").rglob("*.py"))
    files += sorted((ROOT / "kit" / "character" / "prompts").rglob("*.txt"))
    files += sorted(Path(__file__).parent.glob("*.py"))
    return files


def _frontmatter(text: str) -> dict:
    block = text.split("---\n")[1]
    return dict(re.findall(r"^(\w+):\s*(.+)$", block, re.M))


def test_the_skill_follows_the_shared_contract():
    body = SKILL.read_text(encoding="utf-8")
    meta = _frontmatter(body)
    assert meta["name"] == "character"
    assert len(meta["description"]) > 80 and "Use when" in meta["description"]
    first_step = re.search(r"^1\. (.+)$", body, re.M).group(1)
    assert 'kit.rules.read("character")' in first_step
    assert re.findall(r"^## (.+)$", body, re.M)[-1] == "Feedback"
    feedback = body[body.index("## Feedback") :]
    assert 'kit.rules.add("character"' in feedback and "python -m kit.rules add character '" in feedback


def test_the_skill_walks_every_approval_and_names_its_commands():
    body = SKILL.read_text(encoding="utf-8")
    for command in ("status", "start", "sheet", "approve sheet", "poses", "approve poses", "redraw", "faces",
                    "faces --eyes", "faces --clear", "moves", "pack"):  # fmt: skip
        assert f"uv run python -m kit.character {command}" in body, command
    assert body.index("kit.character sheet") < body.index("kit.character faces --eyes") < body.index("approve sheet")
    assert body.index("approve sheet") < body.index("approve poses"), "the face is checked before the sheet is approved"
    for words in ("--numbered", "remove character <number>", "13 poses are left to draw, 14 in all", ".attach-1.png",
                  "--description - <<'WORDS'", "smaller hat"):  # fmt: skip
        assert words in body, words
    assert "No image tool found, so I'll give you the prompts to paste into ChatGPT or Gemini" in body
    assert "test_state.mjs" in body
    assert "3 to 5 photos" in body


@pytest.mark.parametrize("path", _authored(), ids=lambda p: str(p.relative_to(ROOT)))
def test_authored_files_have_no_dash_machine_path_or_internal_word(path):
    text = path.read_text(encoding="utf-8")
    assert EM_DASH not in text and EN_DASH not in text
    assert "/" + "Users" + "/" not in text
    lowered = text.lower()
    for word in INTERNAL_WORDS:
        assert word not in lowered, f"{path.name} contains {word!r}"


def test_the_runtime_keeps_to_the_seek_safe_rules():
    source = (RUNTIME / "character.js").read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    code = re.sub(r"(^|[^:])//.*$", r"\1", code, flags=re.M)
    for banned in ("Math.random", "Date.now", "new Date", "performance.now", "requestAnimationFrame", "fetch(",
                   "setTimeout", "setInterval"):  # fmt: skip
        assert banned not in code, banned
    assert "</" + "script" not in source.lower(), "the page compiler inlines this file"
    assert "window.Character = " in code
    for name in ("ready", "mount", "walkTime", "at", "script", "speak"):
        assert re.search(rf"\b{name}: {name}\b", code), name
