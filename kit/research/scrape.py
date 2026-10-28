"""Pulls the latest videos for the creators on the watch list.

Instagram and TikTok go through Apify, but only when the creator has an APIFY_TOKEN, and each run is
held to the spending cap in the profile (research.cost_cap, 25 cents for Instagram and 50 cents for
TikTok unless the creator changed them). YouTube goes through yt-dlp, which is free. All three run at
the same time. A platform that cannot run is skipped with one plain sentence, and the others carry on.
"""

from __future__ import annotations

import concurrent.futures as futures
import importlib
import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlencode

from kit import config
from kit.research import common

APIFY_MISSING = "Instagram and TikTok skipped: add APIFY_TOKEN to .env to include them"
APIFY_OFF = "Instagram and TikTok skipped: this run was started with --no-apify"
API = "https://api.apify.com/v2"
BUDGET_SECONDS = 120.0  # how long a run waits for all three platforms
MIN_WAIT = 5.0  # the least a step waits on yt-dlp, even when the budget is nearly spent
YOUTUBE_FIX = "uv sync --group research --inexact"

# The two Apify actors. `min_usd` is the least a pull needs: Apify refuses a run it cannot fund.
ACTORS = {
    "instagram": {"actor": "apify~instagram-scraper", "min_usd": 0.05, "per_creator": 8},
    "tiktok": {"actor": "clockworks~tiktok-scraper", "min_usd": 0.50, "per_creator": 10},
}
SHORTS_PER_CREATOR = 10


@dataclass
class Pull:
    """What a pull brought back: videos, how each source went, and sentences to show the creator."""

    items: list[dict] = field(default_factory=list)
    sources: dict[str, dict] = field(default_factory=dict)
    notices: list[str] = field(default_factory=list)


class ApifyLimit(Exception):
    """The Apify account has less left this month than a pull needs."""

    def __init__(self, left: float, needed: float):
        super().__init__(f"{left:.2f} left, {needed:.2f} needed")
        self.left, self.needed = left, needed


class ApifyRunError(Exception):
    """An Apify run ended badly. The message is a plain clause such as "the Apify run failed"."""


# --- the watch list -------------------------------------------------------------------------------------


def load_watchlist() -> dict[str, list[str]]:
    """me/watchlist.json as {platform: [handles]}. A missing file is an empty list."""
    empty = {platform: [] for platform in common.RESEARCH_PLATFORMS}
    path = config.me_dir() / "watchlist.json"
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return empty
    except OSError as exc:
        raise config.KitError(f"me/watchlist.json could not be read ({exc.strerror or type(exc).__name__}).") from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise config.KitError(
            f"me/watchlist.json is not valid JSON ({exc.msg}, on line {exc.lineno}). Check for a missing comma, quote or bracket."
        ) from None
    if not isinstance(data, dict):
        raise config.KitError(
            "me/watchlist.json must hold one list of handles for each of instagram, tiktok and youtube, between { and }."
        )
    for platform in common.RESEARCH_PLATFORMS:
        if not isinstance(data.get(platform, []), list):
            raise config.KitError(f"me/watchlist.json: {platform} should be a list of handles.")
    return _tidy(data)


def _tidy(lists: dict) -> dict[str, list[str]]:
    """Handles without the @ or the link around them, each once, in the order given."""
    tidy: dict[str, list[str]] = {}
    for platform in common.RESEARCH_PLATFORMS:
        seen, handles = set(), []
        for raw in lists.get(platform) or []:
            handle = common.handle_of(raw)
            if handle and handle.lower() not in seen:
                seen.add(handle.lower())
                handles.append(handle)
        tidy[platform] = handles
    return tidy


# --- Apify ------------------------------------------------------------------------------------------------


class _Account:
    """The Apify account's balance, asked for once and shared by the platforms that run together."""

    def __init__(self, token: str):
        self.token = token
        self._lock = threading.Lock()
        self._known = False
        self._left: float | None = None

    @property
    def auth(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    def left(self) -> float | None:
        """US dollars left under this month's limit, or None when it cannot be told."""
        with self._lock:
            if not self._known:
                try:
                    data = common.http_json(f"{API}/users/me/limits", headers=self.auth, timeout=15)["data"]
                    self._left = float(data["limits"]["maxMonthlyUsageUsd"]) - float(data["current"]["monthlyUsageUsd"])
                except Exception:
                    self._left = None
                self._known = True
            return self._left


def _apify_run(account: _Account, spec: dict, run_input: dict, cap: float, deadline: float) -> tuple[list, dict]:
    """Start an actor, wait for it inside the time limit, and return its items and what it cost."""
    minimum = spec["min_usd"]
    left = account.left()
    if left is not None:
        if left < minimum:
            raise ApifyLimit(max(left, 0.0), minimum)
        cap = min(cap, round(left - 0.01, 2))
    charge = max(cap, minimum)
    query = urlencode({"maxTotalChargeUsd": charge})
    run = common.http_json(f"{API}/acts/{spec['actor']}/runs?{query}", "POST", run_input, account.auth, timeout=30)["data"]
    run_id, dataset = run["id"], run["defaultDatasetId"]
    status = run.get("status")
    while status not in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
        remaining = deadline - time.monotonic()
        if remaining <= 2:
            try:
                common.http_json(f"{API}/actor-runs/{run_id}/abort", "POST", {}, account.auth, timeout=10)
            except Exception:
                pass
            raise TimeoutError("the Apify run was still going at the time limit, so it was stopped")
        wait = int(min(20, remaining - 1))
        run = common.http_json(f"{API}/actor-runs/{run_id}?waitForFinish={wait}", headers=account.auth, timeout=min(30, remaining + 5))["data"]
        status = run.get("status")
    if status != "SUCCEEDED":
        words = {"FAILED": "failed", "ABORTED": "was stopped", "TIMED-OUT": "timed out"}
        raise ApifyRunError(f"the Apify run {words[status]}")
    items = common.http_json(f"{API}/datasets/{dataset}/items?clean=true&format=json", headers=account.auth, timeout=30)
    return items, {"cost_usd": run.get("usageTotalUsd")}


def _count(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _stamp(value) -> str | None:
    moment = common.parse_iso(value) if isinstance(value, str) else None
    return moment.isoformat() if moment else None


def _instagram_items(raw: list, creators: list[str]) -> tuple[list[dict], list[dict]]:
    per = {name.lower(): {"creator": name.lower(), "videos": 0} for name in creators}
    items = []
    for post in raw if isinstance(raw, list) else []:
        owner = str(post.get("ownerUsername") or "").lower()
        code = post.get("shortCode")
        if owner not in per or not code or str(post.get("type") or "").lower() != "video":
            continue
        per[owner]["videos"] += 1
        caption = (post.get("caption") or "").strip()
        items.append(
            {
                "id": f"ig:{code}",
                "platform": "instagram",
                "creator": owner,
                "url": post.get("url") or f"https://www.instagram.com/p/{code}/",
                "posted_at": _stamp(post.get("timestamp")),
                "title": common.title_from_caption(caption),
                "caption": caption[:300],
                "views": _count(post.get("videoPlayCount") or post.get("videoViewCount")),
                "likes": _count(post.get("likesCount")),
                "comments": _count(post.get("commentsCount")),
                "duration": post.get("videoDuration"),
                "video_url": post.get("videoUrl"),
            }
        )
    return items, _creator_rows(per)


def _tiktok_items(raw: list, creators: list[str]) -> tuple[list[dict], list[dict]]:
    names = {name.lower(): name.lower() for name in creators}
    per = {name: {"creator": name, "videos": 0} for name in names}
    items = []
    for post in raw if isinstance(raw, list) else []:
        owner = names.get(str((post.get("authorMeta") or {}).get("name") or "").lower())
        if not owner or not post.get("id"):
            continue
        per[owner]["videos"] += 1
        meta = post.get("videoMeta") or {}
        english = next((s for s in meta.get("subtitleLinks") or [] if str(s.get("language", "")).lower().startswith("en")), None) or {}
        text = (post.get("text") or "").strip()
        items.append(
            {
                "id": f"tt:{post['id']}",
                "platform": "tiktok",
                "creator": owner,
                "url": post.get("webVideoUrl") or f"https://www.tiktok.com/@{owner}/video/{post['id']}",
                "posted_at": _stamp(post.get("createTimeISO")),
                "title": text.split("\n")[0][:160],
                "caption": text[:300],
                "views": _count(post.get("playCount")),
                "likes": _count(post.get("diggCount")),
                "comments": _count(post.get("commentCount")),
                "duration": meta.get("duration"),
                "subtitle_url": english.get("downloadLink") or english.get("tiktokLink"),
            }
        )
    return items, _creator_rows(per)


def _creator_rows(per: dict) -> list[dict]:
    rows = list(per.values())
    for row in rows:
        if row["videos"] == 0:
            row["note"] = "no videos returned"
    return rows


def _apify_input(platform: str, creators: list[str]) -> dict:
    per_creator = ACTORS[platform]["per_creator"]
    if platform == "instagram":
        return {
            "directUrls": [f"https://www.instagram.com/{name}/" for name in creators],
            "resultsType": "posts",
            "resultsLimit": per_creator,
            "addParentData": False,
        }
    return {
        "profiles": creators,
        "resultsPerPage": per_creator,
        "profileScrapeSections": ["videos"],
        "profileSorting": "latest",
        "excludePinnedPosts": True,
        "downloadSubtitlesOptions": "DOWNLOAD_SUBTITLES",
        "shouldDownloadVideos": False,
        "shouldDownloadCovers": False,
        "shouldDownloadAvatars": False,
        "shouldDownloadSlideshowImages": False,
        "proxyCountryCode": "None",
    }


def _article(label: str) -> str:
    return "an" if label[0] in "AEIOU" else "a"


def _apify_platform(platform: str, creators: list[str], account: _Account, cap: float, deadline: float) -> tuple[list, dict, str | None]:
    """One platform through Apify. Returns (items, source facts, a sentence for the creator or None)."""
    label = common.PLATFORM_LABEL[platform]
    spec = ACTORS[platform]
    source = {"status": "skipped", "tool": f"Apify ({spec['actor'].replace('~', '/')})", "creators": [], "videos": 0}
    if cap < spec["min_usd"]:
        return [], source, (
            f"{label} skipped: your cost cap of US${cap:.2f} is below the US${spec['min_usd']:.2f} one {label} pull needs. "
            f"Raise research.cost_cap.{platform} in me/profile.json"
        )
    try:
        raw, meta = _apify_run(account, spec, _apify_input(platform, creators), cap, deadline)
    except ApifyLimit as exc:
        return [], source, (
            f"{label} skipped: your Apify account has US${exc.left:.2f} left this month, "
            f"and {_article(label)} {label} pull needs US${exc.needed:.2f}"
        )
    except common.HttpError as exc:
        source["status"] = "failed"
        if exc.status == 402:
            return [], source, f"{label} skipped: Apify refused the run (HTTP 402). Check your Apify plan and usage"
        if exc.status is None:
            return [], source, f"{label} skipped: Apify could not be reached ({exc})"
        return [], source, f"{label} skipped: Apify answered with {exc}"
    except (TimeoutError, ApifyRunError) as exc:
        source["status"] = "failed"
        return [], source, f"{label} skipped: {exc}"
    except Exception as exc:
        source["status"] = "failed"
        return [], source, f"{label} skipped: {common.why(exc)}"

    items, rows = (_instagram_items if platform == "instagram" else _tiktok_items)(raw, creators)
    source.update(creators=rows, videos=len(items), cost_usd=meta.get("cost_usd"))
    if not items:
        source["status"] = "failed"
        return [], source, f"{label} returned no videos this time"
    source["status"] = "ok"
    return items, source, None


# --- YouTube ----------------------------------------------------------------------------------------------


class _Quiet:
    """Keeps yt-dlp from printing its own progress."""

    def debug(self, *args): ...
    def info(self, *args): ...
    def warning(self, *args): ...
    def error(self, *args): ...


# This client answers with view counts and caption links in a few seconds and needs no JavaScript player.
_FAST_CLIENT = {"youtube": {"player_client": ["android_vr"], "player_skip": ["js"]}}


def _caption_link(info: dict) -> str | None:
    for tracks, languages in ((info.get("subtitles") or {}, ("en", "en-US", "en-GB")), (info.get("automatic_captions") or {}, ("en-orig", "en"))):
        for language in languages:
            for track in tracks.get(language) or []:
                if track.get("ext") == "json3" and track.get("url"):
                    return track["url"]
    return None


def _youtube(creators: list[str], deadline: float) -> tuple[list, dict, str | None]:
    source = {"status": "skipped", "tool": "", "creators": [], "videos": 0}
    try:
        yt_dlp = importlib.import_module("yt_dlp")
    except ImportError:
        return [], source, f"YouTube skipped: yt-dlp is not installed. Run: {YOUTUBE_FIX}"
    version = getattr(getattr(yt_dlp, "version", None), "__version__", "")
    source["tool"] = f"yt-dlp {version}".strip()
    base = {"quiet": True, "no_warnings": True, "skip_download": True, "socket_timeout": 15, "logger": _Quiet(), "noprogress": True}

    def listing(handle: str) -> list[str]:
        with yt_dlp.YoutubeDL({**base, "extract_flat": "in_playlist", "playlistend": SHORTS_PER_CREATOR}) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/@{handle}/shorts", download=False)
        return [e["id"] for e in (info.get("entries") or []) if e.get("id")][:SHORTS_PER_CREATOR]

    def details(video_id: str) -> dict:
        url = f"https://www.youtube.com/shorts/{video_id}"
        try:
            with yt_dlp.YoutubeDL({**base, "ignore_no_formats_error": True, "extractor_args": _FAST_CLIENT}) as ydl:
                return ydl.extract_info(url, download=False)
        except Exception:  # the quick client can fail where the default one works
            with yt_dlp.YoutubeDL({**base, "ignore_no_formats_error": True}) as ydl:
                return ydl.extract_info(url, download=False)

    rows = {name: {"creator": name, "videos": 0} for name in creators}
    ids: list[tuple[str, str]] = []
    listing_pool = futures.ThreadPoolExecutor(max_workers=max(1, len(creators)))
    try:
        jobs = {listing_pool.submit(listing, name): name for name in creators}
        try:
            for job in futures.as_completed(jobs, timeout=max(MIN_WAIT, deadline - time.monotonic())):
                name = jobs[job]
                try:
                    ids += [(name, video_id) for video_id in job.result()]
                except Exception as exc:
                    rows[name]["note"] = common.why(exc)
        except futures.TimeoutError:
            pass  # a creator that did not answer in time has no note, and is reported as returning nothing
    finally:
        listing_pool.shutdown(wait=False, cancel_futures=True)  # never wait on a page that will not load
    for name in creators:
        if "note" not in rows[name] and not any(owner == name for owner, _ in ids):
            rows[name]["note"] = "no Shorts returned"

    items: list[dict] = []
    skipped = 0
    pool = futures.ThreadPoolExecutor(max_workers=12)
    try:
        jobs = {pool.submit(details, video_id): (name, video_id) for name, video_id in ids}
        try:
            for job in futures.as_completed(jobs, timeout=max(MIN_WAIT, deadline - time.monotonic())):
                name, video_id = jobs[job]
                try:
                    info = job.result()
                except Exception:
                    skipped += 1
                    continue
                stamp = info.get("timestamp")
                posted = datetime.fromtimestamp(stamp, timezone.utc).isoformat() if stamp else None
                rows[name]["videos"] += 1
                items.append(
                    {
                        "id": f"yt:{video_id}",
                        "platform": "youtube",
                        "creator": name,
                        "url": f"https://www.youtube.com/shorts/{video_id}",
                        "posted_at": posted,
                        "title": (info.get("title") or "")[:160],
                        "caption": (info.get("description") or "")[:300],
                        "views": _count(info.get("view_count")),
                        "likes": _count(info.get("like_count")),
                        "comments": _count(info.get("comment_count")),
                        "duration": info.get("duration"),
                        "caption_url": _caption_link(info),
                    }
                )
        except futures.TimeoutError:
            pass  # keep what finished inside the time limit
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    source["creators"] = list(rows.values())
    source["videos"] = len(items)
    if skipped or len(items) < len(ids):
        source["note"] = f"{len(items)} of {len(ids)} Shorts checked"
    if not items:
        source["status"] = "failed"
        first = next((row["note"] for row in rows.values() if row.get("note") and row["note"] != "no Shorts returned"), "")
        return [], source, "YouTube returned no videos this time" + (f" ({first})" if first else "")
    source["status"] = "ok"
    return items, source, None


# --- the pull ---------------------------------------------------------------------------------------------


def pull(profile, watchlist: dict, *, token, use_apify: bool = True, budget: float = BUDGET_SECONDS) -> Pull:
    """Pull the watch list. Platforms run together; one that cannot run is skipped with a sentence and the rest carry on."""
    lists = _tidy(watchlist)
    token = (token or "").strip() or None
    deadline = time.monotonic() + budget
    result = Pull(sources={p: {"status": "skipped", "tool": "", "creators": [], "videos": 0} for p in common.RESEARCH_PLATFORMS})

    jobs: dict[str, object] = {}
    if token is None:
        result.notices.append(APIFY_MISSING)
    elif not use_apify:
        result.notices.append(APIFY_OFF)
    else:
        account = _Account(token)
        for platform in ("instagram", "tiktok"):
            if lists[platform]:
                cap = float(profile.research.cost_cap[platform])
                jobs[platform] = lambda p=platform, c=lists[platform], k=cap: _apify_platform(p, c, account, k, deadline)
    if lists["youtube"]:
        jobs["youtube"] = lambda: _youtube(lists["youtube"], deadline)

    outcomes: dict[str, tuple] = {}
    if jobs:
        with futures.ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            running = {name: pool.submit(job) for name, job in jobs.items()}
            for name, job in running.items():
                try:
                    outcomes[name] = job.result()
                except Exception as exc:  # a platform that blows up must not take the others with it
                    label = common.PLATFORM_LABEL[name]
                    outcomes[name] = ([], {"status": "failed", "tool": "", "creators": [], "videos": 0}, f"{label} skipped: {common.why(exc)}")

    for platform in common.RESEARCH_PLATFORMS:
        if platform not in outcomes:
            continue
        items, source, notice = outcomes[platform]
        result.items.extend(items)
        result.sources[platform] = source
        if notice:
            result.notices.append(notice)

    if token:
        result.notices = [common.redact(n, token) for n in result.notices]
        for source in result.sources.values():
            for row in source.get("creators", []):
                if row.get("note"):
                    row["note"] = common.redact(row["note"], token)
    return result
