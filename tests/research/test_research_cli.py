"""python -m kit.research: the commands a creator (or Claude) runs."""

import io
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from kit.research import __main__ as cli
from research_helpers import visible

ROOT = Path(__file__).resolve().parents[2]
HOOKS = "\n".join(f"- Hook line number {n} for the basil idea." for n in range(1, 7))


def write_ideas(run_dir, urls):
    ideas = [
        {
            "n": n, "title": f"Idea {n} about basil", "angle": f"Angle {n}", "why": f"Why {n}", "format": "Tutorial",
            "evidence": [{"url": url, "creator": "example_gardener_one", "outlier": 4.0 + n}], "hooks": [f"Starter hook {n}."],
        }
        for n, url in enumerate(urls, 1)
    ]
    (run_dir / "ideas.json").write_text(json.dumps(ideas), encoding="utf-8")
    return ideas


def top_urls(run_dir, count=3):
    return [v["url"] for v in json.loads((run_dir / "items.json").read_text())["videos"][:count]]


@pytest.fixture
def with_ideas(live_run):
    profile, run_dir = live_run
    write_ideas(run_dir, top_urls(run_dir))
    return profile, run_dir


# --- run --------------------------------------------------------------------------------------------------


def test_run_offline_prints_the_summary(kit_tree, capsys):
    assert cli.main(["run", "--offline"]) == 0
    out, err = capsys.readouterr()
    assert out.startswith("Research for Example Studio, Wednesday 28 October 2026")
    assert "Wrote research/samples/2026-10-28/items.json and report.html" in out
    assert err == ""


def test_every_flag_is_accepted(kit_tree, capsys):
    assert cli.main(["run", "--offline", "--no-apify", "--no-transcribe"]) == 0


def test_a_creator_who_is_not_set_up_gets_the_sentence_and_nothing_else(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KIT_ME", str(tmp_path / "nothing-here"))
    assert cli.main(["run", "--offline"]) == 1
    out, err = capsys.readouterr()
    assert out == "" and err == "The kit has not been set up yet. Say: set me up.\n"


def test_a_broken_profile_is_reported_in_plain_words(example_me, monkeypatch, capsys):
    (example_me / "profile.json").write_text("{not json", encoding="utf-8")
    assert cli.main(["run", "--offline"]) == 1
    err = capsys.readouterr().err
    assert "me/profile.json is not valid JSON" in err and "Traceback" not in err


def test_no_command_shows_the_usage():
    with pytest.raises(SystemExit) as caught:
        cli.main([])
    assert caught.value.code == 2


def test_the_module_runs_with_dash_m():
    def help_for(*args):
        done = subprocess.run([sys.executable, "-m", "kit.research", *args, "--help"], cwd=ROOT, capture_output=True, text=True, timeout=60)
        assert done.returncode == 0, done.stderr
        return done.stdout

    top = help_for()
    for command in ("run", "report", "hooks"):
        assert command in top
    run = help_for("run")
    for flag in ("--offline", "--no-apify", "--no-transcribe"):
        assert flag in run
    assert "--save" in help_for("hooks")
    assert "--date" in help_for("report")


# --- report -----------------------------------------------------------------------------------------------


def test_report_rebuilds_the_page_from_the_pull_and_the_ideas(with_ideas, capsys):
    _, run_dir = with_ideas
    assert cli.main(["report"]) == 0
    assert "Wrote research/runs/2026-10-28/report.html" in capsys.readouterr().out
    text = visible((run_dir / "report.html").read_text(encoding="utf-8"))
    assert "Idea 1 about basil" in text and "Idea 3 about basil" in text


def test_report_with_a_bad_ideas_file_lists_the_problems_and_exits_one(live_run, capsys):
    _, run_dir = live_run
    (run_dir / "ideas.json").write_text(json.dumps([{"n": 1, "title": "Half an idea"}]), encoding="utf-8")
    assert cli.main(["report"]) == 1
    err = capsys.readouterr().err
    assert "ideas.json needs fixing" in err and "Idea 1 is missing angle" in err
    assert "ideas.json needs fixing" in visible((run_dir / "report.html").read_text(encoding="utf-8"))  # and the page says so too


def test_report_for_a_date_with_no_run(live_run, capsys):
    assert cli.main(["report", "--date", "2026-10-20"]) == 1
    assert capsys.readouterr().err == "There is no research run for 2026-10-20. The latest is 2026-10-28.\n"


def test_report_with_no_run_at_all(kit_tree, capsys):
    assert cli.main(["report"]) == 1
    assert capsys.readouterr().err == "There is no research run yet. Say: run my research.\n"


@pytest.mark.parametrize("date", ["tomorrow", "2026-13-40", "28/10/2026"])
def test_a_date_that_is_not_a_date_is_explained(live_run, capsys, date):
    assert cli.main(["report", "--date", date]) == 1
    assert "A date looks like 2026-10-28" in capsys.readouterr().err


def test_a_named_date_picks_that_run(live_run, kit_tree, capsys):
    _, run_dir = live_run
    older = run_dir.parent / "2026-10-20"
    shutil.copytree(run_dir, older)
    assert cli.main(["report", "--date", "2026-10-20"]) == 0
    assert "Wrote research/runs/2026-10-20/report.html" in capsys.readouterr().out


# --- hooks ------------------------------------------------------------------------------------------------


def test_hooks_prints_what_claude_needs_to_write_them(with_ideas, capsys):
    assert cli.main(["hooks", "2"]) == 0
    out = capsys.readouterr().out
    assert "Idea 2: Idea 2 about basil" in out
    assert "research/runs/2026-10-28" in out
    assert "me/voice/VOICE.md" in out and "between 5 and 10" in out
    assert "Starter hook 2." in out  # the hook it already has


def test_hooks_for_an_idea_that_is_not_there(with_ideas, capsys):
    assert cli.main(["hooks", "9"]) == 1
    assert capsys.readouterr().err == "There is no idea number 9 in research/runs/2026-10-28. It has ideas 1 to 3.\n"


def test_hooks_before_any_ideas_are_written(live_run, capsys):
    assert cli.main(["hooks", "1"]) == 1
    err = capsys.readouterr().err
    assert err == "There are no ideas to work from yet. Ask Claude to write the ideas for the latest run first, or say: run my research.\n"


def test_hooks_with_no_run_at_all(kit_tree, capsys):
    assert cli.main(["hooks", "1"]) == 1
    assert "no ideas to work from yet" in capsys.readouterr().err


def test_hooks_needs_a_whole_number(with_ideas):
    with pytest.raises(SystemExit) as caught:
        cli.main(["hooks", "two"])
    assert caught.value.code == 2


def test_hooks_use_the_latest_run_that_has_ideas_and_say_so(with_ideas, capsys):
    _, run_dir = with_ideas
    newer = run_dir.parent / "2026-10-29"
    newer.mkdir()
    shutil.copy(run_dir / "items.json", newer / "items.json")
    assert cli.main(["hooks", "1"]) == 0
    out = capsys.readouterr().out
    assert "research/runs/2026-10-28" in out
    assert "The run on 2026-10-29 is newer but has no ideas yet." in out


def test_saving_hooks_stores_them_and_refreshes_the_report(with_ideas, monkeypatch, capsys):
    _, run_dir = with_ideas
    monkeypatch.setattr(sys, "stdin", io.StringIO(HOOKS))
    assert cli.main(["hooks", "2", "--save"]) == 0
    assert capsys.readouterr().out == "Saved 6 hooks for idea 2 in research/runs/2026-10-28/ideas.json and refreshed report.html\n"
    saved = json.loads((run_dir / "ideas.json").read_text())
    assert saved[1]["hooks"][0] == "Hook line number 1 for the basil idea." and len(saved[1]["hooks"]) == 6
    assert saved[0]["hooks"] == ["Starter hook 1."]  # the other ideas are untouched
    assert "Hook line number 6 for the basil idea." in visible((run_dir / "report.html").read_text(encoding="utf-8"))


def test_saving_too_few_hooks_changes_nothing(with_ideas, monkeypatch, capsys):
    _, run_dir = with_ideas
    before = (run_dir / "ideas.json").read_bytes()
    monkeypatch.setattr(sys, "stdin", io.StringIO("- one\n- two\n- three"))
    assert cli.main(["hooks", "2", "--save"]) == 1
    assert "Write between 5 and 10 hooks for each idea. This is 3." in capsys.readouterr().err
    assert (run_dir / "ideas.json").read_bytes() == before


def test_saving_hooks_from_nothing_changes_nothing(with_ideas, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n\n"))
    assert cli.main(["hooks", "1", "--save"]) == 1
    assert "This is 0." in capsys.readouterr().err
