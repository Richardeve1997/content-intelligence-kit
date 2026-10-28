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

from mini_me_helpers import ROOT, load, make_character, needs_node, speech_samples, write_wav  # noqa: E402
from PIL import Image  # noqa: E402

from kit import brand, config  # noqa: E402

install = load("install")
margin_scan = load("margin_scan")

GSAP = ROOT / "kit" / "vendor" / "gsap.min.js"
pytestmark = pytest.mark.skipif(not GSAP.is_file(), reason="needs GSAP: bash bin/fetch_gsap.sh")

PAGES = [
    {"t": 0.0, "text": "BASIL *DIES*", "pose": "point_up", "field": "dark", "prop": "card: Week three"},
    {"t": 1.6, "text": "LOOK AT THIS", "pose": "point_left", "field": "light", "prop": "card: A sunny window"},
    {"t": 3.0, "text": "WATER EARLY", "pose": "point_right", "field": "dark", "prop": "stamp: AM"},
    {"t": 4.4, "text": "3 PLANTS", "pose": "thumbs_up", "field": "light", "prop": "count"},
    {"t": 5.6, "text": "COMMENT GROW", "pose": "point_up", "field": "dark", "prop": "cta"},
]
SPOKEN = ("Basil dies in week three. Look at this window. Water it early. Three plants made it. "
          "Comment GROW and I'll send you the checklist.")
SECONDS = 8.0


def words_for(text: str) -> list[dict]:
    """Words laid over the pages: each sentence starts on its page."""
    starts = [p["t"] + 0.12 for p in PAGES]
    out = []
    for start, sentence in zip(starts, re.findall(r"[^.]+\.", text)):
        t = start
        for word in sentence.split():
            out.append({"w": word, "s": round(t, 3), "e": round(t + 0.26, 3)})
            t += 0.3
    return out


def build_project(root: Path, me: Path, slug: str = "example-reel", spoken: str = SPOKEN) -> Path:
    """work/<slug>/ with a voice-over, words and pages, the stand-in character in me/, then install.py."""
    folder = root / "work" / slug
    folder.mkdir(parents=True, exist_ok=True)
    wav = write_wav(speech_samples([("talk", SECONDS - 0.3), ("gap", 0.3)]), folder / "vo.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(wav), "-c:a", "aac", "-b:a", "192k", "-ac", "2",
                    str(folder / "vo.m4a")], check=True)
    wav.unlink()
    (folder / "words.json").write_text(json.dumps({"dur": SECONDS, "words": words_for(spoken)}))
    (folder / "beats.json").write_text(json.dumps({"duration": SECONDS, "pages": PAGES}))
    if not (me / "character" / "manifest.json").exists():
        make_character(me / "character")
    return install.install(slug)


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


@pytest.fixture(scope="module")
def stage(tmp_path_factory, browser):
    base = tmp_path_factory.mktemp("stage")
    root, me = base / "kit", base / "me"
    shutil.copytree(ROOT / "me.example", me)
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("KIT_ME", str(me))
        patch.setattr(config, "kit_root", lambda: root)
        hf = build_project(root, me)
        profile = config.load_profile()
        tokens = brand.colour_tokens(profile)
    page = Page(browser, hf)
    yield {"page": page, "hf": hf, "profile": profile, "tokens": tokens, "dir": base}
    page.page.close()


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
        assert len(shown) == 1 and shown[0]["id"] == f"mm-p{n}", shown
        assert shown[0]["field"] == spec["field"]
        assert f"mm-stk-{spec['field']}" in shown[0]["filter"], "the outline matches the field"


def test_the_character_is_big_and_cropped_at_the_waist(stage):
    pages = stage["page"].js("Stage.audit()")["pages"]
    for spec, info in zip(PAGES, pages):
        scale, crown = info["char"]["scale"], info["char"]["crown"]
        if spec["prop"] == "cta":
            assert 10 <= scale <= 12 and crown <= 1150, info["char"]
        else:
            assert 6 <= scale <= 8, info["char"]
            assert 1000 <= crown <= 1320, info["char"]
        # the waist sits at the bottom of the frame or below it: the feet are never in shot
        assert info["char"]["crown"] + 200 * scale > 1920


def test_headlines_fit_the_safe_area_and_the_first_shows_on_frame_0(stage):
    page = stage["page"]
    for info in page.js("Stage.audit()")["pages"]:
        assert all(90 <= line["left"] and line["right"] <= 990 for line in info["lines"]), info["lines"]
        assert info["headBottom"] <= 632
    page.seek(0)
    assert page.js("getComputedStyle(document.querySelector('#mm-p0 .mm-ln')).opacity") == "1"
    weight = page.js("getComputedStyle(document.querySelector('#mm-p0 .mm-ln')).fontWeight")
    assert weight == "900", "the heaviest weight of the display font"
    family = page.js("getComputedStyle(document.querySelector('#mm-p0 .mm-ln')).fontFamily")
    assert "Inter Tight" in family
    transform = page.js("getComputedStyle(document.querySelector('#mm-p0 .mm-ln')).textTransform")
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
            where.append(page.js(f"getComputedStyle(document.querySelector('#mm-p{n} .mm-hl')).transform"))
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
