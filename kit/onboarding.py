"""Where the creator is in setup, what they have answered, and the step that writes their profile.

Setup is ten steps (see STEPS). After each one the skill saves what it learned in

    me/.onboarding.json     {"step": 4, "done": ["welcome", "machine", "who", "look"], "answers": {...}}

so the creator can stop at any point and say "set me up" again to pick up where they left off.
`done` lists the finished steps in order, `step` is the number of the furthest one, and `answers`
holds what the creator said, shaped like the fields of me/profile.json plus a few extras (such as
the watch list) that do not belong in the profile.

finish() turns the answers into me/profile.json and me/brand/tokens.css. It checks everything
first and writes nothing unless everything is right. The profile goes down last, so a run that
stops half way still reads as "not set up yet" instead of as a finished profile with no tokens.
Later changes go straight into me/profile.json, followed by `tokens`: finish() builds from the
answers saved during setup, so running it again would put those old answers back.

A skill cannot call Python directly, so everything here is also a command:

    python -m kit.onboarding status                  where setup stands
    python -m kit.onboarding next                    the next step, or "done"
    python -m kit.onboarding save <step> [answers]   save a step's answers (JSON; "-" reads standard input)
    python -m kit.onboarding preview [look]          write me/brand/preview.html and list colours that are hard to read
    python -m kit.onboarding finish                  write me/profile.json and me/brand/tokens.css
    python -m kit.onboarding tokens                  write me/brand/tokens.css again from me/profile.json
    python -m kit.onboarding accounts                which platforms are connected in Zernio (step 7)
    python -m kit.onboarding clips [--voice FILE]    make the test clip for each style they chose (step 9)

Problems come back as one plain sentence each: an exception from the functions, a printed line and
exit status 1 from the command.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import dataclasses
import errno
import html
import json
import os
import posixpath
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from kit import brand, config, media
from kit.config import KitError, Profile, ProfileError

__all__ = [
    "STEPS",
    "DONE",
    "COMMANDS",
    "REQUIRED",
    "CLIP_FOLDER",
    "NO_ZERNIO",
    "State",
    "StateError",
    "AnswerError",
    "account_lines",
    "make_clips",
    "preview",
    "rewrite_tokens",
    "main",
]

STEPS = (
    "welcome",
    "machine",
    "who",
    "look",
    "voice",
    "watchlist",
    "posting",
    "styles",
    "test_clip",
    "tour",
)
DONE = "done"  # what next_step() returns when every step is finished
COMMANDS = ("status", "next", "save", "preview", "finish", "tokens", "accounts", "clips")
STATE_FILE = ".onboarding.json"

# Answers the creator has to give. The rest of the profile has a sensible default.
REQUIRED = (
    "name",
    "handles",
    "niche",
    "credibility",
    "audience",
    "cta",
    "platforms",
    "timezone",
    "colours",
    "fonts",
    "styles",
)
_DEFAULTS: dict[str, Any] = {
    "logo": None,
    "posting_times": {},  # the creator posts by hand
    "voice_over": "record",
    "image_provider": "manual",
    "hashtags": {"base": []},
}
_PROFILE_FIELDS = tuple(field.name for field in dataclasses.fields(Profile))

# A key or a password must never be written into the setup answers. They belong in .env.
_SECRET_NAME = re.compile(r"api[_-]?key|token|secret|passw(?:or)?d|credential", re.IGNORECASE)
_UNFINISHED = "TO" + "DO"  # what a half-written answer leaves behind. Spelled in two parts so it never appears in the kit itself
_PLACEHOLDER = re.compile(r"\b" + _UNFINISHED + r"\b")


# --- errors ---------------------------------------------------------------------------------------------


class StateError(KitError):
    """A setup file cannot be read or written. The message says what to do about it."""


class AnswerError(ProfileError):
    """The answers saved so far cannot make a profile. `problems` has one sentence for each."""

    def __init__(self, problems, doing: str = "write your profile"):
        self.problems = list(problems)
        self.doing = doing
        count = len(self.problems)
        head = (
            f"Setup cannot {doing} yet. One answer is missing or wrong:"
            if count == 1
            else f"Setup cannot {doing} yet. {count} answers are missing or wrong:"
        )
        lines = "\n".join(f"- {problem}" for problem in self.problems)
        Exception.__init__(self, f"{head}\n{lines}\nNothing was written. Get these from the creator, save them, then try again.")

    def __reduce__(self):
        return (AnswerError, (self.problems, self.doing))


# --- small helpers ----------------------------------------------------------------------------------------


def _reason(exc: OSError) -> str:
    return exc.strerror or type(exc).__name__


def _save_problem(exc: OSError, what: str) -> str:
    """One plain sentence for a write that failed: not allowed, out of room, or some other reason."""
    if isinstance(exc, PermissionError) or exc.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
        return (
            f"Setup could not save {what}, because it is not allowed to write in your me folder. "
            "Check that the folder belongs to you and is not read-only, then try again."
        )
    if exc.errno == errno.ENOSPC:
        return f"Setup could not save {what} ({_reason(exc)}). Free some disk space and try again."
    return f"Setup could not save {what} ({_reason(exc)}). Try again. If it fails the same way, that reason is what to fix."


def _temp_beside(path: Path) -> Path:
    return path.with_name(f"{path.name}.{os.getpid()}.tmp")


def _stage(path: Path, text: str) -> Path:
    """Write `text` to a temporary file beside `path` and return its name. Nothing at `path` changes yet."""
    temp = _temp_beside(path)
    temp.parent.mkdir(parents=True, exist_ok=True)
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return temp


def _write_text(path: Path, text: str) -> None:
    """Write a file in one step, so a run that stops never leaves half a file."""
    temp = _stage(path, text)
    try:
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def _merge(into: dict[str, Any], new: Mapping[str, Any]) -> None:
    """Add `new` to `into`. A group merges with a group of the same name. Anything else is replaced."""
    for key, value in new.items():
        if isinstance(value, Mapping) and isinstance(into.get(key), dict):
            _merge(into[key], value)
        else:
            into[key] = copy.deepcopy(dict(value) if isinstance(value, Mapping) else value)


def _names(value: Any) -> Iterator[str]:
    """Every key name inside a group of answers, however deep."""
    if isinstance(value, Mapping):
        for key, inner in value.items():
            yield str(key)
            yield from _names(inner)
    elif isinstance(value, (list, tuple)):
        for inner in value:
            yield from _names(inner)


def _placeholder_problems(value: Any, path: str = "") -> list[str]:
    """A sentence for every answer that still holds an unfinished marker, which means nobody asked the creator."""
    found: list[str] = []
    if isinstance(value, str):
        if _PLACEHOLDER.search(value):
            found.append(f"{path} still says {_UNFINISHED}. Ask the creator for the real answer and save it.")
    elif isinstance(value, Mapping):
        for key, inner in value.items():
            found += _placeholder_problems(inner, f"{path}.{key}" if path else str(key))
    elif isinstance(value, (list, tuple)):
        for inner in value:
            found += _placeholder_problems(inner, path)
    return found


@contextlib.contextmanager
def _creator_folder(folder: Path) -> Iterator[None]:
    """Point kit.config.me_dir() at `folder` for a moment, so the brand module finds the same font files.

    Nothing changes when `folder` already is the creator's folder, which is the normal case.
    """
    if folder.absolute() == config.me_dir():
        yield
        return
    previous = os.environ.get("KIT_ME")
    os.environ["KIT_ME"] = str(folder)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("KIT_ME", None)
        else:
            os.environ["KIT_ME"] = previous


_PLATFORM_LIST = ", ".join(config.PLATFORMS[:-1]) + " and " + config.PLATFORMS[-1]


def _platform_fields(answers: Mapping[str, Any]) -> list[tuple[str, list[Any]]]:
    """The answers that hold platform names, as (where, the names they hold)."""
    found: list[tuple[str, list[Any]]] = []
    platforms = answers.get("platforms")
    if isinstance(platforms, (list, tuple)):
        found.append(("platforms", list(platforms)))
    for field in ("handles", "posting_times"):
        group = answers.get(field)
        if isinstance(group, Mapping):
            found.append((field, list(group)))
    cta = answers.get("cta")
    comment = cta.get("comment_platforms") if isinstance(cta, Mapping) else None
    if isinstance(comment, (list, tuple)):
        found.append(("cta.comment_platforms", list(comment)))
    return found


def _platform_name_problem(answers: Mapping[str, Any]) -> str | None:
    """One message about every platform name the kit does not know, or None when all of them are right."""
    sentences = []
    for where, names in _platform_fields(answers):
        unknown: list[Any] = []
        for name in names:
            if name not in config.PLATFORMS and name not in unknown:
                unknown.append(name)
        if not unknown:
            continue
        shown = [json.dumps(name, ensure_ascii=False) for name in unknown]
        listing = shown[0] if len(shown) == 1 else ", ".join(shown[:-1]) + " and " + shown[-1]
        what = "is not a platform name" if len(shown) == 1 else "are not platform names"
        sentences.append(f"{where} has {listing}, which {what} the kit knows.")
    if not sentences:
        return None
    return " ".join(sentences) + f' The platform names are {_PLATFORM_LIST}. X is saved as "twitter".'


def _profile_written(me: Path) -> bool:
    try:
        stored = json.loads((me / "profile.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return False
    return isinstance(stored, dict) and stored.get("onboarded") is True


# --- the state --------------------------------------------------------------------------------------------


class State:
    """Progress through setup, kept in me/.onboarding.json.

    State() reads the file for the creator's folder (me/, or KIT_ME). It does not create anything
    until something is saved.
    """

    def __init__(self, me: Path | str | None = None):
        self.me = Path(me) if me is not None else config.me_dir()
        self.path = self.me / STATE_FILE
        self.step = 0
        self.done: list[str] = []
        self.answers: dict[str, Any] = {}
        self.replaced_profile = False  # set by finish(): did it replace a me/profile.json that was already there?
        self._read()

    @classmethod
    def load(cls, me: Path | str | None = None) -> "State":
        return cls(me)

    # reading

    def _damaged(self, why: str) -> StateError:
        return StateError(
            f"me/{STATE_FILE} is damaged ({why}). Delete it to start setup again; "
            "everything else in your me folder stays as it is."
        )

    def _read(self) -> None:
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise StateError(f"me/{STATE_FILE} could not be read ({_reason(exc)}).") from None
        try:
            stored = json.loads(raw.decode("utf-8-sig"))
        except UnicodeDecodeError:
            raise self._damaged("it is not saved as UTF-8 text") from None
        except json.JSONDecodeError as exc:
            raise self._damaged(f"it is not valid JSON, {exc.msg} on line {exc.lineno}") from None
        if not isinstance(stored, dict):
            raise self._damaged("it should hold one group of settings")
        done, answers = stored.get("done", []), stored.get("answers", {})
        if not isinstance(done, list) or not all(isinstance(name, str) for name in done):
            raise self._damaged("done should be a list of step names")
        if not isinstance(answers, dict):
            raise self._damaged("answers should be a group of answers")
        # A step name from some other version of the kit is dropped, so that step is simply asked again.
        self.done = [step for step in STEPS if step in done]
        self.answers = answers
        self.step = self._furthest(self.done)

    @staticmethod
    def _furthest(done: list[str]) -> int:
        return max((STEPS.index(step) + 1 for step in done), default=0)

    # asking

    def next_step(self) -> str:
        """The first step that is not done, or "done" when every step is."""
        for step in STEPS:
            if step not in self.done:
                return step
        return DONE

    def missing_answers(self) -> list[str]:
        """The answers the profile needs that nobody has saved yet, in profile order. An answer saved as null is not saved."""
        return [name for name in REQUIRED if self.answers.get(name) is None]

    # saving

    def save(self, step: str, answers: Mapping[str, Any] | None = None) -> None:
        """Merge `answers` into what is saved and mark `step` done. The file is replaced in one step."""
        if not isinstance(step, str) or step not in STEPS:
            raise ValueError(f"{step!r} is not a setup step. The steps are: {', '.join(STEPS)}.")
        if answers is None:
            answers = {}
        if not isinstance(answers, Mapping):
            raise ValueError(f"The answers for {step} must be one group of answers (a JSON object), not a list or a single value.")
        if any(_SECRET_NAME.search(name) for name in _names(answers)):
            raise ValueError(
                "Keys, tokens and passwords do not go in the setup answers. The creator puts them in the .env "
                "file themselves (create it with: cp -n .env.example .env)."
            )
        try:
            json.dumps(answers, allow_nan=False)
        except (TypeError, ValueError):
            raise ValueError(
                f"The answers for {step} can only hold plain text, numbers, true or false, lists and groups of answers."
            ) from None
        unknown_names = _platform_name_problem(answers)
        if unknown_names:
            raise ValueError(unknown_names)

        merged = copy.deepcopy(self.answers)
        _merge(merged, answers)
        done = [name for name in STEPS if name in self.done or name == step]
        stored = {"step": self._furthest(done), "done": done, "answers": merged}
        try:
            _write_text(self.path, json.dumps(stored, indent=2, ensure_ascii=False) + "\n")
        except OSError as exc:
            raise StateError(_save_problem(exc, "your progress")) from None
        self.step, self.done, self.answers = stored["step"], done, merged

    # finishing

    def _candidate(self) -> dict[str, Any]:
        data = copy.deepcopy(_DEFAULTS)
        for field in _PROFILE_FIELDS:
            if self.answers.get(field) is not None:  # an answer saved as null counts as not given
                data[field] = copy.deepcopy(self.answers[field])
        data["version"] = 1
        data["onboarded"] = True
        return data

    def finish(self) -> Profile:
        """Check every answer, then write me/profile.json and me/brand/tokens.css.

        Raises AnswerError, with one sentence for each problem, and writes nothing, when an answer is
        missing or wrong. Returns the profile that was written.
        """
        data = self._candidate()
        problems = config.profile_problems(data, base=self.me) + _placeholder_problems(data)
        if problems:
            raise AnswerError(problems)
        profile = Profile.from_dict(data, base=self.me)

        written = profile.to_dict()
        if "research" in data:
            written["research"] = data["research"]  # exactly what the creator set
        else:
            written.pop("research")
        text = json.dumps(written, indent=2, ensure_ascii=False, allow_nan=False) + "\n"

        tokens_path = self.me / "brand" / "tokens.css"
        profile_path = self.me / "profile.json"
        replaced = profile_path.exists()
        staged: list[Path] = []
        try:
            with _creator_folder(self.me):
                brand.tokens_css(profile)  # a dry run, so a problem here is found before anything is written
                staged.append(_temp_beside(tokens_path))
                brand.write_tokens(profile, staged[0])  # written beside the real file, then renamed into place
            staged.append(_stage(profile_path, text))
            # Both files are ready. The renames are all that is left, and the profile goes last: until it
            # exists, setup still counts as unfinished.
            os.replace(staged[0], tokens_path)
            os.replace(staged[1], profile_path)
        except OSError as exc:
            raise StateError(_save_problem(exc, "your files")) from None
        finally:
            for temp in staged:
                temp.unlink(missing_ok=True)
        self.replaced_profile = replaced
        return profile


# --- the look preview and the tokens ----------------------------------------------------------------------


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Your look</title>
<style>
@@TOKENS@@
:root { --canvas-grid: @@GRID@@; }
* { box-sizing: border-box; }
body { margin: 0; padding: 32px 16px; color: var(--ink); font-family: var(--font-body); font-size: 18px; line-height: 1.5;
  background-color: var(--canvas);
  background-image: linear-gradient(var(--canvas-grid) @@LINE@@px, transparent @@LINE@@px), linear-gradient(90deg, var(--canvas-grid) @@LINE@@px, transparent @@LINE@@px);
  background-size: @@CELL@@px @@CELL@@px; }
main { max-width: 720px; margin: 0 auto; display: grid; gap: 28px; }
.top { display: flex; align-items: center; gap: 20px; }
.top img { height: 64px; width: auto; }
h1 { margin: 0; font-family: var(--font-display); font-size: 52px; line-height: 1.05; font-weight: 700; }
h2 { margin: 0 0 12px; font-family: var(--font-display); font-size: 22px; font-weight: 700; }
p { margin: 0; }
.accent { color: var(--accent); font-weight: 600; }
.button { display: inline-block; padding: 12px 28px; border-radius: 999px; background: var(--accent); color: var(--on-accent); font-family: var(--font-display); font-weight: 600; }
.fields { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.field { padding: 28px 24px; border-radius: 16px; font-family: var(--font-display); font-size: 24px; line-height: 1.2; font-weight: 600; }
.field.dark { background: var(--field-dark); color: var(--on-field-dark); }
.field.light { background: var(--field-light); color: var(--ink); }
.mono { font-family: var(--font-mono); font-size: 16px; }
.swatches { list-style: none; margin: 0; padding: 0; display: grid; gap: 10px; }
.swatches li { display: flex; align-items: center; gap: 12px; }
.chip { width: 32px; height: 32px; border-radius: 8px; border: 1px solid rgba(0, 0, 0, 0.25); flex: none; }
.swatches b { min-width: 110px; font-family: var(--font-display); }
.notes { border-left: 4px solid var(--accent); padding: 4px 0 4px 16px; }
.notes ul { margin: 8px 0 0; padding-left: 20px; }
@media (max-width: 560px) { .fields { grid-template-columns: 1fr; } h1 { font-size: 40px; } }
</style>
</head>
<body>
<main>
<div class="top">@@LOGO@@<h1>@@NAME@@</h1></div>
<p>This is body text on your canvas. <span class="accent">Highlights use your accent colour.</span></p>
<p><span class="button">Follow</span></p>
<div class="fields"><div class="field dark">Mini me scenes sit on this dark field.</div><div class="field light">And on this light one.</div></div>
<p class="mono">00:12 &nbsp; 1.15x &nbsp; take-01</p>
<section>
<h2>Your colours</h2>
<ul class="swatches">
@@SWATCHES@@
</ul>
</section>
<section class="notes">
@@NOTES@@
</section>
</main>
</body>
</html>
"""

# The UI story canvas: composite_v2.py draws a thin line every 90 px in the accent, faint over the canvas colour.
# The preview draws the same grid, so the card looks like the canvas the videos sit on.
CANVAS_GRID_CELL = 90  # pixels between lines
CANVAS_GRID_LINE = 2  # pixels thick
CANVAS_GRID_STRENGTH = 0.09  # how much of the accent shows through

_SWATCHES = (
    ("canvas", "Canvas", "canvas"),
    ("ink", "Ink", "ink"),
    ("accent", "Accent", "accent"),
    ("field_dark", "Dark field", "field-dark"),
    ("field_light", "Light field", "field-light"),
)


def _example_profile() -> dict[str, Any]:
    """The made-up creator that ships with the kit. A preview borrows everything except the look from it."""
    path = config.kit_root() / "me.example" / "profile.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise StateError("me.example/profile.json is missing or damaged, so the preview cannot be drawn. Update the kit.") from None


def _preview_page(profile: Profile, css: str, warnings: list[str]) -> str:
    e = html.escape
    logo = ""
    if profile.logo:
        logo = f'<img src="{e(posixpath.relpath(profile.logo, "brand"))}" alt="">'
    swatches = "\n".join(
        f'<li><span class="chip" style="background: var(--{token})"></span><b>{label}</b> {e(getattr(profile.colours, name))}</li>'
        for name, label, token in _SWATCHES
    )
    if warnings:
        items = "\n".join(f"<li>{e(warning)}</li>" for warning in warnings)
        notes = f"<h2>Hard to read</h2>\n<ul>\n{items}\n</ul>"
    else:
        notes = "<p>Every colour pair is easy to read.</p>"
    # The accent at 9% over the canvas, worked out here as one plain colour: the same blend ffmpeg makes.
    grid = brand.grid_colour(profile.colours.accent, profile.colours.canvas, CANVAS_GRID_STRENGTH)
    return (
        _PAGE.replace("@@TOKENS@@", css.strip())
        .replace("@@GRID@@", grid)
        .replace("@@LINE@@", str(CANVAS_GRID_LINE))
        .replace("@@CELL@@", str(CANVAS_GRID_CELL))
        .replace("@@LOGO@@", logo)
        .replace("@@NAME@@", e(profile.name))
        .replace("@@SWATCHES@@", swatches)
        .replace("@@NOTES@@", notes)
    )


def preview(look: Mapping[str, Any] | None = None, *, name: str | None = None) -> tuple[Path, list[str]]:
    """Write me/brand/preview.html, a card showing the look, and return its path and the readability warnings.

    `look` is {"colours": {...}, "fonts": {...}, "logo": path or None}. Without it the saved look is used.
    The page holds its own copy of the tokens, so it needs nothing else and writes nothing else.
    """
    me = config.me_dir()
    state = State(me)
    if look is None:
        look = {key: state.answers[key] for key in ("colours", "fonts", "logo") if key in state.answers}
    if not isinstance(look, Mapping):
        raise ValueError("The look must be one group of answers with colours, fonts and a logo.")
    shown = name if name is not None else state.answers.get("name")
    shown = shown.strip() if isinstance(shown, str) and shown.strip() else "Your name"

    data = {key: value for key, value in _example_profile().items() if key not in ("colours", "fonts", "logo")}
    data["name"] = shown
    data["logo"] = copy.deepcopy(look.get("logo"))
    for key in ("colours", "fonts"):
        if key in look:
            data[key] = copy.deepcopy(look[key])
    problems = config.profile_problems(data, base=me)
    if problems:
        raise AnswerError(problems, "preview your look")

    profile = Profile.from_dict(data, base=me)
    warnings = brand.check(profile)
    page = _preview_page(profile, brand.tokens_css(profile, me / "brand"), warnings)
    out = me / "brand" / "preview.html"
    try:
        _write_text(out, page)
    except OSError as exc:
        raise StateError(_save_problem(exc, "the preview")) from None
    return out, warnings


def rewrite_tokens() -> tuple[Path, list[str]]:
    """Write me/brand/tokens.css again from me/profile.json, and return its path and the readability warnings."""
    profile = config.load_profile()
    out = config.me_dir() / "brand" / "tokens.css"
    try:
        brand.write_tokens(profile, out)
    except OSError as exc:
        raise StateError(_save_problem(exc, "the brand tokens")) from None
    return out, brand.check(profile)


# --- step 7: which platforms are connected in Zernio -----------------------------------------------------

# What the creator hears when there is no Zernio key. It is not a problem: posting then writes post/manual.md in the
# video's folder, one file with the video's path and a caption for each platform.
NO_ZERNIO = (
    "There is no Zernio key, so posting will give you the video and a file with a caption for each platform "
    "(post/manual.md), ready to post by hand."
)


def _list_of(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def account_lines(platforms: Iterable[str] | None = None) -> list[str]:
    """One line for each platform: "Instagram: connected" or "TikTok: not connected", asked of Zernio.

    `platforms` are the creator's own platforms. Without them every platform the kit knows is listed.
    Raises kit.zernio.NoZernio when there is no key, and ZernioError when Zernio cannot be asked.
    """
    from kit import post  # the posting code, which only the creator who posts needs

    found = post.accounts_check()
    names = [name for name in (list(platforms) if platforms else config.PLATFORMS) if name in found]
    lines = [f"{post.captions.display_name(name)}: {found[name]}" for name in names]
    apart = [post.captions.display_name(name) for name in names if found[name] != "connected"]
    if apart:
        lines.append(
            f"Connect {_list_of(apart)} inside Zernio to post there. "
            "The kit skips a platform that is not connected, and never posts it another way."
        )
    return lines


# --- step 9: a test clip for each style -------------------------------------------------------------------

CLIP_FOLDER = "onboarding-test"  # work/onboarding-test/: the test take, and the clips made from it
_CLIPS = {
    "ui-story": ("UI story", ".claude/skills/ui-story/kit/test_clip.py"),
    "mini-me": ("mini me", ".claude/skills/mini-me/kit/test_clip.py"),
}
# How much of the end of what a script said is passed on when it fails. The mini me clip's failure text is eight
# lines: what it says stopped, up to six lines of the step's own log, and what to do next.
_LAST_LINES = 8


def _lines(text: str | None) -> list[str]:
    return [line.rstrip() for line in (text or "").splitlines() if line.strip()]


def _is_note(line: str) -> bool:
    return line.lower().startswith("note:")


def _what_it_said(done: subprocess.CompletedProcess) -> str:
    """The reason a clip script gave, as plain sentences. A crash gives its last line and no stack.

    A script says what stopped it in a few lines at the end, so the last few are kept. The first line that is
    not a note stays with them, because it is the one that says which step stopped. The notes before it were
    for the creator on the way, and the failure is what matters now.
    """
    err = (done.stderr or "").strip()
    if "Traceback" in err:
        return (
            f"It stopped with an error it did not explain ({err.splitlines()[-1].strip()}). "
            "Try the clip again. If it stops the same way, that error is the reason."
        )
    lines = _lines(done.stdout) or _lines(done.stderr)  # a script that never got as far as a sentence wrote to stderr
    if len(lines) > _LAST_LINES:
        start = len(lines) - _LAST_LINES
        head = next((i for i, line in enumerate(lines) if not _is_note(line)), 0)
        lines = lines[start:] if head >= start else [lines[head], *lines[start:]]
    return "It said:\n" + "\n".join("  " + line for line in lines) if lines else "It gave no reason."


def _notes(done: subprocess.CompletedProcess) -> list[str]:
    """What a clip script said to the creator on the way: every line but its last, which gives the result.

    The notes matter even when the clip is made: that a take is HDR and may look washed out, that the take could
    not be used and the sample card was, or that a saved voice was missing.
    """
    lines = _lines(done.stdout)
    if lines and lines[-1].startswith("test clip "):
        lines.pop()
    return lines


def make_clips(
    folder: Path | None = None, styles: Iterable[str] | None = None, run=subprocess.run, voice: Path | str | None = None
) -> int:
    """Make the test clip for each style one after the other, saying what happened to each. Returns 1 if one failed.

    `styles` are the ones to make; the creator's chosen styles by default. A style whose script is not in this
    copy of the kit, or whose character is not built yet, is said plainly and skipped, which is not a failure.
    `voice` is the creator's recording of the mini me clip's line. Without it that clip uses an ElevenLabs voice
    when the profile chose one and a key is set, and a scratch voice otherwise.
    Each clip is a heavy job, so they never run side by side, and none starts with under 5 GB of disk free.
    """
    profile = config.load_profile()
    chosen = list(styles) if styles is not None else list(profile.styles)
    unknown = [style for style in chosen if style not in _CLIPS]
    if unknown:
        raise ValueError(f"{unknown[0]!r} is not a style. The styles are: {', '.join(_CLIPS)}.")
    recording = Path(voice).expanduser().absolute() if voice is not None else None
    if recording is not None and not recording.is_file():
        raise KitError(f"The recording {recording} is not there. Check the path, or leave it out to use another voice.")
    folder = Path(folder) if folder is not None else config.work_dir(CLIP_FOLDER)
    media.require_free()
    root = config.kit_root()
    failed = 0
    for style in chosen:
        label, relative = _CLIPS[style]
        if not (root / relative).is_file():
            print(f"The {label} test clip is not in this copy of the kit yet ({relative}), so there is no clip for it. An update adds it.")
            continue
        if style == "mini-me" and not (config.me_dir() / "character" / "manifest.json").is_file():
            print("Your mini me character is not built yet, so there is no mini me clip. Build it with the character skill, then make the clips again.")
            continue
        out = folder / f"{style}.mp4"
        folder.mkdir(parents=True, exist_ok=True)
        out.unlink(missing_ok=True)  # an old clip must not pass for a new one
        command = [sys.executable, str(root / relative), "--out", str(out)]
        if style == "ui-story":
            command += ["--work", str(folder)]  # the take, if there is one, is in <folder>/take/
        elif recording is not None:
            command += ["--voice", str(recording)]
        print(f"Making the {label} test clip. This takes a few minutes.", flush=True)
        done = run(command, cwd=str(root), capture_output=True, text=True)
        if done.returncode != 0 or not out.is_file():
            failed += 1
            print(f"The {label} test clip did not finish. {_what_it_said(done)}")
            continue
        try:
            length = f" ({media.probe(out).duration:.1f} s)"
        except KitError:
            length = ""
        for note in _notes(done):
            print(note)
        print(f"{label} test clip: {out}{length}")
    return 1 if failed else 0


# --- the command line -------------------------------------------------------------------------------------


def _answers_from(text: str | None) -> dict[str, Any] | None:
    if text is None:
        return None
    if text == "-":
        text = sys.stdin.read()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"The answers are not valid JSON ({exc.msg}, on line {exc.lineno}). Check for a missing comma, quote or bracket."
        ) from None
    if not isinstance(value, dict):
        raise ValueError("The answers must be one group of answers between { and }.")
    return value


def _status_lines(state: State) -> list[str]:
    following = state.next_step()
    if following == DONE:
        lines = [f"Setup: all {len(STEPS)} steps are done."]
    else:
        lines = [f"Setup: {len(state.done)} of {len(STEPS)} steps done. Next: {following} (step {STEPS.index(following) + 1})."]
    if state.done:
        lines.append("Done: " + ", ".join(state.done) + ".")
    left = [step for step in STEPS if step not in state.done]
    if left:
        lines.append("Still to do: " + ", ".join(left) + ".")
    if _profile_written(state.me):
        lines.append("Profile: written (me/profile.json).")
    else:
        lines.append("Profile: not written yet.")
        missing = state.missing_answers()
        if missing:
            lines.append("Answers still needed before the profile can be written: " + ", ".join(missing) + ".")
    return lines


def _warning_lines(warnings: list[str], intro: str) -> list[str]:
    return [intro, *[f"- {warning}" for warning in warnings]] if warnings else []


def _run(args: argparse.Namespace) -> int:
    if args.command == "status":
        print("\n".join(_status_lines(State())))
    elif args.command == "next":
        print(State().next_step())
    elif args.command == "save":
        state = State()
        state.save(args.step, _answers_from(args.answers))
        following = state.next_step()
        if following == DONE:
            print(f"Saved {args.step}. All {len(STEPS)} steps are done.")
        else:
            print(f"Saved {args.step}. {len(state.done)} of {len(STEPS)} steps done. Next: {following}.")
    elif args.command == "preview":
        path, warnings = preview(_answers_from(args.look))
        print("Wrote me/brand/preview.html. Open it in a browser to see the card.")
        print("\n".join(_warning_lines(warnings, "Hard to read:")) or "Every colour pair is easy to read.")
    elif args.command == "accounts":
        from kit.zernio import NoZernio

        try:
            print("\n".join(account_lines(State().answers.get("platforms"))))
        except NoZernio:
            print(NO_ZERNIO)  # not a problem: the creator posts by hand
    elif args.command == "clips":
        return make_clips(voice=args.voice)
    elif args.command == "finish":
        state = State()
        profile = state.finish()
        print("Wrote me/profile.json and me/brand/tokens.css.")
        if state.replaced_profile:
            print("It replaced the me/profile.json that was already there.")
        lines = _warning_lines(brand.check(profile), "Heads up, these colours are hard to read together:")
        if lines:
            print("\n".join(lines))
    else:
        _, warnings = rewrite_tokens()
        print("Wrote me/brand/tokens.css from your profile.")
        lines = _warning_lines(warnings, "Heads up, these colours are hard to read together:")
        if lines:
            print("\n".join(lines))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m kit.onboarding",
        description="Keep track of setup, and write the creator's profile and brand tokens when the answers are in.",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")
    commands.add_parser("status", help="show how far setup has got")
    commands.add_parser("next", help="print the next step, or done")
    one = commands.add_parser("save", help="save a step's answers and mark the step done")
    one.add_argument("step", help="one of: " + ", ".join(STEPS))
    one.add_argument("answers", nargs="?", help='the answers as JSON, or "-" to read them from standard input')
    one = commands.add_parser("preview", help="write me/brand/preview.html for a look and list colours that are hard to read")
    one.add_argument("look", nargs="?", help='{"colours": {...}, "fonts": {...}, "logo": null} as JSON, or "-" for standard input')
    commands.add_parser("finish", help="check every answer, then write me/profile.json and me/brand/tokens.css")
    commands.add_parser("tokens", help="write me/brand/tokens.css again from me/profile.json")
    commands.add_parser("accounts", help="show which platforms are connected in Zernio (needs a Zernio key)")
    one = commands.add_parser("clips", help="make the test clip for each style the creator chose")
    one.add_argument("--voice", help="a recording of the mini me clip's line, for that clip (default: another voice)")

    args = parser.parse_args(argv)
    try:
        return _run(args)
    except (KitError, ValueError) as exc:
        print(exc, file=sys.stderr)
    except OSError as exc:
        print(f"The setup files could not be used ({_reason(exc)}).", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
