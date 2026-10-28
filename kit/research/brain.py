"""What earlier runs saw, kept under research/brain/ so every run has history to compare with.

There is one small file for each creator on each platform, such as research/brain/youtube/<name>.json.
It holds the numbers each video had the last time it was seen and the transcript when there is one, so
a creator's normal is worked out over months of videos rather than one pull, and a video is never
transcribed twice. The folder belongs to the creator. Delete a file to forget a creator.
"""

from __future__ import annotations

from pathlib import Path

from kit import config
from kit.research import common
from kit.research.rank import creator_key

VERSION = 1
KEEP = 200  # videos kept for each creator, newest first
MAX_TRANSCRIPT = 6000  # characters of transcript kept for one video

_FIELDS = ("url", "title", "posted_at", "views", "likes", "comments", "duration")


def root() -> Path:
    return config.research_dir() / "brain"


def _file(platform: str, creator: str) -> Path:
    return root() / platform / (common.safe_name(creator) + ".json")


def _read(path: Path) -> dict | None:
    data = common.read_json(path)
    if isinstance(data, dict) and isinstance(data.get("items"), dict):
        return data
    return None


def load_history() -> dict[tuple[str, str], dict[str, dict]]:
    """Every record on file, as {(platform, creator in lower case): {video id: record}}."""
    history: dict[tuple[str, str], dict[str, dict]] = {}
    for platform in common.RESEARCH_PLATFORMS:
        folder = root() / platform
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.json")):
            data = _read(path)
            if data is None:
                continue
            creator = str(data.get("creator") or path.stem)
            records = {}
            for key, record in data["items"].items():
                if isinstance(record, dict) and record.get("id") == key:
                    records[key] = {**record, "platform": platform, "creator": creator}
            if records:
                history.setdefault(creator_key(platform, creator), {}).update(records)
    return history


def known_transcripts(history: dict) -> dict[str, dict]:
    """{video id: {"text", "source"}} for every record that has a transcript."""
    known = {}
    for records in history.values():
        for video_id, record in records.items():
            if common.is_usable(record.get("transcript")):
                known[video_id] = {"text": record["transcript"], "source": record.get("transcript_source") or "an earlier run"}
    return known


def update(videos: list[dict], *, today: str) -> list[str]:
    """Add what this run saw to the brain. Returns plain sentences about any file that had to be set aside."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for video in videos:
        if video.get("source") == "brain":
            continue  # it came from the brain, so it is already there
        groups.setdefault(creator_key(video["platform"], video["creator"]), []).append(video)

    notices: list[str] = []
    for (platform, _), group in groups.items():
        creator = str(group[0]["creator"])
        path = _file(platform, creator)
        existing: dict[str, dict] = {}
        if path.exists():
            data = _read(path)
            if data is None:
                path.replace(path.with_name(path.name + ".unreadable"))
                notices.append(
                    f"The saved history for {creator} on {common.PLATFORM_LABEL[platform]} could not be read, "
                    f"so it was set aside as {path.name}.unreadable and started again"
                )
            else:
                existing = {k: r for k, r in data["items"].items() if isinstance(r, dict) and r.get("id") == k}
                creator = str(data.get("creator") or creator)

        for video in group:
            before = existing.get(video["id"], {})
            record = {"id": video["id"]}
            record.update({field: video.get(field) for field in _FIELDS})
            record["first_seen"] = before.get("first_seen") or today
            record["last_seen"] = today
            text = video.get("transcript") if common.is_usable(video.get("transcript")) else before.get("transcript")
            if text:
                record["transcript"] = text[:MAX_TRANSCRIPT]
                record["transcript_source"] = (
                    video.get("transcript_source") if text == video.get("transcript") else before.get("transcript_source")
                )
            existing[video["id"]] = record

        newest = sorted(existing.values(), key=lambda r: common.parse_iso(r.get("posted_at")) or common.parse_iso("1970-01-01"), reverse=True)
        kept = {r["id"]: r for r in newest[:KEEP]}
        common.write_json(path, {"version": VERSION, "platform": platform, "creator": creator, "updated": today, "items": kept})
    return notices
