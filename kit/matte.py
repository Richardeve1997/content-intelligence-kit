"""A greyscale matte of the person in a video: white where they are, black everywhere else.

The UI story layout cuts the creator's head out of the rounded picture-in-picture so it breaks out above
the card's top edge. That cut-out needs a matte with the same size, frame rate and frame count as the
video it was made from.

Two ways to make it:

* Robust Video Matting (the default). Loaded through torch.hub when it runs, so neither its code nor its
  weights are in this repo; torch keeps them in its own cache in your home folder. It is GPL-3.0
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
import os
import subprocess
import sys
import urllib.request
import warnings
from pathlib import Path
from typing import Callable

from kit import media
from kit.config import KitError

__all__ = ["KitError", "SELFIE_MODEL_URL", "main", "matte", "model_cache"]

SELFIE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_segmenter/float16/latest/"
    "selfie_segmenter.tflite"
)
RVM_REPO = "PeterL1n/RobustVideoMatting"
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


def _rvm_segmenter(device: str | None = None, downsample: float = 0.5) -> tuple[Segmenter, str]:
    """Robust Video Matting (mobilenetv3) through torch.hub, with its recurrent state kept between frames."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import torch

        device = _device(device)
        model = torch.hub.load(RVM_REPO, "mobilenetv3", trust_repo=True, verbose=False).eval().to(device)
    state = [None, None, None, None]

    def segment(rgb):
        with torch.no_grad():
            frame = torch.from_numpy(rgb.astype("float32")).permute(2, 0, 1).unsqueeze(0).div(255.0).to(device)
            _foreground, alpha, *rest = model(frame, *state, downsample)
            state[:] = rest
        return alpha[0, 0].clamp(0, 1).to("cpu").numpy()

    return segment, device


def _selfie_model() -> Path:
    path = model_cache() / "selfie_segmenter.tflite"
    if not path.is_file() or path.stat().st_size == 0:
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"Downloading the MediaPipe selfie model (Apache-2.0) into {path.parent} ...", flush=True)
        part = path.with_suffix(".part")
        try:
            urllib.request.urlretrieve(SELFIE_MODEL_URL, part)
        except OSError as problem:
            part.unlink(missing_ok=True)
            raise KitError(f"The MediaPipe model could not be downloaded ({problem}). Check the connection.") from None
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
            reason = str(problem) or type(problem).__name__
        if backend == "rvm":
            raise KitError(f"Robust Video Matting could not load: {reason}.")
        print(f"matte: Robust Video Matting is not available ({reason}), so MediaPipe selfie segmentation "
              "makes the matte. Its edge is rougher.", flush=True)
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

    with media.heavy_slot(label=f"matte {video.name}"):
        segment = _choose(backend, downsample)
        decoder = subprocess.Popen(
            ["ffmpeg", "-nostdin", "-v", "error", "-i", str(video), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            stdout=subprocess.PIPE,
        )
        encoder = subprocess.Popen(
            ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{width}x{height}",
             "-r", rate, "-i", "-", *media.encoder_args(), "-an", str(out)],
            stdin=subprocess.PIPE,
        )
        size, count = width * height * 3, 0
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
        finally:
            decoder.stdout.close()
            decoder.wait()
            try:
                encoder.stdin.close()
            except BrokenPipeError:
                pass
            encoder.wait()
    if encoder.returncode != 0 or count == 0:
        raise KitError(f"The matte for {video.name} could not be written.")
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
