#!/usr/bin/env python3
"""plan_brief.py: everything the plan step needs, in one place, before writing plan.md.

    python plan_brief.py --number 2 [--slug <video name>]   save idea 2 of the latest research run as
                                                             work/<video>/idea.json, then print the brief
    python plan_brief.py <video name>                        print the brief for a video that has idea.json

The brief holds the idea, who the creator is and who they talk to, their call to action and the closing line
it gives, the creator's rules for this skill, their voice model (me/voice/VOICE.md), and the shape of
plan.md. The plan itself is written fresh from these, in the creator's voice, then put through the humanizer.
"""
import argparse
import json
import re
import sys
from pathlib import Path

from closer import closer_copy
from kit import config, rules

SKELETON = """# {title}

## Talking points

1. Hook: <the problem or the result, said plainly in the first sentence>
2. <one point per line, in the order they are said>
3. ...
N. Close: <the closing line, from the call to action>

## Film guide

- <setup and delivery notes for this video; the standard ones are in the skill>

## On screen

- <what each talking point shows: the real product, a picture of the idea, the proof card>
"""


def slug_from(title: str) -> str:
    words = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return words[:60].rstrip("-") or "video"


def latest_ideas() -> Path | None:
    runs = sorted((config.research_dir() / "runs").glob("*/ideas.json"))
    return runs[-1] if runs else None


def save_idea(number: int, slug: str | None) -> str:
    source = latest_ideas()
    if source is None:
        raise SystemExit("There is no research run yet. Say: run my research, then pick an idea by number.")
    ideas = json.loads(source.read_text(encoding="utf-8"))
    match = next((idea for idea in ideas if idea.get("n") == number), None)
    if match is None:
        raise SystemExit(f"The latest run ({source.parent.name}) has no idea number {number}.")
    slug = slug or slug_from(match.get("title", f"idea-{number}"))
    folder = config.work_dir(slug)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "idea.json").write_text(json.dumps(match, indent=2) + "\n", encoding="utf-8")
    (folder / "take").mkdir(exist_ok=True)
    print(f"saved idea {number} as work/{slug}/idea.json")
    return slug


def main() -> int:
    ap = argparse.ArgumentParser(description="The brief for writing a UI story plan.")
    ap.add_argument("slug", nargs="?", help="the video name (a folder in work/)")
    ap.add_argument("--number", type=int, help="save this idea number from the latest research run first")
    a = ap.parse_args()
    try:
        slug = save_idea(a.number, a.slug) if a.number is not None else a.slug
        if not slug:
            ap.error("give a video name, or --number to start one from the latest research run")
        folder = config.work_dir(slug)
        profile = config.load_profile()
    except ValueError as problem:
        print(problem)
        return 1
    except config.KitError as problem:
        print(problem)
        return 1
    idea_path = folder / "idea.json"
    if not idea_path.is_file():
        print(f"work/{slug}/idea.json is missing. Pick an idea first, such as: plan the video for number 2.")
        return 1
    idea = json.loads(idea_path.read_text(encoding="utf-8"))
    closing = closer_copy(profile)
    voice = config.me_dir() / "voice" / "VOICE.md"

    print(f"# Plan brief: {slug}\n")
    print("## The idea")
    for key in ("title", "angle", "why", "format"):
        if idea.get(key):
            print(f"- {key}: {idea[key]}")
    for hook in idea.get("hooks", []) or []:
        print(f"- hook option: {hook}")
    print("\n## The creator")
    print(f"- {profile.name}: {profile.niche}. Credibility: {profile.credibility}")
    print(f"- Audience: {profile.audience.who}. They want: {profile.audience.wants}")
    print(f"- Call to action: {closing['type']}. Closing line to start from: {closing['spoken']}")
    if closing["type"] == "comment":
        print(f"  The closer on screen types {closing['keyword']} into a comment box.")
    print("\n## The creator's rules for this skill")
    print(rules.read("ui-story").strip() or "(none yet)")
    print("\n## Voice model (me/voice/VOICE.md)")
    print(voice.read_text(encoding="utf-8").strip() if voice.is_file() else "(missing: build it with the voice skill)")
    print("\n## plan.md shape")
    print(SKELETON.format(title=idea.get("title", slug)))
    print(f"Write it to work/{slug}/plan.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
