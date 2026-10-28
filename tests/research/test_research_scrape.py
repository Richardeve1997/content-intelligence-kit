"""kit.research.scrape: Apify for Instagram and TikTok, yt-dlp for YouTube, and what happens when they cannot run."""

import sys
import time
import types
from urllib.parse import parse_qs, urlparse

import pytest

from kit import config
from kit.research import common, scrape
from research_helpers import apify, ig_post, make_yt_dlp, short_info, started, tt_post, write_profile

TOKEN = "fake-value-for-tests"
MISSING = "Instagram and TikTok skipped: add APIFY_TOKEN to .env to include them"

WATCH = {
    "instagram": ["@example_gardener_one", "example_gardener_two"],
    "tiktok": ["@example_tiktoker"],
    "youtube": [],
}
IG_ONLY = {"instagram": ["@example_gardener_one"], "tiktok": [], "youtube": []}
IG_TWO = {"instagram": ["@example_gardener_one", "@example_gardener_two"], "tiktok": [], "youtube": []}


@pytest.fixture
def profile(example_me):
    return config.load_profile()


def charge_cap(web, actor):
    (url,) = [u for u in web.urls("POST") if actor in u]
    return float(parse_qs(urlparse(url).query)["maxTotalChargeUsd"][0])


# --- the time limit -----------------------------------------------------------------------------------------


def with_seconds(profile, value):
    """A profile whose research settings carry scrape_seconds, as they will once the profile format allows it."""
    return types.SimpleNamespace(research=types.SimpleNamespace(cost_cap=profile.research.cost_cap, scrape_seconds=value))


def test_the_time_limit_is_two_minutes_unless_the_profile_says_otherwise(profile):
    assert scrape.BUDGET_SECONDS == 120.0
    assert scrape.scrape_seconds(profile) == 120.0  # an existing profile has no such setting


@pytest.mark.parametrize("value, expected", [(45, 45.0), (45.5, 45.5), (10, 10.0), (1800, 1800.0), (300, 300.0)])
def test_a_profile_can_set_the_time_limit(profile, value, expected):
    assert scrape.scrape_seconds(with_seconds(profile, value)) == expected


@pytest.mark.parametrize("value", [None, 0, 5, -30, 5000, "60", True, float("nan"), [60]])
def test_a_time_limit_that_makes_no_sense_falls_back_to_the_default(profile, value):
    assert scrape.scrape_seconds(with_seconds(profile, value)) == 120.0


def test_a_pull_waits_as_long_as_the_profile_allows(profile, monkeypatch):
    seen = []

    def fake_youtube(creators, deadline):
        seen.append(deadline - time.monotonic())
        return [], {"status": "failed", "tool": "", "creators": [], "videos": 0}, None

    monkeypatch.setattr(scrape, "_youtube", fake_youtube)
    watch = {"instagram": [], "tiktok": [], "youtube": ["@example_channel"]}
    scrape.pull(with_seconds(profile, 45), watch, token=None)
    scrape.pull(profile, watch, token=None)
    scrape.pull(with_seconds(profile, 45), watch, token=None, budget=30)  # a limit given to the call wins
    assert 44 < seen[0] <= 45 and 119 < seen[1] <= 120 and 29 < seen[2] <= 30


# --- no key, no Apify -----------------------------------------------------------------------------


def test_the_exact_sentence_for_a_missing_key():
    assert scrape.APIFY_MISSING == MISSING


def test_without_a_token_instagram_and_tiktok_are_skipped_and_nothing_is_called(profile, web):
    pull = scrape.pull(profile, WATCH, token=None)
    assert pull.items == []
    assert pull.notices == [MISSING]
    assert pull.sources["instagram"]["status"] == "skipped" and pull.sources["tiktok"]["status"] == "skipped"
    assert web.calls == []


def test_a_blank_token_counts_as_no_token(profile, web):
    assert scrape.pull(profile, WATCH, token="   ").notices == [MISSING]


def test_no_apify_flag_with_a_token_skips_without_asking_for_a_key(profile, web):
    pull = scrape.pull(profile, WATCH, token=TOKEN, use_apify=False)
    assert pull.notices == ["Instagram and TikTok skipped: this run was started with --no-apify"]
    assert web.calls == []


def test_no_apify_flag_without_a_token_still_names_the_key(profile, web):
    assert scrape.pull(profile, WATCH, token=None, use_apify=False).notices == [MISSING]


# --- Apify, the happy path ------------------------------------------------------------------------


def test_instagram_and_tiktok_come_back_as_plain_videos(profile, web):
    apify(
        web,
        ig=[
            ig_post("AAA111"),
            ig_post("BBB222", kind="Image"),  # a photo post is not a video
            ig_post("CCC333", owner="someone_else"),  # not on the watch list
            ig_post("DDD444", owner="Example_Gardener_Two", likes=-1, comments=5),  # likes hidden
        ],
        tt=[tt_post("7000000000000000001"), tt_post("7000000000000000002", owner="not_on_the_list")],
    )
    pull = scrape.pull(profile, WATCH, token=TOKEN)
    assert pull.notices == []
    by_id = {i["id"]: i for i in pull.items}
    assert sorted(by_id) == ["ig:AAA111", "ig:DDD444", "tt:7000000000000000001"]

    a = by_id["ig:AAA111"]
    assert (a["platform"], a["creator"]) == ("instagram", "example_gardener_one")
    assert a["url"] == "https://www.instagram.com/p/AAA111/"
    assert (a["views"], a["likes"], a["comments"]) == (12000, 500, 40)
    assert a["title"] == "Why basil dies in week three AAA111"
    assert a["duration"] == pytest.approx(34.2)
    assert a["video_url"] == "https://cdn.example.com/AAA111.mp4"
    assert common.parse_iso(a["posted_at"]) is not None

    d = by_id["ig:DDD444"]
    assert d["creator"] == "example_gardener_two"  # matched without regard to case
    assert d["likes"] == 0 and d["comments"] == 5  # a hidden count is treated as zero

    t = by_id["tt:7000000000000000001"]
    assert (t["platform"], t["creator"]) == ("tiktok", "example_tiktoker")
    assert (t["views"], t["likes"], t["comments"]) == (30000, 900, 60)
    assert t["subtitle_url"] == "https://cdn.example.com/en.vtt"  # the English track
    assert t["title"] == "Soil that never dries out 7000000000000000001"


def test_the_token_travels_in_a_header_never_in_an_address(profile, web):
    apify(web, ig=[ig_post("AAA111")], tt=[tt_post("1")])
    scrape.pull(profile, WATCH, token=TOKEN)
    assert web.calls
    assert all(TOKEN not in url for url in web.urls())
    sent = [h for (_, url, _), h in zip(web.calls, web.headers) if "apify.com" in url]
    assert sent and all(h.get("Authorization") == f"Bearer {TOKEN}" for h in sent)


def test_the_run_inputs_name_every_creator_on_the_list(profile, web):
    apify(web, ig=[ig_post("AAA111")], tt=[tt_post("1")])
    scrape.pull(profile, WATCH, token=TOKEN)
    bodies = {u: b for (m, u, b) in web.calls if m == "POST"}
    ig_body = next(b for u, b in bodies.items() if "instagram-scraper" in u)
    tt_body = next(b for u, b in bodies.items() if "tiktok-scraper" in u)
    assert ig_body["directUrls"] == [
        "https://www.instagram.com/example_gardener_one/",
        "https://www.instagram.com/example_gardener_two/",
    ]
    assert ig_body["resultsType"] == "posts"
    assert tt_body["profiles"] == ["example_tiktoker"]


def test_a_creator_who_returned_nothing_is_noted(profile, web):
    apify(web, ig=[ig_post("AAA111")], tt=[tt_post("1")])
    pull = scrape.pull(profile, WATCH, token=TOKEN)
    ig = {c["creator"]: c for c in pull.sources["instagram"]["creators"]}
    assert ig["example_gardener_one"]["videos"] == 1
    assert ig["example_gardener_two"]["videos"] == 0 and ig["example_gardener_two"]["note"] == "no videos returned"


def test_a_platform_that_returns_no_videos_at_all_is_reported(profile, web):
    apify(web, ig=[ig_post("AAA111")], tt=[])
    pull = scrape.pull(profile, WATCH, token=TOKEN)
    assert pull.sources["tiktok"]["status"] == "failed"
    assert "TikTok returned no videos this time for @example_tiktoker" in pull.notices
    assert [i["id"] for i in pull.items] == ["ig:AAA111"]


def test_the_cost_of_a_run_is_kept(profile, web):
    apify(web, ig=[ig_post("AAA111")])
    pull = scrape.pull(profile, IG_ONLY, token=TOKEN)
    assert pull.sources["instagram"]["cost_usd"] == pytest.approx(0.12)
    assert pull.sources["instagram"]["tool"] == "Apify (apify/instagram-scraper)"


# --- creators who return nothing are named ---------------------------------------------------------------


def test_creators_with_no_videos_are_named_in_one_notice(profile, web):
    apify(web, ig=[ig_post("AAA111")])
    watch = {"instagram": ["@example_gardener_one", "@example_gardener_two", "@example_third"], "tiktok": [], "youtube": []}
    pull = scrape.pull(profile, watch, token=TOKEN)
    assert pull.notices == ["Instagram returned no videos for @example_gardener_two and @example_third"]
    assert pull.sources["instagram"]["status"] == "ok"  # one creator did answer


def test_a_reason_from_apify_is_given_when_there_is_one(profile, web):
    error = {"inputUrl": "https://www.instagram.com/Example_Gardener_Two/", "error": "no_items", "errorDescription": "Empty or private data for provided input"}
    apify(web, ig=[ig_post("AAA111"), error])
    pull = scrape.pull(profile, IG_TWO, token=TOKEN)
    assert pull.notices == ["Instagram returned no videos for @example_gardener_two (Empty or private data for provided input)"]
    row = next(r for r in pull.sources["instagram"]["creators"] if r["creator"] == "example_gardener_two")
    assert row["note"] == "Empty or private data for provided input"


def test_an_error_item_for_a_creator_who_did_return_videos_changes_nothing(profile, web):
    error = {"inputUrl": "https://www.instagram.com/example_gardener_one/", "error": "rate_limited", "errorDescription": "Too many requests"}
    apify(web, ig=[ig_post("AAA111"), error])
    pull = scrape.pull(profile, IG_ONLY, token=TOKEN)
    assert pull.notices == [] and [i["id"] for i in pull.items] == ["ig:AAA111"]


def test_a_platform_where_every_creator_is_silent_names_them_all(profile, web):
    apify(web, ig=[], tt=[])
    pull = scrape.pull(profile, WATCH, token=TOKEN)
    assert "Instagram returned no videos this time for @example_gardener_one and @example_gardener_two" in pull.notices
    assert "TikTok returned no videos this time for @example_tiktoker" in pull.notices


def test_a_long_list_of_silent_creators_is_shortened(profile, web):
    names = [f"@creator_{n}" for n in range(12)]
    apify(web, ig=[ig_post("AAA111", owner="creator_0")])
    pull = scrape.pull(profile, {"instagram": names, "tiktok": [], "youtube": []}, token=TOKEN)
    (notice,) = pull.notices
    assert notice.startswith("Instagram returned no videos for @creator_1, @creator_2")
    assert notice.endswith("@creator_8 and 3 more")  # 11 are silent: eight are named, then "and 3 more"
    assert "@creator_9" not in notice


def test_youtube_names_who_returned_nothing_and_why(profile, web, youtube):
    watch = {"instagram": [], "tiktok": [], "youtube": ["@example_channel", "@broken_channel", "@empty_channel"]}
    pull = scrape.pull(profile, watch, token=None)
    assert "YouTube returned no videos for @broken_channel (channel not found) and @empty_channel (no Shorts returned)" in pull.notices


def test_a_youtube_creator_whose_shorts_cannot_be_read_is_named(profile, web, monkeypatch):
    class Half:
        def __init__(self, options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=False):
            if "/@" in url:
                return {"entries": [{"id": "a1"}, {"id": "a2"}]}
            raise RuntimeError("Video unavailable")

    module = type(sys)("yt_dlp")
    module.YoutubeDL = Half
    monkeypatch.setitem(sys.modules, "yt_dlp", module)
    pull = scrape.pull(profile, {"instagram": [], "tiktok": [], "youtube": ["@example_channel"]}, token=None)
    assert "YouTube returned no videos this time for @example_channel (its Shorts could not be read)" in pull.notices


@pytest.mark.parametrize(
    "raw, plain",
    [
        ("ERROR: [youtube:tab] @zz_nobody/shorts: Unable to download API page: HTTP Error 404: Not Found (caused by <HTTPError 404: Not Found>)", "channel not found"),
        ("ERROR: [youtube:tab] example: This channel does not have a shorts tab", "This channel does not have a shorts tab"),
        ("\x1b[0;31mERROR:\x1b[0m [youtube:tab] x: Something odd happened (caused by <Odd>)", "Something odd happened"),
        ("plain words with no prefix", "plain words with no prefix"),
        ("", "RuntimeError"),
    ],
)
def test_yt_dlp_errors_become_plain_reasons(raw, plain):
    assert scrape._ytdlp_reason(RuntimeError(raw)) == plain


def test_a_very_long_reason_is_cut(profile):
    assert len(scrape._ytdlp_reason(RuntimeError("word " * 100))) <= 120


# --- a run that stops at its charge limit -------------------------------------------------------------------


@pytest.mark.parametrize("status", ["ABORTED", "FAILED", "TIMED-OUT", "SUCCEEDED"])
def test_a_run_stopped_at_the_charge_limit_keeps_what_was_paid_for(profile, web, status):
    apify(web, ig=[ig_post("AAA111")], ig_run=started("run-ig", "ds-ig", status, 0.25))
    pull = scrape.pull(profile, IG_TWO, token=TOKEN)
    assert [i["id"] for i in pull.items] == ["ig:AAA111"]  # the videos Apify had already delivered are not thrown away
    assert pull.sources["instagram"]["status"] == "ok" and pull.sources["instagram"]["stopped_by"] == "cap"
    assert pull.notices == [
        "Instagram stopped at your spending cap of US$0.25, so no videos came back for @example_gardener_two. "
        "Raise research.cost_cap.instagram in me/profile.json to pull more"
    ]


def test_the_cap_that_is_named_is_the_one_in_the_profile(example_me, web):
    write_profile(example_me, research={"cost_cap": {"instagram": 0.10, "tiktok": 0.75}})
    apify(web, tt=[tt_post("1")], tt_run=started("run-tt", "ds-tt", "ABORTED", 0.75))
    watch = {"instagram": [], "tiktok": ["@example_tiktoker", "@example_other"], "youtube": []}
    pull = scrape.pull(config.load_profile(), watch, token=TOKEN)
    assert pull.notices == [
        "TikTok stopped at your spending cap of US$0.75, so no videos came back for @example_other. "
        "Raise research.cost_cap.tiktok in me/profile.json to pull more"
    ]


def test_using_the_whole_cap_is_said_even_when_everyone_answered(profile, web):
    apify(web, ig=[ig_post("AAA111")], ig_run=started("run-ig", "ds-ig", "SUCCEEDED", 0.25))
    pull = scrape.pull(profile, IG_ONLY, token=TOKEN)
    assert pull.notices == [
        "Instagram used all of its US$0.25 spending cap. Raise research.cost_cap.instagram in me/profile.json if a creator looks thin"
    ]
    assert [i["id"] for i in pull.items] == ["ig:AAA111"]


def test_a_cap_reached_with_nothing_delivered_is_a_failure_with_the_cap_named(profile, web):
    apify(web, ig=[], ig_run=started("run-ig", "ds-ig", "ABORTED", 0.25))
    pull = scrape.pull(profile, IG_ONLY, token=TOKEN)
    assert pull.items == [] and pull.sources["instagram"]["status"] == "failed"
    assert pull.notices == [
        "Instagram stopped at your spending cap of US$0.25, so no videos came back for @example_gardener_one. "
        "Raise research.cost_cap.instagram in me/profile.json to pull more"
    ]


def test_the_account_running_low_is_not_blamed_on_the_cap_setting(profile, web):
    apify(web, ig=[ig_post("AAA111")], left=0.20, ig_run=started("run-ig", "ds-ig", "ABORTED", 0.19))
    pull = scrape.pull(profile, IG_TWO, token=TOKEN)
    assert pull.sources["instagram"]["stopped_by"] == "balance"
    assert pull.notices == [
        "Instagram stopped because your Apify account only had US$0.20 left this month, so no videos came back for @example_gardener_two"
    ]
    assert "cost_cap" not in pull.notices[0]


def test_a_run_that_stopped_short_of_its_cap_is_still_a_failure(profile, web):
    apify(web, ig=[ig_post("AAA111")], ig_run=started("run-ig", "ds-ig", "FAILED", 0.10))
    pull = scrape.pull(profile, IG_ONLY, token=TOKEN)
    assert pull.items == [] and pull.notices == ["Instagram skipped: the Apify run failed"]


def test_a_polled_run_that_ends_at_the_charge_limit_keeps_its_videos(profile, web):
    apify(web, ig=[ig_post("AAA111")], ig_run=started("run-ig", "ds-ig", "RUNNING", 0))
    web.add("GET", "/actor-runs/run-ig", {"data": {"id": "run-ig", "status": "ABORTED", "usageTotalUsd": 0.25}})
    pull = scrape.pull(profile, IG_ONLY, token=TOKEN, budget=100.0)
    assert [i["id"] for i in pull.items] == ["ig:AAA111"]
    assert pull.sources["instagram"]["cost_usd"] == pytest.approx(0.25)
    assert pull.notices and "research.cost_cap.instagram" in pull.notices[0]


# --- the spending caps ----------------------------------------------------------------------------


def test_the_default_caps_are_a_quarter_and_fifty_cents(profile, web):
    apify(web, ig=[ig_post("AAA111")], tt=[tt_post("1")])
    scrape.pull(profile, WATCH, token=TOKEN)
    assert charge_cap(web, "instagram-scraper") == 0.25
    assert charge_cap(web, "tiktok-scraper") == 0.5


def test_the_caps_come_from_the_profile(example_me, web):
    write_profile(example_me, research={"cost_cap": {"instagram": 0.10, "tiktok": 0.75}})
    apify(web, ig=[ig_post("AAA111")], tt=[tt_post("1")])
    scrape.pull(config.load_profile(), WATCH, token=TOKEN)
    assert charge_cap(web, "instagram-scraper") == 0.1
    assert charge_cap(web, "tiktok-scraper") == 0.75


def test_a_cap_below_what_a_pull_needs_skips_that_platform_only(example_me, web):
    write_profile(example_me, research={"cost_cap": {"tiktok": 0.20}})
    apify(web, ig=[ig_post("AAA111")], tt=[tt_post("1")])
    pull = scrape.pull(config.load_profile(), WATCH, token=TOKEN)
    assert [i["id"] for i in pull.items] == ["ig:AAA111"]
    assert pull.sources["tiktok"]["status"] == "skipped"
    assert pull.notices == [
        "Instagram returned no videos for @example_gardener_two",  # the second creator on the list answered nothing
        "TikTok skipped: your cost cap of US$0.20 is below the US$0.50 one TikTok pull needs. "
        "Raise research.cost_cap.tiktok in me/profile.json",
    ]
    assert not [u for u in web.urls("POST") if "tiktok" in u]


@pytest.mark.parametrize("left, expected_cap", [(0.30, 0.25), (0.20, 0.19), (0.06, 0.05)])
def test_a_cap_never_goes_above_what_the_account_has_left(profile, web, left, expected_cap):
    apify(web, ig=[ig_post("AAA111")], left=left)
    scrape.pull(profile, IG_ONLY, token=TOKEN)
    assert charge_cap(web, "instagram-scraper") == pytest.approx(expected_cap)


def test_an_account_with_almost_nothing_left_skips_with_a_plain_sentence(profile, web):
    apify(web, ig=[ig_post("AAA111")], tt=[tt_post("1")], left=0.03)
    pull = scrape.pull(profile, WATCH, token=TOKEN)
    assert pull.items == []
    assert "Instagram skipped: your Apify account has US$0.03 left this month, and an Instagram pull needs US$0.05" in pull.notices
    assert any(n.startswith("TikTok skipped: your Apify account has US$0.03 left") for n in pull.notices)
    assert not web.urls("POST")


def test_an_unknown_balance_does_not_stop_the_run(profile, web):
    apify(web, ig=[ig_post("AAA111")])
    web.routes = [r for r in web.routes if "/users/me/limits" not in r[1]]
    web.add("GET", "/users/me/limits", common.HttpError(401, "HTTP 401"))
    pull = scrape.pull(profile, IG_ONLY, token=TOKEN)
    assert [i["id"] for i in pull.items] == ["ig:AAA111"]
    assert charge_cap(web, "instagram-scraper") == 0.25


# --- Apify, when things go wrong ------------------------------------------------------------------


def test_a_refused_run_is_explained(profile, web):
    apify(web, ig_run=common.HttpError(402, "HTTP 402"))
    pull = scrape.pull(profile, IG_ONLY, token=TOKEN)
    assert pull.notices == ["Instagram skipped: Apify refused the run (HTTP 402). Check your Apify plan and usage"]


def test_an_unreachable_apify_is_explained(profile, web):
    web.add("GET", "/users/me/limits", common.HttpError(None, "no network route"))
    web.add("POST", "/runs", common.HttpError(None, "no network route"))
    pull = scrape.pull(profile, IG_ONLY, token=TOKEN)
    assert pull.notices == ["Instagram skipped: Apify could not be reached (no network route)"]
    assert pull.sources["instagram"]["status"] == "failed"


def test_a_failed_actor_run_is_explained(profile, web):
    apify(web, ig_run=started("run-ig", "ds-ig", "FAILED", 0))
    pull = scrape.pull(profile, IG_ONLY, token=TOKEN)
    assert pull.notices == ["Instagram skipped: the Apify run failed"]


def test_a_run_still_going_at_the_time_limit_is_stopped(profile, web):
    apify(web, ig_run=started("run-ig", "ds-ig", "RUNNING", 0))
    web.add("POST", "/actor-runs/run-ig/abort", {"data": {}})
    pull = scrape.pull(profile, IG_ONLY, token=TOKEN, budget=1.0)
    assert pull.notices == ["Instagram skipped: the Apify run was still going at the time limit, so it was stopped"]
    assert any("/actor-runs/run-ig/abort" in u for u in web.urls("POST"))


def test_a_run_that_finishes_after_a_poll_is_read(profile, web):
    apify(web, ig=[ig_post("AAA111")], ig_run=started("run-ig", "ds-ig", "RUNNING", 0))
    web.add("GET", "/actor-runs/run-ig", {"data": {"id": "run-ig", "status": "SUCCEEDED", "usageTotalUsd": 0.09}})
    pull = scrape.pull(profile, IG_ONLY, token=TOKEN, budget=100.0)
    assert [i["id"] for i in pull.items] == ["ig:AAA111"]
    assert pull.sources["instagram"]["cost_usd"] == pytest.approx(0.09)


def test_one_platform_failing_does_not_stop_the_other(profile, web):
    apify(web, tt=[tt_post("1")], ig_run=common.HttpError(500, "HTTP 500"))
    pull = scrape.pull(profile, WATCH, token=TOKEN)
    assert [i["id"] for i in pull.items] == ["tt:1"]
    assert pull.sources["tiktok"]["status"] == "ok" and pull.sources["instagram"]["status"] == "failed"


def test_a_platform_with_no_creators_on_the_list_is_left_alone(profile, web):
    apify(web, tt=[tt_post("1")])
    pull = scrape.pull(profile, {"instagram": [], "tiktok": ["@example_tiktoker"], "youtube": []}, token=TOKEN)
    assert not [u for u in web.urls("POST") if "instagram" in u]
    assert pull.sources["instagram"]["status"] == "skipped" and pull.notices == []


def test_a_sentence_from_the_kit_ends_a_notice_without_a_second_full_stop(profile, web):
    # Notices end with no full stop, so a kit sentence placed at the end of one gives up its own.
    web.add("GET", "/users/me/limits", config.KitError("The Apify account could not be read."))
    web.add("POST", "/runs", config.KitError("The Apify account could not be read."))
    pull = scrape.pull(profile, IG_ONLY, token=TOKEN)
    assert pull.notices == ["Instagram skipped: The Apify account could not be read"]


def test_no_error_text_ever_carries_the_token(profile, web):
    web.add("GET", "/users/me/limits", RuntimeError(f"boom {TOKEN} https://api.apify.com/v2/x?token={TOKEN}"))
    web.add("POST", "/runs", RuntimeError(f"boom {TOKEN}"))
    pull = scrape.pull(profile, IG_ONLY, token=TOKEN)
    assert pull.notices and all(TOKEN not in n for n in pull.notices)
    assert TOKEN not in repr(pull.sources)


# --- YouTube --------------------------------------------------------------------------------------


@pytest.fixture
def youtube(monkeypatch):
    channels = {
        "example_channel": {
            "vid001": short_info("Basil mistakes", views=9000, likes=700, comments=30, captions="https://cdn.example.com/c1.json3"),
            "vid002": short_info("Compost in a bucket", views=4000, likes=200, comments=9),
        },
        "empty_channel": {},
    }
    monkeypatch.setitem(sys.modules, "yt_dlp", make_yt_dlp(channels, failing=("broken_channel",)))
    return channels


def test_youtube_shorts_come_back_as_plain_videos(profile, web, youtube):
    pull = scrape.pull(profile, {"instagram": [], "tiktok": [], "youtube": ["@example_channel"]}, token=None)
    by_id = {i["id"]: i for i in pull.items}
    assert sorted(by_id) == ["yt:vid001", "yt:vid002"]
    a = by_id["yt:vid001"]
    assert (a["platform"], a["creator"]) == ("youtube", "example_channel")
    assert a["url"] == "https://www.youtube.com/shorts/vid001"
    assert (a["views"], a["likes"], a["comments"]) == (9000, 700, 30)
    assert a["title"] == "Basil mistakes"
    assert a["caption_url"] == "https://cdn.example.com/c1.json3"
    assert by_id["yt:vid002"].get("caption_url") is None
    assert common.parse_iso(a["posted_at"]) is not None
    source = pull.sources["youtube"]
    assert source["status"] == "ok" and source["tool"].startswith("yt-dlp 2026.01.01")
    assert pull.notices == [MISSING]  # YouTube worked, and the missing key is still told
    assert web.calls == []  # yt-dlp does its own talking


def test_youtube_runs_even_when_apify_does_not(profile, web, youtube):
    pull = scrape.pull(profile, {"instagram": [], "tiktok": [], "youtube": ["@example_channel"]}, token=None, use_apify=False)
    assert len(pull.items) == 2


def test_a_youtube_creator_that_fails_is_noted_and_the_rest_carry_on(profile, web, youtube):
    watch = {"instagram": [], "tiktok": [], "youtube": ["@example_channel", "@broken_channel", "@empty_channel"]}
    pull = scrape.pull(profile, watch, token=None)
    assert len(pull.items) == 2
    notes = {c["creator"]: c.get("note") for c in pull.sources["youtube"]["creators"]}
    assert notes["broken_channel"] == "channel not found"  # yt-dlp's 404, put plainly
    assert notes["empty_channel"] == "no Shorts returned"
    assert notes.get("example_channel") is None


def test_youtube_with_nothing_at_all_is_a_failure_not_a_crash(profile, web, youtube):
    pull = scrape.pull(profile, {"instagram": [], "tiktok": [], "youtube": ["@broken_channel"]}, token=None)
    assert pull.items == [] and pull.sources["youtube"]["status"] == "failed"
    assert "YouTube returned no videos this time for @broken_channel (channel not found)" in pull.notices


def test_a_youtube_creator_that_never_answers_does_not_hold_the_run(profile, web, monkeypatch):
    import threading
    import time

    monkeypatch.setattr(scrape, "MIN_WAIT", 0.3)

    release = threading.Event()

    class Slow:
        def __init__(self, options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=False):
            release.wait(30)  # a creator whose page never loads
            raise RuntimeError("gave up")

    module = type(sys)("yt_dlp")
    module.YoutubeDL = Slow
    monkeypatch.setitem(sys.modules, "yt_dlp", module)
    started_at = time.monotonic()
    try:
        pull = scrape.pull(profile, {"instagram": [], "tiktok": [], "youtube": ["@slow_channel"]}, token=None, budget=1.0)
        took = time.monotonic() - started_at
    finally:
        release.set()
    assert took < 5  # the run gave up on the creator and did not wait out the 30 seconds
    assert pull.items == [] and pull.sources["youtube"]["status"] == "failed"
    assert pull.notices == [MISSING, "YouTube returned no videos this time for @slow_channel (did not answer in time)"]


def test_youtube_without_yt_dlp_installed_says_how_to_add_it(profile, web, monkeypatch):
    monkeypatch.setitem(sys.modules, "yt_dlp", None)  # an import of it now fails
    pull = scrape.pull(profile, {"instagram": [], "tiktok": [], "youtube": ["@example_channel"]}, token=None)
    assert pull.items == []
    assert "YouTube skipped: yt-dlp is not installed. Run: uv sync --group research --inexact" in pull.notices


def test_an_empty_youtube_list_never_loads_yt_dlp(profile, web, monkeypatch):
    monkeypatch.setitem(sys.modules, "yt_dlp", None)
    pull = scrape.pull(profile, {"instagram": [], "tiktok": [], "youtube": []}, token=None)
    assert not any("YouTube" in n for n in pull.notices)


# --- the watch list -------------------------------------------------------------------------------


def test_watch_list_entries_are_tidied(example_me):
    (example_me / "watchlist.json").write_text(
        '{"instagram": ["@One", "one", " https://www.instagram.com/two/ ", ""], "tiktok": ["@three"], "youtube": []}',
        encoding="utf-8",
    )
    assert scrape.load_watchlist() == {"instagram": ["One", "two"], "tiktok": ["three"], "youtube": []}


def test_the_example_watch_list_loads(example_me):
    lists = scrape.load_watchlist()
    assert lists["instagram"] == ["example_gardener_one", "example_gardener_two"]
    assert lists["tiktok"] == ["example_tiktoker"] and lists["youtube"] == ["example_channel"]


def test_a_missing_watch_list_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("KIT_ME", str(tmp_path))
    assert scrape.load_watchlist() == {"instagram": [], "tiktok": [], "youtube": []}


def test_a_watch_list_with_the_wrong_shape_is_explained(example_me):
    (example_me / "watchlist.json").write_text('["@one"]', encoding="utf-8")
    with pytest.raises(config.KitError) as caught:
        scrape.load_watchlist()
    assert "me/watchlist.json" in str(caught.value)
    (example_me / "watchlist.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(config.KitError) as caught:
        scrape.load_watchlist()
    assert "not valid JSON" in str(caught.value)
