"""bin/doctor.py: the machine check, and bin/fetch_gsap.sh, the fix for one of its rows.

The checks run against a fake environment, so every row can be driven into every state without
touching this Mac. A few tests use the real probe on a throwaway folder. One test, marked mac,
runs the whole thing on the machine the tests are running on.
"""

import ast
import base64
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "bin" / "doctor.py"
FETCH = ROOT / "bin" / "fetch_gsap.sh"
EM_DASH, EN_DASH = chr(0x2014), chr(0x2013)  # written as code points so this file holds no dash
MARKER = "TO" + "DO"  # an unfinished marker, spelled in two parts so it never appears here

PIN = "0.8.68"
BREW_INSTALL = '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
BREW_SHELLENV = (
    "echo 'eval \"$(/opt/homebrew/bin/brew shellenv)\"' >> ~/.zprofile && "
    'eval "$(/opt/homebrew/bin/brew shellenv)"'
)
SYNC = "uv sync --group video --group research --inexact"
CLT = "xcode-select --install"
CHROMIUM = "uv run python -m playwright install chromium"
ENV_EXAMPLE = "# Names only.\nAPIFY_TOKEN=\nOPENAI_API_KEY=\nGEMINI_API_KEY=\nELEVENLABS_API_KEY=\nZERNIO_API_KEY=\n"
KEYS = ["APIFY_TOKEN", "OPENAI_API_KEY", "GEMINI_API_KEY", "ELEVENLABS_API_KEY", "ZERNIO_API_KEY"]
ENCODERS = " V....D h264_videotoolbox    VideoToolbox H.264 Encoder (codec h264)\n V....D libx264 x264\n"
FILTERS = " ... scale             V->V       Scale the input video size.\n .S. zscale            V->V       Apply resizing, colorspace and bit depth conversion.\n T.. tonemap           V->V       Conversion to/from different dynamic ranges.\n"
FILTERS_WITHOUT_ZSCALE = " ... scale             V->V       Scale the input video size.\n T.. tonemap           V->V       Conversion to/from different dynamic ranges. Not zscale.\n"
FULL_FFMPEG = "brew install ffmpeg-full && brew unlink ffmpeg && brew link --force ffmpeg-full"
FULL_FFMPEG_FRESH = "brew install ffmpeg-full && brew link --force ffmpeg-full"
GOOD_REPORT = {
    "mlx_whisper": "ok",
    "faster_whisper": "ok",
    "whisperx": "ok",
    "torch": "ok",
    "yt_dlp": "ok",
    "playwright": "ok",
    "chromium": "ok",
}


# --- the script and a fake environment -------------------------------------------------------------------


@pytest.fixture
def doctor():
    spec = importlib.util.spec_from_file_location("doctor_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fake(doctor):
    """A factory for a fake Mac that passes every check. Change its attributes to break one."""

    class FakeProbe(doctor.Probe):
        def __init__(self):
            super().__init__(Path("/nowhere"))
            self.os_name = "Darwin"
            self.arch = "arm64"
            self.arm_hardware = True
            self.programs = {
                "python3": "/usr/bin/python3",
                "brew": "/opt/homebrew/bin/brew",
                "ffmpeg": "/opt/homebrew/bin/ffmpeg",
                "uv": "/opt/homebrew/bin/uv",
                "node": "/opt/homebrew/bin/node",
                "npx": "/opt/homebrew/bin/npx",
            }
            self.outputs = {
                "python3 --version": (0, "Python 3.9.6\n"),
                "ffmpeg -hide_banner -encoders": (0, ENCODERS),
                "ffmpeg -hide_banner -filters": (0, FILTERS),
                "uv python find 3.11": (0, "/opt/homebrew/bin/python3.11\n"),
                "node --version": (0, "v22.11.0\n"),
                "npx --no-install hyperframes --version": (0, PIN + "\n"),
            }
            self.free = 120.0
            self.sizes = {"kit/vendor/gsap.min.js": 4096, "node_modules/hyperframes/package.json": 900}
            self.present = set()
            self.texts = {
                "package.json": json.dumps({"devDependencies": {"hyperframes": PIN}}),
                ".env.example": ENV_EXAMPLE,
            }
            self.env = {}
            self.python = "/nowhere/.venv/bin/python"
            self.report = dict(GOOD_REPORT)
            self.calls = []

        def system(self):
            return self.os_name

        def machine(self):
            return self.arch

        def apple_silicon_hardware(self):
            return self.arm_hardware

        def which(self, name):
            return self.programs.get(name)

        def exists(self, path):
            return path in self.present

        def run(self, argv, timeout=30):
            key = " ".join([Path(argv[0]).name, *argv[1:]])
            self.calls.append(key)
            return self.outputs.get(key)

        def free_gb(self):
            return self.free

        def file_size(self, relative):
            return self.sizes.get(relative, 0)

        def read_text(self, relative):
            return self.texts.get(relative)

        def environ(self, name):
            return self.env.get(name)

        def kit_python(self):
            return self.python

        def python_report(self):
            return self.report

    return FakeProbe


def rows_of(doctor, probe):
    return doctor.collect(probe)


def find(rows, words):
    found = [row for row in rows if words.lower() in row["check"].lower()]
    assert len(found) == 1, f"{words!r} matched {[row['check'] for row in found]}"
    return found[0]


# --- the rows ---------------------------------------------------------------------------------------------


def test_doctor_rows_shape(doctor, fake):
    rows = rows_of(doctor, fake())

    assert isinstance(rows, list) and rows
    for row in rows:
        assert set(row) == {"check", "status", "fix"}
        assert row["status"] in {"ok", "missing", "warn"}
        assert isinstance(row["check"], str) and row["check"].strip()
        assert isinstance(row["fix"], str)
        assert EM_DASH not in row["check"] + row["fix"] and EN_DASH not in row["check"] + row["fix"]
    assert len({row["check"] for row in rows}) == len(rows), "a check is listed twice"
    # a row that passes has nothing to fix, and a row that does not says what to do
    for row in rows:
        assert (row["fix"] == "") == (row["status"] == "ok"), row


def test_a_healthy_mac_passes_every_required_check(doctor, fake):
    rows = rows_of(doctor, fake())

    for words in (
        "macOS",
        "python3",
        "Homebrew",
        "ffmpeg",
        "zscale",
        "uv",
        "Python 3.11",
        "Node 22",
        "HyperFrames",
        "Chromium",
        "GSAP",
        "Transcription",
        "WhisperX",
        "torch",
        "yt-dlp",
        "Free disk",
    ):
        assert find(rows, words)["status"] == "ok", words
    assert "missing" not in {row["status"] for row in rows}


def test_the_hyperframes_row_names_the_pinned_version(doctor, fake):
    assert PIN in find(rows_of(doctor, fake()), "HyperFrames")["check"]


@pytest.mark.parametrize("section", ["devDependencies", "dependencies"])
@pytest.mark.parametrize("written, pin", [("0.9.1", "0.9.1"), ("^0.9.1", "0.9.1"), ("~0.9.1", "0.9.1")])
def test_the_hyperframes_pin_is_read_from_either_dependency_section(doctor, fake, section, written, pin):
    probe = fake()
    probe.texts["package.json"] = json.dumps({"name": "x", section: {"hyperframes": written}})
    probe.outputs["npx --no-install hyperframes --version"] = (0, pin + "\n")
    row = find(rows_of(doctor, probe), "HyperFrames")
    assert (row["status"], row["check"]) == ("ok", f"HyperFrames CLI {pin}")


@pytest.mark.parametrize("packages", [{}, {"dependencies": {"left-pad": "1.0.0"}}, {"devDependencies": []}, {"dependencies": {"hyperframes": 7}}])
def test_a_package_json_that_does_not_pin_hyperframes_gives_a_plain_row(doctor, fake, packages):
    probe = fake()
    probe.texts["package.json"] = json.dumps(packages)
    row = find(rows_of(doctor, probe), "HyperFrames")
    assert (row["status"], row["fix"]) == ("missing", "npm ci")
    assert row["check"] == "HyperFrames CLI at the pinned version"


def test_a_package_json_that_cannot_be_read_gives_the_same_plain_row(doctor, fake):
    probe = fake()
    probe.texts.pop("package.json")
    assert find(rows_of(doctor, probe), "HyperFrames")["check"] == "HyperFrames CLI at the pinned version"
    probe.texts["package.json"] = "not json"
    assert find(rows_of(doctor, probe), "HyperFrames")["status"] == "missing"


STUB_NOTE = (
    "xcode-select: note: No developer tools were found, requesting install.\n"
    "If developer tools are located at a non-default location on disk, use `sudo xcode-select --switch path/to/Xcode.app` "
    "to specify the Xcode that you wish to use for command line developer tools, and cancel the installation dialog.\n"
)

COMMAND_FIXES = [
    pytest.param("python3", lambda p: p.outputs.update({"python3 --version": (1, STUB_NOTE)}), CLT, id="python3-stub"),
    pytest.param("python3", lambda p: p.outputs.update({"python3 --version": None}), CLT, id="python3-will-not-start"),
    pytest.param("python3", lambda p: p.outputs.update({"python3 --version": (0, "")}), CLT, id="python3-says-nothing"),
    pytest.param("python3", lambda p: p.programs.pop("python3"), CLT, id="no-python3"),
    pytest.param("Homebrew", lambda p: p.programs.pop("brew"), BREW_INSTALL, id="no-homebrew"),
    pytest.param(
        "Homebrew",
        lambda p: (p.programs.pop("brew"), p.present.add("/opt/homebrew/bin/brew")),
        BREW_SHELLENV,
        id="homebrew-not-on-the-path",
    ),
    pytest.param("ffmpeg", lambda p: p.programs.pop("ffmpeg"), "brew install ffmpeg", id="no-ffmpeg"),
    pytest.param(
        "ffmpeg",
        lambda p: p.outputs.update({"ffmpeg -hide_banner -encoders": (0, " V....D libx264 x264\n")}),
        "brew reinstall ffmpeg",
        id="homebrew-ffmpeg-without-videotoolbox",
    ),
    pytest.param(
        "ffmpeg",
        lambda p: p.outputs.update({"ffmpeg -hide_banner -encoders": (1, "")}),
        "brew reinstall ffmpeg",
        id="ffmpeg-that-fails",
    ),
    pytest.param("uv", lambda p: p.programs.pop("uv"), "brew install uv", id="no-uv"),
    pytest.param(
        "Python 3.11",
        lambda p: p.outputs.update({"uv python find 3.11": (2, "error: no interpreter found\n")}),
        "uv python install 3.11",
        id="no-python-311",
    ),
    pytest.param(
        "Python 3.11", lambda p: p.programs.pop("uv"), "uv python install 3.11", id="python-311-without-uv"
    ),
    pytest.param("Node 22", lambda p: p.programs.pop("node"), "brew install node", id="no-node"),
    pytest.param(
        "Node 22",
        lambda p: p.outputs.update({"node --version": (0, "v20.18.0\n")}),
        "brew upgrade node",
        id="node-20",
    ),
    pytest.param(
        "Node 22",
        lambda p: p.outputs.update({"node --version": (0, "v21.9.0\n")}),
        "brew upgrade node",
        id="node-21",
    ),
    pytest.param(
        "HyperFrames",
        lambda p: p.outputs.update({"npx --no-install hyperframes --version": (0, "0.8.1\n")}),
        "npm ci",
        id="hyperframes-wrong-version",
    ),
    pytest.param(
        "HyperFrames",
        lambda p: p.outputs.update({"npx --no-install hyperframes --version": (1, "npx canceled\n")}),
        "npm ci",
        id="hyperframes-that-will-not-start",
    ),
    pytest.param(
        "HyperFrames",
        lambda p: p.sizes.pop("node_modules/hyperframes/package.json"),
        "npm ci",
        id="hyperframes-not-installed",
    ),
    pytest.param(
        "HyperFrames", lambda p: p.programs.pop("npx"), "npm ci", id="hyperframes-without-npx"
    ),
    pytest.param(
        "Chromium",
        lambda p: p.report.update({"chromium": "missing"}),
        CHROMIUM,
        id="no-chromium",
    ),
    pytest.param("GSAP", lambda p: p.sizes.pop("kit/vendor/gsap.min.js"), "bash bin/fetch_gsap.sh", id="no-gsap"),
    pytest.param(
        "GSAP", lambda p: p.sizes.update({"kit/vendor/gsap.min.js": 0}), "bash bin/fetch_gsap.sh", id="empty-gsap"
    ),
    pytest.param("WhisperX", lambda p: p.report.update({"whisperx": "missing"}), SYNC, id="no-whisperx"),
    pytest.param("WhisperX", lambda p: p.report.update({"whisperx": "broken"}), SYNC, id="broken-whisperx"),
    pytest.param("torch", lambda p: p.report.update({"torch": "missing"}), SYNC, id="no-torch"),
    pytest.param("yt-dlp", lambda p: p.report.update({"yt_dlp": "missing"}), SYNC, id="no-yt-dlp"),
]


@pytest.mark.parametrize("words, break_it, fix", COMMAND_FIXES)
def test_a_failed_check_is_missing_and_names_the_exact_fix(doctor, fake, words, break_it, fix):
    probe = fake()
    break_it(probe)

    row = find(rows_of(doctor, probe), words)

    assert row["status"] == "missing"
    assert row["fix"] == fix


def test_losing_uv_fails_only_the_rows_that_need_it(doctor, fake):
    probe = fake()
    probe.programs.pop("uv")
    rows = rows_of(doctor, probe)
    failed = sorted(row["check"] for row in rows if row["status"] == "missing")
    assert failed == sorted([find(rows, "uv")["check"], find(rows, "Python 3.11")["check"]])


def test_the_macos_row_needs_a_mac_with_apple_silicon(doctor, fake):
    for change in (
        lambda p: setattr(p, "os_name", "Linux"),
        lambda p: (setattr(p, "arch", "x86_64"), setattr(p, "arm_hardware", False)),
    ):
        probe = fake()
        change(probe)
        row = find(rows_of(doctor, probe), "macOS")
        assert row["status"] == "missing"
        assert "Apple silicon" in row["fix"]


def test_a_terminal_running_under_rosetta_gets_a_specific_fix(doctor, fake):
    probe = fake()
    probe.arch, probe.arm_hardware = "x86_64", True
    row = find(rows_of(doctor, probe), "macOS")
    assert row["status"] == "missing"
    assert "Rosetta" in row["fix"]


def test_the_python3_row_comes_right_after_the_mac_and_before_homebrew(doctor, fake):
    names = [row["check"] for row in rows_of(doctor, fake())]
    python3 = names.index(find(rows_of(doctor, fake()), "python3")["check"])
    assert python3 == names.index("macOS on Apple silicon") + 1
    assert python3 < names.index("Homebrew"), "Homebrew's own installer needs the command line developer tools"


def test_the_python3_row_asks_python3_for_its_version_and_nothing_else(doctor, fake):
    probe = fake()
    rows_of(doctor, probe)
    assert [call for call in probe.calls if call.startswith("python3")] == ["python3 --version"]


def test_a_working_python3_from_anywhere_passes(doctor, fake):
    for path, answer in (("/opt/homebrew/bin/python3", "Python 3.14.0\n"), ("/usr/local/bin/python3", "Python 3.12.7\n")):
        probe = fake()
        probe.programs["python3"] = path
        probe.outputs["python3 --version"] = (0, answer)
        assert find(rows_of(doctor, probe), "python3")["status"] == "ok", path


def test_ffmpeg_from_somewhere_else_is_not_fixed_by_brew_alone(doctor, fake):
    probe = fake()
    probe.programs["ffmpeg"] = "/usr/local/bin/ffmpeg"
    probe.outputs["ffmpeg -hide_banner -encoders"] = (0, " V....D libx264 x264\n")
    row = find(rows_of(doctor, probe), "ffmpeg")
    assert row["status"] == "missing"
    assert row["fix"].startswith("brew install ffmpeg")
    assert "/usr/local/bin/ffmpeg" in row["fix"]


# --- HDR phone videos: the zscale filter -------------------------------------------------------------------
# Homebrew's plain ffmpeg is built without the zimg library, so it has no zscale filter and an HDR take cannot
# be tone-mapped. The full build has it. Turning HDR video off on the phone avoids the problem, so the row warns.


def test_the_zscale_row_passes_when_ffmpeg_has_the_filter(doctor, fake):
    probe = fake()
    row = find(rows_of(doctor, probe), "zscale")
    assert row == {"check": "HDR tone mapping (zscale filter)", "status": "ok", "fix": ""}
    assert "ffmpeg -hide_banner -filters" in probe.calls


def test_the_zscale_row_warns_with_the_full_build_when_the_filter_is_missing(doctor, fake):
    probe = fake()
    probe.outputs["ffmpeg -hide_banner -filters"] = (0, FILTERS_WITHOUT_ZSCALE)
    row = find(rows_of(doctor, probe), "zscale")
    assert row["status"] == "warn"
    assert row["fix"] == FULL_FFMPEG


def test_only_a_filter_called_zscale_counts(doctor, fake):
    # the word in a description, or a longer filter name, is not the filter
    for listing in (FILTERS_WITHOUT_ZSCALE, " ... zscale_hw          V->V       A made-up filter.\n"):
        probe = fake()
        probe.outputs["ffmpeg -hide_banner -filters"] = (0, listing)
        assert find(rows_of(doctor, probe), "zscale")["status"] == "warn", listing


@pytest.mark.parametrize("answer", [(1, "ffmpeg: broken\n"), (0, ""), None], ids=["fails", "empty", "will-not-start"])
def test_an_ffmpeg_that_cannot_list_its_filters_gets_the_same_warning(doctor, fake, answer):
    probe = fake()
    probe.outputs["ffmpeg -hide_banner -filters"] = answer
    row = find(rows_of(doctor, probe), "zscale")
    assert (row["status"], row["fix"]) == ("warn", FULL_FFMPEG)


def test_the_zscale_row_is_a_warning_and_never_stops_the_run(doctor, fake, capsys):
    probe = fake()
    probe.outputs["ffmpeg -hide_banner -filters"] = (0, FILTERS_WITHOUT_ZSCALE)
    assert doctor.main([], probe=probe) == 0
    out = capsys.readouterr().out
    assert re.search(r"^warn\s+HDR tone mapping \(zscale filter\)\s+" + re.escape(FULL_FFMPEG) + "$", out, re.M)
    assert "Nothing required is missing. The rows marked warn are worth a look" in out
    assert doctor.main(["--json"], probe=probe) == 0


def test_without_ffmpeg_the_zscale_row_offers_the_full_build_to_install_first(doctor, fake):
    probe = fake()
    probe.programs.pop("ffmpeg")
    rows = rows_of(doctor, probe)
    assert find(rows, "videotoolbox")["fix"] == "brew install ffmpeg", "the ffmpeg row still says how to get a working one"
    row = find(rows, "zscale")
    assert (row["status"], row["fix"]) == ("warn", FULL_FFMPEG_FRESH), "nothing to unlink yet"
    assert not any("-filters" in call for call in probe.calls), "no ffmpeg, so none was asked"


def test_an_ffmpeg_from_somewhere_else_is_named_in_the_zscale_fix(doctor, fake):
    probe = fake()
    probe.programs["ffmpeg"] = "/usr/local/bin/ffmpeg"
    probe.outputs["ffmpeg -hide_banner -filters"] = (0, FILTERS_WITHOUT_ZSCALE)
    row = find(rows_of(doctor, probe), "zscale")
    assert row["status"] == "warn"
    assert row["fix"].startswith(FULL_FFMPEG_FRESH)
    assert "/usr/local/bin/ffmpeg" in row["fix"] and "open a new terminal" in row["fix"]
    assert "brew unlink" not in row["fix"], "that ffmpeg is not Homebrew's, so unlinking would change nothing"


def test_the_zscale_row_sits_right_after_the_ffmpeg_row(doctor, fake):
    names = [row["check"] for row in rows_of(doctor, fake())]
    assert names.index("HDR tone mapping (zscale filter)") == names.index("ffmpeg with h264_videotoolbox") + 1


def test_the_full_build_commands_are_in_the_fix_list(doctor):
    assert FULL_FFMPEG in doctor.FIX_COMMANDS and FULL_FFMPEG_FRESH in doctor.FIX_COMMANDS


def test_node_22_is_enough_and_so_is_anything_newer(doctor, fake):
    for version in ("v22.0.0", "v22.11.0", "v25.2.1", "v30.1.0"):
        probe = fake()
        probe.outputs["node --version"] = (0, version + "\n")
        assert find(rows_of(doctor, probe), "Node 22")["status"] == "ok", version


def test_transcription_needs_only_one_of_the_two_engines(doctor, fake):
    for have in ({"mlx_whisper": "ok", "faster_whisper": "missing"}, {"mlx_whisper": "missing", "faster_whisper": "ok"}):
        probe = fake()
        probe.report.update(have)
        assert find(rows_of(doctor, probe), "Transcription")["status"] == "ok", have
    probe = fake()
    probe.report.update({"mlx_whisper": "missing", "faster_whisper": "broken"})
    row = find(rows_of(doctor, probe), "Transcription")
    assert (row["status"], row["fix"]) == ("missing", SYNC)


def test_every_python_row_is_missing_when_the_environment_does_not_exist_yet(doctor, fake):
    probe = fake()
    probe.python = None
    rows = rows_of(doctor, probe)
    for words in ("Chromium", "Transcription", "WhisperX", "torch", "yt-dlp"):
        row = find(rows, words)
        assert (row["status"], row["fix"]) == ("missing", SYNC), words
    # the rows that do not depend on that environment are unaffected
    for words in ("Homebrew", "ffmpeg", "uv", "Node 22", "GSAP"):
        assert find(rows, words)["status"] == "ok", words


def test_a_report_that_could_not_be_collected_counts_as_missing(doctor, fake):
    probe = fake()
    probe.report = None
    rows = rows_of(doctor, probe)
    for words in ("Chromium", "Transcription", "WhisperX", "torch", "yt-dlp"):
        assert (find(rows, words)["status"], find(rows, words)["fix"]) == ("missing", SYNC), words


def test_chromium_is_only_fixed_by_the_installer_when_the_playwright_package_is_there(doctor, fake):
    probe = fake()
    probe.report.update({"playwright": "missing", "chromium": "missing"})
    row = find(rows_of(doctor, probe), "Chromium")
    assert (row["status"], row["fix"]) == ("missing", SYNC)


def test_one_sync_command_covers_every_gap_and_the_rows_that_pass_stay_ok(doctor, fake):
    probe = fake()
    probe.report.update({"whisperx": "missing", "yt_dlp": "missing"})
    rows = rows_of(doctor, probe)
    assert find(rows, "WhisperX")["fix"] == SYNC
    assert find(rows, "yt-dlp")["fix"] == SYNC
    assert find(rows, "torch")["status"] == "ok"


def test_the_sync_fixes_never_remove_groups_installed_earlier(doctor, fake):
    probe = fake()
    probe.python = None
    for row in rows_of(doctor, probe):
        if row["fix"].startswith("uv sync"):
            assert "--inexact" in row["fix"].split()


# --- free disk --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "free, status",
    [(500.0, "ok"), (20.0, "ok"), (19.99, "warn"), (12.3, "warn"), (5.0, "warn"), (4.99, "missing"), (0.2, "missing")],
)
def test_free_disk_warns_under_20_and_fails_under_5(doctor, fake, free, status):
    probe = fake()
    probe.free = free
    row = find(rows_of(doctor, probe), "Free disk")
    assert row["status"] == status
    if status == "ok":
        assert row["fix"] == ""
    else:
        assert "5 GB" in row["fix"]


def test_a_warning_does_not_fail_the_run_but_a_missing_row_does(doctor, fake, capsys):
    probe = fake()
    probe.free = 12.0
    assert doctor.main(["--json"], probe=probe) == 0
    probe.free = 2.0
    assert doctor.main(["--json"], probe=probe) == 1
    capsys.readouterr()


# --- optional keys ----------------------------------------------------------------------------------------

FOOTER_START = "Optional keys not set: "
FOOTER_END = ". Add them to .env when you want those features."


def table_text(doctor, probe, capsys) -> str:
    doctor.main([], probe=probe)
    return capsys.readouterr().out


def footer_names(text: str):
    """The key names on the footer line under the table, or None when there is no footer."""
    lines = [line for line in text.splitlines() if line.startswith(FOOTER_START)]
    assert len(lines) <= 1, "one footer line, never one line for each key"
    if not lines:
        return None
    assert lines[0].endswith(FOOTER_END)
    return lines[0][len(FOOTER_START) : -len(FOOTER_END)].split(", ")


def test_optional_keys_are_one_footer_line_and_never_rows(doctor, fake, capsys):
    probe = fake()
    rows = rows_of(doctor, probe)

    assert [row for row in rows if any(name in row["check"] + row["fix"] for name in KEYS)] == []
    assert "optional" not in " ".join(row["check"] + row["fix"] for row in rows).lower()
    assert footer_names(table_text(doctor, probe, capsys)) == KEYS
    assert doctor.main(["--json"], probe=probe) == 0, "a key that is not set never fails the run"
    assert json.loads(capsys.readouterr().out) == rows, "JSON holds the rows and nothing about keys"


def test_the_footer_sits_under_the_table_and_above_the_summary(doctor, fake, capsys):
    lines = table_text(doctor, fake(), capsys).splitlines()
    footer = next(i for i, line in enumerate(lines) if line.startswith(FOOTER_START))
    assert lines[footer - 1] == "" and lines[footer + 1] == ""
    assert lines[footer - 2].startswith("warn") is False
    assert any(line.startswith("ok") for line in lines[:footer])
    assert lines[-1] == "Everything the kit needs is installed.", "a key that is not set does not change the summary"


def test_the_footer_lists_only_the_keys_that_are_not_set_and_never_a_value(doctor, fake, capsys):
    secret = "fake-value-for-tests"
    probe = fake()
    probe.texts[".env"] = f"# keys\nAPIFY_TOKEN={secret}\nZERNIO_API_KEY=\nexport GEMINI_API_KEY='{secret}-two'\n"

    text = table_text(doctor, probe, capsys)
    doctor.main(["--json"], probe=probe)
    text += capsys.readouterr().out

    assert footer_names(text.split("[")[0]) == ["OPENAI_API_KEY", "ELEVENLABS_API_KEY", "ZERNIO_API_KEY"]
    assert secret not in text


def test_a_key_in_the_environment_counts_too(doctor, fake, capsys):
    probe = fake()
    probe.env = {"OPENAI_API_KEY": "not-a-real-key", "ELEVENLABS_API_KEY": "   "}
    text = table_text(doctor, probe, capsys)
    assert footer_names(text) == ["APIFY_TOKEN", "GEMINI_API_KEY", "ELEVENLABS_API_KEY", "ZERNIO_API_KEY"]
    assert "not-a-real-key" not in text


def test_a_later_blank_line_in_dotenv_clears_an_earlier_value(doctor, fake, capsys):
    probe = fake()
    probe.texts[".env"] = "APIFY_TOKEN=fake-value-for-tests\nAPIFY_TOKEN=\n"
    assert "APIFY_TOKEN" in footer_names(table_text(doctor, probe, capsys))


def test_quoted_and_commented_values_are_read_like_the_kit_reads_them(doctor, fake, capsys):
    probe = fake()
    probe.texts[".env"] = 'APIFY_TOKEN=""\nOPENAI_API_KEY=   # nothing yet\nGEMINI_API_KEY="fake-value-for-tests"\n'
    names = footer_names(table_text(doctor, probe, capsys))
    assert "APIFY_TOKEN" in names and "OPENAI_API_KEY" in names
    assert "GEMINI_API_KEY" not in names


def test_when_every_key_is_set_there_is_no_footer(doctor, fake, capsys):
    probe = fake()
    probe.texts[".env"] = "".join(f"{name}=fake-value-for-tests\n" for name in KEYS)
    text = table_text(doctor, probe, capsys)
    assert footer_names(text) is None
    assert "Optional" not in text


def test_the_key_names_come_from_dotenv_example(doctor, fake, capsys):
    probe = fake()
    probe.texts[".env.example"] = "SOME_NEW_KEY=\nAPIFY_TOKEN=\n"
    assert footer_names(table_text(doctor, probe, capsys)) == ["SOME_NEW_KEY", "APIFY_TOKEN"]


def test_a_missing_dotenv_example_falls_back_to_the_five_known_names_and_stops_nothing(doctor, fake, capsys):
    probe = fake()
    probe.texts.pop(".env.example")
    text = table_text(doctor, probe, capsys)
    assert footer_names(text) == KEYS
    assert re.search(r"^ok\s+Homebrew$", text, re.M)


# --- the command ------------------------------------------------------------------------------------------


def test_json_prints_the_rows_and_only_the_rows(doctor, fake, capsys):
    probe = fake()
    probe.programs.pop("uv")

    code = doctor.main(["--json"], probe=probe)

    out = capsys.readouterr()
    assert code == 1
    assert json.loads(out.out) == rows_of(doctor, probe)
    assert out.err == ""


def test_the_table_lists_every_check_with_its_fix(doctor, fake, capsys):
    probe = fake()
    probe.programs.pop("ffmpeg")
    probe.free = 8.0

    code = doctor.main([], probe=probe)

    text = capsys.readouterr().out
    assert code == 1
    for row in rows_of(doctor, probe):
        assert row["check"] in text
        if row["fix"]:
            assert row["fix"] in text
    assert re.search(r"^missing\s+ffmpeg with h264_videotoolbox\s+brew install ffmpeg$", text, re.M)
    assert re.search(r"^warn\s+Free disk space\s+Heavy jobs need at least 5 GB free", text, re.M)
    assert re.search(r"^ok\s+Homebrew$", text, re.M), "a row that passes has no fix after it"
    assert "1 check needs fixing" in text
    assert "python3 bin/doctor.py again" in text
    assert footer_names(text) == KEYS
    assert "Traceback" not in text
    assert EM_DASH not in text and EN_DASH not in text


def test_the_summary_counts_the_problems(doctor, fake, capsys):
    probe = fake()
    probe.programs.pop("ffmpeg")
    probe.programs.pop("node")
    doctor.main([], probe=probe)
    assert "2 checks need fixing. Fix them in the order shown" in capsys.readouterr().out


def test_the_summary_when_only_notes_remain(doctor, fake, capsys):
    probe = fake()
    probe.free = 12.0
    assert doctor.main([], probe=probe) == 0
    last = capsys.readouterr().out.rstrip().splitlines()[-1]
    assert last == "Nothing required is missing. The rows marked warn are worth a look, but they do not stop you."


def test_the_summary_when_everything_is_there(doctor, fake, capsys):
    probe = fake()
    probe.texts[".env"] = "".join(f"{name}=fake-value-for-tests\n" for name in KEYS)
    assert doctor.main([], probe=probe) == 0
    assert capsys.readouterr().out.rstrip().splitlines()[-1] == "Everything the kit needs is installed."


def test_the_summary_ignores_keys_that_are_not_set(doctor, fake, capsys):
    assert doctor.main([], probe=fake()) == 0
    out = capsys.readouterr().out.rstrip().splitlines()
    assert out[-1] == "Everything the kit needs is installed."
    assert out[-3].startswith(FOOTER_START)


def test_an_unknown_option_is_refused_with_usage(doctor, fake, capsys):
    with pytest.raises(SystemExit) as stop:
        doctor.main(["--nonsense"], probe=fake())
    assert stop.value.code == 2
    assert "usage" in capsys.readouterr().err.lower()


def test_a_check_that_blows_up_becomes_a_row_not_a_traceback(doctor, fake, capsys):
    probe = fake()

    def boom(*args, **kwargs):
        raise RuntimeError("the disk went away")

    probe.free_gb = boom
    rows = rows_of(doctor, probe)
    row = find(rows, "Free disk")
    assert row["status"] == "missing"
    assert "could not run" in row["fix"]
    assert "the disk went away" not in row["fix"], "an exception message might carry a path or a value"
    assert doctor.main([], probe=probe) == 1
    assert "Traceback" not in capsys.readouterr().out


def test_the_checks_run_in_the_order_the_fixes_must_be_made(doctor, fake):
    names = [row["check"] for row in rows_of(doctor, fake())]
    order = ["macOS", "Homebrew", "ffmpeg", "uv", "Python 3.11", "Node", "HyperFrames", "Chromium"]
    positions = [next(i for i, n in enumerate(names) if word.lower() in n.lower()) for word in order]
    assert positions == sorted(positions)


def test_every_fix_command_is_in_the_onboarding_guide(doctor, fake):
    guide = (ROOT / "ONBOARDING.md").read_text(encoding="utf-8")
    for command in doctor.FIX_COMMANDS:
        assert command in guide, f"ONBOARDING.md does not show: {command}"
    probe = fake()
    probe.programs.clear()
    probe.python = None
    probe.sizes.clear()
    probe.os_name = "Linux"
    probe.free = 1.0
    rows = rows_of(doctor, probe)
    sentences = {find(rows, "macOS")["fix"], find(rows, "Free disk")["fix"]}
    for row in rows:
        if row["status"] == "missing" and row["fix"] not in sentences:
            assert row["fix"] in doctor.FIX_COMMANDS, row


# --- the real probe, on a throwaway folder ----------------------------------------------------------------


def test_the_real_probe_reads_files_under_its_root(doctor, tmp_path):
    (tmp_path / "kit" / "vendor").mkdir(parents=True)
    (tmp_path / "kit" / "vendor" / "gsap.min.js").write_text("// gsap")
    (tmp_path / ".env").write_text("APIFY_TOKEN=fake-value-for-tests\n")
    probe = doctor.Probe(tmp_path)

    assert probe.file_size("kit/vendor/gsap.min.js") == 7
    assert probe.file_size("kit/vendor/nothing.js") == 0
    assert probe.read_text(".env") == "APIFY_TOKEN=fake-value-for-tests\n"
    assert probe.read_text("package.json") is None
    assert probe.free_gb() > 0


def test_the_real_probe_runs_programs_and_reports_trouble_as_none(doctor, tmp_path):
    probe = doctor.Probe(tmp_path)
    assert probe.run(["sh", "-c", "echo hello; exit 3"]) == (3, "hello\n")
    assert probe.run(["a-program-that-does-not-exist-here"]) is None
    assert probe.run(["sleep", "5"], timeout=0.2) is None
    assert probe.which("a-program-that-does-not-exist-here") is None


def test_the_real_probe_finds_the_kit_environment(doctor, tmp_path, monkeypatch):
    monkeypatch.delenv("UV_PROJECT_ENVIRONMENT", raising=False)
    probe = doctor.Probe(tmp_path)
    assert probe.kit_python() is None

    python = tmp_path / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\n")
    python.chmod(0o755)
    assert probe.kit_python() == str(python)

    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "bin").mkdir(parents=True)
    (elsewhere / "bin" / "python").write_text("#!/bin/sh\n")
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(elsewhere))
    assert probe.kit_python() == str(elsewhere / "bin" / "python")
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "elsewhere")
    assert probe.kit_python() == str(elsewhere / "bin" / "python")


def write_python(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


def test_the_real_probe_reads_the_report_the_kit_python_prints(doctor, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("UV_PROJECT_ENVIRONMENT", raising=False)
    write_python(
        tmp_path / ".venv" / "bin" / "python",
        'echo "a warning from some package"\n'
        "echo 'KIT_DOCTOR_REPORT {\"torch\": \"ok\", \"whisperx\": \"broken\"}'\n"
        'echo "a trailing line"\n',
    )
    report = doctor.Probe(tmp_path).python_report()
    assert report == {"torch": "ok", "whisperx": "broken"}
    assert "Checking" in capsys.readouterr().err, "the slow part says so on stderr, never on stdout"


@pytest.mark.parametrize(
    "body",
    [
        "exit 1\n",
        'echo "nothing useful"\n',
        "echo 'KIT_DOCTOR_REPORT not json'\n",
        "echo 'KIT_DOCTOR_REPORT [1, 2]'\n",
    ],
    ids=["fails", "no-report", "bad-json", "not-a-group"],
)
def test_a_report_that_cannot_be_read_is_none(doctor, tmp_path, monkeypatch, body, capsys):
    monkeypatch.delenv("UV_PROJECT_ENVIRONMENT", raising=False)
    write_python(tmp_path / ".venv" / "bin" / "python", body)
    assert doctor.Probe(tmp_path).python_report() is None
    capsys.readouterr()


def test_the_real_probe_stays_quiet_when_asked_to(doctor, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("UV_PROJECT_ENVIRONMENT", raising=False)
    write_python(tmp_path / ".venv" / "bin" / "python", "echo 'KIT_DOCTOR_REPORT {\"torch\": \"ok\"}'\n")
    assert doctor.Probe(tmp_path, progress=False).python_report() == {"torch": "ok"}
    assert capsys.readouterr().err == ""


def test_json_mode_asks_for_no_progress_line_and_the_table_keeps_it(doctor, fake, monkeypatch):
    made = []

    class Recording(fake):
        def __init__(self, root=None, progress=True):
            super().__init__()
            self.progress = progress
            made.append(self)

    monkeypatch.setattr(doctor, "Probe", Recording)
    doctor.main(["--json"], out=io.StringIO())
    doctor.main([], out=io.StringIO())
    assert [probe.progress for probe in made] == [False, True]


def test_the_real_probe_without_an_environment_has_no_report(doctor, tmp_path, monkeypatch):
    monkeypatch.delenv("UV_PROJECT_ENVIRONMENT", raising=False)
    assert doctor.Probe(tmp_path).python_report() is None


# --- what the script is allowed to be ---------------------------------------------------------------------


def test_the_script_uses_only_the_standard_library_and_nothing_from_the_kit():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            imported.add((node.module or "").split(".")[0])
    outside = {name for name in imported if name not in sys.stdlib_module_names}
    assert outside == set(), f"not standard library: {outside}"
    assert "kit" not in imported


def test_the_script_is_written_for_python_39():
    ast.parse(SCRIPT.read_text(encoding="utf-8"), feature_version=(3, 9))
    assert SCRIPT.read_text(encoding="utf-8").startswith("#!/usr/bin/env python3\n")
    assert SCRIPT.stat().st_mode & 0o111, "bin/doctor.py must be executable"


def test_the_script_has_no_dashes_and_no_unfinished_markers():
    text = SCRIPT.read_text(encoding="utf-8")
    assert EM_DASH not in text and EN_DASH not in text
    assert MARKER not in text


@pytest.mark.skipif(not Path("/usr/bin/python3").exists(), reason="no system python3 here")
def test_the_script_starts_under_the_systems_own_python3():
    done = subprocess.run(
        ["/usr/bin/python3", str(SCRIPT), "--help"], capture_output=True, text=True, stdin=subprocess.DEVNULL
    )
    assert done.returncode == 0, done.stderr
    assert "--json" in done.stdout


def test_the_script_runs_in_a_fresh_clone_with_no_environment(tmp_path):
    """No virtual environment, no node_modules, no GSAP: every row still comes back and nothing crashes."""
    (tmp_path / "bin").mkdir()
    shutil.copy(SCRIPT, tmp_path / "bin" / "doctor.py")
    shutil.copy(ROOT / "package.json", tmp_path / "package.json")
    shutil.copy(ROOT / ".env.example", tmp_path / ".env.example")
    env = {k: v for k, v in os.environ.items() if k != "UV_PROJECT_ENVIRONMENT"}

    done = subprocess.run(
        [sys.executable, str(tmp_path / "bin" / "doctor.py"), "--json"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=120,
    )

    rows = json.loads(done.stdout)
    assert done.returncode == 1 and "Traceback" not in done.stderr
    by_name = {row["check"]: row for row in rows}
    gsap = next(row for name, row in by_name.items() if "GSAP" in name)
    assert (gsap["status"], gsap["fix"]) == ("missing", "bash bin/fetch_gsap.sh")
    for word in ("WhisperX", "torch", "yt-dlp"):
        row = next(row for name, row in by_name.items() if word in name)
        assert (row["status"], row["fix"]) == ("missing", SYNC)
    assert not (tmp_path / ".venv").exists(), "checking must not create an environment"
    assert not (tmp_path / "node_modules").exists(), "checking must not install anything"


def test_json_keeps_both_streams_clean_when_the_environment_answers(tmp_path):
    (tmp_path / "bin").mkdir()
    shutil.copy(SCRIPT, tmp_path / "bin" / "doctor.py")
    shutil.copy(ROOT / "package.json", tmp_path / "package.json")
    shutil.copy(ROOT / ".env.example", tmp_path / ".env.example")
    write_python(tmp_path / ".venv" / "bin" / "python", 'echo \'KIT_DOCTOR_REPORT {"torch": "ok"}\'\n')
    env = {k: v for k, v in os.environ.items() if k != "UV_PROJECT_ENVIRONMENT"}

    def run(*flags):
        return subprocess.run(
            [sys.executable, str(tmp_path / "bin" / "doctor.py"), *flags],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=120,
        )

    quiet = run("--json")
    assert quiet.stderr == "", "no progress line in JSON mode"
    assert isinstance(json.loads(quiet.stdout), list)
    chatty = run()
    assert "Checking the Python tools" in chatty.stderr
    assert "Optional keys not set: " in chatty.stdout


# --- this Mac -----------------------------------------------------------------------------------------------


@pytest.mark.mac
def test_doctor_on_this_mac():
    """Every required row is ok. Disk space may be ok or warn (it only fails under 5 GB)."""
    system_python = "/usr/bin/python3" if Path("/usr/bin/python3").exists() else sys.executable
    done = subprocess.run(
        [system_python, str(SCRIPT), "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=600,
    )
    rows = json.loads(done.stdout)

    assert "Traceback" not in done.stderr
    problems = [row for row in rows if row["status"] == "missing"]
    assert problems == [], problems
    assert done.returncode == 0
    for row in rows:
        if "Free disk" in row["check"] or "zscale" in row["check"]:
            assert row["status"] in {"ok", "warn"}, row  # a Mac with Homebrew's plain ffmpeg has no zscale
        else:
            assert row["status"] == "ok", row
    assert len(rows) >= 15


# --- bin/fetch_gsap.sh ----------------------------------------------------------------------------------------


FAKE_CURL = r"""#!/bin/bash
# A stand-in for curl. It records each URL and serves files from $FAKE_CURL_DIR.
out=""
url=""
want_code=0
while [ $# -gt 0 ]; do
  case "$1" in
    -o|--output) out="$2"; shift 2 ;;
    -w|--write-out) want_code=1; shift 2 ;;
    --max-time|--retry|--retry-delay|--connect-timeout) shift 2 ;;
    http*) url="$1"; shift ;;
    *) shift ;;
  esac
done
echo "$url" >> "$FAKE_CURL_DIR/requests.log"
reply() { [ "$want_code" = 1 ] && printf '%s' "$1"; return 0; }
case "${FAKE_CURL_MODE:-ok}" in
  offline) echo "curl: (6) Could not resolve host: registry.npmjs.org" >&2; exit 6 ;;
  notfound) [ -n "$out" ] && echo "{}" > "$out"; reply 404; exit 0 ;;
  busy) [ -n "$out" ] && echo "busy" > "$out"; reply 503; exit 0 ;;
esac
case "$url" in
  *.tgz) served="$FAKE_CURL_DIR/gsap.tgz" ;;
  *) served="$FAKE_CURL_DIR/meta.json" ;;
esac
if [ -n "$out" ]; then cat "$served" > "$out"; else cat "$served"; fi
reply 200
"""

# A stand-in for cp that writes half the file and then fails, as a full disk would.
FAILING_CP = """#!/bin/bash
for last; do :; done
printf 'half of a file' > "$last"
exit 1
"""

DIST = "/*! GSAP {version} | https://gsap.com | a stand-in build for tests */\nvar gsap = {{}};\n"


def digest_of(path: Path) -> str:
    return "sha512-" + base64.b64encode(hashlib.sha512(path.read_bytes()).digest()).decode()


def make_registry(folder: Path, version: str, *, tarball_version=None, integrity=None, dist=None) -> str:
    """The two files the fake curl serves for one GSAP version. Returns the real digest of the package it made.

    The metadata file carries that digest unless `integrity` says otherwise.
    """
    folder.mkdir(parents=True, exist_ok=True)
    body = (dist if dist is not None else DIST.format(version=version)).encode()
    package = json.dumps({"name": "gsap", "version": tarball_version or version}).encode()
    with tarfile.open(folder / "gsap.tgz", "w:gz") as tar:
        for name, data in (("package/package.json", package), ("package/dist/gsap.min.js", body)):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    real = digest_of(folder / "gsap.tgz")
    meta = {
        "name": "gsap",
        "version": version,
        "dist": {
            "tarball": f"https://registry.npmjs.org/gsap/-/gsap-{version}.tgz",
            "integrity": integrity or real,
        },
    }
    (folder / "meta.json").write_text(json.dumps(meta))
    return real


@pytest.fixture
def gsap_run(tmp_path):
    """Run a copy of the script against a fake curl. The real kit/vendor is never touched.

    The copy can be given another pinned checksum with run.pin(...), so a stand-in package can be tested
    as if it were the real one. The script itself has no switch for that.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "fetch_gsap.sh"
    shutil.copy(FETCH, script)
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    (fake_bin / "curl").write_text(FAKE_CURL)
    (fake_bin / "curl").chmod(0o755)
    registry = tmp_path / "registry"
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    def run(*args, mode="ok", **env):
        return subprocess.run(
            ["bash", str(script), *args],
            env={
                "PATH": f"{fake_bin}:/usr/bin:/bin",
                "FAKE_CURL_DIR": str(registry),
                "FAKE_CURL_MODE": mode,
                "TMPDIR": str(scratch),
                "HOME": str(tmp_path),
                **env,
            },
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )

    def pin(integrity: str) -> None:
        text = script.read_text()
        changed = re.sub(r'^PINNED_INTEGRITY=".*"$', f'PINNED_INTEGRITY="{integrity}"', text, flags=re.M)
        assert changed != text, "the script has no PINNED_INTEGRITY line to replace"
        script.write_text(changed)

    run.pin = pin
    run.registry = registry
    run.fakebin = fake_bin
    run.vendor = tmp_path / "kit" / "vendor"
    run.scratch = scratch
    run.requests = lambda: (registry / "requests.log").read_text().split() if (registry / "requests.log").exists() else []
    return run


def test_the_pinned_checksum_is_a_sha512_and_the_notes_name_its_version():
    text = FETCH.read_text(encoding="utf-8")
    pinned = re.search(r'^PINNED_INTEGRITY="(sha512-[A-Za-z0-9+/]{86}==)"$', text, re.M)
    version = re.search(r'^PINNED_VERSION="(\d+\.\d+\.\d+)"$', text, re.M)
    assert pinned and version, "the script records one sha512 and the version it belongs to"
    assert len(base64.b64decode(pinned.group(1)[len("sha512-") :])) == 64
    notes = (ROOT / "THIRD_PARTY.md").read_text(encoding="utf-8")
    assert version.group(1) in notes.split("## GSAP", 1)[1].split("\n## ", 1)[0]
    assert re.search(r'GSAP_VERSION="\$\{GSAP_VERSION:-\$PINNED_VERSION\}"', text), "the default version is the pinned one"


def test_fetch_gsap_downloads_the_pinned_version_and_checks_it(gsap_run):
    gsap_run.pin(make_registry(gsap_run.registry, "3.15.0"))

    done = gsap_run()

    assert (done.returncode, done.stderr) == (0, "")
    target = gsap_run.vendor / "gsap.min.js"
    assert target.read_text() == DIST.format(version="3.15.0")
    assert done.stdout.count("\n") == 1 and "wrote" in done.stdout and "3.15.0" in done.stdout
    assert "registry's own digest" not in done.stdout
    assert gsap_run.requests() == [
        "https://registry.npmjs.org/gsap/3.15.0",
        "https://registry.npmjs.org/gsap/-/gsap-3.15.0.tgz",
    ]
    assert sorted(p.name for p in gsap_run.vendor.iterdir()) == ["gsap.min.js"], "no part-written file is left behind"
    assert list(gsap_run.scratch.iterdir()) == [], "the temporary folder is removed"


def test_fetch_gsap_refuses_a_tampered_package_even_when_the_registry_digest_matches_it(gsap_run):
    # The registry is lying: its metadata carries the digest of the changed package, so it agrees with the download.
    # The script is not fooled, because the pinned version is checked against the digest recorded in the script.
    make_registry(gsap_run.registry, "3.15.0", dist=DIST.format(version="3.15.0") + "stealTheKeys();\n")

    done = gsap_run()

    assert done.returncode == 1
    assert done.stdout == ""
    assert done.stderr == (
        "The GSAP 3.15.0 download did not match the checksum this kit has recorded for it, so it was thrown away. "
        "Run bash bin/fetch_gsap.sh again.\n"
    )
    assert not (gsap_run.vendor / "gsap.min.js").exists()
    assert list(gsap_run.scratch.iterdir()) == []


def test_fetch_gsap_ignores_the_registry_digest_for_the_pinned_version(gsap_run):
    gsap_run.pin(make_registry(gsap_run.registry, "3.15.0", integrity="sha512-" + base64.b64encode(b"x" * 64).decode()))
    done = gsap_run()
    assert done.returncode == 0, done.stderr
    assert (gsap_run.vendor / "gsap.min.js").is_file()
    assert "registry's own digest" not in done.stdout


def test_fetch_gsap_version_can_be_overridden_and_says_which_digest_it_used(gsap_run):
    make_registry(gsap_run.registry, "3.14.2")

    done = gsap_run(GSAP_VERSION="3.14.2")

    assert done.returncode == 0, done.stderr
    assert gsap_run.requests()[0] == "https://registry.npmjs.org/gsap/3.14.2"
    assert "3.14.2" in (gsap_run.vendor / "gsap.min.js").read_text()
    first, second = done.stdout.splitlines()
    assert first == (
        "fetch_gsap: GSAP 3.14.2 is not the pinned 3.15.0, so the download was checked against the registry's own digest only."
    )
    assert second.startswith("fetch_gsap: wrote ")


def test_fetch_gsap_refuses_another_version_that_does_not_match_the_registry_digest(gsap_run):
    make_registry(gsap_run.registry, "3.14.2", integrity="sha512-" + base64.b64encode(b"x" * 64).decode())

    done = gsap_run(GSAP_VERSION="3.14.2")

    assert done.returncode == 1
    assert done.stderr == (
        "The GSAP 3.14.2 download did not match the checksum npm publishes for it, so it was thrown away. "
        "Run bash bin/fetch_gsap.sh again.\n"
    )
    assert not (gsap_run.vendor / "gsap.min.js").exists()
    assert list(gsap_run.scratch.iterdir()) == []


def test_fetch_gsap_can_write_to_another_folder(gsap_run, tmp_path):
    gsap_run.pin(make_registry(gsap_run.registry, "3.15.0"))
    other = tmp_path / "elsewhere"
    done = gsap_run(str(other))
    assert done.returncode == 0, done.stderr
    assert (other / "gsap.min.js").is_file()
    assert not gsap_run.vendor.exists()


def test_fetch_gsap_says_one_plain_sentence_when_the_network_is_down(gsap_run):
    make_registry(gsap_run.registry, "3.15.0")

    done = gsap_run(mode="offline")

    assert done.returncode == 1
    assert done.stdout == ""
    assert done.stderr == (
        "Could not download GSAP 3.15.0. Check your internet connection and run bash bin/fetch_gsap.sh again.\n"
    )
    assert "curl" not in done.stderr and "resolve" not in done.stderr
    assert not (gsap_run.vendor / "gsap.min.js").exists()
    assert list(gsap_run.scratch.iterdir()) == []


def test_fetch_gsap_names_the_version_in_the_network_sentence(gsap_run):
    done = gsap_run(mode="offline", GSAP_VERSION="3.14.2")
    assert done.stderr.startswith("Could not download GSAP 3.14.2. ")


def test_fetch_gsap_treats_a_busy_registry_like_a_network_problem(gsap_run):
    done = gsap_run(mode="busy")
    assert done.returncode == 1
    assert done.stderr == (
        "Could not download GSAP 3.15.0. Check your internet connection and run bash bin/fetch_gsap.sh again.\n"
    )


def test_fetch_gsap_says_when_the_version_does_not_exist(gsap_run):
    done = gsap_run(mode="notfound", GSAP_VERSION="3.99.9")
    assert done.returncode == 1
    assert done.stderr == "The npm registry has no GSAP 3.99.9. Check the version number in GSAP_VERSION.\n"


def test_fetch_gsap_refuses_a_package_of_another_version(gsap_run):
    gsap_run.pin(make_registry(gsap_run.registry, "3.15.0", tarball_version="3.12.0"))
    done = gsap_run()
    assert done.returncode == 1
    assert done.stderr == "The package that came down is GSAP 3.12.0, not 3.15.0, so it was thrown away.\n"
    assert not (gsap_run.vendor / "gsap.min.js").exists()


def test_fetch_gsap_refuses_a_file_that_is_not_gsap(gsap_run):
    gsap_run.pin(make_registry(gsap_run.registry, "3.15.0", dist="console.log('hello');\n"))
    done = gsap_run()
    assert done.returncode == 1
    assert "does not look like GSAP 3.15.0" in done.stderr
    assert not (gsap_run.vendor / "gsap.min.js").exists()


@pytest.mark.parametrize("version", ["latest", "3.15", "3.15.0/../x", "3.15.0 ", "^3.15.0", "3.15.0-beta.1"])
def test_fetch_gsap_refuses_a_version_that_is_not_plain_numbers(gsap_run, version):
    done = gsap_run(GSAP_VERSION=version)
    assert done.returncode == 1
    assert done.stderr == "GSAP_VERSION must be a version number such as 3.15.0.\n"
    assert gsap_run.requests() == [], "nothing is downloaded for a version that cannot be right"


def test_fetch_gsap_treats_an_empty_version_as_not_set(gsap_run):
    gsap_run.pin(make_registry(gsap_run.registry, "3.15.0"))
    done = gsap_run(GSAP_VERSION="")
    assert done.returncode == 0, done.stderr
    assert gsap_run.requests()[0] == "https://registry.npmjs.org/gsap/3.15.0"


def test_a_failed_copy_leaves_no_part_file_behind(gsap_run):
    gsap_run.pin(make_registry(gsap_run.registry, "3.15.0"))
    (gsap_run.fakebin / "cp").write_text(FAILING_CP)
    (gsap_run.fakebin / "cp").chmod(0o755)

    done = gsap_run()

    assert done.returncode == 1
    assert done.stderr == "Could not write kit/vendor/gsap.min.js. Check that you can save files in that folder.\n"
    assert sorted(p.name for p in gsap_run.vendor.iterdir()) == [], "neither gsap.min.js nor gsap.min.js.part is left"
    assert list(gsap_run.scratch.iterdir()) == []


def test_fetch_gsap_leaves_an_existing_file_alone(gsap_run):
    gsap_run.vendor.mkdir(parents=True)
    (gsap_run.vendor / "gsap.min.js").write_text("// already here\n")
    done = gsap_run(mode="offline")
    assert done.returncode == 0
    assert "Nothing to do" in done.stdout and "kit/vendor/gsap.min.js" in done.stdout
    assert (gsap_run.vendor / "gsap.min.js").read_text() == "// already here\n"
    assert gsap_run.requests() == []


def test_fetch_gsap_says_when_the_file_that_is_there_is_another_version(gsap_run):
    gsap_run.vendor.mkdir(parents=True)
    (gsap_run.vendor / "gsap.min.js").write_text(DIST.format(version="3.14.0"))
    done = gsap_run(mode="offline")
    assert done.returncode == 0
    assert "Nothing to do" in done.stdout
    assert "it is GSAP 3.14.0 and this kit pins GSAP 3.15.0" in done.stdout
    assert "delete the file" in done.stdout
    assert (gsap_run.vendor / "gsap.min.js").read_text() == DIST.format(version="3.14.0")
    assert gsap_run.requests() == []


def test_fetch_gsap_stays_quiet_when_the_file_that_is_there_is_the_pinned_version(gsap_run):
    gsap_run.vendor.mkdir(parents=True)
    (gsap_run.vendor / "gsap.min.js").write_text(DIST.format(version="3.15.0"))
    done = gsap_run()
    assert done.stdout.count("\n") == 1 and "Nothing to do" in done.stdout


def test_third_party_notes_record_the_gsap_version_and_how_it_is_checked(gsap_run):
    gsap_run.pin(make_registry(gsap_run.registry, "3.15.0"))
    gsap_run()
    version = re.search(r"/gsap/(\d+\.\d+\.\d+)$", gsap_run.requests()[0]).group(1)
    assert version == "3.15.0"
    notes = (ROOT / "THIRD_PARTY.md").read_text(encoding="utf-8")
    gsap_section = notes.split("## GSAP", 1)[1].split("\n## ", 1)[0]
    assert version in gsap_section
    assert "bin/fetch_gsap.sh" in gsap_section and "kit/vendor/" in gsap_section
    assert "https://gsap.com/standard-license" in gsap_section
    assert "sha512" in gsap_section and "holds" in gsap_section, "the notes say the script holds the checksum"
    assert "registry itself publishes" in gsap_section, "and that another version is checked against the registry only"
    for earlier in ("## HyperFrames", "## Fonts", "## Humanizer"):
        assert earlier in notes, "the other sections stay"
