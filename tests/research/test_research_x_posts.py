"""kit.research.x_posts: the creator's ideas file, and the X posts it links to."""

import pytest

from kit.research import common, x_posts

ONE = "https://x.com/example_account/status/1000000000000000001"
TWO = "https://x.com/another_account/status/1000000000000000002"

IDEAS_FILE = f"""# Ideas

Add one idea per bullet. The research run reads this file along with your watch list.

- Why windowsill basil dies in week three
- This one blew up on X {ONE}
* Article worth a video https://example.com/article-about-soil.
https://twitter.com/another_account/status/1000000000000000002
1. Numbered idea about compost

Some closing prose with no bullet and no link.
"""


def fx_payload(tweet_id="1000000000000000001", handle="example_account", **tweet):
    base = {
        "id": tweet_id,
        "text": "Nobody tells you this about basil.",
        "created_at": "Tue Oct 27 10:00:00 +0000 2026",
        "author": {"name": "Example Account", "screen_name": handle, "followers": 1200},
        "views": 120000,
        "likes": 3400,
        "retweets": 500,
        "replies": 210,
        "bookmarks": 900,
        "quotes": 12,
        "media": {"all": []},
    }
    base.update(tweet)
    return {"code": 200, "message": "OK", "tweet": base}


def vx_payload(tweet_id="1000000000000000001", handle="example_account"):
    return {
        "tweetID": tweet_id,
        "text": "Nobody tells you this about basil.",
        "user_name": "Example Account",
        "user_screen_name": handle,
        "date": "Tue Oct 27 10:00:00 +0000 2026",
        "likes": 3400,
        "retweets": 500,
        "replies": 210,
        "media_extended": [],
    }


# --- the ideas file -------------------------------------------------------------------------------


def test_ideas_file_notes(tmp_path):
    path = tmp_path / "ideas.md"
    path.write_text(IDEAS_FILE, encoding="utf-8")
    notes = x_posts.read_ideas_file(path)
    assert [n["n"] for n in notes] == [1, 2, 3, 4, 5]
    assert notes[0]["text"] == "Why windowsill basil dies in week three"
    assert notes[0]["links"] == [] and notes[0]["x_urls"] == []
    assert notes[1]["x_urls"] == [ONE]
    assert notes[2]["links"] == ["https://example.com/article-about-soil"]  # the full stop is not part of the link
    assert notes[2]["x_urls"] == []
    assert notes[3]["x_urls"] == [TWO]  # twitter.com is written as x.com
    assert notes[4]["text"] == "Numbered idea about compost"


def test_a_missing_ideas_file_has_no_notes(tmp_path):
    assert x_posts.read_ideas_file(tmp_path / "nope.md") == []


def test_headings_and_plain_prose_are_not_ideas(tmp_path):
    path = tmp_path / "ideas.md"
    path.write_text("# Ideas\n\nJust a paragraph.\n\n## Later\n", encoding="utf-8")
    assert x_posts.read_ideas_file(path) == []


def test_the_example_ideas_file_reads(example_me):
    notes = x_posts.read_ideas_file(example_me / "ideas.md")
    assert len(notes) == 2 and notes[0]["text"].startswith("Why windowsill basil dies")


def test_a_note_is_cut_to_a_sensible_length(tmp_path):
    path = tmp_path / "ideas.md"
    path.write_text("- " + "word " * 400, encoding="utf-8")
    (note,) = x_posts.read_ideas_file(path)
    assert len(note["text"]) <= 400


@pytest.mark.parametrize(
    "text, expected",
    [
        (f"see {ONE} now", [("example_account", "1000000000000000001", ONE)]),
        ("https://mobile.twitter.com/some_one/status/55", [("some_one", "55", "https://x.com/some_one/status/55")]),
        ("https://www.x.com/some_one/statuses/56?s=20", [("some_one", "56", "https://x.com/some_one/status/56")]),
        (f"{ONE} and again {ONE}", [("example_account", "1000000000000000001", ONE)]),
        ("https://example.com/status/1", []),
        ("https://x.com/home", []),
    ],
)
def test_finding_x_links(text, expected):
    assert x_posts.find_links(text) == expected


# --- reading a post -------------------------------------------------------------------------------


def test_a_post_is_read_through_fxtwitter(web):
    web.add("GET", "api.fxtwitter.com/example_account/status/1000000000000000001", fx_payload())
    (post,) = x_posts.read_posts([("example_account", "1000000000000000001", ONE)])
    assert post["status"] == "read" and post["via"] == "fxtwitter"
    assert post["url"] == ONE and post["handle"] == "example_account" and post["author_name"] == "Example Account"
    assert (post["views"], post["likes"], post["reposts"], post["replies"]) == (120000, 3400, 500, 210)
    assert post["text"] == "Nobody tells you this about basil."
    assert post["created_at"] == "2026-10-27T10:00:00+00:00"
    assert all("vxtwitter" not in url for url in web.urls())


def test_vxtwitter_is_the_second_try(web):
    web.add("GET", "api.fxtwitter.com", common.HttpError(503, "HTTP 503"))
    web.add("GET", "api.vxtwitter.com/example_account/status/1000000000000000001", vx_payload())
    (post,) = x_posts.read_posts([("example_account", "1000000000000000001", ONE)])
    assert post["status"] == "read" and post["via"] == "vxtwitter"
    assert post["views"] is None and post["likes"] == 3400


def test_a_post_neither_service_can_read_is_listed_as_unread(web):
    web.add("GET", "api.fxtwitter.com", common.HttpError(503, "HTTP 503"))
    web.add("GET", "api.vxtwitter.com", common.HttpError(None, "no network route"))
    (post,) = x_posts.read_posts([("example_account", "1000000000000000001", ONE)])
    assert post["status"] == "unread"
    assert post["url"] == ONE and post["handle"] == "example_account"
    assert post["reason"] == "HTTP 503"
    assert "text" not in post or not post["text"]


def test_an_answer_with_no_post_in_it_is_unread_not_a_crash(web):
    web.add("GET", "api.fxtwitter.com", {"code": 404, "message": "NOT_FOUND", "tweet": None})
    web.add("GET", "api.vxtwitter.com", {"error": "nope"})
    (post,) = x_posts.read_posts([("example_account", "1000000000000000001", ONE)])
    assert post["status"] == "unread" and post["reason"]


def test_many_posts_keep_their_order(web):
    web.add("GET", "api.fxtwitter.com/example_account/", fx_payload())
    web.add("GET", "api.fxtwitter.com/another_account/", fx_payload("1000000000000000002", "another_account"))
    links = [("another_account", "1000000000000000002", TWO), ("example_account", "1000000000000000001", ONE)]
    assert [p["handle"] for p in x_posts.read_posts(links)] == ["another_account", "example_account"]


def test_an_offline_run_reads_nothing_and_says_why(web):
    (post,) = x_posts.read_posts([("example_account", "1000000000000000001", ONE)], offline=True)
    assert post["status"] == "unread" and post["reason"] == "offline run"
    assert web.calls == []


def test_a_long_post_is_cut(web):
    web.add("GET", "api.fxtwitter.com", fx_payload(text="word " * 300))
    (post,) = x_posts.read_posts([("example_account", "1000000000000000001", ONE)])
    assert len(post["text"]) <= 700
