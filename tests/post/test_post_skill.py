"""The post skill, and the plain-writing rules for everything this feature added.

The skill is read by an agent, so its contract is checked like code: it reads the creator's rules first,
it ends with the Feedback section, and every command and option it names exists.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from kit.post import captions, cli, payloads

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / ".claude" / "skills" / "post" / "SKILL.md"

EM_DASH, EN_DASH = chr(0x2014), chr(0x2013)  # written as code points so this file holds no dash
# Words the kit's own prose never uses. Built from pieces so this file does not contain them whole.
INTERNAL_WORDS = ["rul" + "ing", "contro" + "ller", "release " + "scan", "house " + "brand", "plan " + "task"]
PERSONAL_WORDS = ["Art of the " + "Possible", "AO" + "TP", "Clear " + "Sky", "Mini " + "Rich", "0096" + "FF", "IMG" + "_", "/Us" + "ers/"]

OWNED = [
    "kit/zernio.py",
    "kit/post/__init__.py",
    "kit/post/__main__.py",
    "kit/post/captions.py",
    "kit/post/cli.py",
    "kit/post/errors.py",
    "kit/post/payloads.py",
    "kit/post/schedule.py",
    "kit/post/store.py",
    ".claude/skills/post/SKILL.md",
    "tests/post/conftest.py",
    "tests/post/fake_zernio.py",
    "tests/post/test_post_captions.py",
    "tests/post/test_post_cli.py",
    "tests/post/test_post_payloads.py",
    "tests/post/test_post_schedule.py",
    "tests/post/test_post_skill.py",
    "tests/post/test_zernio_client.py",
]


def text() -> str:
    return SKILL.read_text(encoding="utf-8")


def frontmatter() -> dict:
    block = text().split("---\n")[1]
    return dict(re.findall(r"^(\w+):\s*(.+)$", block, re.M))


# --- the contract every skill follows -----------------------------------------------------------------


def test_the_frontmatter_names_the_skill_and_says_when_to_use_it():
    meta = frontmatter()
    assert meta["name"] == "post"
    assert len(meta["description"]) > 80 and "Use when" in meta["description"]
    assert ": " not in meta["description"], "a colon and a space would break the frontmatter"
    for phrase in ("post it everywhere tomorrow at 6pm", "Zernio key"):
        assert phrase in meta["description"]


def test_it_reads_the_creators_rules_first_and_ends_with_feedback():
    body = text()
    assert 'kit.rules.read("post")' in body
    assert "kit.rules.read" in re.search(r"^1\. (.+)$", body, re.M).group(1)
    assert re.findall(r"^## (.+)$", body, re.M)[-1] == "Feedback"
    feedback = body[body.index("## Feedback") :]
    assert 'kit.rules.add("post"' in feedback and "python -m kit.rules add post '<rule>'" in feedback
    assert not re.findall(r'python -m kit\.rules add \S+ "', body), "a rule is quoted with single quotes in a shell"


def test_the_skill_folder_matches_its_name():
    assert SKILL.parent.name == frontmatter()["name"]


# --- the commands it names are real ------------------------------------------------------------------------


def named_commands() -> list[tuple[str, list[str]]]:
    found = []
    for line in text().splitlines():
        for span in re.findall(r"`([^`]*python -m kit\.post[^`]*)`", line):
            words = span.split()
            after = words[words.index("kit.post") + 1 :]
            if not after[0].startswith("<"):  # "<command>" stands for any of them
                found.append((after[0], [w for w in after if w.startswith("--")]))
    return found


def test_every_command_and_option_the_skill_names_exists():
    parser = cli.build_parser()
    commands = parser._subparsers._group_actions[0].choices
    seen = set()
    for name, flags in named_commands():
        assert name in commands, f"the skill names a command that does not exist: {name}"
        known = {option for action in commands[name]._actions for option in action.option_strings}
        for flag in flags:
            assert flag in known, f"the skill names an option that does not exist: {name} {flag}"
        seen.add(name)
    assert {"check", "lines", "captions", "approve", "schedule", "list", "reschedule", "cancel", "manual"} <= seen


def test_the_skill_runs_the_checks_in_the_order_that_keeps_the_creator_in_charge():
    body = text()
    order = [
        body.index("post captions <slug>"),
        body.index("Show the creator every caption"),
        body.index("post approve <slug>"),
        body.index("post schedule <slug>"),
    ]
    assert order == sorted(order)


def test_step_8_keeps_the_creator_in_charge_of_what_goes_out():
    step = re.search(r"^8\. (.+)$", text(), re.M).group(1)
    assert "never run `approve` or `schedule` until the creator has read every caption and said yes" in step.lower()
    assert "in this conversation" in step
    assert "never call the python function directly" in step.lower()
    assert "TikTok" in step and "preview and consent" in step  # their yes to that caption is TikTok's consent
    assert "`schedule` refuses to send until the creator approves again" in re.search(r"^9\. (.+)$", text(), re.M).group(1)


def test_the_numbers_in_the_skill_match_the_rules_in_the_code():
    body = text()
    assert captions.HASHTAG_CAPS == {"instagram": 5, "tiktok": 5, "youtube": 8, "facebook": 5, "twitter": 2, "linkedin": 0}
    assert "Instagram, TikTok and Facebook take at most 5, YouTube 8, X 2 and LinkedIn none" in body
    assert f"{captions.X_LIMIT} characters" in body
    assert f"first {captions.LINKEDIN_VISIBLE} characters" in body
    assert f"{payloads.YOUTUBE_TITLE_MAX} characters" in body
    for tag in captions.NEVER_TAGS:
        assert tag in body


def test_the_json_example_in_the_skill_is_valid():
    block = re.search(r"```json\n(.*?)```", text(), re.S).group(1)
    example = json.loads(block)
    assert set(example["instagram"]) == {"title", "body", "hashtags"}


# --- plain writing, for everything this feature added ----------------------------------------------------------------


@pytest.mark.parametrize("path", OWNED)
def test_no_em_or_en_dash_and_no_machine_path(path):
    content = (ROOT / path).read_text(encoding="utf-8")
    assert EM_DASH not in content and EN_DASH not in content
    assert "/" + "Users" + "/" not in content


@pytest.mark.parametrize("path", OWNED)
def test_no_internal_process_words_and_nothing_personal(path):
    content = (ROOT / path).read_text(encoding="utf-8")
    lower = content.lower()
    for word in INTERNAL_WORDS:
        assert word not in lower, f"{path} contains {word!r}"
    for word in PERSONAL_WORDS:
        assert word.lower() not in lower, f"{path} contains a word that is personal to the kit's author"


@pytest.mark.parametrize("path", [p for p in OWNED if p.endswith(".py") and not p.startswith("tests/")])
def test_the_code_names_no_colour_font_or_handle(path):
    content = (ROOT / path).read_text(encoding="utf-8")
    assert not re.search(r"(?<![\w&#])#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})(?![\w-])", content)
    assert "rgb(" not in content.lower()
