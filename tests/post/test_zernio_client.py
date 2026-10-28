"""kit.zernio: the client for the Zernio API.

Every test talks to the stand-in in fake_zernio.py, never to the real service. The key used here is
a plain made-up word, and some tests check that it never shows up in a message.
"""

from __future__ import annotations

import copy
import pickle
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fake_zernio import HANG_UP, make_account

from kit import zernio
from kit.config import KitError, PLATFORMS
from kit.zernio import Client, NoZernio, ZernioError

KEY = "fake-value-for-tests"


def new_post(title: str = "Example Studio instagram example-reel") -> dict:
    return {
        "title": title,
        "content": "Water the base, never the leaves.",
        "mediaItems": [{"type": "video", "url": "https://media.example.test/1/final.mp4"}],
        "platforms": [
            {"platform": "instagram", "accountId": "acct-instagram", "platformSpecificData": {"shareToFeed": True}}
        ],
        "scheduledFor": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "timezone": "Europe/London",
        "isDraft": False,
    }


# --- calling the API ---------------------------------------------------------------------------------


def test_the_key_goes_out_as_a_bearer_token(stub):
    stub.client().call("GET", "/accounts")
    (seen,) = stub.calls("GET", "/api/v1/accounts")
    assert seen.headers["authorization"] == f"Bearer {KEY}"


def test_429_then_ok(stub):
    stub.reply("GET", r"/api/v1/accounts", 429, {"error": "Slow down"}, {"Retry-After": "0"})
    answer = stub.client().call("GET", "/accounts")
    assert [a["platform"] for a in answer["accounts"]] == list(PLATFORMS)
    assert len(stub.calls("GET", "/api/v1/accounts")) == 2


def test_429_waits_as_long_as_zernio_asks_and_never_more_than_a_minute(stub, monkeypatch):
    slept = []
    monkeypatch.setattr(zernio, "time", SimpleNamespace(sleep=slept.append))
    stub.reply("GET", r"/api/v1/accounts", 429, {"error": "Slow down"}, {"Retry-After": "2"})
    stub.reply("GET", r"/api/v1/accounts", 429, {"error": "Slow down"})  # no Retry-After: back off
    stub.reply("GET", r"/api/v1/accounts", 429, {"error": "Slow down"}, {"Retry-After": "99999"})
    stub.client().call("GET", "/accounts")
    assert slept == [2.0, 10.0, 60.0]


def test_429_gives_up_after_six_tries(stub, monkeypatch):
    monkeypatch.setattr(zernio, "time", SimpleNamespace(sleep=lambda seconds: None))
    stub.reply("GET", r"/api/v1/accounts", 429, {"error": "Rate limit exceeded"}, times=20)
    with pytest.raises(ZernioError) as caught:
        stub.client().call("GET", "/accounts")
    assert len(stub.calls("GET", "/api/v1/accounts")) == 6
    assert caught.value.status == 429
    assert caught.value.uncertain is False  # a 429 means Zernio did nothing with the request
    assert "Rate limit exceeded" in str(caught.value)


# --- what goes wrong, in plain sentences ------------------------------------------------------------


def test_zernios_own_reason_is_shown(stub):
    stub.reply("GET", r"/api/v1/accounts", 400, {"error": "The title is too long"})
    with pytest.raises(ZernioError) as caught:
        stub.client().call("GET", "/accounts")
    assert "HTTP 400" in str(caught.value)
    assert "The title is too long" in str(caught.value)
    assert caught.value.status == 400
    assert caught.value.body == {"error": "The title is too long"}
    assert caught.value.uncertain is False


def test_a_message_field_counts_as_a_reason_too(stub):
    stub.reply("GET", r"/api/v1/accounts", 422, {"message": "That media type is not accepted"})
    with pytest.raises(ZernioError, match="That media type is not accepted"):
        stub.client().call("GET", "/accounts")


def test_the_key_never_appears_in_a_message(stub):
    stub.reply("GET", r"/api/v1/accounts", 400, {"error": f"bad header Authorization: Bearer {KEY}"})
    client = stub.client()
    with pytest.raises(ZernioError) as caught:
        client.call("GET", "/accounts")
    assert KEY not in str(caught.value)
    assert KEY not in repr(client)
    assert KEY not in str(client)


def test_a_wrong_key_points_at_the_env_file_and_does_not_repeat_the_key(stub):
    wrong = "not-a-real-key"
    with pytest.raises(ZernioError) as caught:
        stub.client(key=wrong).call("GET", "/accounts")
    text = str(caught.value)
    assert caught.value.status == 401
    assert "ZERNIO_API_KEY" in text and ".env" in text
    assert wrong not in text and KEY not in text


def test_a_dropped_connection_is_reported_as_uncertain(stub):
    stub.reply("GET", r"/api/v1/accounts", HANG_UP)
    with pytest.raises(ZernioError) as caught:
        stub.client().call("GET", "/accounts")
    assert caught.value.uncertain is True
    assert caught.value.status is None
    assert "connection" in str(caught.value).lower()


def test_a_slow_answer_is_reported_as_uncertain(stub):
    stub.reply("GET", r"/api/v1/accounts", 200, {"accounts": []}, delay=0.5)
    with pytest.raises(ZernioError) as caught:
        stub.client().call("GET", "/accounts", timeout=0.1)
    assert caught.value.uncertain is True
    assert "in time" in str(caught.value)


@pytest.mark.parametrize(
    "status, uncertain",
    [(400, False), (404, False), (422, False), (408, True), (409, True), (500, True), (503, True)],
)
def test_only_an_answer_that_leaves_the_outcome_open_is_uncertain(stub, status, uncertain):
    stub.reply("POST", r"/api/v1/posts", status, {"error": "No."})
    with pytest.raises(ZernioError) as caught:
        stub.client().call("POST", "/posts", json={})
    assert caught.value.status == status
    assert caught.value.uncertain is uncertain


def test_a_duplicate_that_names_the_post_it_duplicates_is_a_refusal_not_a_doubt(stub):
    stub.reply("POST", r"/api/v1/posts", 409, {"error": "Duplicate post", "existingPostId": "post-7"})
    with pytest.raises(ZernioError) as caught:
        stub.client().call("POST", "/posts", json={})
    assert caught.value.status == 409 and caught.value.uncertain is False
    assert caught.value.body["existingPostId"] == "post-7"


def test_answers_that_cannot_be_read_are_errors_and_an_empty_one_is_fine(stub):
    client = stub.client()
    stub.reply("GET", r"/api/v1/accounts", 200, b"<html>maintenance</html>")
    with pytest.raises(ZernioError, match="could not be read") as caught:
        client.call("GET", "/accounts")
    assert caught.value.uncertain is True

    stub.reply("GET", r"/api/v1/accounts", 200, [])
    with pytest.raises(ZernioError, match="could not be read"):
        client.call("GET", "/accounts")

    stub.reply("GET", r"/api/v1/accounts", 200, {"success": False, "error": "Not today"})
    with pytest.raises(ZernioError, match="Not today"):
        client.call("GET", "/accounts")

    stub.reply("DELETE", r"/api/v1/posts/post-1", 200, b"")
    assert client.call("DELETE", "/posts/post-1") == {}


def test_a_reason_is_cut_short_and_kept_on_one_line(stub):
    stub.reply("GET", r"/api/v1/accounts", 400, {"error": "line one\nline two " + "x" * 500})
    with pytest.raises(ZernioError) as caught:
        stub.client().call("GET", "/accounts")
    assert "\n" not in str(caught.value)
    assert len(str(caught.value)) < 300


def test_the_address_must_be_https_or_this_machine():
    assert Client(KEY).base == "https://zernio.com/api/v1"
    assert Client(KEY, base="https://zernio.com/api/v1/").base == "https://zernio.com/api/v1"
    assert Client(KEY, base="http://127.0.0.1:9/api/v1")
    for bad in ("http://zernio.example.test/api/v1", "ftp://zernio.com", "zernio.com/api/v1", ""):
        with pytest.raises(ValueError, match="https"):
            Client(KEY, base=bad)


def test_a_client_needs_a_key():
    for bad in ("", "   ", None):
        with pytest.raises(ValueError, match="key"):
            Client(bad)


# --- where the key comes from -----------------------------------------------------------------------


def test_from_env_reads_the_key_from_the_environment(studio, monkeypatch):
    monkeypatch.setenv("ZERNIO_API_KEY", KEY)
    client = Client.from_env()
    assert client.base == "https://zernio.com/api/v1"
    assert KEY not in repr(client)


def test_from_env_reads_the_key_from_the_env_file(studio):
    (studio / ".env").write_text("# keys\nZERNIO_API_KEY=not-a-real-key\n", encoding="utf-8")
    assert Client.from_env().key == "not-a-real-key"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_from_env_without_a_key_raises_a_plain_sentence(studio, monkeypatch, value):
    if value is not None:
        monkeypatch.setenv("ZERNIO_API_KEY", value)
    with pytest.raises(NoZernio) as caught:
        Client.from_env()
    assert isinstance(caught.value, KitError)
    assert "ZERNIO_API_KEY" in str(caught.value) and ".env" in str(caught.value)


def test_errors_survive_a_copy():
    error = ZernioError("Zernio returned HTTP 400: No.", status=400, body={"error": "No."}, uncertain=False)
    for copied in (copy.deepcopy(error), pickle.loads(pickle.dumps(error))):
        assert (str(copied), copied.status, copied.body, copied.uncertain) == (
            "Zernio returned HTTP 400: No.", 400, {"error": "No."}, False
        )
    other = NoZernio("custom words")
    assert str(pickle.loads(pickle.dumps(other))) == "custom words"


# --- uploading ----------------------------------------------------------------------------------------


def test_upload_asks_for_an_address_then_sends_the_bytes_without_the_key(stub, tmp_path):
    video = tmp_path / "final.mp4"
    video.write_bytes(b"pretend video bytes" * 50)
    url = stub.client().upload(video)

    (presign,) = stub.calls("POST", "/api/v1/media/presign")
    assert presign.body == {"filename": "final.mp4", "contentType": "video/mp4", "size": video.stat().st_size}
    (upload,) = stub.uploads
    assert upload["bytes"] == video.read_bytes()
    assert upload["content_type"] == "video/mp4"
    assert upload["authorization"] is None  # the storage address never sees the key
    assert url == "https://media.example.test/1/final.mp4"


def test_a_failed_upload_says_nothing_was_posted(stub, tmp_path):
    video = tmp_path / "final.mp4"
    video.write_bytes(b"bytes")
    client = stub.client()

    stub.reply("PUT", r"/upload/1", 403, b"<Error>Denied</Error>")
    with pytest.raises(ZernioError, match="HTTP 403") as caught:
        client.upload(video)
    assert "Nothing was posted" in str(caught.value)
    assert caught.value.uncertain is False

    stub.reply("PUT", r"/upload/2", HANG_UP)
    with pytest.raises(ZernioError, match="Nothing was posted"):
        client.upload(video)

    stub.reply("POST", r"/api/v1/media/presign", 500, {"error": "Storage is down"})
    with pytest.raises(ZernioError, match="Storage is down"):
        client.upload(video)


def test_an_answer_without_an_upload_address_is_an_error(stub, tmp_path):
    video = tmp_path / "final.mp4"
    video.write_bytes(b"bytes")
    stub.reply("POST", r"/api/v1/media/presign", 200, {"publicUrl": "https://media.example.test/x.mp4"})
    with pytest.raises(ZernioError, match="upload address"):
        stub.client().upload(video)
    assert stub.uploads == []


def test_a_missing_file_is_a_plain_sentence(stub, tmp_path):
    with pytest.raises(ZernioError, match="cannot be read"):
        stub.client().upload(tmp_path / "nothing.mp4")
    assert stub.calls() == []


# --- accounts -------------------------------------------------------------------------------------------


def test_accounts_groups_the_connected_ones_by_platform_in_one_call(stub):
    stub.accounts = [
        make_account("instagram"),
        make_account("tiktok", active=False),
        make_account("youtube", needs_reconnection=True),
        make_account("facebook", enabled=False),
        make_account("linkedin", account_id="acct-linkedin-a"),
        make_account("linkedin", account_id="acct-linkedin-b", username="second-page"),
        make_account("threads"),  # a platform the kit does not post to
    ]
    found = stub.client().accounts()

    assert list(found) == list(PLATFORMS)
    assert [a["_id"] for a in found["instagram"]] == ["acct-instagram"]
    assert found["tiktok"] == [] and found["youtube"] == [] and found["facebook"] == []
    assert [a["_id"] for a in found["linkedin"]] == ["acct-linkedin-a", "acct-linkedin-b"]
    assert found["twitter"] == []
    (call,) = stub.calls()  # one read, nothing else
    assert (call.method, call.path, call.query) == ("GET", "/api/v1/accounts", {})


def test_an_account_with_no_flags_but_active_counts_as_connected(stub):
    stub.accounts = [{"_id": "acct-x", "platform": "twitter", "isActive": True}]
    assert [a["_id"] for a in stub.client().accounts()["twitter"]] == ["acct-x"]


# --- posts ----------------------------------------------------------------------------------------------


def test_create_post_sends_the_request_id_and_returns_the_answer(stub):
    answer = stub.client().create_post(new_post(), "00000000-0000-4000-8000-000000000001")
    (seen,) = stub.creates()
    assert seen.headers["x-request-id"] == "00000000-0000-4000-8000-000000000001"
    assert answer["post"]["status"] == "scheduled"
    assert stub.client().get_post(answer["post"]["_id"])["title"] == "Example Studio instagram example-reel"


def test_get_update_and_delete_a_post(stub):
    client = stub.client()
    post = client.create_post(new_post(), "00000000-0000-4000-8000-000000000001")["post"]

    again = client.update_post(post["_id"], {"scheduledFor": "2031-01-01T10:00:00+00:00", "timezone": "UTC"})
    assert again["post"]["status"] == "scheduled"
    assert client.get_post(post["_id"])["scheduledFor"] == "2031-01-01T10:00:00.000Z"

    assert client.delete_post(post["_id"]) is None
    with pytest.raises(ZernioError) as caught:
        client.get_post(post["_id"])
    assert caught.value.status == 404


def test_a_post_id_is_escaped_in_the_address(stub):
    with pytest.raises(ZernioError) as caught:
        stub.client().get_post("../accounts")
    assert caught.value.status == 404
    assert stub.calls("GET", "/api/v1/accounts") == []


def test_find_post_by_title_goes_through_the_pages_newest_first(stub):
    target = stub.seed_post("Example Studio instagram old-clip")  # the oldest post
    for n in range(230):
        stub.seed_post(f"Example Studio instagram clip-{n}")
    found = stub.client().find_post_by_title("Example Studio instagram old-clip", profile_id="profile-main")
    assert found["_id"] == target["_id"]
    reads = stub.calls("GET", "/api/v1/posts")
    assert [r.query["page"] for r in reads] == ["1", "2", "3"]
    assert {r.query["sortBy"] for r in reads} == {"created-desc"}
    assert {r.query["limit"] for r in reads} == {"100"}
    assert {r.query["profileId"] for r in reads} == {"profile-main"}


def test_find_post_by_title_gives_up_after_one_page_when_that_is_all_there_is(stub):
    for n in range(5):
        stub.seed_post(f"Example Studio instagram clip-{n}")
    assert stub.client().find_post_by_title("Example Studio tiktok clip-1") is None
    assert len(stub.calls("GET", "/api/v1/posts")) == 1


def test_find_post_by_title_reads_five_pages_at_most(stub):
    stub.seed_post("Example Studio instagram buried")  # the oldest post, 600 posts down
    for n in range(600):
        stub.seed_post(f"Example Studio instagram more-{n}")
    assert stub.client().find_post_by_title("Example Studio instagram buried") is None
    assert len(stub.calls("GET", "/api/v1/posts")) == 5


def test_find_post_by_title_ignores_extra_spaces_but_not_other_words(stub):
    stub.seed_post("Example  Studio instagram  clip")
    client = stub.client()
    assert client.find_post_by_title("Example Studio instagram clip")
    assert client.find_post_by_title("Example Studio instagram clip two") is None
