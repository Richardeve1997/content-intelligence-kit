"""ship.py: the sound effects, the bed under the voice, the finish at speed 1.0 and the speech gate.

These run the real kit.finish on a small fixture (a grey picture, a speech-like voice-over with a pause, a noise
bed in me/music). The speech gate is a stand-in: its own tests cover it, and it needs the speech model.
"""

from __future__ import annotations

import json
import os
import re
import subprocess

import pytest

np = pytest.importorskip("numpy")

from mini_me_helpers import decode, duration, ffmpeg, load, speech_samples, write_wav  # noqa: E402

from kit import config, music  # noqa: E402

ship = load("ship")

TALK_1, GAP, TALK_2 = (0.2, 2.4), (2.4, 4.0), (4.0, 5.8)
SECONDS = 6.0


MADE = 1_700_000_000.0  # file times are set from here, never left to the clock (a coarse filesystem ties them)


def stamp(path, seconds_after: float) -> None:
    """Give a file a time `seconds_after` MADE, so which file is newer never rests on the order of two writes."""
    os.utime(path, (MADE + seconds_after, MADE + seconds_after))


def segment_lufs(path, start: float, end: float) -> float:
    text = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-ss", f"{start}", "-t", f"{end - start}", "-i", str(path),
                           "-vn", "-af", "ebur128", "-f", "null", "-"], capture_output=True, text=True).stderr
    return float(re.search(r"I:\s+(-?[\d.]+) LUFS", text[text.rindex("Summary:"):]).group(1))


@pytest.fixture
def reel(kit_home, example_me, monkeypatch):
    """work/example-reel with a rendered picture, a voice-over with a pause, words, one page, and a bed."""
    folder = config.work_dir("example-reel")
    build = folder / "build"
    build.mkdir(parents=True)
    ffmpeg("-f", "lavfi", "-i", f"color=c=gray:s=108x192:r=60:d={SECONDS}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
           build / "picture.mp4")
    pattern = [("gap", TALK_1[0]), ("talk", TALK_1[1] - TALK_1[0]), ("gap", GAP[1] - GAP[0]),
               ("talk", TALK_2[1] - TALK_2[0]), ("gap", SECONDS - TALK_2[1])]
    wav = write_wav(speech_samples(pattern), folder / "vo.wav")
    ffmpeg("-i", wav, "-af", "loudnorm=I=-13:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "256k", "-ar", 48000, "-ac", 2,
           folder / "vo.m4a")
    wav.unlink()
    words = [{"w": f"w{i}", "s": round(a, 2), "e": round(a + 0.3, 2)}
             for i, a in enumerate([x / 10 for x in range(3, 22, 4)] + [x / 10 for x in range(41, 56, 4)])]
    (folder / "words.json").write_text(json.dumps({"dur": SECONDS, "words": words}))
    (folder / "beats.json").write_text(json.dumps({"duration": SECONDS, "pages": [
        {"t": 0.0, "text": "FRESH BASIL", "pose": "point_up", "field": "dark", "prop": "none"}]}))
    calm = example_me / "music" / "calm"
    calm.mkdir(parents=True)
    ffmpeg("-f", "lavfi", "-i", "anoisesrc=color=pink:amplitude=0.5:d=8:r=48000", "-ac", 2, calm / "bed.wav")
    for name in ("vo.m4a", "words.json", "beats.json"):
        stamp(folder / name, 0)
    stamp(build / "picture.mp4", 100)  # rendered after the pages and the voice-over, as in a real build
    state = {"checked": [], "problems": [], "refreshed": []}

    def refresh(slug):
        state["refreshed"].append(slug)
        return False  # no browser here: the hits come from beats.json

    monkeypatch.setattr(ship, "refresh_hits", refresh)

    def speech_gate(final, words, **kwargs):
        state["checked"].append({"final": final, "speed": kwargs.get("speed"), "words": len(words)})
        return list(state["problems"])

    monkeypatch.setattr(ship.qa_captions, "check", speech_gate)
    state["folder"], state["me"] = folder, example_me
    return state


def test_bed_15db_under_voice(reel):
    assert ship.main(["example-reel"]) == 0
    final = reel["folder"] / "final.mp4"
    speech = segment_lufs(final, TALK_1[0] + 0.2, TALK_1[1] - 0.1)
    bed_alone = segment_lufs(final, GAP[0] + 0.25, GAP[1] - 0.25)
    assert speech - bed_alone >= 15.0, (speech, bed_alone)
    mix = json.loads((reel["folder"] / "build" / "mix.json").read_text())
    assert mix["under_db"] >= 15.0 and mix["bed"] == "bed.wav"
    assert abs(mix["bed_lufs"] - mix["bed_lufs_set"]) <= 1.5, "the bed is measured, and lands near its setting"
    assert mix["under_db"] == round(mix["voice_lufs"] - mix["bed_lufs"], 1)


def test_the_finish_runs_at_speed_one_with_the_house_loudness(reel):
    assert ship.main(["example-reel"]) == 0
    final = reel["folder"] / "final.mp4"
    assert abs(duration(final) - SECONDS) < 0.05, "nothing is sped up: the voice-over is already at its pace"
    assert reel["checked"] == [{"final": reel["folder"] / "build" / "final.next.mp4", "speed": 1.0, "words": 9}]
    text = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(final), "-af", "ebur128=peak=true", "-f", "null",
                           "-"], capture_output=True, text=True).stderr
    summary = text[text.rindex("Summary:"):]
    integrated = float(re.search(r"I:\s+(-?[\d.]+) LUFS", summary).group(1))
    peak = float(re.search(r"Peak:\s+(-?[\d.]+) dBFS", summary).group(1))
    assert abs(integrated + 13) <= 0.5 and peak <= -1.5


def test_the_hits_land_on_their_times(reel):
    build = reel["folder"] / "build"
    (build / "hits.json").write_text(json.dumps({"hits": [{"kind": "whoosh", "t": 3.2, "gain": 0.6}]}))
    assert ship.main(["example-reel", "--music", "none"]) == 0
    samples = decode(build / "edit.mov")
    rate = 48000
    gap_before = np.abs(samples[int(2.6 * rate) : int(2.9 * rate)]).max()
    around = np.abs(samples[int(3.05 * rate) : int(3.35 * rate)]).max()
    assert around > 10 * max(gap_before, 1e-4), (around, gap_before)
    assert json.loads((build / "mix.json").read_text())["hits"] == 1


def test_without_hits_json_the_flips_and_stamps_come_from_the_pages(reel):
    folder = reel["folder"]
    (folder / "beats.json").write_text(json.dumps({"duration": SECONDS, "pages": [
        {"t": 0.0, "text": "FRESH BASIL", "pose": "point_up", "field": "dark", "prop": "none"},
        {"t": 2.5, "text": "WATER EARLY", "pose": "thumbs_up", "field": "light", "prop": "stamp: AM"},
        {"t": 4.2, "text": "SAME FIELD", "pose": "shrug", "field": "light", "prop": "none"}]}))
    hits = ship.hits_for(folder)
    assert [(h.kind, h.t) for h in hits] == [("whoosh", 2.5), ("thud", 2.5 + ship.STAMP_DELAY)]


def test_a_new_final_clears_the_old_review_pass(reel):
    review = reel["folder"] / "review"
    review.mkdir()
    (review / "review.json").write_text('{"passed": true}')
    assert ship.main(["example-reel"]) == 0
    assert not (review / "review.json").exists(), "the pass described the previous final"


def test_a_render_that_fails_the_speech_gate_does_not_replace_the_final(reel, capsys):
    folder = reel["folder"]
    (folder / "final.mp4").write_bytes(b"the previous final")
    (folder / "review").mkdir()
    (folder / "review" / "review.json").write_text('{"passed": true}')
    reel["problems"].append('"basil" (at 1.20 s in the edit, before the speed-up) is missing from the final audio.')
    assert ship.main(["example-reel"]) == 1
    assert (folder / "final.mp4").read_bytes() == b"the previous final"
    assert (folder / "review" / "review.json").exists(), "the old pass still describes the old final"
    assert (folder / "build" / "final.next.mp4").is_file()
    assert "basil" in capsys.readouterr().out
    assert not (folder / "build" / "mix.json").exists(), "levels are recorded only for a final that passed"
    ledger = reel["me"] / "music" / "MUSIC.md"
    assert "| example-reel |" not in ledger.read_text(), "the bed is logged only once the Reel passed"


def test_one_bed_per_video_logged_once(reel):
    for _ in range(2):
        assert ship.main(["example-reel"]) == 0
    rows = [line for line in (reel["me"] / "music" / "MUSIC.md").read_text().splitlines() if "| example-reel |" in line]
    assert len(rows) == 1 and music.chosen("example-reel").name == "bed.wav"


def test_the_bed_never_sits_closer_than_15_db(reel, capsys):
    assert ship.main(["example-reel", "--under", "11"]) == 1
    assert "at least 15 dB" in capsys.readouterr().out
    assert not (reel["folder"] / "final.mp4").exists()


def test_a_missing_render_is_a_plain_sentence(reel, capsys):
    (reel["folder"] / "build" / "picture.mp4").unlink()
    assert ship.main(["example-reel"]) == 1
    assert "picture.mp4 is missing" in capsys.readouterr().out


def test_a_render_older_than_the_pages_is_refused(reel, capsys):
    beats = reel["folder"] / "beats.json"
    beats.write_text(beats.read_text())
    stamp(beats, 200)  # the pages changed after the render
    assert ship.main(["example-reel"]) == 1
    out = capsys.readouterr().out
    assert "older than beats.json" in out and "render.py example-reel" in out
    assert not (reel["folder"] / "final.mp4").exists()


def test_hits_older_than_the_scenes_are_made_again(reel, monkeypatch):
    build = reel["folder"] / "build"
    (build / "hits.json").write_text(json.dumps({"hits": []}))
    scenes = build / "hf" / "lib" / "scenes.js"
    scenes.parent.mkdir(parents=True)
    scenes.write_text("/* a stamp added after the last check */\n")
    stamp(build / "hits.json", 200)
    stamp(scenes, 300)  # the scenes changed after the last page check
    stamp(build / "picture.mp4", 400)  # and the render came after that

    def refresh(slug):
        reel["refreshed"].append(slug)
        (build / "hits.json").write_text(json.dumps({"hits": [{"kind": "thud", "t": 1.0, "gain": 0.5}]}))
        return True

    monkeypatch.setattr(ship, "refresh_hits", refresh)
    assert ship.main(["example-reel", "--music", "none"]) == 0
    assert reel["refreshed"] == ["example-reel"]
    assert json.loads((build / "mix.json").read_text())["hits"] == 1


def test_fresh_hits_are_used_as_they_are(reel):
    build = reel["folder"] / "build"
    (build / "hits.json").write_text(json.dumps({"hits": [{"kind": "whoosh", "t": 3.2, "gain": 0.6}]}))
    stamp(build / "hits.json", 200)  # the page check came after the pages
    assert ship.main(["example-reel", "--music", "none"]) == 0
    assert reel["refreshed"] == [], "nothing changed since the page check"


def test_stale_hits_that_cannot_be_made_again_are_named(reel, capsys):
    build = reel["folder"] / "build"
    (build / "hits.json").write_text(json.dumps({"hits": [{"kind": "whoosh", "t": 3.2, "gain": 0.6}]}))
    scenes = build / "hf" / "lib" / "scenes.js"
    scenes.parent.mkdir(parents=True)
    scenes.write_text("/* changed after the last page check */\n")
    stamp(build / "hits.json", 200)
    stamp(scenes, 300)
    stamp(build / "picture.mp4", 400)
    assert ship.main(["example-reel", "--music", "none"]) == 0  # the fixture's page check cannot run (no browser)
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if "hits.json" in line]
    assert len(lines) == 1 and "older than scenes.js" in lines[0] and "could not run" in lines[0], out
    assert json.loads((build / "mix.json").read_text())["hits"] == 1, "the older hits are still used, and said so"


def test_missing_hits_that_cannot_be_made_say_where_the_sound_comes_from(reel, capsys):
    assert ship.main(["example-reel", "--music", "none"]) == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if "hits.json" in line]
    assert len(lines) == 1 and "missing" in lines[0] and "beats.json" in lines[0], lines


def test_a_page_check_with_no_playwright_here_counts_as_one_that_cannot_run(monkeypatch):
    """Only SystemExit (no browser) used to count as "the page check cannot run here". With Playwright not
    installed the import fails instead, which is the same case: the sound follows the older hits or the pages."""
    import check as page_check  # the module refresh_hits imports, beside ship.py

    def no_playwright(slug, at):
        raise ModuleNotFoundError("No module named 'playwright'")

    monkeypatch.setattr(page_check, "run", no_playwright)
    assert ship.refresh_hits("example-reel") is False


def test_a_page_check_that_breaks_part_way_is_a_plain_sentence(monkeypatch):
    import check as page_check

    def closed(slug, at):
        raise RuntimeError("the browser closed while the page loaded")

    monkeypatch.setattr(page_check, "run", closed)
    with pytest.raises(config.KitError) as caught:
        ship.refresh_hits("example-reel")
    said = str(caught.value)
    assert "the browser closed while the page loaded" in said and "check.py example-reel" in said, said
