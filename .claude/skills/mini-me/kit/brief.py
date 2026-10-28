#!/usr/bin/env python3
"""brief.py: everything the narration needs, in one place, before writing script.md.

    python brief.py --number 3 [--slug <video name>]   save idea 3 of the latest research run as
                                                       work/<video>/idea.json, then print the brief
    python brief.py <video name>                       print the brief for a video that has idea.json

The brief holds the idea, who the creator is and who they talk to, the closing line their call to action
gives, the creator's rules for this skill, their voice model (me/voice/VOICE.md) and the shape of script.md.
The narration itself is written fresh from these, in the creator's voice, then put through the humanizer.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from kit import config, rules
from kit.config import KitError

SKELETON = """# {title}

## Narration

<the words the character says, as plain sentences: about 60 to 110 words, 20 to 40 seconds>

## Notes

- Hook: <what the first sentence makes clear>
- Facts: <each number or claim, with where it comes from>
- On screen: <a receipt, object or stamp for the pages that need one>
"""


def latest_ideas() -> Path | None:
    runs = sorted((config.research_dir() / "runs").glob("*/ideas.json"))
    return runs[-1] if runs else None


def save_idea(number: int, slug: str | None) -> str:
    source = latest_ideas()
    if source is None:
        raise KitError("There is no research run yet. Say: run my research, then pick an idea by number.")
    ideas = json.loads(source.read_text(encoding="utf-8"))
    match = next((idea for idea in ideas if idea.get("n") == number), None)
    if match is None:
        raise KitError(f"The latest run ({source.parent.name}) has no idea number {number}.")
    slug = slug or config.free_video_name(match.get("title") or f"idea {number}")
    folder = config.work_dir(slug)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "idea.json").write_text(json.dumps(match, indent=2) + "\n", encoding="utf-8")
    (folder / "take").mkdir(exist_ok=True)
    print(f"saved idea {number} as work/{slug}/idea.json")
    return slug


def closing_line(profile) -> str:
    """The closing line to start from, written from the creator's call to action."""
    cta = profile.cta
    if cta.type == "comment" and cta.keyword:
        payload = (cta.payload or "").strip()
        return f"Comment {cta.keyword} and I'll send you {payload}." if payload else f"Comment {cta.keyword}."
    return "Follow for more."


def brief(slug: str) -> str:
    folder = config.work_dir(slug)
    profile = config.load_profile()
    idea_path = folder / "idea.json"
    if not idea_path.is_file():
        raise KitError(f"work/{slug}/idea.json is missing. Pick an idea first, such as: make number 3 a mini me video.")
    idea = json.loads(idea_path.read_text(encoding="utf-8"))
    voice = config.me_dir() / "voice" / "VOICE.md"
    lines = [f"# Narration brief: {slug}", "", "## The idea"]
    for key in ("title", "angle", "why", "format"):
        if idea.get(key):
            lines.append(f"- {key}: {idea[key]}")
    for hook in idea.get("hooks", []) or []:
        lines.append(f"- hook option: {hook}")
    for source in idea.get("evidence", []) or []:
        if isinstance(source, dict) and source.get("url"):
            lines.append(f"- evidence: {source['url']}")
    lines += ["", "## The creator",
              f"- {profile.name}: {profile.niche}. Credibility: {profile.credibility}",
              f"- Audience: {profile.audience.who}. They want: {profile.audience.wants}",
              f"- Call to action: {profile.cta.type}. Closing line to start from: {closing_line(profile)}",
              "", "## The creator's rules for this skill", rules.read("mini-me").strip() or "(none yet)",
              "", "## Voice model (me/voice/VOICE.md)",
              voice.read_text(encoding="utf-8").strip() if voice.is_file() else "(missing: build it with the voice skill)",
              "", "## script.md shape", SKELETON.format(title=idea.get("title", slug)),
              f"Write it to work/{slug}/script.md"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="The brief for writing a mini me narration.")
    ap.add_argument("slug", nargs="?", help="the video name (a folder in work/)")
    ap.add_argument("--number", type=int, help="save this idea number from the latest research run first")
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        slug = save_idea(a.number, a.slug) if a.number is not None else a.slug
        if not slug:
            ap.error("give a video name, or --number to start one from the latest research run")
        print(brief(slug))
    except (KitError, ValueError) as problem:
        print(problem)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
