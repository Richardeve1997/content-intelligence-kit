"""A made-up sample for runs that must not touch the network.

"run my research --offline" uses this instead of the watch list, so the report can be tried on a fresh
copy of the kit with no keys and no connection. The creators are invented, the addresses point at a
reserved example site, and an offline run never adds to the brain.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from kit.research import common
from kit.research.scrape import Pull

SAMPLE_PATH = Path(__file__).with_name("offline_sample.json")
OFFLINE_NOTICE = "Offline run: these videos are made-up samples, not your watch list"


def sample_pull(now) -> Pull:
    """The sample videos, dated back from `now`, shaped like a real pull."""
    data = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    pull = Pull(notices=[OFFLINE_NOTICE])
    sources = {platform: {"status": "skipped", "tool": "", "creators": [], "videos": 0} for platform in common.RESEARCH_PLATFORMS}
    for entry in data["creators"]:
        platform, creator = entry["platform"], entry["creator"]
        source = sources[platform]
        source.update(status="ok", tool="sample data")
        source["creators"].append({"creator": creator, "videos": len(entry["videos"])})
        for video in entry["videos"]:
            item = {
                "id": f"{common.ID_PREFIX[platform]}:sample-{video['id']}",
                "platform": platform,
                "creator": creator,
                "url": f"https://example.com/sample/{platform}/{creator}/{video['id']}",
                "posted_at": (now - timedelta(days=video["days_ago"])).isoformat(),
                "title": video["title"],
                "caption": video["title"],
                "views": video["views"],
                "likes": video["likes"],
                "comments": video["comments"],
                "duration": video["duration"],
            }
            if video.get("transcript"):
                item["transcript"] = video["transcript"]
                item["transcript_source"] = "sample data"
            pull.items.append(item)
            source["videos"] += 1
    pull.sources = sources
    return pull
