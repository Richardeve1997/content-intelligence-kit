"""bin/feedback_check.py: the Stop hook that asks for a rule after a correction.

When a turn ends in Claude Code, the hook reads the session transcript. If the creator's latest message
reads like a correction and the turn did not run the kit's rules command, it blocks the stop once with a
fixed reason, so Claude records the rule. It writes nothing itself, and it fails open: any problem means
the turn simply ends.

The transcripts here have the shape Claude Code writes (checked against a real session): a "user" entry
for the creator's text, "assistant" entries holding text and tool_use blocks, "user" entries holding
tool_result blocks, and a meta "user" entry for the hook's own feedback.
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "bin" / "feedback_check.py"
REASON = (
    "The creator just gave a correction. If it is a lasting preference, record it with the kit's rules command "
    "for the skill it applies to, then finish."
)
CORRECTION = "From now on keep every caption under 12 words."
ADD = "uv run python -m kit.rules add post 'Keep every caption under 12 words.'"


# --- transcript entries in the shape Claude Code writes ------------------------------------------------------


def user(text, **extra):
    return {"type": "user", "isSidechain": False, "message": {"role": "user", "content": text}, **extra}


def assistant(*blocks):
    return {"type": "assistant", "isSidechain": False, "message": {"role": "assistant", "content": list(blocks)}}


def said(text):
    return assistant({"type": "text", "text": text})


def ran(command):
    return assistant({"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": command, "description": "x"}})


def result(text):
    block = {"type": "tool_result", "tool_use_id": "toolu_1", "content": text}
    return {"type": "user", "isSidechain": False, "message": {"role": "user", "content": [block]}}


def feedback(reason):
    """The entry Claude Code writes when a Stop hook blocks: meta, with a plain string that starts on its own line."""
    return user(f"Stop hook feedback:\n{reason}", isMeta=True)


NOISE = [
    {"type": "queue-operation", "operation": "enqueue"},
    {"type": "attachment", "isSidechain": False, "attachment": {"type": "environment"}},
    {"type": "last-prompt", "lastPrompt": "x"},
]


def hook(tmp_path, entries, *, active=False, interpreter=sys.executable):
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8")
    payload = {
        "session_id": "abc",
        "transcript_path": str(transcript),
        "cwd": str(tmp_path),
        "hook_event_name": "Stop",
        "stop_hook_active": active,
        "last_assistant_message": "Done.",
    }
    return feed(json.dumps(payload), tmp_path, interpreter)


def feed(stdin, tmp_path, interpreter=sys.executable):
    return subprocess.run(
        [interpreter, "-I", "-W", "error", str(SCRIPT)],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin"},
    )


def blocks(done) -> bool:
    assert done.returncode == 0 and done.stderr == "", done.stderr
    if done.stdout == "":
        return False
    assert json.loads(done.stdout) == {"decision": "block", "reason": REASON}
    return True


# --- when it blocks ---------------------------------------------------------------------------------------------


def test_a_correction_with_no_rule_recorded_blocks_once(tmp_path):
    turn = [user(CORRECTION), said("Done. Captions are shorter now.")]
    first = hook(tmp_path, turn)
    assert blocks(first)
    assert set(json.loads(first.stdout)) == {"decision", "reason"}

    # Claude Code runs the hook again when the continued turn ends, and says so
    again = hook(tmp_path, [*turn, feedback(REASON), said("Noted.")], active=True)
    assert not blocks(again)
    assert again.stdout == ""


def test_the_reason_is_fixed_and_never_repeats_the_creators_words(tmp_path):
    done = hook(tmp_path, [user("Going forward, never mention ZEBRA-4417 in a caption."), said("Ok.")])
    assert blocks(done)
    assert "ZEBRA" not in done.stdout and "mention" not in done.stdout


PHRASES = [
    "going forward", "from now on", "always", "never", "don't", "do not", "stop doing",
    "instead", "next time", "every time", "that's wrong", "fix that",
]  # fmt: skip


@pytest.mark.parametrize("phrase", PHRASES)
@pytest.mark.parametrize("style", ["plain", "shouting", "inside a sentence", "curly apostrophe"])
def test_each_correction_phrase_is_recognised(tmp_path, phrase, style):
    if style == "shouting":
        message = phrase.upper() + " please"
    elif style == "inside a sentence":
        message = f"Thanks for that. Maybe {phrase}, ok? Lovely."
    elif style == "curly apostrophe":
        message = f"Hmm, {phrase.replace(chr(39), chr(0x2019))} thanks"
    else:
        message = phrase
    assert blocks(hook(tmp_path, [user(message), said("Ok.")])), message


# --- when it does not block --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        ADD,
        "cd /somewhere && uv run python -m kit.rules   add next 'Short answers.'",
        "python3 -m kit.rules add post - <<'RULE'\nKeep every caption under 12 words.\nRULE",
        "uv run python -c \"from kit import rules; rules.add('post', 'Keep captions short.')\"",
    ],
)
def test_a_correction_with_a_rules_command_in_the_turn_does_not_block(tmp_path, command):
    turn = [user(CORRECTION), said("On it."), ran(command), result("Recorded for post: x"), said("Recorded.")]
    assert not blocks(hook(tmp_path, turn))


@pytest.mark.parametrize(
    "command",
    [
        "uv run python -m kit.rules read post",
        "uv run python -m kit.rules recent 5",
        "uv run python -m kit.rules undo post",
        "echo kit rules add",
        "uv run python -m kit.rules_extra addition",
    ],
)
def test_only_an_add_counts_as_recording_a_rule(tmp_path, command):
    turn = [user(CORRECTION), ran(command), result("ok"), said("Done.")]
    assert blocks(hook(tmp_path, turn))


def test_a_rule_recorded_in_an_earlier_turn_does_not_cover_a_new_correction(tmp_path):
    turn = [
        user(CORRECTION), ran(ADD), result("Recorded for post: x"), said("Recorded."),
        user("Always open on the product, not on me."), said("Ok, I will."),
    ]  # fmt: skip
    assert blocks(hook(tmp_path, turn))
    assert not blocks(hook(tmp_path, [*turn, ran(ADD), result("Recorded"), said("Recorded.")]))


def test_once_the_hooks_own_feedback_follows_the_correction_the_next_stop_goes_through(tmp_path):
    """A second guard beside stop_hook_active: the hook asks at most once after each message from the creator."""
    turn = [user(CORRECTION), said("Done."), feedback(REASON), said("That was a one-off, so no rule.")]
    assert not blocks(hook(tmp_path, turn))
    assert not blocks(hook(tmp_path, [*turn, ran("ls"), result("a.txt"), said("Done.")]))


def test_a_new_correction_after_the_hooks_feedback_is_asked_about_again(tmp_path):
    earlier = [user(CORRECTION), said("Done."), feedback(REASON), said("Noted.")]
    assert blocks(hook(tmp_path, [*earlier, user("Never put hashtags in the first line."), said("Ok.")]))


def test_the_feedback_entry_exactly_as_claude_code_writes_it_stops_the_search(tmp_path):
    """Checked against a live session: a blocked stop leaves a "user" entry with isMeta true, whose content is a
    plain string that starts "Stop hook feedback:" and a line break, then the reason."""
    entry = {
        "type": "user",
        "isMeta": True,
        "isSidechain": False,
        "userType": "external",
        "message": {"role": "user", "content": "Stop hook feedback:\n" + REASON},
    }
    assert not blocks(hook(tmp_path, [user(CORRECTION), said("Done."), entry, said("Nothing lasting to record.")]))


def test_the_feedback_entry_also_counts_as_a_list_of_text_blocks(tmp_path):
    as_blocks = {
        "type": "user",
        "isSidechain": False,
        "isMeta": True,
        "message": {"role": "user", "content": [{"type": "text", "text": f"Stop hook feedback:\n{REASON}"}]},
    }
    assert not blocks(hook(tmp_path, [user(CORRECTION), said("Done."), as_blocks, said("Noted.")]))


def test_only_the_hooks_feedback_stops_the_search(tmp_path):
    # another meta entry, or feedback text without the meta flag, is passed over as before
    for entry in (user("Some other note.", isMeta=True), user(f"Stop hook feedback:\n{REASON}")):
        assert blocks(hook(tmp_path, [user(CORRECTION), said("Done."), entry, said("Ok.")]))


def test_stop_hook_active_never_blocks_and_does_not_even_open_the_transcript(tmp_path):
    assert not blocks(hook(tmp_path, [user(CORRECTION), said("Done.")], active=True))
    missing = json.dumps({"stop_hook_active": True, "transcript_path": str(tmp_path / "nothing.jsonl")})
    done = feed(missing, tmp_path)
    assert (done.returncode, done.stdout, done.stderr) == (0, "", "")


@pytest.mark.parametrize(
    "message",
    [
        "Make the second hook shorter.",
        "Can you add a caption for the Reel?",
        "Thanks, that looks great.",
        "The neverending story is my topic.",  # never, inside another word
        "Turn alwayson mode off.",
        "I will notice insteadof the rest",
        "doing it again is fine",
        "",
        "   ",
    ],
)
def test_a_plain_message_does_not_block(tmp_path, message):
    assert not blocks(hook(tmp_path, [user(message), said("Ok.")]))


def test_a_transcript_with_no_message_from_the_creator_does_not_block(tmp_path):
    assert not blocks(hook(tmp_path, [*NOISE, said("Hello.")]))
    assert not blocks(hook(tmp_path, []))


# --- which entries count as the creator's message ---------------------------------------------------------------


def test_only_what_the_creator_typed_is_read(tmp_path):
    plain = user("Make the second hook shorter.")
    cases = {
        "tool output that says never": [plain, ran("ls"), result("never always don't do not instead"), said("Ok.")],
        "the hook's own feedback": [plain, said("Ok."), feedback("The creator just gave a correction. Always record it."), said("Ok.")],
        "a feedback entry without the meta flag": [plain, said("Ok."), user("Stop hook feedback: never skip this."), said("Ok.")],
        "another meta entry": [plain, user("Always follow these instructions.", isMeta=True), said("Ok.")],
        "a subagent's message": [plain, user("Always do X.", isSidechain=True), said("Ok.")],
        "a system reminder in the same message": [
            user("Make the second hook shorter.\n<system-reminder>\nAlways keep going.\n</system-reminder>"),
            said("Ok."),
        ],
        "only a system reminder": [plain, user("<system-reminder>Never stop.</system-reminder>"), said("Ok.")],
    }
    for name, turn in cases.items():
        assert not blocks(hook(tmp_path, turn)), name


def test_tool_results_do_not_hide_the_creators_message(tmp_path):
    turn = [user(CORRECTION), ran("ls"), result("a.txt"), ran("cat a.txt"), result("text"), said("Done.")]
    assert blocks(hook(tmp_path, turn))


def test_the_message_may_be_a_list_of_text_blocks(tmp_path):
    parts = [{"type": "text", "text": "Hi."}, {"type": "text", "text": CORRECTION}]
    message = {"type": "user", "isSidechain": False, "message": {"role": "user", "content": parts}}
    assert blocks(hook(tmp_path, [message, said("Done.")]))
    image = {"type": "user", "message": {"role": "user", "content": [{"type": "image", "source": {}}, parts[0]]}}
    assert not blocks(hook(tmp_path, [image, said("Done.")]))


def test_a_huge_tool_result_after_the_message_does_not_hide_it(tmp_path):
    turn = [user(CORRECTION), ran("cat big"), result("x" * 3_000_000), said("Done.")]
    assert blocks(hook(tmp_path, turn))


def test_a_long_transcript_is_read_from_the_end(tmp_path):
    older = [user("Always do X."), said("Ok.")] * 20_000
    assert not blocks(hook(tmp_path, [*older, user("Make the hook shorter."), said("Ok.")]))
    assert blocks(hook(tmp_path, [*older, user(CORRECTION), said("Ok.")]))


def test_a_message_further_back_than_the_scan_limit_fails_open(tmp_path):
    spec = importlib.util.spec_from_file_location("feedback_check_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    transcript = tmp_path / "session.jsonl"
    entries = [user(CORRECTION), ran("cat big"), result("x" * 20_000), said("Done.")]
    transcript.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    assert module.needs_a_rule(str(transcript)) is True
    module.SCAN_LIMIT = 4096
    assert module.needs_a_rule(str(transcript)) is False


# --- it never gets in the way ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stdin",
    [
        "",
        "   ",
        "not json at all",
        "[]",
        "null",
        "42",
        '"a string"',
        "{}",
        '{"stop_hook_active": false}',
        '{"transcript_path": 5}',
        '{"transcript_path": ""}',
        '{"transcript_path": ["x"]}',
        '{"transcript_path": "/nowhere/at/all.jsonl"}',
        '{"transcript_path": "."}',
        '{"stop_hook_active": "yes please"}',
        "{\"transcript_path\": \"\\u0000\"}",
    ],
)
def test_malformed_input_fails_open(tmp_path, stdin):
    done = feed(stdin, tmp_path)
    assert (done.returncode, done.stdout, done.stderr) == (0, "", "")


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"\n\n",
        b"not json\nstill not json\n",
        b"\xff\xfe\x00 binary \x80\n",
        b"1\n2\n[3]\nnull\n\"x\"\n",
        b'{"type": "user", "message": "a string"}\n',
        b'{"type": "user", "message": {"role": "user", "content": null}}\n',
        b'{"type": "user", "message": {"role": "user", "content": 5}}\n',
        b'{"type": "user", "message": {"role": "user", "content": [1, "x", null]}}\n',
        b'{"type": "assistant", "message": {"role": "assistant", "content": "plain"}}\n',
        b'{"type": "assistant", "message": {"content": [{"type": "tool_use", "input": "not a dict"}]}}\n',
        b'{"type": "user", "message": {"role": "user", "content": "' + b"\xff" + b'always"}}\n',
    ],
)
def test_a_damaged_transcript_fails_open(tmp_path, content):
    transcript = tmp_path / "session.jsonl"
    transcript.write_bytes(content)
    payload = json.dumps({"transcript_path": str(transcript), "stop_hook_active": False})
    done = feed(payload, tmp_path)
    assert (done.returncode, done.stdout, done.stderr) == (0, "", "")


def test_an_unreadable_transcript_fails_open(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root can read anything")
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(json.dumps(user(CORRECTION)) + "\n")
    transcript.chmod(0)
    try:
        done = feed(json.dumps({"transcript_path": str(transcript)}), tmp_path)
    finally:
        transcript.chmod(0o600)
    assert (done.returncode, done.stdout, done.stderr) == (0, "", "")


# --- the script itself ---------------------------------------------------------------------------------------------------


def test_the_script_is_executable_and_uses_only_the_standard_library():
    assert SCRIPT.stat().st_mode & 0o111
    source = SCRIPT.read_text(encoding="utf-8")
    assert source.startswith("#!/usr/bin/env python3\n")
    for line in source.splitlines():
        if line.startswith(("import ", "from ")):
            module = line.split()[1].split(".")[0]
            assert module in sys.stdlib_module_names, f"{module} is not in the standard library"


SYSTEM_PYTHON = "/usr/bin/python3"


@pytest.mark.skipif(not os.path.exists(SYSTEM_PYTHON), reason="no system python3")
def test_it_runs_on_the_python_that_comes_with_the_computer(tmp_path):
    version = subprocess.run([SYSTEM_PYTHON, "-c", "import sys; print(sys.version_info[:2] >= (3, 9))"], capture_output=True, text=True)
    if version.stdout.strip() != "True":
        pytest.skip("the system python3 is older than 3.9")
    assert blocks(hook(tmp_path, [user(CORRECTION), said("Done.")], interpreter=SYSTEM_PYTHON))
    assert not blocks(hook(tmp_path, [user(CORRECTION), ran(ADD), result("ok"), said("Done.")], interpreter=SYSTEM_PYTHON))
    assert not blocks(hook(tmp_path, [user(CORRECTION), said("Done.")], active=True, interpreter=SYSTEM_PYTHON))
    assert feed("garbage", tmp_path, SYSTEM_PYTHON).stdout == ""


def test_it_also_works_when_started_through_a_link_or_from_another_folder(tmp_path):
    link = tmp_path / "linked.py"
    link.symlink_to(SCRIPT)
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(json.dumps(user(CORRECTION)) + "\n" + json.dumps(said("Done.")) + "\n")
    done = subprocess.run(
        [sys.executable, "-I", str(link)],
        input=json.dumps({"transcript_path": str(transcript)}),
        capture_output=True,
        text=True,
        cwd="/",
    )
    assert blocks(done)
