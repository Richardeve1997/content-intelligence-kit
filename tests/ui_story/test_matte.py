"""kit.matte: a greyscale matte of the speaker, frame for frame with the input.

Robust Video Matting is loaded through torch.hub at run time (its weights are cached outside the
repo). MediaPipe is the fallback. Either way the matte says which backend made it.
"""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from uistory_helpers import ffmpeg

from kit import matte, media


def tiny_clip(path, seconds=0.5, size="96x160", rate=10):
    ffmpeg("-f", "lavfi", "-i", f"testsrc2=s={size}:r={rate}:d={seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
           path)
    return path


def frames(path):
    """(frame count, width, height) of a video."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
         "stream=nb_read_frames,width,height", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    stream = json.loads(out)["streams"][0]
    return int(stream["nb_read_frames"]), int(stream["width"]), int(stream["height"])


class FakeSegmenter:
    """Stands in for the MediaPipe model: the left half is the person."""

    def __init__(self):
        self.calls = 0

    def __call__(self, rgb: np.ndarray) -> np.ndarray:
        self.calls += 1
        mask = np.zeros(rgb.shape[:2], np.float32)
        mask[:, : rgb.shape[1] // 2] = 1.0
        return mask


def test_matte_fallback(tmp_path, monkeypatch, capsys):
    """With torch unavailable, the matte comes from MediaPipe and says so."""
    monkeypatch.setitem(sys.modules, "torch", None)  # `import torch` now raises ImportError
    fake = FakeSegmenter()
    monkeypatch.setattr(matte, "_mediapipe_segmenter", lambda: fake)
    src = tiny_clip(tmp_path / "take.mp4")

    out = matte.matte(src, tmp_path / "matte.mp4")

    printed = capsys.readouterr().out
    assert out == tmp_path / "matte.mp4"
    assert "MediaPipe" in printed and "torch" in printed
    assert fake.calls == 5
    assert frames(out) == (5, 96, 160)

    # white on the person, black elsewhere
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(out), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, check=True,
    ).stdout
    grey = np.frombuffer(raw, np.uint8).reshape(160, 96)
    assert grey[:, 10:38].mean() > 200 and grey[:, 58:86].mean() < 40


def test_rvm_is_tried_first_and_named(tmp_path, monkeypatch, capsys):
    class FakeRvm:
        backend = "Robust Video Matting"

        def __call__(self, rgb):
            return np.ones(rgb.shape[:2], np.float32)

    monkeypatch.setattr(matte, "_rvm_segmenter", lambda **kwargs: (FakeRvm(), "cpu"))
    monkeypatch.setattr(matte, "_mediapipe_segmenter", lambda: pytest.fail("MediaPipe must not load when RVM works"))
    out = matte.matte(tiny_clip(tmp_path / "take.mp4"), tmp_path / "matte.mp4")
    printed = capsys.readouterr().out
    assert "Robust Video Matting" in printed and "torch.hub" in printed
    assert out.is_file()


def test_a_failed_download_also_falls_back(tmp_path, monkeypatch, capsys):
    def offline(**kwargs):
        raise RuntimeError("could not reach github.com")

    monkeypatch.setattr(matte, "_rvm_segmenter", offline)
    monkeypatch.setattr(matte, "_mediapipe_segmenter", FakeSegmenter)
    matte.matte(tiny_clip(tmp_path / "take.mp4"), tmp_path / "matte.mp4")
    printed = capsys.readouterr().out
    assert "MediaPipe" in printed and "could not reach github.com" in printed


def test_matting_holds_a_heavy_slot(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(matte, "_mediapipe_segmenter", FakeSegmenter)
    monkeypatch.setitem(sys.modules, "torch", None)

    real = media.heavy_slot

    def watching(*args, **kwargs):
        seen.append("slot")
        return real(*args, **kwargs)

    monkeypatch.setattr(matte.media, "heavy_slot", watching)
    matte.matte(tiny_clip(tmp_path / "take.mp4"), tmp_path / "matte.mp4")
    assert seen == ["slot"]


def stops_at_the_third_frame(problem):
    """A segmenter that makes two frames, then fails with `problem` (a stop key or a crash)."""

    class Stops(FakeSegmenter):
        def __call__(self, rgb):
            if self.calls == 2:
                raise problem
            return super().__call__(rgb)

    return Stops


def test_a_matte_stopped_part_way_leaves_no_matte_behind(tmp_path, monkeypatch):
    """A run that stops before the last frame writes nothing at the matte's path. prep.py skips a step whose
    output is newer than its input, so a half matte left there would pass for a finished one."""
    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.setattr(matte, "_mediapipe_segmenter", stops_at_the_third_frame(KeyboardInterrupt()))
    out = tmp_path / "matte.mp4"
    with pytest.raises(KeyboardInterrupt):
        matte.matte(tiny_clip(tmp_path / "take.mp4"), out)
    assert not out.exists()
    assert not list(tmp_path.glob("matte*")), "no part-written file is left either"


def test_a_stopped_run_keeps_the_matte_that_was_there(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.setattr(matte, "_mediapipe_segmenter", FakeSegmenter)
    take, out = tiny_clip(tmp_path / "take.mp4"), tmp_path / "matte.mp4"
    matte.matte(take, out)
    before = out.read_bytes()

    monkeypatch.setattr(matte, "_mediapipe_segmenter", stops_at_the_third_frame(RuntimeError("the model stopped")))
    with pytest.raises(RuntimeError):
        matte.matte(take, out)
    assert out.read_bytes() == before


def test_a_video_that_stops_decoding_part_way_leaves_no_matte(tmp_path, monkeypatch):
    """When the ffmpeg reading the video dies part way, the frames simply end. That is a failure, not a short
    matte: a short matte at the path would pass for a finished one."""
    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.setattr(matte, "_mediapipe_segmenter", FakeSegmenter)
    real_popen = subprocess.Popen

    def popen(cmd, *args, **kwargs):
        if "rgb24" in cmd:  # the reader: two frames of a 96x160 picture, then an error exit
            cmd = [sys.executable, "-c", "import sys; sys.stdout.buffer.write(bytes(96 * 160 * 3 * 2)); sys.exit(1)"]
        return real_popen(cmd, *args, **kwargs)

    monkeypatch.setattr(matte.subprocess, "Popen", popen)
    out = tmp_path / "matte.mp4"
    with pytest.raises(matte.KitError) as caught:
        matte.matte(tiny_clip(tmp_path / "take.mp4"), out)
    assert "take.mp4" in str(caught.value)
    assert not out.exists()
    assert not list(tmp_path.glob("matte*")), "no part-written file is left either"


def test_a_leftover_part_file_is_replaced_by_a_good_run(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.setattr(matte, "_mediapipe_segmenter", FakeSegmenter)
    (tmp_path / "matte.part.mp4").write_bytes(b"left by a run that was stopped")
    out = matte.matte(tiny_clip(tmp_path / "take.mp4"), tmp_path / "matte.mp4")
    assert frames(out) == (5, 96, 160)
    assert not (tmp_path / "matte.part.mp4").exists()


def test_the_model_cache_is_outside_the_repo(monkeypatch, tmp_path):
    from kit import config

    assert config.kit_root() not in matte.model_cache().parents
    monkeypatch.setenv("KIT_MODEL_CACHE", str(tmp_path / "models"))
    assert matte.model_cache() == tmp_path / "models"


def test_a_missing_input_is_a_plain_sentence(tmp_path):
    with pytest.raises(matte.KitError) as caught:
        matte.matte(tmp_path / "nope.mp4", tmp_path / "matte.mp4")
    assert "nope.mp4" in str(caught.value)


def test_a_mask_with_a_channel_axis_is_flattened(tmp_path, monkeypatch):
    """MediaPipe returns its confidence mask as H x W x 1. The matte must still come out H x W."""

    def with_channel(rgb):
        mask = np.zeros((*rgb.shape[:2], 1), np.float32)
        mask[:, : rgb.shape[1] // 2, 0] = 1.0
        return mask

    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.setattr(matte, "_mediapipe_segmenter", lambda: with_channel)
    out = matte.matte(tiny_clip(tmp_path / "take.mp4"), tmp_path / "matte.mp4")
    assert frames(out) == (5, 96, 160)


def test_the_model_downloads_are_pinned():
    import re

    repo, _, ref = matte.RVM_REPO.partition(":")
    assert repo == "PeterL1n/RobustVideoMatting"
    assert re.fullmatch(r"[0-9a-f]{40}", ref), "a full commit, not a branch"
    assert "/latest/" not in matte.SELFIE_MODEL_URL and "/float16/1/" in matte.SELFIE_MODEL_URL
    assert re.fullmatch(r"[0-9a-f]{64}", matte.SELFIE_MODEL_SHA256)


def test_a_model_download_that_is_not_the_pinned_file_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("KIT_MODEL_CACHE", str(tmp_path / "models"))
    monkeypatch.setattr(matte.urllib.request, "urlretrieve", lambda url, to: Path(to).write_bytes(b"not the model"))
    with pytest.raises(matte.KitError) as caught:
        matte._selfie_model()
    assert "not the expected file" in str(caught.value)
    assert list((tmp_path / "models").iterdir()) == [], "nothing half-downloaded is left behind"


def test_the_pinned_model_is_used_from_the_cache_without_a_download(tmp_path, monkeypatch):
    import hashlib

    content = b"pretend model bytes"
    monkeypatch.setattr(matte, "SELFIE_MODEL_SHA256", hashlib.sha256(content).hexdigest())
    monkeypatch.setenv("KIT_MODEL_CACHE", str(tmp_path / "models"))
    calls = []

    def fetch(url, to):
        calls.append(url)
        Path(to).write_bytes(content)

    monkeypatch.setattr(matte.urllib.request, "urlretrieve", fetch)
    first = matte._selfie_model()
    second = matte._selfie_model()
    assert first == second and first.read_bytes() == content
    assert calls == [matte.SELFIE_MODEL_URL], "downloaded once, then read from the cache"


# --- the Robust Video Matting weights ----------------------------------------------------------------------------


def test_the_rvm_weights_are_pinned_to_the_file_the_hub_code_names():
    """The pinned hub code loads its weights from the repo's v1.0.0 release; the kit downloads that one file
    itself and checks its SHA-256, as it does for MediaPipe and GSAP."""
    import re

    assert matte.RVM_WEIGHTS_URL == (
        "https://github.com/PeterL1n/RobustVideoMatting/releases/download/v1.0.0/rvm_mobilenetv3.pth")
    assert re.fullmatch(r"[0-9a-f]{64}", matte.RVM_WEIGHTS_SHA256)


def test_rvm_weights_that_are_not_the_pinned_file_are_refused(tmp_path, monkeypatch):
    pytest.importorskip("torch")
    monkeypatch.setenv("TORCH_HOME", str(tmp_path / "torch"))
    monkeypatch.setattr(matte.urllib.request, "urlretrieve", lambda url, to: Path(to).write_bytes(b"not the weights"))
    with pytest.raises(matte.KitError) as caught:
        matte._rvm_weights()
    assert "not the expected file" in str(caught.value)
    assert list((tmp_path / "torch" / "hub" / "checkpoints").iterdir()) == [], "nothing half-downloaded is left"


def test_the_pinned_rvm_weights_are_used_from_torchs_cache_without_a_download(tmp_path, monkeypatch):
    import hashlib

    pytest.importorskip("torch")
    content = b"pretend weights"
    monkeypatch.setattr(matte, "RVM_WEIGHTS_SHA256", hashlib.sha256(content).hexdigest())
    monkeypatch.setenv("TORCH_HOME", str(tmp_path / "torch"))
    calls = []

    def fetch(url, to):
        calls.append(url)
        Path(to).write_bytes(content)

    monkeypatch.setattr(matte.urllib.request, "urlretrieve", fetch)
    first = matte._rvm_weights()
    second = matte._rvm_weights()
    assert first == second == tmp_path / "torch" / "hub" / "checkpoints" / "rvm_mobilenetv3.pth"
    assert first.read_bytes() == content
    assert calls == [matte.RVM_WEIGHTS_URL], "downloaded once, then read from torch's cache"


def test_rvm_loads_the_checked_weights_and_never_downloads_its_own(tmp_path, monkeypatch):
    """torch.hub would fetch the weights itself with no checksum. The model is built without them and given
    the file the kit checked."""
    torch = pytest.importorskip("torch")
    checked = tmp_path / "checked.pth"
    checked.write_bytes(b"checked weights")
    seen = {}

    class Model:
        def load_state_dict(self, state):
            seen["state"] = state

        def eval(self):
            return self

        def to(self, device):
            return self

    def fake_hub_load(repo, entry, *args, **kwargs):
        seen["hub"] = (repo, entry, kwargs)
        return Model()

    def fake_torch_load(path, **kwargs):
        seen["loaded"] = (Path(path), kwargs)
        return {"weights": "from the checked file"}

    monkeypatch.setattr(matte, "_rvm_weights", lambda: checked)
    monkeypatch.setattr(torch.hub, "load", fake_hub_load)
    monkeypatch.setattr(torch, "load", fake_torch_load)
    matte._rvm_segmenter("cpu")
    repo, entry, kwargs = seen["hub"]
    assert (repo, entry) == (matte.RVM_REPO, "mobilenetv3") and kwargs.get("pretrained") is False, kwargs
    assert seen["loaded"][0] == checked and seen["loaded"][1].get("weights_only") is True
    assert seen["state"] == {"weights": "from the checked file"}
