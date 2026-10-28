#!/usr/bin/env python3
"""test_clip.py: a short UI story clip in the creator's own brand, made once during setup.

    python test_clip.py --out <clip.mp4> [--take <file>] [--line "<words>"] [--work <folder>] [--keep]

The clip shows what a UI story Reel looks like with their colours, fonts and handle: a branded card on the
grid canvas, karaoke captions at y 1570, and the house finish at 1.15x. It runs 5 to 10 seconds, and it has
to pass the same speech gate as a Reel (kit.qa_captions) before it is handed over.

  with a take     work/onboarding-test/take/ holds their 20 second test take, as a link to the file and never a
                  copy. The clip cuts 5 to 10 seconds of it where they speak, sets it in the rounded
                  picture-in-picture below the card, and captions what they say.
  without a take  the card sits on the canvas and a scratch voice (the macOS say command) reads a sample line,
                  so the captions and the card can still be judged.

It runs the pipeline's own pieces: cut_base.py (the 1080x1920, 60 fps base, with the HDR tone map), light_voice.py,
kit.transcribe, layout.py, scaffold.py (the creator's tokens and fonts), build_captions.py, HyperFrames,
composite_v2.py, kit.finish and kit.qa_captions. It leaves out the person matte, so the head stays inside the
picture-in-picture instead of breaking out above it. A real edit makes the matte, and its model downloads then.

The whole run holds one heavy-job slot and checks for 5 GB of free disk first. The scratch files go in a folder of
their own, <work>/test-clip-build/, which is cleared before a run and deleted after it unless --keep is given.
Nothing else in <work> is touched, and the take is only read. Whatever the script prints before its last line is a
note for the creator, which is why the finish's own loudness report is kept off the screen.
"""

from __future__ import annotations

import argparse
import contextlib
import html
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path

from kit import brand, config, finish, media, qa_captions, transcribe
from kit.config import KitError
from kit.transcribe import Word

KIT = Path(__file__).resolve().parent
SLUG = "onboarding-test"  # work/onboarding-test/: the test take, the scratch files and the clips
SCRATCH = "test-clip-build"  # the script builds here, in <work>, and never in a folder called build
FPS = 60
SPEED = finish.HOUSE["speed"]
MIN_SECONDS, MAX_SECONDS = 5.0, 10.0  # how long the finished clip should run
# The part of a take that is used runs this long before the speed-up, so the clip lands inside 5 to 10 seconds.
SOURCE_MIN, SOURCE_MAX = 6.0, 11.0
LEAD, TAIL = 0.25, 0.3  # seconds of take before the first word and after the last one
SAMPLE_TAIL = TAIL + 0.2  # seconds the sample clip runs on after the voice stops
# The picture-in-picture starts at this row and shows the take from CROP_Y down. With no matte the whole head stays
# inside it, so it starts high enough that the captions (y 1570) fall on the chest and not on the face.
PIP_TOP, CROP_Y = 800, 200
VIDEO = {".mov", ".mp4", ".m4v", ".mkv"}
SAMPLE_LINE = (
    "This is your test clip. It uses your palette and your fonts. "
    "The captions follow every word as it is spoken, so you can judge how they look."
)
SAMPLE_TARGET = 7.0  # how long the finished sample clip should run, in seconds
SAMPLE_RATE = 175  # words a minute to start from
SAMPLE_VOICE = "Samantha"  # a voice every Mac has, tried first
NO_SAY = (
    "This Mac has no say command, so there is no scratch voice for the sample clip. "
    "Film a 20 second test take instead and link it into work/onboarding-test/take/."
)


# --- choosing the part of the take ----------------------------------------------------------------------


def pick_window(words: list[Word], duration: float, *, lo: float = SOURCE_MIN, hi: float = SOURCE_MAX) -> tuple[float, float] | None:
    """(start, end) of the part of a take to use, in seconds on the take, or None when nothing is said in it.

    It starts a moment before the first word. It ends on the last word that finishes between `lo` and `hi`
    seconds after the start, preferring the end of a sentence, and a moment after that word. When speech
    stops before `lo`, the clip runs on to `lo` seconds (or to the end of the take).
    """
    if not words:
        return None
    start = max(0.0, words[0].start - LEAD)
    inside = [w for w in words if w.end - start <= hi]
    if not inside:  # the first word alone is longer than the longest clip
        inside = [words[0]]
    in_range = [w for w in inside if w.end - start >= lo]
    sentence_ends = [w for w in in_range if w.text.rstrip("\"')”’").endswith((".", "?", "!"))]
    last = (sentence_ends or in_range or inside)[-1]
    end = last.end + TAIL
    if end - start < lo:
        end = start + lo
    end = min(end, duration)
    return round(start, 3), round(end, 3)


def window_words(words: list[Word], start: float, end: float) -> list[Word]:
    """The words inside the window, with their times counted from the start of the window."""
    return [
        Word(w.text, round(max(0.0, w.start - start), 3), round(w.end - start, 3))
        for w in words
        if w.start >= start - 0.02 and w.end <= end + 0.02
    ]


def find_take(folder: Path, explicit: Path | None = None) -> tuple[Path | None, str | None]:
    """The take to use, and a plain sentence when a take is there that cannot be used. (None, None) means none."""
    if explicit is not None:
        if not Path(explicit).is_file():
            raise KitError(f"The take {explicit} is not there. Check the path, or leave --take out to use the sample card.")
        return Path(explicit), None
    take = folder / "take"
    files = sorted(p for p in take.iterdir() if p.suffix.lower() in VIDEO and not p.name.startswith(".")) if take.is_dir() else []
    for file in files:
        if file.exists():
            return file, None
    if files:
        return None, (
            f"take/{files[0].name} points to a file that is not there any more, so the clip uses the sample card. "
            "Link the file again to use your take."
        )
    return None, None


# --- the sample voice -----------------------------------------------------------------------------------


def rate_for(rate: int, seconds: float, target: float) -> int:
    """The speaking rate that would make a line read in `target` seconds, given that it took `seconds` at `rate`."""
    return max(110, min(320, round(rate * seconds / target)))


def _ffmpeg(*args) -> None:
    done = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *map(str, args)], capture_output=True, text=True)
    if done.returncode:
        raise KitError("ffmpeg could not make a file for the test clip. What it said:\n" + done.stderr.strip()[-400:])


def speak(line: str, out: Path, rate: int) -> Path:
    """The line read by the macOS say command, as a 48 kHz stereo WAV."""
    if not shutil.which("say"):
        raise KitError(NO_SAY)
    aiff = out.with_suffix(".aiff")
    for voice in (SAMPLE_VOICE, None):
        command = ["say", *(["-v", voice] if voice else []), "-r", str(rate), "-o", str(aiff), line]
        if subprocess.run(command, capture_output=True).returncode == 0:
            break
    else:
        raise KitError("The say command could not read the sample line. Check Spoken Content in System Settings, or film a test take.")
    _ffmpeg("-i", aiff, "-ar", 48000, "-ac", 2, out)
    aiff.unlink(missing_ok=True)
    return out


def sample_clip_seconds(speech: float) -> float:
    """How long the finished sample clip runs, for a voice of `speech` seconds."""
    return (speech + SAMPLE_TAIL) / SPEED


def sample_voice(line: str, out: Path) -> Path:
    """The sample line in a voice, read again once at another speed when the clip would run too long or too short."""
    speak(line, out, SAMPLE_RATE)
    seconds = media.probe(out).duration
    if abs(sample_clip_seconds(seconds) - SAMPLE_TARGET) > 1.0:
        speech = SAMPLE_TARGET * SPEED - SAMPLE_TAIL  # the voice time that makes a clip of the target length
        speak(line, out, rate_for(SAMPLE_RATE, seconds, speech))
    return out


# --- the scene ------------------------------------------------------------------------------------------


def handle_of(profile) -> str:
    for platform in ("instagram", "tiktok", "youtube", "twitter", "facebook", "linkedin"):
        value = getattr(profile.handles, platform, "")
        if value:
            return value
    return profile.name


def name_size(name: str, mode: str) -> int:
    """The size of the name in the card, in px: a longer name is set smaller so it stays inside the card."""
    count = len(name)
    size = 96 if count <= 12 else 80 if count <= 18 else 64 if count <= 26 else 52
    return round(size * 1.25) if mode == "canvas" else size


def card_html(profile, mode: str) -> str:
    """The branded card, as one scene for the graphics layer. Every colour and font comes from the tokens.

    In the split layout it sits in the card zone above the picture-in-picture. In the canvas layout it is
    larger and sits in the middle of the canvas, so it fills the zone.
    """
    name, handle = html.escape(profile.name), html.escape(handle_of(profile))
    if profile.logo:
        suffix = html.escape(Path(profile.logo).suffix.lower() or ".png")
        avatar = f'<img src="assets/logo{suffix}" alt="" style="width:100%;height:100%;border-radius:50%;object-fit:cover">'
    else:
        avatar = html.escape(profile.name[:1].upper())
    zone = "zone-split" if mode == "split" else "zone-full"
    swatches = "".join(
        f'<span class="sw" style="background:var(--{token})"></span>'
        for token in ("canvas", "ink", "accent", "field-dark", "field-light")
    )
    return (
        '<div class="scene" id="testcard">\n'
        f'  <div class="{zone} center">\n'
        f'    <div class="card tc tc-{mode}" id="brandcard" style="position:relative;width:900px">\n'
        '      <div class="hdr">\n'
        f'        <div class="avatar">{avatar}</div>\n'
        f'        <div class="handle">{handle}</div>\n'
        '        <div class="grow"></div>\n'
        '        <span class="pill" id="tc-pill"><span class="dot"></span>UI story</span>\n'
        "      </div>\n"
        '      <div class="tc-body">\n'
        f'        <div class="display tc-name" id="tc-name" style="font-size:{name_size(profile.name, mode)}px">{name}</div>\n'
        f'        <div class="swatches" id="tc-swatches">{swatches}</div>\n'
        "      </div>\n"
        '      <div class="tc-bar" id="tc-bar"></div>\n'
        "    </div>\n"
        "  </div>\n"
        "</div>"
    )


CARD_STYLE = """
:root{--zone-bottom: @@ZONE_BOTTOM@@px}
.tc .hdr{padding:30px 36px;gap:22px}
.tc .avatar{width:84px;height:84px;border-radius:50%;background:var(--accent-tint);color:var(--accent-deep);display:flex;
  align-items:center;justify-content:center;font-family:var(--font-display);font-weight:700;font-size:40px;flex:none;overflow:hidden}
.tc .handle{font-family:var(--font-body);font-size:34px;font-weight:600;color:var(--ink2)}
.tc-body{padding:34px 36px 44px}
.tc-name{font-weight:700;letter-spacing:-.025em;line-height:1.02;overflow-wrap:anywhere}
.swatches{display:flex;gap:18px;margin-top:38px}
.sw{width:112px;height:112px;border-radius:24px;box-shadow:inset 0 0 0 1.5px var(--line)}
.tc-bar{position:absolute;left:0;bottom:0;width:100%;height:14px;background:var(--accent);transform-origin:0 50%}
.tc-canvas .hdr{padding:46px 52px;gap:26px}
.tc-canvas .avatar{width:112px;height:112px;font-size:52px}
.tc-canvas .handle{font-size:44px}
.tc-canvas .tc-body{padding:56px 52px 76px}
.tc-canvas .swatches{gap:24px;margin-top:60px}
.tc-canvas .sw{width:140px;height:230px;border-radius:32px}
"""


def card_js(duration: float) -> str:
    """The timeline lines for the card. The bar never stops, so the card is never still."""
    return "\n".join([
        "// the test card: it pops in, the five colours land one by one, the pill pops, the bar fills",
        "UIS.pop(tl, '#brandcard', 0.05);",
        "UIS.rise(tl, '#tc-name', 0.25);",
        "UIS.fill(tl, '#tc-swatches .sw', [0.6, 0.75, 0.9, 1.05, 1.2]);",
        "UIS.chip(tl, '#tc-pill', 1.4);",
        "gsap.set('#tc-bar', {scaleX: 0});",
        f"tl.to('#tc-bar', {{scaleX: 1, duration: {duration:.3f}, ease: 'none'}}, 0);",
    ])


def write_scene(hf: Path, profile, mode: str, duration: float) -> None:
    """Put the card into the scaffolded composition: its style, its markup and its timeline lines."""
    index = hf / "index.html"
    page = index.read_text(encoding="utf-8")
    for needed in ("<!-- CAPTIONS:BEGIN -->", "UIS.captions(tl);", "</style>"):
        if needed not in page:
            raise KitError(f"The composition the scaffold wrote has no {needed}, so the card has nowhere to go. Update the kit.")
    style = CARD_STYLE.strip("\n").replace("@@ZONE_BOTTOM@@", str(PIP_TOP - 50))  # the card keeps clear of the picture
    page = page.replace("</style>", style + "\n</style>", 1)
    page = page.replace("<!-- CAPTIONS:BEGIN -->", card_html(profile, mode) + "\n\n  <!-- CAPTIONS:BEGIN -->", 1)
    page = page.replace("UIS.captions(tl);", card_js(duration) + "\n\nUIS.captions(tl);", 1)
    index.write_text(page, encoding="utf-8")


# --- the build ------------------------------------------------------------------------------------------


def run(doing: str, command, *, cwd: Path | None = None) -> str:
    """Run one step. A step that fails stops the clip with one plain sentence and the end of what it said."""
    done = subprocess.run([str(part) for part in command], cwd=cwd, capture_output=True, text=True)
    if done.returncode:
        said = "\n".join((done.stdout + done.stderr).strip().splitlines()[-6:])
        raise KitError(f"The test clip stopped while {doing} (exit {done.returncode}). What it said:\n{said}")
    return done.stdout


def ffmpeg_colour(hex_colour: str) -> str:
    return "0x" + hex_colour.lstrip("#")


def write_words(path: Path, words: list[Word], duration: float) -> None:
    data = {"text": " ".join(w.text for w in words), "dur": round(duration, 3),
            "words": [{"w": w.text, "s": w.start, "e": w.end} for w in words]}
    path.write_text(json.dumps(data, indent=0), encoding="utf-8")


def cut_take(take: Path, window: tuple[float, float], build: Path) -> Path:
    """The 1080x1920, 60 fps base from the chosen part of the take."""
    edl = build / "edl.json"
    edl.write_text(json.dumps({"keep": [[window[0], window[1]]]}), encoding="utf-8")
    base = build / "base.mp4"
    said = run("cutting the take", [sys.executable, KIT / "cut_base.py", take, edl, base])
    for sentence in said.splitlines():
        if "HDR" in sentence:  # the cut goes on without the colour fix, and the creator should hear why
            print(sentence)
    return base


def blank_base(profile, seconds: float, out: Path) -> Path:
    """A canvas-coloured 1080x1920, 60 fps video for the sample clip. The compositor draws the canvas over it."""
    canvas = brand.colour_tokens(profile)["canvas"]
    _ffmpeg("-f", "lavfi", "-i", f"color=c={ffmpeg_colour(canvas)}:s=1080x1920:r={FPS}:d={seconds:.3f}",
            *media.encoder_args(bitrate="6M", crf=23), "-r", FPS, out)
    return out


def render_graphics(hf: Path, out: Path) -> Path:
    """The card and the captions on a transparent layer, rendered by HyperFrames."""
    npx = shutil.which("npx")
    if not npx:
        raise KitError("Node is not installed, so the graphics cannot be drawn. Run python3 bin/doctor.py and follow its fix.")
    run("drawing the card and the captions", [npx, "--no-install", "hyperframes", "render", "--format", "webm", "--crf", "10",
                                              "--fps", FPS, "--workers", 1, "--output", out, hf], cwd=config.kit_root())
    if not out.is_file():
        raise KitError("HyperFrames finished but left no graphics layer. Run python3 bin/doctor.py, then try again.")
    return out


def clip(out: Path, *, take: Path | None = None, line: str | None = None, work: Path | None = None, keep: bool = False) -> Path:
    """Make the test clip at `out`. Returns `out`. Raises KitError with one plain sentence when it cannot."""
    profile = config.load_profile()
    folder = Path(work) if work is not None else config.work_dir(SLUG)
    build = folder / SCRATCH
    source, note = find_take(folder, take)
    if note:
        print(note)
    with media.heavy_slot(label="ui story test clip"):
        shutil.rmtree(build, ignore_errors=True)
        build.mkdir(parents=True)
        try:
            final = _build(profile, build, out, source, line)
        finally:
            if not keep:
                shutil.rmtree(build, ignore_errors=True)
    return final


def _build(profile, build: Path, out: Path, take: Path | None, line: str | None) -> Path:
    py = sys.executable
    window, words, voice = None, [], build / "clean.wav"
    if take is not None:
        heard = transcribe.words(take)
        window = pick_window(heard, media.probe(take).duration)
        if window is None:
            print("Nothing can be heard in the take, so the clip uses the sample card.")
            take = None
        else:
            words = window_words(heard, *window)
    if take is not None:
        mode = "split"
        base = cut_take(take, window, build)
        run("cleaning the voice", [py, KIT / "light_voice.py", base, voice])
        seconds = media.probe(base).duration
        layout_note = "your take in the picture-in-picture"
    else:
        mode = "canvas"
        said = line or SAMPLE_LINE
        sample_voice(said, voice)
        words = transcribe.words(voice)
        if not words:
            raise KitError("The sample line was read, but no words could be heard in it. Run python3 bin/doctor.py, then try again.")
        seconds = round(media.probe(voice).duration + SAMPLE_TAIL, 3)
        base = blank_base(profile, seconds, build / "base.mp4")
        layout_note = "a sample card and a scratch voice"

    write_words(build / "base.words.json", words, seconds)
    (build / "layout.json").write_text(json.dumps({"windows": [{"mode": mode, "t": 0}]}), encoding="utf-8")
    hf = build / "hf"
    total = seconds + 2 / FPS  # HyperFrames renders the last partial frame blank, so the composition runs two frames long
    run("setting up the graphics project", [py, KIT / "scaffold.py", hf, f"{total:.4f}"])
    run("laying out the clip", [py, KIT / "layout.py", "--layout", build / "layout.json", "--words", build / "base.words.json",
                                "--duration", f"{seconds:.4f}", "--out", build / "layout.resolved.json", "--hf", hf])
    write_scene(hf, profile, mode, seconds)
    run("writing the captions", [py, KIT / "build_captions.py", "--words", build / "base.words.json", "--hf", hf,
                                 "--layout", build / "layout.resolved.json", "--captions-dir", build / "captions",
                                 "--duration", f"{total:.4f}"])
    gfx = render_graphics(hf, build / "gfx.webm")
    composite = build / "composite.mov"
    run("putting the clip together", [py, KIT / "composite_v2.py", "--base", base, "--layout", build / "layout.resolved.json",
                                      "--pip-top", PIP_TOP, "--crop-y", CROP_Y, "--zoom", 1.0, "--no-head",
                                      "--gfx", gfx, "--audio", voice, "--out", composite])

    candidate = build / "final.next.mp4"
    with contextlib.redirect_stdout(io.StringIO()):  # its report is about loudness and a scratch file, not for the creator
        finish.finish(composite, candidate, speed=SPEED, music=None)
    problems = qa_captions.check(candidate, words, speed=SPEED, fallback=composite)
    if problems:
        raise KitError("The test clip lost words from its sound, so it was not saved:\n" + "\n".join(f"- {p}" for p in problems))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(candidate), str(out))
    length = media.probe(out).duration
    if not MIN_SECONDS <= length <= MAX_SECONDS:
        print(f"note: the clip runs {length:.1f} s; a test clip is best at 5 to 10 s.")
    print(f"test clip {out} ({length:.1f} s, {layout_note})")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="A short UI story clip in the creator's own brand.")
    ap.add_argument("--out", type=Path, required=True, help="where to save the clip, such as work/onboarding-test/ui-story.mp4")
    ap.add_argument("--take", type=Path, help="a take to use (default: the first video in work/onboarding-test/take/)")
    ap.add_argument("--line", help="the words for the scratch voice when there is no take (default: a short sample line)")
    ap.add_argument("--work", type=Path, help="the scratch folder (default: work/onboarding-test)")
    ap.add_argument("--keep", action="store_true", help=f"keep the scratch files in <work>/{SCRATCH}/ to look at")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        clip(args.out, take=args.take, line=args.line, work=args.work, keep=args.keep)
    except (KitError, ValueError) as problem:
        print(problem)
        return 1
    except Exception as problem:  # the creator never sees a stack: say what stopped the clip and what to try
        print(f"The test clip stopped with an error it did not explain ({type(problem).__name__}: {problem}). "
              "Run python3 bin/doctor.py, then try again.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
