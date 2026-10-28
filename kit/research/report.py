"""Builds report.html from a run's items.json and ideas.json, in the creator's own brand.

The page is one file. Its colours and fonts come from the creator's tokens (kit.brand.tokens_css), written
into the page itself with font addresses that work from research/runs/<date>/. It has no scripts and no
pictures and asks the web for nothing, so it opens the same anywhere. Everything that came from the web
(captions, post text, names) is escaped, and only http and https addresses become links.
"""

from __future__ import annotations

import html
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from kit import brand
from kit.research import common, ideas as ideas_module
from kit.research.offline import OFFLINE_NOTICE
from kit.research.rank import EARLIER_LIMIT, EARLIER_MIN_OUTLIER

TEMPLATE = Path(__file__).with_name("report_template.html")
MAX_LISTED = 20  # recent videos shown
TIMES = "×"

E = html.escape


@dataclass
class Page:
    profile: object
    data: dict
    ideas: list | None
    problems: list
    tz: object
    by_url: dict


def _href(url) -> str | None:
    """The address when it is a plain web link, else None."""
    text = str(url or "").strip()
    parsed = urlparse(text)
    if parsed.scheme in ("http", "https") and parsed.netloc and not any(c in text for c in ' "<>'):
        return text
    return None


def _link(url, label: str, *, cls: str = "") -> str:
    href = _href(url)
    if not href:
        return ""
    extra = f' class="{cls}"' if cls else ""
    return f'<a{extra} href="{E(href)}" target="_blank" rel="noopener noreferrer">{E(label)}</a>'


def _score(value) -> str:
    if value is None:
        return "no score"
    return (f"{value:.0f}" if value >= 100 else f"{value:.1f}") + TIMES


def _strong(value) -> str:
    """The class that gives a score the accent colour. Only a score of 2 times normal or more earns it."""
    return "score strong" if value is not None and value >= EARLIER_MIN_OUTLIER else "score"


def _plural_creators(count: int) -> str:
    return common.plural(count, "creator")


# --- the pieces ---------------------------------------------------------------------------------------------


def _hero(p: Page) -> str:
    """The top of the page. Its big numbers are the whole pull, the same ones the run printed in the terminal; the
    sentence above them says how many of those videos are from the last two weeks."""
    data = p.data
    videos = data.get("videos", [])
    this_run = [v for v in videos if v.get("source", "run") == "run"]  # not the earlier standouts from past runs
    recent = [v for v in this_run if v.get("set") == "recent"]
    if "videos_recent" in data:  # counted by the run before the list was cut short, as the terminal counts
        total, recent_count = data.get("videos_total", 0), data["videos_recent"]
    else:  # a run from before these counts: its total also counted the earlier standouts it listed
        total = max(len(this_run), (data.get("videos_total") or 0) - (len(videos) - len(this_run)))
        recent_count = len(recent)
    total = total if this_run else 0
    pulled = [c for c in data.get("creators", []) if c.get("pulled")]
    scored = [v for v in recent if v.get("outlier") is not None]
    days = data.get("window_days", common.WINDOW_DAYS)
    if total:
        window = f"{recent_count} of them from the last {days} days" if recent_count else f"none of them from the last {days} days"
        lede = f"{common.plural(total, 'video')} from {_plural_creators(len(pulled))} this time, {window}."
        if scored:
            top = max(scored, key=lambda v: v["outlier"])
            lede += f" The strongest of those ran {top['outlier']:.1f} times that creator's normal."
            if top.get("low_sample"):
                lede += " That creator has few videos on record, so treat the number as rough."
    else:
        lede = "No videos came back this time."
    posts = data.get("x_posts", [])
    read = sum(1 for post in posts if post.get("status") == "read")
    stats = [
        ("Videos", str(total)),
        ("Creators", str(len(pulled))),
        ("X posts", f"{read} of {len(posts)}" if posts else "0"),
        ("Ideas", str(len(p.ideas)) if p.ideas else "0"),
    ]
    banner = f'<p class="banner">{E(OFFLINE_NOTICE)}</p>' if data.get("mode") == "offline" else ""
    generated = data.get("generated_at")
    cells = "".join(f"<div><dt>{E(label)}</dt><dd class=\"num\">{E(value)}</dd></div>" for label, value in stats)
    return (
        '<header class="hero"><div class="wrap">'
        f'<p class="kicker">{E(common.fmt_long_day(generated, p.tz))}</p>'
        f"<h1>Research for {E(p.profile.name)}</h1>"
        f'<p class="lede">{E(lede)}</p>{banner}'
        f'<dl class="stats">{cells}</dl>'
        "</div></header>"
    )


def _source_rows(p: Page) -> list[tuple[str, str, str, str]]:
    """(name, chip class, chip text, detail) for each line of "How this run went"."""
    data = p.data
    offline = data.get("mode") == "offline"
    rows = []
    for platform in common.RESEARCH_PLATFORMS:
        source = (data.get("sources") or {}).get(platform) or {"status": "skipped"}
        status = source.get("status")
        if status == "ok":
            listed = source.get("creators", [])
            made = [c for c in listed if c.get("videos")]
            detail = f"{common.plural(source.get('videos', 0), 'video')} from {common.creators_phrase(len(made), len(listed))}"
            if source.get("tool"):
                detail += f", {source['tool']}"
            if isinstance(source.get("cost_usd"), (int, float)):
                detail += f", US${source['cost_usd']:.2f}"
            if source.get("stopped_by"):
                detail += ", stopped at its spending limit"
            if source.get("note"):
                detail += f", {source['note']}"
            rows.append((common.PLATFORM_LABEL[platform], "on", "Sample" if offline else "Live", detail))
        else:
            rows.append((common.PLATFORM_LABEL[platform], "quiet", "Failed" if status == "failed" else "Skipped", ""))

    posts = data.get("x_posts", [])
    if posts:
        read = sum(1 for post in posts if post.get("status") == "read")
        rows.append(("X posts", "on" if read else "quiet", "Live" if read else "Unread", f"{read} of {len(posts)} read"))
    else:
        rows.append(("X posts", "quiet", "None", "no links in me/ideas.md"))
    notes = data.get("notes", [])
    rows.append(("Your notes", "on" if notes else "quiet", "Read" if notes else "None", f"{common.plural(len(notes), 'note')} in me/ideas.md"))

    counts = data.get("transcripts") or {}
    parts = [
        f"{counts[key]} {label}"
        for key, label in (("brain", "from earlier runs"), ("captions", "from captions"), ("local", "transcribed here"), ("missing", "without one"))
        if counts.get(key)
    ]
    have = any(counts.get(k) for k in ("brain", "captions", "local"))
    rows.append(("Transcripts", "on" if have else "quiet", "Done" if have else "None", ", ".join(parts)))

    perf = data.get("performance") or {}
    if perf.get("status") == "ok":
        rows.append(("Your results", "on", "Live", common.plural(perf.get("feed_reels", 0), "feed Reel")))
    else:
        rows.append(("Your results", "quiet", "Skipped", ""))
    return rows


def _notices(p: Page) -> str:
    """Sentences about anything skipped or stopped, shown first so the creator sees them."""
    notices = [n for n in p.data.get("notices", []) if n != OFFLINE_NOTICE]
    if not notices:
        return ""
    return f'<div class="wrap"><ul class="notices">{"".join(f"<li>{E(n)}</li>" for n in notices)}</ul></div>'


def _run_section(p: Page) -> str:
    rows = "".join(
        f'<li><span class="name">{E(name)}</span><span class="chip {cls}">{E(chip)}</span><span class="detail">{E(detail)}</span></li>'
        for name, cls, chip, detail in _source_rows(p)
    )
    return (
        '<section id="run"><div class="wrap"><h2>How this run went</h2>'
        f'<div class="section-body"><ul class="sources">{rows}</ul></div></div></section>'
    )


def _ideas_section(p: Page) -> str:
    head = '<section id="ideas"><div class="wrap"><h2>Ideas to film</h2>'
    if p.problems:
        items = "".join(f"<li>{E(problem)}</li>" for problem in p.problems)
        return (
            head + '<p class="dek">The ideas are written, but the file has mistakes the kit cannot read past.</p>'
            f'<div class="problem"><h3>ideas.json needs fixing</h3><ul>{items}</ul>'
            '<p class="note">Ask Claude to fix it, then run: uv run python -m kit.research report</p></div></div></section>'
        )
    if not p.ideas:
        return (
            head + '<p class="dek">No ideas are written for this run yet. Ask Claude to write them from items.json, '
            "or say: run my research.</p></div></section>"
        )
    cards = []
    for idea in sorted(p.ideas, key=lambda i: i["n"]):
        evidence = []
        for entry in idea["evidence"]:
            known = p.by_url.get(common.norm_url(entry["url"]), {})
            on = f" on {common.PLATFORM_LABEL[known['platform']]}" if known.get("platform") in common.PLATFORM_LABEL else (" on X" if known.get("handle") else "")
            score = _score(entry["outlier"]) if entry["outlier"] > 0 else "not scored"
            link = _link(entry["url"], "Open on X" if known.get("handle") else "Watch")
            evidence.append(
                f'<li><span class="who">@{E(entry["creator"])}{E(on)}</span><span class="x num">{E(score)}</span>{link}</li>'
            )
        evidence_html = f'<div class="evidence"><h4>Evidence</h4><ul>{"".join(evidence)}</ul></div>' if evidence else ""
        hooks_html = (
            '<div class="hooklist"><h4>Hooks</h4><ol>' + "".join(f"<li><span>{E(hook)}</span></li>" for hook in idea["hooks"]) + "</ol></div>"
            if idea["hooks"]
            else ""
        )
        cards.append(
            f'<li class="idea" id="idea-{idea["n"]}"><div class="rank num" aria-hidden="true">{idea["n"]}</div><div>'
            f'<p class="chips"><span class="chip">{E(idea["format"])}</span></p>'
            f'<h3>{E(idea["title"])}</h3>'
            f'<div class="fields"><div><h4>Angle</h4><p>{E(idea["angle"])}</p></div><div><h4>Why</h4><p>{E(idea["why"])}</p></div></div>'
            f"{evidence_html}{hooks_html}</div></li>"
        )
    return (
        head + '<p class="dek">Ranked best first. Say "give me hooks for number 2" to get more hooks for any of them.</p>'
        f'<div class="section-body"><ol class="ideas">{"".join(cards)}</ol></div></div></section>'
    )


def _video_row(p: Page, video: dict) -> str:
    chips = []
    if video.get("low_sample"):
        chips.append('<span class="chip quiet">Low sample</span>')
    if video.get("young"):
        chips.append('<span class="chip quiet">Still climbing</span>')
    after = ""
    if video.get("hook_source") == "transcript" and video.get("transcript"):
        rest = common.split_opening(video["transcript"])[1]
        after = f'<p class="then">Then: {E(common.words(rest, 30))}</p>' if rest else ""
    else:
        after = '<p class="note">From the caption. There is no transcript for this one.</p>'
    meta = [
        f'<span class="who">{E(common.PLATFORM_LABEL.get(video["platform"], video["platform"]))} @{E(video["creator"])}</span>',
        f"<span>{E(common.fmt_day(video.get('posted_at'), p.tz))}</span>",
        f"<span class=\"num\">{E(common.short_num(video.get('views')))} views</span>",
    ]
    if video.get("duration"):
        meta.append(f'<span class="num">{round(float(video["duration"]))}s</span>')
    watch = _link(video.get("url"), "Watch")
    side = f'<div class="side"><div class="chips">{"".join(chips)}</div>{watch}</div>'
    return (
        f'<li><div class="{_strong(video.get("outlier"))} num">{E(_score(video.get("outlier")))}</div><div>'
        f'<p class="hook"><q>{E(video.get("hook") or video.get("title") or "")}</q></p>{after}'
        f'<p class="meta">{"".join(meta)}</p></div>{side}</li>'
    )


def _videos_section(p: Page) -> str:
    recent = [v for v in p.data.get("videos", []) if v.get("set") == "recent"][:MAX_LISTED]
    if not recent:
        return ""
    days = p.data.get("window_days", common.WINDOW_DAYS)
    rows = "".join(_video_row(p, v) for v in recent)
    return (
        '<section id="videos"><div class="wrap"><h2>What is working</h2>'
        f'<p class="dek">Videos from the last {days} days, each scored against that creator\'s own normal. '
        f"A score of 1{TIMES} is an ordinary video for them.</p>"
        f'<div class="section-body"><ol class="videos">{rows}</ol></div></div></section>'
    )


def _earlier_section(p: Page) -> str:
    older = [v for v in p.data.get("videos", []) if v.get("set") == "earlier" and (v.get("outlier") or 0) >= EARLIER_MIN_OUTLIER]
    older = older[:EARLIER_LIMIT]
    if not older:
        return ""
    rows = "".join(_video_row(p, v) for v in older)
    return (
        '<section id="earlier"><div class="wrap"><h2>Earlier standouts</h2>'
        '<p class="dek">Older videos that ran well above normal, from this pull and from earlier runs.</p>'
        f'<div class="section-body"><ol class="videos compact">{rows}</ol></div></div></section>'
    )


def _creators_section(p: Page) -> str:
    creators = sorted(p.data.get("creators", []), key=lambda c: (c.get("best_outlier") is None, -(c.get("best_outlier") or 0)))
    if not creators:
        return ""
    low = '<span class="chip quiet">Low sample</span>'
    rows = "".join(
        "<tr>"
        f'<th scope="row">@{E(c["creator"])} <span class="muted">{E(common.PLATFORM_LABEL.get(c["platform"], c["platform"]))}</span></th>'
        f'<td class="num">{c.get("on_record", 0)}</td><td class="num">{c.get("in_window", 0)}</td>'
        f'<td class="num">{E(common.short_num(c.get("median_engagement")))}</td>'
        f'<td class="num">{E(_score(c.get("best_outlier")))}</td>'
        f"<td>{low if c.get('low_sample') else ''}</td></tr>"
        for c in creators
    )
    return (
        '<section id="creators"><div class="wrap"><h2>Creators</h2>'
        '<p class="dek">Each creator\'s normal is the middle value of 3 x comments + likes across their videos on record.</p>'
        '<div class="section-body table-wrap"><table><thead><tr><th>Creator</th><th class="num">On record</th><th class="num">Recent</th>'
        '<th class="num">Normal</th><th class="num">Best score</th><th></th></tr></thead>'
        f"<tbody>{rows}</tbody></table></div></div></section>"
    )


def _shared_section(p: Page) -> str:
    posts, notes = p.data.get("x_posts", []), p.data.get("notes", [])
    used = {common.norm_url(e["url"]) for idea in (p.ideas or []) for e in idea["evidence"]}
    read = [post for post in posts if post.get("status") == "read"]
    unread = [post for post in posts if post.get("status") != "read"]

    cards = []
    for post in read:
        numbers = "".join(
            f'<span class="num">{E(common.short_num(post[key]))} {label}</span>'
            for key, label in (("views", "views"), ("likes", "likes"), ("reposts", "reposts"), ("replies", "replies"))
            if post.get(key) is not None
        )
        when = common.fmt_day(post.get("created_at"), p.tz)
        chip = '<span class="chip on">Used in an idea</span>' if common.norm_url(post["url"]) in used else ""
        cards.append(
            f'<li class="post"><p><span class="by">{E(post.get("author_name") or "")}</span> '
            f'<span class="muted">@{E(post.get("handle") or "")}{" · " + E(when) if when else ""}</span> {chip}</p>'
            f'<p class="text">{E(post.get("text") or "")}</p><p class="numbers">{numbers}</p>'
            f'<p class="numbers">{_link(post["url"], "Open on X")}</p></li>'
        )
    parts = [f'<ul class="posts">{"".join(cards)}</ul>'] if cards else []

    if unread:
        lines = "".join(
            f'<li><span class="chip quiet">Unread</span>{_link(post["url"], post["url"]) or E(post["url"])}'
            f'<span class="muted">{E(post.get("reason") or "")}</span></li>'
            for post in unread
        )
        parts.append(
            '<h3 class="sub">Posts I could not read</h3><p class="dek">The services that read X posts are unofficial and can stop working. '
            f'Open these by hand if they matter.</p><ul class="unread">{lines}</ul>'
        )
    if notes:
        items = "".join(
            f'<li>{E(note["text"])}'
            + "".join(f' {_link(url, urlparse(url).netloc.removeprefix("www."))}' for url in note.get("links", []) if _href(url))
            + "</li>"
            for note in notes
        )
        parts.append(f'<h3 class="sub">Your notes</h3><ul class="notes">{items}</ul>')
    if not parts:
        parts.append('<p class="dek">Nothing here this time. Add ideas and links to me/ideas.md.</p>')
    return (
        '<section id="shared"><div class="wrap"><h2>Links and notes you shared</h2>'
        f'<div class="section-body">{"".join(parts)}</div></div></section>'
    )


def _you_section(p: Page) -> str:
    perf = p.data.get("performance") or {}
    head = '<section id="you"><div class="wrap"><h2>Your own results</h2>'
    if perf.get("status") != "ok":
        return head + f'<p class="dek">{E(perf.get("message") or "Performance skipped")}</p></div></section>'
    if not perf.get("top"):
        return head + f'<p class="dek">{E(perf.get("message") or "No Reels to show.")}</p></div></section>'
    followers = f" {E(common.short_num(perf['followers']))} followers." if perf.get("followers") else ""
    dek = (
        f"Your feed Reels from {E(common.fmt_day(perf['window_from'], p.tz))} to {E(common.fmt_day(perf['window_to'], p.tz))}, "
        f"scored against your own median. Median {E(common.short_num(perf.get('median_views')))} views.{followers}"
    )
    cards = []
    for reel in perf["top"]:
        facts = [f"{common.short_num(reel.get('views'))} views", f"{common.short_num(reel.get('saves'))} saves"]
        if reel.get("skip_rate") is not None:
            facts.append(f"{reel['skip_rate']:.0f}% skipped")
        cards.append(
            f'<li><div class="{_strong(reel.get("multiple"))} num">{E(_score(reel.get("multiple")))}</div><p class="title">{E(reel.get("title") or "")}</p>'
            f'<p class="note">{E(" · ".join(facts))}</p><p class="note">{_link(reel.get("url"), "Open")}</p></li>'
        )
    patterns = ""
    if perf.get("patterns"):
        patterns = '<h3 class="sub">What the numbers say</h3><ol class="patterns">' + "".join(
            f"<li><span>{E(item['text'])}</span></li>" for item in perf["patterns"]
        ) + "</ol>"
    return head + f'<p class="dek">{dek}</p><div class="section-body"><ul class="mine">{"".join(cards)}</ul>{patterns}</div></div></section>'


def _footer(p: Page) -> str:
    built = common.fmt_stamp(p.data.get("generated_at"), p.tz)
    zone = getattr(p.tz, "key", None) or str(p.tz)
    return (
        '<footer><div class="wrap">'
        "<p>How the score works. A video's score is (3 x comments + likes) divided by that creator's median, the middle value across "
        "their videos on record. 1x is an ordinary video for them. Creators with fewer than 3 videos on record are marked Low sample, "
        "so treat their scores as rough. Videos under two days old are marked Still climbing, because their numbers are still growing.</p>"
        f"<p>Built {E(built)}. Times are shown in {E(zone)}.</p>"
        "</div></footer>"
    )


# --- the page ---------------------------------------------------------------------------------------------------


def render(profile, data: dict, ideas: list | None, problems: list, run_dir) -> str:
    """The whole page as text."""
    tz = common.tz_of(profile)
    by_url = {common.norm_url(v.get("url")): v for v in data.get("videos", [])}
    by_url.update({common.norm_url(post.get("url")): post for post in data.get("x_posts", [])})
    page = Page(profile, data, ideas, problems, tz, by_url)

    body = "".join(
        [
            _hero(page), "<main>", _notices(page), _ideas_section(page), _videos_section(page), _earlier_section(page),
            _creators_section(page), _shared_section(page), _you_section(page), _run_section(page), "</main>", _footer(page),
        ]
    )  # fmt: skip
    recent = [v for v in data.get("videos", []) if v.get("set") == "recent"]
    names = {(v["platform"], str(v["creator"]).lower()) for v in recent}
    title = f"{profile.name} research, {common.fmt_date(data.get('generated_at'), tz)}"
    description = f"{common.plural(len(recent), 'video')} from {_plural_creators(len(names))}, each scored against that creator's own normal."
    template = TEMPLATE.read_text(encoding="utf-8")
    return (
        template.replace("{{TITLE}}", E(title))
        .replace("{{DESCRIPTION}}", E(description))
        .replace("{{TOKENS}}", brand.tokens_css(profile, Path(run_dir)))
        .replace("{{BODY}}", body)
    )


def write(profile, run_dir) -> tuple[Path, list[str]]:
    """Build report.html in a run folder from its items.json and ideas.json. Returns the path and any problems with ideas.json."""
    run_dir = Path(run_dir)
    data = common.read_json(run_dir / "items.json")
    if not isinstance(data, dict):
        raise ValueError(f"There is no items.json in {common.kit_relative(run_dir)}. Run: uv run python -m kit.research run")
    ideas, problems = ideas_module.read(run_dir)
    page = render(profile, data, ideas, problems, run_dir)
    path = run_dir / "report.html"
    temp = path.with_name(path.name + ".tmp")
    try:
        temp.write_text(page, encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return path, problems
