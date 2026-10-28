"""kit.research.brain: what earlier runs saw, kept under research/brain/ so each run has history to compare with."""

import json

from kit.research import brain


def seen(vid, creator="alpha", platform="instagram", **extra):
    base = {
        "id": f"ig:{vid}", "platform": platform, "creator": creator, "url": f"https://example.com/{vid}",
        "title": f"Title {vid}", "posted_at": "2026-10-20T10:00:00+00:00", "views": 1000, "likes": 100, "comments": 10,
        "duration": 30, "source": "run",
    }
    base.update(extra)
    return base


def test_an_empty_brain_has_no_history(kit_tree):
    assert brain.load_history() == {}


def test_what_a_run_saw_is_kept_by_platform_and_creator(kit_tree):
    brain.update([seen("a"), seen("b", creator="Bravo"), seen("t", platform="tiktok", id="tt:t")], today="2026-10-28")
    folder = kit_tree / "research" / "brain"
    assert sorted(str(p.relative_to(folder)) for p in folder.rglob("*.json")) == [
        "instagram/alpha.json", "instagram/bravo.json", "tiktok/alpha.json",
    ]
    stored = json.loads((folder / "instagram" / "alpha.json").read_text())
    assert stored["version"] == 1 and stored["platform"] == "instagram" and stored["creator"] == "alpha"
    assert set(stored["items"]) == {"ig:a"}


def test_history_comes_back_keyed_by_platform_and_lower_case_creator(kit_tree):
    brain.update([seen("a", creator="Alpha"), seen("b", creator="alpha")], today="2026-10-28")
    history = brain.load_history()
    assert list(history) == [("instagram", "alpha")]
    assert set(history[("instagram", "alpha")]) == {"ig:a", "ig:b"}
    record = history[("instagram", "alpha")]["ig:a"]
    assert (record["likes"], record["comments"], record["views"]) == (100, 10, 1000)
    assert record["first_seen"] == "2026-10-28" and record["last_seen"] == "2026-10-28"
    assert record["posted_at"] == "2026-10-20T10:00:00+00:00" and record["url"] == "https://example.com/a"


def test_a_later_run_updates_the_numbers_and_keeps_the_first_sighting(kit_tree):
    brain.update([seen("a", likes=100)], today="2026-10-20")
    brain.update([seen("a", likes=900, comments=40)], today="2026-10-28")
    record = brain.load_history()[("instagram", "alpha")]["ig:a"]
    assert (record["likes"], record["comments"]) == (900, 40)
    assert record["first_seen"] == "2026-10-20" and record["last_seen"] == "2026-10-28"


def test_whether_a_video_was_a_paid_partnership_is_kept_for_later_runs(kit_tree):
    brain.update([seen("a", paid=True), seen("b", paid=False), seen("c")], today="2026-10-28")
    records = brain.load_history()[("instagram", "alpha")]
    assert (records["ig:a"]["paid"], records["ig:b"]["paid"], records["ig:c"]["paid"]) == (True, False, None)


def test_a_transcript_is_kept_when_a_later_run_has_none(kit_tree):
    said = "Water basil in the morning and never at night because the roots sit in cold water"
    brain.update([seen("a", transcript=said, transcript_source="YouTube captions")], today="2026-10-20")
    brain.update([seen("a")], today="2026-10-28")
    record = brain.load_history()[("instagram", "alpha")]["ig:a"]
    assert record["transcript"] == said and record["transcript_source"] == "YouTube captions"


def test_known_transcripts_are_listed_by_video(kit_tree):
    said = "Said in the first video, which goes on for long enough to count as a real transcript"
    brain.update([seen("a", transcript=said, transcript_source="TikTok subtitles"), seen("b")], today="2026-10-28")
    assert brain.known_transcripts(brain.load_history()) == {"ig:a": {"text": said, "source": "TikTok subtitles"}}


def test_a_video_that_only_came_from_the_brain_is_not_saved_again(kit_tree):
    brain.update([seen("a", source="brain", likes=5)], today="2026-10-28")
    assert brain.load_history() == {}


def test_only_the_newest_two_hundred_per_creator_are_kept(kit_tree):
    many = [seen(f"v{n:03d}", posted_at=f"2026-{1 + n // 28:02d}-{1 + n % 28:02d}T10:00:00+00:00") for n in range(230)]
    brain.update(many, today="2026-10-28")
    kept = brain.load_history()[("instagram", "alpha")]
    assert len(kept) == brain.KEEP == 200
    assert "ig:v229" in kept and "ig:v000" not in kept


def test_a_transcript_too_short_to_show_how_a_video_opens_is_not_kept(kit_tree):
    brain.update([seen("a", transcript="Too short", transcript_source="TikTok subtitles")], today="2026-10-28")
    assert "transcript" not in brain.load_history()[("instagram", "alpha")]["ig:a"]


def test_a_very_long_transcript_is_cut(kit_tree):
    brain.update([seen("a", transcript="word " * 5000, transcript_source="local transcription")], today="2026-10-28")
    assert len(brain.load_history()[("instagram", "alpha")]["ig:a"]["transcript"]) <= brain.MAX_TRANSCRIPT


def test_an_unreadable_file_is_set_aside_not_overwritten_in_silence(kit_tree):
    brain.update([seen("a")], today="2026-10-20")
    path = kit_tree / "research" / "brain" / "instagram" / "alpha.json"
    path.write_text("{not json", encoding="utf-8")
    assert brain.load_history() == {}
    notices = brain.update([seen("b")], today="2026-10-28")
    assert notices == ["The saved history for alpha on Instagram could not be read, so it was set aside as alpha.json.unreadable and started again"]
    assert (path.parent / "alpha.json.unreadable").read_text() == "{not json"
    assert set(brain.load_history()[("instagram", "alpha")]) == {"ig:b"}


def test_a_creator_name_cannot_point_outside_the_brain(kit_tree):
    brain.update([seen("a", creator="../../escape"), seen("b", creator="Two Words & More")], today="2026-10-28")
    folder = kit_tree / "research" / "brain"
    written = [p for p in kit_tree.rglob("*.json")]
    assert written and all(folder in p.parents for p in written)
    assert {key[1] for key in brain.load_history()} == {"../../escape", "two words & more"}


def test_files_are_written_whole(kit_tree):
    brain.update([seen("a")], today="2026-10-28")
    leftovers = [p for p in (kit_tree / "research" / "brain").rglob("*") if p.is_file() and p.suffix != ".json"]
    assert leftovers == []


def test_odd_records_in_a_file_are_ignored(kit_tree):
    brain.update([seen("a")], today="2026-10-28")
    path = kit_tree / "research" / "brain" / "instagram" / "alpha.json"
    data = json.loads(path.read_text())
    data["items"]["ig:weird"] = "not a record"
    data["items"]["ig:noid"] = {"likes": 5}
    path.write_text(json.dumps(data))
    assert set(brain.load_history()[("instagram", "alpha")]) == {"ig:a"}
