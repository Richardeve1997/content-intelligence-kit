"""Fixtures for the posting tests: a stand-in Zernio, a throwaway kit folder and a finished video.

Nothing here can reach the real service. The stand-in listens on this machine, and a guard refuses
any connection to another address, so a test that forgot to inject it fails loudly.
"""

from __future__ import annotations

import copy
import json
import socket
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fake_zernio import FakeZernio

from kit import config

LOCAL = {"127.0.0.1", "::1", "localhost"}

# A valid set of captions for the example creator in me.example/: comment replies on Instagram and
# Facebook, a plain follow line everywhere else, and LinkedIn written on its own.
CAPTIONS = {
    "instagram": {
        "title": None,
        "body": (
            "Basil on a windowsill lasts three weeks if you skip one thing.\n"
            "Water the base, never the leaves.\n\n"
            'Comment "GROW" for the balcony planting checklist.'
        ),
        "hashtags": ["#balconygarden", "#growyourown", "#smallspaces", "#basil", "#windowsill"],
    },
    "tiktok": {
        "title": None,
        "body": "Basil on a windowsill lasts three weeks if you skip one thing.\n\nFollow @examplestudio for more.",
        "hashtags": ["#balconygarden", "#growyourown", "#smallspaces", "#basil", "#windowsill"],
    },
    "youtube": {
        "title": "Why windowsill basil dies in week three",
        "body": (
            "Windowsill basil usually dies in week three. The leaves stay wet, and that is the cause.\n"
            "Water the base and it keeps going.\n\nFollow @examplestudio for more."
        ),
        "hashtags": ["#balconygarden", "#growyourown", "#smallspaces", "#basil", "#windowsill", "#herbs"],
    },
    "facebook": {
        "title": None,
        "body": (
            "Basil on a windowsill lasts three weeks if you skip one thing.\n\n"
            'Comment "GROW" for the balcony planting checklist.'
        ),
        "hashtags": ["#balconygarden", "#growyourown", "#smallspaces", "#basil", "#windowsill"],
    },
    "linkedin": {
        "title": None,
        "body": (
            "Windowsill basil usually dies in week three, and wet leaves are the cause.\n\n"
            "What is the first herb you lose each year?\n\nFollow Example Studio for more."
        ),
        "hashtags": [],
    },
    "twitter": {
        "title": None,
        "body": "Windowsill basil dies in week three because the leaves stay wet. Water the base.\n\nFollow @examplestudio for more.",
        "hashtags": ["#balconygarden", "#basil"],
    },
}


@pytest.fixture(autouse=True)
def only_local_network(monkeypatch):
    """Refuse any connection that is not to this machine, so no test can reach the real service."""
    real_connect = socket.socket.connect

    def guarded(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in LOCAL:
            raise AssertionError(f"a test tried to connect to {host}")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")


@pytest.fixture
def stub():
    server = FakeZernio().start()
    yield server
    server.stop()


@pytest.fixture
def studio(tmp_path, monkeypatch, example_me):
    """A throwaway kit folder: the example creator's me/ folder, an empty work/, no .env and no Zernio key."""
    root = tmp_path / "kit"
    root.mkdir()
    monkeypatch.setattr(config, "kit_root", lambda: root)
    monkeypatch.delenv("ZERNIO_API_KEY", raising=False)
    return root


@pytest.fixture
def profile(studio):
    return config.load_profile()


@pytest.fixture
def reel(studio, monkeypatch):
    """A finished video: work/example-reel/ with a final.mp4 (only bytes) and valid captions.json."""
    from kit.post import payloads

    folder = studio / "work" / "example-reel"
    folder.mkdir(parents=True)
    (folder / "final.mp4").write_bytes(b"not a real video, only bytes to upload " * 20)
    (folder / "captions.json").write_text(json.dumps(CAPTIONS, indent=2), encoding="utf-8")
    state = SimpleNamespace(slug="example-reel", folder=folder, video=folder / "final.mp4", seconds=30.0)
    monkeypatch.setattr(payloads, "video_seconds", lambda path: state.seconds)
    return state


@pytest.fixture
def captions_set():
    """A fresh copy of the valid captions, for a test to break one way."""
    return copy.deepcopy(CAPTIONS)


@pytest.fixture
def when():
    """Six in the evening tomorrow, in the example creator's time zone."""
    now = datetime.now(ZoneInfo("Europe/London"))
    return (now + timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
