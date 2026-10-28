"""Fixtures for the research tests: a throwaway kit folder, blocked sockets and stand-ins for the web.

Nothing here reaches the network. Every creator in these tests is invented.
"""

from __future__ import annotations

import socket
import sys
import types
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

import pytest

from kit import config
from kit import transcribe as real_transcribe
from kit.research import common
from research_helpers import NOW


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    """Every research test runs at NOW, so nothing here depends on the day or the hour the suite is run."""
    monkeypatch.setattr(common, "utcnow", lambda: NOW)


@pytest.fixture
def kit_tree(tmp_path, monkeypatch, example_me) -> Path:
    """A throwaway kit folder for the example creator: no keys, nothing under research/ yet."""
    root = tmp_path / "kit-root"
    root.mkdir()
    monkeypatch.setattr(config, "kit_root", lambda: root)
    for name in ("APIFY_TOKEN", "ZERNIO_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return root


@pytest.fixture
def me(example_me) -> Path:
    return example_me


@pytest.fixture
def no_network(monkeypatch):
    """Any attempt to open a socket fails the test."""

    def refuse(*args, **kwargs):
        raise AssertionError("this test tried to use the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


ID_PREFIX = {"instagram": "ig", "tiktok": "tt", "youtube": "yt"}


@pytest.fixture
def video():
    """A factory for one scraped video of an invented creator."""

    def make(vid, creator="alpha", *, likes=100, comments=10, days_ago=1.0, platform="instagram", **extra) -> dict:
        posted = NOW - timedelta(days=days_ago)
        item = {
            "id": f"{ID_PREFIX[platform]}:{vid}",
            "platform": platform,
            "creator": creator,
            "url": f"https://example.com/{platform}/{creator}/{vid}",
            "posted_at": posted.isoformat(),
            "title": f"Title of {vid}",
            "caption": f"Title of {vid}\n#tag",
            "views": likes * 10,
            "likes": likes,
            "comments": comments,
            "duration": 30,
        }
        item.update(extra)
        return item

    return make


# --- a stand-in for the web -----------------------------------------------------------------------


class FakeWeb:
    """Stands in for common.http_json and common.http_bytes. Routes match on a piece of the URL."""

    def __init__(self):
        self.routes: list[tuple[str, str, object]] = []
        self.calls: list[tuple[str, str, object]] = []
        self.headers: list[dict] = []

    def add(self, method: str, fragment: str, response) -> "FakeWeb":
        """`response` is data to return, an exception to raise, or a function of (url, body)."""
        self.routes.append((method.upper(), fragment, response))
        return self

    def _answer(self, url: str, method: str, body, headers=None):
        self.calls.append((method.upper(), url, body))
        self.headers.append(dict(headers or {}))
        for route_method, fragment, response in self.routes:
            if route_method == method.upper() and fragment in url:
                if isinstance(response, BaseException):
                    raise response
                if callable(response):
                    return response(url, body)
                return response
        raise common.HttpError(404, "not found")

    def http_json(self, url, method="GET", body=None, headers=None, timeout=30):
        return self._answer(url, method, body, headers)

    def http_bytes(self, url, headers=None, timeout=30, limit=None):
        return self._answer(url, "GET", None, headers)

    def urls(self, method: str | None = None) -> list[str]:
        return [url for m, url, _ in self.calls if method is None or m == method.upper()]


@pytest.fixture
def web(monkeypatch) -> FakeWeb:
    fake = FakeWeb()
    monkeypatch.setattr(common, "http_json", fake.http_json)
    monkeypatch.setattr(common, "http_bytes", fake.http_bytes)
    return fake


# --- stand-ins for the modules other parts of the kit own ---------------------------------------------


class Recorder:
    """Records what a fake module was asked to do."""

    def __init__(self):
        self.events: list[str] = []


@pytest.fixture
def fake_speech(monkeypatch):
    """Stand-ins for kit.transcribe and kit.media (they belong to other parts of the kit).

    Returns the Recorder. Set `recorder.text` to change what a transcription says.
    """
    recorder = Recorder()
    recorder.text = "Water basil in the morning, never at night"
    recorder.free_calls = []

    transcribe = types.ModuleType("kit.transcribe")
    transcribe.AudioProblem = real_transcribe.AudioProblem  # the error for one file that cannot be read

    def words(audio, *, align=True):
        recorder.events.append(f"words:{Path(audio).name}:align={align}")
        return [types.SimpleNamespace(text=w, start=0.0, end=0.0) for w in recorder.text.split()]

    transcribe.words = words

    media = types.ModuleType("kit.media")

    @contextmanager
    def heavy_slot():
        recorder.events.append("slot:enter")
        try:
            yield
        finally:
            recorder.events.append("slot:exit")

    def require_free(gb=5.0):
        recorder.free_calls.append(gb)

    media.heavy_slot = heavy_slot
    media.require_free = require_free
    monkeypatch.setitem(sys.modules, "kit.transcribe", transcribe)
    monkeypatch.setitem(sys.modules, "kit.media", media)
    try:
        import numpy  # noqa: F401  the real speech tools need it, so the check for the tools looks for it too
    except ImportError:
        monkeypatch.setitem(sys.modules, "numpy", types.ModuleType("numpy"))
    return recorder


@pytest.fixture
def fake_zernio(monkeypatch):
    """A stand-in for kit.zernio. Set `recorder.pages` to the analytics pages the client should return."""
    recorder = Recorder()
    recorder.pages = []
    recorder.calls = []
    recorder.keys = []

    module = types.ModuleType("kit.zernio")

    class Client:
        def __init__(self, key, base="https://zernio.com/api/v1"):
            recorder.keys.append(key)

        def call(self, method, path, **kwargs):
            recorder.calls.append((method, path, kwargs))
            page = int(kwargs.get("params", {}).get("page", 1))
            if isinstance(recorder.pages, BaseException):
                raise recorder.pages
            return recorder.pages[page - 1]

    module.Client = Client
    monkeypatch.setitem(sys.modules, "kit.zernio", module)
    return recorder


@pytest.fixture
def offline_run(kit_tree):
    """Runs the sample (offline) research once and returns (profile, run folder)."""
    from kit.research import pipeline

    pipeline.run(offline=True, say=lambda *args, **kwargs: None)
    return config.load_profile(), kit_tree / "research" / "runs" / "2026-10-28"
