"""cut_base.py and HDR phone takes.

An iPhone films HDR by default. Turning an HDR take into normal colours needs ffmpeg's zscale filter, which
comes from the zimg library, and Homebrew's plain ffmpeg is built without it. The cut must not stop there:
it says in one plain sentence what will look wrong and how to fix it, and goes on without the colour fix.
The ffmpeg filter list is faked in most of these tests, so they behave the same on any machine.
"""

import json
import subprocess
import sys

import pytest
from uistory_helpers import ffmpeg, load

cut_base = load("cut_base")

WITH_ZSCALE = " ... scale             V->V       Scale the input video size.\n .S. zscale            V->V       Apply resizing, colorspace and bit depth conversion.\n"
WITHOUT_ZSCALE = " ... scale             V->V       Scale the input video size.\n T.. tonemap           V->V       Conversion to/from different dynamic ranges. Not zscale.\n"

HLG, PQ = "arib-std-b67", "smpte2084"


@pytest.fixture
def take(monkeypatch):
    """Answers for ffprobe and for ffmpeg's filter list, set per test."""
    state = {"size": (1080, 1920), "transfer": HLG, "filters": WITH_ZSCALE}
    monkeypatch.setattr(cut_base, "_probe", lambda raw: (*state["size"], state["transfer"]))
    monkeypatch.setattr(cut_base, "_filter_listing", lambda: state["filters"])
    return state


def test_an_hdr_take_is_tone_mapped_when_ffmpeg_has_zscale(take, capsys):
    for transfer in (HLG, PQ):
        take["transfer"] = transfer
        prefix = cut_base.prefix_for("take.mov")
        assert "zscale=t=linear" in prefix and "tonemap=tonemap=hable" in prefix
        assert prefix.endswith(","), "the zooms follow it in the same chain"
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("transfer", [HLG, PQ])
def test_an_hdr_take_without_zscale_goes_on_without_the_tone_map_and_says_why(take, capsys, transfer):
    take["transfer"], take["filters"] = transfer, WITHOUT_ZSCALE
    prefix = cut_base.prefix_for("take.mov")
    assert "zscale" not in prefix and "tonemap" not in prefix, "a filter ffmpeg does not have would stop the cut"
    said = capsys.readouterr().out.strip().splitlines()
    assert len(said) == 1, "one plain sentence"
    sentence = said[0]
    assert "washed out" in sentence
    assert "HDR video turned off" in sentence and "phone" in sentence, "the first fix: film again without HDR"
    assert "full ffmpeg" in sentence and "bin/doctor.py" in sentence, "the second fix: the full build, and where its command is"
    assert chr(0x2014) not in sentence and chr(0x2013) not in sentence


def test_the_sentence_is_the_one_the_module_carries(take, capsys):
    take["filters"] = WITHOUT_ZSCALE
    cut_base.prefix_for("take.mov")
    assert capsys.readouterr().out.strip() == cut_base.NO_ZSCALE


@pytest.mark.parametrize("transfer", ["bt709", "", "unknown"])
@pytest.mark.parametrize("filters", [WITH_ZSCALE, WITHOUT_ZSCALE], ids=["zscale", "no-zscale"])
def test_a_standard_range_take_never_hears_about_hdr(take, capsys, transfer, filters):
    take["transfer"], take["filters"] = transfer, filters
    assert cut_base.prefix_for("take.mov") == ""
    assert capsys.readouterr().out == ""


def test_a_wide_hdr_take_is_still_cropped_to_9_by_16_after_the_fallback(take, capsys):
    take["size"], take["filters"] = (1920, 1080), WITHOUT_ZSCALE
    assert cut_base.prefix_for("take.mov") == "crop=trunc(ih*9/16/2)*2:ih,"
    take["size"] = (1080, 2400)  # taller than 9:16
    assert cut_base.prefix_for("take.mov") == "crop=iw:trunc(iw*16/9/2)*2:0:0,"
    capsys.readouterr()


def test_only_the_name_of_a_filter_counts(monkeypatch):
    monkeypatch.setattr(cut_base, "_filter_listing", lambda: WITHOUT_ZSCALE)
    assert cut_base._has_filter("zscale") is False
    assert cut_base._has_filter("tonemap") is True
    monkeypatch.setattr(cut_base, "_filter_listing", lambda: WITH_ZSCALE)
    assert cut_base._has_filter("zscale") is True


def test_an_ffmpeg_that_cannot_be_asked_counts_as_having_no_zscale(monkeypatch):
    def broken(*args, **kwargs):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(cut_base.subprocess, "run", broken)
    assert cut_base._filter_listing() == ""
    assert cut_base._has_filter("zscale") is False


def test_the_filter_list_comes_from_the_real_ffmpeg():
    assert cut_base._has_filter("scale") and cut_base._has_filter("zoompan") and cut_base._has_filter("concat")
    assert not cut_base._has_filter("no_such_filter")


# --- a real HDR take through the whole cut ----------------------------------------------------------------------------


def hlg_take(path, seconds=1.6):
    """A short vertical clip marked as HLG, 10-bit, the way an iPhone writes HDR video."""
    ffmpeg("-f", "lavfi", "-i", f"testsrc2=s=1080x1920:r=30:d={seconds}", "-f", "lavfi", "-i", f"sine=frequency=440:d={seconds}",
           "-c:v", "libx265", "-preset", "ultrafast", "-pix_fmt", "yuv420p10le",
           "-x265-params", f"log-level=error:colorprim=bt2020:transfer={HLG}:colormatrix=bt2020nc",
           "-c:a", "aac", "-shortest", path)
    return path


def probe(path, entries):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", entries, "-of", "json", str(path)],
                         capture_output=True, text=True).stdout
    return json.loads(out)["streams"][0]


def test_a_real_hdr_take_is_cut_even_when_ffmpeg_has_no_zscale(tmp_path, monkeypatch, capsys):
    raw = hlg_take(tmp_path / "hdr.mp4")
    assert probe(raw, "stream=color_transfer")["color_transfer"] == HLG, "the fixture must really be HLG"
    monkeypatch.setattr(cut_base, "_filter_listing", lambda: WITHOUT_ZSCALE)  # as Homebrew's plain ffmpeg lists itself
    edl = tmp_path / "edl.json"
    edl.write_text(json.dumps({"keep": [[0.0, 1.5]]}))
    out = tmp_path / "base.mp4"
    assert cut_base.main([str(raw), str(edl), str(out), "--no-hwaccel"]) == 0
    printed = capsys.readouterr().out
    assert cut_base.NO_ZSCALE in printed
    found = probe(out, "stream=width,height,avg_frame_rate")
    assert (found["width"], found["height"], found["avg_frame_rate"]) == (1080, 1920, "60/1")


@pytest.mark.skipif(not cut_base._has_filter("zscale"), reason="this ffmpeg has no zscale filter")
def test_a_real_hdr_take_is_tone_mapped_when_this_ffmpeg_can(tmp_path, capsys):
    raw = hlg_take(tmp_path / "hdr.mp4")
    edl = tmp_path / "edl.json"
    edl.write_text(json.dumps({"keep": [[0.0, 1.5]]}))
    out = tmp_path / "base.mp4"
    assert cut_base.main([str(raw), str(edl), str(out), "--no-hwaccel"]) == 0
    assert cut_base.NO_ZSCALE not in capsys.readouterr().out
    assert probe(out, "stream=width,height")["height"] == 1920
