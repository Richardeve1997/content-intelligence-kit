"""The post commands, for a shell. A skill cannot call Python directly, so each operation is also a command:

    python -m kit.post check                              which platforms are connected in Zernio
    python -m kit.post lines [--tags "basil herbs"]       the call to action and tags to use on each platform
    python -m kit.post captions <video>                   check work/<video>/captions.json against the rules
    python -m kit.post approve <video>                    record the creator's yes to those exact captions
    python -m kit.post schedule <video> --on tomorrow --time 18:00 [--platforms a,b] [--dry-run]
    python -m kit.post schedule <video> --on tomorrow     each platform at its usual time from the profile
    python -m kit.post schedule <video> --at 2026-10-29T18:00
    python -m kit.post list <video>                       what was scheduled, as Zernio reports it
    python -m kit.post reschedule <video> <platform> --on 2026-10-30 --time 09:00
    python -m kit.post cancel <video> <platform>
    python -m kit.post manual <video>                     write the file for posting by hand

A time with no zone is read in the creator's time zone from the profile. `schedule` refuses to send until
`approve` has been run on the captions as they are now, and any change to a caption undoes the approval.
The refusal comes from kit.post.schedule itself, so calling the Python function directly is no way round it.
Problems come back as plain sentences on standard error with exit status 1. A platform that fails also
gives exit status 1, and one that is skipped does not.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from kit import config, post
from kit.config import KitError


def _sentence(text: str | None) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else text + "."


def _when_text(iso: str | None, zone: ZoneInfo) -> str:
    if not iso:
        return "a time Zernio did not give"
    return post.describe_time(datetime.fromisoformat(iso).astimezone(zone))


def _line(result: post.Result, zone: ZoneInfo) -> str:
    name = post.captions.display_name(result.platform)
    note = _sentence(result.reason)
    if result.status == "scheduled":
        head = f"{name}: scheduled for {_when_text(result.scheduled_for, zone)}."
    elif result.status == "published":
        head = f"{name}: posted" + (f" at {result.url}." if result.url else ".")
    elif result.status == "cancelled":
        head = f"{name}: cancelled."
    elif result.status == "dry run":
        head = f"{name}: dry run."
    else:
        head = f"{name}: {result.status}."
    return f"{head} {note}".strip()


def _print_results(results: list[post.Result], zone: ZoneInfo) -> int:
    for result in results:
        print(_line(result, zone))
    return 1 if any(r.status == "failed" for r in results) else 0


# --- reading times and platforms -----------------------------------------------------------------------


def _platform_list(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    return [part for value in values for part in re.split(r"[,\s]+", value) if part]


def _day(text: str, zone: ZoneInfo) -> date:
    today = datetime.now(zone).date()
    word = text.strip().casefold()
    if word == "today":
        return today
    if word == "tomorrow":
        return today + timedelta(days=1)
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        raise post.PostError(f"{text!r} is not a day. Use today, tomorrow or a date such as 2026-10-29.") from None


def _clock(text: str) -> time:
    found = re.fullmatch(r"(\d{1,2}):(\d{2})", text.strip())
    if found and int(found.group(1)) < 24 and int(found.group(2)) < 60:
        return time(int(found.group(1)), int(found.group(2)))
    raise post.PostError(f"{text!r} is not a time. Use the 24 hour form, such as 18:00.")


def _times(profile, args, platforms: list[str]) -> dict[datetime, list[str]]:
    """The platforms grouped by the moment each is to go out."""
    zone = ZoneInfo(profile.timezone)
    groups: dict[datetime, list[str]] = defaultdict(list)
    if args.at:
        try:
            moment = datetime.fromisoformat(args.at.strip())
        except ValueError:
            raise post.PostError(f"{args.at!r} is not a date and time. Write it like 2026-10-29T18:00.") from None
        groups[post.local_time(moment, profile.timezone)] = list(platforms)
        return groups
    day = _day(args.on, zone)
    if args.time:
        moment = datetime.combine(day, _clock(args.time), tzinfo=zone)
        groups[moment] = list(platforms)
        return groups
    for name in platforms:
        groups[post.default_when(profile, name, day)].append(name)
    return groups


# --- the commands ------------------------------------------------------------------------------------------


def _check(args) -> int:
    for name, state in post.accounts_check().items():
        print(f"{post.captions.display_name(name)}: {state}")
    return 0


def _lines(args) -> int:
    profile = config.load_profile()
    video_tags = [t if t.startswith("#") else "#" + t for t in re.split(r"[,\s]+", args.tags or "") if t]
    shown = {}
    for name in profile.platforms:
        shown[name] = {
            "cta": post.captions.cta_line(profile, name),
            "hashtags": post.captions.select_hashtags(profile, name, video_tags),
            "hashtag_limit": post.captions.HASHTAG_CAPS[name],
        }
    print(json.dumps(shown, indent=2, ensure_ascii=False))
    return 0


def _captions(args) -> int:
    profile = config.load_profile()
    problems = post.captions.check(post.captions.load(args.video), profile)
    if not problems:
        print("All captions pass.")
        return 0
    for problem in problems:
        print(problem)
    return 1


def _approve(args) -> int:
    profile = config.load_profile()
    problems = post.captions.check(post.captions.load(args.video), profile)
    if problems:
        raise post.CaptionProblems(problems)
    post.captions.approve(args.video)
    print(f"Recorded the creator's approval of the captions for {args.video}.")
    return 0


def _schedule(args) -> int:
    profile = config.load_profile()
    zone = ZoneInfo(profile.timezone)
    names = post.platform_names(_platform_list(args.platforms), profile)
    groups = _times(profile, args, names)
    # A dry run for every group first: whatever can be wrong on this machine is found before a request leaves.
    plan = [r for moment, group in groups.items() for r in post.schedule(args.video, group, moment, dry_run=True)]
    plan.sort(key=lambda r: profile.platforms.index(r.platform))
    if args.dry_run:
        code = _print_results(plan, zone)
        print("Nothing was sent. To see which platforms are connected, run: uv run python -m kit.post check")
        return code
    results: list[post.Result] = []
    try:
        for moment, group in groups.items():
            results.extend(post.schedule(args.video, group, moment))
    except post.NoZernio as exc:
        if results:
            raise
        print(exc)  # the manual file is written, and that is the answer
        return 0
    except KitError as exc:
        if not results:
            raise
        # Some platforms were already scheduled, so say so before the problem that stopped the rest.
        results.sort(key=lambda r: profile.platforms.index(r.platform))
        _print_results(results, zone)
        print(exc, file=sys.stderr)
        return 1
    results.sort(key=lambda r: profile.platforms.index(r.platform))
    return _print_results(results, zone)


def _list(args) -> int:
    profile = config.load_profile()
    results = post.list_scheduled(args.video)
    if not results:
        print(f"Nothing is scheduled for {args.video}.")
        return 0
    return _print_results(results, ZoneInfo(profile.timezone))


def _reschedule(args) -> int:
    profile = config.load_profile()
    name = post.platform_names([args.platform], profile)[0]
    ((moment, _),) = _times(profile, args, [name]).items()
    return _print_results([post.reschedule(args.video, name, moment)], ZoneInfo(profile.timezone))


def _cancel(args) -> int:
    profile = config.load_profile()
    return _print_results([post.cancel(args.video, args.platform)], ZoneInfo(profile.timezone))


def _manual(args) -> int:
    path = post.write_manual(args.video)
    print(f"The captions and the video are in work/{args.video}/post/{path.name}, ready to post by hand.")
    return 0


# --- the parser --------------------------------------------------------------------------------------------------


def _add_when(command: argparse.ArgumentParser) -> None:
    where = command.add_mutually_exclusive_group(required=True)
    where.add_argument("--at", help="a date and time such as 2026-10-29T18:00, read in the creator's time zone")
    where.add_argument("--on", help="today, tomorrow or a date such as 2026-10-29")
    command.add_argument("--time", help="the 24 hour time on that day, such as 18:00. Without it each platform uses its usual time")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m kit.post",
        description="Write, check and schedule the posts for a finished video through Zernio.",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")

    commands.add_parser("check", help="show which platforms are connected in Zernio")

    one = commands.add_parser("lines", help="show the call to action and the hashtags to use on each platform")
    one.add_argument("--tags", help="the video's own hashtags, such as: basil windowsill herbs")

    one = commands.add_parser("captions", help="check the captions against the rules")
    one.add_argument("video")

    one = commands.add_parser("approve", help="record that the creator has read and approved these captions")
    one.add_argument("video")

    one = commands.add_parser("schedule", help="schedule the video on each platform")
    one.add_argument("video")
    one.add_argument("--platforms", nargs="+", help="the platforms to use, such as instagram tiktok. Without it, all of them")
    _add_when(one)
    one.add_argument("--dry-run", action="store_true", help="show what would happen and send nothing")

    one = commands.add_parser("list", help="show what was scheduled for the video")
    one.add_argument("video")

    one = commands.add_parser("reschedule", help="move one scheduled post to a new time")
    one.add_argument("video")
    one.add_argument("platform")
    _add_when(one)

    one = commands.add_parser("cancel", help="cancel one scheduled post")
    one.add_argument("video")
    one.add_argument("platform")

    one = commands.add_parser("manual", help="write the file for posting by hand")
    one.add_argument("video")
    return parser


_COMMANDS = {
    "check": _check,
    "lines": _lines,
    "captions": _captions,
    "approve": _approve,
    "schedule": _schedule,
    "list": _list,
    "reschedule": _reschedule,
    "cancel": _cancel,
    "manual": _manual,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _COMMANDS[args.command](args)
    except (KitError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1
