"""kit.post: scheduling a finished video through Zernio, against the stand-in in fake_zernio.py.

The example creator posts from Europe/London and answers comments on Instagram and Facebook. Every
test uses the `reel` fixture (a folder with a final.mp4 and valid captions) and the `stub` fixture.
"""

from __future__ import annotations

import dataclasses
import json
import stat
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fake_zernio import default_accounts, make_account

from kit import config, post, zernio
from kit.config import PLATFORMS
from kit.post import captions, payloads, store
from kit.post.errors import CaptionProblems, PostError
from kit.zernio import Client, NoZernio, ZernioError

KEY = "fake-value-for-tests"
LONDON = ZoneInfo("Europe/London")
# `kit.post.schedule` is the function, so the module has to be fetched by name.
schedule_module = sys.modules["kit.post.schedule"]


def run(reel, stub, platforms, when, *, approve=True, **kw):
    """Schedule through the stand-in. The captions are approved first, as the skill does after the creator says yes."""
    if approve and not kw.get("dry_run"):
        captions.approve(reel.slug)
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
    captions.approve(reel.slug)
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


def test_with_no_key_the_manual_file_keeps_the_time_the_creator_asked_for(reel, when):
    """Asked for 21:15 tomorrow, the file posted by hand says 21:15 on that day, not each platform's usual time.
    Platforms that were not asked for keep their usual time, with no date."""
    asked = when.replace(hour=21, minute=15)
    captions.approve(reel.slug)
    with pytest.raises(NoZernio):
        post.schedule(reel.slug, ["instagram", "youtube"], asked)
    text = (reel.folder / "post" / "manual.md").read_text(encoding="utf-8")
    day = f"{asked:%a %d %b}"
    assert f"## Instagram ({day} 21:15)" in text and f"## YouTube ({day} 21:15)" in text, text
    assert "## TikTok (19:00)" in text and "## LinkedIn (08:30)" in text, text
    assert "Europe/London" in text


def test_the_manual_file_will_not_hold_a_caption_that_breaks_a_rule(reel):
    path = reel.folder / "captions.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["linkedin"]["hashtags"] = ["#basil"]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(CaptionProblems, match="LinkedIn"):
        post.write_manual(reel.slug)
    assert not (reel.folder / "post" / "manual.md").exists()


def test_the_key_in_dotenv_is_used_and_one_only_in_the_environment_is_not(reel, stub, when, monkeypatch):
    monkeypatch.setenv("ZERNIO_API_KEY", KEY)  # an agent's shell can carry a key the creator never gave the kit
    captions.approve(reel.slug)
    with pytest.raises(NoZernio):
        post.schedule(reel.slug, ["instagram"], when)
    (config.kit_root() / ".env").write_text(f"ZERNIO_API_KEY={KEY}\n", encoding="utf-8")
    assert Client.from_env().key == KEY
    monkeypatch.setattr(Client, "from_env", classmethod(lambda cls, base=zernio.API: stub.client()))
    captions.approve(reel.slug)
    (result,) = post.schedule(reel.slug, ["instagram"], when)
    assert result.status == "scheduled"


# --- a dry run ----------------------------------------------------------------------------------------------------


def test_dry_run_sends_nothing(reel, stub, when):
    assert not (reel.folder / "captions.approved.json").exists()  # a dry run needs no approval
    results = run(reel, stub, ["instagram", "tiktok"], when, dry_run=True)
    assert stub.calls() == []  # no upload, no create and not even a read
    assert [(r.platform, r.status) for r in results] == [("instagram", "dry run"), ("tiktok", "dry run")]
    assert all(r.post_id is None and "Nothing was sent" in r.reason for r in results)
    assert datetime.fromisoformat(results[0].scheduled_for) == when
    assert not (reel.folder / "post").exists()  # no receipt, no upload record and no lock file


def test_a_dry_run_carries_each_platforms_post_as_it_would_go_out(reel, stub, when):
    """The post each platform would get: the text with its tags, the YouTube title, the TikTok settings and the
    time. The account and the uploaded video's address only exist in Zernio, so plain placeholders stand in."""
    results = run(reel, stub, ["instagram", "tiktok", "youtube", "facebook", "linkedin"], when, dry_run=True)
    assert stub.calls() == []
    data = json.loads((reel.folder / "captions.json").read_text(encoding="utf-8"))
    sent = {r.platform: r.payload for r in results}
    for name, body in sent.items():
        assert body["content"] == payloads.content_for(data[name], name)
        assert datetime.fromisoformat(body["scheduledFor"]) == when and body["timezone"] == "Europe/London"
        assert body["platforms"][0]["accountId"].startswith("<") and body["mediaItems"][0]["url"].startswith("<")
    assert sent["youtube"]["platforms"][0]["platformSpecificData"]["title"] == "Why windowsill basil dies in week three"
    assert sent["tiktok"]["tiktokSettings"] == payloads.TIKTOK_SETTINGS
    assert sent["facebook"]["platforms"][0]["platformSpecificData"] == {"contentType": "reel"}
    assert "#" not in sent["linkedin"]["content"]


def test_a_dry_run_with_a_video_it_cannot_measure_fails_facebook_alone(reel, stub, when, monkeypatch):
    """Facebook needs the video's length; the other platforms do not. A length that cannot be read fails Facebook
    with the reason, as a real post would, and the rest of the plan still comes back."""
    def cannot_measure(path):
        raise PostError("The length of final.mp4 could not be read. Check that it plays and try again.")

    monkeypatch.setattr(payloads, "video_seconds", cannot_measure)
    results = by_platform(run(reel, stub, ["instagram", "facebook"], when, dry_run=True))
    assert stub.calls() == []
    assert results["instagram"].status == "dry run" and results["instagram"].payload["content"]
    assert results["facebook"].status == "failed" and results["facebook"].payload is None
    assert "could not be read" in results["facebook"].reason


def test_a_result_without_a_payload_is_the_same_as_before():
    plain = post.Result("instagram", "scheduled", post_id="post-1")
    assert plain.payload is None
    assert plain == post.Result("instagram", "scheduled", post_id="post-1", payload={"content": "text"})
    assert hash(post.Result("instagram", "dry run", payload={"content": "text"}))


def test_a_dry_run_needs_no_key_and_still_checks_the_captions(reel, when):
    (result,) = post.schedule(reel.slug, ["instagram"], when, dry_run=True)
    assert result.status == "dry run"

    path = reel.folder / "captions.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["instagram"]["hashtags"] = ["#shorts"]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(CaptionProblems, match="never list"):
        post.schedule(reel.slug, ["instagram"], when, dry_run=True)


# --- the creator's yes is checked here, not only by the skill ----------------------------------------------------------


def test_schedule_refuses_to_send_until_the_creator_has_said_yes(reel, stub, when):
    # the case that was probed: no approval on record, one platform, a client ready to take the request
    with pytest.raises(PostError, match="not been approved") as caught:
        post.schedule(reel.slug, ["tiktok"], when, client=stub.client())
    assert f"approve {reel.slug}" in str(caught.value)
    assert stub.calls() == []  # no lookup, no upload, no create
    assert not (reel.folder / "post").exists()


def test_a_caption_changed_after_the_yes_needs_a_new_yes(reel, stub, when):
    captions.approve(reel.slug)
    path = reel.folder / "captions.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["tiktok"]["body"] = data["tiktok"]["body"].replace("one thing", "one mistake")
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(PostError, match="changed after"):
        post.schedule(reel.slug, ["instagram", "tiktok"], when, client=stub.client())
    assert stub.calls() == []

    captions.approve(reel.slug)  # the creator read the new words and said yes
    assert [r.status for r in post.schedule(reel.slug, ["instagram", "tiktok"], when, client=stub.client())] == [
        "scheduled", "scheduled",
    ]


def test_the_approval_is_checked_against_the_words_that_will_actually_go_out(reel, stub, when, monkeypatch):
    # if the file changed between schedule() reading it and checking the approval, a second reading would pass
    captions.approve(reel.slug)
    real, reads = captions.load, {"n": 0}

    def edited_on_the_first_read(slug):
        data = real(slug)
        reads["n"] += 1
        if reads["n"] == 1:
            data["tiktok"]["body"] += " Sneaked in."
        return data

    monkeypatch.setattr(captions, "load", edited_on_the_first_read)
    with pytest.raises(PostError, match="changed after"):
        post.schedule(reel.slug, ["tiktok"], when, client=stub.client())
    assert stub.calls() == []


def test_no_file_for_posting_by_hand_is_written_for_captions_nobody_approved(reel, when):
    with pytest.raises(PostError, match="not been approved"):
        post.schedule(reel.slug, ["instagram"], when)  # no key either
    assert not (reel.folder / "post").exists()


def test_moving_or_cancelling_a_post_needs_no_new_yes_because_no_words_go_out(reel, stub, when):
    run(reel, stub, ["instagram", "tiktok"], when)
    path = reel.folder / "captions.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["tiktok"]["body"] += " A later edit."  # the approval no longer matches the file
    path.write_text(json.dumps(data), encoding="utf-8")
    assert post.reschedule(reel.slug, "instagram", when + timedelta(days=1), client=stub.client()).status == "scheduled"
    assert post.cancel(reel.slug, "tiktok", client=stub.client()).status == "cancelled"
    assert [r.status for r in post.list_scheduled(reel.slug, client=stub.client())] == ["scheduled"]


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


# --- two runs at once ------------------------------------------------------------------------------------------------


def test_the_lock_says_so_only_when_it_has_to_wait(tmp_path, capsys):
    lock = tmp_path / ".post.lock"
    with store.locked(lock, "Waiting for another run."):
        pass
    assert capsys.readouterr().err == ""  # nobody held it, so nothing is said

    said = []
    inside, release = threading.Event(), threading.Event()

    def holder():
        with store.locked(lock):
            inside.set()
            release.wait(10)

    thread = threading.Thread(target=holder)
    thread.start()
    try:
        assert inside.wait(10)

        def waiter():
            with store.locked(lock, "Waiting for another run."):
                said.append("got it")

        second = threading.Thread(target=waiter)
        second.start()
        err = ""
        for _ in range(500):
            err += capsys.readouterr().err
            if "Waiting for another run." in err:
                break
            time.sleep(0.01)
        assert said == [] and err.count("Waiting for another run.") == 1  # one line, and still waiting
    finally:
        release.set()
    thread.join(10)
    second.join(10)
    assert said == ["got it"]


def test_two_runs_for_one_video_take_turns_and_the_second_says_it_is_waiting(reel, stub, when, capsys):
    inside, release = threading.Event(), threading.Event()

    def hold(request):  # the first run's create is held at the stand-in, with the video's lock still on
        inside.set()
        assert release.wait(10)

    stub.before_create = hold
    outcome = {}
    first = threading.Thread(target=lambda: outcome.update(first=run(reel, stub, ["instagram"], when)))
    first.start()
    try:
        assert inside.wait(10)
        stub.before_create = None  # a second create, if it ever got that far, would not be held

        second = threading.Thread(target=lambda: outcome.update(second=run(reel, stub, ["instagram"], when)))
        second.start()
        err = ""
        for _ in range(500):
            err += capsys.readouterr().err
            if "Waiting for it to finish" in err:
                break
            time.sleep(0.01)
        assert err.count("Another run is posting this video. Waiting for it to finish.") == 1
        assert len(stub.creates()) == 1 and "second" not in outcome  # the second run has sent nothing and is still waiting
    finally:
        release.set()
    first.join(10)
    second.join(10)
    assert [r.status for r in outcome["first"]] == ["scheduled"]
    (after,) = outcome["second"]
    assert after.status == "scheduled" and after.post_id == outcome["first"][0].post_id
    assert after.reason == "Already scheduled from an earlier run."
    assert len(stub.creates()) == 1 and len(stub.posts) == 1  # one post, made once


def test_cancel_waits_its_turn_too(reel, stub, when, capsys):
    run(reel, stub, ["instagram"], when)
    capsys.readouterr()
    done = {}
    with store.locked(reel.folder / ".post.lock"):
        thread = threading.Thread(target=lambda: done.update(result=post.cancel(reel.slug, "instagram", client=stub.client())))
        thread.start()
        err = ""
        for _ in range(500):
            err += capsys.readouterr().err
            if "Waiting for it to finish" in err:
                break
            time.sleep(0.01)
        assert "Another run is posting this video." in err and "result" not in done
    thread.join(10)
    assert done["result"].status == "cancelled"


def test_list_waits_its_turn_when_it_has_to_search_for_an_interrupted_post(reel, stub, when, capsys):
    # The search keeps the post it finds in the record, so it takes the video's lock, as schedule, cancel and
    # reschedule do. Another run could be writing the same record.
    stub.drop_creates = ["after"]
    run(reel, stub, ["instagram"], when)
    capsys.readouterr()
    done = {}
    with store.locked(reel.folder / ".post.lock"):
        thread = threading.Thread(target=lambda: done.update(result=post.list_scheduled(reel.slug, client=stub.client())))
        thread.start()
        err = ""
        for _ in range(500):
            err += capsys.readouterr().err
            if "Waiting for it to finish" in err:
                break
            time.sleep(0.01)
        assert "Another run is posting this video." in err and "result" not in done
    thread.join(10)
    (listed,) = done["result"]
    assert listed.status == "scheduled" and receipt(reel, "instagram")["post_id"] == listed.post_id


def test_list_with_nothing_to_search_for_does_not_wait_for_the_lock(reel, stub, when, capsys):
    run(reel, stub, ["instagram"], when)
    capsys.readouterr()
    with store.locked(reel.folder / ".post.lock"):
        (listed,) = post.list_scheduled(reel.slug, client=stub.client())  # only reads, so it never waits
    assert listed.status == "scheduled" and "Waiting" not in capsys.readouterr().err


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


def test_a_create_held_up_by_rate_limiting_is_not_sent_once_the_posts_time_is_close(reel, stub, when, monkeypatch):
    now = {"at": when.astimezone(timezone.utc) - timedelta(seconds=100)}  # zernio's clock: 100 seconds before the post
    monkeypatch.setattr(zernio, "_now", lambda: now["at"])
    monkeypatch.setattr(zernio, "time", type("Clock", (), {"sleep": staticmethod(lambda s: now.update(at=now["at"] + timedelta(seconds=s)))}))
    stub.reply("POST", r"/api/v1/posts", 429, {"error": "Slow down"}, {"Retry-After": "50"})

    (result,) = run(reel, stub, ["instagram"], when)
    assert result.status == "failed" and "Pick a later time" in result.reason
    assert len(stub.creates()) == 1 and stub.posts == {}  # the one request that was turned away, and nothing after it
    assert not (reel.folder / "post" / "instagram.json").exists()  # nothing was made, so nothing is remembered


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


# --- a create whose answer was lost, found again --------------------------------------------------------------------------


def edit_caption(reel, platform: str, words: str) -> None:
    path = reel.folder / "captions.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data[platform]["body"] = words
    path.write_text(json.dumps(data), encoding="utf-8")


NEW_WORDS = 'Basil lasts longer when you skip one thing.\n\nComment "GROW" for the balcony planting checklist.'


def test_the_request_id_is_also_the_idempotency_key(reel, stub, when):
    run(reel, stub, ["instagram", "tiktok"], when)
    for create in stub.creates():
        platform = create.body["platforms"][0]["platform"]
        assert create.headers["x-request-id"] == create.headers["idempotency-key"] == receipt(reel, platform)["request_id"]


def test_an_edited_caption_resent_after_a_lost_answer_does_not_make_a_second_post(reel, stub, when):
    stub.drop_creates = ["after"]  # Zernio saves the post, the answer is lost
    run(reel, stub, ["instagram"], when)
    stub.list_lag = True  # and the list has not caught up, so a search by title finds nothing
    edit_caption(reel, "instagram", NEW_WORDS)

    (again,) = run(reel, stub, ["instagram"], when)  # the creator approved the new words first
    sent = stub.creates()
    assert len(sent) == 2 and len(stub.posts) == 1  # the key made Zernio answer with the post it already had
    assert sent[0].headers["idempotency-key"] == sent[1].headers["idempotency-key"]
    assert sent[0].body["content"] != sent[1].body["content"]
    assert again.status == "scheduled" and again.post_id in stub.posts
    assert "still holds the earlier text" in again.reason  # and the result says which words are scheduled


def test_a_resend_turned_away_by_rate_limiting_keeps_its_request_id_so_a_later_run_makes_no_second_post(
    reel, stub, when, monkeypatch
):
    # run 1: Zernio saves the post, the answer is lost, and the post list has not caught up
    stub.drop_creates = ["after"]
    (first,) = run(reel, stub, ["instagram"], when)
    assert first.status == "failed" and len(stub.posts) == 1
    kept = receipt(reel, "instagram")
    stub.list_lag = True

    # run 2: the resend is turned away with 429 until the post's own time is less than a minute off
    with monkeypatch.context() as patch:
        now = {"at": when.astimezone(timezone.utc) - timedelta(seconds=100)}
        patch.setattr(zernio, "_now", lambda: now["at"])
        patch.setattr(
            zernio, "time", SimpleNamespace(sleep=lambda seconds: now.update(at=now["at"] + timedelta(seconds=seconds)))
        )
        stub.reply("POST", r"/api/v1/posts", 429, {"error": "Slow down"}, {"Retry-After": "50"})
        (second,) = run(reel, stub, ["instagram"], when)
    assert second.status == "failed" and "Pick a later time" in second.reason
    assert "earlier attempt may already have made this post" in second.reason
    assert receipt(reel, "instagram") == kept  # the same request id, and the time of the first attempt

    # run 3: the creator edits the caption, approves it and asks again
    edit_caption(reel, "instagram", NEW_WORDS)
    (third,) = run(reel, stub, ["instagram"], when)
    assert len(stub.posts) == 1  # not two
    assert {c.headers["idempotency-key"] for c in stub.creates()} == {kept["request_id"]}  # one key throughout
    assert third.status == "scheduled" and third.post_id in stub.posts
    assert "still holds the earlier text" in third.reason


def test_a_resend_that_runs_out_of_rate_limited_tries_keeps_its_receipt_too(reel, stub, when, monkeypatch):
    stub.drop_creates = ["after"]
    run(reel, stub, ["instagram"], when)
    kept = receipt(reel, "instagram")
    stub.list_lag = True
    monkeypatch.setattr(zernio, "time", SimpleNamespace(sleep=lambda seconds: None))
    stub.reply("POST", r"/api/v1/posts", 429, {"error": "Rate limit exceeded"}, {"Retry-After": "0"}, times=6)

    (second,) = run(reel, stub, ["instagram"], when)
    assert second.status == "failed" and "HTTP 429" in second.reason
    assert receipt(reel, "instagram") == kept

    (third,) = run(reel, stub, ["instagram"], when)  # the rate limit has lifted
    assert third.status == "scheduled" and len(stub.posts) == 1


def test_a_first_attempt_that_runs_out_of_rate_limited_tries_is_forgotten(reel, stub, when, monkeypatch):
    # every answer was 429, so Zernio never looked at any of the requests and there is nothing to remember
    monkeypatch.setattr(zernio, "time", SimpleNamespace(sleep=lambda seconds: None))
    stub.reply("POST", r"/api/v1/posts", 429, {"error": "Rate limit exceeded"}, {"Retry-After": "0"}, times=6)
    (result,) = run(reel, stub, ["instagram"], when)
    assert result.status == "failed" and "HTTP 429" in result.reason
    assert not (reel.folder / "post" / "instagram.json").exists()
    assert len(stub.posts) == 0


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_a_resend_that_is_refused_says_nothing_about_the_first_attempt_so_its_receipt_stays(reel, stub, when, status):
    stub.drop_creates = ["after"]
    run(reel, stub, ["instagram"], when)
    kept = receipt(reel, "instagram")
    stub.list_lag = True
    stub.reply("POST", r"/api/v1/posts", status, {"error": "Refused for now"}, times=1)

    (second,) = run(reel, stub, ["instagram"], when)
    assert second.status == "failed" and "Refused for now" in second.reason
    assert "earlier attempt may already have made this post" in second.reason
    assert receipt(reel, "instagram") == kept

    stub.list_lag = False  # the list has caught up: the next run finds the post it made the first time
    (third,) = run(reel, stub, ["instagram"], when)
    assert third.status == "scheduled" and len(stub.posts) == 1 and third.post_id in stub.posts


def test_a_resend_answered_with_the_earlier_post_at_its_earlier_time_says_how_to_move_it(reel, stub, when):
    stub.drop_creates = ["after"]
    run(reel, stub, ["instagram"], when)
    stub.list_lag = True
    later = when + timedelta(days=1)  # the creator asked for a later time in the meantime

    (second,) = run(reel, stub, ["instagram"], later)
    # the post is scheduled, at the earlier time: the first answer says so, as the next run's answer does
    assert second.status == "scheduled" and len(stub.posts) == 1
    assert datetime.fromisoformat(second.scheduled_for) == when
    assert "already had this post from an earlier attempt" in second.reason
    assert post.describe_time(when) in second.reason and "reschedule" in second.reason

    stub.list_lag = False
    (third,) = run(reel, stub, ["instagram"], later)  # the next run finds it and gives the same answer
    assert third.status == second.status and third.scheduled_for == second.scheduled_for
    assert "instead" in third.reason and "reschedule" in third.reason
    assert len(stub.posts) == 1


def test_a_resend_answered_at_another_time_exits_zero_on_the_command_line(reel, stub, when, monkeypatch, capsys):
    stub.drop_creates = ["after"]
    run(reel, stub, ["instagram"], when)
    stub.list_lag = True
    later = when + timedelta(days=1)
    (second,) = run(reel, stub, ["instagram"], later)
    from kit.post import cli

    assert cli._print_results([second], LONDON) == 0, "a scheduled post is not a failure"
    assert capsys.readouterr().out.startswith(f"Instagram: scheduled for {post.describe_time(when)}.")


def test_after_23_hours_an_attempt_that_left_no_post_is_not_sent_again(reel, stub, when, monkeypatch):
    clock = {"now": datetime.now(timezone.utc)}
    monkeypatch.setattr(schedule_module, "_now", lambda: clock["now"])
    later = when + timedelta(days=3)
    stub.drop_creates = ["before"]
    (first,) = run(reel, stub, ["instagram"], later)
    assert first.status == "failed" and len(stub.posts) == 0

    clock["now"] += timedelta(hours=23, minutes=1)
    (second,) = run(reel, stub, ["instagram"], later)
    assert second.status == "failed"
    for words in ("more than 23 hours ago", "Check Zernio", 'titled "Example Studio instagram example-reel"', "instagram.json"):
        assert words in second.reason
    assert len(stub.creates()) == 1  # it was not sent again
    assert receipt(reel, "instagram")["create_attempted"] is True  # and the record is left for the creator


def test_inside_23_hours_it_is_sent_again_with_the_same_key(reel, stub, when, monkeypatch):
    clock = {"now": datetime.now(timezone.utc)}
    monkeypatch.setattr(schedule_module, "_now", lambda: clock["now"])
    later = when + timedelta(days=3)
    stub.drop_creates = ["before"]
    run(reel, stub, ["instagram"], later)

    clock["now"] += timedelta(hours=22, minutes=59)
    (second,) = run(reel, stub, ["instagram"], later)
    assert second.status == "scheduled"
    sent = stub.creates()
    assert len(sent) == 2 and sent[0].headers["idempotency-key"] == sent[1].headers["idempotency-key"]


def test_the_time_of_the_first_attempt_is_kept_when_it_is_sent_again(reel, stub, monkeypatch):
    clock = {"now": datetime(2030, 1, 1, 9, 0, tzinfo=timezone.utc)}
    monkeypatch.setattr(schedule_module, "_now", lambda: clock["now"])
    later = datetime(2030, 1, 5, 18, 0, tzinfo=LONDON)
    stub.drop_creates = ["before", "before"]
    run(reel, stub, ["instagram"], later)
    first = receipt(reel, "instagram")["attempted_at"]
    assert first == "2030-01-01T09:00:00+00:00"

    clock["now"] += timedelta(hours=5)
    run(reel, stub, ["instagram"], later)
    assert receipt(reel, "instagram")["attempted_at"] == first  # Zernio's 24 hours run from the first request


def test_a_record_with_no_time_on_it_is_treated_as_too_old_to_send_again(reel, stub, when):
    (reel.folder / "post").mkdir()
    (reel.folder / "post" / "instagram.json").write_text(
        json.dumps({"title": "Example Studio instagram example-reel", "request_id": "r-1", "create_attempted": True}),
        encoding="utf-8",
    )
    (result,) = run(reel, stub, ["instagram"], when)
    assert result.status == "failed" and "Check Zernio" in result.reason
    assert stub.creates() == []


def test_editing_a_caption_after_scheduling_says_zernio_still_holds_the_earlier_text(reel, stub, when):
    run(reel, stub, ["instagram", "tiktok"], when)
    edit_caption(reel, "instagram", NEW_WORDS)
    results = by_platform(run(reel, stub, ["instagram", "tiktok"], when))

    assert results["instagram"].reason.startswith("Already scheduled from an earlier run.")
    assert "still holds the earlier text" in results["instagram"].reason and "cancel it" in results["instagram"].reason
    assert results["tiktok"].reason == "Already scheduled from an earlier run."  # nothing changed there
    assert len(stub.creates()) == 2  # and nothing was sent again


def test_the_same_words_with_different_line_breaks_are_not_a_change(reel, stub, when):
    run(reel, stub, ["instagram"], when)
    (held,) = stub.posts.values()
    held["content"] = held["content"].replace("\n\n", "\n").replace("\n", " \n ")
    (again,) = run(reel, stub, ["instagram"], when)
    assert again.reason == "Already scheduled from an earlier run."


# --- cancelling and moving a post whose create lost its answer -----------------------------------------------------------------


def test_cancel_removes_the_post_the_stand_in_holds_after_a_lost_answer(reel, stub, when):
    stub.drop_creates = ["after"]
    run(reel, stub, ["instagram"], when)
    (held,) = stub.posts.values()
    assert "post_id" not in receipt(reel, "instagram")  # the kit never heard which post it made

    result = post.cancel(reel.slug, "instagram", client=stub.client())
    assert (result.status, result.post_id) == ("cancelled", held["_id"])
    assert stub.posts == {}  # the post is gone from Zernio, not only forgotten here
    assert (reel.folder / "post" / "instagram.cancelled.json").is_file()
    assert not (reel.folder / "post" / "instagram.json").exists()


def test_reschedule_moves_the_post_the_stand_in_holds_after_a_lost_answer(reel, stub, when):
    stub.drop_creates = ["after"]
    run(reel, stub, ["instagram"], when)
    (held,) = stub.posts.values()

    later = when + timedelta(days=2)
    result = post.reschedule(reel.slug, "instagram", later, client=stub.client())
    assert (result.status, result.post_id) == ("scheduled", held["_id"])
    assert datetime.fromisoformat(held["scheduledFor"].replace("Z", "+00:00")) == later
    saved = receipt(reel, "instagram")
    assert saved["post_id"] == held["_id"] and saved["scheduled_for"] == later.astimezone(LONDON).isoformat()


def test_cancel_and_reschedule_say_so_when_the_interrupted_create_made_no_post(reel, stub, when):
    stub.drop_creates = ["before"]
    run(reel, stub, ["instagram"], when)

    for result, undone, command in (
        (post.cancel(reel.slug, "instagram", client=stub.client()), "cancelled", "cancel"),
        (post.reschedule(reel.slug, "instagram", when + timedelta(days=1), client=stub.client()), "moved", "reschedule"),
    ):
        assert result.status == "skipped"
        assert "interrupted" in result.reason and "no post titled" in result.reason
        assert "Example Studio instagram example-reel" in result.reason and f"Nothing was {undone}" in result.reason
        assert result.reason.endswith(f"If it appears in Zernio later, run {command} again.")  # and what to do next
    assert stub.calls("DELETE") == [] and post_edits(stub) == []
    assert receipt(reel, "instagram")["create_attempted"] is True  # the record stays, in case the create lands late


def test_the_search_for_an_interrupted_post_stays_inside_its_own_profile(reel, stub, when):
    stub.drop_creates = ["after"]
    run(reel, stub, ["instagram"], when)
    before = len(stub.calls("GET", "/api/v1/posts"))
    post.cancel(reel.slug, "instagram", client=stub.client())
    reads = stub.calls("GET", "/api/v1/posts")[before:]
    assert reads and {r.query["profileId"] for r in reads} == {"profile-main"}


def test_cancel_and_reschedule_of_an_interrupted_create_need_the_key(reel, stub, when):
    stub.drop_creates = ["before"]
    run(reel, stub, ["instagram"], when)
    with pytest.raises(NoZernio):
        post.cancel(reel.slug, "instagram")
    with pytest.raises(NoZernio):
        post.reschedule(reel.slug, "instagram", when + timedelta(days=1))


def test_a_failed_search_while_cancelling_is_reported_not_guessed_at(reel, stub, when):
    stub.drop_creates = ["after"]
    run(reel, stub, ["instagram"], when)
    stub.reply("GET", r"/api/v1/posts", 503, {"error": "Try later"})
    result = post.cancel(reel.slug, "instagram", client=stub.client())
    assert result.status == "failed" and "HTTP 503" in result.reason
    assert len(stub.posts) == 1 and stub.calls("DELETE") == []


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
    assert 'no post titled "Example Studio instagram example-reel"' in listed.reason, "it looked, and says so"


def test_list_finds_the_post_an_interrupted_create_made_and_says_so(reel, stub, when):
    stub.drop_creates = ["after"]  # Zernio saved the post, and the answer was lost
    run(reel, stub, ["instagram"], when)
    (held,) = stub.posts.values()
    assert "post_id" not in receipt(reel, "instagram")

    (listed,) = post.list_scheduled(reel.slug, client=stub.client())
    assert (listed.status, listed.post_id) == ("scheduled", held["_id"])
    assert datetime.fromisoformat(listed.scheduled_for) == when
    assert "interrupted" in listed.reason and "found" in listed.reason
    assert receipt(reel, "instagram")["post_id"] == held["_id"], "and it is remembered from now on"


def test_list_needs_the_key_to_look_for_an_interrupted_create(reel, stub, when):
    stub.drop_creates = ["before"]
    run(reel, stub, ["instagram"], when)
    with pytest.raises(NoZernio):
        post.list_scheduled(reel.slug)


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


def test_a_result_is_five_plain_fields_a_time_and_an_optional_payload():
    result = post.Result("instagram", "scheduled", "post-1", None, None)  # positional calls work as they always did
    assert dataclasses.astuple(result) == ("instagram", "scheduled", "post-1", None, None, None, None)
    assert [f.name for f in dataclasses.fields(result)] == [
        "platform", "status", "post_id", "url", "reason", "scheduled_for", "payload",
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
    assert "read it back" in result.reason and "not confirmed" in result.reason  # Zernio found no such post
    assert receipt(reel, "instagram")["post_id"] == "post-not-there-yet"  # it is remembered, not repeated


def test_a_post_zernio_cannot_find_when_it_is_read_back_is_not_confirmed(reel, stub, when):
    # The create's own answer says scheduled, but reading the post back finds nothing: that is not a confirmation.
    saved = {"_id": "post-not-found", "status": "scheduled", "scheduledFor": when.astimezone(timezone.utc).isoformat(),
             "platforms": [{"platform": "instagram", "status": "scheduled"}]}
    stub.reply("POST", r"/api/v1/posts$", 201, {"post": saved}, times=1)
    (result,) = run(reel, stub, ["instagram"], when)
    assert (result.status, result.post_id) == ("unknown", "post-not-found")
    assert "not confirmed" in result.reason and "Zernio" in result.reason
    assert receipt(reel, "instagram")["post_id"] == "post-not-found", "kept, so the next run looks before it sends"


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


def test_facebook_alone_with_a_video_it_cannot_measure_uploads_nothing(reel, stub, when, monkeypatch):
    def unreadable(path):
        raise PostError("The length of final.mp4 could not be read. Check that it plays and try again.")

    monkeypatch.setattr(payloads, "video_seconds", unreadable)
    (result,) = run(reel, stub, ["facebook"], when)
    assert result.status == "failed" and "length" in result.reason
    assert stub.uploads == [] and not (reel.folder / "post" / "media.json").exists(), "nothing was uploaded for nothing"
    assert stub.creates() == []


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
