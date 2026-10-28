"""No brand colour is written into the kit's code, CSS or JS.

Colours reach graphics only through the creator's tokens: var(--accent) and the others in
me/brand/tokens.css. Neutral greys are fine, so the check looks for chromatic colours, meaning
any colour whose red, green and blue values are not all the same.

It reads three ways of writing a colour:
  #rgb, #rgba, #rrggbb and #rrggbbaa
  0xrrggbb, the form ffmpeg takes
  rgb() and rgba() with plain numbers
It skips url(#id) and href="#id", which point at an element and are not colours.

Scanned: .css .js .mjs .ts .html .svg .json .sh and .py files under .claude/skills/, .py and .js
files under kit/, and .py files under bin/. Left out: the top-level tests/ folder, me.example/,
kit/vendor/, and anything git ignores. Other file types are not checked, and neither are hsl()
or colour names.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Folder -> the file types scanned under it.
SCANNED = {
    ".claude/skills": (".css", ".js", ".mjs", ".ts", ".html", ".svg", ".json", ".sh", ".py"),
    "kit": (".py", ".js"),
    "bin": (".py",),
}

# Left out of the scan. These are top-level folders only: a folder that is merely named tests
# or vendor, further down, is scanned like any other.
EXEMPT_FOLDERS = ("tests", "me.example", "kit/vendor")

# A file that must carry a chromatic colour anyway goes here, as its path from the repo root with
# the reason. Leave it empty unless the colour truly cannot come from the tokens.
NEUTRAL_FALLBACKS: dict[str, str] = {}

# #rgb, #rgba, #rrggbb or #rrggbbaa, standing on its own. The look-behind skips URL fragments
# (page#abc) and HTML entities (&#123;). The look-ahead skips ids such as #add-button.
HEX_COLOUR = re.compile(r"(?<![\w&#])#([0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})(?![\w-])")

# 0xrrggbb as ffmpeg takes it. Eight digits are left alone: those are usually plain 32-bit numbers.
FFMPEG_COLOUR = re.compile(r"(?<!\w)0[xX]([0-9a-fA-F]{6})(?!\w)")

# rgb(30, 144, 255), rgba(30 144 255 / 50%) and the like. The look-behind skips to_rgb( and argb(.
RGB_CALL = re.compile(r"(?<![\w-])rgba?\(([^)]{0,80})\)", re.IGNORECASE)
_NUMBER = r"[0-9]*\.?[0-9]+%?"
RGB_NUMBERS = re.compile(rf"\s*({_NUMBER})\s*[,\s]\s*({_NUMBER})\s*[,\s]\s*({_NUMBER})")

# What stands just before the # in url(#id) and href="#id". Those point at an element.
_QUOTE = r"""(?:&quot;|&apos;|&#3[49];|["'])"""
POINTS_AT_AN_ELEMENT = re.compile(rf"""(?:url\(\s*{_QUOTE}?|href\s*=\s*["'])\Z""", re.IGNORECASE)


def chromatic_colours(text: str) -> list[tuple[int, str]]:
    """Every chromatic colour written in `text`, as (line number, the colour as written)."""
    found: list[tuple[int, str]] = []  # (offset in the text, colour as written)

    for match in HEX_COLOUR.finditer(text):
        if POINTS_AT_AN_ELEMENT.search(text[max(0, match.start() - 24) : match.start()]):
            continue
        digits = match.group(1)
        width = 1 if len(digits) in (3, 4) else 2  # an alpha digit or pair after the colour is ignored
        if len({digits[i : i + width].lower() for i in range(0, 3 * width, width)}) > 1:
            found.append((match.start(), match.group(0)))

    for match in FFMPEG_COLOUR.finditer(text):
        digits = match.group(1)
        if len({digits[i : i + 2].lower() for i in (0, 2, 4)}) > 1:
            found.append((match.start(), match.group(0)))

    for match in RGB_CALL.finditer(text):
        numbers = RGB_NUMBERS.match(match.group(1))
        if numbers and len({float(value.rstrip("%")) for value in numbers.groups()}) > 1:
            found.append((match.start(), match.group(0)))

    return [(text.count("\n", 0, offset) + 1, literal) for offset, literal in sorted(found)]


def would_scan(relative: str) -> bool:
    """True when a file, named by its path from the repo root with forward slashes, is checked."""
    if relative in NEUTRAL_FALLBACKS:
        return False
    if any(relative == top or relative.startswith(top + "/") for top in EXEMPT_FOLDERS):
        return False
    lower = relative.lower()
    return any(relative.startswith(folder + "/") and lower.endswith(types) for folder, types in SCANNED.items())


def repo_files(root: Path) -> list[str]:
    """The files under `root` that might be scanned, as paths from `root` with forward slashes.

    In a git checkout this asks git for the files it would commit, so files that git ignores
    (downloaded tools, caches) are not included. Anywhere else it walks the scanned folders.
    """
    if (root / ".git").exists() and shutil.which("git"):
        listing = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True,
        )
        if listing.returncode == 0:
            return sorted(name for name in listing.stdout.decode("utf-8", "replace").split("\0") if name)
    return sorted(
        path.relative_to(root).as_posix()
        for folder in SCANNED
        for path in (root / folder).rglob("*")
        if path.is_file()
    )


def scanned_files(root: Path = ROOT) -> list[Path]:
    return [root / name for name in repo_files(root) if would_scan(name) and (root / name).is_file()]


def find_problems(root: Path = ROOT) -> list[str]:
    """One 'path:line  colour' entry for every chromatic colour found in the scanned files."""
    problems = []
    for path in scanned_files(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        for line, literal in chromatic_colours(text):
            problems.append(f"{path.relative_to(root).as_posix()}:{line}  {literal}")
    return problems


def failure_message(problems: list[str]) -> str:
    return (
        "A brand colour must come from the creator's tokens (var(--accent) and the others in "
        "me/brand/tokens.css), not be written into the file. If a match below is an element id "
        "that only looks like a colour, rename the id.\n" + "\n".join(problems)
    )


# --- the detector, proved on known cases ---------------------------------------------------------

FLAGGED = [
    "color: #1E90FF;",
    "color:#1e90ff",
    "accent = '#19f'",
    "background: #1E90FF80",  # with an alpha pair
    "#19fa",  # short form with an alpha digit
    'fill="#FFEB3B"',
    "var(--accent, #1E90FF)",  # a hardcoded fallback is still hardcoded
    "border: 1px solid #c04a28",
    "color=c=0x1E90FF:s=1080x1920",
    "-i color=0x1e90ff@0.5",
    "0X1E90FF",
    "rgb(30, 144, 255)",
    "rgba(30,144,255,0.5)",
    "rgb(30 144 255 / 50%)",
    "RGBA(30, 144, 255, .5)",
    "rgb(12%, 56%, 100%)",
    "rgba(255, 255, 254, 1)",
    "box-shadow: 0 0 4px rgba(\n    30,\n    144,\n    255, 0.4)",
]

NOT_FLAGGED = [
    # greys, written every way
    "#fff",
    "#FFFFFF",
    "#000",
    "#000000",
    "#333333",
    "#f2f2f2",
    "#888",
    "#ABABAB",
    "#ffffffcc",  # white with an alpha pair
    "#000a",
    "0xFFFFFF",
    "0x000000",
    "0x333333@0.5",
    "rgba(0, 0, 0, 0.5)",
    "rgb(255 255 255 / 6%)",
    "rgba(51,51,51,1)",
    "rgb(0%, 0%, 0%)",
    # things that are not colours
    'href="#top"',
    "&#123;",
    "see page#abc",
    "# a comment",
    "#12345",  # five digits
    "0x1F",  # two digits
    "0xDEADBEEF",  # eight digits: a plain 32-bit number, not an ffmpeg colour
    "x = 0x1E90FFA",  # seven digits
    "rgb(var(--r), var(--g), var(--b))",
    "rgba(var(--ink-rgb), 0.06)",
    "rgb(from var(--accent) r g b / 50%)",
    "rgba(${red}, ${green}, ${blue}, 0.5)",
    "to_rgb(30, 144, 255)",
    "argb(30, 144, 255)",
    "my-rgb(30, 144, 255)",
    "color: var(--accent)",
    'f"#{red:02x}{green:02x}{blue:02x}"',
]

# Pointers to an element are not colours, even when the id happens to look like one.
FRAGMENT_REFERENCES = [
    'fill="url(#a1b2c3)"',
    "filter: url(#face)",
    "filter: url( '#cafe' )",
    'mask="url( &quot;#bad&quot; )"',
    '<use href="#bad"/>',
    "<use xlink:href='#cafe'/>",
    '<use href = "#dead"/>',
]


@pytest.mark.parametrize("text", FLAGGED)
def test_the_detector_flags_chromatic_colours(text):
    assert chromatic_colours(text), text


@pytest.mark.parametrize("text", NOT_FLAGGED)
def test_the_detector_passes_greys_and_things_that_are_not_colours(text):
    assert chromatic_colours(text) == [], text


def test_the_detector_reads_ffmpeg_colour_strings():
    assert chromatic_colours("color=c=0x1E90FF") == [(1, "0x1E90FF")]
    assert chromatic_colours("color=c=0x1E90FF@0.5:s=2x2") == [(1, "0x1E90FF")]
    assert chromatic_colours("color=c=0xAAAAAA") == []


def test_the_detector_reads_rgb_functions():
    assert chromatic_colours("a { color: rgb(30, 144, 255) }") == [(1, "rgb(30, 144, 255)")]
    assert chromatic_colours("a { color: rgba(30 144 255 / 0.5) }") == [(1, "rgba(30 144 255 / 0.5)")]
    assert chromatic_colours("a { color: rgba(7, 7, 7, 0.5) }") == []


@pytest.mark.parametrize("text", FRAGMENT_REFERENCES)
def test_the_detector_skips_references_to_an_element(text):
    assert chromatic_colours(text) == [], text


def test_a_colour_beside_a_reference_is_still_flagged():
    assert chromatic_colours("background: url(photo.png) #1E90FF") == [(1, "#1E90FF")]
    assert chromatic_colours('<use href="#bad" fill="#1E90FF"/>') == [(1, "#1E90FF")]


def test_an_element_id_that_looks_like_a_colour_is_flagged_so_it_gets_renamed():
    assert chromatic_colours("#add { margin: 0 }") == [(1, "#add")]


def test_the_detector_reports_the_line_of_each_hit():
    text = "\n".join(
        [
            "a {",
            "  color: #fff;",
            "  fill: #c04a28;",
            "  stroke: rgb(30, 144, 255);",
            "}",
            "b { stroke: #09F; fill: 0x1E90FF }",
            "",
        ]
    )
    assert chromatic_colours(text) == [
        (3, "#c04a28"),
        (4, "rgb(30, 144, 255)"),
        (6, "#09F"),
        (6, "0x1E90FF"),
    ]


# --- which files are scanned ---------------------------------------------------------------------

SCOPE = [
    # under .claude/skills: nine file types
    (".claude/skills/ui-story/kit/card.css", True),
    (".claude/skills/ui-story/kit/card.js", True),
    (".claude/skills/ui-story/kit/card.mjs", True),
    (".claude/skills/ui-story/kit/card.ts", True),
    (".claude/skills/ui-story/kit/card.html", True),
    (".claude/skills/ui-story/kit/icon.svg", True),
    (".claude/skills/ui-story/kit/theme.json", True),
    (".claude/skills/ui-story/kit/run.sh", True),
    (".claude/skills/ui-story/kit/build.py", True),
    (".claude/skills/ui-story/kit/BUILD.PY", True),  # the case of the extension does not matter
    (".claude/skills/ui-story/SKILL.md", False),
    (".claude/skills/ui-story/kit/photo.png", False),
    # kit/ holds Python and JavaScript only
    ("kit/config.py", True),
    ("kit/research/rank.js", True),
    ("kit/report.css", False),
    ("kit/schema/profile.schema.json", False),
    ("kit/fonts/fonts.json", False),
    # bin/ holds Python only
    ("bin/doctor.py", True),
    ("bin/fetch_gsap.sh", False),
    # the exempt folders are exempt at the top level only
    ("kit/vendor/gsap.min.js", False),
    ("kit/vendor/nested/lib.py", False),
    ("kit/research/vendor/lib.js", True),
    ("kit/vendors/lib.py", True),
    (".claude/skills/ui-story/vendor/lib.js", True),
    (".claude/skills/ui-story/tests/check.py", True),
    # outside every scanned folder
    ("tests/test_config.py", False),
    ("me.example/brand/tokens.css", False),
    ("docs/page.html", False),
    ("README.md", False),
]


@pytest.mark.parametrize("relative,expected", SCOPE)
def test_which_files_are_scanned(relative, expected):
    assert would_scan(relative) is expected


def test_a_listed_neutral_fallback_is_left_out(monkeypatch):
    assert would_scan("kit/odd.py") is True
    monkeypatch.setitem(NEUTRAL_FALLBACKS, "kit/odd.py", "needs a literal for a stated reason")
    assert would_scan("kit/odd.py") is False


def _write_tree(root, files):
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def test_a_folder_with_no_git_is_scanned_as_described(tmp_path):
    blue = "#1E90FF"
    clean_css = "a { color: var(--accent); background: #f2f2f2; box-shadow: 0 0 4px rgba(0, 0, 0, .3) }\n"
    _write_tree(
        tmp_path,
        {
            # each of these carries a chromatic colour, in a different form, and is scanned
            ".claude/skills/demo/a.css": f"a {{ color: {blue} }}\n",
            ".claude/skills/demo/b.svg": '<rect fill="rgb(30, 144, 255)"/>\n',
            ".claude/skills/demo/c.json": f'{{"accent": "{blue}"}}\n',
            ".claude/skills/demo/d.sh": "ffmpeg -f lavfi -i color=c=0x1E90FF\n",
            ".claude/skills/demo/vendor/e.js": f'const c = "{blue}";\n',
            ".claude/skills/demo/tests/f.py": f'C = "{blue}"\n',
            ".claude/skills/demo/g.ts": f'const c: string = "{blue}";\n',
            ".claude/skills/demo/h.mjs": f'export const c = "{blue}";\n',
            ".claude/skills/demo/i.html": f'<p style="color:{blue}">x</p>\n',
            "kit/a.py": f'C = "{blue}"\n',
            "kit/sub/b.js": f'const c = "{blue}";\n',
            "bin/tool.py": f'C = "{blue}"\n',
            # scanned and clean
            ".claude/skills/demo/ok.css": clean_css,
            # left out
            ".claude/skills/demo/note.md": f"{blue}\n",
            "kit/page.css": f"a {{ color: {blue} }}\n",
            "kit/vendor/lib.js": f'const c = "{blue}";\n',
            "tests/test_x.py": f'C = "{blue}"\n',
            "me.example/brand/tokens.css": f":root {{ --accent: {blue} }}\n",
            "bin/run.sh": f"echo '{blue}'\n",
        },
    )
    reported = sorted(line.split(":")[0] for line in find_problems(tmp_path))
    assert reported == sorted(
        [
            ".claude/skills/demo/a.css",
            ".claude/skills/demo/b.svg",
            ".claude/skills/demo/c.json",
            ".claude/skills/demo/d.sh",
            ".claude/skills/demo/vendor/e.js",
            ".claude/skills/demo/tests/f.py",
            ".claude/skills/demo/g.ts",
            ".claude/skills/demo/h.mjs",
            ".claude/skills/demo/i.html",
            "kit/a.py",
            "kit/sub/b.js",
            "bin/tool.py",
        ]
    )


def test_files_that_git_ignores_are_not_scanned(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    _write_tree(
        tmp_path,
        {
            ".gitignore": "generated/\n",
            ".claude/skills/demo/generated/x.css": "a { color: #1E90FF }\n",
            ".claude/skills/demo/y.css": "a { color: #1E90FF }\n",
        },
    )
    assert [line.split(":")[0] for line in find_problems(tmp_path)] == [".claude/skills/demo/y.css"]


# --- the rule, applied to this repo ---------------------------------------------------------------


def test_the_failure_message_tells_the_author_what_to_do():
    message = failure_message([".claude/skills/demo/a.css:1  #1E90FF"])
    assert "creator's tokens" in message
    assert "rename the id" in message
    assert ".claude/skills/demo/a.css:1  #1E90FF" in message


def test_no_hardcoded_brand():
    problems = find_problems()
    assert problems == [], failure_message(problems)


def test_the_scan_covers_the_kit_package():
    names = {path.relative_to(ROOT).as_posix() for path in scanned_files()}
    assert {"kit/config.py", "kit/brand.py"} <= names


def test_the_allowlist_has_no_stale_or_unexplained_entries():
    for path, reason in NEUTRAL_FALLBACKS.items():
        assert (ROOT / path).is_file(), f"{path} is on the allowlist but does not exist"
        assert reason.strip(), f"{path} is on the allowlist with no reason"


def test_the_diagrams_in_the_docs_use_greys_only():
    """The docs show layouts to every creator, so they carry no brand colour either: greys only."""
    diagrams = sorted((ROOT / "docs").rglob("*.svg"))
    assert ROOT / "docs" / "media" / "ui-story-layouts.svg" in diagrams
    for path in diagrams:
        found = chromatic_colours(path.read_text(encoding="utf-8"))
        assert found == [], f"{path.relative_to(ROOT).as_posix()} has a chromatic colour: {found}"
