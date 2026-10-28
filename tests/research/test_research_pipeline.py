"""kit.research.pipeline: a whole research run, start to finish, with the web and the speech tools faked."""

import hashlib
import importlib
import importlib.util
import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from kit import config
from kit.research import __main__ as cli
from kit.research import common, pipeline
from research_helpers import (
    NOW, apify, ig_post, make_yt_dlp, short_info, started, tt_post, visible, write_ideas_file, write_profile, write_watchlist,
)

ROOT = Path(__file__).resolve().parents[2]
MISSING = "Instagram and TikTok skipped: add APIFY_TOKEN to .env to include them"
TOKEN = "fake-value-for-tests"
X_LINK = "https://x.com/example_account/status/1000000000000000001"
HOME_PREFIX = "/".join(["", "Users", ""])  # a home folder path, built here so the source holds no such literal


def run_dir_of(root, day="2026-10-28", folder="runs"):
    return root / "research" / folder / day


def sample_dir_of(root, day="2026-10-28"):
    """Where an offline run on made-up videos writes: apart from the real runs, where no idea picker looks."""
    return run_dir_of(root, day, "samples")


def items_of(root, day="2026-10-28", folder="runs"):
    return json.loads((run_dir_of(root, day, folder) / "items.json").read_text(encoding="utf-8"))


def page_of(root, day="2026-10-28", folder="runs"):
    return (run_dir_of(root, day, folder) / "report.html").read_text(encoding="utf-8")


def channel(*titles, **info):
    return {f"vid{n}": short_info(title, views=1000 + 100 * n, likes=100 + 10 * n, comments=5 + n, **info) for n, title in enumerate(titles, 1)}


@pytest.fixture
def youtube(monkeypatch):
    """A YouTube channel with three Shorts, available to yt-dlp."""
    monkeypatch.setitem(sys.modules, "yt_dlp", make_yt_dlp({"example_channel": channel("One", "Two", "Three")}))


@pytest.fixture
def watching_youtube(me):
    write_watchlist(me, youtube=["@example_channel"])


# --- the four behaviours the brief names --------------------------------------------------------------


def test_offline_runs_without_network(kit_tree, no_network, monkeypatch, capsys):
    imported = []
    real = importlib.import_module
    monkeypatch.setattr(importlib, "import_module", lambda name, *a, **k: (imported.append(name), real(name, *a, **k))[1])

    def forbidden(*args, **kwargs):
        raise AssertionError("an offline run reached for the web")

    monkeypatch.setattr(common, "http_json", forbidden)
    monkeypatch.setattr(common, "http_bytes", forbidden)
    monkeypatch.setattr(common, "http_download", forbidden)

    assert cli.main(["run", "--offline"]) == 0

    data = items_of(kit_tree, folder="samples")
    assert data["mode"] == "offline" and data["videos"] and data["creators"]
    assert (sample_dir_of(kit_tree) / "report.html").is_file()
    assert not (kit_tree / "research" / "runs").exists(), "a sample never goes where the real runs are"
    assert not (kit_tree / "research" / "brain").exists()  # made-up videos never become history
    touched = [name for name in imported if name.startswith(("kit.zernio", "kit.transcribe", "kit.media", "yt_dlp"))]
    assert touched == []
    assert "Offline run" in capsys.readouterr().out


def test_no_apify_skips_with_message(kit_tree, me, web, youtube, watching_youtube, capsys):
    write_watchlist(me, instagram=["@example_gardener_one"], tiktok=["@example_tiktoker"], youtube=["@example_channel"])
    assert cli.main(["run", "--no-transcribe"]) == 0  # the run completes

    assert MISSING in page_of(kit_tree)
    data = items_of(kit_tree)
    assert MISSING in data["notices"]
    assert data["mode"] == "live"
    assert {v["platform"] for v in data["videos"]} == {"youtube"}  # YouTube still came through
    assert [u for u in web.urls() if "apify" in u] == []  # and Apify was never called
    assert MISSING in capsys.readouterr().out


def test_x_link_failure_noted(kit_tree, me, web, capsys):
    write_watchlist(me)
    write_ideas_file(me, f"# Ideas\n\n- Worth a video {X_LINK}\n")
    web.add("GET", "api.fxtwitter.com", common.HttpError(503, "HTTP 503"))
    web.add("GET", "api.vxtwitter.com", common.HttpError(None, "no network route"))

    assert cli.main(["run", "--no-transcribe"]) == 0

    (post,) = items_of(kit_tree)["x_posts"]
    assert post["status"] == "unread" and post["url"] == X_LINK
    page = page_of(kit_tree)
    assert X_LINK in page and "Unread" in page
    assert "X posts: 0 of 1 read" in capsys.readouterr().out


@pytest.mark.parametrize(
    "zone, folder, long_day",
    [
        ("Pacific/Auckland", "2026-10-29", "Thursday 29 October 2026"),
        ("America/Los_Angeles", "2026-10-28", "Wednesday 28 October 2026"),
    ],
)
def test_timezone_from_profile(kit_tree, me, zone, folder, long_day):
    write_profile(me, timezone=zone)  # 13:30 UTC on the 28th
    assert cli.main(["run", "--offline"]) == 0
    assert (sample_dir_of(kit_tree, folder) / "report.html").is_file()
    assert [p.name for p in (kit_tree / "research" / "samples").iterdir()] == [folder]
    data = items_of(kit_tree, folder, "samples")
    assert data["date"] == folder and data["timezone"] == zone
    assert common.parse_iso(data["generated_at"]).utcoffset().total_seconds() == 0  # stored in UTC, shown in the profile's zone
    assert long_day in visible(page_of(kit_tree, folder, "samples"))


# --- what a run leaves behind --------------------------------------------------------------------------


def test_items_json_has_the_agreed_shape(kit_tree):
    pipeline.run(offline=True, say=lambda *a, **k: None)
    data = items_of(kit_tree, folder="samples")
    assert set(data) == {
        "version", "date", "generated_at", "timezone", "name", "mode", "window_days", "notices", "sources",
        "transcripts", "creators", "videos", "videos_total", "videos_recent", "x_posts", "notes", "performance",
    }
    assert data["version"] == 1 and data["name"] == "Example Studio" and data["window_days"] == 14
    needed = {"id", "platform", "creator", "url", "posted_at", "title", "views", "likes", "comments", "engagement", "outlier",
              "creator_median", "creator_n", "low_sample", "in_window", "young", "set", "source", "rank", "hook", "hook_source"}
    for video in data["videos"]:
        assert needed <= set(video)
    outliers = [v["outlier"] for v in data["videos"] if v["outlier"] is not None]
    assert outliers == sorted(outliers, reverse=True)
    assert [v["rank"] for v in data["videos"]] == list(range(1, len(data["videos"]) + 1))
    assert {"platform", "creator", "pulled", "on_record", "in_window", "median_engagement", "low_sample", "best_outlier"} <= set(data["creators"][0])
    assert data["transcripts"].keys() == {"brain", "captions", "local", "missing"}


def test_a_run_writes_only_inside_research(kit_tree, me):
    def fingerprint(folder):
        return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.rglob("*")) if p.is_file()}

    before_me = fingerprint(me)
    pipeline.run(offline=True, say=lambda *a, **k: None)
    assert fingerprint(me) == before_me
    outside = [p for p in kit_tree.iterdir() if p.name != "research"]
    assert outside == []


def test_videos_are_capped_but_the_total_is_kept(kit_tree, me, monkeypatch):
    handles = [f"creator_{n}" for n in range(9)]
    monkeypatch.setitem(
        sys.modules, "yt_dlp", make_yt_dlp({h: {f"{h}_{n}": short_info(f"{h} {n}", likes=100 + n) for n in range(10)} for h in handles})
    )
    write_watchlist(me, youtube=[f"@{h}" for h in handles])
    pipeline.run(no_transcribe=True, say=lambda *a, **k: None)
    data = items_of(kit_tree)
    assert data["videos_total"] == 90 and len(data["videos"]) == pipeline.MAX_VIDEOS == 80


def test_the_run_prints_a_short_plain_summary(kit_tree, me, youtube, watching_youtube):
    lines = []
    pipeline.run(no_transcribe=True, say=lines.append)
    text = "\n".join(lines)
    assert lines[0] == "Research for Example Studio, Wednesday 28 October 2026"
    assert MISSING in lines
    assert "YouTube: 3 videos from 1 creator" in lines
    assert "X posts: none in me/ideas.md" in lines
    assert "Notes: 2 in me/ideas.md" in lines
    assert "Wrote research/runs/2026-10-28/items.json and report.html" in lines
    assert lines[-1].startswith("Next:") and "ideas" in lines[-1]
    assert str(kit_tree) not in text and HOME_PREFIX not in text  # paths are shown from the kit folder down


def test_creators_who_returned_nothing_are_named_in_the_summary_and_the_report(kit_tree, me, web):
    (kit_tree / ".env").write_text(f"APIFY_TOKEN={TOKEN}\n", encoding="utf-8")
    write_watchlist(me, instagram=["@example_gardener_one", "@example_gardener_two"])
    apify(web, ig=[ig_post("AAA111"), ig_post("BBB222", days_ago=4)])
    lines = []
    pipeline.run(no_transcribe=True, say=lines.append)
    named = "Instagram returned no videos for @example_gardener_two"
    assert "Instagram: 2 videos from 1 of 2 creators (US$0.12)" in lines  # not "from 1 creator", as if it were everyone
    assert named in lines
    data = items_of(kit_tree)
    assert named in data["notices"]
    text = visible(page_of(kit_tree))
    assert text.count(named) == 1 and text.index(named) < text.index("Ideas to film")
    assert "2 videos from 1 of 2 creators" in text  # the line for Instagram under "How this run went"


def test_a_capped_run_is_explained_in_the_summary_and_the_report(kit_tree, me, web):
    (kit_tree / ".env").write_text(f"APIFY_TOKEN={TOKEN}\n", encoding="utf-8")
    write_watchlist(me, instagram=["@example_gardener_one", "@example_gardener_two"])
    apify(web, ig=[ig_post("AAA111")], ig_run=started("run-ig", "ds-ig", "ABORTED", 0.25))
    lines = []
    pipeline.run(no_transcribe=True, say=lines.append)
    notice = (
        "Instagram stopped at your spending cap of US$0.25, so no videos came back for @example_gardener_two. "
        "Raise research.cost_cap.instagram in me/profile.json to pull more"
    )
    assert notice in lines
    assert notice in visible(page_of(kit_tree))
    assert items_of(kit_tree)["sources"]["instagram"]["stopped_by"] == "cap"


# --- the creator's files --------------------------------------------------------------------------------


def test_an_empty_watch_list_and_no_ideas_file_are_explained_not_fatal(kit_tree, me):
    (me / "watchlist.json").unlink()
    (me / "ideas.md").unlink()
    lines = []
    pipeline.run(no_transcribe=True, say=lines.append)
    data = items_of(kit_tree)
    assert "Your watch list is empty. Add creators to me/watchlist.json, or say: set me up" in data["notices"]
    assert data["videos"] == [] and data["notes"] == []


def test_a_creator_who_is_not_set_up_gets_a_sentence(tmp_path, monkeypatch):
    monkeypatch.setenv("KIT_ME", str(tmp_path / "empty-me"))
    with pytest.raises(config.NotOnboarded):
        pipeline.run(offline=True, say=lambda *a, **k: None)


# --- the same day, run twice ---------------------------------------------------------------------------------


def test_a_second_run_on_the_same_day_keeps_the_ideas_already_written(kit_tree, me, youtube, watching_youtube):
    pipeline.run(no_transcribe=True, say=lambda *a, **k: None)
    first = items_of(kit_tree)
    ideas = [{
        "n": 1, "title": "Idea that was kept", "angle": "a", "why": "w", "format": "f",
        "evidence": [{"url": first["videos"][0]["url"], "creator": "example_channel", "outlier": 1.0}], "hooks": [],
    }]
    ideas_path = run_dir_of(kit_tree) / "ideas.json"
    ideas_path.write_text(json.dumps(ideas), encoding="utf-8")

    lines = []
    pipeline.run(no_transcribe=True, say=lines.append)
    assert json.loads(ideas_path.read_text(encoding="utf-8")) == ideas
    assert "Kept the ideas already written for today in research/runs/2026-10-28/ideas.json" in lines
    assert "Idea that was kept" in page_of(kit_tree)  # and the report shows them


def test_an_offline_run_on_the_day_of_a_real_one_leaves_it_alone(kit_tree, me, youtube, watching_youtube, capsys):
    pipeline.run(no_transcribe=True, say=lambda *a, **k: None)
    before = {p.name: p.read_bytes() for p in run_dir_of(kit_tree).iterdir()}
    assert cli.main(["run", "--offline"]) == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert {p.name: p.read_bytes() for p in run_dir_of(kit_tree).iterdir()} == before
    assert items_of(kit_tree, folder="samples")["mode"] == "offline"
    # the printout says where the sample went and that its videos are made up
    assert "Wrote research/samples/2026-10-28/items.json and report.html" in out
    closing = out.strip().splitlines()[-2:]
    assert any("made-up" in line and "research/samples/" in line for line in closing), closing


def test_the_guard_against_a_sample_over_a_real_run_tells_an_agent_to_ask_first(kit_tree, me, capsys):
    # A sample only ever goes to research/samples/, so this guard should never fire. If a real pull ever sits
    # there, the run stops and changes nothing, and its words never send an agent to move or delete a folder.
    folder = sample_dir_of(kit_tree)
    folder.mkdir(parents=True)
    (folder / "items.json").write_text(json.dumps({"mode": "live"}), encoding="utf-8")
    assert cli.main(["run", "--offline"]) == 1
    err = capsys.readouterr().err
    assert "Ask the creator" in err and "never move or delete" in err.lower()
    assert "Move or delete" not in err
    assert json.loads((folder / "items.json").read_text(encoding="utf-8")) == {"mode": "live"}


def test_a_real_run_replaces_a_sample_left_among_the_runs_by_an_older_kit(kit_tree, me, youtube, watching_youtube):
    folder = run_dir_of(kit_tree)
    folder.mkdir(parents=True)
    (folder / "items.json").write_text(json.dumps({"mode": "offline", "videos": []}), encoding="utf-8")
    (folder / "ideas.json").write_text(json.dumps([{"n": 1, "title": "Sample idea"}]), encoding="utf-8")
    lines = []
    pipeline.run(no_transcribe=True, say=lines.append)
    assert items_of(kit_tree)["mode"] == "live"
    assert not (folder / "ideas.json").exists()  # the sample's ideas went with it
    assert "Replaced the offline sample from earlier today" in lines


# --- a sample run never shadows the creator's real ideas ----------------------------------------------------------------


REAL_IDEA = "Ask the plant what it needs before you water it"
SAMPLE_IDEA = "A made-up idea from the sample videos"


def an_idea_titled(title):
    return [{"n": 1, "title": title, "angle": "a", "why": "w", "format": "Tutorial", "evidence": [], "hooks": ["One hook."]}]


def load_skill_script(monkeypatch, skill, name):
    """Import .claude/skills/<skill>/kit/<name>.py, with its own folder on the path as it has when it runs."""
    folder = ROOT / ".claude" / "skills" / skill / "kit"
    monkeypatch.syspath_prepend(str(folder))
    spec = importlib.util.spec_from_file_location(f"research_test_{skill.replace('-', '_')}_{name}", folder / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("days_later", [0, 1])
def test_a_sample_run_after_a_real_one_leaves_hooks_and_both_video_briefs_on_the_real_run(
    kit_tree, me, youtube, watching_youtube, monkeypatch, capsys, days_later
):
    pipeline.run(no_transcribe=True, say=lambda *a, **k: None)
    (run_dir_of(kit_tree) / "ideas.json").write_text(json.dumps(an_idea_titled(REAL_IDEA)), encoding="utf-8")

    monkeypatch.setattr(common, "utcnow", lambda: NOW + timedelta(days=days_later))
    sample = pipeline.run(offline=True, say=lambda *a, **k: None)
    (sample / "ideas.json").write_text(json.dumps(an_idea_titled(SAMPLE_IDEA)), encoding="utf-8")  # as an agent might
    capsys.readouterr()

    assert cli.main(["hooks", "1"]) == 0
    out = capsys.readouterr().out
    assert f"Idea 1: {REAL_IDEA}" in out and "Run: research/runs/2026-10-28" in out and SAMPLE_IDEA not in out

    for skill, script in (("ui-story", "plan_brief"), ("mini-me", "brief")):
        module = load_skill_script(monkeypatch, skill, script)
        slug = module.save_idea(1, f"from-{skill}")
        saved = json.loads((config.work_dir(slug) / "idea.json").read_text(encoding="utf-8"))
        assert saved["title"] == REAL_IDEA, skill


# --- the brain ---------------------------------------------------------------------------------------------------


def test_the_brain_grows_and_gives_the_next_run_its_history(kit_tree, me, monkeypatch):
    write_watchlist(me, youtube=["@example_channel"])
    day_one, day_two = NOW, NOW + timedelta(days=1)
    clock = {"now": day_one}
    monkeypatch.setattr(common, "utcnow", lambda: clock["now"])

    monkeypatch.setitem(sys.modules, "yt_dlp", make_yt_dlp({"example_channel": {"a": short_info("A"), "b": short_info("B")}}))
    pipeline.run(no_transcribe=True, say=lambda *a, **k: None)
    first = items_of(kit_tree, "2026-10-28")
    assert first["creators"][0]["on_record"] == 2 and first["creators"][0]["low_sample"] is True
    assert (kit_tree / "research" / "brain" / "youtube" / "example_channel.json").is_file()

    clock["now"] = day_two
    monkeypatch.setitem(sys.modules, "yt_dlp", make_yt_dlp({"example_channel": {"b": short_info("B"), "c": short_info("C")}}))
    pipeline.run(no_transcribe=True, say=lambda *a, **k: None)
    second = items_of(kit_tree, "2026-10-29")
    assert second["creators"][0]["pulled"] == 2
    assert second["creators"][0]["on_record"] == 3  # a, b and c, with b counted once
    assert second["creators"][0]["low_sample"] is False


def test_the_counts_leave_out_earlier_standouts_from_past_runs(kit_tree, me, monkeypatch):
    """A second run lists a big video from the first run as an earlier standout. The page's numbers count only what
    this run pulled, the same as the terminal: 5 pulled and 1 older standout is 5 videos, not 6."""
    write_watchlist(me, youtube=["@example_channel"])
    clock = {"now": NOW}
    monkeypatch.setattr(common, "utcnow", lambda: clock["now"])
    monkeypatch.setitem(sys.modules, "yt_dlp", make_yt_dlp({"example_channel": {"big": short_info("Big", likes=50000)}}))
    pipeline.run(no_transcribe=True, say=lambda *a, **k: None)

    clock["now"] = NOW + timedelta(days=1)
    pulled = {f"v{n}": short_info(f"Video {n}", likes=100 + n, ts=int(NOW.timestamp()) - (1 + 20 * (n % 2)) * 86400)
              for n in range(5)}
    monkeypatch.setitem(sys.modules, "yt_dlp", make_yt_dlp({"example_channel": pulled}))
    printed = []
    pipeline.run(no_transcribe=True, say=printed.append)
    data = items_of(kit_tree, "2026-10-29")
    assert any(v.get("source") == "brain" for v in data["videos"]), "the first run's big video comes back as a standout"
    assert any(line.startswith("YouTube: 5 videos from 1 creator") for line in printed), printed
    assert data["videos_total"] == 5
    recent_this_run = [v for v in data["videos"] if v.get("source") == "run" and v.get("set") == "recent"]
    page = page_of(kit_tree, "2026-10-29")
    assert '<dt>Videos</dt><dd class="num">5</dd>' in page
    assert f"5 videos from 1 creator this time, {len(recent_this_run)} of them from the last" in visible(page)


def test_a_transcript_found_once_is_not_fetched_again(kit_tree, me, web, monkeypatch):
    write_watchlist(me, youtube=["@example_channel"])
    words = "Water basil in the morning and never at night because the roots sit in cold water"
    info = {"v1": short_info("Basil", captions="https://cdn.example.com/c.json3")}
    monkeypatch.setitem(sys.modules, "yt_dlp", make_yt_dlp({"example_channel": info}))
    web.add("GET", "cdn.example.com/c.json3", json.dumps({"events": [{"segs": [{"utf8": words}]}]}).encode())
    clock = {"now": NOW}
    monkeypatch.setattr(common, "utcnow", lambda: clock["now"])

    pipeline.run(say=lambda *a, **k: None)
    (video,) = items_of(kit_tree, "2026-10-28")["videos"]
    assert video["transcript_source"] == "YouTube captions" and video["hook"].startswith("Water basil in the morning")

    clock["now"] = NOW + timedelta(days=1)
    web.calls.clear()
    web.routes.clear()  # the caption file is no longer there
    pipeline.run(say=lambda *a, **k: None)
    (again,) = items_of(kit_tree, "2026-10-29")["videos"]
    assert again["transcript"].startswith("Water basil in the morning") and again["transcript_source"] == "YouTube captions"
    assert [u for u in web.urls() if "c.json3" in u] == []


def test_a_missing_speech_module_is_a_notice_not_a_failure(kit_tree, me, youtube, watching_youtube, monkeypatch):
    monkeypatch.setitem(sys.modules, "kit.transcribe", None)
    pipeline.run(say=lambda *a, **k: None)
    data = items_of(kit_tree)
    assert "Speech to text skipped: the kit's transcription tools are not available in this copy" in data["notices"]
    assert data["videos"] and (run_dir_of(kit_tree) / "report.html").is_file()


def test_speech_to_text_runs_for_videos_with_no_captions(kit_tree, me, youtube, watching_youtube, fake_speech, monkeypatch):
    from kit.research import transcribe_queue

    monkeypatch.setattr(transcribe_queue, "fetch_audio", lambda item, folder: _wav(folder, item))
    fake_speech.text = "Three weeks in every basil plant does this and here is why it happens"
    pipeline.run(say=lambda *a, **k: None)
    data = items_of(kit_tree)
    assert data["transcripts"]["local"] == 3 and data["transcripts"]["missing"] == 0
    assert all(v["transcript_source"] == "local transcription" for v in data["videos"])
    assert not (run_dir_of(kit_tree) / "media").exists()  # the downloads are gone


def _wav(folder, item):
    path = folder / (item["id"].replace(":", "_") + ".wav")
    path.write_bytes(b"RIFF")
    return path


# --- Apify, through the creator's env file ---------------------------------------------------------------------------


def test_the_apify_token_is_read_from_the_env_file(kit_tree, me, web, capsys):
    (kit_tree / ".env").write_text(f"APIFY_TOKEN={TOKEN}\n", encoding="utf-8")
    write_watchlist(me, instagram=["@example_gardener_one"], tiktok=["@example_tiktoker"])
    apify(web, ig=[ig_post("AAA111"), ig_post("BBB222", days_ago=5), ig_post("CCC333", days_ago=9)], tt=[tt_post("7000000000000000001")])
    assert cli.main(["run", "--no-transcribe"]) == 0
    data = items_of(kit_tree)
    assert {v["platform"] for v in data["videos"]} == {"instagram", "tiktok"}
    assert MISSING not in data["notices"]
    everything = capsys.readouterr().out + json.dumps(data) + page_of(kit_tree)
    assert TOKEN not in everything


def test_no_apify_with_a_token_leaves_apify_alone(kit_tree, me, web, monkeypatch):
    (kit_tree / ".env").write_text(f"APIFY_TOKEN={TOKEN}\n", encoding="utf-8")
    write_watchlist(me, instagram=["@example_gardener_one"])
    pipeline.run(no_apify=True, no_transcribe=True, say=lambda *a, **k: None)
    assert "Instagram and TikTok skipped: this run was started with --no-apify" in items_of(kit_tree)["notices"]
    assert web.calls == []


def test_a_run_takes_neither_key_from_the_environment(kit_tree, me, web, youtube, fake_zernio, monkeypatch, capsys):
    # The run reads APIFY_TOKEN and ZERNIO_API_KEY from .env alone: a shell that carries them changes nothing.
    monkeypatch.setenv("APIFY_TOKEN", TOKEN)
    monkeypatch.setenv("ZERNIO_API_KEY", TOKEN)
    write_watchlist(me, instagram=["@example_gardener_one"], youtube=["@example_channel"])
    pipeline.run(no_transcribe=True, say=lambda *a, **k: None)
    data = items_of(kit_tree)
    assert MISSING in data["notices"]
    assert data["performance"]["message"] == "Performance skipped: add ZERNIO_API_KEY to .env to include your own results"
    assert [u for u in web.urls() if "apify" in u] == [] and fake_zernio.keys == [] and fake_zernio.calls == []


# --- the creator's own results ------------------------------------------------------------------------------------


def test_performance_is_included_when_the_key_is_set(kit_tree, me, fake_zernio, monkeypatch):
    (kit_tree / ".env").write_text(f"ZERNIO_API_KEY={TOKEN}\n", encoding="utf-8")
    write_watchlist(me)
    when = (NOW - timedelta(days=7)).isoformat()
    post = {"publishedAt": when, "mediaType": "video", "platformPostUrl": "https://www.instagram.com/reel/ABC/", "content": "Reel about basil",
            "isSharedToFeed": True, "analytics": {"views": 900, "likes": 80, "comments": 6, "saves": 20, "videoDurationSeconds": 30}}
    fake_zernio.pages = [{"posts": [post], "pagination": {"pages": 1}, "accounts": [], "overview": {}}]
    pipeline.run(no_transcribe=True, say=lambda *a, **k: None)
    data = items_of(kit_tree)
    assert data["performance"]["status"] == "ok" and data["performance"]["feed_reels"] == 1
    assert "Your own results" in page_of(kit_tree) and "Reel about basil" in page_of(kit_tree)
    assert TOKEN not in json.dumps(data) + page_of(kit_tree)


def test_without_a_zernio_key_the_report_says_so(kit_tree, me, youtube, watching_youtube):
    pipeline.run(no_transcribe=True, say=lambda *a, **k: None)
    assert items_of(kit_tree)["performance"] == {
        "status": "skipped", "message": "Performance skipped: add ZERNIO_API_KEY to .env to include your own results",
    }
    assert "Performance skipped: add ZERNIO_API_KEY to .env to include your own results" in page_of(kit_tree)
