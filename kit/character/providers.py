"""The image tools that draw the character.

Every tool has the same one call, `generate(prompt, refs, out) -> out`: draw what the prompt asks for,
showing the tool the reference pictures in `refs` (in that order), and save the picture as a PNG at `out`.

  manual   the creator pastes the prompt into ChatGPT or Gemini themselves. The prompt and copies of the
           pictures to attach go to me/character/inbox/, and the call raises WaitingForImage until the picture
           they save there as <name>.png (or .jpg or .webp) exists. Then the copies are deleted. Free.
  codex    a signed-in Codex CLI, run once per picture through `codex exec` with its image tool.
  openai   the OpenAI Images API, with OPENAI_API_KEY in .env.
  gemini   the Gemini API, with GEMINI_API_KEY in .env.

Keys are read with kit.config.env and only ever go into the request header. They are never printed,
logged or written to a file, and no error message repeats them.
"""

from __future__ import annotations

import base64
import functools
import io
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from kit import config
from kit.character import store
from kit.config import KitError

# The models. Each is the provider's current image model on the day this was written; change it here.
OPENAI_MODEL = "gpt-image-2.5-sunburst"
OPENAI_SIZE = "1536x1024"
OPENAI_QUALITY = "high"
OPENAI_GENERATIONS = "https://api.openai.com/v1/images/generations"
OPENAI_EDITS = "https://api.openai.com/v1/images/edits"

GEMINI_MODEL = "gemini-nano-banana-2.1"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
GEMINI_ASPECT = "16:9"
GEMINI_SIZE = "2K"

# The Codex model that runs the image tool. CODEX_IMAGE_MODEL in .env overrides it; "default" there means the
# model the creator's own Codex settings name.
CODEX_MODEL = "gpt-6-astra"
CODEX_EFFORT = "low"  # the image tool does the drawing, so the model needs little thinking
CODEX_TIMEOUT = 900  # seconds for one picture
HTTP_TIMEOUT = 600

NAMES = ("manual", "openai", "gemini", "codex")
AUTO_ORDER = ("codex", "openai", "gemini")  # the order tools are tried in when the creator named none
KEYS = {"openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY"}
NO_TOOL = "No image tool found, so I'll give you the prompts to paste into ChatGPT or Gemini."
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")

__all__ = [
    "NAMES", "NO_TOOL", "Provider", "ProviderError", "WaitingForImage", "Choice", "ManualProvider",
    "CodexProvider", "OpenAIProvider", "GeminiProvider", "get", "ready", "choose", "draw",
]  # fmt: skip


class ProviderError(KitError):
    """An image tool could not make the picture. The message is a plain sentence and never holds a key."""


class WaitingForImage(KitError):
    """The manual tool is waiting for the creator to make a picture by hand and save it in the inbox."""

    def __init__(self, name: str, prompt_file: Path, image_file: Path, attachments: list[Path], folder: Path):
        self.name = name
        self.prompt_file = prompt_file
        self.image_file = image_file
        self.attachments = attachments
        where = lambda p: _shown(p, folder)  # noqa: E731
        attach = (
            " Attach " + ", then ".join(where(p) for p in attachments) + " in that order."
            if attachments
            else " There is nothing to attach."
        )
        super().__init__(
            f"Waiting for a picture. Paste the prompt in {where(prompt_file)} into ChatGPT or Gemini.{attach} "
            f"Save the picture it makes as {where(image_file)}, then say done."
        )

    def __reduce__(self):
        return (WaitingForImage, (self.name, self.prompt_file, self.image_file, self.attachments, self.prompt_file.parent.parent))


class Provider(Protocol):
    name: str

    def generate(self, prompt: str, refs: list[Path], out: Path) -> Path: ...


def _shown(path: Path, folder: Path) -> str:
    """A path as the creator sees it: from the me folder when it sits inside it."""
    try:
        return str(path.relative_to(folder.parent.parent)) if folder.parent.name == "me" else str(path.relative_to(folder))
    except ValueError:
        return str(path)


def _save_png(data: bytes, out: Path) -> Path:
    """Whatever picture format came back, saved as a PNG at `out`."""
    from PIL import Image  # from the character group; imported here so this module loads without it

    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            out.parent.mkdir(parents=True, exist_ok=True)
            image.save(out, format="PNG")
    except OSError:
        raise ProviderError("The image tool sent back something that is not a picture.") from None
    return out


# --- manual -----------------------------------------------------------------------------------------------------


class ManualProvider:
    """The creator makes each picture by hand in ChatGPT or Gemini and drops it in the inbox.

    While it waits, the inbox holds the prompt and a copy of each picture to attach, named <name>.attach-1,
    <name>.attach-2 and so on, each keeping its own file type (for a sheet drawn from photos, these are copies
    of the creator's photos). The copies are deleted once the picture is back."""

    name = "manual"

    def __init__(self, folder: Path | None = None):
        self.folder = folder

    def generate(self, prompt: str, refs: list[Path], out: Path) -> Path:
        folder = self.folder or store.character_dir()
        inbox = folder / "inbox"
        name = Path(out).stem
        for suffix in IMAGE_SUFFIXES:
            dropped = inbox / f"{name}{suffix}"
            if dropped.is_file():
                saved = _save_png(dropped.read_bytes(), Path(out))
                for copy in inbox.glob(f"{name}.attach-*"):
                    copy.unlink(missing_ok=True)  # only there to be attached, and the picture is back
                return saved
        inbox.mkdir(parents=True, exist_ok=True)
        prompt_file = inbox / f"{name}.prompt.txt"
        prompt_file.write_text(prompt.rstrip("\n") + "\n", encoding="utf-8")
        attachments = []
        for number, ref in enumerate(refs, 1):
            copy = inbox / f"{name}.attach-{number}{Path(ref).suffix.lower() or '.png'}"
            shutil.copyfile(ref, copy)
            attachments.append(copy)
        raise WaitingForImage(name, prompt_file, inbox / f"{name}.png", attachments, folder)


# --- codex ----------------------------------------------------------------------------------------------------------

CODEX_INSTRUCTIONS = (
    "Use your image generation tool exactly once. Pass it the prompt between the two lines of dashes below "
    "word for word, without rewriting, shortening or adding to it, and give it every attached image as a "
    "reference image, in the order they are attached. Do not run shell commands and do not edit files. "
    "When the image is saved, reply with only the full path of the saved image file."
)
_PICTURE = re.compile(r"(/[^\s'\"`<>()\[\]]+\.(?:png|jpg|jpeg|webp))", re.IGNORECASE)


@functools.lru_cache(maxsize=1)
def _codex_ready() -> tuple[bool, str]:
    if shutil.which("codex") is None:
        return False, "the Codex CLI is not installed"
    try:
        done = subprocess.run(["codex", "login", "status"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False, "the Codex CLI did not answer"
    if done.returncode != 0:
        return False, "the Codex CLI is not signed in (run: codex login)"
    return True, ""


def _codex_home() -> Path:
    home = os.environ.get("CODEX_HOME", "").strip()
    return Path(home).expanduser() if home else Path.home() / ".codex"


def _find_codex_picture(text: str, since: float, refs: list[Path]) -> Path | None:
    """The picture this run of Codex saved: a file in its generated_images folder that is newer than the run's
    start and is not one of the reference pictures. A path Codex printed is tried first, then the newest file
    in that folder."""
    folder = (_codex_home() / "generated_images").resolve()
    references = set()
    for ref in refs:
        try:
            references.add(Path(ref).resolve())
        except OSError:
            continue

    def made_now(path: Path) -> bool:
        try:
            real = path.resolve()
            return (real.is_file() and folder in real.parents and real not in references
                    and real.stat().st_mtime >= since - 1)  # fmt: skip
        except OSError:
            return False

    for path in (Path(found) for found in _PICTURE.findall(text)):
        if made_now(path):
            return path
    if folder.is_dir():
        fresh = [p for p in folder.rglob("*") if p.suffix.lower() in IMAGE_SUFFIXES and made_now(p)]
        if fresh:
            return max(fresh, key=lambda p: p.stat().st_mtime)
    return None


class CodexProvider:
    """One `codex exec` run per picture, with the image tool switched on and the references attached."""

    name = "codex"

    def generate(self, prompt: str, refs: list[Path], out: Path) -> Path:
        if shutil.which("codex") is None:
            raise ProviderError("The Codex CLI is not installed, so it cannot draw. Install it, or use another image tool.")
        work = Path(tempfile.mkdtemp(prefix="character-codex-"))
        try:
            last = work / "last-message.txt"
            command = ["codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only", "--enable", "image_generation",
                       "--color", "never", "-c", f"model_reasoning_effort={CODEX_EFFORT}", "--output-last-message", str(last)]  # fmt: skip
            model = (config.env("CODEX_IMAGE_MODEL") or CODEX_MODEL).strip()
            if model and model != "default":
                command += ["--model", model]
            for ref in refs:
                command += ["-i", str(Path(ref).resolve())]
            full = f"{CODEX_INSTRUCTIONS}\n---\n{prompt.strip()}\n---\n"
            started = time.time()
            try:
                done = subprocess.run(command, input=full, cwd=work, capture_output=True, text=True, timeout=CODEX_TIMEOUT)
            except subprocess.TimeoutExpired:
                raise ProviderError(f"Codex did not finish the picture within {CODEX_TIMEOUT // 60} minutes.") from None
            reply = last.read_text(encoding="utf-8", errors="replace") if last.is_file() else ""
            picture = _find_codex_picture(reply + "\n" + done.stdout + "\n" + done.stderr, started, list(refs))
            if picture is None:
                tail = "\n".join((done.stderr or done.stdout or reply).strip().splitlines()[-6:])
                raise ProviderError(f"Codex did not make a picture. The last thing it said:\n{tail}")
            return _save_png(picture.read_bytes(), Path(out))
        finally:
            shutil.rmtree(work, ignore_errors=True)


# --- the HTTP tools ----------------------------------------------------------------------------------------------------


def _http_post(url: str, **kwargs: Any):
    """One POST. Tests replace this function; nothing else in the module talks to the network."""
    import requests

    try:
        return requests.post(url, **kwargs)
    except requests.RequestException as exc:
        raise ProviderError(f"The image service could not be reached ({type(exc).__name__}). Check the connection.") from None


def _key(tool: str) -> str:
    value = config.env(KEYS[tool])
    if not value:
        raise ProviderError(f"{KEYS[tool]} is not set in .env, so the {tool} image tool cannot draw.")
    return value


def _service_error(service: str, response) -> ProviderError:
    code = response.status_code
    if code in (401, 403):  # the service's own words here can repeat part of the key, so they are left out
        return ProviderError(f"{service} refused the key in .env (HTTP {code}). Check it, or use another image tool.")
    try:
        body = response.json()
    except ValueError:
        body = None
    error = body.get("error") if isinstance(body, dict) else None
    detail = error.get("message") if isinstance(error, dict) else error
    detail = str(detail or response.text or "").strip()[:500]
    if code in (402, 429):
        hint = f"{service} says the account is out of credit or busy (HTTP {code}). Try again later or use another image tool."
    else:
        hint = f"{service} did not make the picture (HTTP {code})."
    return ProviderError(f"{hint} {detail}".strip())


def _mime(path: Path) -> str:
    return mimetypes.guess_type(str(path))[0] or "image/png"


class OpenAIProvider:
    """The OpenAI Images API: the generations endpoint with no references, the edits endpoint with them."""

    name = "openai"

    def generate(self, prompt: str, refs: list[Path], out: Path) -> Path:
        key = _key("openai")
        headers = {"Authorization": f"Bearer {key}"}
        settings = {"model": OPENAI_MODEL, "prompt": prompt, "size": OPENAI_SIZE, "quality": OPENAI_QUALITY,
                    "background": "opaque", "n": 1}  # fmt: skip
        if refs:
            files = [("image[]", (Path(r).name, Path(r).read_bytes(), _mime(Path(r)))) for r in refs]
            data = {k: str(v) for k, v in settings.items()}
            response = _http_post(OPENAI_EDITS, headers=headers, data=data, files=files, timeout=HTTP_TIMEOUT)
        else:
            response = _http_post(OPENAI_GENERATIONS, headers=headers, json=settings, timeout=HTTP_TIMEOUT)
        if response.status_code != 200:
            raise _service_error("OpenAI", response)
        try:
            picture = base64.b64decode(response.json()["data"][0]["b64_json"])
        except (ValueError, KeyError, IndexError, TypeError):
            raise ProviderError("OpenAI answered, but with no picture in the reply.") from None
        return _save_png(picture, Path(out))


def _pictures_in(reply: Any) -> list[str]:
    """Every base64 picture in a Gemini reply, in order. Both the interactions shape ({"type": "image",
    "data"}) and the older generateContent shape ({"inlineData": {"data"}}) are read; thoughts are skipped."""
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("thought") is True or node.get("type") == "thought":
                return
            if node.get("type") == "image" and isinstance(node.get("data"), str):
                found.append(node["data"])
            for key in ("inlineData", "inline_data"):
                inline = node.get(key)
                if isinstance(inline, dict) and isinstance(inline.get("data"), str):
                    found.append(inline["data"])
            for key, value in node.items():
                if key not in ("inlineData", "inline_data"):
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(reply)
    return found


class GeminiProvider:
    """The Gemini API's interactions endpoint, asked for an image."""

    name = "gemini"

    def generate(self, prompt: str, refs: list[Path], out: Path) -> Path:
        key = _key("gemini")
        parts: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for ref in refs:
            data = base64.b64encode(Path(ref).read_bytes()).decode("ascii")
            parts.append({"type": "image", "mime_type": _mime(Path(ref)), "data": data})
        body = {
            "model": GEMINI_MODEL,
            "input": parts,
            "response_format": {"type": "image", "mime_type": "image/png", "aspect_ratio": GEMINI_ASPECT,
                                "image_size": GEMINI_SIZE},  # fmt: skip
        }
        headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
        response = _http_post(GEMINI_URL, headers=headers, json=body, timeout=HTTP_TIMEOUT)
        if response.status_code != 200:
            raise _service_error("Gemini", response)
        try:
            pictures = _pictures_in(response.json())
        except ValueError:
            pictures = []
        if not pictures:
            raise ProviderError("Gemini answered, but with no picture in the reply. Try again, or use another image tool.")
        return _save_png(base64.b64decode(pictures[-1]), Path(out))


# --- choosing ------------------------------------------------------------------------------------------------------------


def get(name: str, *, folder: Path | None = None) -> Provider:
    """The tool called `name`. `folder` is the character folder the manual tool's inbox goes in."""
    if name == "manual":
        return ManualProvider(folder)
    if name == "codex":
        return CodexProvider()
    if name == "openai":
        return OpenAIProvider()
    if name == "gemini":
        return GeminiProvider()
    raise ProviderError(f"{name!r} is not an image tool this kit knows. Use one of: {', '.join(NAMES)}.")


def ready(name: str) -> tuple[bool, str]:
    """(whether the tool can draw on this machine, why not)."""
    if name == "manual":
        return True, ""
    if name == "codex":
        return _codex_ready()
    if name in KEYS:
        return (True, "") if config.env(KEYS[name]) else (False, f"{KEYS[name]} is not set in .env")
    return False, f"{name} is not an image tool this kit knows"


def draw(folder: Path, tool: Provider, prompt: str, refs: list[Path], out: Path) -> Path:
    """Ask `tool` for one picture and count the call in the character record. A wait is not a call."""
    started = time.time()
    try:
        path = tool.generate(prompt, refs, out)
    except WaitingForImage:
        raise
    except BaseException:
        store.record_call(folder, Path(out).stem, getattr(tool, "name", "unknown"), False, time.time() - started)
        raise
    store.record_call(folder, Path(out).stem, getattr(tool, "name", "unknown"), True, time.time() - started)
    return path


@dataclass(frozen=True)
class Choice:
    name: str
    provider: Provider
    message: str | None  # a sentence for the creator when the tool is not the one they asked for


def choose(preferred: str | None = None, *, folder: Path | None = None) -> Choice:
    """The tool to draw with. The creator's choice when it is ready; with no choice, the first ready tool.

    When no tool is ready at all the manual tool is used, with NO_TOOL as the message. Choosing manual on
    purpose needs no message."""
    if preferred == "manual":
        return Choice("manual", ManualProvider(folder), None)
    if preferred is not None and preferred not in NAMES:
        raise ProviderError(f"{preferred!r} is not an image tool this kit knows. Use one of: {', '.join(NAMES)}.")
    if preferred is not None:
        ok, why = ready(preferred)
        if ok:
            return Choice(preferred, get(preferred, folder=folder), None)
    others = [name for name in AUTO_ORDER if name != preferred and ready(name)[0]]
    if preferred is None and others:
        return Choice(others[0], get(others[0], folder=folder), None)
    if not others:
        return Choice("manual", ManualProvider(folder), NO_TOOL)
    return Choice(
        "manual",
        ManualProvider(folder),
        f"The {preferred} image tool is not ready ({why}), so I'll give you the prompts to paste into ChatGPT or "
        f"Gemini. The {others[0]} image tool is ready: say if you want to use it instead.",
    )
