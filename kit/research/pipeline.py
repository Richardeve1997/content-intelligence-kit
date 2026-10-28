"""One research run, start to finish.

A run gathers what the watch list posted (or the offline sample), the creator's ideas file and the X
posts it links to, and their own results when there is a Zernio key. It scores every video, gets
transcripts for the strongest, adds what it saw to the brain, and writes research/runs/<date>/items.json
and report.html. The ideas come after: the skill reads items.json and writes ideas.json.

The folder is named for the day in the creator's own time zone. Running again the same day replaces the
pull and the report and keeps any ideas already written. An offline run, on made-up sample videos, writes to
research/samples/<date>/ instead: no idea picker reads that folder, so a sample never replaces or shadows a
real run, whatever its date.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from kit import config
from kit.research import brain, common, offline as offline_sample, performance, rank, report, scrape, transcribe_queue, x_posts

MAX_VIDEOS = 80  # videos kept in items.json, best first
MAX_NOTES = 40
MAX_TRANSCRIPT = 1800  # characters of transcript kept in items.json
VERSION = 1

# Fields that only mattered while fetching, or that are signed links that expire.
_DROP = {"video_url", "caption_url", "subtitle_url", "thumb_url", "first_seen", "last_seen"}
EMPTY_WATCHLIST = "Your watch list is empty. Add creators to me/watchlist.json, or say: set me up"
SAMPLES = "samples"  # research/samples/<date>/: offline runs on made-up videos, a folder no idea picker reads


def _trim(video: dict) -> dict:
    """A video as items.json keeps it: no fetch links, tidy numbers, a transcript cut to a readable length."""
    kept = {k: v for k, v in video.items() if k not in _DROP}
    for key, digits in (("outlier", 2), ("engagement", 1), ("creator_median", 1)):
        if isinstance(kept.get(key), (int, float)):
            kept[key] = round(kept[key], digits)
    if kept.get("transcript"):
        kept["transcript"] = kept["transcript"][:MAX_TRANSCRIPT]
    return kept


def _check_existing(run_dir: Path, offline: bool, day: str, label: str) -> bool:
    """Never put a sample over a real run. Returns True when an offline sample from earlier today was cleared away.

    A sample goes to research/samples/, so it never meets a real run there. The refusal is a guard all the same,
    and its words send an agent to the creator, never to moving or deleting a folder.
    """
    existing = common.read_json(run_dir / "items.json")
    if not isinstance(existing, dict):
        return False
    was_offline = existing.get("mode") == "offline"
    if offline and not was_offline:
        raise config.KitError(
            f"{label} already holds a research run from your real watch list for {day}, so this sample run stopped "
            "and changed nothing. Ask the creator what they want to do. Never move or delete a run folder on your own."
        )
    if was_offline and not offline:
        for name in ("items.json", "ideas.json", "report.html"):
            (run_dir / name).unlink(missing_ok=True)
        return True
    return False


def _unique_links(notes: list[dict]) -> list[tuple[str, str, str]]:
    seen, links = set(), []
    for note in notes:
        for link in x_posts.find_links(" ".join(note["x_urls"])):
            if link[1] not in seen:
                seen.add(link[1])
                links.append(link)
    return links


def run(*, offline: bool = False, no_apify: bool = False, no_transcribe: bool = False, say=print) -> Path:
    """Run the research and return the run folder. `say` is given each line of the summary."""
    profile = config.load_profile()
    now = common.utcnow()
    tz = common.tz_of(profile)
    day = common.run_date(profile, now)
    run_dir = config.research_dir() / (SAMPLES if offline else "runs") / day
    label = common.kit_relative(run_dir)
    replaced_sample = _check_existing(run_dir, offline, day, label)

    say(f"Research for {profile.name}, {common.fmt_long_day(now, tz)}")
    if replaced_sample:
        say("Replaced the offline sample from earlier today")

    notes = x_posts.read_ideas_file(config.me_dir() / "ideas.md")[:MAX_NOTES]
    links = _unique_links(notes)
    workdir = run_dir / "media"
    try:
        if offline:
            history: dict = {}
            pull = offline_sample.sample_pull(now)
            posts = x_posts.read_posts(links, offline=True)
            results = performance.collect(profile, key=None, offline=True, now=now)
        else:
            history = brain.load_history()
            watchlist = scrape.load_watchlist()
            pull = scrape.pull(profile, watchlist, token=config.key("APIFY_TOKEN"), use_apify=not no_apify)
            if not any(watchlist.values()):
                pull.notices.append(EMPTY_WATCHLIST)
            posts = x_posts.read_posts(links)
            results = performance.collect(profile, key=config.key("ZERNIO_API_KEY"), now=now)

        scored = rank.score(pull.items, history, now=now)
        videos = scored.videos
        known = {} if offline else brain.known_transcripts(history)
        speech = transcribe_queue.attach(videos, known, local=not (offline or no_transcribe), workdir=workdir)
        rank.attach_hooks(videos)

        notices = list(pull.notices)
        if not offline:
            notices += brain.update(videos, today=day)
        notices += speech.notices

        pulled = [v for v in videos if v.get("source") == "run"]
        creators = [
            {**c, "median_engagement": round(c["median_engagement"], 1), "best_outlier": None if c["best_outlier"] is None else round(c["best_outlier"], 2)}
            for c in scored.creators
        ]
        data = {
            "version": VERSION,
            "date": day,
            "generated_at": now.isoformat(),
            "timezone": profile.timezone,
            "name": profile.name,
            "mode": "offline" if offline else "live",
            "window_days": common.WINDOW_DAYS,
            "notices": notices,
            "sources": pull.sources,
            "transcripts": {"brain": speech.brain, "captions": speech.captions, "local": speech.local, "missing": speech.missing},
            "creators": creators,
            "videos": [_trim(v) for v in videos[:MAX_VIDEOS]],
            # this run's own videos, counted before the list is cut: the earlier standouts from past runs are not
            # part of the pull the terminal reports, so they are left out of both numbers
            "videos_total": len(pulled),
            "videos_recent": sum(1 for v in pulled if v.get("set") == "recent"),
            "x_posts": posts,
            "notes": notes,
            "performance": results,
        }
        common.write_json(run_dir / "items.json", data)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    _summarise(data, say)
    _, problems = report.write(profile, run_dir)
    if offline:
        say(f"Wrote {label}/items.json and report.html")
        say(f"This was a sample run on made-up videos, kept in {label} apart from your real research. No idea or video plan reads it.")
        say(f"Next: open {label}/report.html to see the sample report, or say: run my research")
        return run_dir
    kept_ideas = (run_dir / "ideas.json").is_file() and not replaced_sample
    if kept_ideas:
        say(f"Kept the ideas already written for today in {label}/ideas.json")
        for problem in problems:
            say(f"ideas.json needs fixing: {problem}")
    say(f"Wrote {label}/items.json and report.html")
    if kept_ideas and not problems:
        say(f"Next: open {label}/report.html, or say: give me hooks for number 2")
    else:
        say(f"Next: read {label}/items.json and write the ideas into ideas.json, then run: uv run python -m kit.research report")
    return run_dir


def _summarise(data: dict, say) -> None:
    for platform in common.RESEARCH_PLATFORMS:
        source = data["sources"].get(platform) or {}
        if source.get("status") == "ok":
            listed = source.get("creators", [])
            made = [c for c in listed if c.get("videos")]
            cost = f" (US${source['cost_usd']:.2f})" if isinstance(source.get("cost_usd"), (int, float)) else ""
            say(f"{common.PLATFORM_LABEL[platform]}: {common.plural(source['videos'], 'video')} from {common.creators_phrase(len(made), len(listed))}{cost}")
    for notice in data["notices"]:
        say(notice)
    posts = data["x_posts"]
    if posts:
        say(f"X posts: {sum(1 for p in posts if p.get('status') == 'read')} of {len(posts)} read")
    else:
        say("X posts: none in me/ideas.md")
    say(f"Notes: {len(data['notes'])} in me/ideas.md")
    counts = data["transcripts"]
    parts = [f"{counts[k]} {label}" for k, label in (("brain", "from earlier runs"), ("captions", "from captions"), ("local", "transcribed here"), ("missing", "without one")) if counts[k]]
    if parts:
        say("Transcripts: " + ", ".join(parts))
    performance_result = data["performance"]
    if performance_result.get("status") == "ok":
        say(f"Your results: {common.plural(performance_result['feed_reels'], 'feed Reel')} in the last six weeks")
    else:
        say(performance_result["message"])
    scored = [v for v in data["videos"]
              if v.get("set") == "recent" and v.get("outlier") is not None and v.get("source", "run") == "run"]
    if scored:
        best = scored[0]
        say(f"Best this time: {best['outlier']:.1f}x, @{best['creator']} on {common.PLATFORM_LABEL.get(best['platform'], best['platform'])}")
