"""ship.py: a re-ship keeps the video's bed, logs it once, clears a stale review pass, and only a render
that passed the speech gate becomes final.mp4.

The composite, the finish and the speech gate are stand-ins here; their own tests cover them.
"""

import json
import subprocess
from pathlib import Path

import pytest
from uistory_helpers import load

from kit import config, music

ship = load("ship")
INPUTS = ("base.mp4", "matte.mp4", "layout.resolved.json", "geometry.json", "gfx.webm", "clean.wav", "blocks.json")


@pytest.fixture
def video(tmp_path, example_me, monkeypatch):
    """work/example-reel with every build input present, two beds in me/music/calm, and stand-ins."""
    monkeypatch.setattr(config, "kit_root", lambda: tmp_path / "kit")
    folder = config.work_dir("example-reel")
    build = folder / "build"
    (build / "captions").mkdir(parents=True)
    for name in INPUTS:
        (build / name).write_text("x")
    caption = {"text": "hello", "start": 0, "end": 1, "words": [{"w": "hello", "s": 0, "e": 1}]}
    (build / "captions" / "transcript.json").write_text(json.dumps({"captions": [caption]}))
    calm = example_me / "music" / "calm"
    calm.mkdir(parents=True)
    for name in ("a.mp3", "b.mp3"):
        (calm / name).write_bytes(b"audio")
    music.log(calm / "a.mp3", "older-reel")  # the last bed used, so this video gets b.mp3

    state = {"beds": [], "problems": [], "renders": 0}

    def composite(cmd, *args, **kwargs):
        Path(cmd[cmd.index("--out") + 1]).write_text("composite")
        return subprocess.CompletedProcess(cmd, 0)

    def finishing(src, out, **kwargs):
        state["beds"].append(kwargs["music"])
        state["renders"] += 1
        Path(out).write_text(f"render {state['renders']}")
        return Path(out)

    monkeypatch.setattr(ship.subprocess, "run", composite)
    monkeypatch.setattr(ship.finish, "finish", finishing)
    monkeypatch.setattr(ship.qa_captions, "check", lambda *args, **kwargs: list(state["problems"]))
    state["folder"], state["me"] = folder, example_me
    return state


def ledger_rows(me, slug):
    return [line for line in (me / "music" / "MUSIC.md").read_text().splitlines() if f"| {slug} |" in line]


def test_three_ships_of_one_video_use_one_bed_and_log_one_row(video):
    for _ in range(3):
        assert ship.main(["example-reel"]) == 0
    calm = video["me"] / "music" / "calm"
    assert video["beds"] == [calm / "b.mp3"] * 3
    assert len(ledger_rows(video["me"], "example-reel")) == 1
    assert (video["folder"] / "final.mp4").read_text() == "render 3"


def test_a_new_final_clears_the_old_review_pass(video):
    review = video["folder"] / "review"
    review.mkdir()
    (review / "review.json").write_text('{"passed": true}')
    assert ship.main(["example-reel"]) == 0
    assert not (review / "review.json").exists(), "the pass described the previous final"


def test_a_render_that_fails_the_speech_gate_does_not_replace_the_final(video):
    folder = video["folder"]
    (folder / "final.mp4").write_text("the previous final")
    (folder / "review").mkdir()
    (folder / "review" / "review.json").write_text('{"passed": true}')
    video["problems"].append('"seeds" (at 1.45 s in the edit) is missing from the final audio.')
    assert ship.main(["example-reel"]) == 1
    assert (folder / "final.mp4").read_text() == "the previous final"
    assert (folder / "review" / "review.json").exists(), "the pass still describes the final that is there"
    assert (folder / "build" / "final.next.mp4").read_text() == "render 1", "kept to listen to"


def test_music_none_ships_with_no_bed_and_logs_nothing(video):
    assert ship.main(["example-reel", "--music", "none"]) == 0
    assert video["beds"] == [None]
    assert ledger_rows(video["me"], "example-reel") == []
