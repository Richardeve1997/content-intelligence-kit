"""research.scrape_seconds: how long one research run waits for the platforms, an optional profile setting.

It sits in the research group beside cost_cap. A profile that does not mention it gets two minutes, and a
value outside 10 to 1800 seconds is refused with a sentence that names the setting.
"""

import json
import time

import pytest

from kit import config
from kit.config import Profile, ProfileError
from kit.research import scrape

EM_DASH, EN_DASH = chr(0x2014), chr(0x2013)  # written as code points so this file holds no dash
HINT = "It should be a number of seconds from 10 to 1800: the longest one research run waits for Instagram, TikTok and YouTube."


def _read(me) -> dict:
    return json.loads((me / "profile.json").read_text(encoding="utf-8"))


def _edit(me, change) -> None:
    data = _read(me)
    change(data)
    (me / "profile.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


def _write_raw(me, literal: str) -> None:
    """Add a research group whose scrape_seconds is written exactly as `literal` (JSON text such as NaN)."""
    text = (me / "profile.json").read_text(encoding="utf-8")
    text = text.rstrip().removesuffix("}") + ', "research": {"scrape_seconds": ' + literal + "}}"
    (me / "profile.json").write_text(text, encoding="utf-8")


def _problems(me) -> list[str]:
    with pytest.raises(ProfileError) as caught:
        config.load_profile()
    return caught.value.problems


# --- the default ---------------------------------------------------------------------------------


def test_the_example_profile_is_still_valid_and_gets_two_minutes(example_me):
    assert "research" not in _read(example_me)  # nothing in the committed example changes
    research = config.load_profile().research
    assert research.scrape_seconds == 120.0 and isinstance(research.scrape_seconds, float)
    assert research.cost_cap == {"instagram": 0.25, "tiktok": 0.50}  # the caps are untouched


@pytest.mark.parametrize("group", [{}, {"cost_cap": {"tiktok": 1}}])
def test_the_default_holds_when_the_group_has_no_such_setting(example_me, group):
    _edit(example_me, lambda d: d.update(research=group))
    assert config.load_profile().research.scrape_seconds == 120.0


def test_the_default_is_written_once_in_the_schema_and_the_scraper_agrees():
    rule = json.loads(config.SCHEMA_PATH.read_text(encoding="utf-8"))["properties"]["research"]["properties"]["scrape_seconds"]
    assert (rule["type"], rule["minimum"], rule["maximum"], rule["default"]) == ("number", 10, 1800, 120)
    assert config.Research(cost_cap={}).scrape_seconds == 120.0  # code that builds the group by hand gets it too
    assert (scrape.MIN_SECONDS, scrape.MAX_SECONDS, scrape.BUDGET_SECONDS) == (10.0, 1800.0, 120.0)


# --- a value that is accepted ---------------------------------------------------------------------------


def test_a_value_is_accepted_and_read(example_me):
    _edit(example_me, lambda d: d.update(research={"scrape_seconds": 45}))
    research = config.load_profile().research
    assert research.scrape_seconds == 45.0 and isinstance(research.scrape_seconds, float)
    assert research.cost_cap == {"instagram": 0.25, "tiktok": 0.50}


@pytest.mark.parametrize("value", [10, 10.0, 59.5, 120, 1800, 1800.0])
def test_the_whole_range_is_accepted_ends_included(example_me, value):
    _edit(example_me, lambda d: d.update(research={"scrape_seconds": value}))
    assert config.load_profile().research.scrape_seconds == float(value)


def test_it_sits_beside_the_cost_caps(example_me):
    _edit(example_me, lambda d: d.update(research={"cost_cap": {"tiktok": 1}, "scrape_seconds": 300}))
    research = config.load_profile().research
    assert research.cost_cap == {"instagram": 0.25, "tiktok": 1.0} and research.scrape_seconds == 300.0


def test_the_profile_round_trips_with_the_setting(example_me):
    _edit(example_me, lambda d: d.update(research={"scrape_seconds": 45}))
    profile = config.load_profile()
    data = profile.to_dict()
    assert data["research"]["scrape_seconds"] == 45.0
    assert Profile.from_dict(data) == profile


def test_the_scraper_reads_the_setting_from_a_real_profile(example_me):
    assert scrape.scrape_seconds(config.load_profile()) == 120.0
    _edit(example_me, lambda d: d.update(research={"scrape_seconds": 45}))
    assert scrape.scrape_seconds(config.load_profile()) == 45.0


def test_a_pull_waits_as_long_as_the_real_profile_says(example_me, monkeypatch):
    # from a value in me/profile.json, through the loader, to the deadline the scrape works to
    seen = []

    def fake_youtube(creators, deadline):
        seen.append(deadline - time.monotonic())
        return [], {"status": "failed", "tool": "", "creators": [], "videos": 0}, None

    monkeypatch.setattr(scrape, "_youtube", fake_youtube)
    watch = {"instagram": [], "tiktok": [], "youtube": ["@example_channel"]}
    scrape.pull(config.load_profile(), watch, token=None)
    _edit(example_me, lambda d: d.update(research={"scrape_seconds": 45}))
    scrape.pull(config.load_profile(), watch, token=None)
    assert 119 < seen[0] <= 120 and 44 < seen[1] <= 45


# --- a value that is refused ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [5, 9.99, 0, -60])
def test_a_value_below_the_range_is_refused_in_plain_words(example_me, value):
    _edit(example_me, lambda d: d.update(research={"scrape_seconds": value}))
    assert _problems(example_me) == [f"research.scrape_seconds is {value}, which is below the lowest allowed value of 10. {HINT}"]


@pytest.mark.parametrize("value", [1801, 5000, 99999999])
def test_a_value_above_the_range_is_refused_in_plain_words(example_me, value):
    _edit(example_me, lambda d: d.update(research={"scrape_seconds": value}))
    assert _problems(example_me) == [f"research.scrape_seconds is {value}, which is above the highest allowed value of 1800. {HINT}"]


@pytest.mark.parametrize(
    "value, kind",
    [("60", "text"), (True, "true or false"), ([60], "a list"), ({"seconds": 60}, "a group of settings")],
)
def test_a_value_of_the_wrong_kind_is_refused(example_me, value, kind):
    _edit(example_me, lambda d: d.update(research={"scrape_seconds": value}))
    assert _problems(example_me) == [f"research.scrape_seconds has the wrong kind of value ({kind}). {HINT}"]


def test_an_empty_value_is_refused(example_me):
    _edit(example_me, lambda d: d.update(research={"scrape_seconds": None}))
    assert _problems(example_me) == [f"research.scrape_seconds is empty. {HINT}"]


def test_a_value_that_is_not_a_real_number_is_refused(example_me):
    _write_raw(example_me, "NaN")
    assert _problems(example_me) == [f"research.scrape_seconds is NaN, which is not a usable number of seconds. {HINT}"]


@pytest.mark.parametrize("literal, side", [("Infinity", "above the highest allowed value of 1800"), ("-Infinity", "below the lowest allowed value of 10")])
def test_infinity_is_reported_once_as_out_of_range(example_me, literal, side):
    _write_raw(example_me, literal)
    (problem,) = _problems(example_me)
    assert f"which is {side}." in problem and problem.startswith(f"research.scrape_seconds is {literal},")


def test_a_misspelt_setting_suggests_the_right_one(example_me):
    _edit(example_me, lambda d: d.update(research={"scrape_second": 60}))
    assert _problems(example_me) == ["research.scrape_second is not a setting the kit knows. Did you mean research.scrape_seconds?"]


def test_the_sentences_name_the_setting_and_hold_no_dash(example_me):
    for value in (5, 5000, "60", None):
        _edit(example_me, lambda d: d.update(research={"scrape_seconds": value}))
        for problem in _problems(example_me):
            assert problem.startswith("research.scrape_seconds ")
            assert EM_DASH not in problem and EN_DASH not in problem


def test_other_problems_are_still_listed_alongside(example_me):
    _edit(example_me, lambda d: d.update(research={"scrape_seconds": 5, "cost_cap": {"tiktok": -1}}))
    found = _problems(example_me)
    assert len(found) == 2
    assert any(p.startswith("research.cost_cap.tiktok is -1") for p in found)
    assert any(p.startswith("research.scrape_seconds is 5,") for p in found)
