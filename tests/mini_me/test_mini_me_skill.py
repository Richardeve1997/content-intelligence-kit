"""The mini-me skill: its SKILL.md follows the shared contract, says what the code does, and the kit's small
scripts (the brief and the test clip) behave.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from mini_me_helpers import KIT, ROOT, SKILL, load

from kit import config, voice

BODY = (SKILL / "SKILL.md").read_text(encoding="utf-8")
EM_DASH, EN_DASH = chr(0x2014), chr(0x2013)
INTERNAL_WORDS = ["rul" + "ing", "contro" + "ller", "release " + "scan", "house " + "brand", "plan " + "task"]
AUTHORED = [SKILL / "SKILL.md", *sorted(KIT.glob("*.py")), KIT / "stage.js", KIT / "stage.css", KIT / "gates.sh",
            ROOT / "kit" / "voice.py", ROOT / "kit" / "sfx.py"]


def frontmatter(text: str) -> dict:
    block = text.split("---\n")[1]
    return dict(re.findall(r"^(\w+):\s*(.+)$", block, re.M))


def test_the_skill_follows_the_shared_contract():
    meta = frontmatter(BODY)
    assert meta["name"] == "mini-me"
    assert "Use when" in meta["description"] and "make number 3 a mini me video" in meta["description"]
    first_step = re.search(r"^1\. (.+)$", BODY, re.M).group(1)
    assert 'kit.rules.read("mini-me")' in first_step
    headings = re.findall(r"^## (.+)$", BODY, re.M)
    assert headings[-1] == "Feedback", "Feedback is the last section"
    feedback = BODY[BODY.index("## Feedback") :]
    assert 'kit.rules.add("mini-me"' in feedback and "python -m kit.rules add mini-me - <<'RULE'\n<rule>\nRULE\n" in feedback


def test_the_adapter_check_passes_with_the_skill_in_place():
    done = subprocess.run(["bash", str(ROOT / "bin" / "check_adapters.sh")], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout


@pytest.mark.parametrize("path", AUTHORED, ids=lambda p: p.name)
def test_authored_files_have_no_dashes_paths_or_process_words(path):
    text = path.read_text(encoding="utf-8")
    assert EM_DASH not in text and EN_DASH not in text, path.name
    assert "/" + "Users" + "/" not in text
    lower = text.lower()
    for word in INTERNAL_WORDS:
        assert word not in lower, f"{path.name} contains {word!r}"


def test_the_skill_names_the_no_key_sentence_and_the_house_numbers():
    assert '"No ElevenLabs key, so record the voice-over on your phone and drop it in work/<video>/take/"' in BODY
    assert voice.NO_KEY.format(slug="<video>") in BODY
    for words in ("scale 6 to 8", "scale 10 to 12", "12 px", "3 px", "at least 15 dB", "four words", "no caption track",
                  "--font-display", "--field-dark", "--field-light", "1.15", "-13 LUFS", "humanizer", "at most 5 cycles"):
        assert words in BODY, words


def test_the_lint_runs_through_kit_media_like_every_hyperframes_call():
    """`kit.media run` turns HyperFrames' telemetry off. render.py does the same for the render itself."""
    blocks = re.findall(r"^```bash\n(.*?)^```", BODY, re.M | re.S)
    calls = [line for block in blocks for line in block.splitlines() if "hyperframes " in line]
    assert calls == ["uv run python -m kit.media run -- npx hyperframes lint $W/build/hf"]
    assert "`npx hyperframes" not in BODY, "the checklist names no bare npx command to copy"


def test_every_kit_file_the_skill_names_exists():
    for name in re.findall(r"`([a-z_]+\.(?:py|sh|js|css))`", BODY):
        if name in {"character.js", "scenes.js", "kit.py"}:
            continue
        assert (KIT / name).is_file() or (ROOT / "kit" / name).is_file(), name
    assert (KIT / "character" / "character.js").is_file()


def test_every_pose_and_prop_the_skill_lists_is_known_to_beats():
    beats = load("beats")
    table = BODY[BODY.index("Pick the pose for what the phrase means") : BODY.index("Props:")]
    named = set(re.findall(r"`([a-z_]+)`", table))
    assert named == set(beats.CORE_POSES)
    props = BODY[BODY.index("Props:") : BODY.index("## The look")]
    assert set(re.findall(r"^\| `([a-z]+)", props, re.M)) == set(beats.PROPS)


def test_every_script_answers_help():
    for script in ("beats.py", "install.py", "check.py", "render.py", "ship.py", "margin_scan.py", "brief.py", "test_clip.py"):
        done = subprocess.run(["uv", "run", "--project", str(ROOT), "python", str(KIT / script), "--help"],
                              capture_output=True, text=True, cwd=ROOT, timeout=120)
        assert done.returncode == 0 and "usage" in done.stdout.lower(), script + done.stderr


# --- the brief ---------------------------------------------------------------------------------------------------------


def research_run(root: Path):
    run = root / "research" / "runs" / "2026-10-27"
    run.mkdir(parents=True)
    (run / "ideas.json").write_text(json.dumps([
        {"n": 1, "title": "One", "angle": "a", "why": "b", "format": "c", "evidence": [], "hooks": []},
        {"n": 3, "title": "Why windowsill basil dies in week three", "angle": "light, not water", "why": "outlier",
         "format": "list", "evidence": [{"url": "https://example.com/basil", "creator": "@example", "outlier": 4.2}],
         "hooks": ["Your basil is not thirsty."]},
    ]))


def test_the_brief_saves_the_idea_and_names_the_closing_line(kit_home, capsys):
    research_run(kit_home)
    brief = load("brief")
    assert brief.main(["--number", "3"]) == 0
    out = capsys.readouterr().out
    slug = "why-windowsill-basil-dies"  # short and readable, not the whole title
    idea = json.loads((config.work_dir(slug) / "idea.json").read_text())
    assert idea["n"] == 3 and (config.work_dir(slug) / "take").is_dir()
    assert "Comment GROW and I'll send you the balcony planting checklist." in out
    assert "## Narration" in out and f"work/{slug}/script.md" in out and "Voice model" in out


def test_the_brief_without_research_is_a_plain_sentence(kit_home, capsys):
    brief = load("brief")
    assert brief.main(["--number", "2"]) == 1
    assert "run my research" in capsys.readouterr().out


# --- the test clip -----------------------------------------------------------------------------------------------------


def test_the_test_clip_runs_the_pipeline_on_one_page(kit_home, tmp_path, monkeypatch):
    test_clip = load("test_clip")
    install, render, ship = (load(name) for name in ("install", "render", "ship"))
    calls = []
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/say" if name == "say" else None)

    def fake_say(text, slug, **kwargs):
        folder = config.work_dir(slug)
        (folder / "vo.m4a").write_bytes(b"audio")
        calls.append(("say", text))
        return voice.VO(folder / "vo.m4a", [])

    monkeypatch.setattr(voice, "from_say", fake_say)
    monkeypatch.setattr(test_clip.media, "probe", lambda path: type("I", (), {"duration": 6.2})())
    import sys

    monkeypatch.setitem(sys.modules, "install", install)
    monkeypatch.setitem(sys.modules, "render", render)
    monkeypatch.setitem(sys.modules, "ship", ship)
    monkeypatch.setattr(install, "install", lambda slug: calls.append(("install", slug)))
    monkeypatch.setattr(render, "render", lambda slug, *a: calls.append(("render", slug)))

    def fake_ship(args):
        calls.append(("ship", args))
        (config.work_dir(args[0]) / "final.mp4").write_bytes(b"clip")
        return 0

    monkeypatch.setattr(ship, "main", fake_ship)
    out = tmp_path / "clips" / "mini-me.mp4"
    assert test_clip.main(["--out", str(out)]) == 0
    assert out.read_bytes() == b"clip"
    assert [c[0] for c in calls] == ["say", "install", "render", "ship"]
    assert "Example Studio" in calls[0][1]
    assert calls[-1][1] == ["mini-me-test-clip", "--music", "none"]
    assert not config.work_dir("mini-me-test-clip").exists(), "the scratch folder is cleaned up"


def noisy_pipeline(monkeypatch, *, fail: bool = False):
    """The three build steps, each printing the kind of progress lines the real ones print."""
    import sys

    install, render, ship = (load(name) for name in ("install", "render", "ship"))
    for name, module in (("install", install), ("render", render), ("ship", ship)):
        monkeypatch.setitem(sys.modules, name, module)

    def fake_install(slug):
        print(f"installed work/{slug}/build/hf (6.2 s, 1 pages).")

    def fake_render(slug, *args):
        print("rendered picture.mp4 (1080x1920, 60 fps, 6.20 s)")

    def fake_ship(args):
        print("  #       t field  pose        scale crown  headline sizes")
        if fail:
            print("build/hits.json is missing, so the page check made it again.")
            print("0 whooshes and 0 thuds -> build/hits.json")
            print("FAIL: the speech gate found 1 lost word run(s):")
            print('- "mini" (at 1.45 s in the edit) is missing from the final audio.')
            print("The failed render is build/final.next.mp4; final.mp4 was left as it was.")
            return 1
        print("finished build/final.next.mp4 at 1x: -13.0 LUFS, true peak -2.6 dBTP")
        print("PASS: the speech gate heard every word of the voice-over.")
        (config.work_dir(args[0]) / "final.mp4").write_bytes(b"clip")
        return 0

    monkeypatch.setattr(install, "install", fake_install)
    monkeypatch.setattr(render, "render", fake_render)
    monkeypatch.setattr(ship, "main", fake_ship)


def scratch_voice(monkeypatch, seconds: float = 6.2):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/say" if name == "say" else None)

    def fake_say(text, slug, **kwargs):
        (config.work_dir(slug) / "vo.m4a").write_bytes(b"audio")
        return voice.VO(config.work_dir(slug) / "vo.m4a", [])

    monkeypatch.setattr(voice, "from_say", fake_say)
    test_clip = load("test_clip")
    monkeypatch.setattr(test_clip.media, "probe", lambda path: type("I", (), {"duration": seconds})())
    return test_clip


def test_the_build_steps_keep_their_progress_lines_off_the_screen(kit_home, tmp_path, monkeypatch, capsys):
    """Whatever the script prints before its last line is a note for the creator, so the build's own log is not."""
    noisy_pipeline(monkeypatch)
    test_clip = scratch_voice(monkeypatch, seconds=4.0)  # a short line earns a real note
    assert test_clip.main(["--out", str(tmp_path / "clip.mp4")]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[0].startswith("note: the line runs 4.0 s")
    assert len(lines) == 2 and lines[1].startswith("test clip "), lines


def test_a_clip_that_fails_its_checks_says_what_the_steps_said(kit_home, tmp_path, monkeypatch, capsys):
    noisy_pipeline(monkeypatch, fail=True)
    test_clip = scratch_voice(monkeypatch)
    assert test_clip.main(["--out", str(tmp_path / "clip.mp4")]) == 1
    said = capsys.readouterr().out
    assert 'FAIL: the speech gate found 1 lost word run(s):' in said and '"mini" (at 1.45 s' in said
    assert "--keep" in said and "final.next.mp4" in said
    # the clips command cannot pass --keep, so the sentence says how to run the script by hand
    assert "uv run python .claude/skills/mini-me/kit/test_clip.py --out" in said and "by hand" in said
    assert "Run it again with --keep" not in said
    assert len(said.strip().splitlines()) == 8, "a first line, the last six lines of the log, and how to look closer"
    assert "Traceback" not in said
    # onboarding's clips command shows a failure's last eight lines, so none of these is cut when it passes them on
    from kit import onboarding

    shown = onboarding._what_it_said(subprocess.CompletedProcess([], 1, stdout=said, stderr=""))
    for line in said.strip().splitlines():
        assert line.strip() in shown, f"the clips command would cut: {line}"
    assert not (tmp_path / "clip.mp4").exists()


def test_a_recording_that_was_paced_gets_a_note_that_fits_a_test_clip(kit_home, tmp_path, monkeypatch, capsys):
    """kit.voice says how to keep a recording's own pace for a whole Reel. A test clip has no such option."""
    noisy_pipeline(monkeypatch)
    test_clip = scratch_voice(monkeypatch, seconds=6.0)
    recording = tmp_path / "line.m4a"
    recording.write_bytes(b"the creator's own recording")

    def fake_recording(path, slug, **kwargs):
        (config.work_dir(slug) / "vo.m4a").write_bytes(b"audio")
        print(voice.PACED.format(tempo=voice.RECORDING_TEMPO, longest=voice.PAUSE_LONGEST, keep=voice.PAUSE_KEEP))
        return voice.VO(config.work_dir(slug) / "vo.m4a", [])

    monkeypatch.setattr(voice, "from_recording", fake_recording)
    assert test_clip.main(["--out", str(tmp_path / "clip.mp4"), "--voice", str(recording)]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2 and lines[1].startswith("test clip "), lines
    assert lines[0].startswith("note: your recording was sped up to 1.15x and pauses over 0.35 s were cut to 0.24 s")
    assert "--keep-pace" not in lines[0], "that option belongs to the voice-over command, not to a test clip"


def test_a_recording_that_was_not_paced_says_nothing_about_pace(kit_home, tmp_path, monkeypatch, capsys):
    noisy_pipeline(monkeypatch)
    test_clip = scratch_voice(monkeypatch, seconds=6.0)
    recording = tmp_path / "line.m4a"
    recording.write_bytes(b"the creator's own recording")

    def fake_recording(path, slug, **kwargs):
        (config.work_dir(slug) / "vo.m4a").write_bytes(b"audio")
        return voice.VO(config.work_dir(slug) / "vo.m4a", [])

    monkeypatch.setattr(voice, "from_recording", fake_recording)
    assert test_clip.main(["--out", str(tmp_path / "clip.mp4"), "--voice", str(recording)]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1 and lines[0].startswith("test clip ")


def test_the_test_clip_script_is_executable_like_the_scripts_beside_it():
    script = KIT / "test_clip.py"
    assert script.stat().st_mode & 0o111
    assert script.read_text(encoding="utf-8").startswith("#!/usr/bin/env python3")


def test_the_test_clip_page_is_one_headline_under_four_words():
    test_clip = load("test_clip")
    assert len(test_clip.HEADLINE.split()) <= 4


def test_with_no_voice_at_all_the_test_clip_says_what_to_do(kit_home, tmp_path, monkeypatch, capsys):
    test_clip = load("test_clip")
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert test_clip.main(["--out", str(tmp_path / "clip.mp4")]) == 1
    assert "--voice" in capsys.readouterr().out
    assert not (tmp_path / "clip.mp4").exists()


def fake_pipeline(monkeypatch, calls):
    install, render, ship = (load(name) for name in ("install", "render", "ship"))
    import sys

    monkeypatch.setitem(sys.modules, "install", install)
    monkeypatch.setitem(sys.modules, "render", render)
    monkeypatch.setitem(sys.modules, "ship", ship)
    monkeypatch.setattr(install, "install", lambda slug: calls.append(("install", slug)))
    monkeypatch.setattr(render, "render", lambda slug, *a: calls.append(("render", slug)))

    def fake_ship(args):
        calls.append(("ship", args))
        (config.work_dir(args[0]) / "final.mp4").write_bytes(b"clip")
        return 0

    monkeypatch.setattr(ship, "main", fake_ship)


def test_a_recording_kept_in_the_scratch_folder_is_read_and_left_alone(kit_home, tmp_path, monkeypatch):
    test_clip = load("test_clip")
    calls = []
    fake_pipeline(monkeypatch, calls)
    recording = config.work_dir("mini-me-test-clip") / "take" / "line.m4a"
    recording.parent.mkdir(parents=True)
    recording.write_bytes(b"the creator's own recording")

    def fake_recording(path, slug, **kwargs):
        assert Path(path).read_bytes() == b"the creator's own recording", "still there when it is read"
        (config.work_dir(slug) / "vo.m4a").write_bytes(b"audio")
        calls.append(("record", Path(path)))
        return voice.VO(config.work_dir(slug) / "vo.m4a", [])

    monkeypatch.setattr(voice, "from_recording", fake_recording)
    monkeypatch.setattr(test_clip.media, "probe", lambda path: type("I", (), {"duration": 6.0})())
    assert test_clip.main(["--out", str(tmp_path / "clip.mp4"), "--voice", str(recording)]) == 0
    assert calls[0] == ("record", recording.absolute())
    assert recording.read_bytes() == b"the creator's own recording", "never deleted"
    assert not (config.work_dir("mini-me-test-clip") / "vo.m4a").exists(), "what the script made is cleaned up"


def test_the_test_clip_never_reads_a_stock_voice_in_place_of_a_missing_copy(kit_home, example_me, tmp_path, monkeypatch, capsys):
    test_clip = load("test_clip")
    calls = []
    fake_pipeline(monkeypatch, calls)
    profile = json.loads((example_me / "profile.json").read_text())
    profile["voice_over"] = "clone"
    (example_me / "profile.json").write_text(json.dumps(profile))
    (kit_home / ".env").write_text("ELEVENLABS_API_KEY=not-a-real-key\n", encoding="utf-8")  # where the kit reads keys
    monkeypatch.setattr(voice, "from_elevenlabs", lambda *a, **k: pytest.fail("no stock voice in place of the copy"))
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/say" if name == "say" else None)

    def fake_say(text, slug, **kwargs):
        (config.work_dir(slug) / "vo.m4a").write_bytes(b"audio")
        return voice.VO(config.work_dir(slug) / "vo.m4a", [])

    monkeypatch.setattr(voice, "from_say", fake_say)
    monkeypatch.setattr(test_clip.media, "probe", lambda path: type("I", (), {"duration": 6.0})())
    assert test_clip.main(["--out", str(tmp_path / "clip.mp4")]) == 0
    assert "no copy of your voice yet" in capsys.readouterr().out
