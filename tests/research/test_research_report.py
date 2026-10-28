"""kit.research.report: one HTML page in the creator's own brand."""

import dataclasses
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote
from zoneinfo import available_timezones

import pytest

from kit import brand, config
from kit.research import ideas as ideas_module
from kit.research import report
from research_helpers import visible, write_profile

EM_DASH, EN_DASH = chr(0x2014), chr(0x2013)  # written as code points so this file holds no dash
HEX = re.compile(r"(?<![\w&#])#([0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})(?![\w-])")
TOKENS_BLOCK = re.compile(r'<style id="brand-tokens">(.*?)</style>', re.S)


def is_grey(digits: str) -> bool:
    width = 1 if len(digits) in (3, 4) else 2
    return len({digits[i : i + width].lower() for i in range(0, 3 * width, width)}) == 1


def chromatic(text: str) -> list[str]:
    return ["#" + d for d in HEX.findall(text) if not is_grey(d)]


def load(run_dir: Path) -> dict:
    return json.loads((run_dir / "items.json").read_text(encoding="utf-8"))


def save(run_dir: Path, data: dict) -> None:
    (run_dir / "items.json").write_text(json.dumps(data), encoding="utf-8")


def rebuild(profile, run_dir: Path) -> str:
    path, _ = report.write(profile, run_dir)
    return path.read_text(encoding="utf-8")


def write_ideas(run_dir: Path, ideas: list[dict]) -> None:
    (run_dir / "ideas.json").write_text(json.dumps(ideas), encoding="utf-8")


def an_idea(n, title, url, *, outlier=5.5, hooks=("First hook line.", "Second hook line.")):
    return {
        "n": n, "title": title, "angle": f"Angle for {title}", "why": f"Why {title} matters now", "format": "Tutorial",
        "evidence": [{"url": url, "creator": "example_gardener_one", "outlier": outlier}], "hooks": list(hooks),
    }


# --- the brand ------------------------------------------------------------------------------------


def test_report_uses_tokens(offline_run):
    profile, run_dir = offline_run
    page = (run_dir / "report.html").read_text(encoding="utf-8")
    assert "var(--accent)" in page

    block = TOKENS_BLOCK.search(page)
    assert block, "the creator's tokens are inlined in a style block of their own"
    tokens = block.group(1)
    rest = page.replace(block.group(0), "")
    assert chromatic(rest) == [], "outside the tokens, the page may only use var(--...) and greys"
    # the tokens block holds the creator's colours, and the shades the kit works out from them, and nothing else
    own = {c.lower() for c in dataclasses.astuple(profile.colours)}
    derived = {c.lower() for c in brand.colour_tokens(profile).values()}
    assert {c.lower() for c in chromatic(tokens)} <= own | derived
    for colour in own:
        assert colour in tokens.lower()
    for name in ("canvas", "ink", "accent", "on-accent", "field-dark", "field-light", "font-display", "font-body"):
        assert f"--{name}:" in tokens


def test_font_files_are_found_from_the_run_folder(offline_run):
    _, run_dir = offline_run
    tokens = TOKENS_BLOCK.search((run_dir / "report.html").read_text(encoding="utf-8")).group(1)
    urls = re.findall(r'url\("([^"]+)"\)', tokens)
    assert urls
    for url in urls:
        assert not url.startswith(("http", "/", "file:"))
        assert (run_dir / unquote(url)).resolve().is_file()


def test_the_template_holds_no_brand_colour():
    template = (Path(report.__file__).with_name("report_template.html")).read_text(encoding="utf-8")
    assert chromatic(template) == []
    assert "var(--accent)" in template and "var(--canvas)" in template and "var(--ink)" in template


def test_the_research_code_names_no_fixed_time_zone():
    areas = sorted({name.split("/")[0] for name in available_timezones() if "/" in name})
    assert areas, "this machine has no time zone database to check against"
    pattern = re.compile(r"""["'](?:""" + "|".join(areas) + r""")/\w+""")
    folder = Path(report.__file__).parent
    for path in list(folder.glob("*.py")) + list(folder.glob("*.html")) + list(folder.glob("*.json")):
        assert not pattern.search(path.read_text(encoding="utf-8")), path.name


# --- the page itself ------------------------------------------------------------------------------


def test_the_report_is_one_file_and_asks_for_nothing_from_the_web(offline_run):
    _, run_dir = offline_run
    page = (run_dir / "report.html").read_text(encoding="utf-8")
    for forbidden in ("<script", "<img", "<iframe", "<link", "@import", "<video", "<audio"):
        assert forbidden not in page, forbidden
    body_urls = re.findall(r'(?:src|action)="([^"]*)"', page)
    assert body_urls == []
    block = TOKENS_BLOCK.search(page).group(0)
    outside = page.replace(block, "")
    assert "url(" not in outside


class Balanced(HTMLParser):
    VOID = {"meta", "br", "hr", "input", "col", "wbr"}

    def __init__(self):
        super().__init__()
        self.stack, self.problems = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.problems.append(f"</{tag}> closes {self.stack[-1:] or 'nothing'}")
        else:
            self.stack.pop()


def test_the_report_is_well_formed(offline_run):
    _, run_dir = offline_run
    page = (run_dir / "report.html").read_text(encoding="utf-8")
    parser = Balanced()
    parser.feed(page)
    parser.close()
    assert parser.problems == [] and parser.stack == []
    assert page.lstrip().lower().startswith("<!doctype html>")


def test_the_page_has_a_title_a_language_and_a_phone_viewport(offline_run):
    _, run_dir = offline_run
    page = (run_dir / "report.html").read_text(encoding="utf-8")
    assert re.search(r"<html[^>]*\blang=\"en\"", page)
    assert '<meta name="viewport" content="width=device-width, initial-scale=1' in page
    assert "<title>Example Studio research, 28 Oct 2026</title>" in page
    assert "@media (max-width" in page
    assert page.count("<h1") == 1


def test_the_report_names_the_creator_and_the_day(offline_run):
    _, run_dir = offline_run
    text = visible((run_dir / "report.html").read_text(encoding="utf-8"))
    assert "Research for Example Studio" in text
    assert "Wednesday 28 October 2026" in text
    for heading in ("How this run went", "Ideas to film", "What is working", "Creators"):
        assert heading in text


def test_the_counts_at_the_top_match_what_the_run_printed(offline_run):
    """The big numbers are the whole pull, the same ones the terminal printed. The sentence above them says how
    many of those are from the last two weeks, so the page and the terminal never disagree."""
    from kit.research import pipeline

    profile, run_dir = offline_run
    data = load(run_dir)
    recent = [v for v in data["videos"] if v.get("set") == "recent"]
    assert 0 < len(recent) < data["videos_total"], "the sample has videos both inside and outside the window"
    printed: list[str] = []
    pipeline._summarise(data, printed.append)
    page = rebuild(profile, run_dir)
    tiles = dict(re.findall(r"<dt>([^<]+)</dt><dd class=\"num\">([^<]+)</dd>", page))
    per_platform = [re.match(r"\w+: (\d+) videos? from (\d+) creators?", line) for line in printed]
    videos_printed = sum(int(m.group(1)) for m in per_platform if m)
    creators_printed = sum(int(m.group(2)) for m in per_platform if m)
    assert (tiles["Videos"], tiles["Creators"]) == (str(videos_printed), str(creators_printed)), (tiles, printed)
    lede = visible(re.search(r'<p class="lede">(.*?)</p>', page, re.S).group(1))
    assert f"{data['videos_total']} videos" in lede and f"{len(recent)} of them from the last" in lede, lede


def test_the_hidden_description_counts_this_run_as_the_tiles_do(offline_run):
    """A standout from an earlier run is not part of this pull, even when it is recent, so the page's description
    counts the same videos and creators as the big numbers."""
    profile, run_dir = offline_run
    data = load(run_dir)
    standout = dict(data["videos"][0], id="ig:from-an-earlier-run", creator="example_earlier_creator",
                    url="https://example.com/earlier/1", source="brain", set="recent", outlier=4.0)
    data["videos"].append(standout)
    save(run_dir, data)

    page = rebuild(profile, run_dir)
    tiles = dict(re.findall(r"<dt>([^<]+)</dt><dd class=\"num\">([^<]+)</dd>", page))
    description = re.search(r'<meta name="description" content="([^"]*)">', page).group(1)
    assert description.startswith(f"{tiles['Videos']} videos from {tiles['Creators']} creators,"), (description, tiles)


def test_the_offline_banner_is_shown(offline_run):
    _, run_dir = offline_run
    text = visible((run_dir / "report.html").read_text(encoding="utf-8"))
    assert "Offline run: these videos are made-up samples, not your watch list" in text


def test_the_report_text_carries_no_dashes(offline_run):
    _, run_dir = offline_run
    page = (run_dir / "report.html").read_text(encoding="utf-8")
    assert EM_DASH not in page and EN_DASH not in page


# --- what comes from the web is never trusted -------------------------------------------------------


def test_everything_from_the_web_is_escaped(offline_run):
    profile, run_dir = offline_run
    data = load(run_dir)
    video = data["videos"][0]
    video.update(
        title='<script>alert("t")</script>',
        hook='"><img src=x onerror=alert(1)>',
        creator='x"onmouseover="alert(2)',
        transcript="<b>bold</b> words in the transcript that go on for a good while longer than that",
    )
    data["x_posts"] = [{
        "url": "https://x.com/someone/status/1", "status": "read", "handle": "<i>handle</i>", "author_name": "<u>Name</u>",
        "text": "<script>steal()</script> Text of the post", "views": 10, "likes": 2, "reposts": 1, "replies": 0,
    }]
    data["notes"] = [{"n": 1, "text": "<img src=y onerror=alert(5)>", "links": [], "x_urls": []}]
    data["notices"] = ["<script>alert(6)</script>"]
    save(run_dir, data)
    page = rebuild(profile, run_dir)
    assert "<script" not in page and "<img" not in page and "<b>bold" not in page and "<u>" not in page and "<i>handle" not in page
    assert '"onmouseover="' not in page
    assert "&lt;script&gt;" in page


def test_only_web_addresses_become_links(offline_run):
    profile, run_dir = offline_run
    data = load(run_dir)
    data["videos"][0]["url"] = "javascript:alert(3)"
    data["videos"][1]["url"] = "data:text/html,<p>x</p>"
    data["x_posts"] = [{"url": "javascript:alert(4)", "status": "unread", "reason": "HTTP 404"}]
    save(run_dir, data)
    page = rebuild(profile, run_dir)
    assert not re.search(r'href="(?:javascript|data):', page, re.I)
    hrefs = re.findall(r'href="([^"]*)"', page)
    assert hrefs and all(h.startswith(("https://", "http://", "#")) for h in hrefs)


def test_links_that_leave_the_page_open_safely(offline_run):
    _, run_dir = offline_run
    page = (run_dir / "report.html").read_text(encoding="utf-8")
    for tag in re.findall(r'<a [^>]*href="https?://[^>]*>', page):
        assert 'rel="noopener noreferrer"' in tag and 'target="_blank"' in tag


# --- the videos -----------------------------------------------------------------------------------


def test_the_report_lists_videos_best_first_with_their_scores(offline_run):
    _, run_dir = offline_run
    text = visible((run_dir / "report.html").read_text(encoding="utf-8"))
    top = text.index("6.3×")  # the sample's TikTok standout, 11100 over a median of 1770
    second = text.index("5.5×")  # the Instagram standout, 6430 over 1173
    assert top < second
    assert "Your basil is not dying because of the sun." in text  # the hook is the first spoken line
    assert "Then:" in text


def test_a_video_with_no_transcript_shows_its_caption_and_says_so(offline_run):
    _, run_dir = offline_run
    data = load(run_dir)
    quiet = next(v for v in data["videos"] if not v.get("transcript") and v["set"] == "recent")
    text = visible((run_dir / "report.html").read_text(encoding="utf-8"))
    assert quiet["hook"] in text
    assert "From the caption" in text


def test_low_sample_creators_are_marked(offline_run):
    _, run_dir = offline_run
    text = visible((run_dir / "report.html").read_text(encoding="utf-8"))
    assert text.count("Low sample") >= 2  # on the creator's video and in the creators table
    assert "fewer than 3 videos" in text  # and the method note says what it means


def test_a_fresh_video_is_marked_as_still_climbing(offline_run):
    profile, run_dir = offline_run
    data = load(run_dir)
    data["videos"][0]["young"] = True
    save(run_dir, data)
    assert "Still climbing" in visible(rebuild(profile, run_dir))


def test_numbers_are_shortened_for_people(offline_run):
    profile, run_dir = offline_run
    data = load(run_dir)
    data["videos"][0]["views"] = 1_250_000
    data["videos"][1]["views"] = 12_345
    save(run_dir, data)
    text = visible(rebuild(profile, run_dir))
    assert "views" in text and "12k" in text


def test_earlier_standouts_have_their_own_section(offline_run):
    _, run_dir = offline_run
    text = visible((run_dir / "report.html").read_text(encoding="utf-8"))
    assert "Earlier standouts" in text


# --- the ideas ------------------------------------------------------------------------------------


def test_ideas_appear_in_order_with_evidence_and_hooks(offline_run):
    profile, run_dir = offline_run
    top = load(run_dir)["videos"][0]
    write_ideas(run_dir, [
        an_idea(1, "Idea about soil", top["url"], outlier=6.3),
        an_idea(2, "Idea about basil", "https://example.com/not-in-the-run", outlier=0, hooks=("Only hook.",)),
    ])
    page = rebuild(profile, run_dir)
    text = visible(page)
    assert text.index("Idea about soil") < text.index("Idea about basil")
    assert "Angle for Idea about soil" in text and "Why Idea about soil matters now" in text
    assert "First hook line." in text and "Second hook line." in text and "Only hook." in text
    assert "6.3×" in text
    assert "not scored" in text  # evidence with an outlier of 0 has no score to show
    assert f'href="{top["url"]}"' in page
    assert "give me hooks for number 2" in text


def test_a_report_with_no_ideas_says_how_to_get_them(offline_run):
    _, run_dir = offline_run
    text = visible((run_dir / "report.html").read_text(encoding="utf-8"))
    assert "No ideas are written for this run yet" in text
    assert "run my research" in text


def test_a_broken_ideas_file_is_shown_not_hidden(offline_run):
    profile, run_dir = offline_run
    write_ideas(run_dir, [{"n": 1, "title": "<b>Half an idea</b>"}])
    path, problems = report.write(profile, run_dir)
    assert problems and any("missing" in p for p in problems)
    page = path.read_text(encoding="utf-8")
    text = visible(page)
    assert "ideas.json needs fixing" in text and "Idea 1 is missing angle" in text
    assert "<b>Half" not in page


def test_ideas_written_for_a_run_are_checked_against_the_schema(offline_run):
    profile, run_dir = offline_run
    top = load(run_dir)["videos"][0]
    write_ideas(run_dir, [an_idea(1, "Fine", top["url"])])
    _, problems = report.write(profile, run_dir)
    assert problems == [] and ideas_module.validate(json.loads((run_dir / "ideas.json").read_text())) == []


# --- sources: links, notes and the creator's own results ---------------------------------------------


def test_unread_x_links_are_listed_with_the_reason(offline_run):
    profile, run_dir = offline_run
    data = load(run_dir)
    data["x_posts"] = [
        {"url": "https://x.com/example_account/status/1000000000000000001", "status": "unread", "reason": "HTTP 503",
         "handle": "example_account"},
    ]
    save(run_dir, data)
    page = rebuild(profile, run_dir)
    text = visible(page)
    assert "Unread" in text and "HTTP 503" in text
    assert 'href="https://x.com/example_account/status/1000000000000000001"' in page


def test_a_read_x_post_shows_its_text_and_numbers(offline_run):
    profile, run_dir = offline_run
    data = load(run_dir)
    data["x_posts"] = [{
        "url": "https://x.com/example_account/status/9", "status": "read", "handle": "example_account", "author_name": "Example Account",
        "text": "Nobody tells you this about basil.", "views": 120000, "likes": 3400, "reposts": 500, "replies": 210,
        "created_at": "2026-10-27T10:00:00+00:00", "via": "fxtwitter",
    }]
    save(run_dir, data)
    text = visible(rebuild(profile, run_dir))
    assert "Nobody tells you this about basil." in text and "120k views" in text and "@example_account" in text


def test_notes_from_the_ideas_file_are_listed(offline_run):
    _, run_dir = offline_run
    text = visible((run_dir / "report.html").read_text(encoding="utf-8"))
    assert "Why windowsill basil dies in week three" in text  # the first note in the example ideas file


def test_the_performance_section_says_in_one_sentence_when_it_was_skipped(offline_run):
    _, run_dir = offline_run
    text = visible((run_dir / "report.html").read_text(encoding="utf-8"))
    assert "Your own results" in text
    assert "Performance skipped: this is an offline run" in text


def test_the_performance_section_shows_the_creators_own_reels(offline_run):
    profile, run_dir = offline_run
    data = load(run_dir)
    data["performance"] = {
        "status": "ok", "handle": "examplestudio", "followers": 1200, "window_from": "2026-09-16", "window_to": "2026-10-28",
        "median_engagement": 953, "median_views": 11500, "feed_reels": 8, "trial_reels": 1, "young": [],
        "top": [{"id": "me:A3", "url": "https://www.instagram.com/reel/A3/", "title": "Reel A3 about basil", "posted_at": "2026-10-21T10:00:00Z",
                 "views": 22000, "saves": 700, "skip_rate": 38.0, "multiple": 2.16, "views_multiple": 1.9}],
        "patterns": [{"kind": "keyword", "ratio": 13.5, "text": "A comment keyword pays for you."}],
    }
    save(run_dir, data)
    page = rebuild(profile, run_dir)
    text = visible(page)
    assert "Reel A3 about basil" in text and "2.2×" in text and "22k views" in text and "700 saves" in text
    assert "A comment keyword pays for you." in text
    assert "1.2k followers" in text or "1,200 followers" in text
    assert 'href="https://www.instagram.com/reel/A3/"' in page


def test_the_notices_are_in_the_report_before_anything_else(offline_run):
    profile, run_dir = offline_run
    data = load(run_dir)
    notice = "Instagram and TikTok skipped: add APIFY_TOKEN to .env to include them"
    data["notices"] = [notice]
    save(run_dir, data)
    text = visible(rebuild(profile, run_dir))
    assert text.count(notice) == 1  # told once, not again further down
    assert text.index(notice) < text.index("Ideas to film")


def test_only_strong_scores_get_the_accent(offline_run):
    _, run_dir = offline_run
    page = (run_dir / "report.html").read_text(encoding="utf-8")
    strong = re.findall(r'<div class="score strong num">([\d.]+)', page)
    plain = re.findall(r'<div class="score num">([\d.]+)', page)
    assert strong and plain
    assert all(float(v) >= 2 for v in strong) and all(float(v) < 2 for v in plain)


def test_a_run_with_no_transcripts_does_not_say_done(offline_run):
    _, run_dir = offline_run
    text = visible((run_dir / "report.html").read_text(encoding="utf-8"))
    assert "Transcripts None 8 without one" in text


# --- time zones and odd runs ------------------------------------------------------------------------


def test_dates_follow_the_zone_in_the_profile(offline_run):
    profile, run_dir = offline_run
    auckland = visible(rebuild(dataclasses.replace(profile, timezone="Pacific/Auckland"), run_dir))
    los_angeles = visible(rebuild(dataclasses.replace(profile, timezone="America/Los_Angeles"), run_dir))
    # the run happened at 13:30 UTC on 28 October: already the 29th in Auckland, still the 28th in Los Angeles
    assert "Thursday 29 October 2026" in auckland and "Times are shown in Pacific/Auckland" in auckland
    assert "Wednesday 28 October 2026" in los_angeles and "Times are shown in America/Los_Angeles" in los_angeles
    assert "Thursday 29 October 2026" not in los_angeles


def test_the_page_title_follows_the_zone_too(offline_run):
    profile, run_dir = offline_run
    page = rebuild(dataclasses.replace(profile, timezone="Pacific/Auckland"), run_dir)
    assert "<title>Example Studio research, 29 Oct 2026</title>" in page


def test_a_report_for_a_run_with_nothing_in_it_still_builds(offline_run):
    profile, run_dir = offline_run
    data = load(run_dir)
    data.update(videos=[], creators=[], x_posts=[], notes=[], notices=["Your watch list is empty"], performance=None)
    save(run_dir, data)
    text = visible(rebuild(profile, run_dir))
    assert "No videos came back this time" in text and "Your watch list is empty" in text


def test_the_report_cannot_be_built_without_a_pull(tmp_path, example_me):
    with pytest.raises(ValueError) as caught:
        report.write(config.load_profile(), tmp_path)
    assert "items.json" in str(caught.value)


def test_a_different_brand_gives_a_different_page(offline_run, example_me):
    profile, run_dir = offline_run
    write_profile(example_me, colours={"canvas": "#fdf6e3", "ink": "#222222", "accent": "#8a2be2", "field_dark": "#1b1b3a", "field_light": "#efe8ff"})
    page = rebuild(config.load_profile(), run_dir)
    assert "#8a2be2" in TOKENS_BLOCK.search(page).group(1).lower()
    assert "#177245" not in page.lower()
