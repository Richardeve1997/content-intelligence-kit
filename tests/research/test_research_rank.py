"""kit.research.rank: the outlier score, each creator's normal, and the low sample flag."""

import copy

import pytest

from kit.research import rank


def history_of(*records, platform="instagram", creator="alpha"):
    """Earlier-run records for one creator, in the shape the brain stores them."""
    return {(platform, creator): {r["id"]: r for r in records}}


# --- the score itself ---------------------------------------------------------------------------


def test_outlier_formula():
    # (3 x comments + likes) / that creator's median, worked out by hand
    assert rank.outlier(comments=10, likes=100, creator_median=50) == pytest.approx(2.6)  # (30 + 100) / 50
    assert rank.outlier(comments=0, likes=80, creator_median=40) == pytest.approx(2.0)  # 80 / 40
    assert rank.outlier(comments=5, likes=0, creator_median=5) == pytest.approx(3.0)  # 15 / 5
    assert rank.outlier(comments=1200, likes=36000, creator_median=4800) == pytest.approx(8.25)  # 39600 / 4800
    assert rank.outlier(10, 100, 50) == rank.outlier(comments=10, likes=100, creator_median=50)


def test_comments_count_three_times_as_much_as_likes():
    assert rank.engagement(likes=100, comments=10) == 130
    assert rank.engagement(likes=0, comments=1) == 3


def test_a_hidden_like_count_counts_as_zero():
    # Some platforms send -1 when a creator hides likes.
    assert rank.engagement(likes=-1, comments=4) == 12
    assert rank.engagement(likes=None, comments=None) == 0


@pytest.mark.parametrize("median", [0, 0.0, -3])
def test_a_median_of_zero_cannot_score_anything(median):
    with pytest.raises(ValueError) as caught:
        rank.outlier(comments=1, likes=1, creator_median=median)
    assert "median" in str(caught.value)


# --- the low sample flag --------------------------------------------------------------------------


def test_low_sample_flag(video):
    items = [
        video("a1", "alpha", likes=100),
        video("a2", "alpha", likes=200),
        video("b1", "bravo", likes=100),
        video("b2", "bravo", likes=200),
        video("b3", "bravo", likes=300),
        video("t1", "alpha", likes=100, platform="tiktok"),
    ]
    scored = rank.score(items)
    by_key = {(c["platform"], c["creator"]): c for c in scored.creators}
    assert by_key[("instagram", "alpha")]["low_sample"] is True  # 2 videos
    assert by_key[("instagram", "bravo")]["low_sample"] is False  # exactly 3 is enough
    assert by_key[("tiktok", "alpha")]["low_sample"] is True  # 1 video, and its own baseline
    flags = {(v["platform"], v["creator"]): v["low_sample"] for v in scored.videos}
    assert flags == {("instagram", "alpha"): True, ("instagram", "bravo"): False, ("tiktok", "alpha"): True}


def test_three_is_the_smallest_sample_that_is_not_low():
    assert rank.LOW_SAMPLE_BELOW == 3
    assert rank.is_low_sample(0) and rank.is_low_sample(1) and rank.is_low_sample(2)
    assert not rank.is_low_sample(3) and not rank.is_low_sample(40)


def test_earlier_runs_count_toward_the_sample_but_a_repeat_counts_once(video):
    items = [video("a1", likes=100, comments=0), video("a2", likes=200, comments=0)]
    earlier = history_of(
        {"id": "ig:a1", "likes": 90, "comments": 0, "posted_at": items[0]["posted_at"]},  # the same video again
        {"id": "ig:old1", "likes": 300, "comments": 0, "posted_at": "2026-08-01T10:00:00+00:00"},
    )
    scored = rank.score(items, earlier)
    (creator,) = scored.creators
    assert creator["on_record"] == 3  # a1, a2 and old1: a1 is not counted twice
    assert creator["low_sample"] is False
    # the pull from this run wins over the older numbers for the same video
    a1 = next(v for v in scored.videos if v["id"] == "ig:a1")
    assert a1["engagement"] == 100


# --- each creator's normal ------------------------------------------------------------------------


def test_median_is_over_this_run_plus_earlier_runs(video):
    items = [video("v1", likes=100, comments=0), video("v2", likes=300, comments=0)]
    earlier = history_of(
        *[
            {"id": f"ig:h{n}", "likes": likes, "comments": 0, "posted_at": "2026-09-01T10:00:00+00:00"}
            for n, likes in ((1, 200), (2, 400), (3, 1000))
        ]
    )
    scored = rank.score(items, earlier)
    by_id = {v["id"]: v for v in scored.videos if v["set"] == "recent"}
    # engagement over all five: 100, 200, 300, 400, 1000 -> the median is 300
    assert by_id["ig:v1"]["creator_median"] == 300
    assert by_id["ig:v1"]["outlier"] == pytest.approx(100 / 300)
    assert by_id["ig:v2"]["outlier"] == pytest.approx(1.0)
    assert by_id["ig:v2"]["creator_n"] == 5


def test_a_normal_video_scores_about_one_and_a_standout_scores_high(video):
    items = [video(f"v{n}", likes=likes, comments=0) for n, likes in enumerate([100, 100, 100, 100, 1200], 1)]
    scored = rank.score(items)
    top = scored.videos[0]
    assert top["id"] == "ig:v5"
    assert top["outlier"] == pytest.approx(12.0)  # 1200 / median 100
    assert [round(v["outlier"], 2) for v in scored.videos[1:]] == [1.0, 1.0, 1.0, 1.0]


def test_videos_come_back_best_first_with_a_rank(video):
    items = [video("v1", likes=100), video("v2", likes=900), video("v3", likes=300)]
    scored = rank.score(items)
    assert [v["id"] for v in scored.videos] == ["ig:v2", "ig:v3", "ig:v1"]
    assert [v["rank"] for v in scored.videos] == [1, 2, 3]


def test_a_creator_with_no_engagement_at_all_gets_no_score(video):
    items = [video(f"z{n}", "quiet", likes=0, comments=0) for n in range(3)]
    items.append(video("a1", "loud", likes=500))
    scored = rank.score(items)
    quiet = [v for v in scored.videos if v["creator"] == "quiet"]
    assert len(quiet) == 3 and all(v["outlier"] is None for v in quiet)
    assert scored.videos[0]["creator"] == "loud"  # an unscored video never outranks a scored one
    assert [v["creator"] for v in scored.videos[1:]] == ["quiet"] * 3


# --- fresh and older videos -----------------------------------------------------------------------


def test_window_and_young_flags(video):
    items = [
        video("hours", days_ago=10 / 24),
        video("days", days_ago=3),
        video("weeks", days_ago=20),
    ]
    by_id = {v["id"]: v for v in rank.score(items).videos}
    assert by_id["ig:hours"]["young"] is True and by_id["ig:hours"]["in_window"] is True
    assert by_id["ig:days"]["young"] is False and by_id["ig:days"]["in_window"] is True
    assert by_id["ig:weeks"]["in_window"] is False and by_id["ig:weeks"]["set"] == "earlier"
    assert by_id["ig:days"]["set"] == "recent"


def test_a_video_exactly_two_days_old_is_no_longer_young(video):
    by_id = {v["id"]: v for v in rank.score([video("edge", days_ago=2.0), video("under", days_ago=1.99)]).videos}
    assert by_id["ig:edge"]["young"] is False  # "under two days old" means strictly under
    assert by_id["ig:under"]["young"] is True


def test_standouts_from_earlier_runs_join_the_list(video):
    items = [video("now1", likes=100, comments=0)]
    old = [
        {"id": f"ig:old{n}", "likes": 100, "comments": 0, "url": f"https://example.com/old{n}", "title": "old",
         "posted_at": "2026-08-01T10:00:00+00:00"}
        for n in range(1, 6)
    ]
    old.append({"id": "ig:big", "likes": 900, "comments": 0, "url": "https://example.com/big", "title": "big",
                "posted_at": "2026-08-02T10:00:00+00:00"})
    scored = rank.score(items, history_of(*old))
    ids = [v["id"] for v in scored.videos]
    assert "ig:big" in ids  # 900 against a median of 100 is 9 times normal
    big = next(v for v in scored.videos if v["id"] == "ig:big")
    assert big["source"] == "brain" and big["set"] == "earlier" and big["outlier"] == pytest.approx(9.0)
    assert "ig:old1" not in ids  # an ordinary old video is not worth listing
    assert next(v for v in scored.videos if v["id"] == "ig:now1")["source"] == "run"


def test_only_the_best_few_earlier_standouts_are_kept(video):
    items = [video("now1", likes=100, comments=0)]
    ordinary = [
        {"id": f"ig:norm{n}", "likes": 100, "comments": 0, "url": f"https://example.com/n{n}", "title": "x",
         "posted_at": "2026-08-01T10:00:00+00:00"}
        for n in range(20)
    ]
    standouts = [
        {"id": f"ig:o{n}", "likes": 1000 + 100 * n, "comments": 0, "url": f"https://example.com/o{n}", "title": "x",
         "posted_at": "2026-08-01T10:00:00+00:00"}
        for n in range(12)
    ]
    scored = rank.score(items, history_of(*ordinary, *standouts))
    earlier = [v for v in scored.videos if v["source"] == "brain"]
    # 33 videos on record, so the median is an ordinary 100 and every standout is 10 times normal or more
    assert len(earlier) == rank.EARLIER_LIMIT == 8
    assert {v["id"] for v in earlier} == {f"ig:o{n}" for n in range(4, 12)}  # the eight strongest
    assert min(v["outlier"] for v in earlier) >= rank.EARLIER_MIN_OUTLIER


def test_odd_history_records_are_ignored(video):
    history = history_of({"id": "ig:ok", "likes": 50, "comments": 0, "posted_at": "2026-08-01T10:00:00+00:00"})
    history[("instagram", "alpha")]["junk"] = "not a record"
    history[("instagram", "alpha")]["noid"] = {"likes": 5}
    scored = rank.score([video("a1", likes=100, comments=0)], history)
    (creator,) = scored.creators
    assert creator["on_record"] == 2  # the one good record and this run's video


def test_scoring_leaves_its_input_alone(video):
    items = [video("v1", likes=100), video("v2", likes=900), video("v3", likes=300)]
    before = copy.deepcopy(items)
    history = history_of({"id": "ig:h", "likes": 5, "comments": 0, "posted_at": "2026-08-01T10:00:00+00:00"})
    history_before = copy.deepcopy(history)
    rank.score(items, history)
    assert items == before
    assert history == history_before


def test_creator_names_match_without_regard_to_case(video):
    items = [video("v1", "Alpha", likes=100), video("v2", "alpha", likes=200), video("v3", "ALPHA", likes=300)]
    (creator,) = rank.score(items).creators
    assert creator["on_record"] == 3 and creator["low_sample"] is False


def test_best_outlier_is_reported_for_each_creator(video):
    items = [video(f"v{n}", likes=likes, comments=0) for n, likes in enumerate([100, 100, 100, 400], 1)]
    (creator,) = rank.score(items).creators
    assert creator["best_outlier"] == pytest.approx(4.0)
    assert creator["median_engagement"] == 100
    assert creator["pulled"] == 4 and creator["in_window"] == 4
