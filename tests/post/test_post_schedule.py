"""kit.post: scheduling a finished video through Zernio, against the stand-in in fake_zernio.py.

The example creator posts from Europe/London and answers comments on Instagram and Facebook. Every
test uses the `reel` fixture (a folder with a final.mp4 and valid captions) and the `stub` fixture.
"""

from __future__ import annotations

import dataclasses
import json
import stat
import sys
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fake_zernio import default_accounts, make_account

from kit import config, post, zernio
from kit.config import PLATFORMS
from kit.post import captions, payloads
from kit.post.errors import CaptionProblems, PostError
from kit.zernio import Client, NoZernio, ZernioError

KEY = "fake-value-for-tests"
LONDON = ZoneInfo("Europe/London")
# `kit.post.schedule` is the function, so the module has to be fetched by name.
schedule_module = sys.modules["kit.post.schedule"]


def run(reel, stub, platforms, when, **kw):
    return post.schedule(reel.slug, platforms, when, client=stub.client(), **kw)


def receipt(reel, platform: str) -> dict:
    return json.loads((reel.folder / "post" / f"{platform}.json").read_text(encoding="utf-8"))


def by_platform(results):
    return {r.platform: r for r in results}


def post_edits(stub):
    """The PUT calls that edit a post. The video upload is a PUT too, so it is filtered out."""
    return [c for c in stub.calls("PUT") if c.path.startswith("/api/v1/posts/")]


# --- one upload, one post for each platform -----------------------------------------------------------


def test_one_upload_three_platforms(reel, stub, when):
    results = run(reel, stub, ["instagram", "tiktok", "youtube"], when)

    assert [(r.platform, r.status) for r in results] == [
        ("instagram", "scheduled"), ("tiktok", "scheduled"), ("youtube", "scheduled"),
    ]
    assert len(stub.calls("POST", "/api/v1/media/presign")) == 1
    assert len(stub.uploads) == 1 and stub.uploads[0]["bytes"] == reel.video.read_bytes()
    assert len(stub.creates()) == 3
    assert len({c.body["mediaItems"][0]["url"] for c in stub.creates()}) == 1  # all three use the one upload

    titles = {c.body["platforms"][0]["platform"]: c.body["title"] for c in stub.creates()}
    assert titles == {
        "instagram": "Example Studio instagram example-reel",
        "tiktok": "Example Studio tiktok example-reel",
        "youtube": "Example Studio youtube example-reel",
    }
    for create in stub.creates():  # each post has its own caption, time and request id
        assert create.body["scheduledFor"] == when.isoformat()
        assert create.body["timezone"] == "Europe/London"
        assert create.body["isDraft"] is False
    assert len({c.headers["x-request-id"] for c in stub.creates()}) == 3
    assert len(stub.posts) == 3 and all(p["status"] == "scheduled" for p in stub.posts.values())


def test_each_result_names_the_post_and_the_time(reel, stub, when):
    (result,) = run(reel, stub, ["instagram"], when)
    assert result.post_id in stub.posts
    assert result.url is None and result.reason is None
    assert datetime.fromisoformat(result.scheduled_for) == when
    assert result.scheduled_for.endswith(when.isoformat()[-6:])  # in the creator's own offset


def test_payload_fields(reel, stub, when):
    run(reel, stub, list(PLATFORMS), when)
    bodies = {c.body["platforms"][0]["platform"]: c.body for c in stub.creates()}
    data = {name: body["platforms"][0].get("platformSpecificData") for name, body in bodies.items()}

    assert data["instagram"] == {"shareToFeed": True}
    assert 0 < len(data["youtube"]["title"]) <= 100 and data["youtube"]["visibility"] == "public"
    assert data["youtube"]["title"] == "Why windowsill basil dies in week three"
    assert data["facebook"] == {"contentType": "reel"}  # the video is 30 seconds
    assert bodies["tiktok"]["tiktokSettings"]["express_consent_given"] is True
    assert "twitter" in bodies
    assert data["linkedin"] is None and data["twitter"] is None
    assert [name for name, body in bodies.items() if "tiktokSettings" in body] == ["tiktok"]
    assert bodies["instagram"]["content"].endswith("#balconygarden #growyourown #smallspaces #basil #windowsill")
    assert "#" not in bodies["linkedin"]["content"]


def test_a_long_video_goes_to_facebook_as_a_feed_video(reel, stub, when):
    reel.seconds = 75.0
    run(reel, stub, ["facebook"], when)
    (create,) = stub.creates()
    assert "platformSpecificData" not in create.body["platforms"][0]


def test_the_length_is_only_read_when_facebook_is_among_the_platforms(reel, stub, when, monkeypatch):
    def refuse(path):
        raise AssertionError("the length was read for a platform that does not need it")

    monkeypatch.setattr(payloads, "video_seconds", refuse)
    results = run(reel, stub, ["instagram", "linkedin"], when)
    assert [r.status for r in results] == ["scheduled", "scheduled"]


# --- when the connection fails -------------------------------------------------------------------------------


def test_dropped_response_no_double_post(reel, stub, when):
    stub.drop_creates = ["after"]  # Zernio saves the post, then the answer never arrives
    (first,) = run(reel, stub, ["instagram"], when)

    assert first.status == "failed" and "connection" in first.reason
    assert "Run the same command again" in first.reason
    assert len(stub.posts) == 1  # the post exists although the kit never heard so
    saved = receipt(reel, "instagram")
    assert saved["create_attempted"] is True and "post_id" not in saved

    (second,) = run(reel, stub, ["instagram"], when)
    assert second.status == "scheduled"
    (only,) = stub.posts.values()
    assert second.post_id == only["_id"]
    assert len(stub.creates()) == 1  # the rerun found the post by its title and made no second create
    assert stub.calls("GET", "/api/v1/posts")  # that is how it found it
    assert receipt(reel, "instagram")["post_id"] == only["_id"]


def test_a_create_that_never_arrived_is_sent_again_with_the_same_request_id(reel, stub, when):
    stub.drop_creates = ["before"]  # Zernio never saw it
    (first,) = run(reel, stub, ["instagram"], when)
    assert first.status == "failed" and len(stub.posts) == 0

    (second,) = run(reel, stub, ["instagram"], when)
    assert second.status == "scheduled" and len(stub.posts) == 1
    sent = stub.creates()
    assert len(sent) == 2
    assert sent[0].headers["x-request-id"] == sent[1].headers["x-request-id"] == receipt(reel, "instagram")["request_id"]


def test_the_receipt_is_saved_before_the_create_is_sent(reel, stub, when):
    seen = {}

    def peek(request):
        seen["receipt"] = receipt(reel, "instagram")
        seen["header"] = request.headers["x-request-id"]

    stub.before_create = peek
    run(reel, stub, ["instagram"], when)

    assert seen["receipt"]["request_id"] == seen["header"]
    assert seen["receipt"]["create_attempted"] is True and "post_id" not in seen["receipt"]
    assert seen["receipt"]["title"] == "Example Studio instagram example-reel"
    assert receipt(reel, "instagram")["post_id"] in stub.posts


def test_429_then_ok(reel, stub, when):
    stub.rate_limits = 3  # the first three requests are turned away, and none of them was processed
    results = run(reel, stub, ["instagram", "tiktok"], when)
    assert [r.status for r in results] == ["scheduled", "scheduled"]
    assert len(stub.posts) == 2 and len(stub.uploads) == 1


def test_a_refused_create_is_reported_with_zernios_reason_and_forgotten(reel, stub, when):
    stub.reply("POST", r"/api/v1/posts", 400, {"error": "That video is too long for this account"}, times=1)
    results = by_platform(run(reel, stub, ["instagram", "tiktok"], when))

    assert results["instagram"].status == "failed"
    assert "That video is too long for this account" in results["instagram"].reason
    assert KEY not in results["instagram"].reason
    assert results["tiktok"].status == "scheduled"  # one platform's trouble does not stop the others
    assert not (reel.folder / "post" / "instagram.json").exists()  # nothing was made, so nothing is remembered

    assert by_platform(run(reel, stub, ["instagram"], when))["instagram"].status == "scheduled"
    ids = {c.headers["x-request-id"] for c in stub.creates() if c.body["platforms"][0]["platform"] == "instagram"}
    assert len(ids) == 2  # a refused request id is not used again


def test_an_unsure_create_keeps_its_receipt_so_the_next_run_checks_first(reel, stub, when):
    stub.reply("POST", r"/api/v1/posts", 503, {"error": "Try later"})
    (result,) = run(reel, stub, ["instagram"], when)
    assert result.status == "failed" and "Run the same command again" in result.reason
    assert receipt(reel, "instagram")["create_attempted"] is True


def test_a_failed_upload_fails_each_platform_without_trying_again(reel, stub, when):
    stub.reply("PUT", r"/upload/1", 403, b"denied")
    results = run(reel, stub, ["instagram", "tiktok", "youtube"], when)
    assert [r.status for r in results] == ["failed"] * 3
    assert all("Nothing was posted" in r.reason for r in results)
    assert len(stub.calls("POST", "/api/v1/media/presign")) == 1
    assert stub.creates() == []
    assert not (reel.folder / "post" / "media.json").exists()


def test_a_failed_account_lookup_stops_before_anything_is_uploaded(reel, stub, when):
    stub.reply("GET", r"/api/v1/accounts", 401, {"error": "Invalid or missing API key"})
    with pytest.raises(ZernioError, match="ZERNIO_API_KEY"):
        run(reel, stub, ["instagram"], when)
    assert stub.uploads == [] and stub.creates() == []


# --- platforms that are not connected -------------------------------------------------------------------------


def test_unconnected_platform_skipped(reel, stub, when):
    stub.accounts = default_accounts(skip=("tiktok",))
    results = by_platform(run(reel, stub, ["instagram", "tiktok", "youtube"], when))

    assert results["tiktok"].status == "skipped"
    assert results["tiktok"].reason == "not connected in Zernio"
    assert results["tiktok"].post_id is None
    assert results["instagram"].status == results["youtube"].status == "scheduled"
    assert [c.body["platforms"][0]["platform"] for c in stub.creates()] == ["instagram", "youtube"]
    assert not (reel.folder / "post" / "tiktok.json").exists()


@pytest.mark.parametrize(
    "flags", [{"active": False}, {"needs_reconnection": True}, {"enabled": False}], ids=["inactive", "reconnect", "off"]
)
def test_an_account_that_cannot_post_counts_as_not_connected(reel, stub, when, flags):
    stub.accounts = [make_account("instagram", **flags), *default_accounts(skip=("instagram",))]
    (result,) = run(reel, stub, ["instagram"], when)
    assert (result.status, result.reason) == ("skipped", "not connected in Zernio")
    assert stub.uploads == []  # nothing is uploaded when nothing is going anywhere


def test_two_accounts_on_a_platform_use_the_one_that_matches_the_handle(reel, stub, when):
    stub.accounts = [
        make_account("instagram", account_id="acct-other", username="@somebodyelse", display_name="Somebody Else"),
        make_account("instagram", account_id="acct-mine"),
        *default_accounts(skip=("instagram",)),
    ]
    (result,) = run(reel, stub, ["instagram"], when)
    assert result.status == "scheduled"
    assert stub.creates()[0].body["platforms"][0]["accountId"] == "acct-mine"


def test_two_accounts_and_no_match_is_skipped_never_guessed(reel, stub, when):
    stub.accounts = [
        make_account("instagram", account_id="acct-a", username="@first", display_name="First"),
        make_account("instagram", account_id="acct-b", username="@second", display_name="Second"),
    ]
    (result,) = run(reel, stub, ["instagram"], when)
    assert result.status == "skipped" and "more than one" in result.reason
    assert stub.creates() == [] and stub.uploads == []


# --- no key -------------------------------------------------------------------------------------------------------


def test_no_key_manual_files(reel, when):
    with pytest.raises(NoZernio) as caught:  # no key in the environment, no .env, no client given
        post.schedule(reel.slug, ["instagram", "youtube"], when)

    manual = reel.folder / "post" / "manual.md"
    assert manual.is_file()
    assert "post/manual.md" in str(caught.value) and "nothing was scheduled" in str(caught.value)
    text = manual.read_text(encoding="utf-8")
    assert str(reel.video) in text
    for platform in PLATFORMS:  # the file covers every platform in the profile, not just the ones asked for
        assert payloads.content_for(json.loads((reel.folder / "captions.json").read_text())[platform], platform) in text
    assert "Why windowsill basil dies in week three" in text  # the YouTube title
    assert not (reel.folder / "post" / "instagram.json").exists()


def test_the_manual_file_gives_each_platforms_usual_time(reel, profile):
    path = post.write_manual(reel.slug)
    text = path.read_text(encoding="utf-8")
    assert "## Instagram (18:00)" in text and "## LinkedIn (08:30)" in text and "## X (12:00)" in text
    assert "Europe/London" in text
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_the_manual_file_will_not_hold_a_caption_that_breaks_a_rule(reel):
    path = reel.folder / "captions.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["linkedin"]["hashtags"] = ["#basil"]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(CaptionProblems, match="LinkedIn"):
        post.write_manual(reel.slug)
    assert not (reel.folder / "post" / "manual.md").exists()


def test_a_key_in_the_environment_is_used(reel, stub, when, monkeypatch):
    monkeypatch.setenv("ZERNIO_API_KEY", KEY)
    monkeypatch.setattr(Client, "from_env", classmethod(lambda cls, base=zernio.API: stub.client()))
    (result,) = post.schedule(reel.slug, ["instagram"], when)
    assert result.status == "scheduled"


# --- a dry run ----------------------------------------------------------------------------------------------------


def test_dry_run_sends_nothing(reel, stub, when):
    results = run(reel, stub, ["instagram", "tiktok"], when, dry_run=True)
    assert stub.calls() == []  # no upload, no create and not even a read
    assert [(r.platform, r.status) for r in results] == [("instagram", "dry run"), ("tiktok", "dry run")]
    assert all(r.post_id is None and "Nothing was sent" in r.reason for r in results)
    assert datetime.fromisoformat(results[0].scheduled_for) == when
    assert not (reel.folder / "post").exists()  # no receipt, no upload record and no lock file


def test_a_dry_run_needs_no_key_and_still_checks_the_captions(reel, when):
    (result,) = post.schedule(reel.slug, ["instagram"], when, dry_run=True)
    assert result.status == "dry run"

    path = reel.folder / "captions.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["instagram"]["hashtags"] = ["#shorts"]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(CaptionProblems, match="never list"):
        post.schedule(reel.slug, ["instagram"], when, dry_run=True)


# --- running it twice ----------------------------------------------------------------------------------------------


def test_a_second_run_schedules_nothing_new(reel, stub, when):
    first = run(reel, stub, ["instagram", "tiktok"], when)
    second = run(reel, stub, ["instagram", "tiktok"], when)

    assert [r.post_id for r in second] == [r.post_id for r in first]
    assert [r.status for r in second] == ["scheduled", "scheduled"]
    assert all("already scheduled" in r.reason.lower() for r in second)
    assert len(stub.creates()) == 2 and len(stub.uploads) == 1 and len(stub.posts) == 2


def test_a_second_run_at_another_time_says_so_and_changes_nothing(reel, stub, when):
    run(reel, stub, ["instagram"], when)
    (again,) = run(reel, stub, ["instagram"], when + timedelta(days=1))
    assert again.status == "scheduled" and "instead" in again.reason and "reschedule" in again.reason
    assert datetime.fromisoformat(again.scheduled_for) == when  # still the first time
    assert len(stub.creates()) == 1


def test_a_run_that_stopped_part_way_finishes_the_rest(reel, stub, when):
    stub.reply("POST", r"/api/v1/posts", 503, {"error": "Try later"})
    first = run(reel, stub, ["instagram", "tiktok"], when)
    assert [r.status for r in first] == ["failed", "scheduled"]
    stub.seed_post("unrelated")  # other posts in the list do not confuse the search

    second = run(reel, stub, ["instagram", "tiktok"], when)
    assert [r.status for r in second] == ["scheduled", "scheduled"]
    assert len(stub.posts) == 3  # the two for this video, plus the unrelated one
    assert len(stub.uploads) == 1


def test_the_upload_is_reused_across_runs_until_the_video_changes(reel, stub, when):
    run(reel, stub, ["instagram"], when)
    run(reel, stub, ["tiktok"], when)
    assert len(stub.uploads) == 1
    record = json.loads((reel.folder / "post" / "media.json").read_text(encoding="utf-8"))
    assert record["url"] == stub.creates()[0].body["mediaItems"][0]["url"]
    assert record["path"] == "final.mp4" and record["size"] == reel.video.stat().st_size

    reel.video.write_bytes(b"a new cut of the video " * 40)
    run(reel, stub, ["youtube"], when)
    assert len(stub.uploads) == 2
    assert stub.creates()[-1].body["mediaItems"][0]["url"] != stub.creates()[0].body["mediaItems"][0]["url"]


def test_a_post_that_zernio_no_longer_has_is_made_again(reel, stub, when):
    (first,) = run(reel, stub, ["instagram"], when)
    stub.posts.clear()  # deleted from Zernio's own dashboard
    (second,) = run(reel, stub, ["instagram"], when)

    assert second.status == "scheduled" and second.post_id != first.post_id
    assert receipt(reel, "instagram")["post_id"] == second.post_id
    assert (reel.folder / "post" / "instagram.stale.json").is_file()


def test_losing_the_receipt_does_not_make_a_second_post(reel, stub, when):
    (first,) = run(reel, stub, ["instagram"], when)
    (reel.folder / "post" / "instagram.json").unlink()  # the creator tidied the work folder
    (second,) = run(reel, stub, ["instagram"], when)

    assert second.status == "scheduled" and second.post_id == first.post_id  # Zernio named the post it already had
    assert len(stub.posts) == 1


def test_a_look_alike_post_that_is_not_ours_is_left_alone(reel, stub, when):
    run(reel, stub, ["instagram"], when)
    (ours,) = stub.posts.values()
    ours["title"] = "Something somebody else made"
    (reel.folder / "post" / "instagram.json").unlink()
    (result,) = run(reel, stub, ["instagram"], when)

    assert result.status == "failed" and "Duplicate post" in result.reason
    assert len(stub.posts) == 1 and ours["title"] == "Something somebody else made"
    assert not (reel.folder / "post" / "instagram.json").exists()


# --- the time ------------------------------------------------------------------------------------------------------


def test_a_time_with_no_zone_is_read_in_the_creators_zone(reel, stub):
    summer = datetime(2099, 7, 1, 18, 0)
    winter = datetime(2099, 12, 1, 18, 0)
    run(reel, stub, ["instagram"], summer)
    run(reel, stub, ["tiktok"], winter)
    sent = {c.body["platforms"][0]["platform"]: c.body for c in stub.creates()}
    assert sent["instagram"]["scheduledFor"] == "2099-07-01T18:00:00+01:00"
    assert sent["tiktok"]["scheduledFor"] == "2099-12-01T18:00:00+00:00"
    assert {b["timezone"] for b in sent.values()} == {"Europe/London"}


def test_a_time_in_another_zone_is_sent_as_the_same_moment(reel, stub, when):
    in_tokyo = when.astimezone(ZoneInfo("Asia/Tokyo"))
    (result,) = run(reel, stub, ["instagram"], in_tokyo)
    (create,) = stub.creates()
    assert datetime.fromisoformat(create.body["scheduledFor"]) == when
    assert create.body["scheduledFor"] == when.astimezone(LONDON).isoformat()
    assert datetime.fromisoformat(result.scheduled_for) == when


def test_a_time_that_has_passed_is_refused_because_zernio_would_post_at_once(reel, stub):
    for moment in (datetime.now(LONDON) - timedelta(hours=1), datetime.now(LONDON) + timedelta(minutes=2)):
        with pytest.raises(PostError, match="passed"):
            run(reel, stub, ["instagram"], moment)
    assert stub.calls() == []


def test_the_time_is_checked_again_after_the_upload(reel, stub, when, monkeypatch):
    clock = {"now": datetime.now(timezone.utc)}
    monkeypatch.setattr(schedule_module, "_now", lambda: clock["now"])
    real_upload = Client.upload

    def slow_upload(self, path):
        url = real_upload(self, path)
        clock["now"] = when.astimezone(timezone.utc) - timedelta(seconds=20)  # the upload took until almost then
        return url

    monkeypatch.setattr(Client, "upload", slow_upload)
    (result,) = run(reel, stub, ["instagram"], when)
    assert result.status == "failed" and "passed" in result.reason
    assert stub.creates() == []  # nothing was sent that Zernio would have published at once


def test_the_usual_time_comes_from_the_profile(profile):
    moment = post.default_when(profile, "linkedin", date(2099, 7, 1))
    assert moment.isoformat() == "2099-07-01T08:30:00+01:00"
    assert post.default_when(profile, "facebook", date(2099, 12, 1)).isoformat() == "2099-12-01T18:30:00+00:00"
    bare = dataclasses.replace(profile, posting_times={"instagram": "18:00"})
    with pytest.raises(PostError, match="TikTok"):
        post.default_when(bare, "tiktok", date(2099, 7, 1))


# --- looking at, moving and cancelling what was scheduled ---------------------------------------------------------------


def test_list_reschedule_and_cancel(reel, stub, when):
    run(reel, stub, ["instagram", "tiktok"], when)

    listed = post.list_scheduled(reel.slug, client=stub.client())
    assert [(r.platform, r.status) for r in listed] == [("instagram", "scheduled"), ("tiktok", "scheduled")]
    assert all(datetime.fromisoformat(r.scheduled_for) == when for r in listed)

    later = when + timedelta(days=2, hours=1)
    moved = post.reschedule(reel.slug, "instagram", later, client=stub.client())
    assert moved.status == "scheduled" and datetime.fromisoformat(moved.scheduled_for) == later
    (put,) = post_edits(stub)
    assert put.body == {"isDraft": False, "scheduledFor": later.astimezone(LONDON).isoformat(), "timezone": "Europe/London"}
    assert receipt(reel, "instagram")["scheduled_for"] == later.astimezone(LONDON).isoformat()
    assert datetime.fromisoformat(stub.posts[moved.post_id]["scheduledFor"].replace("Z", "+00:00")) == later

    gone = post.cancel(reel.slug, "tiktok", client=stub.client())
    assert gone.status == "cancelled" and gone.post_id not in stub.posts
    assert not (reel.folder / "post" / "tiktok.json").exists()
    assert (reel.folder / "post" / "tiktok.cancelled.json").is_file()  # the receipt is kept, renamed
    assert [r.platform for r in post.list_scheduled(reel.slug, client=stub.client())] == ["instagram"]

    nothing = post.cancel(reel.slug, "tiktok", client=stub.client())
    assert nothing.status == "skipped" and "Nothing is scheduled" in nothing.reason

    again = run(reel, stub, ["tiktok"], when)[0]  # after a cancel, scheduling it again is a fresh post
    assert again.status == "scheduled" and again.post_id != gone.post_id


def test_a_post_that_has_gone_out_cannot_be_cancelled_or_moved(reel, stub, when):
    (result,) = run(reel, stub, ["instagram"], when)
    stub.posts[result.post_id]["status"] = "published"
    stub.posts[result.post_id]["platforms"][0]["status"] = "published"
    stub.posts[result.post_id]["platforms"][0]["platformPostUrl"] = "https://social.example.test/instagram/1"

    cancelled = post.cancel(reel.slug, "instagram", client=stub.client())
    assert cancelled.status == "failed" and "already" in cancelled.reason
    assert cancelled.url == "https://social.example.test/instagram/1"
    assert stub.calls("DELETE") == []
    assert (reel.folder / "post" / "instagram.json").is_file()

    moved = post.reschedule(reel.slug, "instagram", when + timedelta(days=1), client=stub.client())
    assert moved.status == "failed" and "already" in moved.reason
    assert post_edits(stub) == []


def test_a_failed_post_has_to_be_cancelled_before_it_is_scheduled_again(reel, stub, when):
    (result,) = run(reel, stub, ["instagram"], when)
    stub.posts[result.post_id]["status"] = "failed"
    stub.posts[result.post_id]["platforms"][0]["errorMessage"] = "The account needs to be reconnected"

    (listed,) = post.list_scheduled(reel.slug, client=stub.client())
    assert listed.status == "failed" and listed.reason == "The account needs to be reconnected"

    (again,) = run(reel, stub, ["instagram"], when)
    assert again.status == "failed" and "cancel" in again.reason.lower()
    assert len(stub.creates()) == 1

    moved = post.reschedule(reel.slug, "instagram", when + timedelta(days=1), client=stub.client())
    assert moved.status == "failed" and "cancel" in moved.reason.lower()

    assert post.cancel(reel.slug, "instagram", client=stub.client()).status == "cancelled"
    assert run(reel, stub, ["instagram"], when)[0].status == "scheduled"


def test_list_shows_what_zernio_reports(reel, stub, when):
    run(reel, stub, ["instagram", "tiktok", "youtube", "facebook"], when)
    ids = {r.platform: r.post_id for r in post.list_scheduled(reel.slug, client=stub.client())}
    published = stub.posts[ids["instagram"]]
    published["status"] = "published"
    published["platforms"][0].update(status="published", platformPostUrl="https://social.example.test/instagram/9")
    stub.posts[ids["tiktok"]]["status"] = "publishing"
    stub.posts[ids["youtube"]]["status"] = "draft"
    del stub.posts[ids["facebook"]]

    results = by_platform(post.list_scheduled(reel.slug, client=stub.client()))
    assert (results["instagram"].status, results["instagram"].url) == ("published", "https://social.example.test/instagram/9")
    assert results["tiktok"].status == "scheduled" and "publishing" in results["tiktok"].reason
    assert results["youtube"].status == "unknown" and "draft" in results["youtube"].reason
    assert results["facebook"].status == "cancelled" and "no longer has" in results["facebook"].reason


def test_list_notes_a_create_that_never_finished(reel, stub, when):
    stub.drop_creates = ["before"]
    run(reel, stub, ["instagram"], when)
    (listed,) = post.list_scheduled(reel.slug, client=stub.client())
    assert listed.status == "unknown" and "interrupted" in listed.reason


def test_list_needs_no_key_when_nothing_was_scheduled(reel):
    assert post.list_scheduled(reel.slug) == []
    assert post.cancel(reel.slug, "instagram").status == "skipped"


def test_list_cancel_and_reschedule_need_the_key_once_there_is_something_scheduled(reel, stub, when):
    run(reel, stub, ["instagram"], when)
    with pytest.raises(NoZernio):
        post.list_scheduled(reel.slug)
    with pytest.raises(NoZernio):
        post.cancel(reel.slug, "instagram")
    with pytest.raises(NoZernio):
        post.reschedule(reel.slug, "instagram", when + timedelta(days=1))


def test_reschedule_needs_a_future_time_and_something_scheduled(reel, stub, when):
    assert post.reschedule(reel.slug, "instagram", when, client=stub.client()).status == "skipped"
    run(reel, stub, ["instagram"], when)
    with pytest.raises(PostError, match="passed"):
        post.reschedule(reel.slug, "instagram", datetime.now(LONDON) - timedelta(hours=1), client=stub.client())


# --- what is checked before anything is sent -----------------------------------------------------------------------------


def test_captions_that_break_a_rule_stop_everything(reel, stub, when):
    path = reel.folder / "captions.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["linkedin"]["body"] = "Basil dies in week three. Read it at https://example.com/basil\n\nFollow Example Studio for more."
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(CaptionProblems, match="LinkedIn") as caught:
        run(reel, stub, ["instagram", "linkedin"], when)
    assert len(caught.value.problems) == 1
    assert stub.calls() == []
    assert run(reel, stub, ["instagram"], when)[0].status == "scheduled"  # a platform not being sent is not checked


@pytest.mark.parametrize("bad", ["mastodon", "", "insta gram"])
def test_a_platform_the_kit_does_not_post_to_is_refused(reel, stub, when, bad):
    with pytest.raises(PostError, match="not a platform"):
        run(reel, stub, [bad], when)
    assert stub.calls() == []


def test_a_platform_outside_the_profile_is_refused(reel, stub, when, example_me):
    data = json.loads((example_me / "profile.json").read_text(encoding="utf-8"))
    data["platforms"] = ["instagram", "youtube"]
    data["cta"]["comment_platforms"] = ["instagram"]
    for gone in ("tiktok", "facebook", "linkedin", "twitter"):
        del data["handles"][gone]
        data["posting_times"].pop(gone, None)
    (example_me / "profile.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(PostError, match="not one of the platforms in your profile"):
        run(reel, stub, ["tiktok"], when)


def test_x_can_be_asked_for_by_its_short_name_and_a_list_is_tidied(reel, stub, when):
    results = run(reel, stub, ["X", "instagram", "x", "Instagram"], when)
    assert [r.platform for r in results] == ["twitter", "instagram"]


def test_no_platforms_means_all_of_them(reel, stub, when):
    assert [r.platform for r in run(reel, stub, None, when)] == list(config.load_profile().platforms)


def test_a_missing_video_or_captions_is_a_plain_sentence(reel, stub, when):
    reel.video.unlink()
    with pytest.raises(PostError, match="final.mp4"):
        run(reel, stub, ["instagram"], when)
    reel.video.write_bytes(b"bytes")
    (reel.folder / "captions.json").unlink()
    with pytest.raises(PostError, match="no captions.json"):
        run(reel, stub, ["instagram"], when)
    assert stub.calls() == []


def test_an_unsafe_video_name_is_refused(studio, stub, when):
    with pytest.raises(ValueError, match="cannot be used as a video name"):
        post.schedule("../elsewhere", ["instagram"], when, client=stub.client())


def test_a_damaged_receipt_stops_that_platform_not_the_whole_run(reel, stub, when):
    (reel.folder / "post").mkdir()
    (reel.folder / "post" / "instagram.json").write_text("{damaged", encoding="utf-8")
    results = by_platform(run(reel, stub, ["instagram", "tiktok"], when))

    assert results["instagram"].status == "failed" and "damaged" in results["instagram"].reason
    assert results["tiktok"].status == "scheduled"
    assert [c.body["platforms"][0]["platform"] for c in stub.creates()] == ["tiktok"]  # nothing that might repeat a post
    assert (reel.folder / "post" / "instagram.json").read_text(encoding="utf-8") == "{damaged"  # left for the creator


# --- accounts_check --------------------------------------------------------------------------------------------------------


def test_accounts_check_names_each_platform(stub):
    stub.accounts = default_accounts(skip=("tiktok", "twitter"))
    found = post.accounts_check(client=stub.client())
    assert found == {
        "instagram": "connected", "tiktok": "not connected", "youtube": "connected",
        "facebook": "connected", "linkedin": "connected", "twitter": "not connected",
    }
    assert list(found) == list(PLATFORMS)
    (call,) = stub.calls()
    assert (call.method, call.path, call.query) == ("GET", "/api/v1/accounts", {})  # one read-only call


def test_accounts_check_works_before_setup_has_finished(stub, tmp_path, monkeypatch):
    monkeypatch.setenv("KIT_ME", str(tmp_path / "no-profile-yet"))
    assert set(post.accounts_check(client=stub.client()).values()) == {"connected"}


def test_accounts_check_without_a_key_says_so(studio):
    with pytest.raises(NoZernio, match="ZERNIO_API_KEY"):
        post.accounts_check()


def test_accounts_check_shows_nothing_but_the_status(stub):
    stub.accounts = [make_account("instagram", username="@private-handle", account_id="acct-private-id")]
    shown = repr(post.accounts_check(client=stub.client()))
    assert "private" not in shown and KEY not in shown


# --- the shape of the package ----------------------------------------------------------------------------------------------


def test_the_public_names():
    assert callable(post.schedule) and callable(post.list_scheduled) and callable(post.reschedule)
    assert callable(post.cancel) and callable(post.accounts_check) and callable(post.default_when)
    assert post.NoZernio is zernio.NoZernio and post.ZernioError is zernio.ZernioError
    assert post.captions is captions and post.payloads is payloads
    assert schedule_module.schedule is post.schedule  # the package's `schedule` is the function


def test_a_result_is_five_plain_fields_and_a_time():
    result = post.Result("instagram", "scheduled", "post-1", None, None)
    assert dataclasses.astuple(result) == ("instagram", "scheduled", "post-1", None, None, None)
    assert [f.name for f in dataclasses.fields(result)] == [
        "platform", "status", "post_id", "url", "reason", "scheduled_for",
    ]
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.status = "failed"


# --- what is written to disk ------------------------------------------------------------------------------------------------


def test_records_are_private_and_never_hold_the_key(reel, stub, when):
    run(reel, stub, ["instagram", "tiktok"], when)
    post_dir = reel.folder / "post"
    files = [p for p in post_dir.iterdir() if p.suffix == ".json"]
    assert {p.name for p in files} == {"instagram.json", "tiktok.json", "media.json"}
    for path in files:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    everything = "".join(p.read_text(encoding="utf-8", errors="ignore") for p in reel.folder.rglob("*") if p.is_file())
    assert KEY not in everything
    assert not [p for p in post_dir.iterdir() if p.name.endswith(".tmp")]


# --- odd answers from Zernio ---------------------------------------------------------------------------------------------------


def test_a_video_name_with_no_folder_is_one_sentence(studio, stub, when):
    for call in (
        lambda: post.schedule("typo-reel", ["instagram"], when, client=stub.client()),
        lambda: post.list_scheduled("typo-reel", client=stub.client()),
        lambda: post.cancel("typo-reel", "instagram", client=stub.client()),
        lambda: post.reschedule("typo-reel", "instagram", when, client=stub.client()),
        lambda: post.write_manual("typo-reel"),
    ):
        with pytest.raises(PostError, match="There is no video called typo-reel"):
            call()
    assert stub.calls() == []


def test_a_refused_move_or_cancel_is_reported_with_zernios_reason(reel, stub, when):
    run(reel, stub, ["instagram"], when)
    stub.reply("PUT", r"/api/v1/posts/.*", 400, {"error": "That post cannot be edited right now"})
    moved = post.reschedule(reel.slug, "instagram", when + timedelta(days=1), client=stub.client())
    assert moved.status == "failed" and "cannot be edited right now" in moved.reason

    stub.reply("DELETE", r"/api/v1/posts/.*", 400, {"error": "Zernio is busy with that post"})
    kept = post.cancel(reel.slug, "instagram", client=stub.client())
    assert kept.status == "failed" and "busy with that post" in kept.reason
    assert (reel.folder / "post" / "instagram.json").is_file()  # still scheduled, so still remembered


def test_an_answer_that_names_no_post_keeps_the_receipt_so_the_next_run_looks(reel, stub, when):
    stub.reply("POST", r"/api/v1/posts", 201, {"post": {}})
    (result,) = run(reel, stub, ["instagram"], when)
    assert result.status == "failed" and "did not say which post" in result.reason and "again" in result.reason
    assert receipt(reel, "instagram")["create_attempted"] is True


def test_a_post_that_cannot_be_read_back_is_reported_as_unsure_not_failed(reel, stub, when):
    stub.reply("POST", r"/api/v1/posts", 202, {"postId": "post-not-there-yet"})
    (result,) = run(reel, stub, ["instagram"], when)
    assert result.status == "unknown" and result.post_id == "post-not-there-yet"
    assert "could not read it back" in result.reason
    assert receipt(reel, "instagram")["post_id"] == "post-not-there-yet"  # it is remembered, not repeated


def test_a_post_zernio_set_for_another_time_is_not_reported_as_scheduled(reel, stub, when, monkeypatch):
    real = Client.get_post

    def an_hour_late(self, post_id):
        found = dict(real(self, post_id))
        found["scheduledFor"] = (when + timedelta(hours=1)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        return found

    monkeypatch.setattr(Client, "get_post", an_hour_late)
    (result,) = run(reel, stub, ["instagram"], when)
    assert result.status == "failed" and "Zernio set it for" in result.reason


def test_a_post_that_went_out_at_once_is_reported_as_published_never_as_scheduled(reel, stub, when, monkeypatch):
    real = Client.get_post

    def already_out(self, post_id):
        found = dict(real(self, post_id))
        found["status"] = "published"
        found["platforms"] = [{"platform": "instagram", "status": "published", "platformPostUrl": "https://social.example.test/x"}]
        return found

    monkeypatch.setattr(Client, "get_post", already_out)
    (result,) = run(reel, stub, ["instagram"], when)
    assert (result.status, result.url) == ("published", "https://social.example.test/x")
    assert "at once" in result.reason


def test_a_video_whose_length_cannot_be_read_fails_facebook_alone(reel, stub, when, monkeypatch):
    def unreadable(path):
        raise PostError("The length of final.mp4 could not be read. Check that it plays and try again.")

    monkeypatch.setattr(payloads, "video_seconds", unreadable)
    results = by_platform(run(reel, stub, ["facebook", "instagram"], when))
    assert results["facebook"].status == "failed" and "length" in results["facebook"].reason
    assert results["instagram"].status == "scheduled"
    assert not (reel.folder / "post" / "facebook.json").exists()  # nothing was sent, so nothing is remembered


def test_a_run_that_posts_nothing_leaves_no_post_folder_behind(reel, stub, when):
    # `next` takes a post/ folder to mean the video went out, so skipped and refused runs must not make one
    stub.accounts = []
    results = run(reel, stub, ["instagram", "tiktok"], when)
    assert [r.status for r in results] == ["skipped", "skipped"]
    assert not (reel.folder / "post").exists()

    stub.accounts = default_accounts()
    stub.reply("POST", r"/api/v1/posts", 400, {"error": "No."}, times=2)
    assert [r.status for r in run(reel, stub, ["instagram", "tiktok"], when)] == ["failed", "failed"]
    assert {p.name for p in (reel.folder / "post").glob("*.json")} == {"media.json"}  # only the upload is remembered
