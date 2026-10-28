"""The ui-story and review skills: their shared contract, the house values they carry, and the review sheet."""

import re
import subprocess
import sys

import pytest
from uistory_helpers import KIT, REVIEW, ROOT, SKILL, ffmpeg

EM_DASH, EN_DASH = chr(0x2014), chr(0x2013)  # written as code points so this file holds no dash
SKILLS = {"ui-story": SKILL / "SKILL.md", "review": REVIEW / "SKILL.md"}
HOME_ROOT = "/" + "Users" + "/"  # built from pieces so this file never holds a home path
RAW_TAKE_NAME = re.compile(r"\b" + "IM" + r"G_\d")


def text(path):
    return path.read_text(encoding="utf-8")


def frontmatter(path):
    block = text(path).split("---\n")[1]
    return dict(re.findall(r"^(\w+):\s*(.+)$", block, re.M))


@pytest.mark.parametrize("name", list(SKILLS))
def test_each_skill_follows_the_shared_contract(name):
    path = SKILLS[name]
    meta = frontmatter(path)
    assert meta["name"] == name
    assert len(meta["description"]) > 80 and "Use when" in meta["description"]
    body = text(path)
    first_step = re.search(r"^1\. (.+)$", body, re.M).group(1)
    assert f'kit.rules.read("{name}")' in first_step
    headings = re.findall(r"^## (.+)$", body, re.M)
    assert headings[-1] == "Feedback", "Feedback is the last section"
    feedback = body[body.index("## Feedback") :]
    assert f'kit.rules.add("{name}"' in feedback and f"python -m kit.rules add {name} - <<'RULE'" in feedback


@pytest.mark.parametrize("name", list(SKILLS))
def test_skill_prose_has_no_dashes_and_no_machine_paths(name):
    body = text(SKILLS[name])
    assert EM_DASH not in body and EN_DASH not in body
    assert HOME_ROOT not in body and not RAW_TAKE_NAME.search(body)


def test_ui_story_carries_the_house_rules():
    body = text(SKILLS["ui-story"])
    for value in ("1.15", "--music-lufs -24", "--mix-peak 0.74", "1570", "-13 LUFS", "about six words",
                  "about every two seconds", "split", "canvas", "face", "kit.music.pick", "VOICE.md",
                  "humanizer", "plan.md", "final.mp4", "kit.media", "heavy", "5 GB"):
        assert value in body, value
    assert "proof card" in body and "subject card" in body, "the three-beat open"
    assert "never" in body and "scroll" in body, "no scrolled text recordings"


def test_the_plans_film_guide_tells_the_creator_to_turn_hdr_video_off():
    body = text(SKILLS["ui-story"])
    plan = body.split("## The plan")[1].split("## The look")[0]
    guide = plan[plan.index("Write the film guide") :]
    assert "Turn HDR video off" in guide and "Settings, Camera, Record Video, HDR Video off" in guide
    assert "washed out" in guide


def test_ui_story_names_every_kit_script_and_every_named_script_exists():
    body = text(SKILLS["ui-story"])
    shipped = {p.name for p in KIT.iterdir() if p.is_file()}
    named = set(re.findall(r"`(?:\$K/|kit/)?([a-z_0-9]+\.(?:py|sh|css|js|html))(?![a-z])", body))
    missing = {n for n in named if n not in shipped and n not in {"review_frames.py", "index.html"}}
    assert not missing, f"the skill names files that do not exist: {missing}"
    unnamed = {n for n in shipped if n.endswith((".py", ".sh")) and n not in body}
    assert not unnamed, f"kit scripts the skill never mentions: {unnamed}"


def test_review_carries_the_rubric_and_the_loop_limit():
    body = text(SKILLS["review"])
    for value in ("bin/review_frames.py", "sheet.jpg", "5 cycles", "kit.qa_captions", "1570", "-13", "-1.5",
                  "safe", "frame 0"):
        assert value in body, value


def test_one_fact_shows_one_number_on_every_card():
    """A subject card's star badge, a later stat card and a slow count once showed three numbers for one fact."""
    ui, review = text(SKILLS["ui-story"]), text(SKILLS["review"])
    real_things = ui.split("### Real things on screen")[1].split("###")[0]
    assert "One fact shows one number" in real_things
    assert "badge" in real_things, "the subject card's badges are named, since they come first"
    assert "stronger sourced version" not in real_things, "the number rule and the one-number rule must agree"
    assert "is the one the creator says" in real_things, "it says plainly that the spoken number wins"
    pace = ui.split("### Pace and reading load")[1].split("###")[0]
    assert "in under a second" in pace, "a count's passing values must not read as numbers"
    rubric = review.split("## The rubric")[1].split("## Common mistakes")[0]
    assert "one fact shows one number" in rubric.lower()
    handover = ui.split("## Before handing over")[1]
    assert "one number" in handover


def test_the_restart_step_keeps_a_repeat_the_creator_meant():
    """The restart check flags any word said twice in a row, so the recipe says a meant repeat is not cut."""
    recipe = text(SKILLS["ui-story"]).split("## The edit")[1]
    restart_line = next(line for line in recipe.splitlines() if "restarts.log" in line)
    assert "a repeat the creator meant stays" in restart_line


def test_both_skills_are_routed_in_claude_md():
    claude = text(ROOT / "CLAUDE.md")
    assert "`ui-story`" in claude and "`review`" in claude


# --- the review sheet ----------------------------------------------------------------------------------------


def test_review_frames_writes_a_sheet_into_the_videos_review_folder(tmp_path):
    work = tmp_path / "work" / "example-reel"
    work.mkdir(parents=True)
    final = work / "final.mp4"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=1080x1920:r=30:d=6", "-c:v", "libx264", "-pix_fmt", "yuv420p", final)
    done = subprocess.run([sys.executable, str(ROOT / "bin" / "review_frames.py"), str(final)],
                          capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr
    sheet = work / "review" / "sheet.jpg"
    assert sheet.is_file()
    assert str(sheet) in done.stdout
    from PIL import Image

    with Image.open(sheet) as image:
        width, height = image.size
    assert width >= 600 and height >= 300
    assert (work / "review" / "open.jpg").is_file(), "the mute-open strip for the first five seconds"


def test_review_frames_takes_a_rate_and_rejects_a_missing_file(tmp_path):
    work = tmp_path / "work" / "example-reel"
    work.mkdir(parents=True)
    final = work / "final.mp4"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=1080x1920:r=30:d=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", final)
    done = subprocess.run([sys.executable, str(ROOT / "bin" / "review_frames.py"), str(final), "--fps", "2"],
                          capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr
    assert "8 frames" in done.stdout
    missing = subprocess.run([sys.executable, str(ROOT / "bin" / "review_frames.py"), str(work / "nope.mp4")],
                             capture_output=True, text=True)
    assert missing.returncode != 0
    assert "Traceback" not in missing.stderr and "nope.mp4" in missing.stdout + missing.stderr


def _review_frames():
    import importlib.util

    spec = importlib.util.spec_from_file_location("review_frames", ROOT / "bin" / "review_frames.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("fps", [1, 2, 3, 4, 10, 20])
def test_every_tile_on_a_sheet_has_its_own_time(fps):
    """At two frames a second the tiles at 1.5 s and 2 s must not both say 2s: the review names a moment by it."""
    stamp = _review_frames().stamp
    labels = [stamp(i / fps, fps) for i in range(int(fps * 6))]
    assert len(set(labels)) == len(labels), labels
    assert stamp(0.2, 1) == "0.2s" and stamp(3, 1) == "3s" and stamp(1.5, 2) == "1.5s"


@pytest.mark.parametrize("fps", [0.5, 1, 2])
def test_each_tile_shows_the_frame_at_its_time(tmp_path, fps):
    """A tile stamped 1.5s shows the frame at 1.5 s, so a fix named by that time lands where the review saw it.
    The test video's brightness is twice its frame number, so each tile says which frame it holds."""
    from PIL import Image, ImageStat

    video = tmp_path / "ramp.mp4"
    ffmpeg("-f", "lavfi", "-i", "nullsrc=s=64x64:r=30:d=4,format=gray,geq=lum='N*2'", "-c:v", "libx264", "-qp", "0",
           "-pix_fmt", "yuv420p", video)
    tiles = _review_frames().frames_at_rate(video, fps, tmp_path)
    assert len(tiles) >= 2
    for index, tile in enumerate(tiles):
        frame = ImageStat.Stat(Image.open(tile).convert("L")).mean[0] / 2
        assert abs(frame - index / fps * 30) <= 1.5, (index, frame)


@pytest.mark.parametrize("fps", [1, 2])
def test_tiles_keep_their_times_when_the_picture_starts_after_the_sound(tmp_path, fps):
    """In some files the first picture comes a frame after the sound. The tile stamped 0s is still that first
    picture, not one a whole period later. Here the picture starts 1/60 s in and its brightness is its frame number."""
    from PIL import Image, ImageStat

    ramp, late = tmp_path / "ramp.mp4", tmp_path / "late.mp4"
    ffmpeg("-f", "lavfi", "-i", "nullsrc=s=64x64:r=60:d=4,format=gray,geq=lum='N'", "-c:v", "libx264", "-qp", "0",
           "-pix_fmt", "yuv420p", ramp)
    ffmpeg("-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-itsoffset", "0.0166667", "-i", ramp,
           "-map", "1:v", "-map", "0:a", "-t", "4", "-c:v", "copy", "-c:a", "aac", late)
    folder = tmp_path / "tiles"
    folder.mkdir()
    tiles = _review_frames().frames_at_rate(late, fps, folder)
    assert len(tiles) >= 4
    for index, tile in enumerate(tiles):
        frame = ImageStat.Stat(Image.open(tile).convert("L")).mean[0]
        assert abs(frame - max(0.0, index / fps - 1 / 60) * 60) <= 2.0, (index, frame)


# --- what this round of fixes added ------------------------------------------------------------------------


@pytest.mark.parametrize("name", list(SKILLS))
def test_feedback_removes_an_older_rule_by_its_number(name):
    feedback = text(SKILLS[name]).split("## Feedback", 1)[1]
    assert f"python -m kit.rules read {name} --numbered" in feedback
    assert f"python -m kit.rules remove {name} <number>" in feedback
    assert "kit.rules undo" not in feedback, "undo removes the newest rule, not the one it contradicts"


def test_ui_story_states_the_rules_the_review_found_missing():
    body = text(SKILLS["ui-story"])
    assert "The first real footage lands early" in body
    assert "build/hf/assets/" in body and "only loads files inside the project" in body
    assert "mkdir -p $B/sources" in body
    assert '"windows": [' in body and '"kind": "punch"' in body, "the layout.json format"
    assert "follow line" in body and "cta.comment_platforms" in body
    assert "review/review.json" in body and "final.next.mp4" in body


def test_layout_help_prints_the_format():
    from uistory_helpers import run_kit

    done = run_kit("layout.py", "--help")
    assert done.returncode == 0
    assert '"windows": [' in done.stdout and "punch" in done.stdout and "slow" in done.stdout


def test_review_says_a_new_final_clears_the_pass():
    body = text(SKILLS["review"])
    assert "ship.py" in body and "review.json" in body


def test_prep_makes_the_sources_folder_first(tmp_path, monkeypatch):
    from uistory_helpers import load

    from kit import config

    monkeypatch.setattr(config, "kit_root", lambda: tmp_path / "kit")
    prep = load("prep")
    with pytest.raises(SystemExit) as caught:
        prep.main(["example-reel"])
    assert "no take" in str(caught.value).lower()
    assert (tmp_path / "kit" / "work" / "example-reel" / "build" / "sources").is_dir()


# --- the polish pass ---------------------------------------------------------------------------------------


def test_an_edit_with_no_plan_names_the_video_and_links_the_take_first():
    """A creator who filmed first and says "edit my take" has no work/<video>/ yet. The steps say how to name the
    video and link the take before the recipe's prep.py, which expects the take in work/<video>/take/."""
    steps = text(SKILLS["ui-story"]).split("## Steps")[1].split("## The plan")[0]
    edit_step = next(line for line in steps.splitlines() if line.startswith("4. For the edit"))
    assert "no plan" in edit_step, "the edit step covers a take that skipped the plan"
    assert "video name" in edit_step and "work/<video>/take/" in edit_step and "ln -s" in edit_step, edit_step
    assert "never" in edit_step and "copy" in edit_step, "the raw take is linked, never copied"


def test_the_lint_warnings_the_kit_causes_on_purpose_are_named_as_expected():
    """Two HyperFrames lint warnings come from the kit's own design: one index.html with one timeline, and a
    picture shown in two scenes (plain layers carry no start time). The skill names them, so a creator does not
    chase them, and keeps every other warning worth a look."""
    notes = text(SKILLS["ui-story"]).split("Notes on the recipe:")[1].split("## The kit")[0]
    lint = next(line for line in notes.splitlines() if "hyperframes lint" in line)
    assert "composition_file_too_large" in lint and "index.html" in lint, lint
    assert "duplicate_media_discovery_risk" in lint and "<img>" in lint, lint
    assert "expected" in lint and "error" in lint, lint
    assert "timeline_track_too_dense" not in text(SKILLS["ui-story"]), "the caption track no longer causes it"


def test_the_cleanup_waits_for_the_render_gate_and_the_review():
    """The snapshots were once deleted before the render, because the note said "after the composite passes";
    snap_render_gate.py needs them after the render, and every ship reads gfx.webm."""
    notes = text(SKILLS["ui-story"]).split("Notes on the recipe:")[1].split("## The kit")[0]
    cleanup = next(line for line in notes.splitlines() if "delete `build/gfx.webm`" in line)
    assert "After the composite passes" not in cleanup, cleanup
    assert "snap_render_gate.py" in cleanup and "review" in cleanup, cleanup
    assert cleanup.index("review") < cleanup.index("delete `build/gfx.webm`"), "the order comes first"


def test_the_skill_finds_windows_by_name_or_time_never_by_place():
    body = text(SKILLS["ui-story"])
    layout_note = next(line for line in body.splitlines() if line.startswith("`build/layout.json` lists"))
    assert "UIS.win(" in layout_note and '"name"' in layout_note, layout_note
    assert "never by its place" in layout_note, layout_note
    assert '"name":' in body.split('"windows": [')[1].split("```")[0], "the example layout.json shows a name"
    helpers = next(line for line in body.splitlines() if line.startswith("| `uistory.js` |"))
    assert "`win`" in helpers, helpers


def test_layout_help_wraps_its_usage_example():
    """A backslash at a line end inside the help text once joined the example into one 186 character line."""
    from uistory_helpers import run_kit

    lines = run_kit("layout.py", "--help").stdout.splitlines()
    first = next(i for i, line in enumerate(lines) if "python layout.py --layout" in line)
    assert "--out layout.resolved.json" not in lines[first], lines[first]
    assert lines[first].rstrip().endswith("\\"), "the example breaks the way a shell command does"
    assert lines[first + 1].strip().startswith("--out layout.resolved.json"), lines[first + 1]
    assert max(len(line) for line in lines) <= 120


def test_the_number_rule_reads_the_same_in_the_checklist_and_the_rubric():
    """The real-things section says the spoken number wins and a fact with no spoken number shows the sourced
    one. The handover checklist and the review rubric once said only "matches the spoken one"."""
    ui, review = text(SKILLS["ui-story"]), text(SKILLS["review"])
    assert "is the one the creator says; a fact they give no number for shows the sourced one" in ui
    handover = ui.split("## Before handing over")[1]
    checklist = next(line for line in handover.splitlines() if "number on screen" in line)
    rubric = review.split("## The rubric")[1].split("## Common mistakes")[0]
    item6 = next(line for line in rubric.splitlines() if line.startswith("6. "))
    for line in (checklist, item6):
        assert "is the one the creator says" in line and "no number for shows the sourced one" in line, line
    assert "matches the spoken one" not in ui and "matches the number spoken" not in review


def test_a_skipped_proof_card_is_allowed_by_the_skill_and_the_rubric():
    """The proof card has about 1.5 s before the subject card, and seven words need about 3 s to read, so the
    skill lets it go. The rubric's first item must not then send the open back for lacking one."""
    ui, review = text(SKILLS["ui-story"]), text(SKILLS["review"])
    proof = next(line for line in ui.split("### The open")[1].splitlines() if "one proof card" in line)
    assert "skip it" in proof and "no fact makes the idea clearer" in proof, proof
    assert "long enough to read" in proof, "a proof card that cannot be read in time is skipped too"
    rubric = review.split("## The rubric")[1].split("## Common mistakes")[0]
    item1 = next(line for line in rubric.splitlines() if line.startswith("1. "))
    assert "no proof card passes" in item1 and "skips" in item1, item1
    assert "no fact makes the idea clearer" in item1 and "long enough to read" in item1, item1
