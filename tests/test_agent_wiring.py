"""Agent wiring: CLAUDE.md, AGENTS.md, the hooks, the skills' shared contract and the adapter check.

Claude Code and Codex both work on this folder. CLAUDE.md is the one set of instructions, AGENTS.md
points Codex at it, and .agents/skills is a link to .claude/skills so both see the same skills.
Hooks only run in Claude Code, so each skill's own Feedback section is what records corrections.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from kit import rules

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / ".claude" / "skills"
EM_DASH, EN_DASH = chr(0x2014), chr(0x2013)  # written as code points so this file holds no dash
MIDDLE_DOT = "·"
OWN_SKILLS = ["learnings", "next", "update-kit"]
ROUTED = ["onboard", "research", "ui-story", "mini-me", "review", "post", "learnings", "update-kit", "next"]
RULES_COMMANDS = {"read", "add", "undo", "remove", "recent"}
UPDATE_FLAGS = {"--check", "--apply", "--ref", "--from", "--write-manifest"}

# The humanizer is a third party's skill, copied without a change.
HUMANIZER_SHA = {
    "SKILL.md": "14fc8a965b6e0a8dc100ba4dffeab55cb94bbac112abbde7e014d5c15a35c202",
    "LICENSE": "4ac4810254ab36d45419141aeb8e69bf50652cfafe5b2dab947d06d44e5cbf96",
    "README.md": "d45a4faae168c33c3bce155e9bda14eaafc86a2342d4a9ffac47d35dd7a8a8f0",
}


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def settings() -> dict:
    return json.loads(text(ROOT / ".claude" / "settings.json"))


def skill_folders() -> list[Path]:
    return sorted(p for p in SKILLS.iterdir() if p.is_dir())


# --- the link and the third-party skill -----------------------------------------------------------------------


def test_agents_skills_is_a_link_to_the_claude_skills():
    link = ROOT / ".agents" / "skills"
    assert link.is_symlink()
    assert os.readlink(link) == "../.claude/skills"
    assert link.resolve() == SKILLS.resolve()


def test_the_humanizer_is_an_unmodified_copy_with_its_licence():
    for name, expected in HUMANIZER_SHA.items():
        data = (SKILLS / "humanizer" / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == expected, f"{name} was changed"
    assert text(SKILLS / "humanizer" / "LICENSE").startswith("MIT License")
    assert re.search(r"^name: humanizer$", text(SKILLS / "humanizer" / "SKILL.md"), re.M)


def test_third_party_notes_credit_the_humanizer():
    notes = text(ROOT / "THIRD_PARTY.md")
    assert "Humanizer" in notes
    assert ".claude/skills/humanizer" in notes
    assert "MIT" in notes


# --- the hooks --------------------------------------------------------------------------------------------------


def test_settings_run_the_session_start_script():
    hooks = settings()["hooks"]
    assert set(hooks) == {"SessionStart", "Stop"}
    (group,) = hooks["SessionStart"]
    (hook,) = group["hooks"]
    assert hook["type"] == "command"
    assert hook["command"] == 'bash "$CLAUDE_PROJECT_DIR/bin/session_start.sh"'
    assert hook["timeout"] <= 30


def test_settings_run_the_feedback_check_when_a_turn_ends():
    (group,) = settings()["hooks"]["Stop"]
    (hook,) = group["hooks"]
    assert hook["type"] == "command", "a command hook, not an agent: it must be instant and cost nothing"
    assert hook["command"] == 'python3 "$CLAUDE_PROJECT_DIR/bin/feedback_check.py"'
    assert hook["timeout"] <= 30
    assert set(hook) == {"type", "command", "timeout"}, "no prompt, model or async setting"
    assert (ROOT / "bin" / "feedback_check.py").is_file()


def test_the_settings_pre_approve_only_read_only_rules_commands():
    allowed = settings()["permissions"]
    assert set(allowed) == {"allow"}
    assert allowed["allow"] == [
        "Bash(uv run python -m kit.rules read:*)",
        "Bash(uv run python -m kit.rules recent:*)",
    ], "adding, undoing and removing rules still ask first"


# --- the session start script ---------------------------------------------------------------------------------------


START = ROOT / "bin" / "session_start.sh"
NOT_SET_UP = "This kit is not set up yet. Say: set me up.\n"


def start(me: Path | None, cwd=None) -> subprocess.CompletedProcess:
    env = {"PATH": "/usr/bin:/bin", "HOME": str(cwd or ROOT)}
    if me is not None:
        env["KIT_ME"] = str(me)
    return subprocess.run(["bash", str(START)], env=env, cwd=cwd, capture_output=True, text=True)


def test_session_start_is_executable_and_quiet_about_nothing():
    assert START.stat().st_mode & 0o111


@pytest.mark.parametrize("how", ["no folder", "empty folder", "not finished", "unreadable profile"])
def test_session_start_says_when_the_kit_is_not_set_up(tmp_path, how):
    me = tmp_path / "me"
    if how != "no folder":
        me.mkdir()
    if how == "not finished":
        (me / "profile.json").write_text('{"version": 1, "onboarded": false}')
    if how == "unreadable profile":
        (me / "profile.json").write_text("not json")
    done = start(me)
    assert (done.returncode, done.stdout, done.stderr) == (0, NOT_SET_UP, "")


def test_session_start_finds_the_default_me_folder_beside_the_script(tmp_path):
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "session_start.sh").write_text(text(START))
    done = subprocess.run(
        ["bash", str(tmp_path / "bin" / "session_start.sh")],
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
    )
    assert done.stdout == NOT_SET_UP
    (tmp_path / "me").mkdir()
    (tmp_path / "me" / "profile.json").write_text('{"onboarded":true}')
    done = subprocess.run(
        ["bash", str(tmp_path / "bin" / "session_start.sh")],
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
    )
    assert (done.returncode, done.stdout) == (0, "")


def test_session_start_is_silent_when_set_up_and_nothing_has_been_learned(example_me):
    done = start(example_me)
    assert (done.returncode, done.stdout, done.stderr) == (0, "", "")


def test_session_start_shows_the_last_five_lessons(example_me, monkeypatch):
    for day in range(1, 8):
        monkeypatch.setattr(rules, "_now", lambda day=day: datetime(2026, 10, day, 9, 30))
        rules.add("learnings", f"Rule number {day}. It has a second sentence.")

    done = start(example_me)

    assert done.returncode == 0 and done.stderr == ""
    lines = done.stdout.splitlines()
    assert lines[0] == "Recent lessons from the creator's corrections:"
    assert lines[1:] == [f"- 2026-10-0{day} 09:30 {MIDDLE_DOT} Rule number {day}." for day in (3, 4, 5, 6, 7)]


def test_session_start_skips_lessons_that_were_taken_back(example_me, monkeypatch):
    for day in range(1, 5):
        monkeypatch.setattr(rules, "_now", lambda day=day: datetime(2026, 10, day, 9, 30))
        rules.add("learnings", f"Rule number {day}.")
    rules.undo("learnings")  # takes back rule 4

    done = start(example_me)

    assert [line for line in done.stdout.splitlines()[1:]] == [
        f"- 2026-10-0{day} 09:30 {MIDDLE_DOT} Rule number {day}." for day in (1, 2, 3)
    ]


# --- the adapter check --------------------------------------------------------------------------------------------------

CHECK = ROOT / "bin" / "check_adapters.sh"

SKILL_TEXT = """---
name: {name}
description: Does one job for the creator. Use when they ask for it.
---

# {name}

1. Read the creator's rules with `kit.rules.read("{name}")`.

## Feedback

Record a correction with `kit.rules.add("{name}", "<rule>")`.
"""


def tree(root: Path, skills: dict, *, link: str | None = "../.claude/skills") -> Path:
    for name, body in skills.items():
        folder = root / ".claude" / "skills" / name
        folder.mkdir(parents=True)
        if body is not None:
            (folder / "SKILL.md").write_text(body.replace("{name}", name))
    (root / ".claude" / "skills").mkdir(parents=True, exist_ok=True)
    if link is not None:
        (root / ".agents").mkdir()
        (root / ".agents" / "skills").symlink_to(link)
    return root


def adapters(root: Path) -> tuple[int, str]:
    done = subprocess.run(["bash", str(CHECK), str(root)], capture_output=True, text=True)
    return done.returncode, done.stdout + done.stderr


def test_the_adapter_check_passes_on_a_good_tree(tmp_path):
    root = tree(tmp_path, {"alpha": SKILL_TEXT, "beta": SKILL_TEXT})
    code, out = adapters(root)
    assert (code, out) == (0, "Adapters look right: .agents/skills leads to .claude/skills, and 2 skills are set up alike.\n")


def test_the_adapter_check_passes_on_this_repo():
    code, out = adapters(ROOT)
    assert code == 0, out
    done = subprocess.run(["bash", str(CHECK)], cwd=ROOT, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr


def test_a_block_description_counts(tmp_path):
    block = "---\nname: alpha\ndescription: |\n  Two lines\n  of description.\nlicense: MIT\n---\n"
    body = block + "\n`kit.rules.read(\"alpha\")`\n\n## Feedback\n"
    code, out = adapters(tree(tmp_path, {"alpha": body}))
    assert code == 0, out


def test_the_humanizer_is_exempt_from_the_rules_and_feedback_checks(tmp_path):
    plain_skill = "---\nname: humanizer\ndescription: Rewrites text.\n---\n\n# Humanizer\n"
    code, out = adapters(tree(tmp_path, {"humanizer": plain_skill, "alpha": SKILL_TEXT}))
    assert code == 0, out


def test_a_missing_link_is_reported(tmp_path):
    code, out = adapters(tree(tmp_path, {"alpha": SKILL_TEXT}, link=None))
    assert code == 1
    assert ".agents/skills is missing" in out and "ln -s ../.claude/skills .agents/skills" in out


def test_a_link_to_the_wrong_place_is_reported(tmp_path):
    (tmp_path / "elsewhere").mkdir()
    code, out = adapters(tree(tmp_path, {"alpha": SKILL_TEXT}, link="../elsewhere"))
    assert code == 1 and ".agents/skills does not lead to .claude/skills" in out


def test_a_copy_instead_of_a_link_is_reported(tmp_path):
    root = tree(tmp_path, {"alpha": SKILL_TEXT}, link=None)
    (root / ".agents" / "skills").mkdir(parents=True)
    code, out = adapters(root)
    assert code == 1 and ".agents/skills is a folder, not a link" in out


def test_a_skill_folder_without_a_skill_file_is_reported(tmp_path):
    code, out = adapters(tree(tmp_path, {"alpha": SKILL_TEXT, "empty": None}))
    assert code == 1 and ".claude/skills/empty has no SKILL.md" in out


BROKEN = {
    "no frontmatter": ("# alpha\n\n`kit.rules.read(\"alpha\")`\n\n## Feedback\n", "has no frontmatter"),
    "never closed": ("---\nname: alpha\ndescription: x\n\n`kit.rules.read(`\n## Feedback\n", "never closes"),
    "no name": ("---\ndescription: x\n---\n`kit.rules.read(`\n## Feedback\n", "has no name"),
    "empty name": ("---\nname:\ndescription: x\n---\n`kit.rules.read(`\n## Feedback\n", "has no name"),
    "no description": ("---\nname: alpha\n---\n`kit.rules.read(`\n## Feedback\n", "has no description"),
    "empty description": ("---\nname: alpha\ndescription:\n---\n`kit.rules.read(`\n## Feedback\n", "has no description"),
    "empty block description": (
        "---\nname: alpha\ndescription: |\nlicense: MIT\n---\n`kit.rules.read(`\n## Feedback\n",
        "has no description",
    ),
    "never reads the rules": ("---\nname: alpha\ndescription: x\n---\n\n## Feedback\n", "kit.rules.read("),
    "no feedback heading": ("---\nname: alpha\ndescription: x\n---\n\n`kit.rules.read(`\n\nFeedback is welcome.\n", "Feedback section"),
}


@pytest.mark.parametrize("name", list(BROKEN))
def test_each_skill_problem_is_reported_and_fails_the_check(tmp_path, name):
    body, words = BROKEN[name]
    code, out = adapters(tree(tmp_path, {"alpha": body}))
    assert code == 1
    assert ".claude/skills/alpha/SKILL.md" in out and words in out
    assert out.splitlines()[0] == "Found 1 problem:"


def test_all_the_problems_are_listed_not_just_the_first(tmp_path):
    root = tree(
        tmp_path,
        {"alpha": BROKEN["no name"][0], "beta": BROKEN["no feedback heading"][0], "empty": None, "ok": SKILL_TEXT},
        link=None,
    )
    code, out = adapters(root)
    assert code == 1
    lines = out.splitlines()
    assert lines[0] == "Found 4 problems:"
    assert len([line for line in lines if line.startswith("- ")]) == 4
    for words in (".agents/skills is missing", "alpha/SKILL.md has no name", "beta/SKILL.md has no Feedback section", "empty has no SKILL.md"):
        assert words in out


def test_a_feedback_heading_can_be_at_any_level(tmp_path):
    body = "---\nname: alpha\ndescription: x\n---\n`kit.rules.read(`\n\n### Feedback and rules\n"
    assert adapters(tree(tmp_path, {"alpha": body}))[0] == 0


# --- the files that tell an agent what to do ----------------------------------------------------------------------------


def test_claude_md_covers_what_the_kit_is_and_the_first_run_rule():
    claude = text(ROOT / "CLAUDE.md")
    assert claude.startswith("# Content Intelligence Kit")
    assert "kit.config.load_profile()" in claude and "NotOnboarded" in claude
    assert 'say: "set me up"' in claude.lower()
    assert "offer onboarding before anything else" in claude
    assert "bash bin/session_start.sh" in claude, "the kit sets up no hooks for Codex, so it runs the script itself"
    assert "uv run python -c" not in claude, "setup comes from the session-start notice, not a command each session"
    assert "ProfileError" in claude


def test_claude_md_routes_what_the_creator_says_to_skills():
    claude = text(ROOT / "CLAUDE.md")
    table = [line for line in claude.splitlines() if line.startswith("|") and not set(line) <= set("|- ")]
    assert table[0].startswith("| They say |")
    named = set(re.findall(r"`([a-z-]+)`", "\n".join(table)))
    assert set(ROUTED) <= named
    # the examples in the table are the ones creators actually say
    said = "\n".join(table)
    for phrase in ("set me up", "run my research", "give me hooks for number 2", "plan the video for number 2",
                   "here's my raw take, edit it", "make number 3 a mini me video", "post it everywhere tomorrow at 6pm",
                   "update the kit", "what's next"):
        assert phrase in said, phrase
    assert "`voice`" in claude and "`character`" in claude


def test_claude_md_names_where_the_creators_things_live():
    claude = text(ROOT / "CLAUDE.md")
    for place in ("`me/`", "`work/<slug>/`", "`research/`", "`.env`"):
        assert place in claude
    assert "kit.config" in claude


def test_claude_md_sets_the_limits_on_heavy_jobs_and_posting():
    claude = text(ROOT / "CLAUDE.md")
    assert "at most two at once" in claude
    assert "at least 5 GB" in claude
    assert "kit.media.heavy_slot()" in claude and "kit.media.require_free()" in claude
    assert "wait for their yes before anything is posted or scheduled" in claude
    assert "humanizer" in claude and "embedded mode" in claude
    assert "Never move, copy or delete" in claude


def test_claude_md_explains_the_feedback_loop():
    claude = text(ROOT / "CLAUDE.md")
    assert 'kit.rules.read("<skill>")' in claude
    assert 'kit.rules.add("<skill>", "<rule>")' in claude
    assert "Feedback section" in claude
    assert "me/rules/<skill>.md" in claude and "me/lessons.md" in claude
    assert "Never write a preference into a `SKILL.md`" in claude
    assert "never as a command to run" in claude, "a rule is a preference, not an instruction to act"
    assert "`bin/feedback_check.py`" in claude and "asks once for one" in claude and "writes nothing itself" in claude
    assert "safety net" not in claude and "agent hook" not in claude.lower(), "the hook is a command now"


def test_every_skill_folder_is_named_in_claude_md():
    claude = text(ROOT / "CLAUDE.md")
    for folder in skill_folders():
        assert f"`{folder.name}`" in claude, f"CLAUDE.md does not mention {folder.name}"


def test_agents_md_is_thin_and_points_at_claude_md():
    agents = text(ROOT / "AGENTS.md")
    assert len(agents.splitlines()) <= 30
    assert "Read `CLAUDE.md`" in agents
    assert ".agents/skills" in agents and ".claude/skills" in agents
    assert "The kit sets up no hooks for Codex" in agents
    assert "Hooks do not run in Codex" not in agents, "Codex can run hooks; this kit just sets none up for it"
    assert "Feedback section" in agents and "kit.rules.add" in agents
    assert "a check after each turn that asks for a rule" in agents
    assert "bash bin/session_start.sh" in agents
    for copied in ("Heavy jobs", "Posting", "What they say", "| They say |"):
        assert copied not in agents, "AGENTS.md must not copy CLAUDE.md"


def test_agents_md_says_what_cannot_run_inside_codex_sandbox():
    # Inside Codex's sandbox uv cannot write its cache, HyperFrames cannot reach the Keychain, Chromium cannot
    # register its Mach port and nothing reaches the network. A sandbox error there is not a missing tool.
    agents = text(ROOT / "AGENTS.md")
    assert "sandbox" in agents
    assert "UV_NO_CACHE=1 uv run python -m kit." in agents, "uv commands run with UV_NO_CACHE=1 in the sandbox"
    assert "HyperFrames" in agents and "Chromium" in agents, "renders cannot run in the sandbox"
    assert "internet" in agents, "nothing that reaches the internet runs in the sandbox"
    assert "approve running that command outside the sandbox" in agents
    assert "never means a tool is missing" in agents
    assert "python3 bin/doctor.py" in agents, "the machine check says which rows it could not check"
    # UV_NO_CACHE=1 works because the sandbox lets uv make a folder in the temporary folder, so say so.
    assert "can write only in this folder and the temporary folder" in agents


def test_agents_md_says_what_to_do_when_codex_cannot_ask_for_approval():
    # A headless codex exec run has no way to ask, and one such run reached for computer use to open Terminal.
    agents = text(ROOT / "AGENTS.md")
    line = next(line for line in agents.splitlines() if "cannot ask" in line)
    assert "`codex exec`" in line
    for ruled_out in ("another way out of the sandbox", "computer use", "no other app", "no other tool"):
        assert ruled_out in line, ruled_out
    assert "Terminal" in line and "`python3 bin/doctor.py`" in line and "HyperFrames" in line and "Chromium" in line


# --- the three skills this task adds --------------------------------------------------------------------------------------


def frontmatter(path: Path) -> dict:
    block = text(path).split("---\n")[1]
    return dict(re.findall(r"^(\w+):\s*(.+)$", block, re.M))


@pytest.mark.parametrize("name", OWN_SKILLS)
def test_each_new_skill_follows_the_shared_contract(name):
    path = SKILLS / name / "SKILL.md"
    meta = frontmatter(path)
    assert meta["name"] == name
    assert len(meta["description"]) > 80 and "Use when" in meta["description"]
    body = text(path)
    assert f'kit.rules.read("{name}")' in body
    assert re.search(r"^## Feedback$", body, re.M)
    assert re.findall(r"^## (.+)$", body, re.M)[-1] == "Feedback", "Feedback is the last section"
    # reading the creator's rules is the first numbered step
    first_step = re.search(r"^1\. (.+)$", body, re.M).group(1)
    assert "kit.rules.read" in first_step
    feedback = body[body.index("## Feedback") :]
    assert f'kit.rules.add("{name}"' in feedback and f"python -m kit.rules add {name}" in feedback


def test_every_skill_folder_name_matches_its_name_field():
    for folder in skill_folders():
        assert frontmatter(folder / "SKILL.md")["name"] == folder.name


def test_every_rules_command_named_in_the_docs_exists():
    sources = [ROOT / "CLAUDE.md", ROOT / "AGENTS.md", *[f / "SKILL.md" for f in skill_folders() if f.name != "humanizer"]]
    seen = set()
    for source in sources:
        for command in re.findall(r"python -m kit\.rules (\w+)", text(source)):
            assert command in RULES_COMMANDS, f"{source.name} names a rules command that does not exist: {command}"
            seen.add(command)
    assert {"read", "add", "undo", "remove", "recent"} <= seen


def test_every_update_option_named_in_the_docs_exists():
    sources = [ROOT / "CLAUDE.md", *[f / "SKILL.md" for f in skill_folders() if f.name != "humanizer"]]
    for source in sources:
        for line in text(source).splitlines():
            if "update_kit.py" in line:
                for flag in re.findall(r"--[a-z-]+", line):
                    assert flag in UPDATE_FLAGS, f"{source.name} names an update option that does not exist: {flag}"


def test_update_kit_skill_runs_the_script_in_check_then_apply_order():
    body = text(SKILLS / "update-kit" / "SKILL.md")
    assert body.index("uv run python bin/update_kit.py\n") < body.index("uv run python bin/update_kit.py --apply")
    for words in ("me/kit-backups/", "--from", "--ref", "bash bin/check_adapters.sh", "uv sync"):
        assert words in body


def test_update_kit_skill_keeps_the_groups_that_are_installed_when_it_syncs():
    body = text(SKILLS / "update-kit" / "SKILL.md")
    assert "`uv sync --group video --group research --inexact`" in body
    assert "add `--group character`" in body and "mini me character" in body
    assert "without it uv removes the groups the command does not name" in body


def test_update_kit_skill_sends_git_clones_to_git_pull_and_explains_what_is_kept():
    body = text(SKILLS / "update-kit" / "SKILL.md")
    assert "`git pull`" in body and "gitignored" in body
    assert body.index("git pull") < body.index("uv run python bin/update_kit.py\n"), "the clone check comes first"
    for words in ("Kept your edits", "stays as it is", "stays deleted", "unless the release changed"):
        assert words in body, words
    assert "replaced even when the release did not change it" not in body, "the old policy is gone"
    claude = text(ROOT / "CLAUDE.md")
    assert "`git pull`" in claude and "me/kit-backups/" in claude and "unless the release changed it too" in claude


def test_only_a_clone_whose_origin_is_the_kit_updates_with_git_pull():
    """A copy made with "Use this template" has git history, but its origin is the creator's own repository,
    so a pull there never brings a release. The origin decides, not the .git folder."""
    body = text(SKILLS / "update-kit" / "SKILL.md")
    step = re.search(r"^2\. (.+?)(?=^3\. )", body, re.M | re.S).group(1)
    assert "`git remote get-url origin`" in step
    assert "https://github.com/" in step and "/content-intelligence-kit" in step, "the kit's address to compare with"
    assert "Use this template" in step and "origin" in step
    assert "If the folder has a `.git` folder, it is a git clone" not in body, "a .git folder alone is not the test"
    assert body.index("git remote get-url origin") < body.index("uv run python bin/update_kit.py\n")
    claude = text(ROOT / "CLAUDE.md")
    updating = claude[claude.index("## Updating") :]
    assert "`git remote get-url origin`" in updating and "Use this template" in updating
    assert "A kit cloned with git updates with `git pull`" not in claude


def test_update_kit_skill_says_what_to_do_when_a_pull_refuses():
    body = text(SKILLS / "update-kit" / "SKILL.md")
    step = re.search(r"^2\. (.+?)(?=^3\. )", body, re.M | re.S).group(1)
    assert "If `git pull` refuses" in step
    for words in ("me/kit-backups/", "git checkout --", "again", "rules"):
        assert words in step, words
    assert "untracked file" in step and "move that file into the same backup folder" in step, (
        "a file of the creator's where the release adds one has no kit version to check out"
    )
    assert "git pull --ff-only" in step, "a pull never makes a merge on its own"
    assert "uv sync --group video --group research --inexact" in step and "npm ci" in step, "a pull can change the dependencies"


def test_shell_examples_that_add_a_rule_never_use_double_quotes_and_claude_md_uses_standard_input():
    sources = [ROOT / "CLAUDE.md", ROOT / "AGENTS.md", *[f / "SKILL.md" for f in skill_folders() if f.name != "humanizer"]]
    for source in sources:
        doubled = re.findall(r"python -m kit\.rules add \S+ \"", text(source))
        assert doubled == [], f"{source.name} quotes a rule with double quotes in a shell example"
    claude = text(ROOT / "CLAUDE.md")
    assert "python -m kit.rules add <skill> - <<'RULE'" in claude and "\n   RULE\n" in claude
    assert "python -m kit.rules add <skill> '<rule>'" not in claude, "an apostrophe in the rule would end the quote"
    assert "300 characters" in claude


def test_every_feedback_section_sends_the_rule_on_standard_input():
    """A rule often has an apostrophe in it ("don't", "the creator's"), which ends a single-quoted shell word early.
    On standard input, behind a quoted end marker, every character of the rule stays as typed."""
    for folder in skill_folders():
        if folder.name == "humanizer":
            continue
        body = text(folder / "SKILL.md")
        feedback = body[body.index("## Feedback") :]
        assert f"python -m kit.rules add {folder.name} - <<'RULE'\n<rule>\nRULE\n" in feedback, folder.name
        assert f"python -m kit.rules add {folder.name} '" not in feedback, f"{folder.name} still quotes the rule"


def test_learnings_skill_documents_adding_by_hand_with_its_limits():
    body = text(SKILLS / "learnings" / "SKILL.md")
    section = body[body.index("## Add a rule by hand") : body.index("## What this skill changes")]
    assert "python -m kit.rules add post 'Never use exclamation marks.'" in section
    assert "Single quotes keep `$` and backticks exactly as typed" in section
    assert "python -m kit.rules add post - <<'RULE'" in section and "\nRULE\n" in section, "the standard input form"
    for words in ("300 characters", "folder in `.claude/skills/`", "already recorded", "names the ones that are"):
        assert words in section, words


def test_next_never_starts_heavy_work_by_itself_even_when_told_to_continue():
    body = text(SKILLS / "next" / "SKILL.md")
    assert "without asking" not in body
    step = re.search(r"^3\. (.+)$", body, re.M).group(1)
    assert "If they said \"continue\" or \"keep going\", hand over right away for light work" in step
    assert "A render, an encode or a transcription still waits for their go-ahead" in step


def test_learnings_skill_covers_show_undo_and_prune():
    body = text(SKILLS / "learnings" / "SKILL.md")
    for words in ("read <skill> --numbered", "recent 10", "undo <skill>", "remove <skill> <number>",
                  "highest number down", "more than five rules", "The entry stays"):
        assert words in body, words
    for heading in ("## Show", "## Undo", "## Remove or tidy", "## Add a rule by hand"):
        assert heading in body, heading
    assert "prune" in body.lower()


# --- the next skill's state check ------------------------------------------------------------------------------------------------


def next_snippet() -> str:
    blocks = re.findall(r"```python\n(.*?)\n```", text(SKILLS / "next" / "SKILL.md"), re.S)
    assert len(blocks) == 1, "the skill carries exactly one script"
    return blocks[0]


def look(root: Path, **env) -> dict:
    done = subprocess.run(
        [sys.executable, "-I", "-W", "error", "-c", next_snippet()],
        cwd=root,
        env={"PATH": "/usr/bin:/bin", **env},
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr
    found = {}
    for line in done.stdout.splitlines():
        key, _, value = line.partition(": ")
        found.setdefault(key, []).append(value)
    return {key: (values if key == "warning" else values[0]) for key, values in found.items()}


def make_video(root: Path, slug: str, entries: list[str]) -> Path:
    folder = root / "work" / slug
    folder.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        if entry.endswith("/"):
            (folder / entry).mkdir(parents=True, exist_ok=True)
        elif entry.startswith("take/"):
            (folder / "take").mkdir(exist_ok=True)
            source = root / "raw.mov"
            source.write_bytes(b"raw")
            (folder / entry).symlink_to(source)
        else:
            (folder / entry).write_text("{}")
    return folder


STATES = [
    ([], "no_idea", "nothing"),
    (["idea.json"], "needs_plan", "idea.json"),
    (["idea.json", "plan.md"], "needs_take", "idea.json, plan.md"),
    (["idea.json", "plan.md", "take/"], "needs_take", "idea.json, plan.md, take"),
    (["idea.json", "plan.md", "take/take-01.mov"], "take_ready", "idea.json, plan.md, take"),
    (["idea.json", "script.md"], "mini_me_in_progress", "idea.json, script.md"),
    (["idea.json", "plan.md", "take/take-01.mov", "final.mp4"], "final_ready", "idea.json, plan.md, take, final.mp4"),
    (["idea.json", "script.md", "final.mp4"], "final_ready", "idea.json, script.md, final.mp4"),
    (["idea.json", "script.md", "final.mp4", "captions.json"], "captions_ready", "idea.json, script.md, final.mp4, captions.json"),
    (
        # a post folder with nothing in it is not a posted video: only a receipt for a platform counts
        ["idea.json", "script.md", "final.mp4", "captions.json", "post/"],
        "captions_ready",
        "idea.json, script.md, final.mp4, captions.json, post",
    ),
]


@pytest.mark.parametrize("entries, state, found", STATES, ids=[s[1] + ":" + str(len(s[0])) for s in STATES])
def test_next_names_the_state_from_what_exists(tmp_path, entries, state, found):
    make_video(tmp_path, "example-reel", entries)
    seen = look(tmp_path)
    assert (seen["video"], seen["state"], seen["found"]) == ("example-reel", state, found)


def test_next_with_no_videos_and_no_research(tmp_path):
    assert look(tmp_path) == {"video": "none", "state": "no_video", "found": "nothing"}
    (tmp_path / "work").mkdir()
    assert look(tmp_path)["state"] == "no_video"


def test_next_counts_the_ideas_in_the_latest_research_run(tmp_path):
    for day, count in (("2026-10-27", 3), ("2026-10-28", 4)):
        run = tmp_path / "research" / "runs" / day
        run.mkdir(parents=True)
        (run / "ideas.json").write_text(json.dumps([{"n": n} for n in range(1, count + 1)]))
    assert look(tmp_path)["ideas"] == "4 in research/runs/2026-10-28"

    (tmp_path / "research" / "runs" / "2026-10-29").mkdir()
    (tmp_path / "research" / "runs" / "2026-10-29" / "ideas.json").write_text("not json")
    assert "ideas" not in look(tmp_path)


def test_next_reads_the_idea_when_it_can(tmp_path):
    folder = make_video(tmp_path, "example-reel", [])
    (folder / "idea.json").write_text(json.dumps({"n": 2, "title": "Why windowsill basil dies"}))
    seen = look(tmp_path)
    assert seen["idea"] == "2, Why windowsill basil dies"
    (folder / "idea.json").write_text("not json")
    assert "idea" not in look(tmp_path)


def test_next_looks_at_the_video_touched_most_recently(tmp_path):
    older = make_video(tmp_path, "older-reel", ["idea.json"])
    newer = make_video(tmp_path, "newer-reel", ["idea.json", "plan.md"])
    (tmp_path / "work" / ".slots").mkdir()
    for path, when in ((older, 1_700_000_000), (newer, 1_700_100_000)):
        for item in [path, *path.iterdir()]:
            os.utime(item, (when, when))
    os.utime(tmp_path / "work" / ".slots", (1_800_000_000, 1_800_000_000))
    assert look(tmp_path)["video"] == "newer-reel"

    # touching a file inside the other folder makes that one the newest
    os.utime(older / "idea.json", (1_700_200_000, 1_700_200_000))
    assert look(tmp_path)["video"] == "older-reel"


def test_next_can_be_pointed_at_another_video(tmp_path):
    make_video(tmp_path, "older-reel", ["idea.json"])
    make_video(tmp_path, "newer-reel", ["idea.json", "plan.md"])
    assert look(tmp_path, SLUG="older-reel")["state"] == "needs_plan"
    seen = look(tmp_path, SLUG="nothing-by-that-name")
    assert (seen["video"], seen["state"]) == ("nothing-by-that-name", "no_such_video")


def test_next_warns_about_a_take_link_that_points_nowhere(tmp_path):
    folder = make_video(tmp_path, "example-reel", ["idea.json", "plan.md"])
    (folder / "take").mkdir()
    (folder / "take" / "take-01.mov").symlink_to(tmp_path / "moved-away.mov")
    seen = look(tmp_path)
    assert seen["state"] == "take_ready"
    assert seen["warning"] == ["take/take-01.mov points to a file that is not there any more"]


# A post folder keeps more than live posts: the upload record (media.json), the file for posting by hand
# (manual.md) and every receipt that no longer applies, renamed to <platform>.cancelled.json or
# <platform>.stale.json. Only a live receipt, <platform>.json, says something was sent.

READY = ["idea.json", "script.md", "final.mp4", "captions.json"]
SENT = {"slug": "example-reel", "platform": "instagram", "create_attempted": True, "post_id": "not-a-real-id"}


def write_post(folder: Path, files: dict) -> None:
    (folder / "post").mkdir(exist_ok=True)
    for name, body in files.items():
        (folder / "post" / name).write_text(body if isinstance(body, str) else json.dumps(body))


def test_a_post_folder_holding_only_the_upload_is_not_a_posted_video(tmp_path):
    write_post(make_video(tmp_path, "example-reel", READY), {"media.json": {"url": "https://example.com/v.mp4"}})
    seen = look(tmp_path)
    assert seen["state"] == "captions_ready"
    assert "receipts" not in seen


def test_a_post_that_was_cancelled_is_not_posted_any_more(tmp_path):
    write_post(
        make_video(tmp_path, "example-reel", READY),
        {"media.json": {"url": "https://example.com/v.mp4"}, "instagram.cancelled.json": SENT},
    )
    seen = look(tmp_path)
    assert seen["state"] == "captions_ready"
    assert seen["receipts"] == "instagram cancelled"


def test_every_platform_cancelled_still_leaves_the_video_unposted(tmp_path):
    files = {f"{name}.cancelled.json": SENT for name in ("instagram", "tiktok", "youtube")}
    write_post(make_video(tmp_path, "example-reel", READY), files)
    seen = look(tmp_path)
    assert seen["state"] == "captions_ready"
    assert seen["receipts"] == "instagram cancelled, tiktok cancelled, youtube cancelled"


def test_a_receipt_with_a_post_id_means_posted(tmp_path):
    write_post(make_video(tmp_path, "example-reel", READY), {"instagram.json": SENT})
    seen = look(tmp_path)
    assert seen["state"] == "posted"
    assert seen["receipts"] == "instagram scheduled"


def test_cancelling_one_platform_leaves_the_others_posted(tmp_path):
    write_post(
        make_video(tmp_path, "example-reel", READY),
        {"tiktok.json": SENT, "instagram.cancelled.json": SENT, "media.json": {}},
    )
    seen = look(tmp_path)
    assert seen["state"] == "posted"
    assert seen["receipts"] == "instagram cancelled, tiktok scheduled", "in the order the kit lists its platforms"


def test_a_new_receipt_wins_over_an_older_cancelled_one(tmp_path):
    write_post(make_video(tmp_path, "example-reel", READY), {"instagram.json": SENT, "instagram.cancelled.json": SENT})
    seen = look(tmp_path)
    assert (seen["state"], seen["receipts"]) == ("posted", "instagram scheduled")


def test_a_post_zernio_lost_is_not_posted(tmp_path):
    write_post(make_video(tmp_path, "example-reel", READY), {"instagram.stale.json": SENT})
    seen = look(tmp_path)
    assert (seen["state"], seen["receipts"]) == ("captions_ready", "instagram gone")


def test_an_attempt_nobody_confirmed_is_not_called_posted(tmp_path):
    interrupted = {"slug": "example-reel", "platform": "instagram", "create_attempted": True, "request_id": "not-a-real-id"}
    write_post(make_video(tmp_path, "example-reel", READY), {"instagram.json": interrupted})
    seen = look(tmp_path)
    assert seen["state"] == "post_unsure"
    assert seen["receipts"] == "instagram unconfirmed"


def test_a_damaged_receipt_is_not_called_posted(tmp_path):
    write_post(make_video(tmp_path, "example-reel", READY), {"instagram.json": "not json at all"})
    seen = look(tmp_path)
    assert (seen["state"], seen["receipts"]) == ("post_unsure", "instagram unreadable")


def test_one_confirmed_post_beats_an_unconfirmed_one_and_the_warning_says_which(tmp_path):
    interrupted = {"create_attempted": True, "request_id": "not-a-real-id"}
    write_post(make_video(tmp_path, "example-reel", READY), {"instagram.json": SENT, "tiktok.json": interrupted})
    seen = look(tmp_path)
    assert seen["state"] == "posted"
    assert seen["receipts"] == "instagram scheduled, tiktok unconfirmed"
    assert seen["warning"] == ["post/tiktok.json shows an attempt Zernio never confirmed"]


def test_the_file_for_posting_by_hand_is_not_a_posted_video(tmp_path):
    write_post(make_video(tmp_path, "example-reel", READY), {"manual.md": "# Post example-reel by hand\n"})
    seen = look(tmp_path)
    assert seen["state"] == "captions_ready"
    assert seen["manual"] == "post/manual.md"
    assert "receipts" not in seen


def test_receipts_are_ignored_on_a_video_that_has_no_final(tmp_path):
    write_post(make_video(tmp_path, "example-reel", ["idea.json", "script.md"]), {"instagram.json": SENT})
    assert look(tmp_path)["state"] == "mini_me_in_progress"


def test_next_does_not_mistake_the_setup_test_folder_for_a_video(tmp_path):
    # setup keeps its test take and test clips in work/onboarding-test, which is newer than any real video
    video = make_video(tmp_path, "example-reel", ["idea.json"])
    scratch = make_video(tmp_path, "onboarding-test", ["take/"])
    os.utime(video, (1_700_000_000, 1_700_000_000))
    os.utime(scratch, (1_800_000_000, 1_800_000_000))
    assert look(tmp_path)["video"] == "example-reel"
    assert look(tmp_path, SLUG="onboarding-test")["video"] == "onboarding-test", "still reachable by name"

    only_scratch = tmp_path / "second"
    make_video(only_scratch, "onboarding-test", [])
    assert look(only_scratch)["state"] == "no_video"


def test_next_names_the_same_platforms_as_the_kit():
    import ast

    from kit import config

    listed = re.search(r"PLATFORMS = (\([^)]*\))", next_snippet())
    assert listed, "the script lists the platforms a receipt can be for"
    assert tuple(ast.literal_eval(listed.group(1))) == config.PLATFORMS


def test_every_state_the_script_can_print_is_in_the_table_and_the_other_way_round():
    body = text(SKILLS / "next" / "SKILL.md")
    states = set(re.findall(r'state = "(\w+)"', next_snippet()))
    table = set(re.findall(r"^\| `(\w+)` \|", body, re.M))
    assert states == table, states ^ table


# --- the kit's own prose ---------------------------------------------------------------------------------------------------------


INTERNAL_WORDS = ["rul" + "ing", "contro" + "ller", "release " + "scan", "house " + "brand", "plan " + "task"]
MINE = [
    "CLAUDE.md",
    "AGENTS.md",
    ".claude/settings.json",
    ".claude/skills/learnings/SKILL.md",
    ".claude/skills/next/SKILL.md",
    ".claude/skills/update-kit/SKILL.md",
    "bin/session_start.sh",
    "bin/feedback_check.py",
    "bin/check_adapters.sh",
    "bin/update_kit.py",
    "kit/rules.py",
]


@pytest.mark.parametrize("path", MINE)
def test_authored_files_have_no_em_or_en_dash_and_no_machine_path(path):
    content = text(ROOT / path)
    assert EM_DASH not in content and EN_DASH not in content
    assert "/" + "Users" + "/" not in content


@pytest.mark.parametrize("path", MINE)
def test_authored_files_have_no_internal_process_words(path):
    content = text(ROOT / path).lower()
    for word in INTERNAL_WORDS:
        assert word not in content, f"{path} contains {word!r}"


def test_claude_md_says_the_kit_sets_up_no_hooks_for_codex():
    """Codex can run hooks. The kit gives it none, so its Feedback sections are what record corrections."""
    claude = text(ROOT / "CLAUDE.md")
    assert "The kit sets up no hooks for Codex" in claude
    assert "Codex has no hooks" not in claude
