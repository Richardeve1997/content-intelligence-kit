"""kit.research.performance: the creator's own recent results, read through Zernio when they have a key."""

import dataclasses
import sys
from datetime import timedelta

import pytest

from kit import config
from kit.config import KitError
from kit.research import performance
from research_helpers import NOW, write_profile

KEY = "fake-value-for-tests"
SKIPPED_NO_KEY = "Performance skipped: add ZERNIO_API_KEY to .env to include your own results"


def stamp(days_ago=0.0, hours_ago=0.0):
    return (NOW - timedelta(days=days_ago, hours=hours_ago)).isoformat().replace("+00:00", "Z")


def reel(code, *, views, likes, comments, saves, skip, duration, days_ago=7, keyword=True, feed=True, media="video", **extra):
    caption = f"Reel {code} about basil. " + ("Comment GROW and I send the checklist." if keyword else "Follow for more.")
    post = {
        "publishedAt": stamp(days_ago),
        "mediaType": media,
        "platformPostUrl": f"https://www.instagram.com/reel/{code}/",
        "content": caption,
        "isSharedToFeed": feed,
        "analytics": {
            "views": views, "reach": views - 500, "likes": likes, "comments": comments, "saves": saves,
            "shares": 20, "follows": 5, "reelsSkipRate": skip, "igReelsAvgWatchTime": 9500, "videoDurationSeconds": duration,
        },
    }
    post.update(extra)
    return post


# Worked out by hand. Engagement is 3 x comments + likes:
#   A1 1800, A2 1470, A3 2060, A4 1640 (short, kept viewers, a comment keyword)
#   B1 330, B2 274, B3 436, B4 347 (long, lost viewers, no keyword)
# Sorted: 274 330 347 436 1470 1640 1800 2060, so the median engagement is (436 + 1470) / 2 = 953.
# Views sorted: 3500 4000 4200 5000 18000 19000 20000 22000, so the median views is (5000 + 18000) / 2 = 11500.
REELS = [
    reel("A1", views=20000, likes=1500, comments=100, saves=600, skip=30, duration=30),
    reel("A2", views=18000, likes=1200, comments=90, saves=500, skip=35, duration=35),
    reel("A3", views=22000, likes=1700, comments=120, saves=700, skip=38, duration=28),
    reel("A4", views=19000, likes=1400, comments=80, saves=550, skip=42, duration=33),
    reel("B1", views=4000, likes=300, comments=10, saves=40, skip=65, duration=70, keyword=False),
    reel("B2", views=3500, likes=250, comments=8, saves=30, skip=70, duration=75, keyword=False),
    reel("B3", views=5000, likes=400, comments=12, saves=60, skip=62, duration=80, keyword=False),
    reel("B4", views=4200, likes=320, comments=9, saves=45, skip=68, duration=65, keyword=False),
]


def page(posts, *, pages=1, followers=1200, handle="examplestudio"):
    return {
        "success": True,
        "posts": posts,
        "pagination": {"pages": pages},
        "accounts": [{"username": handle, "followersCount": followers}, {"username": "someone_else", "followersCount": 9}],
        "overview": {"lastSync": stamp(0, 3)},
    }


@pytest.fixture
def profile(example_me):
    return config.load_profile()


def analyse(profile, posts, **kwargs):
    raw = {"posts": posts, "followers": 1200, "last_sync": stamp(0, 3), "handle": "examplestudio"}
    return performance.analyse(raw, profile, now=NOW, **kwargs)


# --- the maths ------------------------------------------------------------------------------------


def test_a_reel_is_scored_against_the_creators_own_median(profile):
    result = analyse(profile, REELS)
    assert result["status"] == "ok"
    assert result["median_engagement"] == 953
    assert result["median_views"] == 11500
    assert result["feed_reels"] == 8
    top = result["top"][0]
    assert top["id"] == "me:A3" and top["multiple"] == pytest.approx(2060 / 953)
    assert top["views_multiple"] == pytest.approx(22000 / 11500)
    assert [r["id"] for r in result["top"]] == ["me:A3", "me:A1", "me:A4", "me:A2", "me:B3"]  # five best, highest first


def test_the_three_patterns_the_numbers_back(profile):
    patterns = analyse(profile, REELS)["patterns"]
    assert [p["kind"] for p in patterns] == ["keyword", "skip", "short"]
    keyword = patterns[0]
    assert keyword["ratio"] == pytest.approx(13.5)  # median saves 575 with the keyword against 42.5 without
    assert "4 Reels" in keyword["text"]
    assert patterns[1]["ratio"] == pytest.approx(4.8)  # 19500 views against 4100
    assert patterns[2]["ratio"] == pytest.approx(4.8)


def test_a_pattern_needs_four_reels_on_each_side(profile):
    assert analyse(profile, REELS[:7])["patterns"] == []  # 4 + 3: one side is short


def test_a_pattern_needs_a_clear_gap(profile):
    flat = [
        reel(f"F{n}", views=10000 + n, likes=800, comments=40, saves=300, skip=30 if n < 4 else 70, duration=30 if n < 4 else 75,
             keyword=n < 4)
        for n in range(8)
    ]
    assert analyse(profile, flat)["patterns"] == []


def test_the_keyword_pattern_uses_the_keyword_in_the_profile(example_me):
    write_profile(example_me, cta={"type": "follow", "keyword": None, "payload": None, "comment_platforms": []})
    kinds = [p["kind"] for p in analyse(config.load_profile(), REELS)["patterns"]]
    assert "keyword" not in kinds  # a plain follow has no keyword to look for


def test_a_young_reel_is_listed_but_left_out_of_the_median(profile):
    fresh = reel("NEW", views=900, likes=80, comments=5, saves=9, skip=50, duration=30)
    fresh["publishedAt"] = stamp(hours_ago=10)
    result = analyse(profile, REELS + [fresh])
    assert result["median_engagement"] == 953
    assert result["young"] == ["me:NEW"]
    assert "me:NEW" not in [r["id"] for r in result["top"]]


def test_trial_reels_posts_that_are_not_video_and_old_posts_are_left_out(profile):
    extras = [
        reel("TRY", views=100, likes=5, comments=0, saves=1, skip=50, duration=30, feed=False),
        reel("PIC", views=100, likes=5, comments=0, saves=1, skip=50, duration=30, media="image"),
        reel("OLD", views=100, likes=5, comments=0, saves=1, skip=50, duration=30, days_ago=60),
        {"publishedAt": stamp(3), "mediaType": "video", "platformPostUrl": None, "content": "no link", "analytics": {}},
    ]
    result = analyse(profile, REELS + extras)
    assert result["feed_reels"] == 8 and result["trial_reels"] == 1
    assert result["median_engagement"] == 953


def test_a_missing_feed_flag_counts_as_a_feed_reel(profile):
    posts = [{k: v for k, v in r.items() if k != "isSharedToFeed"} for r in REELS]
    assert analyse(profile, posts)["feed_reels"] == 8


def test_no_reels_gives_an_honest_empty_result(profile):
    result = analyse(profile, [])
    assert result["status"] == "ok" and result["feed_reels"] == 0 and result["top"] == [] and result["patterns"] == []
    assert "no reels" in result["message"].lower()


def test_without_durations_there_is_no_length_pattern(profile):
    # no duration on any reel, so neither the length nor the short pattern can be drawn
    posts = [{**r, "analytics": {k: v for k, v in r["analytics"].items() if k != "videoDurationSeconds"}} for r in REELS]
    assert not {"short", "length"} & {p["kind"] for p in analyse(profile, posts)["patterns"]}


def test_followers_come_from_the_creators_own_account(profile):
    raw = {"posts": REELS, "followers": 777, "last_sync": None, "handle": "examplestudio"}
    assert performance.analyse(raw, profile, now=NOW)["followers"] == 777


# --- fetching through Zernio ----------------------------------------------------------------------


def test_without_a_key_the_section_says_so_in_one_sentence(profile):
    result = performance.collect(profile, key=None, now=NOW)
    assert result == {"status": "skipped", "message": SKIPPED_NO_KEY}


def test_a_blank_key_counts_as_no_key(profile):
    assert performance.collect(profile, key="  ", now=NOW)["message"] == SKIPPED_NO_KEY


def test_an_offline_run_does_not_ask_zernio(profile, fake_zernio):
    result = performance.collect(profile, key=KEY, offline=True, now=NOW)
    assert result == {"status": "skipped", "message": "Performance skipped: this is an offline run"}
    assert fake_zernio.calls == []


def test_the_analytics_are_read_page_by_page_with_the_creators_key(profile, fake_zernio):
    fake_zernio.pages = [page(REELS[:4], pages=2), page(REELS[4:], pages=2)]
    result = performance.collect(profile, key=KEY, now=NOW)
    assert result["status"] == "ok" and result["feed_reels"] == 8
    assert fake_zernio.keys == [KEY]
    assert [(m, p, kw["params"]["page"]) for m, p, kw in fake_zernio.calls] == [("GET", "/analytics", 1), ("GET", "/analytics", 2)]
    assert fake_zernio.calls[0][2]["params"] == {"platform": "instagram", "limit": 50, "page": 1}
    assert result["followers"] == 1200  # the creator's own account, not the other one in the list
    assert result["handle"] == "examplestudio"
    assert KEY not in repr(result)


def test_the_window_runs_six_weeks_back(profile, fake_zernio):
    assert performance.PERF_DAYS == 42
    fake_zernio.pages = [page(REELS)]
    result = performance.collect(profile, key=KEY, now=NOW)
    assert result["window_to"] == "2026-10-28" and result["window_from"] == "2026-09-16"


def test_a_zernio_error_is_explained_without_the_key(profile, fake_zernio):
    fake_zernio.pages = RuntimeError(f"Zernio returned HTTP 401: the key {KEY} is not valid")
    result = performance.collect(profile, key=KEY, now=NOW)
    assert result["status"] == "skipped"
    assert result["message"] == "Performance skipped: Zernio did not answer (Zernio returned HTTP 401: the key <key> is not valid)"
    assert KEY not in repr(result)


def test_zernios_own_sentence_is_shown_as_it_is(profile, fake_zernio):
    # kit.zernio raises a KitError whose message is already one plain sentence, so nothing is added to it
    fake_zernio.pages = KitError(f"Zernio returned HTTP 401: the key {KEY} was not accepted.")
    result = performance.collect(profile, key=KEY, now=NOW)
    # it keeps its words but gives up its own full stop, since the line it ends has none, like every notice
    assert result == {"status": "skipped", "message": "Performance skipped: Zernio returned HTTP 401: the key <key> was not accepted"}
    fake_zernio.pages = KitError("Zernio did not answer in time.")
    assert performance.collect(profile, key=KEY, now=NOW)["message"] == "Performance skipped: Zernio did not answer in time"


def test_a_copy_of_the_kit_without_zernio_carries_on(profile, monkeypatch):
    monkeypatch.setitem(sys.modules, "kit.zernio", None)
    result = performance.collect(profile, key=KEY, now=NOW)
    assert result == {"status": "skipped", "message": "Performance skipped: the kit's Zernio tools are not available in this copy"}


def test_a_creator_who_is_not_on_instagram_is_told_so(profile, fake_zernio):
    other = dataclasses.replace(profile, platforms=("tiktok", "youtube"))
    result = performance.collect(other, key=KEY, now=NOW)
    assert result["message"] == "Performance skipped: it reads Instagram, and Instagram is not in your platforms"
    assert fake_zernio.calls == []
