"""A greyscale matte of the person in a video: white where they are, black everywhere else.

The UI story layout cuts the creator's head out of the rounded picture-in-picture so it breaks out above
the card's top edge. That cut-out needs a matte with the same size, frame rate and frame count as the
video it was made from.

Two ways to make it:

* Robust Video Matting (the default). Loaded through torch.hub when it runs, so neither its code nor its
  weights are in this repo; torch keeps them in its own cache in your home folder. The weights are
  downloaded once and checked against a pinned SHA-256 before the model loads them. It is GPL-3.0
  licensed. It keeps a clean hair edge and runs on the Apple GPU (mps) when there is one.
* MediaPipe selfie segmentation (the fallback, when torch is missing or the download fails). Its model
  file is downloaded once into a cache outside the repo (KIT_MODEL_CACHE moves it). The edge is rougher,
  so treat it as a preview.

Whichever runs, a line says so. Matting is heavy, so it runs inside a heavy-job slot.

Command line:

    python -m kit.matte <video> <matte.mp4> [--backend auto|rvm|mediapipe] [--downsample 0.5]
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import warnings
from pathlib import Path
from typing import Callable

from kit import media
from kit.config import KitError

__all__ = ["KitError", "RVM_REPO", "RVM_WEIGHTS_SHA256", "RVM_WEIGHTS_URL", "SELFIE_MODEL_SHA256",
           "SELFIE_MODEL_URL", "main", "matte", "model_cache"]

# Every download is pinned, so every machine runs the same code and weights.
# Robust Video Matting: the upstream repo at a fixed commit (the head of master when this was written). Its
# v1.0.0 tag cannot be used: that code imports a torchvision helper newer torchvision no longer has. torch.hub
# checks a ref against the repo's branch and tag heads, which a fixed commit stops being once master moves,
# so the load skips that check; the full commit id is what ties it to the upstream repo.
RVM_REPO = "PeterL1n/RobustVideoMatting:53d74c6826735f01f4406b5ca9075eee27bec094"
# Its weights: the file that commit's hubconf.py loads, from the repo's v1.0.0 release. torch.hub would fetch it
# with no check, so the kit downloads it into torch's own cache, checks this SHA-256, and gives the model that file.
RVM_WEIGHTS_URL = "https://github.com/PeterL1n/RobustVideoMatting/releases/download/v1.0.0/rvm_mobilenetv3.pth"
RVM_WEIGHTS_SHA256 = "3c7c1d92033f7c38d6577c481d13a195d7d80a159b960f4f3119ac7b534cf4f8"
# MediaPipe selfie segmenter: version 1 of the float16 model, checked against this SHA-256 after download.
SELFIE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_segmenter/float16/1/"
    "selfie_segmenter.tflite"
)
SELFIE_MODEL_SHA256 = "191ac9529ae506ee0beefa6b2c945a172dab9d07d1e802a290a4e4038226658b"
# A model file download that receives nothing for this long is given up on, so a stalled file cannot hold a matte.
# The matting code itself comes through torch.hub, which sets no limit of its own.
DOWNLOAD_TIMEOUT = 60
Segmenter = Callable[["object"], "object"]  # an RGB frame (H x W x 3, uint8) in, a 0..1 float mask (H x W) out


def model_cache() -> Path:
    """Where downloaded model files are kept: KIT_MODEL_CACHE, or a cache folder in your home folder."""
    override = os.environ.get("KIT_MODEL_CACHE", "").strip()
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CACHE_HOME", "").strip() or str(Path.home() / ".cache")
    return Path(base) / "content-intelligence-kit" / "models"


def _device(preference: str | None):
    import torch

    if preference in ("cpu", "mps", "cuda"):
        return preference
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def _rvm_weights() -> Path:
    """The pinned Robust Video Matting weights in torch's own cache, downloaded once and checked against their
    SHA-256. A file there that does not match is downloaded again."""
    import torch

    path = Path(torch.hub.get_dir()) / "checkpoints" / RVM_WEIGHTS_URL.rsplit("/", 1)[1]
    if path.is_file() and _sha256(path) == RVM_WEIGHTS_SHA256:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading the Robust Video Matting weights (GPL-3.0) into {path.parent} ...", flush=True)
    part = path.with_suffix(".part")
    _fetch(RVM_WEIGHTS_URL, part, "the Robust Video Matting weights")
    if _sha256(part) != RVM_WEIGHTS_SHA256:
        part.unlink(missing_ok=True)
        raise KitError("The Robust Video Matting weights that downloaded are not the expected file, so they were not used.")
    part.replace(path)
    return path


def _rvm_segmenter(device: str | None = None, downsample: float = 0.5) -> tuple[Segmenter, str]:
    """Robust Video Matting (mobilenetv3) through torch.hub, with its recurrent state kept between frames. The
    model is built without weights and given the checked file (_rvm_weights), so torch.hub never fetches its own."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import torch

        device = _device(device)
        weights = _rvm_weights()
        model = torch.hub.load(RVM_REPO, "mobilenetv3", pretrained=False, trust_repo=True, skip_validation=True,
                               verbose=False)
        model.load_state_dict(torch.load(weights, map_location="cpu", weights_only=True))
        model = model.eval().to(device)
    state = [None, None, None, None]

    def segment(rgb):
        with torch.no_grad():
            frame = torch.from_numpy(rgb.astype("float32")).permute(2, 0, 1).unsqueeze(0).div(255.0).to(device)
            _foreground, alpha, *rest = model(frame, *state, downsample)
            state[:] = rest
        return alpha[0, 0].clamp(0, 1).to("cpu").numpy()

    return segment, device


def _fetch(url: str, part: Path, what: str) -> None:
    """Download `url` to `part`. A connection that sends nothing for DOWNLOAD_TIMEOUT seconds is given up on. Any
    failure deletes `part` and is a plain sentence that says what to do."""
    stalled = (f"The download of {what} stalled: nothing came for {DOWNLOAD_TIMEOUT} seconds, so it was stopped. "
               "Check the connection, then run the matte again.")
    try:
        with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT) as response, open(part, "wb") as out:
            shutil.copyfileobj(response, out, 1 << 20)
    except TimeoutError:
        part.unlink(missing_ok=True)
        raise KitError(stalled) from None
    except urllib.error.URLError as problem:
        part.unlink(missing_ok=True)
        if isinstance(problem.reason, TimeoutError):
            raise KitError(stalled) from None
        raise KitError(f"{what[0].upper()}{what[1:]} could not be downloaded ({problem.reason}). Check the connection, "
                       "then run the matte again.") from None
    except OSError as problem:
        part.unlink(missing_ok=True)
        raise KitError(f"{what[0].upper()}{what[1:]} could not be downloaded ({problem}). Check the connection, "
                       "then run the matte again.") from None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _selfie_model() -> Path:
    """The pinned MediaPipe model file, downloaded once into the cache and checked against its SHA-256."""
    path = model_cache() / "selfie_segmenter_float16_v1.tflite"
    if path.is_file() and _sha256(path) == SELFIE_MODEL_SHA256:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading the MediaPipe selfie model (Apache-2.0) into {path.parent} ...", flush=True)
    part = path.with_suffix(".part")
    _fetch(SELFIE_MODEL_URL, part, "the MediaPipe model")
    if _sha256(part) != SELFIE_MODEL_SHA256:
        part.unlink(missing_ok=True)
        raise KitError("The MediaPipe model that downloaded is not the expected file, so it was not used.")
    part.replace(path)
    return path


def _mediapipe_segmenter(threshold: float = 0.55) -> Segmenter:
    """MediaPipe's selfie segmenter, hardened so its soft edge does not turn into a halo."""
    import numpy as np

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions
        from mediapipe.tasks.python import vision

    options = vision.ImageSegmenterOptions(
        base_options=BaseOptions(model_asset_path=str(_selfie_model())),
        running_mode=vision.RunningMode.IMAGE,
        output_confidence_masks=True,
        output_category_mask=False,
    )
    segmenter = vision.ImageSegmenter.create_from_options(options)

    def segment(rgb):
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
        masks = segmenter.segment(image).confidence_masks
        person = np.squeeze(masks[-1].numpy_view()).astype(np.float32)  # the last mask is the person
        return np.clip((person - threshold) / max(1e-3, 1.0 - threshold), 0.0, 1.0)

    return segment


def _choose(backend: str, downsample: float) -> Segmenter:
    if backend in ("auto", "rvm"):
        try:
            segment, device = _rvm_segmenter(downsample=downsample)
            print(f"matte: Robust Video Matting through torch.hub (mobilenetv3 on {device}).", flush=True)
            return segment
        except ImportError:
            reason = "torch is not installed"
        except Exception as problem:  # an offline download, a hub error
            reason = str(problem).strip() or type(problem).__name__
        reason = reason.rstrip(".")  # it may be a whole sentence already: each message below ends once
        if backend == "rvm":
            raise KitError(f"Robust Video Matting could not load: {reason}.")
        print("matte: Robust Video Matting could not load, so MediaPipe selfie segmentation makes the matte, and "
              f"its edge is rougher. What stopped it: {reason}.", flush=True)
    else:
        print("matte: MediaPipe selfie segmentation.", flush=True)
    return _mediapipe_segmenter()


def matte(video: Path, out: Path, *, backend: str = "auto", downsample: float = 0.5) -> Path:
    """Write a greyscale matte of the person in `video` to `out`, frame for frame. Returns `out`."""
    import numpy as np

    video, out = Path(video), Path(out)
    if not video.is_file():
        raise KitError(f"{video} does not exist, so there is nothing to matte.")
    info = media.probe(video)
    width, height = info.width, info.height
    if not width or not height:
        raise KitError(f"{video.name} has no picture to matte.")
    rate = f"{info.fps:.6f}".rstrip("0").rstrip(".") if info.fps else "30"
    out.parent.mkdir(parents=True, exist_ok=True)
    # The frames go to a part file beside `out`, which takes its place only once every frame is in. A run that
    # stops part way (a crash, ctrl-C, a closed laptop) then leaves no half matte at `out`, where prep.py would
    # take it for a finished one, and keeps a matte that was already there.
    part = out.with_name(f"{out.stem}.part{out.suffix}")

    with media.heavy_slot(label=f"matte {video.name}"):
        segment = _choose(backend, downsample)
        decoder = subprocess.Popen(
            ["ffmpeg", "-nostdin", "-v", "error", "-i", str(video), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            stdout=subprocess.PIPE,
        )
        encoder = subprocess.Popen(
            ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{width}x{height}",
             "-r", rate, "-i", "-", *media.encoder_args(), "-an", str(part)],
            stdin=subprocess.PIPE,
        )
        size, count, read_all = width * height * 3, 0, False
        try:
            while True:
                chunk = decoder.stdout.read(size)
                if len(chunk) < size:
                    break
                rgb = np.frombuffer(chunk, np.uint8).reshape(height, width, 3)
                alpha = np.squeeze(np.asarray(segment(rgb), dtype=np.float32))  # MediaPipe gives H x W x 1
                if alpha.shape != (height, width):
                    from PIL import Image

                    alpha = np.asarray(Image.fromarray((alpha * 255).astype(np.uint8)).resize((width, height))) / 255.0
                encoder.stdin.write((np.clip(alpha, 0, 1) * 255).astype(np.uint8).tobytes())
                count += 1
                if count % 600 == 0:
                    print(f"    {count} frames", flush=True)
            read_all = True
        finally:
            decoder.stdout.close()
            decoder.wait()
            try:
                encoder.stdin.close()
            except BrokenPipeError:
                pass
            encoder.wait()
            if not read_all:
                part.unlink(missing_ok=True)
    if decoder.returncode != 0:  # the frames ended because reading the video failed, not because it ended
        part.unlink(missing_ok=True)
        raise KitError(f"ffmpeg stopped reading {video.name} part way, so its matte was not written. "
                       "Check that the video plays to the end, then run the matte again.")
    if encoder.returncode != 0 or count == 0:
        part.unlink(missing_ok=True)
        raise KitError(f"The matte for {video.name} could not be written.")
    os.replace(part, out)
    print(f"matte: {count} frames -> {out}")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m kit.matte", description="A greyscale matte of the person.")
    parser.add_argument("video", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--backend", choices=("auto", "rvm", "mediapipe"), default="auto")
    parser.add_argument("--downsample", type=float, default=0.5, help="Robust Video Matting inference scale")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        matte(args.video, args.out, backend=args.backend, downsample=args.downsample)
    except KitError as problem:
        print(problem)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
