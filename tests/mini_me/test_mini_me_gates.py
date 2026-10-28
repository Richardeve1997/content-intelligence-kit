"""gates.sh and margin_scan.py: the checks on a finished mini me Reel.

The fixture is a finished-looking file made with ffmpeg: 1080x1920 at 60 fps, a flat field, sound at -13 LUFS.
The speech gate is skipped here (--no-speech); kit.qa_captions has its own tests.
"""

from __future__ import annotations

import json
import subprocess

import pytest

np = pytest.importorskip("numpy")

from mini_me_helpers import KIT, ROOT, ffmpeg, load, speech_samples, write_wav  # noqa: E402

from kit import finish  # noqa: E402

margin_scan = load("margin_scan")
GATES = KIT / "gates.sh"


def finished(folder, *, margin_block=False, seconds=3.0, still=False, level=-13.0, sound_short=0.0, last_word=2.6):
    """work/<video>/ with final.mp4, words.json, build/mix.json and build/hf/index.html.

    still: no motion at all; level: the sound's loudness; sound_short: the sound ends this much before the picture;
    last_word: when the last word ends.
    """
    folder.mkdir(parents=True, exist_ok=True)
    talk = min(2.4, seconds - 0.4)
    wav = write_wav(speech_samples([("gap", 0.2), ("talk", talk), ("gap", seconds - 0.2 - talk)]), folder / "voice.wav")
    box = ",drawbox=x=50:y=900:w=120:h=60:color=white:t=fill" if margin_block else ""
    gain, moving = 0.0, "color=c=0x606060:s=540x540:r=60" if still else "testsrc2=s=540x540:r=60"
    trim = f",atrim=0:{seconds - sound_short:.3f}" if sound_short else ""
    for _ in range(3):  # the target level: gain solved on the finished file, a limiter holding the peaks down
        ffmpeg("-f", "lavfi", "-i", f"color=c=0x303030:s=1080x1920:r=60:d={seconds}",
               "-f", "lavfi", "-i", f"{moving}:d={seconds}", "-i", wav,
               "-filter_complex", f"[0:v][1:v]overlay=270:700{box}[v];[2:a]pan=stereo|c0=c0|c1=c0,"
               f"volume={gain:.2f}dB,aresample=192000,alimiter=limit=0.7:level=0:latency=1,aresample=48000{trim}[a]",
               "-map", "[v]", "-map", "[a]", "-t", seconds, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", 60,
               "-c:a", "aac", "-b:a", "192k", folder / "final.mp4")
        reached = finish.measure(folder / "final.mp4").integrated
        if abs(reached - level) <= 0.3:
            break
        gain += level - reached
    wav.unlink()
    (folder / "words.json").write_text(json.dumps({"words": [{"w": "fresh", "s": 0.3, "e": 0.6},
                                                             {"w": "basil", "s": last_word - 0.4, "e": last_word}]}))
    (folder / "build" / "hf" / "lib").mkdir(parents=True)
    (folder / "build" / "mix.json").write_text(json.dumps({"voice_lufs": -13.0, "bed_lufs": -31.0, "bed_lufs_set": -31.0,
                                                          "under_db": 18.0, "bed": "bed.wav", "hits": 3, "speed": 1.0}))
    (folder / "build" / "hf" / "index.html").write_text("<div id=\"mm\"></div><audio src=\"assets/vo.m4a\"></audio>")
    return folder


def gates(folder):
    return subprocess.run(["bash", str(GATES), str(folder), "--no-speech"], capture_output=True, text=True, cwd=ROOT,
                          timeout=600)


def test_a_clean_reel_passes_every_gate(tmp_path):
    folder = finished(tmp_path / "example-reel")
    done = gates(folder)
    assert done.returncode == 0, done.stdout + done.stderr
    for gate in ("1080x1920, 60 fps", "nothing inside the side margins", "no caption track", "no frozen stretch",
                 "loudness", "bed", "picture and sound", "end"):
        assert f"PASS  {gate}" in done.stdout, gate
    assert (folder / "review" / "sheet.jpg").is_file() and (folder / "review" / "open.jpg").is_file()


def test_content_in_a_margin_fails(tmp_path):
    folder = finished(tmp_path / "example-reel", margin_block=True)
    done = gates(folder)
    assert done.returncode == 1
    assert "FAIL  something crosses a side margin" in done.stdout


def test_a_caption_track_fails(tmp_path):
    folder = finished(tmp_path / "example-reel")
    (folder / "build" / "hf" / "index.html").write_text('<div class="clip" id="cap-1">FRESH</div>')
    done = gates(folder)
    assert done.returncode == 1 and "FAIL  index.html carries a caption track" in done.stdout


def test_a_caption_track_built_in_the_scenes_fails(tmp_path):
    folder = finished(tmp_path / "example-reel")
    (folder / "build" / "hf" / "lib" / "scenes.js").write_text("Stage.page(1, { build: function (c) { captions(c); } });\n")
    done = gates(folder)
    assert done.returncode == 1 and "FAIL  lib/scenes.js builds a caption track" in done.stdout


@pytest.mark.parametrize("problem, made, failure", [
    ("frozen", {"still": True}, "FAIL  the picture holds still for 0.8 s or more"),
    ("quiet", {"level": -20.0}, "FAIL  loudness is off target"),
    ("sound ends early", {"sound_short": 0.3}, "FAIL  the picture and the sound end more than a frame apart"),
    ("long tail", {"last_word": 1.6}, "FAIL  the Reel runs on too long after the last word"),
])
def test_each_failing_gate_is_named(tmp_path, problem, made, failure):
    folder = finished(tmp_path / "example-reel", **made)
    done = gates(folder)
    assert done.returncode == 1, problem
    assert failure in done.stdout, done.stdout
    assert "== 1 gate(s) failed" in done.stdout, "only the gate with the problem fails"


def test_a_bed_too_close_to_the_voice_fails(tmp_path):
    folder = finished(tmp_path / "example-reel")
    mix = json.loads((folder / "build" / "mix.json").read_text())
    mix.update(bed_lufs=-24.0, under_db=11.0)
    (folder / "build" / "mix.json").write_text(json.dumps(mix))
    done = gates(folder)
    assert done.returncode == 1 and "FAIL  the bed measures closer than 15 dB" in done.stdout


def test_a_missing_final_is_a_plain_sentence(tmp_path):
    done = gates(tmp_path / "nothing-here")
    assert done.returncode == 2 and "does not exist" in done.stdout


# --- margin_scan on its own ----------------------------------------------------------------------------------------------


def frame(tmp_path, name, block=None):
    from PIL import Image

    image = np.full((1920, 1080, 3), (22, 48, 43), np.uint8)
    image[:, 102:978] = (30, 60, 52)  # a slightly different field inside the safe lines is fine
    image[300:900, 200:880] = (240, 240, 240)
    if block:
        x0, x1 = block
        image[1000:1100, x0:x1] = (255, 255, 255)
    path = tmp_path / name
    Image.fromarray(image).save(path)
    return path


def test_margin_scan_inside_90_990(tmp_path):
    assert margin_scan.main([str(frame(tmp_path, "clean.png"))]) == 0
    assert margin_scan.main([str(frame(tmp_path, "left.png", (60, 300)))]) == 1
    assert margin_scan.main([str(frame(tmp_path, "right.png", (950, 1000)))]) == 1
    assert margin_scan.main([str(frame(tmp_path, "inside.png", (95, 985)))]) == 0, "x 90 to 990 is the safe area"


def test_margin_scan_reads_a_video_and_skips_windows(tmp_path, capsys):
    folder = finished(tmp_path / "example-reel", margin_block=True, seconds=1.0)
    assert margin_scan.main([str(folder / "final.mp4")]) == 1
    assert "frames 60 flagged 60" in capsys.readouterr().out
    assert margin_scan.main([str(folder / "final.mp4"), "--skip", "0-2"]) == 0
