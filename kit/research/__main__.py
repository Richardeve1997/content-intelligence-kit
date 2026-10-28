"""python -m kit.research: run the research, rebuild the report, or gather what hooks for an idea need.

    run [--offline] [--no-apify] [--no-transcribe]   gather, score and report
    report [--date YYYY-MM-DD]                       rebuild report.html from items.json and ideas.json
    hooks <n> [--save] [--date YYYY-MM-DD]           the brief for idea n, or save hooks written for it

A problem comes back as one plain sentence and exit status 1.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date as calendar_date
from pathlib import Path

from kit import config
from kit.research import common, ideas, pipeline, report

NO_RUN = "There is no research run yet. Say: run my research."


def _pick_run(day: str | None, need: str) -> Path:
    """The folder of the run on `day`, or the newest run that has the file `need`."""
    runs = config.research_dir() / "runs"
    if day is None:
        found = ideas.latest_run(runs, need)
        if found is None:
            raise ValueError(NO_RUN)
        return found
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            raise ValueError
        calendar_date.fromisoformat(day)
    except ValueError:
        raise ValueError(f"{day!r} is not a date. A date looks like 2026-10-28.") from None
    folder = runs / day
    if not (folder / need).is_file():
        latest = ideas.latest_run(runs, need)
        if latest is None:
            raise ValueError(NO_RUN)
        raise ValueError(f"There is no research run for {day}. The latest is {latest.name}.")
    return folder


def _run_command(args) -> int:
    pipeline.run(offline=args.offline, no_apify=args.no_apify, no_transcribe=args.no_transcribe)
    return 0


def _report_command(args) -> int:
    profile = config.load_profile()
    folder = _pick_run(args.date, "items.json")
    path, problems = report.write(profile, folder)
    print(f"Wrote {common.kit_relative(path)}")
    if problems:
        print("ideas.json needs fixing:", file=sys.stderr)
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        return 1
    return 0


def _hooks_command(args) -> int:
    runs = config.research_dir() / "runs"
    if args.date:
        folder = _pick_run(args.date, "ideas.json")
    else:
        folder = ideas.latest_run(runs, "ideas.json")
        if folder is None:
            raise ValueError(ideas.NO_IDEAS_YET)
    label = common.kit_relative(folder)

    if args.save:
        profile = config.load_profile()
        hooks = ideas.parse_hooks(sys.stdin.read())
        ideas.save_hooks(folder, args.n, hooks)
        report.write(profile, folder)
        print(f"Saved {len(hooks)} hooks for idea {args.n} in {label}/ideas.json and refreshed report.html")
        return 0

    found, problems = ideas.read(folder)
    if found is None:
        raise ValueError("ideas.json needs fixing first:\n" + "\n".join(f"- {p}" for p in problems))
    idea = ideas.find(found, args.n, run_label=label)
    newer = ideas.latest_run(runs, "items.json")
    if newer is not None and newer.name > folder.name and not args.date:
        print(f"The run on {newer.name} is newer but has no ideas yet.")
    data = common.read_json(folder / "items.json", {})
    print(ideas.hooks_brief(idea, data if isinstance(data, dict) else {}, run_label=label))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m kit.research",
        description="Run the research, rebuild the report, or gather what hooks for one idea need.",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")

    one = commands.add_parser("run", help="gather what the watch list posted, score it, and write items.json and report.html")
    one.add_argument("--offline", action="store_true", help="use made-up sample videos and no network (for trying the kit)")
    one.add_argument("--no-apify", action="store_true", help="skip Instagram and TikTok even if there is an Apify key")
    one.add_argument("--no-transcribe", action="store_true", help="do not turn videos into text on this machine")

    one = commands.add_parser("report", help="rebuild report.html from items.json and ideas.json")
    one.add_argument("--date", help="the run to rebuild, such as 2026-10-28 (the latest by default)")

    one = commands.add_parser("hooks", help="print the brief for writing hooks for idea n, or save the hooks written for it")
    one.add_argument("n", type=int, help="the idea's number in the latest run")
    one.add_argument("--save", action="store_true", help="read 5 to 10 hooks from standard input, one per line, and save them")
    one.add_argument("--date", help="the run to use, such as 2026-10-28 (the latest with ideas by default)")

    args = parser.parse_args(argv)
    handler = {"run": _run_command, "report": _report_command, "hooks": _hooks_command}[args.command]
    try:
        return handler(args)
    except (config.KitError, ValueError) as exc:
        print(exc, file=sys.stderr)
    except OSError as exc:
        print(f"The research files could not be used ({exc.strerror or type(exc).__name__}).", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
