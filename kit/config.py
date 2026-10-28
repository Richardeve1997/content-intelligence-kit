"""Where the kit finds the creator's files, and how it reads their profile.

Everything that belongs to the creator sits in one folder, me/. The kit reads it only through
this module, so no other file needs to know where that folder is or what profile.json holds.
A problem comes back as a plain sentence the creator can act on, never as a traceback.
"""

from __future__ import annotations

import dataclasses
import difflib
import json
import math
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from jsonschema import Draft202012Validator

# Files the kit owns sit beside this module. They do not move when the creator's folder does.
_PACKAGE = Path(__file__).resolve().parent
SCHEMA_PATH = _PACKAGE / "schema" / "profile.schema.json"
FONTS_DIR = _PACKAGE / "fonts"
FONT_SUFFIXES = (".woff2", ".woff", ".ttf", ".otf")

__all__ = [
    "KitError",
    "NotOnboarded",
    "ProfileError",
    "Profile",
    "Handles",
    "Audience",
    "Cta",
    "Colours",
    "Fonts",
    "Hashtags",
    "Research",
    "PLATFORMS",
    "kit_root",
    "me_dir",
    "work_dir",
    "video_name",
    "free_video_name",
    "research_dir",
    "load_profile",
    "profile_problems",
    "env",
    "bundled_fonts",
    "bundled_font",
    "font_problem",
]


# --- errors ---------------------------------------------------------------------------------


class KitError(Exception):
    """Base for errors that are reported as plain sentences. Entry points print them, not a traceback."""


class NotOnboarded(KitError):
    """There is no finished profile yet. The message tells the creator how to start."""

    def __init__(self, started: bool = False):
        self.started = started
        if started:
            message = "Setup was started but not finished. Say: set me up, and it picks up where it stopped."
        else:
            message = "The kit has not been set up yet. Say: set me up."
        super().__init__(message)

    def __reduce__(self):
        return (NotOnboarded, (self.started,))


class ProfileError(KitError):
    """The profile cannot be used. `problems` holds one sentence for each missing or invalid field."""

    def __init__(self, problems):
        self.problems = list(problems)
        count = len(self.problems)
        head = (
            "Your profile has a problem the kit cannot work around:"
            if count == 1
            else f"Your profile has {count} problems the kit cannot work around:"
        )
        lines = "\n".join(f"- {problem}" for problem in self.problems)
        fix = "Fix it" if count == 1 else "Fix them"
        super().__init__(f"{head}\n{lines}\n{fix} in me/profile.json, or say: set me up.")

    def __reduce__(self):
        return (ProfileError, (self.problems,))


# --- folders --------------------------------------------------------------------------------

_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")


def kit_root() -> Path:
    """The folder that holds kit/, me/, work/, research/ and .env."""
    return _PACKAGE.parent


def me_dir() -> Path:
    """The creator's own folder. The KIT_ME environment variable points it somewhere else (tests use that)."""
    override = os.environ.get("KIT_ME", "").strip()
    if override:
        return Path(override).expanduser().absolute()
    return kit_root() / "me"


def work_dir(slug: str) -> Path:
    """The folder for one video. This only works out the path. It does not create anything."""
    if not isinstance(slug, str) or not _SLUG.fullmatch(slug):
        raise ValueError(
            f"{slug!r} cannot be used as a video name. A video name starts with a letter or number "
            "and then uses only letters, numbers, dots, dashes and underscores, up to 100 characters."
        )
    return kit_root() / "work" / slug


VIDEO_NAME_MAX = 30  # a folder name a creator can read in a path and type in a command
_SMALL_WORDS = frozenset("a an the and or but of to in on at for with from by as is are your my our its it that this so".split())


def video_name(title: str) -> str:
    """A short, readable folder name from a video's title: its first words in lower case, joined by dashes, cut
    between words under VIDEO_NAME_MAX characters. Where the title is cut, no small word is left dangling."""
    words = re.findall(r"[a-z0-9]+", str(title).lower().replace("'", "").replace("’", ""))
    kept: list[str] = []
    for word in words:
        if len("-".join(kept + [word])) > VIDEO_NAME_MAX:
            break
        kept.append(word)
    if not kept and words:  # one word longer than the limit on its own
        kept = [words[0][:VIDEO_NAME_MAX]]
    if len(kept) < len(words):  # the title was cut, so a small word may be left dangling
        while len(kept) > 1 and kept[-1] in _SMALL_WORDS:
            kept.pop()
    return "-".join(kept) or "video"


def free_video_name(title: str) -> str:
    """video_name(title), unless work/ already holds another video under that name: then the first of name-2,
    name-3 and so on that is free. A folder saved for the same idea (its idea.json has this title) is kept."""
    base = name = video_name(title)
    number = 1
    while True:
        folder = work_dir(name)
        if not folder.exists():
            return name
        try:
            if json.loads((folder / "idea.json").read_text(encoding="utf-8")).get("title") == title:
                return name
        except (OSError, ValueError, AttributeError):
            pass
        number += 1
        name = f"{base}-{number}"


def research_dir() -> Path:
    """The folder that holds the research runs and the brain."""
    return kit_root() / "research"


# --- keys and settings from the environment ---------------------------------------------------

_ENV_LINE = re.compile(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)")


def _unquote(raw: str) -> str:
    raw = raw.strip()
    if raw[:1] in ("'", '"'):
        end = raw.find(raw[0], 1)
        if end != -1:
            return raw[1:end]
    return re.split(r"\s#", raw, maxsplit=1)[0].strip()


def _read_dotenv() -> dict[str, str]:
    """The non-empty KEY=VALUE lines of <kit root>/.env. Comments and blank lines are skipped."""
    try:
        text = (kit_root() / ".env").read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return {}
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        found = _ENV_LINE.fullmatch(line)
        if not found:
            continue
        value = _unquote(found.group(2))
        if value:
            values[found.group(1)] = value
        else:
            values.pop(found.group(1), None)  # a later blank line clears an earlier value
    return values


def env(name: str) -> str | None:
    """A setting from the environment, or else from <kit root>/.env. None when it is unset or blank.

    The value is returned and nothing else: it is never printed or logged here.
    """
    value = os.environ.get(name)
    if value is not None and value.strip():
        return value
    return _read_dotenv().get(name)


# --- the schema and the bundled fonts ---------------------------------------------------------


@lru_cache(maxsize=1)
def _schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    return Draft202012Validator(_schema())


PLATFORMS: tuple[str, ...] = tuple(_schema()["$defs"]["platform"]["enum"])


def _default_cost_cap() -> dict[str, float]:
    caps = _schema()["properties"]["research"]["properties"]["cost_cap"]["properties"]
    return {platform: float(rule["default"]) for platform, rule in caps.items()}


def _default_scrape_seconds() -> float:
    return float(_schema()["properties"]["research"]["properties"]["scrape_seconds"]["default"])


@lru_cache(maxsize=1)
def _font_registry() -> dict[str, Any]:
    return json.loads((FONTS_DIR / "fonts.json").read_text(encoding="utf-8"))


def bundled_fonts() -> dict[str, dict[str, Any]]:
    """The fonts that ship with the kit, as {family: {dir, file, weight, sha256, source, path}}."""
    return {
        family: {**entry, "path": FONTS_DIR / entry["dir"] / entry["file"]}
        for family, entry in _font_registry()["families"].items()
    }


def bundled_font(spec: str) -> tuple[str, dict[str, Any]] | None:
    """(family, entry) when `spec` names a bundled family, ignoring case and extra spaces."""
    wanted = " ".join(spec.split()).casefold()
    for family, entry in bundled_fonts().items():
        if family.casefold() == wanted:
            return family, entry
    return None


def _inside_me(path: str) -> bool:
    """True for a plain relative path that stays inside the creator's folder."""
    if not path or "\\" in path or "\x00" in path or path.startswith(("/", "~")) or re.match(r"[A-Za-z]:", path):
        return False
    return ".." not in PurePosixPath(path).parts


def font_problem(spec: str, base: Path | None = None) -> str | None:
    """Why a font setting cannot be used, written to follow its name in a sentence. None when it is fine.

    `base` is the creator's folder. When it is given, a font file of the creator's own must exist there.
    """
    if bundled_font(spec):
        return None
    if "/" not in spec and "\\" not in spec and not spec.lower().endswith(FONT_SUFFIXES):
        names = ", ".join(bundled_fonts())
        near = difflib.get_close_matches(" ".join(spec.split()), list(bundled_fonts()), n=1, cutoff=0.7)
        hint = f" Did you mean {near[0]}?" if near else ""
        return (
            f'is "{spec}", which is not one of the fonts that come with the kit ({names}).{hint} '
            "To use another font, copy its file into me/brand/fonts and enter its path, "
            "such as brand/fonts/YourFont.woff2."
        )
    if not _inside_me(spec):
        return (
            f'is "{spec}", which is not a path inside your me folder. '
            "Use a path with forward slashes, such as brand/fonts/YourFont.woff2."
        )
    if not spec.lower().endswith(FONT_SUFFIXES):
        return f'is "{spec}", which is not a font file the kit can load. Use a .woff2, .woff, .ttf or .otf file.'
    if base is not None and not (base / spec).is_file():
        return f"points to {spec}, but that file is not in your me folder. Copy the font there, or fix the path."
    return None


def _logo_problem(spec: str, base: Path | None) -> str | None:
    if not _inside_me(spec):
        return f'is "{spec}", which is not a path inside your me folder. Use a path such as brand/logo/logo.png.'
    if base is not None and not (base / spec).is_file():
        return f"points to {spec}, but that file is not in your me folder. Copy the logo there, or fix the path."
    return None


def _valid_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError, OSError):
        pass
    if available_timezones():
        return False
    # This machine has no time zone database at all, so settle for the Area/City shape.
    return re.fullmatch(r"[A-Za-z_]+(/[A-Za-z0-9_+-]+)+", name) is not None


# --- the profile -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Handles:
    instagram: str = ""
    tiktok: str = ""
    youtube: str = ""
    facebook: str = ""
    linkedin: str = ""
    twitter: str = ""


@dataclass(frozen=True)
class Audience:
    who: str
    wants: str


@dataclass(frozen=True)
class Cta:
    type: str
    keyword: str | None = None
    payload: str | None = None
    comment_platforms: tuple[str, ...] = ()


@dataclass(frozen=True)
class Colours:
    canvas: str
    ink: str
    accent: str
    field_dark: str
    field_light: str


@dataclass(frozen=True)
class Fonts:
    display: str
    body: str
    mono: str


@dataclass(frozen=True)
class Hashtags:
    base: tuple[str, ...] = ()


@dataclass(frozen=True)
class Research:
    cost_cap: dict[str, float]
    scrape_seconds: float = field(default_factory=_default_scrape_seconds)


@dataclass(frozen=True)
class Profile:
    """The creator's profile. It mirrors kit/schema/profile.schema.json field for field."""

    version: int
    onboarded: bool
    name: str
    handles: Handles
    niche: str
    credibility: str
    audience: Audience
    cta: Cta
    platforms: tuple[str, ...]
    timezone: str
    posting_times: dict[str, str]
    colours: Colours
    fonts: Fonts
    logo: str | None
    styles: tuple[str, ...]
    voice_over: str
    image_provider: str
    hashtags: Hashtags
    research: Research

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, base: Path | None = None) -> "Profile":
        """Check `data` and build a Profile from it. Raises ProfileError listing every problem.

        `base` is the creator's folder. Give it to also check that the logo and any font file exist.
        """
        problems = profile_problems(data, base=base)
        if problems:
            raise ProfileError(problems)
        return _build(data)

    def to_dict(self) -> dict[str, Any]:
        """The profile as plain JSON types, in the order profile.json is written."""
        return _plain(dataclasses.asdict(self))


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _build(data: Mapping[str, Any]) -> Profile:
    handles = data["handles"]
    audience = data["audience"]
    cta = data["cta"]
    research = data.get("research") or {}
    caps = _default_cost_cap()
    caps.update({platform: float(value) for platform, value in research.get("cost_cap", {}).items()})
    seconds = float(research.get("scrape_seconds", _default_scrape_seconds()))
    return Profile(
        version=data["version"],
        onboarded=data["onboarded"],
        name=data["name"],
        handles=Handles(**{platform: handles.get(platform, "") for platform in PLATFORMS}),
        niche=data["niche"],
        credibility=data["credibility"],
        audience=Audience(who=audience["who"], wants=audience["wants"]),
        cta=Cta(
            type=cta["type"],
            keyword=cta.get("keyword"),
            payload=cta.get("payload"),
            comment_platforms=tuple(cta.get("comment_platforms", ())),
        ),
        platforms=tuple(data["platforms"]),
        timezone=data["timezone"],
        posting_times=dict(data["posting_times"]),
        colours=Colours(**{name: data["colours"][name] for name in Colours.__dataclass_fields__}),
        fonts=Fonts(display=data["fonts"]["display"], body=data["fonts"]["body"], mono=data["fonts"]["mono"]),
        logo=data["logo"],
        styles=tuple(data["styles"]),
        voice_over=data["voice_over"],
        image_provider=data["image_provider"],
        hashtags=Hashtags(base=tuple(data["hashtags"]["base"])),
        research=Research(cost_cap=caps, scrape_seconds=seconds),
    )


# --- checking a profile ---------------------------------------------------------------------------


def _node_for(parts: tuple[str, ...]) -> dict[str, Any]:
    """The schema node for a setting, found by walking the schema along its dotted path."""
    node: Any = _schema()
    for part in parts:
        node = _deref(node)
        child = node.get("properties", {}).get(part)
        if child is None:
            child = node.get("additionalProperties")
            if not isinstance(child, dict):
                return {}
        node = child
    return _deref(node)


def _deref(node: Any) -> Any:
    ref = node.get("$ref") if isinstance(node, dict) else None
    if not ref:
        return node
    target: Any = _schema()
    for key in ref[2:].split("/"):
        target = target[key]
    return {**target, **{key: value for key, value in node.items() if key != "$ref"}}


def _should(parts: tuple[str, ...], local: Mapping[str, Any] | None = None) -> str:
    """The hint that ends a sentence. A rule's own description wins over the setting's general one."""
    description = (local or {}).get("description") or _node_for(parts).get("description")
    return f" It should be {description}." if description else ""


def _show(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = text if len(text) <= 50 else text[:47] + "..."
    return f'"{text}"' if isinstance(value, str) else text


def _kind(value: Any) -> str:
    if value is None:
        return "empty"
    if isinstance(value, bool):
        return "true or false"
    if isinstance(value, (int, float)):
        return "a number"
    if isinstance(value, str):
        return "text"
    if isinstance(value, list):
        return "a list"
    return "a group of settings"


def _error_sentences(error) -> Iterator[tuple[str, str]]:
    """One or more (dotted path, sentence) pairs for a schema error."""
    path = list(error.absolute_path)
    parts = tuple(str(part) for part in path if not isinstance(part, int))
    where = ".".join(parts) or "the profile"
    in_a_list = bool(path) and isinstance(path[-1], int)
    kind = error.validator
    rule = error.schema if isinstance(error.schema, Mapping) else {}

    if kind == "required":
        for name in error.validator_value:
            if name not in error.instance:
                missing = parts + (name,)
                local = rule.get("properties", {}).get(name)
                yield ".".join(missing), f"{'.'.join(missing)} is missing.{_should(missing, local)}"
    elif kind == "additionalProperties":
        known = rule.get("properties", {})
        for extra in sorted(key for key in error.instance if key not in known):
            unknown = ".".join(parts + (extra,))
            near = difflib.get_close_matches(extra, list(known), n=1, cutoff=0.6)
            hint = f" Did you mean {'.'.join(parts + (near[0],))}?" if near else ""
            yield unknown, f"{unknown} is not a setting the kit knows.{hint}"
    elif kind == "enum":
        allowed = ", ".join(str(item) for item in error.validator_value)
        if "propertyNames" in error.absolute_schema_path:
            named = _show(error.instance)
            yield where, f"{where} has an entry named {named}, which is not allowed. Use one of: {allowed}."
        else:
            word = "entry" if in_a_list else "value"
            yield where, f"{where} has the {word} {_show(error.instance)}. Use one of: {allowed}."
    elif kind == "const":
        yield where, f"{where} has the value {_show(error.instance)}, but this kit reads {error.validator_value}."
    elif kind == "type" and error.instance is None:
        yield where, f"{where} is empty.{_should(parts, rule)}"
    elif kind == "type":
        yield where, f"{where} has the wrong kind of value ({_kind(error.instance)}).{_should(parts, rule)}"
    elif kind in ("minLength", "minItems"):
        yield where, f"{where} is empty.{_should(parts, rule)}"
    elif kind == "pattern":
        word = "entry" if in_a_list else "value"
        shown = _show(error.instance)
        yield where, f"{where} has the {word} {shown}, which is not in the right form.{_should(parts, rule)}"
    elif kind == "minimum":
        low = error.validator_value
        shown = _show(error.instance)
        yield where, f"{where} is {shown}, which is below the lowest allowed value of {low}.{_should(parts, rule)}"
    elif kind == "maximum":
        high = error.validator_value
        shown = _show(error.instance)
        yield where, f"{where} is {shown}, which is above the highest allowed value of {high}.{_should(parts, rule)}"
    elif kind == "uniqueItems":
        yield where, f"{where} lists the same entry more than once."
    else:
        yield where, f"{where} is not valid.{_should(parts, rule)}"


def _usable_amount(value: Any) -> bool:
    """True for a number that is a real, finite amount. A whole number too big for a float is not."""
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


def _cross_field_checks(data: Mapping[str, Any], failed: set[str]) -> Iterator[tuple[str, str]]:
    """Checks that read two settings together: handles and comment platforms against platforms, and the numbers in the research group.

    A setting the schema already rejected is left alone here, so each mistake is reported once.
    """
    known = set(PLATFORMS)
    platforms = data.get("platforms")
    posting = None
    if isinstance(platforms, list) and "platforms" not in failed:
        posting = [name for name in platforms if isinstance(name, str) and name in known]

    handles = data.get("handles")
    if posting is not None and isinstance(handles, Mapping) and "handles" not in failed:
        for platform in posting:
            path = f"handles.{platform}"
            value = handles.get(platform)
            if path in failed or (isinstance(value, str) and value.strip()):
                continue
            yield path, (
                f"{path} is empty, but {platform} is in platforms.{_should(('handles', platform))} "
                f"If you do not post there, take {platform} out of platforms."
            )

    cta = data.get("cta")
    comment = cta.get("comment_platforms") if isinstance(cta, Mapping) else None
    if posting is not None and isinstance(comment, list) and "cta.comment_platforms" not in failed:
        for platform in comment:
            if isinstance(platform, str) and platform in known and platform not in posting:
                yield "cta.comment_platforms", (
                    f"cta.comment_platforms lists {platform}, which is not in platforms. "
                    f"Add {platform} to platforms, or take it out of cta.comment_platforms."
                )

    research = data.get("research")
    caps = research.get("cost_cap") if isinstance(research, Mapping) else None
    if isinstance(caps, Mapping):
        for platform, value in caps.items():
            path = f"research.cost_cap.{platform}"
            if path in failed or isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            if not _usable_amount(value):
                yield path, (
                    f"{path} is {_show(value)}, which is not a usable amount."
                    f"{_should(('research', 'cost_cap', str(platform)))}"
                )

    seconds = research.get("scrape_seconds") if isinstance(research, Mapping) else None
    if (
        isinstance(seconds, (int, float))
        and not isinstance(seconds, bool)
        and "research.scrape_seconds" not in failed
        and not _usable_amount(seconds)
    ):
        yield "research.scrape_seconds", (
            f"research.scrape_seconds is {_show(seconds)}, which is not a usable number of seconds."
            f"{_should(('research', 'scrape_seconds'))}"
        )


def _checks_beyond_the_schema(
    data: Mapping[str, Any], base: Path | None, failed: set[str]
) -> Iterator[tuple[str, str]]:
    """Checks the schema cannot express: the time zone, the font files, the logo file and the settings that depend on each other."""
    timezone = data.get("timezone")
    if isinstance(timezone, str) and timezone and "timezone" not in failed and not _valid_timezone(timezone):
        yield "timezone", (
            f'timezone is "{timezone}", which is not a time zone name the kit can read. '
            "Use an Area/City name, such as Europe/London."
        )

    fonts = data.get("fonts")
    if isinstance(fonts, Mapping):
        for role in ("display", "body", "mono"):
            spec = fonts.get(role)
            path = f"fonts.{role}"
            if isinstance(spec, str) and spec.strip() and path not in failed:
                problem = font_problem(spec, base)
                if problem:
                    yield path, f"{path} {problem}"

    logo = data.get("logo")
    if isinstance(logo, str) and logo and "logo" not in failed:
        problem = _logo_problem(logo, base)
        if problem:
            yield "logo", f"logo {problem}"

    yield from _cross_field_checks(data, failed)


def profile_problems(data: Any, *, base: Path | None = None) -> list[str]:
    """Every problem with a profile dict, as plain sentences. An empty list means it is good.

    Nothing is read from disk except, when `base` (the creator's folder) is given, the logo and font files.
    """
    if not isinstance(data, Mapping):
        return ["The profile must be one group of settings between { and }, but it is something else."]

    pairs = [pair for error in _validator().iter_errors(data) for pair in _error_sentences(error)]
    failed = {path for path, _ in pairs}
    pairs.extend(_checks_beyond_the_schema(data, base, failed))

    order = {name: index for index, name in enumerate(_schema()["properties"])}
    pairs.sort(key=lambda pair: (order.get(pair[0].split(".")[0], len(order)), pair[0]))
    return list(dict.fromkeys(sentence for _, sentence in pairs))


def load_profile() -> Profile:
    """Read me/profile.json. Raises NotOnboarded when setup has not finished, ProfileError when it is wrong."""
    path = me_dir() / "profile.json"
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise NotOnboarded() from None
    except UnicodeDecodeError:
        raise ProfileError(
            ["me/profile.json must be saved as UTF-8 text. Open it in a text editor and save it again as UTF-8."]
        ) from None
    except OSError as exc:
        raise ProfileError([f"me/profile.json could not be read ({exc.strerror or type(exc).__name__})."]) from None

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProfileError(
            [
                f"me/profile.json is not valid JSON ({exc.msg}, on line {exc.lineno}). "
                "Check for a missing comma, quote or bracket."
            ]
        ) from None
    if not isinstance(data, dict):
        raise ProfileError(["me/profile.json must hold one group of settings between { and }."])

    if data.get("onboarded") is not True:
        raise NotOnboarded(started=True)
    return Profile.from_dict(data, base=me_dir())
