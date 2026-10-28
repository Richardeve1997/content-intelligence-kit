"""kit.research.transcribe_queue: transcripts from the brain, from free captions, or from local speech to text."""

import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

from kit.research import common, rank, transcribe_queue as queue

LONG = "Water basil in the morning and never at night because the roots sit in cold water until sunrise"


def clip(vid, outlier=2.0, **extra):
    base = {
        "id": f"ig:{vid}", "platform": "instagram", "creator": "alpha", "url": f"https://example.com/{vid}",
        "title": f"Caption of {vid}", "in_window": True, "source": "run", "set": "recent", "outlier": outlier,
    }
    base.update(extra)
    return base


def stub_fetch(calls=None):
    """A stand-in for downloading a video: leaves a small file in the work folder."""

    def fetch(item, workdir):
        if calls is not None:
            calls.append(item["id"])
        path = Path(workdir) / (item["id"].replace(":", "_") + ".wav")
        path.write_bytes(b"RIFF")
        return path

    return fetch


# --- reading caption files ------------------------------------------------------------------------


def test_youtube_captions_are_joined_and_cleaned():
    raw = json.dumps(
        {"events": [{"segs": [{"utf8": "Basil "}, {"utf8": "dies"}]}, {"segs": [{"utf8": "[Music]"}]},
                    {"tStartMs": 0}, {"segs": [{"utf8": "in week\nthree"}]}]}
    ).encode()
    assert queue.parse_json3(raw) == "Basil dies in week three"


def test_tiktok_subtitles_are_joined_and_cleaned():
    raw = b"""WEBVTT

00:00:00.000 --> 00:00:02.000
Basil dies

00:00:02.000 --> 00:00:04.000
<c>in week</c> three

00:00:04.000 --> 00:00:05.000
in week three
"""
    assert queue.parse_vtt(raw) == "Basil dies in week three"


@pytest.mark.parametrize(
    "text, usable",
    [(LONG, True), ("Too short to mean anything", False), ("", False), (None, False),
     ("Thanks for watching", False), ("you", False), ("Thank you for watching!", False)],
)
def test_which_transcripts_are_worth_keeping(text, usable):
    assert queue.is_usable(text) is usable


def test_the_hook_comes_from_the_transcript_else_the_caption():
    spoken = clip("a", transcript=LONG + ". And then more follows here.", title="Caption of a")
    silent = clip("b", title="Caption of b")
    junk = clip("c", transcript="Thanks for watching", title="Caption of c")
    rank.attach_hooks([spoken, silent, junk])
    assert spoken["hook"] == LONG + "." and spoken["hook_source"] == "transcript"
    assert silent["hook"] == "Caption of b" and silent["hook_source"] == "caption"
    assert junk["hook"] == "Caption of c" and junk["hook_source"] == "caption"


# --- what is already known, and what captions give -------------------------------------------------


def test_transcripts_from_earlier_runs_are_used_first_and_nothing_is_fetched(tmp_path, web):
    videos = [clip("a"), clip("b")]
    known = {"ig:a": {"text": LONG, "source": "earlier run"}}
    outcome = queue.attach(videos, known, local=False, workdir=tmp_path / "media")
    assert videos[0]["transcript"] == LONG and videos[0]["transcript_source"] == "earlier run"
    assert "transcript" not in videos[1]
    assert (outcome.brain, outcome.captions, outcome.local, outcome.missing) == (1, 0, 0, 1)
    assert web.calls == []


def test_free_captions_are_fetched_for_youtube_and_tiktok(tmp_path, web):
    web.add("GET", "cdn.example.com/yt.json3", json.dumps({"events": [{"segs": [{"utf8": LONG}]}]}).encode())
    web.add("GET", "cdn.example.com/tt.vtt", ("WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n" + LONG + "\n").encode())
    web.add("GET", "cdn.example.com/short.vtt", b"WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nToo short\n")
    videos = [
        clip("y", platform="youtube", id="yt:y", caption_url="https://cdn.example.com/yt.json3"),
        clip("t", platform="tiktok", id="tt:t", subtitle_url="https://cdn.example.com/tt.vtt"),
        clip("s", platform="tiktok", id="tt:s", subtitle_url="https://cdn.example.com/short.vtt"),
        clip("gone", platform="tiktok", id="tt:gone", subtitle_url="https://cdn.example.com/404.vtt"),
    ]
    outcome = queue.attach(videos, {}, local=False, workdir=tmp_path / "media")
    sources = {v["id"]: v.get("transcript_source") for v in videos}
    assert sources == {"yt:y": "YouTube captions", "tt:t": "TikTok subtitles", "tt:s": None, "tt:gone": None}
    assert videos[0]["transcript"] == LONG
    assert (outcome.captions, outcome.missing) == (2, 2)


def test_only_recent_videos_from_this_run_are_looked_at(tmp_path, web):
    videos = [
        clip("old", in_window=False, set="earlier", platform="youtube", id="yt:old", caption_url="https://cdn.example.com/x.json3"),
        clip("brain", source="brain", platform="youtube", id="yt:brain", caption_url="https://cdn.example.com/x.json3"),
    ]
    queue.attach(videos, {}, local=False, workdir=tmp_path / "media")
    assert web.calls == []


# --- local speech to text -------------------------------------------------------------------------


def test_local_transcription_runs_one_at_a_time_inside_the_heavy_slot(tmp_path, fake_speech):
    fake_speech.text = LONG
    videos = [clip("a", 9.0), clip("b", 4.0)]
    workdir = tmp_path / "media"
    outcome = queue.attach(videos, {}, workdir=workdir, fetch=stub_fetch())
    assert [v["transcript"] for v in videos] == [LONG, LONG]
    assert all(v["transcript_source"] == "local transcription" for v in videos)
    # each video takes the slot on its own, and the words are not aligned (research needs the text only)
    assert fake_speech.events == [
        "slot:enter", "words:ig_a.wav:align=False", "slot:exit",
        "slot:enter", "words:ig_b.wav:align=False", "slot:exit",
    ]
    assert fake_speech.free_calls == [5.0]  # the disk is checked before the queue starts
    assert outcome.local == 2 and outcome.missing == 0 and outcome.notices == []


def test_downloads_are_cleaned_up_after_each_transcription(tmp_path, fake_speech):
    workdir = tmp_path / "media"
    seen_during = []

    def fetch(item, folder):
        path = stub_fetch()(item, folder)
        seen_during.append(sorted(p.name for p in Path(folder).iterdir()))
        return path

    queue.attach([clip("a"), clip("b")], {}, workdir=workdir, fetch=fetch)
    assert seen_during == [["ig_a.wav"], ["ig_b.wav"]]  # never more than one file on disk at a time
    assert not workdir.exists() or list(workdir.iterdir()) == []
    assert len([e for e in fake_speech.events if e.startswith("words:")]) == 2  # the transcriptions did run


def test_a_download_is_removed_even_when_the_transcription_fails(tmp_path, fake_speech, monkeypatch):
    def broken(audio, *, align=True):
        raise RuntimeError("model crashed")

    monkeypatch.setattr(sys.modules["kit.transcribe"], "words", broken)
    workdir = tmp_path / "media"
    outcome = queue.attach([clip("a")], {}, workdir=workdir, fetch=stub_fetch())
    assert not workdir.exists() or list(workdir.iterdir()) == []
    assert outcome.local == 0 and outcome.missing == 1
    assert any("model crashed" in n for n in outcome.notices)


def test_leftovers_from_a_run_that_was_stopped_are_cleared_first(tmp_path, fake_speech):
    workdir = tmp_path / "media"
    workdir.mkdir()
    (workdir / "ig_old.mp4.part").write_bytes(b"half a download")
    seen = []

    def fetch(item, folder):
        seen.append(sorted(p.name for p in Path(folder).iterdir()))
        return stub_fetch()(item, folder)

    queue.attach([clip("a")], {}, workdir=workdir, fetch=fetch)
    assert seen == [[]]  # the stale file was gone before the first download
    assert not workdir.exists()


def test_only_the_strongest_videos_are_transcribed_locally(tmp_path, fake_speech):
    videos = [clip(f"v{n}", float(n)) for n in range(15, 0, -1)]  # best first, as the ranking leaves them
    calls = []
    outcome = queue.attach(videos, {}, workdir=tmp_path / "media", fetch=stub_fetch(calls))
    assert calls == [f"ig:v{n}" for n in range(15, 3, -1)]
    assert queue.LOCAL_LIMIT == 12 and outcome.local == 12 and outcome.missing == 3


def test_a_video_that_cannot_be_downloaded_is_skipped_and_the_rest_carry_on(tmp_path, fake_speech):
    def fetch(item, workdir):
        return None if item["id"] == "ig:a" else stub_fetch()(item, workdir)

    videos = [clip("a"), clip("b")]
    outcome = queue.attach(videos, {}, workdir=tmp_path / "media", fetch=fetch)
    assert "transcript" not in videos[0] and videos[1]["transcript"]
    assert outcome.local == 1 and outcome.missing == 1


def test_three_failures_in_a_row_stop_the_queue(tmp_path, fake_speech, monkeypatch):
    def broken(audio, *, align=True):
        raise RuntimeError("no model installed")

    monkeypatch.setattr(sys.modules["kit.transcribe"], "words", broken)
    calls = []
    outcome = queue.attach([clip(f"v{n}") for n in range(8)], {}, workdir=tmp_path / "media", fetch=stub_fetch(calls))
    assert len(calls) == 3
    assert outcome.notices == ["Speech to text stopped after 3 videos in a row failed (RuntimeError: no model installed)"]


def test_local_transcription_can_be_switched_off(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "kit.transcribe", None)
    monkeypatch.setitem(sys.modules, "kit.media", None)
    videos = [clip("a")]
    outcome = queue.attach(videos, {}, local=False, workdir=tmp_path / "media", fetch=stub_fetch())
    assert outcome.notices == [] and outcome.missing == 1


def test_a_copy_of_the_kit_without_the_speech_tools_carries_on(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "kit.transcribe", None)  # an import of it now fails
    outcome = queue.attach([clip("a")], {}, workdir=tmp_path / "media", fetch=stub_fetch())
    assert outcome.local == 0 and outcome.missing == 1
    assert outcome.notices == ["Speech to text skipped: the kit's transcription tools are not available in this copy"]


def test_not_enough_free_disk_skips_speech_to_text_with_the_reason(tmp_path, fake_speech, monkeypatch):
    def tight(gb=5.0):
        raise RuntimeError("Only 2.1 GB is free and 5 GB is needed. Free some space first")

    monkeypatch.setattr(sys.modules["kit.media"], "require_free", tight)
    calls = []
    outcome = queue.attach([clip("a")], {}, workdir=tmp_path / "media", fetch=stub_fetch(calls))
    assert calls == []
    assert outcome.notices == ["Speech to text skipped: Only 2.1 GB is free and 5 GB is needed. Free some space first"]


def test_nothing_to_do_never_touches_the_speech_tools(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "kit.transcribe", None)
    outcome = queue.attach([clip("a", transcript=LONG)], {}, workdir=tmp_path / "media")
    assert outcome.notices == [] and outcome.missing == 0


# --- fetching the audio ---------------------------------------------------------------------------


def test_a_direct_video_link_is_downloaded_and_the_sound_extracted(tmp_path, monkeypatch):
    seen = {}

    def download(url, dest, **kwargs):
        seen["url"] = url
        Path(dest).write_bytes(b"video")
        return Path(dest)

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"RIFF")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(common, "http_download", download)
    monkeypatch.setattr(queue.subprocess, "run", fake_run)
    audio = queue.fetch_audio(clip("a", video_url="https://cdn.example.com/a.mp4"), tmp_path)
    assert audio.suffix == ".wav" and audio.exists()
    assert seen["url"] == "https://cdn.example.com/a.mp4"
    assert seen["cmd"][0] == "ffmpeg" and "-vn" in seen["cmd"] and "16000" in seen["cmd"]
    assert [p.name for p in tmp_path.iterdir()] == [audio.name]  # the video itself is gone


def test_without_a_direct_link_yt_dlp_fetches_the_audio(tmp_path, monkeypatch):
    seen = {}

    class FakeYDL:
        def __init__(self, options):
            seen["options"] = options

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def download(self, urls):
            seen["urls"] = urls
            Path(seen["options"]["outtmpl"].replace("%(ext)s", "wav")).write_bytes(b"RIFF")
            return 0

    module = types.ModuleType("yt_dlp")
    module.YoutubeDL = FakeYDL
    monkeypatch.setitem(sys.modules, "yt_dlp", module)
    audio = queue.fetch_audio(clip("a", platform="youtube", id="yt:a", url="https://www.youtube.com/shorts/a"), tmp_path)
    assert audio.exists() and audio.suffix == ".wav"
    assert seen["urls"] == ["https://www.youtube.com/shorts/a"]
    assert seen["options"]["format"].startswith("bestaudio")
    assert seen["options"]["quiet"] is True
    assert {"key": "FFmpegExtractAudio", "preferredcodec": "wav"} in seen["options"]["postprocessors"]


def test_a_video_that_cannot_be_fetched_gives_none(tmp_path, monkeypatch):
    def refuse(url, dest, **kwargs):
        raise common.HttpError(403, "HTTP 403")

    monkeypatch.setattr(common, "http_download", refuse)
    monkeypatch.setitem(sys.modules, "yt_dlp", None)
    assert queue.fetch_audio(clip("a", video_url="https://cdn.example.com/a.mp4"), tmp_path) is None
    assert list(tmp_path.iterdir()) == []
