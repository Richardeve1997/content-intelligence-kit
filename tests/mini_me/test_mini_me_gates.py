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


def finished(folder, *, margin_block=False, seconds=3.0):
    """work/<video>/ with final.mp4, words.json, build/mix.json and build/hf/index.html."""
    folder.mkdir(parents=True, exist_ok=True)
    talk = min(2.4, seconds - 0.4)
    wav = write_wav(speech_samples([("gap", 0.2), ("talk", talk), ("gap", seconds - 0.2 - talk)]), folder / "voice.wav")
    box = ",drawbox=x=50:y=900:w=120:h=60:color=white:t=fill" if margin_block else ""
    gain = 0.0
    for _ in range(3):  # the house level: gain solved on the finished file, a limiter holding the peaks down
        ffmpeg("-f", "lavfi", "-i", f"color=c=0x303030:s=1080x1920:r=60:d={seconds}",
               "-f", "lavfi", "-i", f"testsrc2=s=540x540:r=60:d={seconds}", "-i", wav,
               "-filter_complex", f"[0:v][1:v]overlay=270:700{box}[v];[2:a]pan=stereo|c0=c0|c1=c0,"
               f"volume={gain:.2f}dB,aresample=192000,alimiter=limit=0.7:level=0:latency=1,aresample=48000[a]",
               "-map", "[v]", "-map", "[a]", "-t", seconds, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", 60,
               "-c:a", "aac", "-b:a", "192k", folder / "final.mp4")
        level = finish.measure(folder / "final.mp4").integrated
        if abs(level + 13.0) <= 0.3:
            break
        gain += -13.0 - level
    wav.unlink()
    (folder / "words.json").write_text(json.dumps({"words": [{"w": "fresh", "s": 0.3, "e": 0.6},
                                                             {"w": "basil", "s": 2.2, "e": 2.6}]}))
    (folder / "build" / "hf").mkdir(parents=True)
    (folder / "build" / "mix.json").write_text(json.dumps({"voice_lufs": -13.0, "bed_lufs": -31.0, "under_db": 18.0,
                                                          "bed": "bed.wav", "hits": 3, "speed": 1.0}))
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


def test_a_bed_too_close_to_the_voice_fails(tmp_path):
    folder = finished(tmp_path / "example-reel")
    mix = json.loads((folder / "build" / "mix.json").read_text())
    mix.update(bed_lufs=-24.0, under_db=11.0)
    (folder / "build" / "mix.json").write_text(json.dumps(mix))
    done = gates(folder)
    assert done.returncode == 1 and "FAIL  the bed sits closer than 15 dB" in done.stdout


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
