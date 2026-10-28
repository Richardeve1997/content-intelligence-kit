"""The rules a creator teaches the kit, and the journal that records when and why.

When the creator corrects something and wants it to stick ("the captions are too long, fix that
going forward"), the correction becomes a rule:

* me/rules/<skill>.md holds one line per rule, such as "- 2026-10-28: Captions stay under 12 words."
  A skill reads its own file first on every run, so the next video follows the rule.
* me/lessons.md is the journal. Each rule adds an entry headed "## 2026-10-28 14:05 · <headline>",
  with the skill and the full text underneath. Entries are never deleted. When a rule is taken back,
  its entry stays and its headline gains "(removed 2026-11-02)".

Both files sit in the creator's me/ folder, so an update to the kit never touches them.

What add accepts: a rule of at most 300 characters (one or two plain sentences), for a skill that has a
folder in .claude/skills/. A rule that is already recorded for that skill, word for word, is skipped.

A skill cannot call Python directly, so everything here is also a command:

    python -m kit.rules read <skill> [--numbered]     print the skill's rules
    python -m kit.rules add <skill> <rule words>      record a rule ("-" reads it from standard input)
    python -m kit.rules undo <skill>                  remove the newest rule for the skill
    python -m kit.rules remove <skill> <number>       remove a rule by its number from `read --numbered`
    python -m kit.rules recent [count]                the newest lessons, five by default

Problems come back as one plain sentence: ValueError from the functions, a printed line and exit
status 1 from the command.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from kit import config

__all__ = ["read", "add", "undo", "remove", "numbered", "recent", "main"]

HEADLINE_WIDTH = 80  # a headline is the first sentence, cut at a whole word near this width
MAX_RULE = 300  # characters, once the rule is on one line

_SKILL = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_RULE_LINE = re.compile(r"- (?:(\d{4}-\d{2}-\d{2}): )?(.*)")
_HEADER = re.compile(r"## (\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}) · (.*)")
_REMOVED = re.compile(r" \(removed \d{4}-\d{2}-\d{2}\)$")
# A sentence ends at . ! or ? when the next word starts with a capital, a digit or an opening mark.
# That keeps "e.g. a number" and "12.5 seconds" inside one sentence.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")


# --- where things are, and what is allowed ------------------------------------------------------------


def _now() -> datetime:
    return datetime.now()


def _check_skill(skill) -> str:
    if not isinstance(skill, str) or not _SKILL.fullmatch(skill):
        raise ValueError(
            f"{skill!r} cannot be used as a skill name. A skill name is lower case letters, numbers and "
            "dashes, starts with a letter or number, and is at most 64 characters long, like ui-story."
        )
    return skill


def _skills_dir() -> Path:
    return config.kit_root() / ".claude" / "skills"


def _known_skills() -> list[str]:
    """The names of the skills this kit has: the folders in .claude/skills/."""
    try:
        return sorted(p.name for p in _skills_dir().iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return []


def _check_known(skill: str) -> None:
    known = _known_skills()
    if skill in known:
        return
    if not known:
        raise ValueError("There are no skills in .claude/skills yet.")
    raise ValueError(f"There is no skill called {skill}. The skills are: {', '.join(known)}.")


def _rules_path(skill) -> Path:
    return config.me_dir() / "rules" / f"{_check_skill(skill)}.md"


def _journal_path() -> Path:
    return config.me_dir() / "lessons.md"


def _one_line(rule) -> str:
    if not isinstance(rule, str) or not rule.strip():
        raise ValueError("A rule cannot be empty. Write it as one or two plain sentences.")
    return " ".join(rule.split())


# --- reading -------------------------------------------------------------------------------------------


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, NotADirectoryError):
        return ""


def read(skill: str) -> str:
    """The creator's rules for one skill, exactly as written in me/rules/<skill>.md. "" when there are none."""
    return _text(_rules_path(skill))


@dataclass(frozen=True)
class _Rule:
    at: int  # index of the line in the file
    date: str | None  # the date the rule was written, when its line has one
    text: str  # the rule without its dash and date
    shown: str  # the line as the creator sees it, without the leading dash


def _rule_lines(text: str) -> list[_Rule]:
    found = []
    for at, raw in enumerate(text.splitlines()):
        match = _RULE_LINE.fullmatch(raw) if raw.startswith("- ") else None
        if match:
            found.append(_Rule(at, match.group(1), match.group(2).strip(), raw[2:].strip()))
    return found


def numbered(skill: str) -> str:
    """The skill's rules with a number in front of each, the numbers `remove` takes."""
    rule_lines = _rule_lines(read(skill))
    return "".join(f"{number}. {item.shown}\n" for number, item in enumerate(rule_lines, 1))


@dataclass
class _Entry:
    at: int  # index of the header line in the journal
    date: str
    time: str
    headline: str
    removed: bool
    skill: str | None = None
    rule: str | None = None


def _entries(lines: list[str]) -> list[_Entry]:
    found: list[_Entry] = []
    for at, raw in enumerate(lines):
        line = raw.rstrip("\r\n")
        header = _HEADER.fullmatch(line)
        if header:
            date, time, rest = header.groups()
            found.append(_Entry(at, date, time, _REMOVED.sub("", rest), bool(_REMOVED.search(rest))))
        elif found and line.startswith("- Skill: ") and found[-1].skill is None:
            found[-1].skill = line[len("- Skill: ") :].strip()
        elif found and line.startswith("- Rule: ") and found[-1].rule is None:
            found[-1].rule = line[len("- Rule: ") :]
    return found


def recent(count: int = 5) -> list[str]:
    """The newest lessons, newest first, as "2026-10-28 14:05 · headline (skill)". Removed ones are left out."""
    live = [entry for entry in _entries(_text(_journal_path()).splitlines(keepends=True)) if not entry.removed]
    chosen = live[::-1][: max(count, 0)]
    return [
        f"{entry.date} {entry.time} · {entry.headline}" + (f" ({entry.skill})" if entry.skill else "")
        for entry in chosen
    ]


# --- writing -------------------------------------------------------------------------------------------


def _headline(rule: str) -> str:
    sentence = _SENTENCE_END.split(rule, maxsplit=1)[0]
    if len(sentence) <= HEADLINE_WIDTH:
        return sentence
    window = sentence[: HEADLINE_WIDTH + 1]
    space = window.rfind(" ")
    stem = window[:space] if space >= HEADLINE_WIDTH // 2 else sentence[:HEADLINE_WIDTH]
    return stem.rstrip(" ,;:") + "..."


def _append(path: Path, text: str, *, blank_line_before: bool = False) -> None:
    """Add `text` to the end of a file, starting a new line first when the file does not end in one."""
    try:
        existing = path.read_bytes()
    except FileNotFoundError:
        existing = b""
    prefix = ""
    if existing:
        if not existing.endswith(b"\n"):
            prefix = "\n\n" if blank_line_before else "\n"
        elif blank_line_before and not existing.endswith(b"\n\n"):
            prefix = "\n"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(prefix + text)


def _write_whole(path: Path, text: str) -> None:
    """Replace a file in one step, so a stopped run never leaves half a file."""
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.replace(temp, path)


def _add(skill: str, rule: str) -> bool:
    """Record a rule. False when the skill already has exactly this rule, in which case nothing is written."""
    path = _rules_path(skill)
    text = _one_line(rule)
    if len(text) > MAX_RULE:
        raise ValueError(
            f"A rule can be at most {MAX_RULE} characters, and this one has {len(text)}. "
            "Shorten it to one or two plain sentences."
        )
    _check_known(skill)
    if any(item.text == text for item in _rule_lines(_text(path))):
        return False
    when = _now()
    path.parent.mkdir(parents=True, exist_ok=True)
    _append(path, f"- {when:%Y-%m-%d}: {text}\n")
    entry = f"## {when:%Y-%m-%d %H:%M} · {_headline(text)}\n\n- Skill: {skill}\n- Rule: {text}\n"
    _append(_journal_path(), entry, blank_line_before=True)
    return True


def add(skill: str, rule: str) -> None:
    """Record a rule for `skill`: one line in me/rules/<skill>.md and one entry in me/lessons.md.

    Raises ValueError for a rule over 300 characters or a skill with no folder in .claude/skills/. A rule the
    skill already has, word for word, is skipped."""
    _add(skill, rule)


def _mark_removed(skill: str, rule: _Rule) -> None:
    """Mark the journal entry for a rule that was taken back. A rule written by hand has no entry."""
    journal = _journal_path()
    lines = _text(journal).splitlines(keepends=True)
    matches = [e for e in _entries(lines) if e.skill == skill and e.rule == rule.text and not e.removed]
    same_day = [e for e in matches if rule.date is None or e.date == rule.date]
    chosen = (same_day or matches)[-1:]
    if not chosen:
        return
    at = chosen[0].at
    lines[at] = f"{lines[at].rstrip(chr(13) + chr(10))} (removed {_now():%Y-%m-%d})\n"
    _write_whole(journal, "".join(lines))


def _take_out(skill: str, path: Path, rule: _Rule) -> str:
    lines = _text(path).splitlines(keepends=True)
    del lines[rule.at]
    rest = "".join(lines)
    if rest.strip():
        _write_whole(path, rest)
    else:
        path.unlink()
    _mark_removed(skill, rule)
    return rule.text


def undo(skill: str) -> str | None:
    """Remove the newest rule for `skill` and return its text. None when the skill has no rules."""
    path = _rules_path(skill)
    rule_lines = _rule_lines(_text(path))
    if not rule_lines:
        return None
    return _take_out(skill, path, rule_lines[-1])


def remove(skill: str, number: int) -> str:
    """Remove rule number `number` (counting from 1, as `numbered` shows) and return its text."""
    path = _rules_path(skill)
    rule_lines = _rule_lines(_text(path))
    if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= len(rule_lines):
        have = "no rules" if not rule_lines else f"{len(rule_lines)} rule" + ("" if len(rule_lines) == 1 else "s")
        raise ValueError(f"There is no rule number {number!r} for {skill}. It has {have}.")
    return _take_out(skill, path, rule_lines[number - 1])


# --- the command line -------------------------------------------------------------------------------------


def _whole_number(text: str, what: str) -> int:
    try:
        return int(text)
    except ValueError:
        raise ValueError(f"The {what} must be a whole number such as 1, not {text!r}.") from None


def _run(args: argparse.Namespace) -> int:
    if args.command == "read":
        sys.stdout.write(numbered(args.skill) if args.numbered else read(args.skill))
    elif args.command == "add":
        rule = sys.stdin.read() if args.rule == ["-"] else " ".join(args.rule)
        recorded = _add(args.skill, rule)
        print(f"{'Recorded' if recorded else 'Already recorded'} for {args.skill}: {_one_line(rule)}")
    elif args.command == "undo":
        removed = undo(args.skill)
        if removed is None:
            print(f"There are no rules for {args.skill} to remove.")
        else:
            print(f"Removed from {args.skill}: {removed}")
    elif args.command == "remove":
        removed = remove(args.skill, _whole_number(args.number, "rule number"))
        print(f"Removed from {args.skill}: {removed}")
    else:
        for line in recent(_whole_number(args.count, "number of lessons")):
            print(line)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m kit.rules",
        description="Read, record and take back the creator's rules, which live in me/rules/ and me/lessons.md.",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")

    one = commands.add_parser("read", help="print a skill's rules")
    one.add_argument("skill")
    one.add_argument("--numbered", action="store_true", help="put a number in front of each rule")

    one = commands.add_parser("add", help="record a rule for a skill")
    one.add_argument("skill")
    one.add_argument("rule", nargs="+", help='the rule, in plain words, or "-" to read it from standard input')

    one = commands.add_parser("undo", help="remove the newest rule for a skill")
    one.add_argument("skill")

    one = commands.add_parser("remove", help="remove a rule by its number")
    one.add_argument("skill")
    one.add_argument("number", help="the number shown by: read <skill> --numbered")

    one = commands.add_parser("recent", help="print the newest lessons")
    one.add_argument("count", nargs="?", default="5", help="how many to print (default 5)")

    args = parser.parse_args(argv)
    try:
        return _run(args)
    except ValueError as exc:
        print(exc, file=sys.stderr)
    except OSError as exc:
        print(f"The rules files could not be used ({exc.strerror or type(exc).__name__}).", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
