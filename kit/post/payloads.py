"""What each platform's post looks like when it goes to Zernio.

One post goes to one platform, so each keeps its own caption, time and cancel. The fields that differ
by platform:

* Instagram posts to the feed as well as the Reels tab (shareToFeed).
* YouTube takes its own title, at most 100 characters, and a public video.
* Facebook gets a Reel when the video is 60 seconds or shorter, and a feed video when it is longer.
* TikTok carries the consent settings it asks for before a direct post.
* X is "twitter" in Zernio as well as in the kit.
* LinkedIn takes the text alone, and never a tag line.
"""

from __future__ import annotations

import math
import subprocess
from pathlib import Path
from typing import Mapping

from kit import config
from kit.post.errors import PostError

__all__ = [
    "ZERNIO_PLATFORM",
    "FACEBOOK_REEL_MAX_SECONDS",
    "YOUTUBE_TITLE_MAX",
    "TIKTOK_SETTINGS",
    "content_for",
    "youtube_title",
    "platform_entry",
    "build_body",
    "video_seconds",
]

# Zernio names its platforms the way the kit does. X is "twitter" on both sides.
ZERNIO_PLATFORM = {name: name for name in config.PLATFORMS}
FACEBOOK_REEL_MAX_SECONDS = 60  # a Facebook Reel runs 3 to 60 seconds, and a longer video goes up as a feed video
YOUTUBE_TITLE_MAX = 100

# TikTok asks for the creator's consent before a direct post. schedule() sends nothing until the creator
# has approved every caption, and these flags say that their approval of the TikTok caption is the
# preview and the consent.
TIKTOK_SETTINGS = {
    "privacy_level": "PUBLIC_TO_EVERYONE",
    "allow_comment": True,
    "allow_duet": True,
    "allow_stitch": True,
    "content_preview_confirmed": True,
    "express_consent_given": True,
}


def content_for(caption: Mapping, platform: str) -> str:
    """The text that is posted: the body, then a blank line and the tags. LinkedIn never gets the tags."""
    body = caption.get("body")
    body = body.strip() if isinstance(body, str) else ""
    tags = caption.get("hashtags")
    tags = [tag for tag in tags if isinstance(tag, str)] if isinstance(tags, list) and platform != "linkedin" else []
    return f"{body}\n\n{' '.join(tags)}" if tags else body


def youtube_title(caption: Mapping) -> str:
    """The YouTube title, cut to the 100 characters YouTube allows."""
    title = caption.get("title")
    title = " ".join(title.split()) if isinstance(title, str) else ""
    return title[:YOUTUBE_TITLE_MAX].rstrip()


def platform_entry(platform: str, account_id: str, caption: Mapping, seconds: float | None = None) -> dict:
    """One item of a post's platforms list. `seconds` is the video's length, which only Facebook needs."""
    data: dict = {}
    if platform == "instagram":
        data["shareToFeed"] = True
    elif platform == "youtube":
        data = {"title": youtube_title(caption), "visibility": "public"}
    elif platform == "facebook":
        if seconds is None:
            raise PostError("Facebook needs the length of the video to choose between a Reel and a feed video.")
        if seconds <= FACEBOOK_REEL_MAX_SECONDS:
            data["contentType"] = "reel"
    entry = {"platform": ZERNIO_PLATFORM[platform], "accountId": account_id}
    if data:
        entry["platformSpecificData"] = data
    return entry


def build_body(
    platform: str,
    *,
    title: str,
    slug: str,
    caption: Mapping,
    account_id: str,
    media_url: str,
    scheduled_for: str,
    timezone: str,
    seconds: float | None = None,
) -> dict:
    """The whole request that schedules one video on one platform."""
    body = {
        "title": title,
        "content": content_for(caption, platform),
        "mediaItems": [{"type": "video", "url": media_url}],
        "platforms": [platform_entry(platform, account_id, caption, seconds)],
        "scheduledFor": scheduled_for,
        "timezone": timezone,
        "isDraft": False,
        "metadata": {"slug": slug, "platform": platform},
    }
    if platform == "tiktok":
        body["tiktokSettings"] = dict(TIKTOK_SETTINGS)
    return body


def video_seconds(video: Path) -> float:
    """The length of a video in seconds, read with ffprobe (which comes with ffmpeg)."""
    command = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(video),
    ]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        raise PostError(
            "ffprobe is needed to read the length of the video, and it comes with ffmpeg. Install ffmpeg and try again."
        ) from None
    except subprocess.TimeoutExpired:
        raise PostError(f"Reading the length of {Path(video).name} took too long. Check that it plays and try again.") from None
    try:
        seconds = float(done.stdout.strip().splitlines()[0]) if done.returncode == 0 and done.stdout.strip() else math.nan
    except ValueError:
        seconds = math.nan
    if not math.isfinite(seconds) or seconds <= 0:
        raise PostError(f"The length of {Path(video).name} could not be read. Check that it plays and try again.")
    return seconds
