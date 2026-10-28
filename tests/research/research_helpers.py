"""Plain helpers shared by the research tests (fixtures live in conftest.py). Every creator here is invented."""

from __future__ import annotations

import html as htmllib
import json
import re
import types
from datetime import datetime, timezone
from pathlib import Path

# 13:30 UTC is already the next morning in Auckland and still early morning in Los Angeles.
NOW = datetime(2026, 10, 28, 13, 30, tzinfo=timezone.utc)


def visible(page: str) -> str:
    """The words a reader of an HTML page sees: no styles, no tags, entities turned back into characters."""
    body = re.sub(r"<style.*?</style>", " ", page, flags=re.S)
    body = re.sub(r"<[^>]+>", " ", body)
    return re.sub(r"\s+", " ", htmllib.unescape(body)).strip()


def write_profile(me: Path, **changes) -> None:
    path = me / "profile.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(changes)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def write_watchlist(me: Path, **lists) -> None:
    data = {"instagram": [], "tiktok": [], "youtube": []}
    data.update(lists)
    (me / "watchlist.json").write_text(json.dumps(data), encoding="utf-8")


def write_ideas_file(me: Path, text: str) -> None:
    (me / "ideas.md").write_text(text, encoding="utf-8")


def make_yt_dlp(channels: dict, failing: tuple = ()) -> types.ModuleType:
    """A stand-in for the yt_dlp module. `channels` maps a handle to {video id: info dict}."""
    module = types.ModuleType("yt_dlp")
    module.version = types.SimpleNamespace(__version__="2026.01.01")

    class YoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=False):
            if "/shorts" in url and "/@" in url:
                handle = url.split("/@", 1)[1].split("/", 1)[0]
                if handle in failing or handle not in channels:
                    raise RuntimeError("HTTP Error 404: Not Found")
                return {"entries": [{"id": vid} for vid in channels[handle]]}
            video = url.rsplit("/", 1)[-1]
            for videos in channels.values():
                if video in videos:
                    return videos[video]
            raise RuntimeError("Video unavailable")

    module.YoutubeDL = YoutubeDL
    return module


def short_info(title: str, *, views=1000, likes=100, comments=10, duration=30, ts=None, captions=None) -> dict:
    info = {
        "title": title,
        "description": title + " description",
        "view_count": views,
        "like_count": likes,
        "comment_count": comments,
        "duration": duration,
        "timestamp": ts if ts is not None else int(NOW.timestamp()) - 2 * 86400,
        "channel_follower_count": 5000,
        "subtitles": {},
        "automatic_captions": {},
    }
    if captions:
        info["automatic_captions"] = {"en": [{"ext": "json3", "url": captions}]}
    return info


# --- payloads shaped like the ones Apify returns -------------------------------------------------------


def when(days_ago):
    return datetime.fromtimestamp(NOW.timestamp() - days_ago * 86400, timezone.utc).isoformat()


def ig_post(code, owner="example_gardener_one", *, kind="Video", likes=500, comments=40, days_ago=2, **extra):
    post = {
        "ownerUsername": owner,
        "shortCode": code,
        "type": kind,
        "timestamp": when(days_ago),
        "url": f"https://www.instagram.com/p/{code}/",
        "caption": f"Why basil dies in week three {code}\n#herbs",
        "videoPlayCount": 12000,
        "likesCount": likes,
        "commentsCount": comments,
        "videoDuration": 34.2,
        "displayUrl": "https://cdn.example.com/cover.jpg",
        "videoUrl": f"https://cdn.example.com/{code}.mp4",
    }
    post.update(extra)
    return post


def tt_post(vid, owner="example_tiktoker", *, likes=900, comments=60, days_ago=3, **extra):
    post = {
        "authorMeta": {"name": owner, "fans": 4000},
        "id": vid,
        "webVideoUrl": f"https://www.tiktok.com/@{owner}/video/{vid}",
        "createTimeISO": when(days_ago),
        "text": f"Soil that never dries out {vid}\n#compost",
        "playCount": 30000,
        "diggCount": likes,
        "commentCount": comments,
        "shareCount": 25,
        "videoMeta": {
            "duration": 28,
            "coverUrl": "https://cdn.example.com/c.jpg",
            "subtitleLinks": [
                {"language": "fra-FR", "downloadLink": "https://cdn.example.com/fr.vtt"},
                {"language": "eng-US", "downloadLink": "https://cdn.example.com/en.vtt"},
            ],
        },
    }
    post.update(extra)
    return post


def started(run_id, dataset, status, cost):
    return {"data": {"id": run_id, "defaultDatasetId": dataset, "status": status, "usageTotalUsd": cost}}


def apify(web, *, ig=None, tt=None, left=4.0, ig_run=None, tt_run=None):
    """Routes for the Apify calls the scraper makes. `ig_run` and `tt_run` replace the answer to starting a run."""
    web.add("GET", "/users/me/limits", {"data": {"limits": {"maxMonthlyUsageUsd": left}, "current": {"monthlyUsageUsd": 0}}})
    web.add("POST", "/acts/apify~instagram-scraper/runs", ig_run or started("run-ig", "ds-ig", "SUCCEEDED", 0.12))
    web.add("POST", "/acts/clockworks~tiktok-scraper/runs", tt_run or started("run-tt", "ds-tt", "SUCCEEDED", 0.31))
    web.add("GET", "/datasets/ds-ig/items", ig if ig is not None else [])
    web.add("GET", "/datasets/ds-tt/items", tt if tt is not None else [])
