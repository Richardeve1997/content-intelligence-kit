"""python -m kit.post: the commands the post skill runs in a shell.

They talk to the stand-in Zernio. `Client.from_env` is pointed at it, and tests of the no-key path leave
it alone so there is no key at all.
"""

from __future__ import annotations

import errno
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fake_zernio import default_accounts, make_account

from kit import zernio
from kit.config import PLATFORMS
from kit.post import cli, store
from kit.zernio import Client

KEY = "fake-value-for-tests"
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def keyed(stub, monkeypatch):
    """A key is set and every Zernio call goes to the stand-in."""
    monkeypatch.setenv("ZERNIO_API_KEY", KEY)
    monkeypatch.setattr(Client, "from_env", classmethod(lambda cls, base=zernio.API: stub.client()))
    return stub


def say(capsys, *argv):
    """Run a command and return (exit status, standard output, standard error)."""
    status = cli.main(list(argv))
    out, err = capsys.readouterr()
    return status, out, err


# --- check and lines ------------------------------------------------------------------------------------


def test_check_lists_each_platform_and_nothing_private(keyed, capsys):
    keyed.accounts = [
        *default_accounts(skip=("tiktok", "twitter")),
    ]
    keyed.accounts[0] = make_account("instagram", username="@private-handle", account_id="acct-private-id")
    status, out, err = say(capsys, "check")
    assert status == 0 and err == ""
    assert out.splitlines() == [
        "Instagram: connected", "TikTok: not connected", "YouTube: connected",
        "Facebook: connected", "LinkedIn: connected", "X: not connected",
    ]
    assert "private" not in out and KEY not in out


def test_check_without_a_key_says_so_in_one_sentence(studio, capsys):
    """Posting by hand is a choice, not a failure: one sentence, and exit 0 so a script reads it as an answer."""
    status, out, err = say(capsys, "check")
    assert status == 0 and err == ""
    assert "ZERNIO_API_KEY" in out and "by hand" in out and out.count("\n") == 1


def test_lines_gives_each_platform_its_call_to_action_and_tags(reel, capsys):
    status, out, _ = say(capsys, "lines", "--tags", "basil, #windowsill herbs")
    shown = json.loads(out)
    assert status == 0 and list(shown) == list(PLATFORMS)
    assert shown["instagram"] == {
        "cta": 'Comment "GROW" for the balcony planting checklist.',
        "hashtags": ["#balconygarden", "#growyourown", "#smallspaces", "#basil", "#windowsill"],
        "hashtag_limit": 5,
    }
    assert shown["tiktok"]["cta"] == "Follow @examplestudio for more."
    assert shown["twitter"]["hashtags"] == ["#balconygarden", "#basil"]
    assert shown["linkedin"] == {"cta": "Follow Example Studio for more.", "hashtags": [], "hashtag_limit": 0}


def test_lines_without_video_tags_still_works(reel, capsys):
    status, out, _ = say(capsys, "lines")
    assert status == 0 and json.loads(out)["instagram"]["hashtags"] == ["#balconygarden", "#growyourown", "#smallspaces"]


# --- captions and approval ---------------------------------------------------------------------------------


def test_captions_passes_or_lists_every_problem(reel, capsys):
    assert say(capsys, "captions", reel.slug) == (0, "All captions pass.\n", "")

    path = reel.folder / "captions.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["linkedin"]["hashtags"] = ["#basil"]
    data["twitter"]["hashtags"] = ["#a1", "#b2", "#c3"]
    path.write_text(json.dumps(data), encoding="utf-8")
    status, out, _ = say(capsys, "captions", reel.slug)
    assert status == 1
    assert [line.split(":")[0] for line in out.splitlines()] == ["LinkedIn", "X"]


def test_approve_refuses_captions_that_break_a_rule(reel, capsys):
    path = reel.folder / "captions.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["linkedin"]["hashtags"] = ["#basil"]
    path.write_text(json.dumps(data), encoding="utf-8")
    status, out, err = say(capsys, "approve", reel.slug)
    assert status == 1 and "LinkedIn" in err and out == ""
    assert not (reel.folder / "captions.approved.json").exists()


def test_schedule_waits_for_the_creators_yes(keyed, reel, capsys):
    status, out, err = say(capsys, "schedule", reel.slug, "--on", "tomorrow", "--time", "18:00")
    assert status == 1 and "not been approved" in err and f"approve {reel.slug}" in err
    assert keyed.calls() == []

    assert say(capsys, "approve", reel.slug)[0] == 0
    status, out, _ = say(capsys, "schedule", reel.slug, "--on", "tomorrow", "--time", "18:00")
    assert status == 0 and len(keyed.creates()) == 6


def test_a_changed_caption_needs_a_new_yes(keyed, reel, capsys):
    say(capsys, "approve", reel.slug)
    path = reel.folder / "captions.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["tiktok"]["body"] += " Water early."
    path.write_text(json.dumps(data), encoding="utf-8")
    status, _, err = say(capsys, "schedule", reel.slug, "--on", "tomorrow", "--time", "18:00")
    assert status == 1 and "changed after" in err
    assert keyed.calls() == []


# --- scheduling ---------------------------------------------------------------------------------------------------


def tomorrow() -> str:
    return (datetime.now(ZoneInfo("Europe/London")) + timedelta(days=1)).date().isoformat()


def test_schedule_prints_one_line_for_each_platform(keyed, reel, capsys):
    keyed.accounts = default_accounts(skip=("tiktok",))
    say(capsys, "approve", reel.slug)
    status, out, err = say(
        capsys, "schedule", reel.slug, "--platforms", "instagram,tiktok", "youtube", "--on", tomorrow(), "--time", "18:00"
    )
    lines = out.splitlines()
    assert status == 0 and err == ""  # a skipped platform is not a failure
    assert lines[0].startswith("Instagram: scheduled for ") and lines[0].endswith("18:00 (Europe/London).")
    assert lines[1] == "TikTok: skipped. Not connected in Zernio."
    assert lines[2].startswith("YouTube: scheduled for ")
    assert len(lines) == 3
    assert KEY not in out


def test_each_platform_goes_at_its_usual_time_when_no_time_is_given(keyed, reel, capsys):
    say(capsys, "approve", reel.slug)
    day = tomorrow()
    status, out, _ = say(capsys, "schedule", reel.slug, "--on", day)
    assert status == 0
    sent = {c.body["platforms"][0]["platform"]: c.body["scheduledFor"][11:16] for c in keyed.creates()}
    assert sent == {
        "instagram": "18:00", "tiktok": "19:00", "youtube": "17:00",
        "facebook": "18:30", "linkedin": "08:30", "twitter": "12:00",
    }
    assert len(keyed.uploads) == 1  # six moments, one upload
    assert [line.split(":")[0] for line in out.splitlines()] == ["Instagram", "TikTok", "YouTube", "Facebook", "LinkedIn", "X"]


def test_at_takes_a_date_and_time_and_x_is_accepted_by_its_short_name(keyed, reel, capsys):
    say(capsys, "approve", reel.slug)
    status, out, _ = say(capsys, "schedule", reel.slug, "--platforms", "X", "--at", "2099-07-01T18:00")
    assert status == 0 and out.startswith("X: scheduled for ")
    assert keyed.creates()[0].body["scheduledFor"] == "2099-07-01T18:00:00+01:00"


def test_a_dry_run_needs_no_approval_and_sends_nothing(keyed, reel, capsys):
    status, out, _ = say(capsys, "schedule", reel.slug, "--on", "tomorrow", "--time", "18:00", "--dry-run")
    assert status == 0 and keyed.calls() == []
    lines = out.splitlines()
    heads = [line for line in lines if not line.startswith(" ") and "dry run" in line]
    assert [line.split(":")[0] for line in heads] == ["Instagram", "TikTok", "YouTube", "Facebook", "LinkedIn", "X"]
    assert all("Would schedule for" in line for line in heads)
    assert lines[-1].startswith("Nothing was sent.")


def test_a_dry_run_prints_each_post_as_it_would_go_out(keyed, reel, capsys):
    """Under each platform's line, indented: the request that would be sent, with placeholders for the account
    and the uploaded video, which only Zernio knows."""
    status, out, _ = say(capsys, "schedule", reel.slug, "--platforms", "tiktok", "youtube", "--on", "tomorrow",
                         "--time", "18:00", "--dry-run")
    assert status == 0 and keyed.calls() == []
    blocks, current = {}, None
    for line in out.splitlines():
        if line.startswith(("TikTok:", "YouTube:")):
            current = line.split(":")[0]
            blocks[current] = []
        elif line.startswith("  ") and current:
            blocks[current].append(line)
    tiktok, youtube = (json.loads("\n".join(blocks[name])) for name in ("TikTok", "YouTube"))
    assert tiktok["tiktokSettings"]["privacy_level"] == "PUBLIC_TO_EVERYONE" and "#basil" in tiktok["content"]
    assert youtube["platforms"][0]["platformSpecificData"]["title"] == "Why windowsill basil dies in week three"
    assert youtube["scheduledFor"].endswith("18:00:00+00:00") or "T18:00:00" in youtube["scheduledFor"]
    assert youtube["platforms"][0]["accountId"] == "<your YouTube account in Zernio>"
    assert youtube["mediaItems"] == [{"type": "video", "url": "<the video, once it is uploaded>"}]


def test_a_failed_platform_makes_the_exit_status_1(keyed, reel, capsys):
    say(capsys, "approve", reel.slug)
    keyed.reply("POST", r"/api/v1/posts", 400, {"error": "That video is too long"})
    status, out, _ = say(capsys, "schedule", reel.slug, "--platforms", "instagram", "tiktok", "--on", tomorrow(), "--time", "18:00")
    assert status == 1
    assert out.splitlines()[0] == "Instagram: failed. Zernio returned HTTP 400: That video is too long."
    assert out.splitlines()[1].startswith("TikTok: scheduled for ")


def test_platforms_scheduled_before_a_later_problem_are_still_reported(keyed, reel, capsys, monkeypatch):
    say(capsys, "approve", reel.slug)
    real = Client.accounts
    calls = {"n": 0}

    def second_lookup_fails(self):
        calls["n"] += 1
        if calls["n"] == 2:
            raise zernio.ZernioError("Zernio returned HTTP 503: Try later.", status=503, uncertain=True)
        return real(self)

    monkeypatch.setattr(Client, "accounts", second_lookup_fails)
    status, out, err = say(capsys, "schedule", reel.slug, "--on", tomorrow())  # six usual times, so six lookups
    assert status == 1 and "HTTP 503" in err
    assert out.startswith("Instagram: scheduled for ") and out.count("\n") == 1  # the first group went out and is reported
    assert len(keyed.creates()) == 1


def test_no_key_writes_the_manual_file_and_says_so_in_one_sentence(reel, capsys):
    say(capsys, "approve", reel.slug)
    status, out, err = say(capsys, "schedule", reel.slug, "--on", "tomorrow", "--time", "18:00")
    assert status == 0 and err == ""
    assert out.count("\n") == 1
    assert out.strip() == (
        "No Zernio key is set, so nothing was scheduled. The captions and the video are in "
        f"work/{reel.slug}/post/manual.md, ready to post by hand."
    )
    assert (reel.folder / "post" / "manual.md").is_file()


def test_with_no_key_the_manual_file_gives_the_day_and_time_of_every_post(reel, capsys):
    say(capsys, "approve", reel.slug)
    day = datetime.strptime(tomorrow(), "%Y-%m-%d").strftime("%a %d %b")
    assert say(capsys, "schedule", reel.slug, "--on", "tomorrow", "--time", "21:15")[0] == 0
    text = (reel.folder / "post" / "manual.md").read_text(encoding="utf-8")
    for name in ("Instagram", "TikTok", "YouTube", "Facebook", "LinkedIn", "X"):
        assert f"## {name} ({day} 21:15)" in text, (name, text)
    assert say(capsys, "schedule", reel.slug, "--on", "tomorrow")[0] == 0, "no time: each platform's usual time"
    text = (reel.folder / "post" / "manual.md").read_text(encoding="utf-8")
    assert f"## Instagram ({day} 18:00)" in text and f"## LinkedIn ({day} 08:30)" in text and f"## X ({day} 12:00)" in text


def test_the_manual_command_writes_the_same_file(reel, capsys):
    status, out, _ = say(capsys, "manual", reel.slug)
    assert status == 0 and "manual.md" in out
    assert (reel.folder / "post" / "manual.md").is_file()


@pytest.mark.parametrize(
    "arguments, words",
    [
        (["--at", "tomorrow evening"], "not a date and time"),
        (["--on", "someday", "--time", "18:00"], "not a day"),
        (["--on", "tomorrow", "--time", "6pm"], "not a time"),
        (["--on", "tomorrow", "--time", "25:00"], "not a time"),
        (["--on", "2020-01-01", "--time", "18:00"], "passed"),
        (["--platforms", "mastodon", "--on", "tomorrow", "--time", "18:00"], "not a platform"),
    ],
)
def test_a_bad_time_or_platform_is_one_plain_sentence_and_sends_nothing(keyed, reel, capsys, arguments, words):
    say(capsys, "approve", reel.slug)
    status, out, err = say(capsys, "schedule", reel.slug, *arguments)
    assert status == 1 and out == "" and words in err and err.count("\n") == 1
    assert keyed.calls() == []


def test_a_missing_usual_time_is_found_before_anything_is_sent(keyed, reel, capsys, example_me):
    data = json.loads((example_me / "profile.json").read_text(encoding="utf-8"))
    del data["posting_times"]["tiktok"]
    (example_me / "profile.json").write_text(json.dumps(data), encoding="utf-8")
    say(capsys, "approve", reel.slug)
    status, out, err = say(capsys, "schedule", reel.slug, "--on", tomorrow())
    assert status == 1 and "TikTok" in err and "usual posting time" in err
    assert keyed.calls() == []


def test_an_unsafe_video_name_is_one_sentence(studio, capsys):
    status, out, err = say(capsys, "captions", "../elsewhere")
    assert status == 1 and "cannot be used as a video name" in err


# --- looking at, moving and cancelling ------------------------------------------------------------------------------------


def test_list_reschedule_and_cancel(keyed, reel, capsys):
    say(capsys, "approve", reel.slug)
    say(capsys, "schedule", reel.slug, "--platforms", "instagram", "tiktok", "--on", tomorrow(), "--time", "18:00")

    status, out, _ = say(capsys, "list", reel.slug)
    assert status == 0 and [line.split(":")[0] for line in out.splitlines()] == ["Instagram", "TikTok"]

    status, out, _ = say(capsys, "reschedule", reel.slug, "instagram", "--on", "2099-07-01", "--time", "09:30")
    assert status == 0 and out.strip().startswith("Instagram: scheduled for ") and "09:30" in out

    status, out, _ = say(capsys, "cancel", reel.slug, "tiktok")
    assert status == 0 and out == "TikTok: cancelled.\n"
    assert say(capsys, "cancel", reel.slug, "tiktok")[1] == "TikTok: skipped. Nothing is scheduled on TikTok for example-reel.\n"

    status, out, _ = say(capsys, "list", reel.slug)
    assert [line.split(":")[0] for line in out.splitlines()] == ["Instagram"]


def test_list_when_nothing_was_scheduled(reel, capsys):
    assert say(capsys, "list", reel.slug) == (0, "Nothing is scheduled for example-reel.\n", "")


def test_reschedule_uses_the_platforms_usual_time_when_only_a_day_is_given(keyed, reel, capsys):
    say(capsys, "approve", reel.slug)
    say(capsys, "schedule", reel.slug, "--platforms", "linkedin", "--on", tomorrow(), "--time", "10:00")
    status, out, _ = say(capsys, "reschedule", reel.slug, "linkedin", "--on", "2099-07-01")
    assert status == 0 and "08:30" in out


# --- a full disk or a folder it may not write to ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error, words, where",
    [
        (OSError(errno.ENOSPC, "No space left on device", "/anywhere/post/instagram.json"), "No space left on device", "instagram.json"),
        (PermissionError(errno.EACCES, "Permission denied", "/anywhere/post/media.json"), "Permission denied", "media.json"),
        (OSError(errno.EIO, "Input/output error"), "Input/output error", "a file"),
    ],
)
def test_a_file_problem_is_one_plain_sentence_not_a_traceback(keyed, reel, capsys, monkeypatch, error, words, where):
    say(capsys, "approve", reel.slug)

    def broken(path, data):
        raise error

    monkeypatch.setattr(store, "write", broken)
    status, out, err = say(capsys, "schedule", reel.slug, "--platforms", "instagram", "--at", "2099-07-01T18:00")
    assert status == 1 and out == "" and err.count("\n") == 1
    assert words in err and f"could not read or write {where}" in err and "disk space" in err
    assert "Traceback" not in err and "/anywhere" not in err
    assert keyed.creates() == []  # it stopped before anything could be sent twice


# --- running it as a module ------------------------------------------------------------------------------------------------


def test_it_runs_as_python_m_kit_post():
    done = subprocess.run(
        [sys.executable, "-m", "kit.post", "--help"], cwd=ROOT, capture_output=True, text=True, timeout=60
    )
    assert done.returncode == 0 and "python -m kit.post" in done.stdout
    for command in ("check", "lines", "captions", "approve", "schedule", "list", "reschedule", "cancel", "manual"):
        assert command in done.stdout
