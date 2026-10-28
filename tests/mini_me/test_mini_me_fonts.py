"""A creator's own font file with a space and an accented letter in its name reaches both styles' projects.

kit.brand.tokens_css percent-encodes every font URL, so a project builder that copies the fonts a stylesheet
names has to decode the URL first: kit.brand.url_file does that, and both styles use it.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path
from urllib.parse import quote

import pytest
from mini_me_helpers import ROOT, load

from kit import brand, config

install = load("install")
NAME = "Mon Écran Display.ttf"  # a space and an accented capital E


def scaffold_module():
    path = ROOT / ".claude" / "skills" / "ui-story" / "kit" / "scaffold.py"
    spec = importlib.util.spec_from_file_location("uistory_scaffold_for_fonts", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def own_font(example_me):
    """The example creator with a display font of their own, saved under a name with a space and an accent."""
    fonts = example_me / "brand" / "fonts"
    fonts.mkdir(parents=True)
    shutil.copy2(ROOT / "kit" / "fonts" / "inter-tight" / "InterTight-Variable.ttf", fonts / NAME)
    profile = json.loads((example_me / "profile.json").read_text())
    profile["fonts"]["display"] = f"brand/fonts/{NAME}"
    (example_me / "profile.json").write_text(json.dumps(profile))
    return fonts / NAME


def test_url_file_decodes_what_tokens_css_encodes(own_font, tmp_path):
    css_dir = tmp_path / "project"
    css_dir.mkdir()
    css = brand.tokens_css(config.load_profile(), css_dir)
    url = next(part.split('"')[1] for part in css.split("url(")[1:] if "Display" in part)
    assert "%20" in url and "%C3%89" in url, url
    assert brand.url_file(url, css_dir) == own_font.resolve()


@pytest.mark.parametrize("style", ["mini-me", "ui-story"])
def test_both_styles_copy_the_font_and_point_at_the_copy(own_font, tmp_path, style):
    hf = tmp_path / style
    hf.mkdir()
    builder = install.project_tokens if style == "mini-me" else scaffold_module().project_tokens
    css = builder(hf)
    copied = hf / "fonts" / NAME
    assert copied.is_file() and copied.read_bytes() == own_font.read_bytes()
    assert f'url("fonts/{quote(NAME)}")' in css
    assert brand.url_file(f"fonts/{quote(NAME)}", hf) == copied.resolve()


def test_the_browser_loads_the_copied_font(own_font, tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    hf = tmp_path / "project"
    hf.mkdir()
    (hf / "tokens.css").write_text(install.project_tokens(hf))
    (hf / "page.html").write_text('<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="tokens.css">'
                                  '</head><body style="font-family: var(--font-display)">FRESH BASIL</body></html>')
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch(args=["--allow-file-access-from-files"])
            page = browser.new_page()
            page.goto((hf / "page.html").as_uri())
            family = page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--font-display')")
            loaded = page.evaluate(f"document.fonts.load('900 40px ' + {json.dumps(family.split(',')[0].strip())})"
                                   ".then(found => found.length)")
            browser.close()
    except Exception as problem:  # noqa: BLE001
        if "Executable doesn't exist" in str(problem):
            pytest.skip("no browser for the page check")
        raise
    assert loaded == 1, "the font file was found through its encoded URL"
