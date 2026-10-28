"""kit.research.common: small helpers for text, dates, web calls and files."""

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
import requests

from kit import config
from kit.research import common

UTC = timezone.utc


# --- text -----------------------------------------------------------------------------------------


def test_first_line_is_the_first_spoken_sentence():
    text = "Basil dies for one reason. Here is the fix. Water it in the morning."
    assert common.first_line(text) == "Basil dies for one reason."


def test_first_line_joins_a_one_word_opener_to_the_next_sentence():
    assert common.first_line("So. Basil dies for one reason. More follows.") == "So. Basil dies for one reason."


def test_first_line_stops_at_forty_words():
    long = " ".join(f"w{n}" for n in range(60)) + ". Next."
    line = common.first_line(long)
    assert line.endswith(" ...")
    assert len(line.removesuffix(" ...").split()) == 40


def test_the_opening_and_what_follows_are_split_cleanly():
    assert common.split_opening("Basil dies   for one\nreason.  Here is the fix.\n\nWater it.") == (
        "Basil dies for one reason.", "Here is the fix. Water it.",
    )
    assert common.split_opening("So. Basil dies for one reason. More follows.") == ("So. Basil dies for one reason.", "More follows.")
    assert common.split_opening("One sentence only.") == ("One sentence only.", "")
    assert common.split_opening("") == ("", "")
    long = " ".join(f"w{n}" for n in range(45)) + ". Next."
    hook, rest = common.split_opening(long)
    assert hook.endswith(" ...") and rest.startswith("w40 w41") and rest.endswith("Next.")


def test_first_line_of_nothing_is_nothing():
    assert common.first_line("") == ""
    assert common.first_line(None) == ""


def test_title_comes_from_the_first_line_that_is_not_a_call_to_action():
    caption = "Comment GROW and I will send it\n\nWhy basil dies in week three #herbs #garden\nMore words"
    assert common.title_from_caption(caption) == "Why basil dies in week three"


def test_title_of_an_empty_caption_is_empty():
    assert common.title_from_caption("") == ""
    assert common.title_from_caption(None) == ""


def test_title_is_cut_to_a_sensible_length():
    assert len(common.title_from_caption("word " * 100)) <= 160


@pytest.mark.parametrize(
    "number, shown",
    [(950, "950"), (1000, "1k"), (1500, "1.5k"), (12345, "12k"), (2_000_000, "2M"), (3_400_000, "3.4M"), (None, ""), ("x", "")],
)
def test_short_numbers(number, shown):
    assert common.short_num(number) == shown


def test_words_cuts_with_an_ellipsis():
    assert common.words("one two three four", 2) == "one two ..."
    assert common.words("one two", 5) == "one two"


@pytest.mark.parametrize(
    "raw, handle",
    [
        ("@Example_One", "Example_One"),
        ("  @x  ", "x"),
        ("plain_name", "plain_name"),
        ("https://www.instagram.com/example_one/", "example_one"),
        ("https://www.instagram.com/example_one/?hl=en", "example_one"),
        ("https://www.tiktok.com/@example_t", "example_t"),
        ("https://www.youtube.com/@example_channel/shorts", "example_channel"),
        ("", ""),
        (None, ""),
    ],
)
def test_handles_come_out_plain(raw, handle):
    assert common.handle_of(raw) == handle


def test_urls_are_compared_without_the_noise():
    same = {
        "https://www.Example.com/a/b/?utm=1#top",
        "http://example.com/a/b",
        "HTTPS://example.com/a/b/",
    }
    assert len({common.norm_url(u) for u in same}) == 1
    assert common.norm_url("https://twitter.com/someone/status/5") == common.norm_url("https://x.com/someone/status/5")
    assert common.norm_url("https://example.com/a") != common.norm_url("https://example.com/b")


# --- time -----------------------------------------------------------------------------------------


def test_days_are_shown_in_the_zone_they_are_given():
    moment = datetime(2026, 10, 27, 23, 30, tzinfo=UTC)
    assert common.fmt_day(moment, ZoneInfo("Pacific/Auckland")) == "28 Oct"
    assert common.fmt_day(moment, ZoneInfo("America/Los_Angeles")) == "27 Oct"
    assert common.fmt_day("2026-10-27T23:30:00Z", ZoneInfo("Pacific/Auckland")) == "28 Oct"
    assert common.fmt_day(None, UTC) == ""
    assert common.fmt_day("not a date", UTC) == ""


def test_stamps_and_long_days():
    moment = datetime(2026, 10, 28, 13, 30, tzinfo=UTC)
    assert common.fmt_stamp(moment, ZoneInfo("Europe/London")) == "28 Oct 13:30"
    assert common.fmt_stamp(moment, ZoneInfo("Pacific/Auckland")) == "29 Oct 02:30"
    assert common.fmt_long_day(moment, ZoneInfo("Pacific/Auckland")) == "Thursday 29 October 2026"
    assert common.fmt_long_day(moment, ZoneInfo("America/Los_Angeles")) == "Wednesday 28 October 2026"


def test_a_time_with_no_zone_is_taken_as_utc():
    assert common.parse_iso("2026-10-28T10:00:00").tzinfo is not None
    assert common.parse_iso("2026-10-28T10:00:00Z") == datetime(2026, 10, 28, 10, 0, tzinfo=UTC)
    assert common.parse_iso("garbage") is None
    assert common.parse_iso(None) is None


def test_the_zone_and_the_run_date_come_from_the_profile(example_me):
    profile = config.load_profile()
    assert str(common.tz_of(profile)) == "Europe/London"
    moment = datetime(2026, 10, 28, 23, 30, tzinfo=UTC)  # Europe/London is on UTC+0 in late October
    assert common.run_date(profile, moment) == "2026-10-28"


def test_an_unusable_zone_falls_back_to_utc(example_me):
    import dataclasses

    profile = dataclasses.replace(config.load_profile(), timezone="Nowhere/Land")
    assert common.tz_of(profile) is UTC or str(common.tz_of(profile)) == "UTC"


# --- files ----------------------------------------------------------------------------------------


def test_json_is_written_in_one_step(tmp_path):
    target = tmp_path / "deep" / "folder" / "data.json"
    common.write_json(target, {"b": 1, "a": [1, 2]})
    assert json.loads(target.read_text()) == {"a": [1, 2], "b": 1}
    assert [p.name for p in target.parent.iterdir()] == ["data.json"]  # no half-written leftover


def test_reading_a_missing_or_broken_file_gives_the_default(tmp_path):
    assert common.read_json(tmp_path / "nope.json", default=[]) == []
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    assert common.read_json(broken, default="fallback") == "fallback"


# --- web calls ------------------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status=200, payload=None, content=b""):
        self.status_code = status
        self.ok = status < 400
        self._payload = payload
        self._content = content
        self.headers = {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    def iter_content(self, size):
        for start in range(0, len(self._content), size):
            yield self._content[start : start + size]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


def test_http_json_returns_the_parsed_answer_and_sends_a_json_body(monkeypatch):
    seen = {}

    def fake_request(method, url, **kwargs):
        seen.update(method=method, url=url, **kwargs)
        return FakeResponse(200, {"data": 1})

    monkeypatch.setattr(requests, "request", fake_request)
    assert common.http_json("https://example.com/api", "POST", {"a": 1}, {"X-Test": "1"}) == {"data": 1}
    assert seen["method"] == "POST" and seen["json"] == {"a": 1}
    assert seen["headers"]["X-Test"] == "1" and "User-Agent" in seen["headers"]
    assert seen["timeout"] == 30


def test_a_failed_status_becomes_a_plain_error_without_the_address(monkeypatch):
    monkeypatch.setattr(requests, "request", lambda *a, **k: FakeResponse(402, {"error": "payment"}))
    with pytest.raises(common.HttpError) as caught:
        common.http_json("https://example.com/secret-path?token=not-a-real-key")
    assert caught.value.status == 402
    assert str(caught.value) == "HTTP 402"


def test_a_dead_network_becomes_a_plain_error(monkeypatch):
    def refuse(*args, **kwargs):
        raise requests.ConnectionError("HTTPSConnectionPool(host='example.com'): Max retries exceeded")

    monkeypatch.setattr(requests, "request", refuse)
    with pytest.raises(common.HttpError) as caught:
        common.http_json("https://example.com/")
    assert str(caught.value) == "no network route"
    assert "example.com" not in str(caught.value)


def test_a_slow_server_becomes_a_plain_error(monkeypatch):
    def slow(*args, **kwargs):
        raise requests.Timeout("read timed out")

    monkeypatch.setattr(requests, "request", slow)
    with pytest.raises(common.HttpError) as caught:
        common.http_bytes("https://example.com/file")
    assert str(caught.value) == "timed out"


def test_an_unreadable_answer_is_an_error_not_a_crash(monkeypatch):
    monkeypatch.setattr(requests, "request", lambda *a, **k: FakeResponse(200, None))
    with pytest.raises(common.HttpError) as caught:
        common.http_json("https://example.com/")
    assert "readable" in str(caught.value)


def test_http_bytes_refuses_a_file_over_its_limit(monkeypatch):
    monkeypatch.setattr(requests, "request", lambda *a, **k: FakeResponse(200, content=b"x" * 5000))
    assert common.http_bytes("https://example.com/f", limit=10_000) == b"x" * 5000
    with pytest.raises(common.HttpError) as caught:
        common.http_bytes("https://example.com/f", limit=1000)
    assert "too large" in str(caught.value)


def test_a_download_lands_in_the_file_it_is_given(monkeypatch, tmp_path):
    monkeypatch.setattr(requests, "request", lambda *a, **k: FakeResponse(200, content=b"abc" * 100))
    target = tmp_path / "out" / "clip.mp4"
    assert common.http_download("https://example.com/clip", target) == target
    assert target.read_bytes() == b"abc" * 100
    assert [p.name for p in target.parent.iterdir()] == ["clip.mp4"]


def test_a_failed_download_leaves_nothing_behind(monkeypatch, tmp_path):
    monkeypatch.setattr(requests, "request", lambda *a, **k: FakeResponse(200, content=b"x" * 5000))
    target = tmp_path / "clip.mp4"
    with pytest.raises(common.HttpError):
        common.http_download("https://example.com/clip", target, limit=100)
    assert list(tmp_path.iterdir()) == []


def test_reasons_never_carry_an_address_or_a_key():
    assert common.why(common.HttpError(402, "HTTP 402")) == "HTTP 402"
    assert common.why(RuntimeError("failed at https://example.com/x?token=not-a-real-key")) == "RuntimeError: failed at <url>"
    assert common.why(TimeoutError()) == "timed out"
    assert common.why(ValueError()) == "ValueError"
