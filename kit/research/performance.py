"""The creator's own recent Instagram Reels, read through Zernio and scored the same way as the watch list.

This only runs when ZERNIO_API_KEY is set. Each Reel is scored against the creator's own median
((3 x comments + likes) divided by the median of their feed Reels), and a few patterns are drawn from the
numbers, but only where both sides of a comparison have at least 4 Reels and the gap is clear. Reels
shared only as trials are counted and left out of the median, and so are Reels under two days old.

A creator without a key gets one plain sentence in the report instead.
"""

from __future__ import annotations

import importlib
import re
from datetime import timedelta

from kit.research import common, rank

PERF_DAYS = 42  # six weeks of Reels
MAX_PAGES = 20
YOUNG_HOURS = common.YOUNG_HOURS
MIN_SIDE = 4  # Reels needed on each side of a comparison
MIN_RATIO = 1.5  # how much better one side must be
SKIPPED_NO_KEY = "Performance skipped: add ZERNIO_API_KEY to .env to include your own results"


def _skipped(reason: str) -> dict:
    return {"status": "skipped", "message": f"Performance skipped: {reason}"}


def collect(profile, *, key, offline: bool = False, now=None) -> dict:
    """The creator's results as a dict with a "status". "skipped" carries one plain sentence in "message"."""
    if offline:
        return _skipped("this is an offline run")
    key = (key or "").strip()
    if not key:
        return {"status": "skipped", "message": SKIPPED_NO_KEY}
    if "instagram" not in profile.platforms:
        return _skipped("it reads Instagram, and Instagram is not in your platforms")
    try:
        zernio = importlib.import_module("kit.zernio")
    except ImportError:
        return _skipped("the kit's Zernio tools are not available in this copy")
    try:
        raw = fetch(zernio, profile, key)
    except Exception as exc:
        reason = common.redact(str(exc).strip() or type(exc).__name__, key)
        return _skipped(f"Zernio did not answer ({reason})")
    return analyse(raw, profile, now=now)


def fetch(zernio, profile, key: str) -> dict:
    """The raw Instagram analytics, page by page."""
    client = zernio.Client(key)
    handle = common.handle_of(profile.handles.instagram)
    posts: list = []
    followers = last_sync = None
    page = pages = 1
    while page <= pages and page <= MAX_PAGES:
        data = client.call("GET", "/analytics", params={"platform": "instagram", "limit": 50, "page": page})
        posts += data.get("posts") or []
        pages = int((data.get("pagination") or {}).get("pages") or 1)
        if followers is None:
            followers = next(
                (a.get("followersCount") for a in data.get("accounts") or [] if str(a.get("username", "")).lower() == handle.lower()), None
            )
        last_sync = (data.get("overview") or {}).get("lastSync") or last_sync
        page += 1
    return {"posts": posts, "followers": followers, "last_sync": last_sync, "handle": handle}


def _keyword_pattern(profile):
    if profile.cta.type == "comment" and profile.cta.keyword:
        return re.compile(r"comment\s+[\"'“‘]?" + re.escape(profile.cta.keyword) + r"\b", re.I)
    return None


def analyse(raw: dict, profile, *, now=None) -> dict:
    """Score the creator's Reels and draw the patterns the numbers back."""
    now = now or common.utcnow()
    tz = common.tz_of(profile)
    cutoff = now - timedelta(days=PERF_DAYS)
    keyword = _keyword_pattern(profile)

    feed, trials = [], 0
    for post in raw.get("posts") or []:
        published = common.parse_iso(post.get("publishedAt"))
        if not published or published < cutoff or post.get("mediaType") != "video":
            continue
        found = re.search(r"/(?:reels?|p)/([A-Za-z0-9_-]+)", post.get("platformPostUrl") or "")
        if not found:
            continue
        if post.get("isSharedToFeed") is False:
            trials += 1
            continue
        stats = post.get("analytics") or {}
        caption = post.get("content") or ""
        watch = stats.get("igReelsAvgWatchTime")
        feed.append(
            {
                "id": f"me:{found.group(1)}",
                "url": post["platformPostUrl"],
                "title": common.title_from_caption(caption),
                "posted_at": published.isoformat(),
                "views": stats.get("views"),
                "likes": stats.get("likes"),
                "comments": stats.get("comments"),
                "saves": stats.get("saves"),
                "shares": stats.get("shares"),
                "skip_rate": stats.get("reelsSkipRate"),
                "avg_watch_s": round(watch / 1000, 1) if watch else None,
                "duration_s": stats.get("videoDurationSeconds"),
                "has_keyword": bool(keyword and keyword.search(caption)),
                "young": (now - published) < timedelta(hours=YOUNG_HOURS),
            }
        )

    mature = [r for r in feed if not r["young"]]
    result = {
        "status": "ok",
        "handle": raw.get("handle"),
        "followers": raw.get("followers"),
        "last_sync": raw.get("last_sync"),
        "window_from": (now - timedelta(days=PERF_DAYS)).astimezone(tz).date().isoformat(),
        "window_to": now.astimezone(tz).date().isoformat(),
        "feed_reels": len(feed),
        "trial_reels": trials,
        "median_engagement": None,
        "median_views": None,
        "young": [r["id"] for r in feed if r["young"]],
        "top": [],
        "patterns": [],
        "message": "",
    }
    if not feed:
        result["message"] = "No Reels from the last six weeks were found."
        return result
    if not mature:
        result["message"] = "Every Reel is under two days old, so there is no median to score them against yet."
        return result

    median_eng = _median([rank.engagement(r["likes"], r["comments"]) for r in mature])
    median_views = _median([r["views"] for r in mature if r["views"] is not None])
    result.update(median_engagement=median_eng, median_views=median_views)
    for reel in feed:
        reel["multiple"] = rank.outlier(reel["comments"], reel["likes"], median_eng) if median_eng else None
        reel["views_multiple"] = (reel["views"] / median_views) if median_views and reel["views"] is not None else None
    top = sorted(mature, key=lambda r: -(r["multiple"] or 0))[:5]
    result["top"] = [{k: v for k, v in r.items() if k not in {"has_keyword", "young"}} for r in top]
    result["patterns"] = _patterns(mature, profile)
    return result


def _median(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2


def _patterns(reels: list[dict], profile) -> list[dict]:
    """Up to three comparisons the creator's own numbers back, strongest first."""
    found: list[dict] = []

    def compare(kind, a, b, text):
        if len(a) >= MIN_SIDE and len(b) >= MIN_SIDE:
            ma, mb = _median(a), _median(b)
            if ma and mb and ma / mb >= MIN_RATIO:
                found.append({"kind": kind, "ratio": round(ma / mb, 1), "text": text(ma, mb, len(a), len(b))})

    def split(test, metric):
        a = [r[metric] for r in reels if test(r) is True and r[metric] is not None]
        b = [r[metric] for r in reels if test(r) is False and r[metric] is not None]
        return a, b

    def skip(r):
        rate = r["skip_rate"]
        return None if rate is None else (True if rate < 45 else False if rate >= 60 else None)

    def length(wanted_long):
        def test(r):
            seconds = r["duration_s"]
            if not seconds or 40 < seconds < 60:
                return None
            return (seconds >= 60) == wanted_long

        return test

    n = common.short_num
    a, b = split(skip, "views")
    compare(
        "skip", a, b,
        lambda ma, mb, na, nb: f"Reels that under 45% of viewers swiped away from had a median of {n(ma)} views ({na} Reels), against {n(mb)} for Reels that 60% or more swiped away from ({nb} Reels).",
    )  # fmt: skip
    keyword = _keyword_pattern(profile)
    if keyword:
        a, b = split(lambda r: r["has_keyword"], "saves")
        word = profile.cta.keyword
        compare(
            "keyword", a, b,
            lambda ma, mb, na, nb: f"Reels that ask people to comment {word} had a median of {n(ma)} saves ({na} Reels), against {n(mb)} for Reels without a keyword ({nb} Reels).",
        )  # fmt: skip
    a, b = split(length(True), "views")
    compare(
        "length", a, b,
        lambda ma, mb, na, nb: f"Reels of a minute or more had a median of {n(ma)} views ({na} Reels), against {n(mb)} for Reels of 40 seconds or less ({nb} Reels).",
    )  # fmt: skip
    a, b = split(length(False), "views")
    compare(
        "short", a, b,
        lambda ma, mb, na, nb: f"Reels of 40 seconds or less had a median of {n(ma)} views ({na} Reels), against {n(mb)} for Reels of a minute or more ({nb} Reels).",
    )  # fmt: skip

    found.sort(key=lambda p: -p["ratio"])
    kept, kinds = [], set()
    for pattern in found:  # length and short say the same thing from opposite ends, so keep one
        family = "length" if pattern["kind"] in ("length", "short") else pattern["kind"]
        if family not in kinds:
            kinds.add(family)
            kept.append(pattern)
    return kept[:3]
