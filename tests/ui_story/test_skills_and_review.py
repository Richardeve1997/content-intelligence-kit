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
    assert f'kit.rules.add("{name}"' in feedback and f"python -m kit.rules add {name} '" in feedback


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
