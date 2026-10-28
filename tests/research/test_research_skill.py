"""The research skill: its front matter, its steps, and that every command it names exists."""

import re
from pathlib import Path

import pytest

from kit.research import __main__ as cli

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / ".claude" / "skills" / "research" / "SKILL.md"
EM_DASH, EN_DASH = chr(0x2014), chr(0x2013)  # written as code points so this file holds no dash


@pytest.fixture(scope="module")
def text() -> str:
    return SKILL.read_text(encoding="utf-8")


def sections(text: str) -> dict[str, str]:
    parts = re.split(r"^## (.+)$", text, flags=re.M)
    return {parts[i].strip(): parts[i + 1] for i in range(1, len(parts), 2)}


def test_front_matter(text):
    match = re.match(r"---\nname: research\ndescription: (.+)\n---\n", text)
    assert match, "the skill starts with a name and a description"
    description = match.group(1)
    for phrase in ("run my research", "give me hooks for number 2", "what should I make videos about"):
        assert phrase in description
    assert len(description) <= 1024


def test_the_first_step_reads_the_creators_rules(text):
    steps = sections(text)["Steps"]
    first = re.split(r"^2\. ", steps, flags=re.M)[0]
    assert first.lstrip().startswith("1. ")
    assert 'kit.rules.read("research")' in first
    assert "uv run python -m kit.rules read research" in first


def test_the_last_section_is_feedback_and_records_rules(text):
    names = list(sections(text))
    assert names[-1] == "Feedback"
    feedback = sections(text)["Feedback"]
    assert 'kit.rules.add("research"' in feedback
    assert "uv run python -m kit.rules add research" in feedback


def test_the_writing_goes_through_the_humanizer(text):
    assert ".claude/skills/humanizer/SKILL.md" in text
    assert "embedded mode" in text


def test_no_dashes_in_the_prose(text):
    assert EM_DASH not in text and EN_DASH not in text


def test_it_points_at_the_files_it_reads_and_writes(text):
    for path in (
        "research/runs/", "items.json", "ideas.json", "report.html", "me/voice/VOICE.md", "me/ideas.md",
        "me/watchlist.json", "me/profile.json", ".env",
    ):
        assert path in text, path
    for key in ("APIFY_TOKEN", "ZERNIO_API_KEY"):
        assert key in text


def test_it_describes_the_shape_of_an_idea(text):
    for field in ("`n`", "`title`", "`angle`", "`why`", "`format`", "`evidence`", "`hooks`", "`outlier`", "`creator`", "`url`"):
        assert field in text, field
    assert "five ideas" in text.lower() or "5 ideas" in text
    assert "between 5 and 10" in text


def test_every_command_it_names_exists(text):
    named = set(re.findall(r"python -m kit\.research ([a-z]+)", text))
    assert {"run", "report", "hooks"} <= named
    parser_commands = {"run", "report", "hooks"}
    assert named <= parser_commands
    for flag in set(re.findall(r"(?<![-\w])(--[a-z][a-z-]*)", text)):
        if flag in {"--inexact", "--group", "--numbered"}:
            continue
        assert flag in {"--offline", "--no-apify", "--no-transcribe", "--save", "--date"}, flag
    # the commands answer --help, so the names above are real
    for command in named:
        with pytest.raises(SystemExit) as caught:
            cli.main([command, "--help"])
        assert caught.value.code == 0


def test_the_skill_keeps_the_outlier_rule_and_the_low_sample_caution(text):
    assert "(3 x comments + likes)" in text and "creator's median" in text
    assert "low sample" in text.lower()


def test_the_skill_leaves_out_what_does_not_belong_in_a_public_kit(text):
    lowered = text.lower()
    chat_tool = "sl" + "ack"  # the old chat input is gone, so the skill must not send anyone to it
    for word in (chat_tool, "to" + "do", "t" + "bd"):
        assert not re.search(rf"\b{word}\b", lowered), word
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text), "an address of an email shape"
    assert "/".join(["", "Users", ""]) not in text


def test_the_skill_says_what_to_do_when_a_key_is_missing(text):
    assert "Instagram and TikTok skipped: add APIFY_TOKEN to .env to include them" in text
    assert "Performance skipped: add ZERNIO_API_KEY to .env to include your own results" in text


def test_a_thin_pull_has_a_step_of_its_own(text):
    steps = sections(text)["Steps"]
    assert re.search(r"^\d+\. If the pull is thin", steps, re.M)
    thin = re.search(r"^\d+\. If the pull is thin.*?(?=^\d+\. )", steps, re.M | re.S).group(0)
    for needed in ("no `APIFY_TOKEN`", "`me/ideas.md`", "X posts that were read", "Never invent evidence", "fewer than five"):
        assert needed in thin, needed


def test_the_mix_rule_says_what_to_do_when_fewer_than_two_recent_videos_ran_2x(text):
    picking = sections(text)["Picking the five ideas"]
    mix = next(line for line in picking.splitlines() if line.startswith("- Mix the sources"))
    assert "fewer than two" in mix, "the rule needs two strong recent videos, and a run can have fewer"
    assert "best-scoring recent videos" in mix and "`why`" in mix


def test_the_skill_says_what_the_paid_flag_means_and_not_to_copy_an_ads_angle(text):
    assert "`paid`" in sections(text)["Steps"], "step 4 names the field"
    picking = sections(text)["Picking the five ideas"]
    assert "`paid` is true" in picking and "paid partnership" in picking and "null" in picking


def test_the_skill_says_the_first_transcription_downloads_a_speech_model(text):
    assert "downloads a speech model" in text
    assert "first time" in text


def test_the_skill_says_where_it_really_writes(text):
    assert "work/.slots" in text
    assert "writes only inside" not in text.lower()  # the claim it replaces


def test_the_install_sentence_always_keeps_the_research_tools(text):
    assert "Speech to text skipped: install the video tools with uv sync --group video --inexact" in text
    for line in text.splitlines():
        if "uv sync --group video" in line:
            assert "--inexact" in line, line  # without it uv removes yt-dlp


def test_the_skill_explains_the_notices_about_creators_and_spending_caps(text):
    assert "returned nothing" in text
    assert "`research.cost_cap.<platform>`" in text


def test_the_skill_names_the_setting_that_gives_the_scrape_longer(text):
    assert "`research.scrape_seconds`" in text
    assert "10 to 1800" in text
    assert "me/profile.json" in text.split("`research.scrape_seconds`")[1][:120]
