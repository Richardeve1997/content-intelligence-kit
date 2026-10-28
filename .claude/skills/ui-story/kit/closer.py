#!/usr/bin/env python3
"""closer.py: the last beat of a UI story Reel, written from the creator's call to action (profile.cta).

A comment CTA (cta.type "comment") types the creator's keyword into a comment box, rings it in the accent
and labels it "Comment <KEYWORD>". A follow CTA shows the creator's profile row and presses Follow. The
spoken line is the default the plan's closing talking point starts from; write it in the creator's voice.

    python closer.py [--start 41.2] [--json | --html | --js]

--json prints the copy, --html the scene to paste inside #root, --js the timeline lines to paste into the
timeline script. With none of them it prints all three.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

from kit import config


def _handle(profile) -> str:
    handles = profile.handles
    for platform in ("instagram", "tiktok", "youtube", "twitter", "facebook", "linkedin"):
        value = getattr(handles, platform, "")
        if value:
            return value
    return profile.name


def closer_copy(profile) -> dict:
    """The words of the closer, all taken from profile.cta (and the name and handle for a follow)."""
    cta = profile.cta
    handle = _handle(profile)
    if cta.type == "comment" and cta.keyword:
        keyword = cta.keyword.strip()
        payload = (cta.payload or "").strip()
        spoken = f"Comment {keyword} and I'll send you {payload}." if payload else f"Comment {keyword}."
        return {"type": "comment", "keyword": keyword, "typed": keyword, "label": f"Comment {keyword}",
                "payload": payload or None, "spoken": spoken, "handle": handle, "name": profile.name}
    return {"type": "follow", "keyword": None, "button": "Follow", "label": f"Follow {handle}", "handle": handle,
            "name": profile.name, "spoken": "Follow for more."}


def _avatar(profile) -> str:
    if profile.logo:
        suffix = html.escape(Path(profile.logo).suffix.lower() or ".png")
        return f'<img src="assets/logo{suffix}" alt="" style="width:100%;height:100%;border-radius:50%;object-fit:cover">'
    return html.escape(profile.name[:1].upper())


def closer_html(profile) -> str:
    """The closer scene, for a split window. Paste it inside #root; closer_js gives its timing."""
    copy = closer_copy(profile)
    label = html.escape(copy["label"])
    if copy["type"] == "comment":
        keyword = html.escape(copy["keyword"])
        return (
            '<div class="scene" id="closer">\n'
            '  <div class="card comment-box" id="closer-box" style="top:330px">\n'
            '    <div class="row" style="height:116px;gap:22px;border:none">\n'
            '      <div class="who"></div>\n'
            f'      <div class="field"><span id="closer-typed" data-keyword="{keyword}">{keyword}</span>'
            '<span class="caret"></span></div>\n'
            '      <div class="send">Post</div>\n'
            "    </div>\n"
            "  </div>\n"
            '  <div class="ring" id="closer-ring" style="left:100px;top:320px;width:880px;height:138px"></div>\n'
            f'  <div class="chip up" id="closer-label" style="left:90px;top:500px">{label}</div>\n'
            "</div>"
        )
    name, handle = html.escape(copy["name"]), html.escape(copy["handle"])
    return (
        '<div class="scene" id="closer">\n'
        '  <div class="card" id="closer-box" style="left:90px;top:300px;width:900px;height:200px">\n'
        '    <div class="row" style="height:200px;gap:26px;border:none">\n'
        f'      <div class="who" style="width:112px;height:112px;font-size:46px">{_avatar(profile)}</div>\n'
        f'      <div class="txt"><div class="display" style="font-weight:700;font-size:40px">{name}</div>'
        f'<div class="muted" style="font-size:28px">{handle}</div></div>\n'
        '      <div style="position:relative;width:220px;height:64px">\n'
        '        <div class="follow-btn" id="closer-follow" style="position:absolute;inset:0">Follow</div>\n'
        '        <div class="follow-btn done" id="closer-following" style="position:absolute;inset:0">Following</div>\n'
        "      </div>\n"
        "    </div>\n"
        "  </div>\n"
        '  <div class="click" id="closer-click" style="left:880px;top:400px"></div>\n'
        f'  <div class="chip up" id="closer-label" style="left:90px;top:540px">{label}</div>\n'
        "</div>"
    )


def closer_js(profile, start: float) -> str:
    """The timeline lines for the closer scene. Paste them into the timeline script."""
    copy = closer_copy(profile)
    s = f"{start:.3f}"
    if copy["type"] == "comment":
        return "\n".join([
            "// closer: the keyword typed into a comment box",
            f"UIS.show(tl, '#closer', {s});",
            f"UIS.pop(tl, '#closer-box', {s});",
            f"const closerTyped = UIS.type(tl, '#closer-typed', {json.dumps(copy['typed'])}, {s} + 0.25);",
            "UIS.ring(tl, '#closer-ring', closerTyped + 0.05);",
            "UIS.chip(tl, '#closer-label', closerTyped + 0.15);",
        ])
    return "\n".join([
        "// closer: the creator's profile row, and Follow pressed",
        f"UIS.show(tl, '#closer', {s});",
        f"UIS.pop(tl, '#closer-box', {s});",
        f"UIS.show(tl, '#closer-following', {s} + 0.62);",
        f"UIS.click(tl, '#closer-click', {s} + 0.5);",
        f"UIS.chip(tl, '#closer-label', {s} + 0.7);",
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description="The closer, from the creator's call to action.")
    parser.add_argument("--start", type=float, default=0.0, help="when the closer starts, in composition seconds")
    shown = parser.add_mutually_exclusive_group()
    shown.add_argument("--json", action="store_true", help="print the copy only")
    shown.add_argument("--html", action="store_true", help="print the scene markup only")
    shown.add_argument("--js", action="store_true", help="print the timeline lines only")
    args = parser.parse_args()
    try:
        profile = config.load_profile()
    except config.KitError as problem:
        print(problem)
        return 1
    if args.json or not (args.html or args.js):
        print(json.dumps(closer_copy(profile), indent=2))
    if args.html or not (args.json or args.js):
        print(closer_html(profile))
    if args.js or not (args.json or args.html):
        print(closer_js(profile, args.start))
    return 0


if __name__ == "__main__":
    sys.exit(main())
