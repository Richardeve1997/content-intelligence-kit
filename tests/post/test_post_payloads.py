"""kit.post.payloads: what each platform's post looks like when it goes to Zernio."""

from __future__ import annotations

import pytest

from kit.config import PLATFORMS
from kit.post import payloads
from kit.post.errors import PostError

CAPTION = {
    "title": None,
    "body": "Water the base, never the leaves.\n\nFollow @examplestudio for more.",
    "hashtags": ["#balconygarden", "#basil"],
}
TIKTOK_SETTINGS = {
    "privacy_level": "PUBLIC_TO_EVERYONE",
    "allow_comment": True,
    "allow_duet": True,
    "allow_stitch": True,
    "content_preview_confirmed": True,
    "express_consent_given": True,
}


def body_for(platform: str, caption: dict | None = None, seconds: float | None = 30.0) -> dict:
    return payloads.build_body(
        platform,
        title=f"Example Studio {platform} example-reel",
        slug="example-reel",
        caption=caption or CAPTION,
        account_id="acct-1",
        media_url="https://media.example.test/1/final.mp4",
        scheduled_for="2026-10-29T18:00:00+00:00",
        timezone="Europe/London",
        seconds=seconds,
    )


def entry(body: dict) -> dict:
    (only,) = body["platforms"]
    return only


# --- the fields each platform needs ------------------------------------------------------------------


def test_payload_fields():
    instagram = entry(body_for("instagram"))
    assert instagram == {
        "platform": "instagram",
        "accountId": "acct-1",
        "platformSpecificData": {"shareToFeed": True},
    }

    long_title = {"title": "Why windowsill basil dies in week three " * 4, "body": "Body.", "hashtags": []}
    youtube = entry(body_for("youtube", long_title))["platformSpecificData"]
    assert 0 < len(youtube["title"]) <= 100
    assert youtube["title"] == long_title["title"][:100].rstrip()
    assert youtube["visibility"] == "public"

    assert entry(body_for("facebook", seconds=60.0))["platformSpecificData"] == {"contentType": "reel"}
    assert entry(body_for("facebook", seconds=12.5))["platformSpecificData"] == {"contentType": "reel"}

    tiktok = body_for("tiktok")
    assert tiktok["tiktokSettings"] == TIKTOK_SETTINGS

    assert entry(body_for("twitter"))["platform"] == "twitter"
    assert entry(body_for("linkedin")) == {"platform": "linkedin", "accountId": "acct-1"}


def test_a_facebook_video_over_sixty_seconds_goes_up_as_a_feed_video():
    assert "platformSpecificData" not in entry(body_for("facebook", seconds=60.01))
    assert "platformSpecificData" not in entry(body_for("facebook", seconds=240))


def test_facebook_needs_to_know_how_long_the_video_is():
    with pytest.raises(PostError, match="length"):
        body_for("facebook", seconds=None)


def test_only_tiktok_carries_the_consent_settings():
    for platform in PLATFORMS:
        assert ("tiktokSettings" in body_for(platform)) is (platform == "tiktok")


def test_the_tiktok_settings_are_a_copy():
    first = body_for("tiktok")
    first["tiktokSettings"]["privacy_level"] = "SELF_ONLY"
    assert body_for("tiktok")["tiktokSettings"]["privacy_level"] == "PUBLIC_TO_EVERYONE"
    assert payloads.TIKTOK_SETTINGS["privacy_level"] == "PUBLIC_TO_EVERYONE"


def test_the_zernio_names_match_the_kits_and_x_is_twitter():
    assert set(payloads.ZERNIO_PLATFORM) == set(PLATFORMS)
    assert payloads.ZERNIO_PLATFORM["twitter"] == "twitter"


def test_a_short_youtube_title_is_left_alone():
    caption = {"title": "  Basil in week three  ", "body": "Body.", "hashtags": []}
    assert entry(body_for("youtube", caption))["platformSpecificData"]["title"] == "Basil in week three"


# --- the rest of the request ---------------------------------------------------------------------------


def test_the_request_carries_the_title_media_time_and_the_slug():
    body = body_for("instagram")
    assert body["title"] == "Example Studio instagram example-reel"
    assert body["mediaItems"] == [{"type": "video", "url": "https://media.example.test/1/final.mp4"}]
    assert body["scheduledFor"] == "2026-10-29T18:00:00+00:00"
    assert body["timezone"] == "Europe/London"
    assert body["isDraft"] is False
    assert body["metadata"] == {"slug": "example-reel", "platform": "instagram"}
    assert "publishNow" not in body


def test_the_text_is_the_body_then_a_blank_line_then_the_tags():
    assert body_for("instagram")["content"] == (
        "Water the base, never the leaves.\n\nFollow @examplestudio for more.\n\n#balconygarden #basil"
    )


def test_no_tags_means_no_tag_line():
    caption = {"title": None, "body": "  Just words.  ", "hashtags": []}
    assert payloads.content_for(caption, "instagram") == "Just words."


def test_linkedin_never_gets_a_tag_line_even_if_the_caption_has_tags():
    assert payloads.content_for(CAPTION, "linkedin") == CAPTION["body"]


def test_youtube_text_is_the_description_and_the_title_is_not_in_it():
    caption = {"title": "A title", "body": "Description.", "hashtags": ["#one"]}
    assert body_for("youtube", caption)["content"] == "Description.\n\n#one"


# --- reading the video's length ---------------------------------------------------------------------------


def fake_ffprobe(tmp_path, monkeypatch, script: str):
    folder = tmp_path / "tools"
    folder.mkdir()
    probe = folder / "ffprobe"
    probe.write_text("#!/bin/sh\n" + script)
    probe.chmod(0o755)
    monkeypatch.setenv("PATH", str(folder))
    video = tmp_path / "final.mp4"
    video.write_bytes(b"bytes")
    return video


def test_video_seconds_reads_the_length_with_ffprobe(tmp_path, monkeypatch):
    video = fake_ffprobe(tmp_path, monkeypatch, 'echo "$@" > "$0.args"\necho 12.480000\n')
    assert payloads.video_seconds(video) == pytest.approx(12.48)
    asked = (tmp_path / "tools" / "ffprobe.args").read_text()
    assert str(video) in asked and "format=duration" in asked


@pytest.mark.parametrize("script", ["echo N/A\n", "exit 1\n", "echo\n", "echo -3\n"])
def test_video_seconds_says_so_when_the_length_cannot_be_read(tmp_path, monkeypatch, script):
    video = fake_ffprobe(tmp_path, monkeypatch, script)
    with pytest.raises(PostError, match="length"):
        payloads.video_seconds(video)


def test_video_seconds_without_ffprobe_points_at_ffmpeg(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    video = tmp_path / "final.mp4"
    video.write_bytes(b"bytes")
    with pytest.raises(PostError, match="ffmpeg"):
        payloads.video_seconds(video)
