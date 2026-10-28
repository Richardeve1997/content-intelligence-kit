"""Shared helpers for the mini me tests (imported by the test files and conftest.py).

The skill's scripts live in .claude/skills/mini-me/kit/, a folder of scripts rather than a package, so tests
load them by path. Media fixtures are made with numpy and ffmpeg at test time; nothing is committed. The
stand-in character is a block figure drawn with numpy, in the shape the character pipeline packs.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / ".claude" / "skills" / "mini-me"
KIT = SKILL / "kit"
SR = 48000

CORE = ("idle", "talking", "point_left", "point_right", "point_up", "thumbs_up", "shrug", "thinking", "mind_blown",
        "celebrate", "hold_card", "typing", "facepalm", "wave")  # fmt: skip
MOVES = ("idle", "talking", "point_left", "point_right", "point_up")


def load(name: str):
    """Import .claude/skills/mini-me/kit/<name>.py as a module."""
    if str(KIT) not in sys.path:
        sys.path.insert(0, str(KIT))
    path = KIT / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"mini_me_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *map(str, args)], check=True)


needs_say = pytest.mark.skipif(shutil.which("say") is None, reason="needs the macOS say command")
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")


# --- sound ----------------------------------------------------------------------------------------------------


def speech_samples(pattern, *, level_db: float = -20.0, floor_db: float = -70.0, seed: int = 5):
    """Mono float samples at 48 kHz: ("talk", seconds) stretches sound like syllables, ("gap", seconds) are quiet."""
    import numpy as np

    rng = np.random.default_rng(seed)
    pieces = []
    for kind, seconds in pattern:
        n = int(seconds * SR)
        t = np.arange(n) / SR
        noise = rng.standard_normal(n)
        spectrum = np.fft.rfft(noise)
        freqs = np.fft.rfftfreq(n, 1 / SR)
        band = np.fft.irfft(spectrum * ((freqs > 120) & (freqs < 3400)) / np.sqrt(np.maximum(freqs, 120) / 120), n)
        band /= np.sqrt(np.mean(band**2)) + 1e-12
        if kind == "talk":
            syllables = 0.12 + 0.88 * np.abs(np.sin(2 * np.pi * 2.7 * t)) ** 2
            edge = np.minimum(1.0, np.minimum(t, seconds - t) / 0.02)
            pieces.append(band * syllables * edge * 10 ** (level_db / 20) * 1.6)
        else:
            pieces.append(band * 10 ** (floor_db / 20))
    return np.concatenate(pieces)


def write_wav(samples, out: Path, rate: int = SR) -> Path:
    import numpy as np

    out.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(np.round(samples * 32767), -32768, 32767).astype("<i2")
    with wave.open(str(out), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm.tobytes())
    return out


def phone_recording(out: Path, pattern) -> Path:
    """A speech-like recording saved the way a phone saves one (AAC in an .m4a)."""
    wav = out.with_suffix(".src.wav")
    write_wav(speech_samples(pattern), wav)
    ffmpeg("-i", wav, "-c:a", "aac", "-b:a", "128k", "-ar", SR, out)
    wav.unlink()
    return out


def decode(path: Path, rate: int = SR):
    import numpy as np

    raw = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(rate),
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).astype(np.float64)


def quiet_runs(samples, rate: int = SR, threshold_db: float = -45.0, frame: float = 0.02) -> list[float]:
    """Lengths in seconds of the quiet stretches between sounds (leading and trailing quiet left out)."""
    import numpy as np

    hop = int(frame * rate)
    count = len(samples) // hop
    level = 20 * np.log10(np.sqrt((samples[: count * hop].reshape(count, hop) ** 2).mean(1)) + 1e-12)
    loud = level > threshold_db
    runs, current, seen_sound = [], 0, False
    for value in loud:
        if value:
            if seen_sound and current:
                runs.append(current * frame)
            current, seen_sound = 0, True
        elif seen_sound:
            current += 1
    return runs


def loudness(path: Path) -> tuple[float, float]:
    """(integrated LUFS, true peak dBTP) of a file, from ffmpeg's ebur128."""
    import re

    text = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(path), "-vn", "-af", "ebur128=peak=true",
                           "-f", "null", "-"], capture_output=True, text=True).stderr
    summary = text[text.rindex("Summary:"):]
    return (float(re.search(r"I:\s+(-?[\d.]+) LUFS", summary).group(1)),
            float(re.search(r"Peak:\s+(-?[\d.]+) dBFS", summary).group(1)))


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout
    return float(out.strip())


# --- a stand-in character ------------------------------------------------------------------------------------------

W, H, FEET = 245, 284, 280
PALETTE = ["#2b2230", "#f2c9a8", "#c98a6b", "#3d6fb0", "#2d4f80", "#4a4a58", "#7a4a2c", "#ffffff"]


def _rgb(colour: str):
    return tuple(int(colour[i : i + 2], 16) for i in (1, 3, 5))


def _figure_standard(pose: str, step: float = 1.0):
    """One 245 x 284 drawing: a block figure whose arms show the pose. Feet on row 279, crown on row 60."""
    import numpy as np

    canvas = np.zeros((H, W, 4), np.uint8)
    ink = _rgb(PALETTE[0])

    def fill(x0, y0, x1, y1, colour):
        canvas[y0 : y1 + 1, x0 : x1 + 1, :3] = colour
        canvas[y0 : y1 + 1, x0 : x1 + 1, 3] = 255

    lift = -16 if pose == "celebrate" else 0
    fill(98, 60 + lift, 146, 72 + lift, _rgb(PALETTE[6]))  # hair
    fill(96, 72 + lift, 148, 112 + lift, _rgb(PALETTE[1]))  # face
    fill(108, 86 + lift, 113, 89 + lift, ink)  # eyes
    fill(131, 86 + lift, 136, 89 + lift, ink)
    fill(116, 100 + lift, 128, 102 + lift, _rgb(PALETTE[2]))  # mouth
    fill(112, 113 + lift, 132, 120 + lift, _rgb(PALETTE[2]))  # neck
    fill(92, 121 + lift, 152, 200 + lift, _rgb(PALETTE[3]))  # shirt
    fill(94, 201 + lift, 150, 262 + lift, _rgb(PALETTE[5]))  # trousers
    fill(92, 263 + lift, 118, 279 + lift, _rgb(PALETTE[0]))  # shoes
    fill(126, 263 + lift, 152, 279 + lift, _rgb(PALETTE[0]))
    sleeve = _rgb(PALETTE[4])
    left_down = lambda: (fill(81, 124, 91, 186, sleeve), fill(81, 187, 91, 196, _rgb(PALETTE[1])))  # noqa: E731
    right_down = lambda: (fill(153, 124, 163, 186, sleeve), fill(153, 187, 163, 196, _rgb(PALETTE[1])))  # noqa: E731
    reach = int(round(step * 40))
    if pose == "point_left":
        fill(90 - reach, 128, 91, 138, sleeve)
        fill(82 - reach, 128, 90 - reach, 138, _rgb(PALETTE[1]))
        right_down()
    elif pose == "point_right":
        fill(153, 128, 154 + reach, 138, sleeve)
        fill(154 + reach, 128, 162 + reach, 138, _rgb(PALETTE[1]))
        left_down()
    elif pose in ("point_up", "wave"):
        fill(153, 124 - reach, 164, 138, sleeve)
        fill(153, 112 - reach, 164, 123 - reach, _rgb(PALETTE[1]))
        left_down()
    elif pose in ("celebrate", "mind_blown"):
        fill(80, 70 + lift, 91, 130 + lift, sleeve)
        fill(153, 70 + lift, 164, 130 + lift, sleeve)
    elif pose == "shrug":
        fill(64, 118, 91, 130, sleeve)
        fill(153, 118, 180, 130, sleeve)
    else:
        left_down()
        right_down()
    return canvas


BIG_HEAD = {"top": 61, "neck": 153, "waist": 221, "mouth": [116, 140], "eyes": [104, 121]}


def _figure_big(pose: str, step: float = 1.0):
    """A big-headed drawing, built like a chibi: the head runs to row 152, the hands hang to row 220."""
    import numpy as np

    canvas = np.zeros((H, W, 4), np.uint8)
    ink, skin, sleeve = _rgb(PALETTE[0]), _rgb(PALETTE[1]), _rgb(PALETTE[4])

    def fill(x0, y0, x1, y1, colour):
        canvas[y0 : y1 + 1, x0 : x1 + 1, :3] = colour
        canvas[y0 : y1 + 1, x0 : x1 + 1, 3] = 255

    lift = -16 if pose == "celebrate" else 0
    fill(95, 61 + lift, 150, 104 + lift, _rgb(PALETTE[6]))  # a tall head of hair
    fill(92, 105 + lift, 153, 152 + lift, skin)  # the face
    fill(104, 122 + lift, 110, 126 + lift, ink)  # eyes
    fill(135, 122 + lift, 141, 126 + lift, ink)
    fill(116, 140 + lift, 128, 142 + lift, _rgb(PALETTE[2]))  # mouth
    fill(112, 153 + lift, 133, 158 + lift, _rgb(PALETTE[2]))  # neck
    fill(98, 159 + lift, 147, 225 + lift, _rgb(PALETTE[3]))  # shirt
    fill(100, 226 + lift, 145, 262 + lift, _rgb(PALETTE[5]))  # trousers
    fill(96, 263 + lift, 121, 279 + lift, ink)  # shoes
    fill(124, 263 + lift, 149, 279 + lift, ink)
    left_down = lambda: (fill(86, 162, 97, 208, sleeve), fill(86, 209, 97, 220, skin))  # noqa: E731
    right_down = lambda: (fill(148, 162, 159, 208, sleeve), fill(148, 209, 159, 220, skin))  # noqa: E731
    reach = int(round(step * 36))
    if pose == "point_left":
        fill(97 - reach, 168, 97, 178, sleeve)
        fill(89 - reach, 168, 97 - reach, 178, skin)
        right_down()
    elif pose == "point_right":
        fill(148, 168, 148 + reach, 178, sleeve)
        fill(148 + reach, 168, 156 + reach, 178, skin)
        left_down()
    elif pose in ("point_up", "wave"):
        fill(148, 160 - reach, 159, 175, sleeve)
        fill(148, 148 - reach, 159, 159 - reach, skin)
        left_down()
    elif pose in ("celebrate", "mind_blown"):
        fill(80, 110 + lift, 91, 170 + lift, sleeve)
        fill(154, 110 + lift, 165, 170 + lift, sleeve)
    elif pose == "shrug":
        fill(70, 160, 97, 172, sleeve)
        fill(148, 160, 175, 172, sleeve)
    else:
        left_down()
        right_down()
    return canvas


def _save(array, path: Path):
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(array, "RGBA").save(path)


def make_character(folder: Path, build: str = "standard") -> Path:
    """A packed stand-in character in `folder` (normally me/character/): strips, faces and the manifest.

    The manifest has the shape the character pipeline writes and character.js reads. build="big_head" draws a
    chibi with a head almost half its height (BIG_HEAD has where its neck, waist, mouth and eyes are).
    """
    import numpy as np

    folder = Path(folder)
    (folder / "strips").mkdir(parents=True, exist_ok=True)
    (folder / "faces").mkdir(parents=True, exist_ok=True)
    if build == "wide":  # arms held out in every drawing: too wide for a close-up
        draw = lambda pose, step=1.0: _figure_standard("shrug", step)  # noqa: E731
    else:
        draw = _figure_big if build == "big_head" else _figure_standard
    if build == "big_head":
        anchors = {"mouth": list(BIG_HEAD["mouth"]), "eyes": list(BIG_HEAD["eyes"]), "hand": None}
    else:
        anchors = {"mouth": [116, 99], "eyes": [107, 85], "hand": None}

    def entry(kind, frames, sequence, hold, strip, ref=122.0):
        return {"kind": kind, "frames": frames, "fps": 24, "strip": strip, "ref_col": ref, "label": strip,
                "status": "approved", "notes": "", "sequence": sequence, "hold": hold, "loop": None,
                "travel_px_per_frame": 0, "frame_info": [dict(anchors) for _ in range(frames)]}

    poses = {}
    for name in CORE:
        _save(draw(name), folder / "strips" / f"pose_{name}.png")
        poses[name] = entry("pose", 1, [[0, 1]], 0, f"strips/pose_{name}.png")
    moves = {"idle": entry("pose", 1, [[0, 1]], 0, "strips/idle.png")}
    _save(draw("idle"), folder / "strips" / "idle.png")
    talking = np.concatenate([draw("talking") for _ in range(4)], axis=1)
    _save(talking, folder / "strips" / "talking.png")
    moves["talking"] = entry("loop", 4, [[0, 4], [1, 4], [2, 4], [3, 4]], None, "strips/talking.png")
    for name in ("point_left", "point_right", "point_up"):
        strip = np.concatenate([draw(name, step) for step in (0.0, 0.4, 0.7, 1.0)], axis=1)
        _save(strip, folder / "strips" / f"{name}.png")
        moves[name] = entry("oneshot", 4, [[0, 8], [1, 2], [2, 2], [3, 24], [2, 2], [1, 2], [0, 8]], 3,
                            f"strips/{name}.png")
    faces = {"mouth_closed": [13, 4], "mouth_mid": [13, 4], "mouth_open": [13, 4], "eyes_open": [30, 6],
             "eyes_closed": [30, 6]}
    for name, (w, h) in faces.items():
        patch = np.zeros((h, w, 4), np.uint8)
        patch[..., :3] = _rgb(PALETTE[0])
        patch[..., 3] = 255
        _save(patch, folder / "faces" / f"{name}.png")
    manifest = {"character": "character", "canvas": [W, H], "baseline_row": FEET, "native_fps": 24,
                "palette": PALETTE, "faces": faces, "rules": [], "moves": moves, "poses": poses}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=1))
    (folder / "manifest.js").write_text("/* stand-in */\nwindow.CHARACTER_MANIFEST = " + json.dumps(manifest) + ";\n")
    return folder / "manifest.json"


# --- a short Reel for the example creator ------------------------------------------------------------------------------

PAGES = [
    {"t": 0.0, "text": "BASIL *DIES*", "pose": "point_up", "field": "dark", "prop": "card: Week three"},
    {"t": 1.6, "text": "LOOK AT THIS", "pose": "point_left", "field": "light", "prop": "card: A sunny window"},
    {"t": 3.0, "text": "WATER EARLY", "pose": "point_right", "field": "dark", "prop": "stamp: AM"},
    {"t": 4.4, "text": "3 OF 5", "pose": "thumbs_up", "field": "light", "prop": "count"},
    {"t": 5.6, "text": "COMMENT GROW", "pose": "point_up", "field": "dark", "prop": "cta"},
]
SPOKEN = ("Basil dies in week three. Look at this window. Water it early. Three plants made it. "
          "Comment GROW and I'll send you the checklist.")
SECONDS = 8.0


def words_for(text: str) -> list[dict]:
    """Words laid over the pages: each sentence starts on its page."""
    starts = [p["t"] + 0.12 for p in PAGES]
    out = []
    for start, sentence in zip(starts, re.findall(r"[^.]+\.", text)):
        t = start
        for word in sentence.split():
            out.append({"w": word, "s": round(t, 3), "e": round(t + 0.26, 3)})
            t += 0.3
    return out


def build_project(root: Path, me: Path, slug: str = "example-reel", spoken: str = SPOKEN, build: str = "standard") -> Path:
    """work/<slug>/ with a voice-over, words and pages, the stand-in character in me/, then install.py."""
    folder = root / "work" / slug
    folder.mkdir(parents=True, exist_ok=True)
    wav = write_wav(speech_samples([("talk", SECONDS - 0.3), ("gap", 0.3)]), folder / "vo.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(wav), "-c:a", "aac", "-b:a", "192k", "-ac", "2",
                    str(folder / "vo.m4a")], check=True)
    wav.unlink()
    (folder / "words.json").write_text(json.dumps({"dur": SECONDS, "words": words_for(spoken)}))
    (folder / "beats.json").write_text(json.dumps({"duration": SECONDS, "pages": PAGES}))
    if not (me / "character" / "manifest.json").exists():
        make_character(me / "character", build)
    return load("install").install(slug)


