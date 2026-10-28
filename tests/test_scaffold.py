"""Scaffold checks.

They guard the decisions made when the repo was created: secrets stay out of git,
Python and HyperFrames stay pinned, every dependency group is declared up front,
and the licence names its holder.
"""

import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

ENV_NAMES = {
    "APIFY_TOKEN",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "ELEVENLABS_API_KEY",
    "ZERNIO_API_KEY",
}

README_HEADINGS = [
    "What it does",
    "What's included",
    "Not included",
    "What you need",
    "Quick start",
    "Costs",
    "Codex",
    "Updating",
    "Credits",
]


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())


def _package_json() -> dict:
    return json.loads((ROOT / "package.json").read_text())


def _dep_names(specs: list[str]) -> set[str]:
    """Normalised distribution names from a list of PEP 508 strings."""
    names = set()
    for spec in specs:
        found = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", spec)
        assert found, f"cannot read a package name from {spec!r}"
        names.add(re.sub(r"[-_.]+", "-", found.group(0)).lower())
    return names


def _git_ignored(path: str) -> bool:
    """True when the .gitignore patterns ignore `path`. The index is not consulted."""
    result = subprocess.run(["git", "check-ignore", "-q", "--no-index", path], cwd=ROOT)
    if result.returncode == 128:
        pytest.skip("not a git checkout")
    return result.returncode == 0


# --- secrets -----------------------------------------------------------------


def test_env_example_names_only():
    lines = [line.strip() for line in (ROOT / ".env.example").read_text().splitlines()]
    entries = [line for line in lines if line and not line.startswith("#")]
    for line in entries:
        assert re.fullmatch(r"[A-Z][A-Z0-9_]*=", line), f"not a bare NAME= line: {line!r}"
    names = [line[:-1] for line in entries]
    assert len(names) == len(set(names)), "a name is listed twice"
    assert set(names) == ENV_NAMES


def test_env_is_ignored():
    assert _git_ignored(".env")


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.local",
        ".venv/bin/python",
        "node_modules/hyperframes/package.json",
        "me/profile.json",
        "work/demo/final.mp4",
        "research/runs/2026-01-01/ideas.json",
        "models/matting.pth",
        "kit/models/rvm_mobilenetv3.pth",
        ".claude/skills/ui-story/models/readme.txt",
        "weights/matting.pt",
        "face_landmarker.onnx",
        "kit/hand_landmarker.tflite",
        "weights/model.safetensors",
        "kit/vendor/gsap.min.js",
        ".DS_Store",
        "docs/.DS_Store",
    ],
)
def test_private_and_fetched_paths_are_ignored(path):
    assert _git_ignored(path), f"{path} should be ignored"


@pytest.mark.parametrize(
    "path",
    [
        ".env.example",
        "me.example/profile.json",
        "me.example/brand/tokens.css",
        "kit/__init__.py",
        "kit/research/__init__.py",
        "kit/fonts/inter/OFL.txt",
        "kit/schema/profile.schema.json",
        ".claude/skills/research/SKILL.md",
        "bin/fetch_gsap.sh",
    ],
)
def test_shipped_paths_are_not_ignored(path):
    # kit/research/ and the research skill share a name with the creator's research/ folder.
    assert not _git_ignored(path), f"{path} must stay tracked"


# --- pins and dependencies ---------------------------------------------------


def test_python_pinned():
    assert _pyproject()["project"]["requires-python"] == ">=3.11,<3.12"


def test_hyperframes_pinned():
    version = _package_json()["devDependencies"]["hyperframes"]
    assert "^" not in version and "~" not in version
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), f"not an exact version: {version!r}"


def test_third_party_records_the_hyperframes_pin():
    version = _package_json()["devDependencies"]["hyperframes"]
    text = (ROOT / "THIRD_PARTY.md").read_text()
    assert "hyperframes" in text.lower()
    assert version in text, "THIRD_PARTY.md must name the pinned version"


def test_third_party_says_the_kit_turns_hyperframes_telemetry_off():
    section = (ROOT / "THIRD_PARTY.md").read_text().split("## HyperFrames", 1)[1].split("\n## ", 1)[0]
    assert "telemetry off" in section
    assert "HYPERFRAMES_NO_TELEMETRY" in section and "DO_NOT_TRACK" in section


def test_dependency_groups_declared():
    data = _pyproject()
    assert _dep_names(data["project"]["dependencies"]) == {"requests", "jsonschema"}
    groups = data["dependency-groups"]
    assert _dep_names(groups["dev"]) >= {"pytest"}
    assert _dep_names(groups["video"]) >= {
        "numpy",
        "pillow",
        "opencv-python-headless",
        "num2words",
        "scipy",
        "faster-whisper",
        "whisperx",
        "torch",
        "mediapipe",
        "playwright",
        "mlx-whisper",
    }
    assert _dep_names(groups["character"]) >= {
        "numpy",
        "pillow",
        "scipy",
        "scikit-image",
        "scikit-learn",
    }
    assert _dep_names(groups["research"]) >= {"yt-dlp"}


def test_mlx_whisper_is_apple_silicon_only():
    specs = [s for s in _pyproject()["dependency-groups"]["video"] if _dep_names([s]) == {"mlx-whisper"}]
    assert len(specs) == 1
    assert "sys_platform == 'darwin'" in specs[0]
    assert "platform_machine == 'arm64'" in specs[0]


def test_pytest_markers_registered(pytestconfig):
    registered = {line.split(":")[0].split("(")[0].strip() for line in pytestconfig.getini("markers")}
    assert {"mac", "heavy"} <= registered


# --- files -------------------------------------------------------------------


def test_license_holder():
    text = (ROOT / "LICENSE").read_text()
    # The holder is written backwards so the name appears in plain text only in LICENSE.
    holder = "evE drahciR"[::-1]
    assert f"Copyright (c) 2026 {holder}" in text.splitlines()
    assert text.startswith("MIT License")
    assert "Permission is hereby granted, free of charge" in text
    assert 'THE SOFTWARE IS PROVIDED "AS IS"' in text


def test_readme_has_the_planned_headings():
    text = (ROOT / "README.md").read_text()
    found = [h for h in re.findall(r"^## (.+)$", text, re.M) if h in README_HEADINGS]
    assert found == README_HEADINGS


def test_every_relative_link_in_the_readme_leads_to_a_file():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    targets = [t for t in re.findall(r"\]\(([^)\s]+)\)", text) if not t.startswith(("http://", "https://", "#"))]
    assert "docs/media/ui-story-layouts.svg" in targets, "the README shows the UI story layouts"
    for target in targets:
        assert (ROOT / target).is_file(), f"README.md links to {target}, which is not in the repo"


def test_readme_says_how_to_start_and_carries_the_credit_line_once():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    sub_headings = re.findall(r"^### (.+)$", text, re.M)
    assert "Clone it, don't fork it" in sub_headings and "This repo takes no pull requests" in sub_headings
    assert "git clone https://github.com/" in text and 'say "set me up"' in text
    assert "Use this template" in text and "npm ci" in text
    # The holder and handle are written backwards so they appear in plain text only in README.md and LICENSE.
    holder, handle = "evE drahciR"[::-1], "ia.elbissopehtfotra"[::-1]
    credit = f"Built by {holder} ([@{handle}](https://www.instagram.com/{handle}/))."
    assert text.splitlines().count(credit) == 1


def _readme_section(heading: str) -> str:
    """The README text from `heading` up to the next heading of the same or a higher level."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    level = heading.split(" ", 1)[0]
    start = text.index(heading + "\n")
    rest = text[start + len(heading) :]
    found = re.search(rf"^#{{1,{len(level)}}} ", rest, re.M)
    return heading + (rest[: found.start()] if found else rest)


def test_readme_says_pull_requests_are_off_for_this_repo_and_why():
    section = _readme_section("### This repo takes no pull requests")
    assert "Pull requests are turned off for this repository" in section
    assert "Clone it and make it yours" in section
    assert "cannot turn pull requests off" not in (ROOT / "README.md").read_text(encoding="utf-8")


def test_readme_not_included_names_no_other_creator_and_says_where_setup_stops():
    section = _readme_section("## Not included")
    assert "- other edit styles and script methods, clips, plain motion graphics, carousels and thumbnails\n" in section
    assert "'s edit style" not in section, "no style is named after a creator"
    assert "- Windows, Linux and Intel Macs: setup stops on them\n" in section
    assert "best effort" not in (ROOT / "README.md").read_text(encoding="utf-8")
    assert "setup stops on any other computer" in _readme_section("## What you need")


def test_readme_says_what_a_creator_gets_without_zernio_and_where_yt_dlp_comes_from():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "the MP4 and one file with each platform's caption" in text, "the kit writes one manual.md per video"
    assert "yt-dlp with Homebrew" not in text, "setup installs yt-dlp with the Python tools"


def test_readme_quick_start_covers_a_new_mac_and_opening_the_folder():
    section = _readme_section("## Quick start")
    assert "command line developer tools" in section and "Say yes" in section, "git on a new Mac asks for them first"
    assert "`claude`" in section and "`codex`" in section, "how to open the folder in each agent"


def test_readme_says_how_to_get_each_agent_before_the_clone():
    """The commands and pages come from each vendor's own install docs. A new creator installs and signs in to
    the agent first, and types "set me up" into it rather than into Terminal."""
    before_clone = _readme_section("## Quick start").split("```bash", 1)[0]
    assert "`curl -fsSL https://claude.ai/install.sh | bash`" in before_clone
    assert "(https://code.claude.com/docs/en/setup)" in before_clone and "browser prompts" in before_clone
    assert "`curl -fsSL https://chatgpt.com/codex/install.sh | sh`" in before_clone
    assert "(https://learn.chatgpt.com/docs/codex/cli)" in before_clone and "Sign in with ChatGPT" in before_clone
    assert '"set me up"' in before_clone and "not into Terminal" in before_clone


def test_readme_names_the_install_steps_no_clean_mac_has_tested():
    section = _readme_section("### How the install is tested")
    untested = next(line for line in section.splitlines() if "clean Mac has tested" in line)
    assert "installing Homebrew itself" in untested
    assert "`brew install uv`" in untested and "`brew install node`" in untested


def test_readme_costs_say_the_elevenlabs_free_plan_has_no_commercial_licence():
    row = next(line for line in _readme_section("## Costs").splitlines() if line.startswith("| ElevenLabs"))
    assert "free plan includes no commercial licence" in row and "need a paid plan" in row, row


def test_readme_codex_section_explains_hooks_and_the_sandbox_in_plain_words():
    section = _readme_section("## Codex")
    assert "The kit sets up no hooks for Codex" in section
    assert "runs no hooks" not in section
    assert "sandbox" in section and "internet" in section and "Say yes when it asks" in section
    lowered = section.lower()
    for words in ("full access", "full-access", "danger", "bypass", "yolo"):
        assert words not in lowered, f"the README never steers creators to {words!r}"


def test_fetch_gsap_targets_kit_vendor(tmp_path):
    script = ROOT / "bin" / "fetch_gsap.sh"
    assert script.stat().st_mode & 0o111, "bin/fetch_gsap.sh must be executable"
    assert "brand/vendor" not in script.read_text()

    # Offline: with the file already in place the script reports it and stops.
    (tmp_path / "bin").mkdir()
    copy = tmp_path / "bin" / "fetch_gsap.sh"
    shutil.copy(script, copy)
    vendor = tmp_path / "kit" / "vendor"
    vendor.mkdir(parents=True)
    (vendor / "gsap.min.js").write_text("// placeholder\n")
    result = subprocess.run(["bash", str(copy)], capture_output=True, text=True, check=True)
    assert "kit/vendor/gsap.min.js" in result.stdout
    assert "Nothing to do" in result.stdout
