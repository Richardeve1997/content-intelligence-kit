#!/usr/bin/env python3
"""Stop hook: ask for a rule when the creator's last message was a correction and none was recorded.

Claude Code runs this when a turn ends and sends a JSON description of the session on standard input.
If the creator's latest message reads like a correction ("going forward", "never", "fix that" and so
on) and the turn did not run the kit's rules command, the hook blocks the stop once. The reason it gives
is fixed text that never repeats what the creator wrote. Claude then records the rule, and the next
stop goes through, because Claude Code marks it with stop_hook_active. A second guard does not depend on
that mark: reading back from the end, the hook stops at a Stop hook's own feedback (a meta entry that
starts "Stop hook feedback") when it meets one before the creator's message, so it asks at most once
after each message the creator sends.

The hook writes nothing. Any problem at all (bad input, no transcript, a file it cannot read) means it
prints nothing and exits 0, so a turn is never held up by this hook failing.

It uses only the standard library and runs on Python 3.9, the version macOS ships.
"""

import json
import os
import re
import sys

REASON = (
    "The creator just gave a correction. If it is a lasting preference, record it with the kit's rules command "
    "for the skill it applies to, then finish."
)

# Phrases that mark a correction. A space matches any run of white space, and an apostrophe may be curly.
PHRASES = (
    "going forward", "from now on", "always", "never", "don't", "do not", "stop doing",
    "instead", "next time", "every time", "that's wrong", "fix that",
)  # fmt: skip
CORRECTION = re.compile(
    r"\b(?:" + "|".join(p.replace(" ", r"\s+").replace("'", "['’]") for p in PHRASES) + r")\b",
    re.IGNORECASE,
)
RECORDS_A_RULE = re.compile(r"kit\.rules\s+add\b|\brules\.add\(")
SYSTEM_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
FEEDBACK = "Stop hook feedback"  # how Claude Code starts the entry that carries a Stop hook's reason

SCAN_LIMIT = 64 * 1024 * 1024  # how far back from the end of the transcript to look, in bytes
CHUNK = 1024 * 1024


def lines_from_the_end(path):
    """The non-empty lines of a file, last first. A line cut off by SCAN_LIMIT is dropped."""
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        start = max(0, position - SCAN_LIMIT)
        pending = b""
        while position > start:
            step = min(CHUNK, position - start)
            position -= step
            handle.seek(position)
            pending = handle.read(step) + pending
            parts = pending.split(b"\n")
            pending = parts[0]
            for line in reversed(parts[1:]):
                if line.strip():
                    yield line
        if start == 0 and pending.strip():
            yield pending


def message_text(message):
    """The text of a message with any system reminder taken out, or None when it holds no text."""
    content = message.get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        pieces = [
            block["text"]
            for block in content
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
        ]
        if not pieces:
            return None
        text = "\n".join(pieces)
    else:
        return None
    return SYSTEM_REMINDER.sub("", text).strip()


def what_the_creator_typed(message):
    """The text of a message the creator typed, or None for anything else (a tool result, an image alone,
    a system reminder, the hook's own feedback)."""
    text = message_text(message)
    if not text or text.startswith(FEEDBACK):
        return None
    return text


def is_stop_hook_feedback(message):
    """True for the entry Claude Code adds when a Stop hook blocked a stop and gave its reason."""
    text = message_text(message)
    return bool(text) and text.startswith(FEEDBACK)


def ran_the_rules_command(content):
    """True if an assistant message holds a Bash call that adds a rule."""
    if not isinstance(content, list):
        return False
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Bash":
            tool_input = block.get("input")
            command = tool_input.get("command") if isinstance(tool_input, dict) else None
            if isinstance(command, str) and RECORDS_A_RULE.search(command):
                return True
    return False


def needs_a_rule(path):
    """True when the creator's latest message is a correction and nothing after it ran the rules command.

    The transcript is read from the end, so a long session costs little. If the creator's message is further
    back than SCAN_LIMIT, or a Stop hook's feedback comes after it, the answer is False."""
    recorded = False
    for line in lines_from_the_end(path):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("isSidechain"):
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        if entry.get("type") == "assistant":
            recorded = recorded or ran_the_rules_command(message.get("content"))
        elif entry.get("type") == "user" and entry.get("isMeta"):
            if is_stop_hook_feedback(message):
                return False  # already asked since the creator's last message
        elif entry.get("type") == "user":
            text = what_the_creator_typed(message)
            if text is not None:
                return not recorded and CORRECTION.search(text) is not None
    return False


def main():
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return 0
        data = json.loads(sys.stdin.read())
        if not isinstance(data, dict) or data.get("stop_hook_active"):
            return 0
        path = data.get("transcript_path")
        if not isinstance(path, str) or not path:
            return 0
        if needs_a_rule(os.path.expanduser(path)):
            print(json.dumps({"decision": "block", "reason": REASON}))
    except Exception:  # fail open: whatever went wrong, the turn ends as it would have
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
