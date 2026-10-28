"""The ranked ideas file, and what is needed to write hooks for one idea.

research/runs/<date>/ideas.json is written by the skill after it reads items.json. Its shape is fixed
(kit/schema/ideas.schema.json) because the video styles read it: "number 2" always means the idea whose
n is 2 in the latest run. This module checks the file and explains any problem as a plain sentence,
finds the latest run, builds the brief the skill writes hooks from, and saves the hooks it writes.
"""

from __future__ import annotations

import difflib
import json
import re
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator

from kit.research import common

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "ideas.schema.json"
HOOKS_MIN, HOOKS_MAX = 5, 10
VOICE_FILE = "me/voice/VOICE.md"
NO_IDEAS_YET = "There are no ideas to work from yet. Ask Claude to write the ideas for the latest run first, or say: run my research."

_DATE_FOLDER = re.compile(r"\d{4}-\d{2}-\d{2}")


@lru_cache(maxsize=1)
def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


# --- checking the file --------------------------------------------------------------------------------


def _should(node: dict) -> str:
    description = node.get("description")
    return f" It should be {description}." if description else ""


def _scope(path: list, idea_schema: dict) -> tuple[str, str | None, dict]:
    """Where an error is: (the place in words, the field it is about or None, the schema of the object that holds the field)."""
    idea = f"Idea {path[0] + 1}"
    if len(path) >= 3 and path[1] == "evidence":
        place = f"{idea}, evidence {path[2] + 1}"
        return place, (path[3] if len(path) > 3 else None), idea_schema["properties"]["evidence"]["items"]
    if len(path) >= 3 and path[1] == "hooks":
        return f"{idea}, hook {path[2] + 1}", None, idea_schema
    return idea, (path[1] if len(path) > 1 else None), idea_schema


def _sentences(error, idea_schema: dict) -> list[str]:
    """One or more plain sentences for one schema error."""
    path = list(error.absolute_path)
    kind = error.validator
    if not path:
        if kind == "type":
            return ["ideas.json must hold a list of ideas between [ and ]."]
        return ["ideas.json has no ideas. Write at least one."]

    place, field, owner = _scope(path, idea_schema)
    subject = f"{place}: {field}" if field else place

    if kind == "required":
        return [f"{place} is missing {name}.{_should(owner['properties'][name])}" for name in error.validator_value if name not in error.instance]
    if kind == "additionalProperties":
        known = list(owner["properties"])
        found = []
        for extra in sorted(key for key in error.instance if key not in known):
            near = difflib.get_close_matches(extra, known, n=1, cutoff=0.6)
            hint = f" Did you mean {near[0]}?" if near else ""
            found.append(f"{place} has a field called {extra}, which the kit does not read.{hint}")
        return found
    if kind == "type":
        wanted = {"object": "is not a group of fields between { and }", "string": "should be text", "integer": "should be a whole number",
                  "number": "should be a number", "array": "should be a list"}.get(error.validator_value)
        return [f"{subject} {wanted}." if wanted else f"{subject} is the wrong kind of value."]
    if field == "url" and kind in ("minLength", "pattern"):
        return [f"{subject} should be a web link starting with https:// or http://."]
    if kind in ("minLength", "pattern"):
        return [f"{subject} is empty."]
    if kind == "maxLength":
        return [f"{subject} is longer than {error.validator_value} characters."]
    if kind == "minimum":
        return [f"{subject} should be 1 or more." if field == "n" else f"{subject} should be a number of {error.validator_value} or more."]
    return [f"{subject} is not valid."]


def validate(data) -> list[str]:
    """Every problem with an ideas file, as plain sentences. An empty list means it is good."""
    schema = _schema()
    idea_schema = schema["items"]
    problems: list[str] = []
    errors = sorted(Draft202012Validator(schema).iter_errors(data), key=lambda e: [str(p) for p in e.absolute_path])
    for error in errors:
        problems.extend(_sentences(error, idea_schema))

    if isinstance(data, list) and data and all(isinstance(i, dict) and type(i.get("n")) is int for i in data):
        numbers = [i["n"] for i in data]
        wanted = list(range(1, len(data) + 1))
        if numbers != wanted:
            problems.append(
                f"The ideas must be numbered {', '.join(map(str, wanted))} in order, with no gaps or repeats. "
                f"This file numbers them {', '.join(map(str, numbers))}."
            )
    return list(dict.fromkeys(problems))


def read(run_dir) -> tuple[list[dict] | None, list[str]]:
    """The ideas written for a run: (ideas, []) when the file is good, (None, []) when there is none yet, (None, problems) when it needs fixing."""
    path = Path(run_dir) / "ideas.json"
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None, []
    except OSError as exc:
        return None, [f"ideas.json could not be read ({exc.strerror or type(exc).__name__})."]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, [f"ideas.json is not valid JSON ({exc.msg}, on line {exc.lineno}). Check for a missing comma, quote or bracket."]
    problems = validate(data)
    return (None, problems) if problems else (data, [])


def latest_run(runs_dir, need: str) -> Path | None:
    """The newest run folder (named by its date) that holds the file `need`."""
    runs_dir = Path(runs_dir)
    if not runs_dir.is_dir():
        return None
    folders = sorted(p for p in runs_dir.iterdir() if p.is_dir() and _DATE_FOLDER.fullmatch(p.name) and (p / need).is_file())
    return folders[-1] if folders else None


def find(ideas: list[dict], n: int, *, run_label: str) -> dict:
    """The idea whose n is `n`."""
    for idea in ideas:
        if idea["n"] == n:
            return idea
    numbers = [i["n"] for i in ideas]
    if len(numbers) == 1:
        have = f"idea {numbers[0]}"
    elif len(numbers) == 2:
        have = f"ideas {numbers[0]} and {numbers[1]}"
    else:
        have = f"ideas {min(numbers)} to {max(numbers)}"
    raise ValueError(f"There is no idea number {n} in {run_label}. It has {have}.")


# --- hooks ------------------------------------------------------------------------------------------------------


def _evidence_for(idea: dict, data: dict) -> tuple[list[dict], list[dict], int]:
    """(videos, X posts, count not found) for the idea's evidence, videos strongest first."""
    videos = {common.norm_url(v["url"]): v for v in data.get("videos", [])}
    posts = {common.norm_url(p["url"]): p for p in data.get("x_posts", []) if p.get("status") == "read"}
    found_videos, found_posts, missing, seen = [], [], 0, set()
    for entry in idea.get("evidence", []):
        key = common.norm_url(entry["url"])
        if key in seen:
            continue
        seen.add(key)
        if key in videos:
            found_videos.append(videos[key])
        elif key in posts:
            found_posts.append(posts[key])
        else:
            missing += 1
    found_videos.sort(key=lambda v: -(v.get("outlier") or 0.0))
    return found_videos, found_posts, missing


def _opening(video: dict) -> list[str]:
    hook = video.get("hook") or video.get("title") or ""
    if video.get("hook_source") == "transcript":
        lines = [f'   Opening line: "{hook}"']
        after = common.split_opening(video.get("transcript"))[1]
        if after:
            lines.append(f'   Then: "{common.words(after, 35)}"')
        return lines
    return [f'   Caption: "{hook}" (no transcript, so this is the caption and not the spoken opening)']


def hooks_brief(idea: dict, data: dict, *, run_label: str, voice_path: str = VOICE_FILE) -> str:
    """What the skill needs to write hooks for one idea: the idea, the openings of its strongest evidence, and the rules."""
    videos, posts, missing = _evidence_for(idea, data)
    lines = [
        f"Idea {idea['n']}: {idea['title']}",
        f"Angle: {idea['angle']}",
        f"Why: {idea['why']}",
        f"Format: {idea['format']}",
        f"Run: {run_label}",
        "",
    ]
    fallback = not videos and not posts
    if fallback:
        videos = [v for v in data.get("videos", []) if v.get("outlier") is not None][:5]
        lines.append("None of this idea's evidence has an opening line on file. These are the strongest videos in this run instead:")
    else:
        lines.append("Evidence, strongest first:")
    for position, video in enumerate(videos, 1):
        flag = ", low sample, so the number is rough" if video.get("low_sample") else ""
        score = video.get("outlier")
        how_well = f"{score:.1f}x their normal" if score is not None else "no score"
        lines.append(
            f"{position}. @{video['creator']} on {common.PLATFORM_LABEL.get(video['platform'], video['platform'])}, "
            f"{how_well}, {common.short_num(video.get('views'))} views{flag}"
        )
        lines.extend(_opening(video))
        lines.append(f"   {video['url']}")
    for post in posts:
        lines.append(f"- @{post.get('handle')} on X, {common.short_num(post.get('views'))} views")
        lines.append(f'   "{common.words(post.get("text"), 60)}"')
        lines.append(f"   {post['url']}")
    if missing and not fallback:
        lines.append(f"({common.plural(missing, 'piece')} of evidence is not in items.json and is left out.)")
    if idea.get("hooks"):
        lines += ["", "Hooks it already has (do not repeat them):"] + [f"- {hook}" for hook in idea["hooks"]]
    lines += [
        "",
        f"Write between {HOOKS_MIN} and {HOOKS_MAX} hooks for this idea in the creator's voice. Read {voice_path} first.",
        "Take the shape of each hook from the opening lines above, and never copy one. Put the best first.",
        f"Save them with: uv run python -m kit.research hooks {idea['n']} --save",
    ]
    return "\n".join(lines)


_LIST_MARK = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+")
_QUOTES = "\"'“”‘’"


def parse_hooks(text: str) -> list[str]:
    """Hooks from pasted text, one per line, without list marks or quotation marks around them."""
    hooks = []
    for line in (text or "").splitlines():
        hook = _LIST_MARK.sub("", line).strip().strip(_QUOTES).strip()
        if hook:
            hooks.append(hook)
    return hooks


def save_hooks(run_dir, n: int, hooks: list[str]) -> list[dict]:
    """Replace the hooks of idea `n` in the run's ideas.json. Nothing is written unless the hooks and the file are good."""
    run_dir = Path(run_dir)
    label = common.kit_relative(run_dir)
    ideas, problems = read(run_dir)
    if ideas is None:
        if problems:
            raise ValueError("ideas.json needs fixing first:\n" + "\n".join(f"- {p}" for p in problems))
        raise ValueError("There are no ideas to add hooks to yet. Ask Claude to write the ideas first.")
    idea = find(ideas, n, run_label=label)
    if not HOOKS_MIN <= len(hooks) <= HOOKS_MAX:
        raise ValueError(f"Write between {HOOKS_MIN} and {HOOKS_MAX} hooks for each idea. This is {len(hooks)}.")
    cleaned = []
    for position, hook in enumerate(hooks, 1):
        hook = str(hook).strip()
        if not hook:
            raise ValueError(f"Idea {n}, hook {position} is empty.")
        if len(hook) > 300:
            raise ValueError(f"Idea {n}, hook {position} is longer than 300 characters.")
        cleaned.append(hook)
    idea["hooks"] = cleaned
    common.write_json(run_dir / "ideas.json", ideas, indent=2)
    return ideas
