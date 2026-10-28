"""kit.brand: contrast maths, readable text colours, the bundled fonts and the tokens file."""

import dataclasses
import hashlib
import json
import re
import struct
from pathlib import Path
from urllib.parse import unquote

import pytest

from kit import brand, config
from kit.config import ProfileError

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "me.example"
FONTS = ROOT / "kit" / "fonts"
EM_DASH, EN_DASH = chr(0x2014), chr(0x2013)  # written as code points so this file holds no dash

PINNED_PROPERTIES = [
    "canvas",
    "ink",
    "accent",
    "on-accent",
    "field-dark",
    "field-light",
    "on-field-dark",
    "on-field-light",
    "grid",
    "font-display",
    "font-body",
    "font-mono",
]


def _with(profile, **colours):
    return dataclasses.replace(profile, colours=dataclasses.replace(profile.colours, **colours))


def _custom_properties(css: str) -> dict[str, str]:
    block = re.search(r":root\s*\{(.*?)\}", css, re.S).group(1)
    return dict(re.findall(r"--([a-z-]+):\s*([^;]+);", block))


def _wcag(a: str, b: str) -> float:
    """An independent WCAG 2 contrast ratio, so the tests do not lean on the code they check."""

    def lum(colour):
        value = colour.lstrip("#")
        if len(value) == 3:
            value = "".join(ch * 2 for ch in value)
        parts = [int(value[i : i + 2], 16) / 255 for i in (0, 2, 4)]
        lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in parts]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

    hi, lo = sorted([lum(a), lum(b)], reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# --- contrast -----------------------------------------------------------------------------


def test_contrast_known_values():
    assert brand.contrast("#000000", "#ffffff") == pytest.approx(21.0)
    assert brand.contrast("#ffffff", "#000000") == pytest.approx(21.0)  # order does not matter
    assert brand.contrast("#ffffff", "#ffffff") == pytest.approx(1.0)
    assert brand.contrast("#767676", "#ffffff") == pytest.approx(4.54, abs=0.01)
    assert brand.contrast("#777777", "#ffffff") == pytest.approx(4.48, abs=0.01)
    assert brand.contrast("#FFEB3B", "#FFFFFF") == pytest.approx(1.22, abs=0.01)


def test_contrast_reads_short_and_mixed_case_hex():
    assert brand.contrast("#fff", "#000") == pytest.approx(21.0)
    assert brand.contrast("#ABC", "#aabbcc") == pytest.approx(1.0)


@pytest.mark.parametrize("bad", ["blue", "#12345", "#ggg", "", "ffffff", "#ffffffff"])
def test_contrast_refuses_things_that_are_not_hex_colours(bad):
    with pytest.raises(ValueError) as caught:
        brand.contrast(bad, "#ffffff")
    assert "hex colour" in str(caught.value)


@pytest.mark.parametrize(
    "surface,ink",
    [
        ("#ffeb3b", "#101820"),
        ("#16302b", "#101820"),
        ("#777777", "#101820"),
        ("#e0e0e0", "#202020"),
        ("#0a0a0a", "#303030"),
        ("#c04a28", "#1a1a1a"),
    ],
)
def test_text_on_picks_whichever_of_ink_and_white_reads_better(surface, ink):
    chosen = brand.text_on(surface, ink)
    other = "#ffffff" if chosen == ink else ink
    assert _wcag(chosen, surface) >= _wcag(other, surface)


def test_a_tie_goes_to_the_ink():
    # White ink against any surface ties with white itself. The ink is returned as given.
    assert brand.text_on("#888888", "#FFFFFF") == "#FFFFFF"


def test_grid_is_the_ink_at_six_percent_over_the_canvas():
    # Worked by hand: 0.94 * 255 = 239.7, which rounds to 240 (0xf0).
    assert brand.grid_colour("#000000", "#ffffff") == "#f0f0f0"
    # R: 0.06*16 + 0.94*255 = 240.66 -> 241.  G: 0.06*24 + 239.7 = 241.14 -> 241.  B: 0.06*32 + 239.7 = 241.62 -> 242.
    assert brand.grid_colour("#101820", "#ffffff") == "#f1f1f2"
    assert brand.grid_colour("#ffffff", "#ffffff") == "#ffffff"


# --- check() -----------------------------------------------------------------------------------


def test_check_is_clean_for_the_example(example_me):
    assert brand.check(config.load_profile()) == []


def test_low_contrast_accent(example_me):
    profile = _with(config.load_profile(), accent="#FFEB3B", canvas="#FFFFFF")
    tokens = brand.colour_tokens(profile)
    # White on yellow is worse than the ink on yellow, so the ink is the text colour on the accent.
    assert tokens["on-accent"] == profile.colours.ink
    warnings = brand.check(profile)
    assert len(warnings) == 1
    warning = warnings[0]
    assert "accent" in warning and "canvas" in warning
    assert "#FFEB3B" in warning and "#FFFFFF" in warning
    assert "1.2:1" in warning
    assert "\n" not in warning and warning.endswith(".")
    assert EM_DASH not in warning and EN_DASH not in warning


def test_every_warning_is_one_plain_sentence(example_me):
    # Poor on all five pairs at once: a mid grey accent and two mid grey fields, and a pale ink on a white canvas.
    poor = {"accent": "#777777", "ink": "#bbbbbb", "canvas": "#ffffff", "field_dark": "#777777", "field_light": "#787878"}
    profile = _with(config.load_profile(), **poor)
    warnings = brand.check(profile)
    assert len(warnings) == 5
    assert [w.split(" has ")[0].split(" is ")[0] for w in warnings] == [
        "The accent #777777 on the canvas #ffffff",
        "The best text colour on the accent #777777",
        "Your ink #bbbbbb on the canvas #ffffff",
        "The best text colour on the dark field #777777",
        "The best text colour on the light field #787878",
    ]
    for warning in warnings:
        assert EM_DASH not in warning and EN_DASH not in warning
        assert "\n" not in warning and warning.endswith(".") and warning.count(". ") == 0
        assert re.search(r"\d\.\d:1", warning), "each warning gives its ratio"


def test_check_passes_just_above_the_threshold_and_warns_just_below(example_me):
    base = config.load_profile()
    assert brand.check(_with(base, accent="#767676", canvas="#ffffff")) == []  # 4.54 : 1
    warnings = brand.check(_with(base, accent="#777777", canvas="#ffffff"))  # 4.48 : 1
    assert any(w.startswith("The accent #777777 on the canvas #ffffff") and "4.4:1" in w for w in warnings)


def test_check_warns_when_the_ink_is_hard_to_read_on_the_canvas(example_me):
    profile = _with(config.load_profile(), ink="#bbbbbb", canvas="#ffffff")
    warnings = brand.check(profile)
    # A pale ink is just as hard to read on the example's light field, so that pair is named too.
    assert [w.split(" has ")[0].split(" is ")[0] for w in warnings] == [
        "Your ink #bbbbbb on the canvas #ffffff",
        "The best text colour on the light field #eaf1e4",
    ]


def test_check_warns_when_no_text_colour_reads_on_the_dark_field(example_me):
    # Mid grey: white reaches 4.48 and the dark ink about 3.5, so neither passes.
    profile = _with(config.load_profile(), field_dark="#777777")
    warnings = brand.check(profile)
    assert len(warnings) == 1
    assert warnings[0].startswith("The best text colour on the dark field #777777")
    assert "#ffffff" in warnings[0]


def test_check_warns_when_no_text_colour_reads_on_the_accent(example_me):
    profile = _with(config.load_profile(), accent="#777777")
    warnings = brand.check(profile)
    assert any(w.startswith("The best text colour on the accent #777777") for w in warnings)


def test_check_warns_when_no_text_colour_reads_on_the_light_field(example_me):
    # Mid grey again: white reaches 4.48 and the dark ink about 3.5, so neither passes.
    profile = _with(config.load_profile(), field_light="#777777")
    warnings = brand.check(profile)
    assert len(warnings) == 1
    assert warnings[0].startswith("The best text colour on the light field #777777 is #ffffff")
    assert "4.4:1" in warnings[0]


def test_a_mid_tone_light_field_gets_white_text(example_me):
    # The ink reaches only 3.2:1 on this grey and white reaches 4.9:1, so white is the text colour and nothing warns.
    profile = _with(config.load_profile(), field_light="#707070")
    assert brand.colour_tokens(profile)["on-field-light"] == "#ffffff"
    assert brand.check(profile) == []


# --- the bundled fonts ----------------------------------------------------------------------------


def _font_files() -> list[Path]:
    return sorted(p for p in FONTS.rglob("*") if p.suffix.lower() in {".ttf", ".otf", ".woff", ".woff2"})


def _fvar_axes(path: Path) -> dict[str, tuple[float, float]]:
    """The variation axes of a TrueType font, as {tag: (minimum, maximum)}."""
    data = path.read_bytes()
    (count,) = struct.unpack(">H", data[4:6])
    for i in range(count):
        tag, _checksum, offset, _length = struct.unpack(">4sIII", data[12 + 16 * i : 28 + 16 * i])
        if tag == b"fvar":
            axes_at, _reserved, axis_count, axis_size = struct.unpack(">HHHH", data[offset + 4 : offset + 12])
            axes = {}
            for n in range(axis_count):
                start = offset + axes_at + n * axis_size
                axis, low, _default, high = struct.unpack(">4sIII", data[start : start + 16])
                axes[axis.decode()] = (low / 65536, high / 65536)
            return axes
    return {}


def test_fonts_have_licence():
    files = _font_files()
    assert len(files) == 3
    for font in files:
        licence = font.parent / "OFL.txt"
        assert licence.is_file(), f"{font.name} has no OFL.txt beside it"
        text = licence.read_text(encoding="utf-8")
        assert "SIL OPEN FONT LICENSE Version 1.1" in text
        assert "Copyright" in text.splitlines()[0]
    digests = [hashlib.sha256(font.read_bytes()).hexdigest() for font in files]
    assert len(set(digests)) == len(digests), "two font files are byte-identical"


def test_each_family_sits_in_its_own_folder():
    assert sorted(font.parent.name for font in _font_files()) == ["inter", "inter-tight", "jetbrains-mono"]
    for folder in FONTS.iterdir():
        if folder.is_dir():
            assert len([f for f in _font_files() if f.parent == folder]) == 1


def test_font_registry_matches_the_files():
    registry = json.loads((FONTS / "fonts.json").read_text(encoding="utf-8"))
    assert registry["source_repository"] == "https://github.com/google/fonts"
    listed = set()
    for family, entry in registry["families"].items():
        path = FONTS / entry["dir"] / entry["file"]
        assert path.is_file(), family
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"], family
        assert entry["source"].startswith(f"https://github.com/google/fonts/blob/{registry['source_commit']}/ofl/")
        listed.add(path)
    assert listed == set(_font_files()), "a font file is missing from fonts.json, or fonts.json lists a missing file"
    assert list(registry["families"]) == ["Inter Tight", "Inter", "JetBrains Mono"]


def test_bundled_fonts_are_variable_and_the_weight_range_is_true():
    for family, entry in config.bundled_fonts().items():
        axes = _fvar_axes(entry["path"])
        assert "wght" in axes, f"{family} has no weight axis"
        low, high = axes["wght"]
        assert entry["weight"] == f"{low:g} {high:g}", family


# --- the tokens file ----------------------------------------------------------------------------------


def test_tokens_exact_names(example_me, tmp_path):
    profile = config.load_profile()
    out = tmp_path / "brand" / "tokens.css"
    brand.write_tokens(profile, out)
    props = _custom_properties(out.read_text(encoding="utf-8"))

    assert list(props) == PINNED_PROPERTIES

    c = profile.colours
    assert props["canvas"] == c.canvas
    assert props["ink"] == c.ink
    assert props["accent"] == c.accent
    assert props["field-dark"] == c.field_dark
    assert props["field-light"] == c.field_light

    def readable(surface):
        return c.ink if _wcag(c.ink, surface) >= _wcag("#ffffff", surface) else "#ffffff"

    assert props["on-accent"] == readable(c.accent)
    assert props["on-field-dark"] == readable(c.field_dark)
    assert props["on-field-light"] == readable(c.field_light)

    def mix(i):  # one channel of the ink at 6% over the canvas
        ink, canvas = int(c.ink[1 + 2 * i : 3 + 2 * i], 16), int(c.canvas[1 + 2 * i : 3 + 2 * i], 16)
        return int(0.06 * ink + 0.94 * canvas + 0.5)

    assert props["grid"] == "#" + "".join(f"{mix(i):02x}" for i in range(3))

    assert props["font-display"] == '"Inter Tight", sans-serif'
    assert props["font-body"] == '"Inter", sans-serif'
    assert props["font-mono"] == '"JetBrains Mono", monospace'


def test_the_tokens_file_holds_only_the_pinned_properties_and_font_rules(example_me):
    css = brand.tokens_css(config.load_profile())
    outside_root = re.sub(r":root\s*\{.*?\}", "", css, flags=re.S)
    outside_faces = re.sub(r"@font-face\s*\{.*?\}", "", outside_root, flags=re.S)
    outside_comments = re.sub(r"/\*.*?\*/", "", outside_faces, flags=re.S)
    assert outside_comments.strip() == ""


def test_the_tokens_file_names_no_machine_paths(example_me):
    css = brand.tokens_css(config.load_profile())
    assert str(ROOT) not in css
    assert str(Path.home()) not in css
    urls = re.findall(r'url\("([^"]+)"\)', css)
    assert urls, "expected at least one font url"
    for url in urls:
        assert not url.startswith("/"), f"{url} is an absolute path"
        assert ":" not in url, f"{url} names a scheme or a drive"


def test_font_face_rules_for_the_bundled_fonts(example_me):
    css = brand.tokens_css(config.load_profile())
    faces = re.findall(r"@font-face\s*\{(.*?)\}", css, re.S)
    assert [re.search(r'font-family:\s*"([^"]+)"', f).group(1) for f in faces] == [
        "Inter Tight",
        "Inter",
        "JetBrains Mono",
    ]
    registry = config.bundled_fonts()
    for face in faces:
        family = re.search(r'font-family:\s*"([^"]+)"', face).group(1)
        assert re.search(rf"font-weight:\s*{re.escape(registry[family]['weight'])};", face)
        assert 'format("truetype")' in face and "font-style: normal;" in face
        url = re.search(r'url\("([^"]+)"\)', face).group(1)
        # The tokens file sits at <root>/me/brand/tokens.css, or <root>/me.example/brand/tokens.css.
        for folder in ("me", "me.example"):
            assert (ROOT / folder / "brand" / unquote(url)).resolve() == registry[family]["path"]


def test_font_urls_can_be_written_for_any_folder(example_me, tmp_path):
    css_dir = tmp_path / "somewhere" / "deeper"
    css_dir.mkdir(parents=True)
    css = brand.tokens_css(config.load_profile(), css_dir)
    urls = re.findall(r'url\("([^"]+)"\)', css)
    assert len(urls) == 3
    for url in urls:
        assert (css_dir / unquote(url)).resolve().is_file()


def test_a_custom_font_gets_its_own_rule(example_me):
    font = example_me / "brand" / "fonts" / "My Font.woff2"
    font.parent.mkdir(parents=True)
    font.write_bytes(b"wOF2")
    data = json.loads((example_me / "profile.json").read_text())
    data["fonts"]["display"] = "brand/fonts/My Font.woff2"
    (example_me / "profile.json").write_text(json.dumps(data))

    css = brand.tokens_css(config.load_profile())
    faces = re.findall(r"@font-face\s*\{(.*?)\}", css, re.S)
    assert len(faces) == 3  # the custom display font, plus the bundled body and mono fonts
    custom = faces[0]
    assert 'font-family: "Custom Display"' in custom
    assert 'format("woff2")' in custom
    url = re.search(r'url\("([^"]+)"\)', custom).group(1)
    assert url == "fonts/My%20Font.woff2"  # relative to me/brand/, with the space encoded
    assert (example_me / "brand" / unquote(url)).read_bytes() == b"wOF2"
    assert _custom_properties(css)["font-display"] == '"Custom Display", sans-serif'
    assert _custom_properties(css)["font-body"] == '"Inter", sans-serif'


def test_two_roles_can_share_one_custom_font_file(example_me):
    font = example_me / "brand" / "fonts" / "Shared.ttf"
    font.parent.mkdir(parents=True)
    font.write_bytes(b"\x00\x01\x00\x00")
    data = json.loads((example_me / "profile.json").read_text())
    data["fonts"]["display"] = data["fonts"]["body"] = "brand/fonts/Shared.ttf"
    (example_me / "profile.json").write_text(json.dumps(data))

    css = brand.tokens_css(config.load_profile())
    assert css.count("Shared.ttf") == 1
    props = _custom_properties(css)
    assert props["font-display"] == props["font-body"]
    assert 'format("truetype")' in css


def test_custom_font_urls_follow_the_folder_the_file_is_written_for(example_me, tmp_path):
    font = example_me / "brand" / "fonts" / "Mine.otf"
    font.parent.mkdir(parents=True)
    font.write_bytes(b"OTTO")
    data = json.loads((example_me / "profile.json").read_text())
    data["fonts"]["mono"] = "brand/fonts/Mine.otf"
    (example_me / "profile.json").write_text(json.dumps(data))

    css_dir = tmp_path / "elsewhere"
    css_dir.mkdir()
    css = brand.tokens_css(config.load_profile(), css_dir)
    for url in re.findall(r'url\("([^"]+)"\)', css):
        assert (css_dir / unquote(url)).resolve().is_file()
    assert 'format("opentype")' in css


def test_a_custom_font_that_is_not_there_is_a_plain_error(example_me):
    profile = config.load_profile()
    broken = dataclasses.replace(profile, fonts=dataclasses.replace(profile.fonts, display="brand/fonts/Nope.woff2"))
    with pytest.raises(ProfileError) as caught:
        brand.tokens_css(broken)
    assert "fonts.display" in str(caught.value)


def test_font_file_finds_bundled_and_custom_fonts(example_me):
    inter_tight = brand.font_file("Inter Tight")
    assert inter_tight.is_file() and inter_tight.parent.name == "inter-tight"
    assert brand.font_file("inter  tight") == inter_tight

    font = example_me / "brand" / "fonts" / "Mine.ttf"
    font.parent.mkdir(parents=True)
    font.write_bytes(b"\x00\x01\x00\x00")
    assert brand.font_file("brand/fonts/Mine.ttf") == font

    with pytest.raises(ProfileError):
        brand.font_file("Comic Sans")


def test_example_tokens_regenerate_identically(tmp_path, monkeypatch):
    monkeypatch.setenv("KIT_ME", str(EXAMPLE))
    profile = config.load_profile()
    committed = (EXAMPLE / "brand" / "tokens.css").read_bytes()

    out = tmp_path / "anywhere" / "tokens.css"  # the folder does not exist yet
    brand.write_tokens(profile, out)
    assert out.read_bytes() == committed

    brand.write_tokens(profile, out)  # writing again changes nothing
    assert out.read_bytes() == committed


def test_write_tokens_says_where_its_font_urls_hold():
    doc = " ".join(brand.write_tokens.__doc__.split())
    assert "me/brand/" in doc
    assert "tokens_css(profile, css_dir)" in doc


def test_tokens_end_with_one_newline_and_use_unix_line_endings(example_me):
    css = brand.tokens_css(config.load_profile())
    assert css.endswith("}\n") and not css.endswith("\n\n")
    assert "\r" not in css


def test_the_example_tokens_use_the_example_profile_values():
    profile = json.loads((EXAMPLE / "profile.json").read_text())
    props = _custom_properties((EXAMPLE / "brand" / "tokens.css").read_text())
    for name, value in profile["colours"].items():
        assert props[name.replace("_", "-")] == value
