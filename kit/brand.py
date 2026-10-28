"""Turns the creator's profile into the brand tokens that every graphic reads.

Colours and fonts come only from the profile. This module names no font, and no colour of its
own except white, the one fixed alternative to the ink for text.
"""

from __future__ import annotations

import math
import os
import posixpath
import re
from pathlib import Path
from urllib.parse import quote, unquote

from kit import config
from kit.config import Profile, ProfileError

__all__ = [
    "MIN_CONTRAST",
    "WHITE",
    "check",
    "colour_tokens",
    "contrast",
    "font_file",
    "grid_colour",
    "text_on",
    "tokens_css",
    "url_file",
    "write_tokens",
]

WHITE = "#ffffff"
MIN_CONTRAST = 4.5  # WCAG AA for body text
GRID_OPACITY = 0.06

# Onboarding writes the tokens to me/brand/tokens.css. Font URLs inside the file are relative to
# that spot, so the file is the same wherever the kit sits and never names a machine path.
_STANDARD_CSS_DIR = "me/brand"
_STANDARD_FONTS_DIR = "kit/fonts"

_FONT_FORMATS = {".woff2": "woff2", ".woff": "woff", ".ttf": "truetype", ".otf": "opentype"}
_HEX = re.compile(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})")


# --- contrast ----------------------------------------------------------------------------------


def _channels(colour: str) -> tuple[int, int, int]:
    found = _HEX.fullmatch(colour.strip()) if isinstance(colour, str) else None
    if not found:
        raise ValueError(f"{colour!r} is not a hex colour. Write it like #rrggbb or #rgb.")
    digits = found.group(1)
    if len(digits) == 3:
        digits = "".join(digit * 2 for digit in digits)
    return int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16)


def _luminance(colour: str) -> float:
    def linear(value: int) -> float:
        scaled = value / 255
        return scaled / 12.92 if scaled <= 0.04045 else ((scaled + 0.055) / 1.055) ** 2.4

    red, green, blue = (linear(value) for value in _channels(colour))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(a: str, b: str) -> float:
    """The WCAG 2 contrast ratio between two hex colours: 1 for no contrast, 21 for black on white."""
    first, second = _luminance(a), _luminance(b)
    return (max(first, second) + 0.05) / (min(first, second) + 0.05)


def _ratio_text(ratio: float) -> str:
    # Rounded down, so a printed ratio is never higher than the real one.
    return f"{math.floor(ratio * 10 + 1e-9) / 10:.1f}:1"


def text_on(surface: str, ink: str) -> str:
    """Whichever of the ink or white reads better on this surface. A tie goes to the ink."""
    return ink if contrast(ink, surface) >= contrast(WHITE, surface) else WHITE


def grid_colour(ink: str, canvas: str, opacity: float = GRID_OPACITY) -> str:
    """The ink at 6% opacity composited over the canvas, as a hex colour."""
    mixed = (int(opacity * top + (1 - opacity) * under + 0.5) for top, under in zip(_channels(ink), _channels(canvas)))
    return "#" + "".join(f"{value:02x}" for value in mixed)


def colour_tokens(profile: Profile) -> dict[str, str]:
    """The colour tokens, keyed by custom property name without the leading dashes."""
    c = profile.colours
    return {
        "canvas": c.canvas,
        "ink": c.ink,
        "accent": c.accent,
        "on-accent": text_on(c.accent, c.ink),
        "field-dark": c.field_dark,
        "field-light": c.field_light,
        "on-field-dark": text_on(c.field_dark, c.ink),
        "on-field-light": text_on(c.field_light, c.ink),
        "grid": grid_colour(c.ink, c.canvas),
    }


def check(profile: Profile) -> list[str]:
    """Warnings, as plain sentences, for colour pairs that will be hard to read. An empty list means all is well.

    Each pair needs a contrast of at least 4.5:1: the accent on the canvas, the best text colour
    on the accent, the ink on the canvas, and the best text colour on each of the two fields.
    """
    c = profile.colours
    tokens = colour_tokens(profile)
    on_accent = tokens["on-accent"]
    short = f"below the {MIN_CONTRAST}:1 needed to read easily"

    warnings = []
    ratio = contrast(c.accent, c.canvas)
    if ratio < MIN_CONTRAST:
        warnings.append(
            f"The accent {c.accent} on the canvas {c.canvas} has a contrast of only {_ratio_text(ratio)}, {short}, "
            "so words and outlines drawn in the accent will be hard to see."
        )
    ratio = contrast(on_accent, c.accent)
    if ratio < MIN_CONTRAST:
        warnings.append(
            f"The best text colour on the accent {c.accent} is {on_accent}, "
            f"which has a contrast of only {_ratio_text(ratio)}, {short}."
        )
    ratio = contrast(c.ink, c.canvas)
    if ratio < MIN_CONTRAST:
        warnings.append(
            f"Your ink {c.ink} on the canvas {c.canvas} has a contrast of only {_ratio_text(ratio)}, {short}."
        )
    for name, field, on_field in (
        ("dark", c.field_dark, tokens["on-field-dark"]),
        ("light", c.field_light, tokens["on-field-light"]),
    ):
        ratio = contrast(on_field, field)
        if ratio < MIN_CONTRAST:
            warnings.append(
                f"The best text colour on the {name} field {field} is {on_field}, "
                f"which has a contrast of only {_ratio_text(ratio)}, {short}."
            )
    return warnings


# --- fonts -------------------------------------------------------------------------------------


def font_file(spec: str) -> Path:
    """The file for a font setting: a bundled family, or a font file of the creator's own."""
    found = config.bundled_font(spec)
    if found:
        return found[1]["path"]
    problem = config.font_problem(spec, config.me_dir())
    if problem:
        raise ProfileError([f"The font setting {problem}"])
    return config.me_dir() / spec


def _relative(target: str, start: str) -> str:
    return posixpath.relpath("/" + target, "/" + start)


def _url(path: str) -> str:
    return quote(path, safe="/")


def url_file(url: str, css_dir: Path) -> Path:
    """The file a url() in a tokens stylesheet points at, for a stylesheet in `css_dir`.

    tokens_css writes every font URL percent-encoded (a space as %20, an accented letter as its UTF-8 bytes), so
    the URL is decoded before it is read as a path. Anything that copies the fonts a stylesheet names uses this.
    """
    return (Path(css_dir) / unquote(url)).resolve()


def _relative_to_folder(file: Path, css_dir: Path) -> str:
    return _url(Path(os.path.relpath(os.path.abspath(file), os.path.abspath(css_dir))).as_posix())


def _font_plan(profile: Profile, css_dir: Path | None) -> tuple[list[tuple[str, str, str, str]], dict[str, str]]:
    """The @font-face rules to write, and the CSS value of each font role."""
    me = config.me_dir()
    faces: list[tuple[str, str, str, str]] = []  # family, url, format, weight range
    stacks: dict[str, str] = {}
    custom_family: dict[str, str] = {}
    problems = []

    for role, generic in (("display", "sans-serif"), ("body", "sans-serif"), ("mono", "monospace")):
        spec = getattr(profile.fonts, role)
        found = config.bundled_font(spec)
        if found:
            family, entry = found
            if css_dir is None:
                url = _url(_relative(f"{_STANDARD_FONTS_DIR}/{entry['dir']}/{entry['file']}", _STANDARD_CSS_DIR))
            else:
                url = _relative_to_folder(entry["path"], css_dir)
            face = (family, url, "truetype", entry["weight"])
        else:
            problem = config.font_problem(spec, me)
            if problem:
                problems.append(f"fonts.{role} {problem}")
                continue
            if css_dir is None:
                url = _url(_relative(spec, "brand"))
            else:
                url = _relative_to_folder(me / spec, css_dir)
            family = custom_family.setdefault(url, f"Custom {role.title()}")
            # A creator's file may be variable or static. Claiming the full range stops the browser
            # from faking a bold on top of a file that already is one.
            face = (family, url, _FONT_FORMATS[Path(spec).suffix.lower()], "100 900")
        if face not in faces:
            faces.append(face)
        stacks[role] = f'"{face[0]}", {generic}'

    if problems:
        raise ProfileError(problems)
    return faces, stacks


def _font_face(family: str, url: str, fmt: str, weight: str) -> str:
    return (
        "@font-face {\n"
        f'  font-family: "{family}";\n'
        f'  src: url("{url}") format("{fmt}");\n'
        f"  font-weight: {weight};\n"
        "  font-style: normal;\n"
        "  font-display: block;\n"
        "}\n"
    )


# --- the tokens file ----------------------------------------------------------------------------

_HEADER = (
    "/* Brand tokens. Generated from profile.json by kit.brand.write_tokens.\n"
    "   Change the profile, not this file: edits here are overwritten. */\n"
)


def tokens_css(profile: Profile, css_dir: Path | None = None) -> str:
    """The text of the tokens file.

    Font URLs are written for a file at me/brand/tokens.css, where onboarding puts it. If the
    stylesheet will sit in another folder, pass that folder as `css_dir` and the URLs are
    worked out from there instead.
    """
    faces, stacks = _font_plan(profile, css_dir)
    lines = [f"  --{name}: {value};" for name, value in colour_tokens(profile).items()]
    lines += [f"  --font-{role}: {stacks[role]};" for role in ("display", "body", "mono")]
    rules = "\n".join(_font_face(*face) for face in faces)
    return f"{_HEADER}\n{rules}\n:root {{\n" + "\n".join(lines) + "\n}\n"


def write_tokens(profile: Profile, out: Path) -> None:
    """Write the tokens file to `out`, creating its folder. Writing the same profile again changes nothing.

    The font URLs are only right for a file at me/brand/. For any other folder, use tokens_css(profile, css_dir).
    """
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(tokens_css(profile).encode("utf-8"))
