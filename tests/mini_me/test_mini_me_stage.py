"""The stage, in a real headless browser: install.py builds the project for a short Reel, and the page is seeked
the way the renderer seeks it.

The Reel is five pages for the example creator, with the stand-in character: a pointing page each way, a stamp,
a counted number and the closing call to action. Nothing here renders a video; the frames are screenshots of the
page at a moment, which is what each rendered frame is.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("PIL")
playwright = pytest.importorskip("playwright.sync_api")

from mini_me_helpers import (  # noqa: E402
    BIG_HEAD, PAGES, ROOT, SECONDS, SPOKEN, STANDARD_CARD, build_project, load, make_character, needs_node,
)
from PIL import Image  # noqa: E402

from kit import brand, config  # noqa: E402

install = load("install")
margin_scan = load("margin_scan")

GSAP = ROOT / "kit" / "vendor" / "gsap.min.js"
pytestmark = pytest.mark.skipif(not GSAP.is_file(), reason="needs GSAP: bash bin/fetch_gsap.sh")

class Page:
    """One loaded composition, seeked the way the renderer seeks it."""

    def __init__(self, browser, hf: Path):
        self.errors: list[str] = []
        self.page = browser.new_page(viewport={"width": 1080, "height": 1920})
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        self.page.goto((hf / "index.html").as_uri())
        # a GSAP timeline is a thenable, so the check returns a plain true rather than the timeline itself
        self.page.wait_for_function("!!(window.Stage && window.Stage.ready && window.__timelines && window.__timelines.main)",
                                    timeout=30000)

    def seek(self, t: float):
        # seek() returns the timeline, a thenable: return a plain value so the browser does not wait on it
        self.page.evaluate(f"(() => {{ window.__timelines.main.seek({t}, false); return true; }})()")

    def js(self, script: str):
        return self.page.evaluate(script)

    def shot(self, t: float, out: Path) -> Path:
        self.seek(t)
        self.page.screenshot(path=str(out))
        return out


def launch(p):
    try:
        return p.chromium.launch(args=["--allow-file-access-from-files"])
    except Exception:
        try:
            return p.chromium.launch(channel="chrome", args=["--allow-file-access-from-files"])
        except Exception as problem:  # noqa: BLE001
            pytest.skip(f"no browser for the page checks: {problem}")


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        started = launch(p)
        yield started
        started.close()


def project(tmp_path_factory, browser, build):
    base = tmp_path_factory.mktemp(build)
    root, me = base / "kit", base / "me"
    shutil.copytree(ROOT / "me.example", me)
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("KIT_ME", str(me))
        patch.setattr(config, "kit_root", lambda: root)
        hf = build_project(root, me, build=build)
        profile = config.load_profile()
        tokens = brand.colour_tokens(profile)
    return {"page": Page(browser, hf), "hf": hf, "profile": profile, "tokens": tokens, "dir": base}


@pytest.fixture(scope="module")
def stage(tmp_path_factory, browser):
    built = project(tmp_path_factory, browser, "standard")
    yield built
    built["page"].page.close()


@pytest.fixture(scope="module")
def big_stage(tmp_path_factory, browser):
    built = project(tmp_path_factory, browser, "big_head")
    yield built
    built["page"].page.close()


def rgb(colour: str) -> tuple[int, int, int]:
    colour = colour.lstrip("#")
    return tuple(int(colour[i : i + 2], 16) for i in (0, 2, 4))


def near(a, b, tolerance=10) -> bool:
    return all(abs(int(x) - int(y)) <= tolerance for x, y in zip(a, b))


# --- the build ---------------------------------------------------------------------------------------------------------


def test_the_page_builds_without_errors(stage):
    page = stage["page"]
    assert page.errors == []
    report = page.js("Stage.audit()")
    assert [p for p in report["problems"] if not p.startswith("note:")] == []
    assert len(report["pages"]) == len(PAGES)


def test_no_caption_track(stage):
    page, hf = stage["page"], stage["hf"]
    found = page.js("""Array.from(document.querySelectorAll('*')).filter(e =>
        /caption|karaoke|capword|subtitle/i.test((e.id || '') + ' ' + (typeof e.className === 'string' ? e.className : ''))).length""")
    assert found == 0
    index = (hf / "index.html").read_text()
    assert "class=\"clip\"" not in index and "karaoke" not in index and "caption" not in index.lower()
    assert not any(name for name in (p.name for p in hf.rglob("*")) if "caption" in name.lower() or "karaoke" in name)
    data = (hf / "lib" / "data.js").read_text()
    assert "caption" not in data.lower()


def test_cta_card_from_profile(stage):
    page, profile = stage["page"], stage["profile"]
    cta = PAGES[-1]["t"]
    page.seek(cta + 0.5)
    card = page.js("""(() => { const c = document.querySelector('.mm-cta');
        return c ? {name: c.querySelector('.nm').textContent, handle: c.querySelector('.hd').textContent,
                    shown: c.closest('.mm-sc').style.display} : null; })()""")
    assert card == {"name": profile.name, "handle": profile.handles.instagram, "shown": "block"}
    assert card["name"] == "Example Studio" and card["handle"] == "@examplestudio"
    # the example creator asks for comments: the keyword is typed in the comment box after it is said
    page.seek(SECONDS - 0.1)
    assert page.js("document.querySelector('.mm-cta .typed').textContent") == profile.cta.keyword


def test_a_follow_cta_presses_follow(tmp_path, monkeypatch, browser):
    root, me = tmp_path / "kit", tmp_path / "me"
    shutil.copytree(ROOT / "me.example", me)
    data = json.loads((me / "profile.json").read_text())
    data["cta"] = {"type": "follow", "keyword": None, "payload": None, "comment_platforms": []}
    (me / "profile.json").write_text(json.dumps(data))
    monkeypatch.setenv("KIT_ME", str(me))
    monkeypatch.setattr(config, "kit_root", lambda: root)
    hf = build_project(root, me, spoken=SPOKEN.replace("Comment GROW and I'll send you the checklist.",
                                                        "Follow for more balcony tips."))
    follow = next(w for w in json.loads((root / "work" / "example-reel" / "words.json").read_text())["words"]
                  if w["w"] == "Follow")
    page = Page(browser, hf)
    label = ("(() => { const b = document.querySelector('.mm-cta .btn'); "
             "return [b.querySelector('.a').style.display, b.querySelector('.b').style.display]; })()")
    page.seek(follow["s"] - 0.05)
    assert page.js(label) == ["inline", "none"], "Follow before the word"
    page.seek(follow["s"] + 0.4)
    assert page.js(label) == ["none", "inline"], "pressed into Following on the word"
    assert page.js("document.querySelector('.mm-cta .hd').textContent") == "@examplestudio"
    page.page.close()


# --- the look ----------------------------------------------------------------------------------------------------------


def visible_scene(page) -> dict:
    return page.js("""(() => { const s = Array.from(document.querySelectorAll('.mm-sc')).filter(e => e.style.display === 'block');
        return s.map(e => ({id: e.id, field: e.classList.contains('light') ? 'light' : 'dark',
                            filter: (e.querySelector('.mm-ch') || {}).style ? e.querySelector('.mm-ch').style.filter : ''})); })()""")


def test_fields_flip_on_every_page(stage):
    page = stage["page"]
    ends = [p["t"] for p in PAGES[1:]] + [SECONDS]
    for n, (spec, end) in enumerate(zip(PAGES, ends)):
        page.seek((spec["t"] + end) / 2)
        shown = visible_scene(page)
        assert len(shown) == 1 and shown[0]["id"] == f"mm-p{n + 1}", shown
        assert shown[0]["field"] == spec["field"]
        assert f"mm-stk-{spec['field']}" in shown[0]["filter"], "the outline matches the field"


STANDARD = {"top": 60, "neck": 113, "waist": 197, "mouth": [116, 99], "eyes": [107, 85]}  # the standard stand-in


def framing(built, body):
    """Each page shows the character down to its waist; the close puts its mouth well inside the frame."""
    info = built["page"].js("Stage.audit()")
    assert [p for p in info["problems"] if not p.startswith("note:")] == []
    for spec, page in zip(PAGES, info["pages"]):
        char = page["char"]
        if spec["prop"] == "cta":
            assert 10 <= char["scale"] <= 12, char
            mouth = char["crown"] + (body["mouth"][1] + 4 - body["top"]) * char["scale"]
            assert 1300 <= mouth <= 1700 and char["mouth"] == mouth, (mouth, char)
            card = built["page"].js("document.querySelector('.mm-cta').offsetTop + document.querySelector('.mm-cta').offsetHeight")
            assert char["crown"] + (body["eyes"][1] - body["top"]) * char["scale"] >= card + 30, "the eyes show under the card"
            continue
        assert 6 <= char["scale"] <= 8 and 960 <= char["crown"] <= 1320, char
        bottom = body["top"] + (1920 - char["crown"]) / char["scale"]  # the art row at the bottom of the frame
        assert bottom >= body["waist"] - 2, f"cropped at row {bottom:.0f}, above the waist ({body['waist']})"
        assert bottom - body["neck"] >= 40, "more than the head and a sliver of shoulder is in view"


def test_the_character_is_big_and_cropped_at_the_waist(stage):
    framing(stage, STANDARD)


def test_a_big_headed_character_is_framed_to_its_waist_too(big_stage):
    """A chibi's waist is far below halfway: the frame still reaches it, and the close keeps the mouth in view."""
    framing(big_stage, BIG_HEAD)
    figure = big_stage["page"].js("window.MM_DATA.figure")
    assert abs(figure["waist"] - BIG_HEAD["waist"]) <= 2 and abs(figure["neck"] - BIG_HEAD["neck"]) <= 3, figure
    assert figure["waist_measured"] is True


def test_headlines_fit_the_safe_area_and_the_first_shows_on_frame_0(stage):
    page = stage["page"]
    for info in page.js("Stage.audit()")["pages"]:
        assert all(90 <= line["left"] and line["right"] <= 990 for line in info["lines"]), info["lines"]
        assert info["headBottom"] <= 632
    page.seek(0)
    assert page.js("getComputedStyle(document.querySelector('#mm-p1 .mm-ln')).opacity") == "1"
    weight = page.js("getComputedStyle(document.querySelector('#mm-p1 .mm-ln')).fontWeight")
    assert weight == "900", "the heaviest weight of the display font"
    family = page.js("getComputedStyle(document.querySelector('#mm-p1 .mm-ln')).fontFamily")
    assert stage["profile"].fonts.display in family
    transform = page.js("getComputedStyle(document.querySelector('#mm-p1 .mm-ln')).textTransform")
    assert transform == "uppercase"


def test_fields_and_the_sticker_outline_use_the_tokens(stage, tmp_path):
    page, tokens = stage["page"], stage["tokens"]
    for n, spec in enumerate(PAGES[:2]):
        out = page.shot(spec["t"] + 0.9, tmp_path / f"p{n}.png")
        frame = np.asarray(Image.open(out).convert("RGB"))
        field = rgb(tokens["field-light" if spec["field"] == "light" else "field-dark"])
        assert near(frame[1800, 20], field, 4), (tuple(frame[1800, 20]), field)
        char = page.js(f"Stage.audit().pages[{n}].char")
        x, crown = char["x"], char["crown"]
        assert near(frame[crown - 6, x], (255, 255, 255), 6), "a white ring around the character"
        if spec["field"] == "light":
            assert near(frame[crown - 13, x], rgb(tokens["ink"]), 12), "an ink edge outside the ring on the light field"
        else:
            assert near(frame[crown - 13, x], field, 6), "no ink edge on the dark field"
        assert near(frame[crown - 24, x], field, 6)


def test_margin_scan_inside_90_990(stage, tmp_path):
    page = stage["page"]
    shots = []
    ends = [p["t"] for p in PAGES[1:]] + [SECONDS]
    for n, (spec, end) in enumerate(zip(PAGES, ends)):
        for k, t in enumerate((spec["t"] + 0.05, (spec["t"] + end) / 2, end - 0.05)):
            shots.append(page.shot(t, tmp_path / f"p{n}-{k}.png"))
    assert margin_scan.main([str(p) for p in shots]) == 0
    # the scan is not blind: a block that crosses the margin is found
    frame = np.asarray(Image.open(shots[1]).convert("RGB")).copy()
    frame[900:960, 60:200] = (255, 255, 255)
    bad = tmp_path / "bad.png"
    Image.fromarray(frame).save(bad)
    assert margin_scan.main([str(bad)]) == 1


def test_hits_mark_every_flip_and_every_stamp(stage):
    hits = stage["page"].js("Stage.hits")
    whooshes = [h["t"] for h in hits if h["kind"] == "whoosh"]
    assert whooshes == [p["t"] for p in PAGES[1:]], "a whoosh on each field flip"
    thuds = [h["t"] for h in hits if h["kind"] == "thud"]
    assert len(thuds) == 2, "the stamp and the press on the closing card"


def test_install_copies_only_the_packed_character(stage):
    hf = stage["hf"]
    names = sorted(p.relative_to(hf / "character").as_posix() for p in (hf / "character").rglob("*") if p.is_file())
    assert "character.js" in names and "manifest.js" in names
    assert all(n.startswith(("strips/", "faces/")) or n in ("character.js", "manifest.js") for n in names)
    assert (hf / "fonts").is_dir() and any((hf / "fonts").iterdir()), "fonts travel with the project"
    tokens = (hf / "tokens.css").read_text()
    assert 'url("fonts/' in tokens and "../" not in tokens


def test_scenes_js_is_never_overwritten(stage, monkeypatch):
    hf = stage["hf"]
    scenes = hf / "lib" / "scenes.js"
    scenes.write_text("/* this Reel's own scenes */\n")
    me = stage["dir"] / "me"
    monkeypatch.setenv("KIT_ME", str(me))
    monkeypatch.setattr(config, "kit_root", lambda: stage["dir"] / "kit")
    install.install("example-reel")
    assert scenes.read_text() == "/* this Reel's own scenes */\n"


def test_install_explains_what_is_missing(kit_home, example_me):
    folder = config.work_dir("example-reel")
    folder.mkdir(parents=True)
    assert install.main(["example-reel"]) == 1
    (folder / "vo.m4a").write_bytes(b"")
    with pytest.raises(config.KitError, match="words.json is missing"):
        install.install("example-reel")


@needs_node
def test_the_stand_in_character_is_the_shape_the_runtime_reads(tmp_path):
    manifest = make_character(tmp_path / "character")
    done = subprocess.run(["node", str(ROOT / ".claude" / "skills" / "mini-me" / "kit" / "character" / "test_state.mjs"),
                           str(manifest)], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and "all passed" in done.stdout, done.stdout + done.stderr


def test_every_page_keeps_moving(stage):
    """A page with a held pose and a finished prop would freeze: the layers drift up the whole time instead."""
    page = stage["page"]
    ends = [p["t"] for p in PAGES[1:]] + [SECONDS]
    for n, (spec, end) in enumerate(zip(PAGES, ends)):
        where = []
        for t in (spec["t"] + 0.4, end - 0.1):
            page.seek(t)
            where.append(page.js(f"getComputedStyle(document.querySelector('#mm-p{n + 1} .mm-hl')).transform"))
        assert where[0] != where[1], f"page {n + 1} holds still"


def test_the_duration_never_reaches_into_an_extra_frame():
    for seconds in (18.582, 23.4567, 10.0, 7.99999):
        frames = int(seconds * 60 + 1e-6)
        written = float(install.written(install.frame_duration(seconds)))
        assert frames - 0.01 < written * 60 <= frames, (seconds, written)


def test_the_cursor_presses_the_button_it_should(stage):
    """On a comment close the cursor lands on Post; its tip sits 19, 9 px into its box."""
    page = stage["page"]
    page.seek(SECONDS - 0.1)
    where = page.js("""(() => { const post = document.querySelector('.mm-cta .post').getBoundingClientRect();
        const cur = document.querySelector('.mm-cursor').getBoundingClientRect();
        return {tip: [cur.left + 19, cur.top + 9], post: [post.left, post.right, post.top, post.bottom]}; })()""")
    (x, y), (left, right, top, bottom) = where["tip"], where["post"]
    assert left - 4 <= x <= right + 4 and top - 4 <= y <= bottom + 4, where


@pytest.fixture(scope="module")
def wide_stage(tmp_path_factory, browser):
    built = project(tmp_path_factory, browser, "wide")
    yield built
    built["page"].page.close()


def test_a_close_that_cannot_reach_scale_10_says_why(wide_stage):
    """A character whose arms are always out is too wide for a close-up: the close shrinks and says so."""
    info = wide_stage["page"].js("Stage.audit()")
    close = info["pages"][-1]["char"]
    assert close["scale"] < 10
    notes = [p for p in info["problems"] if "the close uses scale" in p]
    assert len(notes) == 1 and f"scale {close['scale']}" in notes[0] and "safe lines at the sides" in notes[0], notes


def test_an_unmeasured_waist_says_to_check_the_crop(wide_stage, stage):
    """With no hands at its sides the waist is a guess: the audit says so, and only then."""
    assert wide_stage["page"].js("window.MM_DATA.figure.waist_measured") is False
    notes = [p for p in wide_stage["page"].js("Stage.audit()")["problems"] if "waist" in p]
    assert len(notes) == 1 and notes[0].startswith("note:") and "check the frames" in notes[0], notes
    assert not [p for p in stage["page"].js("Stage.audit()")["problems"] if "waist" in p]


def test_a_single_digit_count_shows_whole(stage):
    """"3 OF 5" never passes through 1 or 2 and keeps its 5: a number under 20 does not count."""
    page, start = stage["page"], PAGES[3]["t"]
    for k in range(-2, 54):  # every frame from just before the page to 0.9 s into it
        page.seek(round(start + k / 60, 4))
        assert page.js("Array.from(document.querySelectorAll('#mm-p4 .mm-num')).map(e => e.textContent)") == ["3", "5"], k
    notes = [p for p in page.js("Stage.audit()")["problems"] if "page 4" in p and "under 20" in p]
    assert len(notes) == 1, page.js("Stage.audit()")["problems"]


def test_a_count_from_the_scenes_never_shows_a_single_digit(stage):
    """Stage.A.count, as scenes.js calls it: a number under 20 stays whole, 20 or more counts up from 10."""
    page = stage["page"]
    page.js("""(() => { const box = document.createElement('div'); box.style.display = 'none'; document.body.appendChild(box);
        for (const [id, to] of [['mm-test-8', '8'], ['mm-test-21', '21']]) {
          const n = document.createElement('span'); n.id = id; n.setAttribute('data-to', to); n.textContent = to; box.appendChild(n);
          Stage.A.count(n, 1.0, 0.6); }
        return true; })()""")
    seen8, seen21 = set(), []
    for k in range(-6, 48):
        page.seek(round(1.0 + k / 60, 4))
        seen8.add(page.js("document.getElementById('mm-test-8').textContent"))
        seen21.append(int(page.js("document.getElementById('mm-test-21').textContent")))
    assert seen8 == {"8"}
    assert min(seen21) >= 10 and seen21[-1] == 21 and seen21 == sorted(seen21), seen21


COUNT_PAGES = [
    {"t": 0.0, "text": "BASIL *DIES*", "pose": "point_up", "field": "dark", "prop": "card: Week three"},
    {"t": 1.6, "text": "21 DAYS", "pose": "thumbs_up", "field": "light", "prop": "count"},
    {"t": 3.4, "text": "COMMENT GROW", "pose": "point_up", "field": "dark", "prop": "cta"},
]
COUNT_SPOKEN = "Basil dies in week three. It roots in 21 days. Comment GROW and I'll send you the checklist."


def test_a_page_counts_a_number_of_20_or_more_up_from_10_and_lands_on_it(tmp_path, monkeypatch, browser):
    """The page's own count prop, the way beats.py drafts it: 21 rolls up from 10 and lands on 21 within the
    page's first 0.7 s, never showing a single digit, and no note says it was left whole."""
    root, me = tmp_path / "kit", tmp_path / "me"
    shutil.copytree(ROOT / "me.example", me)
    monkeypatch.setenv("KIT_ME", str(me))
    monkeypatch.setattr(config, "kit_root", lambda: root)
    page = Page(browser, build_project(root, me, spoken=COUNT_SPOKEN, pages=COUNT_PAGES))
    assert page.errors == []
    start, seen = COUNT_PAGES[1]["t"], []
    for k in range(0, 54):  # every frame from the page's start to 0.9 s into it
        page.seek(round(start + k / 60, 4))
        seen.append(page.js("document.querySelector('#mm-p2 .mm-num').textContent"))
    values = [int(v) for v in seen]
    assert values[0] == 10 and values[-1] == 21, seen
    assert min(values) >= 10 and values == sorted(values) and len(set(values)) >= 5, seen
    assert values.index(21) <= 45, "it lands on its number within about 0.7 s"
    notes = [p for p in page.js("Stage.audit()")["problems"] if "page 2" in p]
    assert notes == [], notes
    page.page.close()


def test_a_count_in_the_teens_shows_whole(stage):
    """Counting 12 up from 10 would show 10 and 11 under the words first, so a number under 20 stays whole."""
    page = stage["page"]
    left_whole = page.js("""(() => { const box = document.createElement('div'); box.style.display = 'none'; document.body.appendChild(box);
        const n = document.createElement('span'); n.id = 'mm-test-12'; n.setAttribute('data-to', '12'); n.textContent = '12'; box.appendChild(n);
        return !Stage.A.count(n, 1.0, 0.6); })()""")
    assert left_whole is True
    seen = set()
    for k in range(-6, 48):
        page.seek(round(1.0 + k / 60, 4))
        seen.add(page.js("document.getElementById('mm-test-12').textContent"))
    assert seen == {"12"}, seen


# --- the card the character holds up --------------------------------------------------------------------------------

HELD_PAGES = [
    {"t": 0.0, "text": "BASIL *DIES*", "pose": "point_up", "field": "dark", "prop": "card: Week three"},
    {"t": 1.6, "text": "THE PLAN", "pose": "hold_card", "field": "light", "prop": "card: Water early, then move it"},
    {"t": 3.4, "text": "COMMENT GROW", "pose": "point_up", "field": "dark", "prop": "cta"},
]
HELD_SPOKEN = "Basil dies in week three. Here is the plan. Comment GROW and I'll send you the checklist."


def held_project(tmp_path, monkeypatch, browser, build="standard", pages=HELD_PAGES):
    root, me = tmp_path / "kit", tmp_path / "me"
    shutil.copytree(ROOT / "me.example", me)
    monkeypatch.setenv("KIT_ME", str(me))
    monkeypatch.setattr(config, "kit_root", lambda: root)
    hf = build_project(root, me, spoken=HELD_SPOKEN, build=build, pages=pages)
    return Page(browser, hf), brand.colour_tokens(config.load_profile())


def test_a_card_on_a_hold_card_page_is_written_on_the_card_the_character_holds(tmp_path, monkeypatch, browser):
    """The hold_card pose holds up a plain card, measured when the character was packed. The page's card words go
    on that card, fitted to its clear face at a readable size, instead of a second card dropping in beside it."""
    page, tokens = held_project(tmp_path, monkeypatch, browser)
    assert page.errors == []
    info = page.js("Stage.audit()")
    assert [p for p in info["problems"] if not p.startswith("note:")] == []
    held, char = info["pages"][1]["held"], info["pages"][1]["char"]
    assert held and held["w"] >= 200 and held["h"] >= 150, held
    sc = char["scale"]  # the card's box on screen is the manifest's box at the character's scale and place
    cx, cy, cw, ch = STANDARD_CARD
    assert (held["left"], held["top"]) == (char["x"] + (cx - 122) * sc, char["y"] + (cy - 280) * sc), (held, char)
    assert (held["w"], held["h"]) == (cw * sc, ch * sc)
    page.seek(2.6)
    shown = page.js("""(() => { const h = document.querySelector('#mm-p2 .mm-held'); if (!h) return null;
        const o = h.getBoundingClientRect();
        return {text: h.textContent, size: parseFloat(getComputedStyle(h.querySelector('span')).fontSize),
                left: o.left, top: o.top, right: o.right, bottom: o.bottom}; })()""")
    assert shown and shown["text"] == "Water early, then move it", shown
    assert shown["size"] >= 26, shown
    assert held["left"] <= shown["left"] and shown["right"] <= held["left"] + held["w"] + 1, (held, shown)
    assert held["top"] <= shown["top"] and shown["bottom"] <= held["top"] + held["h"] + 1, (held, shown)
    assert page.js("document.querySelectorAll('#mm-p2 .mm-card').length") == 0, "a second card dropped in"
    frame = np.asarray(Image.open(page.shot(2.6, tmp_path / "held.png")).convert("RGB"))
    x0, y0 = held["left"], held["top"]
    assert near(frame[y0 + 4, x0 + 4], (255, 255, 255), 6), "the card's face shows round the words"
    inside = frame[y0 : y0 + held["h"], x0 : x0 + held["w"]].reshape(-1, 3).astype(int)
    assert (np.abs(inside - np.array(rgb(tokens["ink"]))).max(1) <= 12).any(), "no ink words on the card"
    page.page.close()


def test_install_refuses_a_hold_card_page_with_a_card_when_no_card_was_measured(tmp_path, monkeypatch):
    """A character packed with no plain card in its hold_card drawing: a page that pairs hold_card with a card is
    refused before the project is written, with what to do."""
    root, me = tmp_path / "kit", tmp_path / "me"
    shutil.copytree(ROOT / "me.example", me)
    monkeypatch.setenv("KIT_ME", str(me))
    monkeypatch.setattr(config, "kit_root", lambda: root)
    with pytest.raises(config.KitError, match="hold_card") as caught:
        build_project(root, me, spoken=HELD_SPOKEN, build="big_head", pages=HELD_PAGES)
    assert "draw hold_card again" in str(caught.value)


def test_the_stage_fails_a_hold_card_page_with_a_card_when_no_card_was_measured(tmp_path, monkeypatch, browser):
    """The same pairing made in scenes.js (a pose override) fails the page check, and the card drops in."""
    pages = [dict(p) for p in HELD_PAGES]
    pages[1]["pose"] = "point_left"
    root, me = tmp_path / "kit", tmp_path / "me"
    shutil.copytree(ROOT / "me.example", me)
    monkeypatch.setenv("KIT_ME", str(me))
    monkeypatch.setattr(config, "kit_root", lambda: root)
    hf = build_project(root, me, spoken=HELD_SPOKEN, build="big_head", pages=pages)
    (hf / "lib" / "scenes.js").write_text('Stage.page(2, { char: { pose: "hold_card" } });\n')
    page = Page(browser, hf)
    info = page.js("Stage.audit()")
    assert info["pages"][1]["pose"] == "hold_card" and info["pages"][1]["held"] is None
    failures = [p for p in info["problems"] if not p.startswith("note:")]
    assert len(failures) == 1 and failures[0].startswith("page 2") and "hold_card" in failures[0], info["problems"]
    assert "draw hold_card again" in failures[0]
    assert page.js("document.querySelectorAll('#mm-p2 .mm-card').length") == 1
    page.page.close()


def test_words_too_long_for_the_held_card_fall_back_to_a_dropped_card(tmp_path, monkeypatch, browser):
    """Words that cannot fit the held card at 26 px are not clipped: the card drops in instead, with a note."""
    pages = [dict(p) for p in HELD_PAGES]
    pages[1]["prop"] = "card: " + " ".join(["water"] * 60)
    page, _ = held_project(tmp_path, monkeypatch, browser, pages=pages)
    info = page.js("Stage.audit()")
    assert page.js("document.querySelectorAll('#mm-p2 .mm-held').length") == 0
    assert page.js("document.querySelectorAll('#mm-p2 .mm-card').length") == 1
    notes = [p for p in info["problems"] if "page 2" in p and "do not fit" in p]
    assert len(notes) == 1 and notes[0].startswith("note:"), info["problems"]
    page.page.close()


def test_a_year_count_from_the_scenes_stays_whole(stage):
    """A year never rolls up from 10, even though it is over 20; a number with a comma is not a year."""
    page = stage["page"]
    page.js("""(() => { const box = document.createElement('div'); box.style.display = 'none'; document.body.appendChild(box);
        for (const [id, to] of [['mm-test-2024', '2024'], ['mm-test-2k', '2,024']]) {
          const n = document.createElement('span'); n.id = id; n.setAttribute('data-to', to); n.textContent = to; box.appendChild(n);
          n.setAttribute('data-counted', String(Stage.A.count(n, 1.0, 0.6))); }
        return true; })()""")
    assert page.js("document.getElementById('mm-test-2024').getAttribute('data-counted')") == "false"
    assert page.js("document.getElementById('mm-test-2k').getAttribute('data-counted')") == "true"
    seen = set()
    for k in range(-6, 48):
        page.seek(round(1.0 + k / 60, 4))
        seen.add(page.js("document.getElementById('mm-test-2024').textContent"))
    assert seen == {"2024"}, seen


def test_a_hold_card_page_with_nothing_to_write_is_noted(tmp_path, monkeypatch, browser):
    """A hold_card page whose prop is not a card holds a blank card up, which the check points out, with what to
    do about it."""
    pages = [dict(p) for p in HELD_PAGES]
    pages[1]["prop"] = "none"
    page, _ = held_project(tmp_path, monkeypatch, browser, pages=pages)
    info = page.js("Stage.audit()")
    assert page.js("document.querySelectorAll('#mm-p2 .mm-held').length") == 0
    notes = [p for p in info["problems"] if "hold_card" in p and "blank" in p]
    assert notes == ["note: page 2: the hold_card pose holds a blank card. Give the page a card prop with the words "
                     "to write on it, or pick another pose."], info["problems"]
    page.page.close()


def test_a_hold_card_page_with_no_measured_card_is_noted_with_the_way_to_fix_it(tmp_path, monkeypatch, browser):
    """With no card measured on the character's hold_card drawing, nothing can be written on it, so the note
    says to pick another pose or draw the pose again with a plain card."""
    pages = [dict(p) for p in HELD_PAGES]
    pages[1]["prop"] = "none"
    page, _ = held_project(tmp_path, monkeypatch, browser, build="big_head", pages=pages)
    info = page.js("Stage.audit()")
    assert info["pages"][1]["pose"] == "hold_card" and info["pages"][1]["held"] is None
    notes = [p for p in info["problems"] if "hold_card" in p and "blank" in p]
    assert notes == ["note: page 2: the hold_card pose holds a blank card, and no card was measured on it when the "
                     "character was packed, so nothing can be written on it. Pick another pose, or draw hold_card "
                     "again with a large plain white card and pack again."], info["problems"]
    page.page.close()


def profile_with(me, **handles):
    data = json.loads((me / "profile.json").read_text())
    data["handles"].update(handles)
    if not handles.get("instagram"):
        data["platforms"] = [p for p in data["platforms"] if p != "instagram"]
        data["cta"]["comment_platforms"] = [p for p in data["cta"]["comment_platforms"] if p != "instagram"]
    (me / "profile.json").write_text(json.dumps(data))
    return config.load_profile()


def test_the_card_shows_the_instagram_handle_with_its_at(example_me):
    card = install.profile_card(profile_with(example_me, instagram="examplestudio"))
    assert card["handle"] == "@examplestudio" and card["name"] == "Example Studio" and card["initial"] == "E"


def test_with_no_instagram_handle_the_card_shows_the_name_alone(example_me):
    card = install.profile_card(profile_with(example_me, instagram="", tiktok="@examplestudio"))
    assert card["handle"] == "", "never another platform's handle on Instagram's profile row"
