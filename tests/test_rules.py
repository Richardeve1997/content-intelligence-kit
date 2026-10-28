"""kit.rules: the creator's rules and the journal of what was learned.

A correction becomes one line in me/rules/<skill>.md and one entry in me/lessons.md. A skill reads
its own rules file first on every run, so the rule shapes the next video without touching the skill.

Every test works on a throwaway copy of me.example/ (the `example_me` fixture), so nothing real is
read or written. The clock is replaced so dates and times are fixed.
"""

import io
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from kit import rules

ROOT = Path(__file__).resolve().parent.parent
MIDDLE_DOT = "·"
EM_DASH, EN_DASH = chr(0x2014), chr(0x2013)  # written as code points so this file holds no dash

HEADER = re.compile(rf"^## (\d{{4}}-\d{{2}}-\d{{2}} \d{{2}}:\d{{2}}) {MIDDLE_DOT} (.+)$", re.M)


SKILLS = ["learnings", "next", "post", "ui-story", "update-kit"]


@pytest.fixture(autouse=True)
def known_skills(tmp_path, monkeypatch):
    """A folder of skills for `add` to check names against, in place of the repo's own .claude/skills."""
    folder = tmp_path / "skills-folder"
    for name in SKILLS:
        (folder / name).mkdir(parents=True)
    monkeypatch.setattr(rules, "_skills_dir", lambda: folder)
    return folder


@pytest.fixture
def clock(monkeypatch):
    """Fix the time that entries are stamped with. A test moves it by changing clock['now']."""
    state = {"now": datetime(2026, 10, 28, 14, 5)}
    monkeypatch.setattr(rules, "_now", lambda: state["now"])
    return state


def journal(me: Path) -> str:
    return (me / "lessons.md").read_text(encoding="utf-8")


def headers(me: Path) -> list[str]:
    return [line[3:] for line in journal(me).splitlines() if line.startswith("## ")]


# --- the round trip -------------------------------------------------------------------------------


def test_rules_roundtrip(example_me, clock):
    assert rules.read("ui-story") == ""

    rules.add("ui-story", "Captions stay under 12 words per line.")

    assert rules.read("ui-story") == "- 2026-10-28: Captions stay under 12 words per line.\n"
    assert headers(example_me) == [f"2026-10-28 14:05 {MIDDLE_DOT} Captions stay under 12 words per line."]

    # a second rule is appended, and another skill keeps its own file
    clock["now"] = datetime(2026, 10, 29, 9, 30)
    rules.add("ui-story", "Open on the product, not on me.")
    rules.add("post", "Never put hashtags in the first line.")
    assert rules.read("ui-story") == (
        "- 2026-10-28: Captions stay under 12 words per line.\n- 2026-10-29: Open on the product, not on me.\n"
    )
    assert rules.read("post") == "- 2026-10-29: Never put hashtags in the first line.\n"
    assert len(headers(example_me)) == 3
    assert sorted(p.name for p in (example_me / "rules").iterdir()) == ["post.md", "ui-story.md"]


def test_read_returns_an_empty_string_when_there_is_no_file(example_me):
    assert not (example_me / "rules").exists()
    assert rules.read("ui-story") == ""
    (example_me / "rules").mkdir()
    assert rules.read("ui-story") == ""


def test_the_journal_entry_names_the_skill_and_keeps_the_whole_rule(example_me, clock):
    rule = "Keep hooks short. Say the number first, then the claim."
    rules.add("ui-story", rule)
    text = journal(example_me)
    assert f"## 2026-10-28 14:05 {MIDDLE_DOT} Keep hooks short.\n" in text
    assert "- Skill: ui-story\n" in text
    assert f"- Rule: {rule}\n" in text


def test_entries_are_separated_by_one_blank_line(example_me, clock):
    rules.add("post", "Rule one.")
    rules.add("post", "Rule two.")
    text = journal(example_me)
    assert "- Rule: Rule one.\n\n## 2026-10-28 14:05" in text
    assert not text.endswith("\n\n")


def test_nothing_the_kit_writes_contains_a_dash_character(example_me, clock):
    rules.add("post", "Write captions in the first person.")
    rules.add("ui-story", "Cut to the product within two seconds.")
    for text in (journal(example_me), rules.read("post"), rules.read("ui-story")):
        assert EM_DASH not in text
        assert EN_DASH not in text
    assert all(HEADER.fullmatch("## " + h) for h in headers(example_me))


def test_an_existing_file_without_a_final_newline_is_handled(example_me, clock):
    (example_me / "rules").mkdir()
    (example_me / "rules" / "post.md").write_text("- 2026-01-01: Written by hand.", encoding="utf-8")
    (example_me / "lessons.md").write_text("Notes the creator wrote.", encoding="utf-8")

    rules.add("post", "A new rule.")

    assert rules.read("post") == "- 2026-01-01: Written by hand.\n- 2026-10-28: A new rule.\n"
    text = journal(example_me)
    assert text.startswith("Notes the creator wrote.\n\n## 2026-10-28 14:05 ")


def test_the_rule_is_written_on_one_line(example_me, clock):
    rules.add("post", "Keep captions\nshort,   and\tplain.\n\n")
    assert rules.read("post") == "- 2026-10-28: Keep captions short, and plain.\n"


# --- the headline ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rule, headline",
    [
        ("Captions stay under 12 words per line.", "Captions stay under 12 words per line."),
        ("Keep hooks short. Say the number first, then the claim.", "Keep hooks short."),
        ("Always lead with the result! Then explain it.", "Always lead with the result!"),
        ("Can a stranger follow the hook? Test it on one.", "Can a stranger follow the hook?"),
        ("No full stop at the end", "No full stop at the end"),
        ("Use 12.5 seconds as the cap. Then cut.", "Use 12.5 seconds as the cap."),
        ("Use e.g. a number in the hook. It helps.", "Use e.g. a number in the hook."),
        ("keep it short. then keep it shorter.", "keep it short. then keep it shorter."),
    ],
)
def test_the_headline_is_the_first_sentence(example_me, clock, rule, headline):
    rules.add("post", rule)
    assert headers(example_me) == [f"2026-10-28 14:05 {MIDDLE_DOT} {headline}"]


def test_a_long_first_sentence_is_trimmed_at_a_word(example_me, clock):
    rule = (
        "Keep every caption short enough to read in one glance, which means no more than twelve words "
        "and never a second line that wraps onto the speaker's face."
    )
    rules.add("post", rule)
    headline = HEADER.search(journal(example_me)).group(2)
    stem = headline.removesuffix("...")
    assert headline.endswith("...")
    assert 60 <= len(stem) <= 80
    assert rule.startswith(stem)
    assert rule[len(stem)] == " ", "the headline must end on a whole word"
    assert "- Rule: " + rule in journal(example_me)


def test_one_enormous_word_is_cut_hard_rather_than_left_whole(example_me, clock):
    rules.add("post", "x" * 300)
    headline = HEADER.search(journal(example_me)).group(2)
    assert headline.endswith("...")
    assert len(headline) <= 83


# --- what is refused --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "skill",
    ["", "../etc/passwd", "a/b", "..", ".hidden", "Post", "ui story", "-post", "ui_story", "x" * 65, "post.md", None, 3],
)
def test_a_skill_name_that_could_leave_the_rules_folder_is_refused(example_me, skill):
    with pytest.raises(ValueError) as caught:
        rules.add(skill, "A rule.")
    assert "skill name" in str(caught.value)
    with pytest.raises(ValueError):
        rules.read(skill)
    assert not (example_me / "rules").exists()
    assert not (example_me / "lessons.md").exists()


@pytest.mark.parametrize("rule", ["", "   ", "\n\t", None, 12])
def test_an_empty_rule_is_refused(example_me, rule):
    with pytest.raises(ValueError) as caught:
        rules.add("post", rule)
    assert "rule" in str(caught.value)
    assert not (example_me / "rules").exists()
    assert not (example_me / "lessons.md").exists()


# --- what add will take --------------------------------------------------------------------------------------


def test_a_rule_can_be_300_characters_but_not_301(example_me, clock):
    rules.add("post", "a" * 300)
    assert rules.read("post") == f"- 2026-10-28: {'a' * 300}\n"

    with pytest.raises(ValueError) as caught:
        rules.add("ui-story", "a" * 301)
    assert str(caught.value) == (
        "A rule can be at most 300 characters, and this one has 301. Shorten it to one or two plain sentences."
    )
    assert rules.read("ui-story") == ""
    assert len(headers(example_me)) == 1, "a refused rule leaves no journal entry"


def test_the_length_is_counted_after_the_rule_is_put_on_one_line(example_me, clock):
    spaced = "word  " * 60  # 360 characters as typed, 299 once the spaces are tidied
    rules.add("post", spaced)
    assert len(rules.read("post").split(": ", 1)[1].strip()) == 299


def test_a_skill_without_a_folder_is_refused_and_the_known_skills_are_named(example_me, clock):
    with pytest.raises(ValueError) as caught:
        rules.add("captions", "Keep it short.")
    assert str(caught.value) == (
        "There is no skill called captions. The skills are: learnings, next, post, ui-story, update-kit."
    )
    assert not (example_me / "rules").exists() and not (example_me / "lessons.md").exists()


def test_only_folders_count_as_skills(example_me, clock, known_skills):
    (known_skills / "a-file").write_text("not a skill\n")
    (known_skills / ".hidden").mkdir()
    with pytest.raises(ValueError) as caught:
        rules.add("a-file", "Keep it short.")
    assert "a-file" not in str(caught.value).split("The skills are:")[1]
    assert ".hidden" not in str(caught.value)


def test_with_no_skills_at_all_the_error_says_so(example_me, clock, monkeypatch, tmp_path):
    monkeypatch.setattr(rules, "_skills_dir", lambda: tmp_path / "nothing-here")
    with pytest.raises(ValueError) as caught:
        rules.add("post", "Keep it short.")
    assert str(caught.value) == "There are no skills in .claude/skills yet."


def test_the_skills_folder_is_the_repos_own_by_default(monkeypatch):
    monkeypatch.undo()
    assert rules._skills_dir() == ROOT / ".claude" / "skills"
    assert "learnings" in rules._known_skills()


def test_rules_for_a_skill_that_no_longer_has_a_folder_can_still_be_read_and_removed(example_me, clock):
    (example_me / "rules").mkdir()
    (example_me / "rules" / "retired-skill.md").write_text("- 2026-01-01: Old rule.\n- 2026-01-02: Older rule.\n")
    assert rules.read("retired-skill").count("\n") == 2
    assert rules.numbered("retired-skill").startswith("1. 2026-01-01: Old rule.")
    assert rules.remove("retired-skill", 1) == "Old rule."
    assert rules.undo("retired-skill") == "Older rule."


def test_an_exact_duplicate_is_skipped(example_me, clock):
    rules.add("post", "Keep it plain.")
    before = (rules.read("post"), journal(example_me))

    rules.add("post", "Keep it plain.")
    rules.add("post", "  Keep   it\nplain.  ")  # the same rule once it is on one line

    assert (rules.read("post"), journal(example_me)) == before


def test_a_duplicate_is_only_a_duplicate_for_the_same_skill_and_the_same_words(example_me, clock):
    rules.add("post", "Keep it plain.")
    rules.add("ui-story", "Keep it plain.")
    rules.add("post", "Keep it plain!")
    rules.add("post", "keep it plain.")
    assert rules.read("ui-story").count("Keep it plain.") == 1
    assert rules.read("post").count("\n") == 3


def test_a_rule_can_be_added_again_after_it_was_taken_back(example_me, clock):
    rules.add("post", "Keep it plain.")
    rules.undo("post")
    rules.add("post", "Keep it plain.")
    assert rules.read("post") == "- 2026-10-28: Keep it plain.\n"
    assert len(headers(example_me)) == 2


def test_a_duplicate_written_by_hand_without_a_date_still_counts(example_me, clock):
    (example_me / "rules").mkdir()
    (example_me / "rules" / "post.md").write_text("- Keep it plain.\n")
    rules.add("post", "Keep it plain.")
    assert rules.read("post") == "- Keep it plain.\n"


# --- taking a rule back -----------------------------------------------------------------------------


def seed(clock):
    rules.add("post", "Never put hashtags in the first line.")
    clock["now"] = datetime(2026, 10, 29, 9, 30)
    rules.add("post", "Write captions in the first person.")
    rules.add("ui-story", "Keep it under 40 seconds.")


def test_undo_removes_the_newest_rule_and_marks_its_entry(example_me, clock):
    seed(clock)
    clock["now"] = datetime(2026, 11, 2, 8, 0)

    removed = rules.undo("post")

    assert removed == "Write captions in the first person."
    assert rules.read("post") == "- 2026-10-28: Never put hashtags in the first line.\n"
    assert rules.read("ui-story") == "- 2026-10-29: Keep it under 40 seconds.\n"
    assert headers(example_me) == [
        f"2026-10-28 14:05 {MIDDLE_DOT} Never put hashtags in the first line.",
        f"2026-10-29 09:30 {MIDDLE_DOT} Write captions in the first person. (removed 2026-11-02)",
        f"2026-10-29 09:30 {MIDDLE_DOT} Keep it under 40 seconds.",
    ]
    # the entry is kept: the journal is the record of what happened
    assert "- Rule: Write captions in the first person.\n" in journal(example_me)


def test_undo_twice_walks_back_through_the_rules(example_me, clock):
    seed(clock)
    assert rules.undo("post") == "Write captions in the first person."
    assert rules.undo("post") == "Never put hashtags in the first line."
    assert rules.read("post") == ""
    assert rules.undo("post") is None
    assert rules.read("ui-story") != ""


def test_undo_with_no_rules_changes_nothing(example_me, clock):
    assert rules.undo("post") is None
    assert not (example_me / "rules").exists()
    assert not (example_me / "lessons.md").exists()


def test_undo_marks_the_newest_matching_entry_when_a_rule_was_added_again_after_it_was_taken_back(example_me, clock):
    rules.add("post", "Keep it plain.")
    clock["now"] = datetime(2026, 10, 30, 7, 0)
    rules.undo("post")
    clock["now"] = datetime(2026, 10, 31, 9, 0)
    rules.add("post", "Keep it plain.")
    clock["now"] = datetime(2026, 11, 2, 8, 0)

    rules.undo("post")

    first, second = headers(example_me)
    assert first.endswith("(removed 2026-10-30)"), "the first removal keeps its own date"
    assert second.endswith("(removed 2026-11-02)")


def test_undo_of_a_rule_the_creator_wrote_by_hand_still_works(example_me, clock):
    (example_me / "rules").mkdir()
    (example_me / "rules" / "post.md").write_text("- Always say the price.\n", encoding="utf-8")

    assert rules.undo("post") == "Always say the price."
    assert rules.read("post") == ""
    assert not (example_me / "lessons.md").exists()


def test_undo_leaves_other_lines_of_the_file_alone(example_me, clock):
    (example_me / "rules").mkdir()
    (example_me / "rules" / "post.md").write_text(
        "Notes for myself.\n\n- 2026-01-01: First.\n- 2026-01-02: Second.\n", encoding="utf-8"
    )
    assert rules.undo("post") == "Second."
    assert rules.read("post") == "Notes for myself.\n\n- 2026-01-01: First.\n"


def test_remove_takes_out_a_numbered_rule(example_me, clock):
    for text in ("One.", "Two.", "Three."):
        rules.add("post", text)
    clock["now"] = datetime(2026, 11, 2, 8, 0)

    assert rules.remove("post", 2) == "Two."

    assert rules.read("post") == "- 2026-10-28: One.\n- 2026-10-28: Three.\n"
    marked = [h for h in headers(example_me) if "(removed 2026-11-02)" in h]
    assert len(marked) == 1 and marked[0].split(f"{MIDDLE_DOT} ")[1].startswith("Two.")


@pytest.mark.parametrize("number", [0, -1, 3, 99, "1", None])
def test_remove_refuses_a_number_that_is_not_a_rule(example_me, clock, number):
    rules.add("post", "One.")
    rules.add("post", "Two.")
    with pytest.raises(ValueError) as caught:
        rules.remove("post", number)
    assert "rule number" in str(caught.value)
    assert rules.read("post").count("\n") == 2


def test_remove_marks_the_entry_with_the_same_date_when_a_rule_appears_twice(example_me, clock):
    # add refuses a repeat, so this is the file as an older kit or the creator's own edits could leave it
    (example_me / "rules").mkdir()
    (example_me / "rules" / "post.md").write_text("- 2026-10-28: Keep it plain.\n- 2026-10-30: Keep it plain.\n")
    (example_me / "lessons.md").write_text(
        "## 2026-10-28 14:05 \u00b7 Keep it plain.\n\n- Skill: post\n- Rule: Keep it plain.\n\n"
        "## 2026-10-30 07:00 \u00b7 Keep it plain.\n\n- Skill: post\n- Rule: Keep it plain.\n"
    )
    clock["now"] = datetime(2026, 11, 2, 8, 0)

    rules.remove("post", 1)  # the older line, dated 2026-10-28

    first, second = headers(example_me)
    assert first.endswith("(removed 2026-11-02)")
    assert "(removed" not in second
    assert rules.read("post") == "- 2026-10-30: Keep it plain.\n"


# --- looking back ----------------------------------------------------------------------------------


def test_rule_lines_can_be_numbered(example_me, clock):
    rules.add("post", "One.")
    rules.add("post", "Two.")
    assert rules.numbered("post") == "1. 2026-10-28: One.\n2. 2026-10-28: Two.\n"
    assert rules.numbered("ui-story") == ""


def test_recent_lists_the_newest_lessons_first_and_skips_removed_ones(example_me, clock):
    for index in range(1, 8):
        clock["now"] = datetime(2026, 10, index, 12, 0)
        rules.add("post", f"Rule {index}.")
    clock["now"] = datetime(2026, 10, 20, 12, 0)
    rules.undo("post")  # removes rule 7

    lines = rules.recent()
    assert lines == [f"2026-10-0{n} 12:00 {MIDDLE_DOT} Rule {n}. (post)" for n in (6, 5, 4, 3, 2)]
    assert rules.recent(2) == lines[:2]
    assert rules.recent(0) == []


def test_recent_is_empty_without_a_journal(example_me):
    assert rules.recent() == []


# --- the command line -----------------------------------------------------------------------------------


def cli(argv, capsys):
    code = rules.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_cli_add_then_read(example_me, clock, capsys):
    code, out, err = cli(["add", "post", "Never", "put", "hashtags", "first."], capsys)
    assert (code, err) == (0, "")
    assert out == "Recorded for post: Never put hashtags first.\n"

    code, out, err = cli(["read", "post"], capsys)
    assert (code, err) == (0, "")
    assert out == "- 2026-10-28: Never put hashtags first.\n"

    code, out, _ = cli(["read", "post", "--numbered"], capsys)
    assert out == "1. 2026-10-28: Never put hashtags first.\n"

    code, out, _ = cli(["read", "ui-story"], capsys)
    assert (code, out) == (0, "")


def test_cli_says_when_a_rule_is_already_recorded(example_me, clock, capsys):
    assert cli(["add", "post", "Keep", "it", "plain."], capsys)[1] == "Recorded for post: Keep it plain.\n"
    code, out, err = cli(["add", "post", "Keep it plain."], capsys)
    assert (code, out, err) == (0, "Already recorded for post: Keep it plain.\n", "")
    assert rules.read("post").count("\n") == 1


def test_cli_add_takes_the_rule_from_standard_input(example_me, clock, capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO('Say "next steps" at the end.\n'))
    code, out, _ = cli(["add", "post", "-"], capsys)
    assert code == 0
    assert rules.read("post") == '- 2026-10-28: Say "next steps" at the end.\n'


def test_cli_undo_remove_and_recent(example_me, clock, capsys):
    cli(["add", "post", "One."], capsys)
    cli(["add", "post", "Two."], capsys)
    cli(["add", "post", "Three."], capsys)

    code, out, _ = cli(["undo", "post"], capsys)
    assert (code, out) == (0, "Removed from post: Three.\n")

    code, out, _ = cli(["remove", "post", "1"], capsys)
    assert (code, out) == (0, "Removed from post: One.\n")

    code, out, _ = cli(["recent", "2"], capsys)
    assert code == 0
    assert out.splitlines() == [
        f"2026-10-28 14:05 {MIDDLE_DOT} Two. (post)",
    ]

    code, out, _ = cli(["undo", "ui-story"], capsys)
    assert (code, out) == (0, "There are no rules for ui-story to remove.\n")


@pytest.mark.parametrize(
    "argv",
    [
        ["add", "Bad Skill", "A rule."],
        ["add", "captions", "A rule."],
        ["add", "post", "x" * 301],
        ["add", "post", "   "],
        ["read", "../x"],
        ["remove", "post", "7"],
        ["remove", "post", "two"],
    ],
)
def test_cli_problems_are_one_plain_sentence_and_exit_1(example_me, clock, capsys, argv):
    code, out, err = cli(argv, capsys)
    assert code == 1
    assert out == ""
    assert len(err.strip().splitlines()) == 1
    assert "Traceback" not in err
    assert EM_DASH not in err and EN_DASH not in err


def test_cli_without_a_command_shows_usage(capsys):
    with pytest.raises(SystemExit) as caught:
        rules.main([])
    assert caught.value.code == 2
    assert "usage" in capsys.readouterr().err.lower()


def test_the_module_runs_as_a_command(tmp_path):
    me = tmp_path / "me"
    me.mkdir()
    env = {"KIT_ME": str(me), "PATH": "/usr/bin:/bin"}

    def run(*args):
        return subprocess.run(
            [sys.executable, "-W", "error", "-m", "kit.rules", *args],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

    added = run("add", "learnings", "Keep it under 40 seconds.")
    assert added.returncode == 0, added.stderr
    assert "Recorded for learnings" in added.stdout
    read = run("read", "learnings")
    assert read.returncode == 0
    assert read.stdout.endswith(": Keep it under 40 seconds.\n")
    assert (me / "lessons.md").is_file()

    unknown = run("add", "no-such-skill", "Keep it short.")
    assert unknown.returncode == 1 and unknown.stdout == ""
    assert unknown.stderr.startswith("There is no skill called no-such-skill. The skills are: ")
    assert "learnings" in unknown.stderr and "Traceback" not in unknown.stderr
