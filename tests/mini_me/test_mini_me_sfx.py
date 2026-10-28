"""kit.sfx: the whoosh on every field flip and the thud on every stamp, made from numbers.

The kit ships no audio files, so both sounds are synthesised with numpy from a fixed seed. These tests
read the WAV files back with the standard library and check what a listener would notice: length, where
the loudest moment sits, how bright or low the sound is, and that the same call always writes the same file.
"""

from __future__ import annotations

import subprocess
import wave
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from kit import sfx  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".aif", ".aiff", ".flac", ".ogg", ".opus", ".aac", ".caf"}


def read(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as handle:
        rate, channels, width = handle.getframerate(), handle.getnchannels(), handle.getsampwidth()
        frames = handle.readframes(handle.getnframes())
    assert width == 2, "16-bit PCM"
    data = np.frombuffer(frames, "<i2").astype(np.float64) / 32768.0
    return data.reshape(-1, channels), rate


def loudest_moment(samples: np.ndarray, rate: int, window: float = 0.01) -> float:
    mono = np.abs(samples).max(axis=1)
    size = max(1, int(window * rate))
    energy = np.convolve(mono**2, np.ones(size), mode="same")
    return float(np.argmax(energy) / rate)


def share_below(samples: np.ndarray, rate: int, hertz: float) -> float:
    spectrum = np.abs(np.fft.rfft(samples.mean(axis=1))) ** 2
    freqs = np.fft.rfftfreq(len(samples), 1 / rate)
    return float(spectrum[freqs < hertz].sum() / spectrum.sum())


def test_whoosh_is_a_short_bright_sweep(tmp_path):
    out = sfx.whoosh(tmp_path / "whoosh.wav")
    assert out == tmp_path / "whoosh.wav" and out.is_file()
    samples, rate = read(out)
    assert rate == sfx.SR == 48000 and samples.shape[1] == 2
    length = len(samples) / rate
    assert 0.3 <= length <= 0.6, length
    assert 0.8 <= np.abs(samples).max() <= 0.95, "normalised just under full scale"
    assert abs(loudest_moment(samples, rate) - sfx.PEAK["whoosh"]) <= 0.04
    assert share_below(samples, rate, 1000) < 0.8, "a whoosh carries air above 1 kHz"
    # it moves across the stereo field: left leads at the start, right at the end
    head, tail = samples[: len(samples) // 4], samples[-len(samples) // 3 :]
    assert np.abs(head[:, 0]).sum() > np.abs(head[:, 1]).sum()
    assert np.abs(tail[:, 1]).sum() > np.abs(tail[:, 0]).sum()


def test_thud_is_a_short_low_impact(tmp_path):
    out = sfx.thud(tmp_path / "thud.wav")
    samples, rate = read(out)
    assert 0.15 <= len(samples) / rate <= 0.5
    assert loudest_moment(samples, rate, window=0.004) <= 0.03, "the impact lands at once"
    assert abs(loudest_moment(samples, rate, window=0.004) - sfx.PEAK["thud"]) <= 0.02
    assert share_below(samples, rate, 150) > 0.6, "a thud is mostly low end"
    assert np.abs(samples[-int(0.02 * rate) :]).max() < 0.05, "it dies away before the file ends"


@pytest.mark.parametrize("make", [sfx.whoosh, sfx.thud], ids=["whoosh", "thud"])
def test_the_same_call_writes_the_same_file(tmp_path, make):
    first, second = make(tmp_path / "a.wav"), make(tmp_path / "b.wav")
    assert first.read_bytes() == second.read_bytes()


def test_ffmpeg_reads_both_files(tmp_path):
    for out in (sfx.whoosh(tmp_path / "w.wav"), sfx.thud(tmp_path / "t.wav")):
        done = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,sample_rate",
                               "-of", "csv=p=0", str(out)], capture_output=True, text=True)
        assert done.returncode == 0 and done.stdout.strip() == "pcm_s16le,48000", done.stdout + done.stderr


def test_the_folder_is_created(tmp_path):
    out = sfx.whoosh(tmp_path / "deep" / "er" / "whoosh.wav")
    assert out.is_file()


def test_a_track_lands_each_peak_on_its_time(tmp_path):
    hits = [sfx.Hit("whoosh", 1.0, 0.5), sfx.Hit("thud", 2.2, 0.6)]
    out = sfx.track(hits, 3.0, tmp_path / "hits.wav")
    samples, rate = read(out)
    assert abs(len(samples) / rate - 3.0) < 1 / rate
    first = samples[int(0.6 * rate) : int(1.6 * rate)]
    second = samples[int(2.0 * rate) : int(2.8 * rate)]
    assert abs(0.6 + loudest_moment(first, rate) - 1.0) <= 0.04
    assert abs(2.0 + loudest_moment(second, rate, window=0.004) - 2.2) <= 0.02
    assert np.abs(first).max() == pytest.approx(0.5 * np.abs(read(sfx.whoosh(tmp_path / "w.wav"))[0]).max(), rel=0.02)
    assert np.abs(samples[: int(0.5 * rate)]).max() == 0.0, "silence before the first hit"


def test_a_hit_at_the_very_start_is_trimmed_not_shifted(tmp_path):
    out = sfx.track([sfx.Hit("whoosh", 0.0, 1.0)], 1.0, tmp_path / "start.wav")
    samples, rate = read(out)
    assert loudest_moment(samples, rate) <= 0.03, "the peak still lands at 0 s"


def test_hits_past_the_end_are_left_out(tmp_path):
    out = sfx.track([sfx.Hit("thud", 5.0, 1.0)], 1.0, tmp_path / "late.wav")
    samples, _ = read(out)
    assert np.abs(samples).max() == 0.0


def test_an_unknown_sound_is_a_plain_sentence(tmp_path):
    with pytest.raises(ValueError, match="whoosh or thud"):
        sfx.track([sfx.Hit("boing", 0.5, 1.0)], 1.0, tmp_path / "x.wav")


def test_the_kit_ships_no_audio_files():
    listing = subprocess.run(["git", "-C", str(ROOT), "ls-files", "--cached", "--others", "--exclude-standard"],
                             capture_output=True, text=True).stdout.split()
    assert not [name for name in listing if Path(name).suffix.lower() in AUDIO_SUFFIXES]
