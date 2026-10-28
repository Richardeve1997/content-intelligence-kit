"""kit.research.offline: the made-up sample a run uses when it must not touch the network."""

import json

import pytest

from kit.research import common, offline, rank
from research_helpers import NOW

CREATORS = {
    ("instagram", "example_gardener_one"): 6,
    ("instagram", "example_gardener_two"): 5,
    ("instagram", "example_newcomer"): 2,
    ("tiktok", "example_tiktoker"): 5,
    ("youtube", "example_channel"): 5,
}


@pytest.fixture
def pull():
    return offline.sample_pull(NOW)


def test_the_sample_has_invented_creators_on_all_three_platforms(pull):
    counts = {}
    for item in pull.items:
        counts[(item["platform"], item["creator"])] = counts.get((item["platform"], item["creator"]), 0) + 1
    assert counts == CREATORS
    assert len({i["id"] for i in pull.items}) == len(pull.items)  # no repeated id
    assert all(i["creator"].startswith("example_") for i in pull.items)
    assert all(i["url"].startswith("https://example.com/") for i in pull.items)  # a reserved example address
    assert {"instagram", "tiktok", "youtube"} == {i["platform"] for i in pull.items}


def test_dates_are_counted_back_from_now(pull):
    newest = max(common.parse_iso(i["posted_at"]) for i in pull.items)
    assert (NOW - newest).total_seconds() == pytest.approx(86400, abs=1)  # the freshest video is a day old
    assert offline.sample_pull(NOW).items == pull.items  # the same every time


def test_every_source_says_it_is_sample_data(pull):
    for name in ("instagram", "tiktok", "youtube"):
        source = pull.sources[name]
        assert source["status"] == "ok" and source["tool"] == "sample data"
        assert source["creators"]
    assert pull.notices == ["Offline run: these videos are made-up samples, not your watch list"]


def test_the_sample_scores_the_way_the_hand_working_says():
    # Engagement is 3 x comments + likes. For the best Instagram creator, by hand:
    #   6430 1000 1265 853 1081 1770 -> sorted 853 1000 1081 1265 1770 6430 -> median (1081 + 1265) / 2 = 1173
    #   the standout is 6430 / 1173 = 5.48
    scored = rank.score(offline.sample_pull(NOW).items)
    standout = next(v for v in scored.videos if v["creator"] == "example_gardener_one" and (v["outlier"] or 0) > 5)
    assert standout["outlier"] == pytest.approx(6430 / 1173)
    assert standout["creator_median"] == 1173
    # The TikTok standout: 11100 against a median of 1770 is 6.27, the best score in the sample.
    assert scored.videos[0]["platform"] == "tiktok" and scored.videos[0]["outlier"] == pytest.approx(11100 / 1770)
    # The newcomer has two videos, which is a low sample.
    flags = {c["creator"]: c["low_sample"] for c in scored.creators}
    assert flags["example_newcomer"] is True
    assert [name for name, low in flags.items() if low] == ["example_newcomer"]


def test_the_sample_holds_an_older_video_that_stood_out():
    # example_gardener_two: 2490 1710 1480 2085 6400 -> median 2085, so the 40 day old video ran 6400 / 2085 = 3.07 times normal
    scored = rank.score(offline.sample_pull(NOW).items)
    old = next(v for v in scored.videos if v["creator"] == "example_gardener_two" and v["set"] == "earlier")
    assert old["outlier"] == pytest.approx(6400 / 2085) and old["outlier"] >= rank.EARLIER_MIN_OUTLIER


def test_some_videos_come_with_what_was_said_in_them_and_some_do_not(pull):
    with_words = [i for i in pull.items if i.get("transcript")]
    without = [i for i in pull.items if not i.get("transcript")]
    assert with_words and without
    for item in with_words:
        assert len(item["transcript"].split()) >= 8 and item["transcript_source"] == "sample data"


def test_the_sample_file_is_plain_json_with_no_real_people_in_it():
    text = offline.SAMPLE_PATH.read_text(encoding="utf-8")
    data = json.loads(text)
    assert data["creators"]
    assert "@" not in text  # no handles or addresses of anyone real
    assert "http" not in text  # addresses are built when the sample is loaded
