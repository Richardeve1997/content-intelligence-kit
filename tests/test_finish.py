"""kit.finish and kit.music: the delivery pass and the creator's music beds.

finish() speeds the edit up, lays the bed under the voice, brings the mix to -13 LUFS with the true
peak at or below -1.5 dBTP, encodes the picture once and copies it into the delivered file. The
fixtures are made with ffmpeg at test time, so no media is committed.
"""

import json
import re
import subprocess
from pathlib import Path

import pytest

from kit import finish, media, music

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def private_slots(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "_slots_dir", lambda: tmp_path / "slots")
    monkeypatch.delenv(media.SLOT_ENV, raising=False)
    monkeypatch.setattr(media, "MIN_FREE_GB", 0.0)


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *args], check=True)


def speechlike(out: Path, seconds: float = 6.0, level: float = 0.08) -> Path:
    """A small vertical clip whose audio rises and falls like talking: pink noise in syllable-length bursts."""
    ffmpeg(
        "-f", "lavfi", "-i", f"testsrc2=s=180x320:r=30:d={seconds}",
        "-f", "lavfi", "-i", f"anoisesrc=color=pink:amplitude={level}:d={seconds}:r=48000",
        "-filter_complex",
        "[1:a]volume='0.15+0.85*pow(abs(sin(2*PI*2.6*t)),3)':eval=frame,"
        "lowpass=f=3400,highpass=f=120,aformat=channel_layouts=stereo[a]",
        "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k",
        str(out),
    )
    return out


def tone_bed(out: Path, seconds: float = 3.0) -> Path:
    ffmpeg(
        "-f", "lavfi", "-i", f"sine=frequency=220:d={seconds}:r=48000",
        "-f", "lavfi", "-i", f"sine=frequency=330:d={seconds}:r=48000",
        "-filter_complex", "[0:a][1:a]amix=inputs=2,aformat=channel_layouts=stereo[a]", "-map", "[a]", str(out),
    )
    return out


def ebur128(path: Path) -> dict:
    text = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True,
    ).stderr
    summary = text[text.rindex("Summary:"):]
    return {
        "I": float(re.search(r"I:\s+(-?[\d.]+) LUFS", summary).group(1)),
        "TP": float(re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", summary).group(1)),
    }


def streams(path: Path) -> dict:
    data = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout)
    return {s["codec_type"]: s for s in data["streams"]} | {"format": data["format"]}


# --- the delivery pass ---------------------------------------------------------------------------------


def test_finish_loudness(tmp_path):
    src = speechlike(tmp_path / "edit.mp4", level=0.05)
    bed = tone_bed(tmp_path / "bed.wav")
    out = finish.finish(
        src, tmp_path / "final.mp4",
        speed=1.15, music=bed, music_lufs=-24, mix_peak=0.74, fade_in=0.3, fade_out=1.2,
    )
    assert out == tmp_path / "final.mp4"
    level = ebur128(out)
    assert abs(level["I"] - (-13.0)) <= 0.5, level
    assert level["TP"] <= -1.5, level


def test_a_loud_source_is_brought_down_and_still_peaks_under_the_ceiling(tmp_path):
    src = speechlike(tmp_path / "hot.mp4", level=0.9)
    out = finish.finish(src, tmp_path / "final.mp4", speed=1.0, music=None, music_lufs=-24, mix_peak=0.74,
                        fade_in=0.3, fade_out=1.2)
    level = ebur128(out)
    assert abs(level["I"] - (-13.0)) <= 0.5, level
    assert level["TP"] <= -1.5, level


def test_finish_speeds_up_and_delivers_h264_with_192k_aac(tmp_path):
    src = speechlike(tmp_path / "edit.mp4", seconds=5.75)
    out = finish.finish(src, tmp_path / "final.mp4", speed=1.15, music=None, music_lufs=-24, mix_peak=0.74,
                        fade_in=0.3, fade_out=1.2)
    info = streams(out)
    assert info["video"]["codec_name"] == "h264"
    assert info["audio"]["codec_name"] == "aac"
    assert info["audio"]["sample_rate"] == "48000"
    assert abs(int(info["audio"]["bit_rate"]) - 192000) < 20000
    assert float(info["format"]["duration"]) == pytest.approx(5.75 / 1.15, abs=0.1)
    assert (info["video"]["width"], info["video"]["height"]) == (180, 320)


def test_at_normal_speed_the_picture_is_copied_not_encoded_again(tmp_path):
    src = speechlike(tmp_path / "edit.mp4", seconds=3)
    out = finish.finish(src, tmp_path / "final.mp4", speed=1.0, music=None, music_lufs=-24, mix_peak=0.74,
                        fade_in=0.3, fade_out=1.2)

    def video_packets(path):
        lines = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v", "-c", "copy", "-f", "framemd5", "-"],
            capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        # size and hash of each packet; the timestamps can be rewritten by the muxer
        return [tuple(part.strip() for part in line.split(",")[-2:]) for line in lines if not line.startswith("#")]

    assert video_packets(out) == video_packets(src)


def test_the_bed_sits_under_the_voice(tmp_path):
    """Before the final gain, the bed alone measures at music_lufs, far under a voice at -13 LUFS."""
    silent_voice = tmp_path / "quiet.mp4"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=180x320:r=30:d=4", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
           "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(silent_voice))
    bed = tone_bed(tmp_path / "bed.wav", seconds=6)
    level = finish.mix_level(silent_voice, speed=1.0, music=bed, music_lufs=-24, fade_in=0.3, fade_out=1.2)
    assert level == pytest.approx(-24, abs=1.5)


def test_measure_reports_loudness_and_true_peak(tmp_path):
    src = speechlike(tmp_path / "edit.mp4", seconds=3)
    reading = finish.measure(src)
    direct = ebur128(src)
    assert reading.integrated == pytest.approx(direct["I"], abs=0.15)
    assert reading.true_peak == pytest.approx(direct["TP"], abs=0.15)
    assert reading.lra >= 0


def test_a_missing_bed_file_is_a_plain_sentence(tmp_path):
    src = speechlike(tmp_path / "edit.mp4", seconds=2)
    with pytest.raises(finish.KitError) as caught:
        finish.finish(src, tmp_path / "final.mp4", speed=1.15, music=tmp_path / "nope.mp3", music_lufs=-24,
                      mix_peak=0.74, fade_in=0.3, fade_out=1.2)
    assert "nope.mp3" in str(caught.value)


@pytest.mark.parametrize("bad", [0.05, 1.5])
def test_the_mix_peak_must_be_a_linear_ceiling(tmp_path, bad):
    src = speechlike(tmp_path / "edit.mp4", seconds=2)
    with pytest.raises(finish.KitError):
        finish.finish(src, tmp_path / "final.mp4", speed=1.15, music=None, music_lufs=-24, mix_peak=bad,
                      fade_in=0.3, fade_out=1.2)


def test_the_house_values_are_the_defaults():
    assert finish.HOUSE == {
        "speed": 1.15, "music_lufs": -24.0, "mix_peak": 0.74, "fade_in": 0.3, "fade_out": 1.2,
    }
    assert finish.TARGET == {"I": -13.0, "TP": -1.5, "LRA": 11.0}


# --- the music library ------------------------------------------------------------------------------------


def _track(folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(b"not really audio")
    return path


def test_music_pick_skips_last(example_me):
    calm = example_me / "music" / "calm"
    first, second = _track(calm, "a-morning.mp3"), _track(calm, "b-evening.mp3")
    music.log(first, "reel-one")
    assert music.pick("calm") == second
    music.log(second, "reel-two")
    assert music.pick("calm") == first


def test_pick_prefers_a_track_never_used(example_me):
    calm = example_me / "music" / "calm"
    used, fresh, also_used = (_track(calm, n) for n in ("a.mp3", "b.m4a", "c.wav"))
    music.log(used, "reel-one")
    music.log(also_used, "reel-two")
    assert music.pick("calm") == fresh


def test_pick_with_no_mood_looks_in_every_folder(example_me):
    only = _track(example_me / "music" / "upbeat", "only.mp3")
    assert music.pick(None) == only


def test_pick_gives_none_when_there_is_no_music(example_me):
    assert music.pick(None) is None
    assert music.pick("calm") is None
    _track(example_me / "music" / "calm", "notes.txt")
    assert music.pick("calm") is None, "only audio files count"


def test_a_single_track_is_used_again_rather_than_none(example_me):
    only = _track(example_me / "music" / "calm", "only.mp3")
    music.log(only, "reel-one")
    assert music.pick("calm") == only


def test_log_appends_a_row_to_the_ledger(example_me):
    track = _track(example_me / "music" / "calm", "a-morning.mp3")
    music.log(track, "example-reel")
    lines = (example_me / "music" / "MUSIC.md").read_text().splitlines()
    assert lines[-1].startswith("| ") and lines[-1].endswith(" | example-reel | calm/a-morning.mp3 |")
    assert re.match(r"\| \d{4}-\d{2}-\d{2} \|", lines[-1])
    assert "| date | slug | track |" in lines


def test_log_starts_a_ledger_when_there_is_none(example_me):
    (example_me / "music" / "MUSIC.md").unlink()
    track = _track(example_me / "music" / "calm", "a.mp3")
    music.log(track, "example-reel")
    text = (example_me / "music" / "MUSIC.md").read_text()
    assert "| date | slug | track |" in text and "calm/a.mp3" in text


# --- one bed per video ----------------------------------------------------------------------------------------


def _ledger_rows(me, slug):
    return [line for line in (me / "music" / "MUSIC.md").read_text().splitlines() if f"| {slug} |" in line]


def test_a_video_keeps_its_bed_across_finishes_and_logs_one_row(example_me):
    calm = example_me / "music" / "calm"
    first, second = _track(calm, "a-morning.mp3"), _track(calm, "b-evening.mp3")
    music.log(first, "older-reel")  # the last bed used, so a new video gets the other one
    for _ in range(3):
        bed = music.for_video("example-reel")
        assert bed == second
        music.log(bed, "example-reel")
    assert len(_ledger_rows(example_me, "example-reel")) == 1
    assert len(_ledger_rows(example_me, "older-reel")) == 1


def test_a_new_bed_for_a_video_replaces_its_row(example_me):
    calm = example_me / "music" / "calm"
    first, second = _track(calm, "a.mp3"), _track(calm, "b.mp3")
    music.log(first, "example-reel")
    music.log(second, "example-reel")
    rows = _ledger_rows(example_me, "example-reel")
    assert len(rows) == 1 and rows[0].endswith("| calm/b.mp3 |")
    assert music.for_video("example-reel") == second


def test_a_mood_the_video_bed_is_not_in_picks_again(example_me):
    calm = _track(example_me / "music" / "calm", "a.mp3")
    upbeat = _track(example_me / "music" / "upbeat", "b.mp3")
    music.log(calm, "example-reel")
    assert music.for_video("example-reel") == calm
    assert music.for_video("example-reel", "calm") == calm
    assert music.for_video("example-reel", "upbeat") == upbeat


def test_a_bed_that_was_deleted_is_replaced(example_me):
    gone = _track(example_me / "music" / "calm", "a.mp3")
    other = _track(example_me / "music" / "calm", "b.mp3")
    music.log(gone, "example-reel")
    gone.unlink()
    assert music.for_video("example-reel") == other


def test_three_finishes_from_the_command_line_use_one_bed_and_one_row(example_me, tmp_path, monkeypatch):
    calm = example_me / "music" / "calm"
    _track(calm, "a.mp3")
    _track(calm, "b.mp3")
    used = []

    def fake_finish(src, out, **kwargs):
        used.append(kwargs["music"])
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"finished")
        return Path(out)

    monkeypatch.setattr(finish, "finish", fake_finish)
    src, out = tmp_path / "edit.mp4", tmp_path / "example-reel" / "final.mp4"
    src.write_bytes(b"edit")
    for _ in range(3):
        assert finish.main([str(src), str(out), "--music", "auto"]) == 0
    assert len(set(used)) == 1 and used[0] is not None
    assert len(_ledger_rows(example_me, "example-reel")) == 1


# --- a failed finish leaves the old file alone ------------------------------------------------------------------


def test_a_finish_that_misses_the_peak_target_leaves_out_untouched(tmp_path, monkeypatch):
    src = speechlike(tmp_path / "edit.mp4", seconds=2)
    out = tmp_path / "final.mp4"
    out.write_bytes(b"the previous final")
    # every reading is on the loudness target but peaks at 0 dBTP, above the -1.5 limit
    monkeypatch.setattr(finish, "measure", lambda path: finish.Loudness(-13.0, 0.0, 3.0))
    with pytest.raises(finish.KitError) as caught:
        finish.finish(src, out, speed=1.15, music=None, music_lufs=-24, mix_peak=0.74, fade_in=0.3, fade_out=1.2)
    assert "peaks" in str(caught.value)
    assert out.read_bytes() == b"the previous final"
    assert not list(tmp_path.glob(".finish-*")), "the temporary files are cleaned up"


def test_a_finish_that_misses_the_loudness_target_leaves_no_new_file(tmp_path, monkeypatch):
    src = speechlike(tmp_path / "edit.mp4", seconds=2)
    out = tmp_path / "final.mp4"
    monkeypatch.setattr(finish, "_solve", lambda mix, work, ceiling: mix)
    monkeypatch.setattr(finish, "measure", lambda path: finish.Loudness(-20.0, -6.0, 3.0))
    with pytest.raises(finish.KitError) as caught:
        finish.finish(src, out, speed=1.0, music=None, music_lufs=-24, mix_peak=0.74, fade_in=0.3, fade_out=1.2)
    assert "LUFS" in str(caught.value)
    assert not out.exists()
