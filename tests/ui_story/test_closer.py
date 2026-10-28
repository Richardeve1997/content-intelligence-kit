"""The closer: the last beat of every UI story Reel, written from the creator's call to action.

A comment CTA types the creator's keyword into a comment box. A follow CTA shows their profile row and a
pressed Follow button. Nothing in the closer is fixed text: it all comes from profile.cta.
"""

import dataclasses
import re

from uistory_helpers import KIT, load

from kit import config

closer = load("closer")


def profile(**cta):
    base = config.load_profile()
    return dataclasses.replace(base, cta=dataclasses.replace(base.cta, **cta)) if cta else base


def test_closer_copy_from_profile(example_me):
    copy = closer.closer_copy(profile())
    assert copy["type"] == "comment"
    assert copy["keyword"] == "GROW"
    assert copy["typed"] == "GROW"
    assert "GROW" in copy["label"]
    assert "the balcony planting checklist" in copy["spoken"]
    assert "GROW" in copy["spoken"]

    other = closer.closer_copy(profile(keyword="BASIL", payload="the watering chart"))
    assert other["typed"] == "BASIL" and "BASIL" in other["label"] and "the watering chart" in other["spoken"]
    assert "GROW" not in str(other)


def test_a_follow_cta_shows_a_follow_and_no_keyword(example_me):
    copy = closer.closer_copy(profile(type="follow", keyword=None, payload=None, comment_platforms=()))
    assert copy["type"] == "follow"
    assert copy["button"] == "Follow"
    assert copy["handle"] == "@examplestudio"
    assert "keyword" not in copy or copy["keyword"] is None
    assert "GROW" not in str(copy)
    assert "follow" in copy["spoken"].lower()


def test_a_comment_cta_without_a_payload_still_reads_well(example_me):
    copy = closer.closer_copy(profile(payload=None))
    assert copy["spoken"].startswith("Comment GROW")
    assert "None" not in copy["spoken"]


def test_the_closer_scene_carries_the_copy_and_only_brand_tokens(example_me):
    html = closer.closer_html(profile())
    assert 'data-keyword="GROW"' in html
    assert "data-start" not in html, "HyperFrames would treat the scene as a timed clip"
    assert "UIS.type(tl, '#closer-typed', \"GROW\", 10.000 + 0.25)" in closer.closer_js(profile(), 10.0)
    assert "GROW" in html
    assert "var(--accent)" in html or 'class="ring' in html
    assert not re.search(r"#[0-9a-fA-F]{6}\b", html), "colours come from the tokens"
    follow = closer.closer_html(profile(type="follow", keyword=None, payload=None, comment_platforms=()))
    assert "Follow" in follow and "@examplestudio" in follow and "GROW" not in follow


def test_the_closer_command_prints_the_copy(example_me):
    from uistory_helpers import run_kit

    done = run_kit("closer.py", "--json")
    assert done.returncode == 0, done.stderr
    assert '"keyword": "GROW"' in done.stdout


def test_no_fixed_cta_wording_is_left_in_the_kit():
    for path in KIT.rglob("*"):
        if path.is_file() and path.suffix in {".py", ".js", ".css", ".html", ".sh"}:
            text = path.read_text(encoding="utf-8").upper()
            assert "COMMENT THIS WORD" not in text, path


def test_the_closer_stays_inside_the_safe_area(example_me):
    """Rings and chips sit inside x 90..990, glow included, so a tall phone's side crop never cuts them."""
    css = (KIT / "uistory.css").read_text()
    box = re.search(r"\.comment-box\{left:(\d+)px;width:(\d+)px\}", css)
    assert box and int(box.group(1)) >= 90 + 10 and int(box.group(1)) + int(box.group(2)) <= 990 - 10
    for markup in (closer.closer_html(profile()),
                   closer.closer_html(profile(type="follow", keyword=None, payload=None, comment_platforms=()))):
        for left, width in re.findall(r'style="left:(\d+)px;top:\d+px;width:(\d+)px', markup):
            assert int(left) >= 90 and int(left) + int(width) <= 990, (left, width)
