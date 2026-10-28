"""kit.character.prompts: the prompt files are generic and fill in only the creator's own words."""

from __future__ import annotations

import re

import pytest

from kit.character import prompts

PLACEHOLDERS = {"description", "outfit", "palette_hint"}


def _all_prompt_files():
    files = sorted(prompts.PROMPTS_DIR.rglob("*.txt"))
    assert files, "no prompt files found"
    return files


MACHINE_PATH = re.compile(r"(?:/Us" + r"ers/|/home/|\b[A-Za-z]:\\)", re.IGNORECASE)  # a folder on someone's computer


@pytest.mark.parametrize("path", _all_prompt_files(), ids=lambda p: p.name)
def test_prompts_are_generic(path):
    """A prompt fills in only the creator's words. It names no colour, address or file of its own."""
    text = path.read_text(encoding="utf-8")
    used = set(re.findall(r"\{([^{}]*)\}", text))
    assert used <= PLACEHOLDERS, f"{path.name} uses placeholders other than {sorted(PLACEHOLDERS)}: {used - PLACEHOLDERS}"
    assert not re.search(r"#[0-9a-fA-F]{6}\b", text), "a prompt names a colour itself; colours come from {palette_hint}"
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text), "a prompt holds an email address"
    assert not MACHINE_PATH.search(text), "a prompt holds a path on someone's computer"
    assert chr(0x2014) not in text and chr(0x2013) not in text


def test_every_template_carries_the_three_placeholders():
    for name in prompts.TEMPLATES:
        text = (prompts.PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")
        for placeholder in PLACEHOLDERS:
            assert "{" + placeholder + "}" in text, f"{name}.txt does not use {{{placeholder}}}"


def test_render_fills_the_creators_words_in_and_nothing_else():
    text = prompts.render(
        "sheet-description",
        description="a cheerful baker with curly hair and round glasses",
        outfit="a yellow apron over a white shirt",
        palette_hint="a small palette of at most 24 flat colours",
    )
    assert "a cheerful baker with curly hair and round glasses" in text
    assert "a yellow apron over a white shirt" in text
    assert "at most 24 flat colours" in text
    assert "{" not in text and "}" not in text


def test_render_leaves_braces_in_the_creators_words_alone():
    text = prompts.render("sheet-description", description="likes {curly} braces", outfit="o", palette_hint="p")
    assert "likes {curly} braces" in text


def test_every_core_pose_and_move_has_a_direction():
    from kit.character.poses import POSE_NAMES, MOVE_NAMES

    directions = prompts.pose_directions()
    assert set(directions) == set(POSE_NAMES) - {"idle"}
    assert set(prompts.move_directions()) == set(MOVE_NAMES)
    for text in list(directions.values()) + list(prompts.move_directions().values()):
        assert text.strip() and len(text) < 400


def test_the_pose_list_is_numbered_in_the_order_given():
    listed = prompts.pose_list(["wave", "shrug"], notes={"shrug": "Arms a little higher."})
    lines = listed.strip().splitlines()
    assert lines[0].startswith("1. wave: ") and lines[1].startswith("2. shrug: ")
    assert lines[1].endswith("Arms a little higher.")


def test_palette_hint_lists_the_locked_colours():
    hint = prompts.palette_hint(["#112233", "#445566"])
    assert "#112233" in hint and "#445566" in hint
    assert prompts.palette_hint(None, colours=24).count("24") == 1
