"""Scores every video against its own creator's normal.

A video's outlier score is (3 x comments + likes) divided by that creator's median. A score of 1 is an
ordinary video for that creator, and 5 is five times as engaging as usual. A comment counts three times
as much as a like because it takes more effort.

The median is taken over every video of that creator on record: the ones pulled in this run plus the
ones earlier runs kept in research/brain/. A creator with fewer than 3 videos on record is marked low
sample, because a median of two videos says little.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import timedelta

from kit.research import common

LOW_SAMPLE_BELOW = 3  # fewer videos on record than this and the creator is a low sample
EARLIER_MIN_OUTLIER = 2.0  # an older video is worth listing when it ran at least this many times normal
EARLIER_LIMIT = 8  # and only the best few of those are listed


@dataclass
class Scored:
    videos: list[dict]  # best score first, each with its rank
    creators: list[dict]  # one summary per creator, in the order they first appear


def _count(value) -> float:
    """A like or comment count. A missing, hidden (-1) or unreadable count is zero."""
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def engagement(likes, comments) -> float:
    """3 x comments + likes."""
    return 3.0 * _count(comments) + _count(likes)


def outlier(comments, likes, creator_median) -> float:
    """(3 x comments + likes) / the creator's median."""
    if creator_median is None or not creator_median > 0:
        raise ValueError("A creator's median must be above zero to score a video against it.")
    return engagement(likes, comments) / creator_median


def is_low_sample(count: int) -> bool:
    return count < LOW_SAMPLE_BELOW


def creator_key(platform: str, creator: str) -> tuple[str, str]:
    """Creators are told apart by platform and by name without regard to case."""
    return platform, str(creator).lower()


def _posted(video: dict) -> float:
    moment = common.parse_iso(video.get("posted_at"))
    return moment.timestamp() if moment else 0.0


def _scored(base: dict, key, *, median, count, low, now, source: str) -> dict:
    video = dict(base)
    video.setdefault("platform", key[0])
    video.setdefault("creator", key[1])
    video.setdefault("url", "")
    video.setdefault("title", "")
    own = engagement(video.get("likes"), video.get("comments"))
    posted = common.parse_iso(video.get("posted_at"))
    recent = bool(posted and posted >= now - timedelta(days=common.WINDOW_DAYS))
    video.update(
        engagement=own,
        creator_median=median,
        creator_n=count,
        low_sample=low,
        outlier=outlier(video.get("comments"), video.get("likes"), median) if median > 0 else None,
        in_window=recent,
        young=bool(posted and posted > now - timedelta(hours=common.YOUNG_HOURS)),
        set="recent" if recent else "earlier",
        source=source,
    )
    return video


def score(items: list[dict], history: dict | None = None, *, now=None) -> Scored:
    """Score the videos pulled in this run against everything on record for their creators.

    `history` maps (platform, creator in lower case) to {video id: record} for earlier runs. Neither
    argument is changed. Older videos from earlier runs that ran well above normal are added to the
    list, up to EARLIER_LIMIT of them.
    """
    now = now or common.utcnow()
    history = history or {}

    groups: dict[tuple[str, str], list[dict]] = {}
    for item in items:
        groups.setdefault(creator_key(item["platform"], item["creator"]), []).append(item)

    videos: list[dict] = []
    creators: list[dict] = []
    standouts: list[dict] = []
    for key, group in groups.items():
        earlier = [r for r in history.get(key, {}).values() if isinstance(r, dict) and r.get("id")]
        on_record: dict[str, dict] = {r["id"]: r for r in earlier}
        on_record.update({item["id"]: item for item in group})  # this run's numbers win over older ones
        median = statistics.median(engagement(r.get("likes"), r.get("comments")) for r in on_record.values())
        count = len(on_record)
        low = is_low_sample(count)

        mine = [_scored(item, key, median=median, count=count, low=low, now=now, source="run") for item in group]
        videos.extend(mine)
        if median > 0:
            this_run = {item["id"] for item in group}
            for record in earlier:
                if record["id"] in this_run:
                    continue
                video = _scored(record, key, median=median, count=count, low=low, now=now, source="brain")
                if video["outlier"] is not None and video["outlier"] >= EARLIER_MIN_OUTLIER:
                    standouts.append(video)

        best = [v["outlier"] for v in mine if v["outlier"] is not None]
        creators.append(
            {
                "platform": key[0],
                "creator": group[0]["creator"],
                "pulled": len(group),
                "on_record": count,
                "in_window": sum(1 for v in mine if v["in_window"]),
                "median_engagement": median,
                "low_sample": low,
                "best_outlier": max(best) if best else None,
            }
        )

    standouts.sort(key=lambda v: (-v["outlier"], -_posted(v)))
    videos.extend(standouts[:EARLIER_LIMIT])
    videos.sort(key=lambda v: (v["outlier"] is None, -(v["outlier"] or 0.0), -_posted(v)))
    for position, video in enumerate(videos, 1):
        video["rank"] = position
    return Scored(videos=videos, creators=creators)


def attach_hooks(videos: list[dict]) -> None:
    """Give each video its hook: the first spoken sentence when there is a transcript, else the caption."""
    for video in videos:
        if common.is_usable(video.get("transcript")):
            video["hook"] = common.first_line(video["transcript"])
            video["hook_source"] = "transcript"
        else:
            video["hook"] = video.get("title") or ""
            video["hook_source"] = "caption"
