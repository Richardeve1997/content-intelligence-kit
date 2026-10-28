"""The mini me voice-over: the creator's own phone recording, or a voice from ElevenLabs.

Every path ends the same way, in work/<video>/:

  vo.m4a      the voice-over, at -13 LUFS with peaks under -1.5 dBTP, AAC 256 kb/s, 48 kHz (one lossy encode)
  words.json  every word with its start and end, {"text", "dur", "words": [{"w", "s", "e"}]}, the shape
              kit.transcribe.load reads, timed by kit.transcribe.words with forced alignment
  vo.json     how it was made (the source, the model and voice name, the pace), never a key or an id

The paths:

* from_recording(path, slug): the creator records the narration on their phone (free, and the default).
  A light chain cleans it: a high-pass for rumble, a gentle noise reduction, a soft compressor.
* from_elevenlabs(text, slug, voice_id=None): ElevenLabs reads the narration with the creator's key
  (ELEVENLABS_API_KEY, read through kit.config.env and never printed). With no voice id it uses one of
  the account's stock voices, chosen the same way every time, so no id is ever written into the kit.
* from_say(text, slug): a scratch voice from macOS `say`, for test clips and drafts only.
* clone(name, samples): an ElevenLabs copy of the creator's own voice. It runs only when the profile
  records their consent (voice_over is "clone"), and saves the new voice in me/voice/elevenlabs.json. The saved
  copy is used only while voice_over stays "clone"; with "clone" and no copy yet, auto stops and says so
  rather than reading in a stock voice.

The pace. The voice-over is made at its final pace before anything is timed to it, so the picture is
built on the words as they will be heard and the finish runs at 1.0 (FINISH_SPEED). ElevenLabs reads at
its own speed setting of 1.15 (SYNTH_SPEED), which sounds more natural than stretching the audio after;
a model without that setting gets the same 1.15 from a pitch-keeping tempo change. A phone recording gets
that tempo change too (RECORDING_TEMPO, the factor a filmed take is sped up by in the finish). Pauses
longer than 0.35 s are cut to 0.24 s, because the style has no dead air. A paced recording prints one line
saying so; --keep-pace keeps the creator's own pace.

Nothing replaces vo.m4a and words.json until both are made, so a failed run leaves the last voice-over
and its words together. The work is heavy (an encode and a transcription), so it holds a heavy-job slot.

Command line:

    python -m kit.voice auto <video> [--keep-pace]      whatever this video has: a recording in take/,
                                                        else ElevenLabs when the profile chose it
    python -m kit.voice record <video> [<file>]         from a recording (the newest file in take/)
    python -m kit.voice elevenlabs <video> [--voice-id ID]  from the narration in script.md
    python -m kit.voice say <video>                     a scratch voice with macOS say
    python -m kit.voice clone <name> <sample files...>  a copy of the creator's voice, with consent

It exits 0 when the voice-over is made, 1 on a problem (one plain sentence) and 3 when it is waiting for
the creator to record.
"""

from __future__ import annotations

import argparse
import functools
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from kit import config, media, transcribe
from kit.config import KitError
from kit.transcribe import Word

__all__ = [
    "FINISH_SPEED",
    "RECORDING_TEMPO",
    "SYNTH_SPEED",
    "TARGET_LUFS",
    "WAITING",
    "NoElevenLabsKey",
    "VO",
    "chosen_voice",
    "clone",
    "from_elevenlabs",
    "from_recording",
    "from_say",
    "main",
    "narration",
    "saved_voice",
    "take",
]

TARGET_LUFS = -13.0
PEAK_CEILING_DB = -2.5  # sample peak before the AAC encode, which adds about half a dB of true peak
RECORDING_TEMPO = 1.15
SYNTH_SPEED = 1.15
FINISH_SPEED = 1.0
PAUSE_LONGEST = 0.35  # a pause longer than this is cut
PAUSE_KEEP = 0.24  # to this
HEAD_PAD = 0.12
TAIL_PAD = 0.30
SAMPLE_RATE = 48000
AAC_BITRATE = "256k"
SCRATCH_WPM = 190.0
MIN_CLONE_SECONDS = 30.0
WAITING = 3

KEY_NAME = "ELEVENLABS_API_KEY"
API = "https://api.elevenlabs.io"
NO_KEY = "No ElevenLabs key, so record the voice-over on your phone and drop it in work/{slug}/take/"
NO_COPY = (
    "Your profile asks for a copy of your voice, but there is none yet. Make it with: uv run python -m kit.voice clone "
    "'<your name>' <sample files>, or record the voice-over on your phone and drop it in work/{slug}/take/."
)
PACED = (
    "Your recording was sped up to {tempo:g}x and pauses over {longest:g} s were cut to {keep:g} s, the pace this style "
    "runs at. To keep your own pace, make the voice-over again with --keep-pace."
)
RECORD = "Record the voice-over on your phone and drop it in work/{slug}/take/, then say go on."
KEY_REFUSED = (
    "ElevenLabs turned down the key in .env ({name}). Check that it is a current API key from your ElevenLabs "
    "account, or record the voice-over on your phone instead."
)
VOICE_SETTINGS = {"stability": 0.5, "similarity_boost": 0.8, "style": 0.3, "use_speaker_boost": True,
                  "speed": SYNTH_SPEED}
FORMATS = ("pcm_44100", "pcm_24000", "mp3_44100_192", "mp3_44100_128")
TAKE_SUFFIXES = {".m4a", ".mp3", ".wav", ".aif", ".aiff", ".caf", ".aac", ".flac", ".ogg", ".opus", ".mp4",
                 ".mov", ".m4v", ".webm"}
_SAY_NEVER = re.compile(r"^(Albert|Bad News|Bahh|Bells|Boing|Bubbles|Cellos|Wobble|Good News|Jester|Junior|Organ|"
                        r"Superstar|Trinoids|Whisper|Zarvox|Eddy|Flo|Grandma|Grandpa|Reed|Rocko|Sandy|Shelley)\b")


class NoElevenLabsKey(KitError):
    """There is no ElevenLabs key, so the creator records the voice-over instead."""

    def __init__(self, slug: str):
        self.slug = slug
        super().__init__(NO_KEY.format(slug=slug))

    def __reduce__(self):
        return (NoElevenLabsKey, (self.slug,))


@dataclass(frozen=True)
class VO:
    """A finished voice-over: the audio file and its timed words."""

    audio: Path
    words: list[Word]


# --- small helpers --------------------------------------------------------------------------------------------------


def narration(text: str) -> str:
    """The words to say, from script.md: the part under "## Narration", or the whole text when it has none.

    Lines are joined with spaces, headings are left out, and an em or en dash becomes a comma (the kit's
    copy has none, and a voice reads a comma as the same small pause).
    """
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if re.match(r"#{1,6}\s*narration\s*$", line.strip(), re.I)), None)
    if start is not None:
        body = []
        for line in lines[start + 1 :]:
            if re.match(r"#{1,6}\s", line.strip()):
                break
            body.append(line)
        lines = body
    words = " ".join(line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#"))
    words = words.replace(chr(0x2014), ", ").replace(chr(0x2013), ", ")  # an em or en dash
    words = re.sub(r"\s+,", ",", words)
    return re.sub(r"\s+", " ", words).strip()


def take(slug: str) -> Path | None:
    """The newest recording in work/<video>/take/ (a link to it counts), or None when there is none."""
    folder = config.work_dir(slug) / "take"
    if not folder.is_dir():
        return None
    found = [p for p in folder.iterdir()
             if not p.name.startswith(".") and p.suffix.lower() in TAKE_SUFFIXES and p.exists()]
    return max(found, key=lambda p: (p.stat().st_mtime, p.name)) if found else None


def _run(cmd: list[str], what: str) -> subprocess.CompletedProcess:
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0:
        tail = "\n".join(done.stderr.strip().splitlines()[-4:])
        raise KitError(f"ffmpeg could not {what}. What it said:\n{tail}")
    return done


def _ffmpeg(*args: str, what: str) -> None:
    _run(["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-y", *args], what)


@functools.lru_cache(maxsize=1)
def _aac() -> list[str]:
    """Apple's AAC encoder where ffmpeg has it (cleaner peaks), otherwise ffmpeg's own."""
    listing = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    name = "aac_at" if any(line.split()[1:2] == ["aac_at"] for line in listing.splitlines() if line.strip()) else "aac"
    return ["-c:a", name, "-b:a", AAC_BITRATE, "-ar", str(SAMPLE_RATE), "-ac", "2"]


def _integrated(path: Path) -> float:
    from kit import finish  # its measure() reads EBU R128 loudness with ffmpeg

    return finish.measure(path).integrated


def _tightened(samples):
    """`samples` (mono, SAMPLE_RATE) with every pause longer than PAUSE_LONGEST cut to PAUSE_KEEP.

    A pause is a run of 10 ms frames quieter than the recording's own floor plus 8 dB (the floor is its
    5th percentile frame, and the threshold stays between -50 and -32 dBFS). Half the kept pause stays
    after the word before and half before the word after, joined with a 5 ms crossfade so no click is
    heard. The quiet before the first word and after the last is trimmed too; the pads go back on later.
    Done here rather than with ffmpeg's silenceremove, whose kept length changed between ffmpeg versions.
    """
    import numpy as np

    hop = SAMPLE_RATE // 100
    count = len(samples) // hop
    if count < 3:
        return samples
    level = 20 * np.log10(np.sqrt((samples[: count * hop].reshape(count, hop) ** 2).mean(1)) + 1e-9)
    threshold = float(np.clip(np.percentile(level, 5) + 8.0, -50.0, -32.0))
    loud = np.nonzero(level >= threshold)[0]
    if len(loud) == 0:
        return samples
    first, last = int(loud[0]), int(loud[-1])
    half = int(PAUSE_KEEP / 2 * SAMPLE_RATE)
    fade = int(0.005 * SAMPLE_RATE)
    keep = [(max(0, first * hop - int(0.05 * SAMPLE_RATE)), None)]
    frame = first
    while frame <= last:
        if level[frame] >= threshold:
            frame += 1
            continue
        end = frame
        while end <= last and level[end] < threshold:
            end += 1
        if (end - frame) * hop / SAMPLE_RATE > PAUSE_LONGEST:
            keep[-1] = (keep[-1][0], frame * hop + half)
            keep.append((end * hop - half, None))
        frame = end
    keep[-1] = (keep[-1][0], min(len(samples), (last + 1) * hop + int(0.1 * SAMPLE_RATE)))
    out = samples[keep[0][0] : keep[0][1]].copy()
    ramp = np.linspace(0.0, 1.0, fade)
    for a, b in keep[1:]:
        piece = samples[a:b].copy()
        if len(out) >= fade and len(piece) >= fade:
            piece[:fade] = piece[:fade] * ramp + out[-fade:] * ramp[::-1]
            out = out[:-fade]
        out = np.concatenate([out, piece])
    return out


def _read_mono(path: Path):
    import numpy as np

    raw = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SAMPLE_RATE),
                          "-f", "f32le", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.float32).astype(np.float64)


def _atempo(factor: float) -> str:
    steps, left = [], factor
    while left > 2.0:
        steps.append(2.0)
        left /= 2.0
    while left < 0.5:
        steps.append(0.5)
        left /= 0.5
    steps.append(left)
    return ",".join(f"atempo={step:.6f}" for step in steps)


def _levelled(source: Path, out: Path, gain_db: float) -> Path:
    ceiling = 10 ** (PEAK_CEILING_DB / 20)
    chain = (f"volume={gain_db:.3f}dB,aresample={SAMPLE_RATE * 4},"
             f"alimiter=limit={ceiling:.5f}:attack=3:release=60:level=0:latency=1,aresample={SAMPLE_RATE}")
    _ffmpeg("-i", str(source), "-af", chain, "-c:a", "pcm_f32le", str(out), what="level the voice-over")
    return out


def _to_target(source: Path, work: Path) -> Path:
    """Gain into a lookahead limiter, the gain solved until the voice sits on -13 LUFS."""
    level = _integrated(source)
    if not math.isfinite(level):
        raise KitError("The voice-over is silent, so there is nothing to make a voice-over from.")
    gain, out = TARGET_LUFS - level, work / "levelled.wav"
    for _ in range(5):
        _levelled(source, out, gain)
        reached = _integrated(out)
        if abs(reached - TARGET_LUFS) <= 0.1:
            break
        gain += TARGET_LUFS - reached
    return out


def _words_json(found: list[Word], seconds: float) -> dict[str, Any]:
    return {"text": " ".join(w.text for w in found), "dur": round(seconds, 3),
            "words": [{"w": w.text, "s": w.start, "e": w.end} for w in found]}


def _make(raw: Path, slug: str, *, clean: bool, tighten: bool, tempo: float, meta: dict[str, Any]) -> VO:
    """The shared tail: clean, tighten, pace, pad, level, encode once, time the words, then replace the old files."""
    folder = config.work_dir(slug)
    folder.mkdir(parents=True, exist_ok=True)
    with media.heavy_slot(label=f"voice-over {slug}"):
        work = Path(tempfile.mkdtemp(prefix=".voice-", dir=folder))
        try:
            mono = "aresample=48000,aformat=channel_layouts=mono"
            cleaned = work / "cleaned.wav"
            chain = [mono]
            if clean:
                chain += ["highpass=f=70", "afftdn=nr=10:nf=-50:tn=1",
                          "acompressor=threshold=-22dB:ratio=3:attack=10:release=200:knee=6"]
            _ffmpeg("-i", str(raw), "-vn", "-af", ",".join(chain), "-c:a", "pcm_f32le", str(cleaned),
                    what=f"read the sound in {raw.name}")
            source = ["-i", str(cleaned)]
            if tighten:
                tight = work / "tight.f32"
                tight.write_bytes(_tightened(_read_mono(cleaned)).astype("<f4").tobytes())
                source = ["-f", "f32le", "-ar", str(SAMPLE_RATE), "-ac", "1", "-i", str(tight)]
            shaped = work / "shaped.wav"
            chain = [_atempo(tempo)] if abs(tempo - 1.0) > 1e-6 else []
            chain += [f"adelay={int(HEAD_PAD * 1000)}:all=1", f"apad=pad_dur={TAIL_PAD}", "pan=stereo|c0=c0|c1=c0"]
            _ffmpeg(*source, "-af", ",".join(chain), "-c:a", "pcm_f32le", str(shaped), what="shape the voice-over")
            candidate = work / "vo.m4a"
            _ffmpeg("-i", str(_to_target(shaped, work)), *_aac(), "-movflags", "+faststart", str(candidate),
                    what="encode the voice-over")
            seconds = media.probe(candidate).duration
            found = transcribe.words(candidate, align=True)
            if not found:
                raise KitError("No words were heard in the voice-over. Check that the recording has the narration in it.")
            meta = {**meta, "pace": {"tempo": round(tempo, 4), "tightened": tighten}, "duration": round(seconds, 3),
                    "loudness": round(_integrated(candidate), 1), "words": len(found), "made": date.today().isoformat()}
            words_tmp, meta_tmp = work / "words.json", work / "vo.json"
            words_tmp.write_text(json.dumps(_words_json(found, seconds), indent=0) + "\n", encoding="utf-8")
            meta_tmp.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
            os.replace(candidate, folder / "vo.m4a")
            os.replace(words_tmp, folder / "words.json")
            os.replace(meta_tmp, folder / "vo.json")
        finally:
            shutil.rmtree(work, ignore_errors=True)
    return VO(audio=folder / "vo.m4a", words=found)


# --- the recording --------------------------------------------------------------------------------------------------


def from_recording(path: Path, slug: str, *, tempo: float = RECORDING_TEMPO, tighten: bool = True) -> VO:
    """The voice-over from the creator's own recording, read in place and never changed.

    `tempo` (1.15 by default) lifts the pace with the pitch kept, and `tighten` cuts long pauses. Pass
    tempo=1.0 and tighten=False to keep the recording's own pace.
    """
    config.work_dir(slug)  # a bad video name stops here, before any work
    path = Path(path)
    if not path.is_file():
        raise KitError(f"{path} does not exist. Drop the recording in work/{slug}/take/ and try again.")
    if not media.probe(path).has_audio:
        raise KitError(f"{path.name} has no sound in it. Record the voice-over again with the microphone on.")
    if not 0.5 <= tempo <= 2.0:
        raise KitError(f"The tempo must be between 0.5 and 2, such as 1.15. It was {tempo}.")
    made = _make(path, slug, clean=True, tighten=tighten, tempo=tempo, meta={"source": "recording", "file": path.name})
    if abs(tempo - 1.0) > 1e-6 or tighten:
        print(PACED.format(tempo=tempo, longest=PAUSE_LONGEST, keep=PAUSE_KEEP))
    return made


# --- ElevenLabs -------------------------------------------------------------------------------------------------------


def _request(method: str, path: str, key: str, *, params=None, json_body=None, files=None, data=None,
             timeout: float = 180) -> tuple[int, Any, bytes]:
    """One ElevenLabs call: (status, the answer as JSON when it is JSON, the raw body)."""
    import requests

    try:
        response = requests.request(method, API + path, headers={"xi-api-key": key}, params=params, json=json_body,
                                    files=files, data=data, timeout=timeout)
    except requests.RequestException:
        raise KitError("ElevenLabs could not be reached. Check the internet connection and try again.") from None
    body = response.content
    parsed = None
    if "json" in response.headers.get("content-type", ""):
        try:
            parsed = response.json()
        except ValueError:
            parsed = None
    return response.status_code, parsed, body


def _reason(answer: Any) -> str:
    detail = answer.get("detail", answer) if isinstance(answer, dict) else answer
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("status") or "")[:300]
    return str(detail or "")[:300]


def _is_key_problem(status: int, answer: Any) -> bool:
    if status == 401:
        return True
    text = json.dumps(answer) if isinstance(answer, (dict, list)) else str(answer or "")
    return any(word in text for word in ("invalid_api_key", "missing_api_key", "api_key_id_used_as_api_key"))


def _call(method: str, path: str, key: str, **options) -> tuple[Any, bytes]:
    status, answer, body = _request(method, path, key, **options)
    if 200 <= status < 300:
        return answer, body
    if _is_key_problem(status, answer):
        raise KitError(KEY_REFUSED.format(name=KEY_NAME))
    reason = _reason(answer)
    raise KitError(f"ElevenLabs answered {status}: {reason}" if reason else f"ElevenLabs answered {status}.")


def _key(slug: str) -> str:
    key = config.env(KEY_NAME)
    if not key:
        raise NoElevenLabsKey(slug)
    return key


def _stock_voice(key: str) -> tuple[str, str]:
    """(id, name) of the account's first stock voice by name, so the same voice is chosen every time."""
    answer, _ = _call("GET", "/v1/voices", key)
    voices = answer.get("voices", []) if isinstance(answer, dict) else []
    stock = sorted((v for v in voices if v.get("category") == "premade" and v.get("voice_id")),
                   key=lambda v: (str(v.get("name", "")).lower(), v["voice_id"]))
    if not stock:
        raise KitError("Your ElevenLabs account lists no stock voices. Pick a voice in ElevenLabs and give its id "
                       "with --voice-id.")
    return stock[0]["voice_id"], str(stock[0].get("name", "stock voice"))


def _model(key: str) -> str:
    """A v4 model when the API lists one, else eleven_v3, else eleven_multilingual_v2."""
    answer, _ = _call("GET", "/v1/models", key)
    models = answer if isinstance(answer, list) else (answer or {}).get("models", [])
    ids = [m["model_id"] for m in models if m.get("model_id") and m.get("can_do_text_to_speech", True)]
    v4 = [m for m in ids if re.match(r"^eleven_v4(\b|_|$)", m)]
    if v4:
        return max(v4, key=lambda m: ("preview" not in m and "alpha" not in m, m == "eleven_v4",
                                      tuple(int(n) for n in re.findall(r"\d+", m))))
    for model in ("eleven_v3", "eleven_multilingual_v2"):
        if model in ids:
            return model
    return ids[0] if ids else "eleven_multilingual_v2"


def _settings(model: str) -> tuple[dict[str, Any], float]:
    """The voice settings for a model, and the tempo still to apply. v3 has no speed setting and three stabilities."""
    settings = dict(VOICE_SETTINGS)
    if model.startswith("eleven_v3"):
        settings.pop("speed")
        settings["stability"] = min((0.0, 0.5, 1.0), key=lambda s: abs(s - settings["stability"]))
        return settings, SYNTH_SPEED
    return settings, 1.0


def _speech(text: str, voice_id: str, model: str, settings: dict, key: str, work: Path) -> Path:
    """The narration as audio: lossless PCM where the plan allows it, so vo.m4a stays the only lossy encode."""
    body = {"text": text, "model_id": model, "voice_settings": settings}
    last: KitError | None = None
    for fmt in FORMATS:
        status, answer, audio = _request("POST", f"/v1/text-to-speech/{voice_id}", key,
                                         params={"output_format": fmt}, json_body=body, timeout=300)
        if 200 <= status < 300:
            if fmt.startswith("pcm_"):
                out = work / "speech.wav"
                with wave.open(str(out), "wb") as handle:
                    handle.setnchannels(1)
                    handle.setsampwidth(2)
                    handle.setframerate(int(fmt.split("_")[1]))
                    handle.writeframes(audio)
                return out
            out = work / "speech.mp3"
            out.write_bytes(audio)
            return out
        if _is_key_problem(status, answer):
            raise KitError(KEY_REFUSED.format(name=KEY_NAME))
        reason = _reason(answer).lower()
        if status == 403 or (status in (400, 422) and any(w in reason for w in ("format", "tier", "subscription", "plan"))):
            last = KitError(f"ElevenLabs answered {status}: {_reason(answer)}")
            continue  # this plan does not offer that format: try the next one
        reason = _reason(answer)
        raise KitError(f"ElevenLabs answered {status}: {reason}" if reason else f"ElevenLabs answered {status}.")
    raise last or KitError("ElevenLabs offered no audio format this kit can use.")


def from_elevenlabs(text: str, slug: str, voice_id: str | None = None) -> VO:
    """The voice-over read by ElevenLabs: the given voice (a copy of the creator's), or a stock voice."""
    config.work_dir(slug)
    key = _key(slug)
    text = narration(text)
    if not text:
        raise KitError(f"There is no narration to read. Write it in work/{slug}/script.md under ## Narration.")
    if voice_id:
        voice_name = "your saved voice" if voice_id == saved_voice() else "the voice you named"
    else:
        voice_id, voice_name = _stock_voice(key)
    model = _model(key)
    settings, tempo = _settings(model)
    folder = config.work_dir(slug)
    folder.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".speech-", dir=folder))
    try:
        speech = _speech(text, voice_id, model, settings, key, work)
        meta = {"source": "elevenlabs", "model": model, "voice": voice_name, "speed": settings.get("speed")}
        if abs(tempo - 1.0) > 1e-6:
            meta["tempo"] = tempo
        return _make(speech, slug, clean=False, tighten=True, tempo=tempo, meta=meta)
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --- the scratch voice ------------------------------------------------------------------------------------------------


def _say_voice() -> str:
    listing = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
    voices = []
    for line in listing.splitlines():
        found = re.match(r"^(.+?)\s+([a-z]{2}_[A-Z]{2,3})\s+#", line)
        if found and found.group(2).startswith("en_"):
            voices.append(found.group(1).strip())
    for tier in ("premium", "enhanced"):
        better = [v for v in voices if tier in v.lower()]
        if better:
            return better[0]
    for name in ("Samantha", "Daniel", "Karen", "Moira", "Tessa", "Alex", "Fred"):
        if name in voices:
            return name
    usable = [v for v in voices if not _SAY_NEVER.match(v)]
    if not usable:
        raise KitError("macOS has no English voice for say. Record the voice-over on your phone instead.")
    return usable[0]


def from_say(text: str, slug: str, *, voice: str | None = None, wpm: float = SCRATCH_WPM) -> VO:
    """A scratch voice-over from macOS `say`, at about `wpm` words a minute. For test clips and drafts only."""
    config.work_dir(slug)
    if shutil.which("say") is None:
        raise KitError("The scratch voice needs macOS say, which this machine does not have. Record the voice-over "
                       "on your phone instead.")
    text = narration(text)
    if not text:
        raise KitError("There is no narration to read.")
    voice = voice or _say_voice()
    folder = config.work_dir(slug)
    folder.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".say-", dir=folder))
    try:
        (work / "text.txt").write_text(text, encoding="utf-8")
        raw = work / "say.aiff"
        rate = int(wpm * 1.05)  # pauses do not scale with the rate, so ask for a little more than the target
        _run(["say", "-v", voice, "-r", str(rate), "-o", str(raw), "-f", str(work / "text.txt")], "make the scratch voice")
        return _make(raw, slug, clean=False, tighten=True, tempo=1.0,
                     meta={"source": "say", "voice": voice, "rate": rate})
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --- a copy of the creator's voice ----------------------------------------------------------------------------------------


def _saved_path() -> Path:
    return config.me_dir() / "voice" / "elevenlabs.json"


def saved_voice() -> str | None:
    """The id of the copy of the creator's voice, from me/voice/elevenlabs.json, or None."""
    try:
        return json.loads(_saved_path().read_text(encoding="utf-8")).get("voice_id") or None
    except (OSError, ValueError, AttributeError):
        return None


def clone(name: str, samples: list[Path]) -> str:
    """Make an ElevenLabs copy of the creator's voice from their own samples, and return its voice id.

    It runs only when the profile records the creator's consent (voice_over is "clone"). The samples are
    recordings of the creator alone: at least 30 seconds in all, one to two minutes is best.
    """
    profile = config.load_profile()
    if profile.voice_over != "clone":
        raise KitError("A copy of your voice needs your consent first. Say yes to it in setup, which sets "
                       'voice_over to "clone" in me/profile.json, then try again.')
    key = config.env(KEY_NAME)
    if not key:
        raise KitError("No ElevenLabs key, so a copy of your voice cannot be made. Add ELEVENLABS_API_KEY to .env, "
                       "or record the voice-overs on your phone.")
    files = [Path(p) for p in samples]
    missing = [p.name for p in files if not p.is_file()]
    if not files or missing:
        raise KitError("These voice samples are missing: " + (", ".join(missing) or "none were given") + ".")
    seconds = 0.0
    for path in files:
        info = media.probe(path)
        if not info.has_audio:
            raise KitError(f"{path.name} has no sound in it, so it cannot be a voice sample.")
        seconds += info.duration
    if seconds < MIN_CLONE_SECONDS:
        raise KitError(f"The samples hold {seconds:.0f} seconds of your voice. A copy needs at least 30 seconds, and "
                       "one to two minutes sounds best.")
    handles = [path.open("rb") for path in files]
    try:
        answer, _ = _call(
            "POST", "/v1/voices/add", key,
            data={"name": name, "description": f"A copy of {name}'s voice, made with their consent.",
                  "remove_background_noise": "false"},
            files=[("files", (path.name, handle, "audio/mpeg")) for path, handle in zip(files, handles)],
            timeout=600,
        )
    finally:
        for handle in handles:
            handle.close()
    voice_id = (answer or {}).get("voice_id")
    if not voice_id:
        raise KitError("ElevenLabs did not send back a voice for the copy. Try again in a minute.")
    out = _saved_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"name": name, "voice_id": voice_id, "made": date.today().isoformat(),
                               "consent": "voice_over is clone in me/profile.json"}, indent=2) + "\n",
                   encoding="utf-8")
    return voice_id


# --- command line --------------------------------------------------------------------------------------------------------


def _report(vo: VO) -> None:
    seconds = media.probe(vo.audio).duration
    print(f"voice-over {vo.audio} ({seconds:.1f} s), {len(vo.words)} words in {vo.audio.parent / 'words.json'}")


def _script(slug: str) -> str:
    script = config.work_dir(slug) / "script.md"
    if not script.is_file():
        raise KitError(f"work/{slug}/script.md is missing. Write the narration first.")
    return script.read_text(encoding="utf-8")


def chosen_voice(profile) -> str | None:
    """The voice id the profile allows: the saved copy only while voice_over is "clone", else none (a stock voice)."""
    return saved_voice() if profile.voice_over == "clone" else None


def _auto(slug: str, keep_pace: bool = False) -> int:
    profile = config.load_profile()
    recording = take(slug)
    if recording is not None:
        options = {"tempo": 1.0, "tighten": False} if keep_pace else {}
        _report(from_recording(recording, slug, **options))
        return 0
    (config.work_dir(slug) / "take").mkdir(parents=True, exist_ok=True)
    if profile.voice_over in ("elevenlabs", "clone"):
        if not config.env(KEY_NAME):
            print(NO_KEY.format(slug=slug))
            return WAITING
        voice_id = chosen_voice(profile)
        if profile.voice_over == "clone" and voice_id is None:
            print(NO_COPY.format(slug=slug))  # never a stock voice in place of the copy they asked for
            return WAITING
        _report(from_elevenlabs(_script(slug), slug, voice_id))
        return 0
    print(RECORD.format(slug=slug))
    return WAITING


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m kit.voice", description="The mini me voice-over.")
    sub = parser.add_subparsers(dest="command", required=True)
    auto = sub.add_parser("auto", help="the voice-over from whatever this video has")
    auto.add_argument("slug")
    auto.add_argument("--keep-pace", action="store_true", help="keep a recording's own pace and pauses")
    record = sub.add_parser("record", help="the voice-over from a recording")
    record.add_argument("slug")
    record.add_argument("file", nargs="?", type=Path, help="the recording (default: the newest file in take/)")
    record.add_argument("--keep-pace", action="store_true", help="keep the recording's own pace and pauses")
    eleven = sub.add_parser("elevenlabs", help="the voice-over read by ElevenLabs from script.md")
    eleven.add_argument("slug")
    eleven.add_argument("--voice-id", help="an ElevenLabs voice id (default: your saved copy when voice_over is clone, "
                        "else a stock voice)")
    say = sub.add_parser("say", help="a scratch voice-over with macOS say")
    say.add_argument("slug")
    copy = sub.add_parser("clone", help="a copy of your voice, with your consent")
    copy.add_argument("name")
    copy.add_argument("samples", nargs="+", type=Path)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.command == "auto":
            return _auto(args.slug, args.keep_pace)
        if args.command == "record":
            recording = args.file or take(args.slug)
            if recording is None:
                print(RECORD.format(slug=args.slug))
                return WAITING
            options = {"tempo": 1.0, "tighten": False} if args.keep_pace else {}
            _report(from_recording(recording, args.slug, **options))
            return 0
        if args.command == "elevenlabs":
            _report(from_elevenlabs(_script(args.slug), args.slug, args.voice_id or chosen_voice(config.load_profile())))
            return 0
        if args.command == "say":
            _report(from_say(_script(args.slug), args.slug))
            return 0
        clone(args.name, args.samples)
        print(f"Made a copy of your voice and saved it in {_saved_path()}. Voice-overs now use it.")
        return 0
    except NoElevenLabsKey as problem:
        print(problem)
        return WAITING
    except (KitError, ValueError) as problem:
        print(problem)
        return 1


if __name__ == "__main__":
    sys.exit(main())
