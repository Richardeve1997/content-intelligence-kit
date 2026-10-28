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
CHROMIUM = "uv run python -m playwright install chromium"
ENV_EXAMPLE = "# Names only.\nAPIFY_TOKEN=\nOPENAI_API_KEY=\nGEMINI_API_KEY=\nELEVENLABS_API_KEY=\nZERNIO_API_KEY=\n"
KEYS = ["APIFY_TOKEN", "OPENAI_API_KEY", "GEMINI_API_KEY", "ELEVENLABS_API_KEY", "ZERNIO_API_KEY"]
ENCODERS = " V....D h264_videotoolbox    VideoToolbox H.264 Encoder (codec h264)\n V....D libx264 x264\n"
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
                "brew": "/opt/homebrew/bin/brew",
                "ffmpeg": "/opt/homebrew/bin/ffmpeg",
                "uv": "/opt/homebrew/bin/uv",
                "node": "/opt/homebrew/bin/node",
                "npx": "/opt/homebrew/bin/npx",
            }
            self.outputs = {
                "ffmpeg -hide_banner -encoders": (0, ENCODERS),
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
        "Homebrew",
        "ffmpeg",
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
    probe = fake()
    probe.texts["package.json"] = json.dumps({"devDependencies": {"hyperframes": "0.9.1"}})
    probe.outputs["npx --no-install hyperframes --version"] = (0, "0.9.1\n")
    row = find(rows_of(doctor, probe), "HyperFrames")
    assert (row["status"], "0.9.1" in row["check"]) == ("ok", True)


COMMAND_FIXES = [
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


def test_ffmpeg_from_somewhere_else_is_not_fixed_by_brew_alone(doctor, fake):
    probe = fake()
    probe.programs["ffmpeg"] = "/usr/local/bin/ffmpeg"
    probe.outputs["ffmpeg -hide_banner -encoders"] = (0, " V....D libx264 x264\n")
    row = find(rows_of(doctor, probe), "ffmpeg")
    assert row["status"] == "missing"
    assert row["fix"].startswith("brew install ffmpeg")
    assert "/usr/local/bin/ffmpeg" in row["fix"]


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


def test_optional_keys_show_by_name_and_never_fail_the_run(doctor, fake):
    rows = rows_of(doctor, fake())
    for name in KEYS:
        row = find(rows, name)
        assert "(optional)" in row["check"]
        assert row["status"] == "warn"
        assert name in row["fix"] and ".env" in row["fix"]
    assert "missing" not in {row["status"] for row in rows}


def test_a_key_in_dotenv_is_present_and_its_value_is_never_shown(doctor, fake, capsys):
    secret = "fake-value-for-tests"
    probe = fake()
    probe.texts[".env"] = f"# keys\nAPIFY_TOKEN={secret}\nZERNIO_API_KEY=\nexport GEMINI_API_KEY='{secret}-two'\n"
    rows = rows_of(doctor, probe)

    assert find(rows, "APIFY_TOKEN")["status"] == "ok"
    assert find(rows, "GEMINI_API_KEY")["status"] == "ok"
    assert find(rows, "ZERNIO_API_KEY")["status"] == "warn"
    assert find(rows, "OPENAI_API_KEY")["status"] == "warn"
    doctor.main([], probe=probe)
    doctor.main(["--json"], probe=probe)
    shown = json.dumps(rows) + capsys.readouterr().out
    assert secret not in shown


def test_a_key_in_the_environment_counts_too(doctor, fake):
    probe = fake()
    probe.env = {"OPENAI_API_KEY": "not-a-real-key", "ELEVENLABS_API_KEY": "   "}
    rows = rows_of(doctor, probe)
    assert find(rows, "OPENAI_API_KEY")["status"] == "ok"
    assert find(rows, "ELEVENLABS_API_KEY")["status"] == "warn", "a blank value is not a key"
    assert "not-a-real-key" not in json.dumps(rows)


def test_a_later_blank_line_in_dotenv_clears_an_earlier_value(doctor, fake):
    probe = fake()
    probe.texts[".env"] = "APIFY_TOKEN=fake-value-for-tests\nAPIFY_TOKEN=\n"
    assert find(rows_of(doctor, probe), "APIFY_TOKEN")["status"] == "warn"


def test_quoted_and_commented_values_are_read_like_the_kit_reads_them(doctor, fake):
    probe = fake()
    probe.texts[".env"] = 'APIFY_TOKEN=""\nOPENAI_API_KEY=   # nothing yet\nGEMINI_API_KEY="fake-value-for-tests"\n'
    rows = rows_of(doctor, probe)
    assert find(rows, "APIFY_TOKEN")["status"] == "warn"
    assert find(rows, "OPENAI_API_KEY")["status"] == "warn"
    assert find(rows, "GEMINI_API_KEY")["status"] == "ok"


def test_the_key_names_come_from_dotenv_example(doctor, fake):
    probe = fake()
    probe.texts[".env.example"] = "SOME_NEW_KEY=\nAPIFY_TOKEN=\n"
    rows = rows_of(doctor, probe)
    assert find(rows, "SOME_NEW_KEY")["status"] == "warn"
    assert [row for row in rows if "OPENAI" in row["check"]] == []


def test_a_missing_dotenv_example_does_not_stop_the_other_checks(doctor, fake):
    probe = fake()
    probe.texts.pop(".env.example")
    rows = rows_of(doctor, probe)
    assert find(rows, "Homebrew")["status"] == "ok"


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
        if "(optional)" in row["check"]:
            continue
        if "Free disk" in row["check"]:
            assert row["status"] in {"ok", "warn"}, row
        else:
            assert row["status"] == "ok", row
    assert len(rows) >= 14


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
if [ -n "$out" ]; then cp "$served" "$out"; else cat "$served"; fi
reply 200
"""

DIST = "/*! GSAP {version} | https://gsap.com | a stand-in build for tests */\nvar gsap = {{}};\n"


def make_registry(folder: Path, version: str, *, tarball_version=None, integrity=None, dist=None) -> Path:
    """The two files the fake curl serves for one GSAP version."""
    folder.mkdir(parents=True, exist_ok=True)
    body = (dist if dist is not None else DIST.format(version=version)).encode()
    package = json.dumps({"name": "gsap", "version": tarball_version or version}).encode()
    with tarfile.open(folder / "gsap.tgz", "w:gz") as tar:
        for name, data in (("package/package.json", package), ("package/dist/gsap.min.js", body)):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    digest = hashlib.sha512((folder / "gsap.tgz").read_bytes()).digest()
    meta = {
        "name": "gsap",
        "version": version,
        "dist": {
            "tarball": f"https://registry.npmjs.org/gsap/-/gsap-{version}.tgz",
            "integrity": integrity or "sha512-" + base64.b64encode(digest).decode(),
        },
    }
    (folder / "meta.json").write_text(json.dumps(meta))
    return folder


@pytest.fixture
def gsap_run(tmp_path):
    """Run a copy of the script against a fake curl. The real kit/vendor is never touched."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shutil.copy(FETCH, bin_dir / "fetch_gsap.sh")
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    (fake_bin / "curl").write_text(FAKE_CURL)
    (fake_bin / "curl").chmod(0o755)
    registry = tmp_path / "registry"
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    def run(*args, mode="ok", **env):
        done = subprocess.run(
            ["bash", str(bin_dir / "fetch_gsap.sh"), *args],
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
        return done

    run.registry = registry
    run.vendor = tmp_path / "kit" / "vendor"
    run.scratch = scratch
    run.requests = lambda: (registry / "requests.log").read_text().split() if (registry / "requests.log").exists() else []
    return run


def test_fetch_gsap_downloads_the_pinned_version_and_checks_it(gsap_run):
    make_registry(gsap_run.registry, "3.15.0")

    done = gsap_run()

    assert (done.returncode, done.stderr) == (0, "")
    target = gsap_run.vendor / "gsap.min.js"
    assert target.read_text() == DIST.format(version="3.15.0")
    assert "wrote" in done.stdout and "3.15.0" in done.stdout
    assert gsap_run.requests() == [
        "https://registry.npmjs.org/gsap/3.15.0",
        "https://registry.npmjs.org/gsap/-/gsap-3.15.0.tgz",
    ]
    assert sorted(p.name for p in gsap_run.vendor.iterdir()) == ["gsap.min.js"], "no part-written file is left behind"
    assert list(gsap_run.scratch.iterdir()) == [], "the temporary folder is removed"


def test_fetch_gsap_version_can_be_overridden_by_the_environment(gsap_run):
    make_registry(gsap_run.registry, "3.14.2")
    done = gsap_run(GSAP_VERSION="3.14.2")
    assert done.returncode == 0, done.stderr
    assert gsap_run.requests()[0] == "https://registry.npmjs.org/gsap/3.14.2"
    assert "3.14.2" in (gsap_run.vendor / "gsap.min.js").read_text()


def test_fetch_gsap_can_write_to_another_folder(gsap_run, tmp_path):
    make_registry(gsap_run.registry, "3.15.0")
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


def test_fetch_gsap_refuses_a_download_that_does_not_match_the_registry_checksum(gsap_run):
    make_registry(gsap_run.registry, "3.15.0", integrity="sha512-" + base64.b64encode(b"x" * 64).decode())

    done = gsap_run()

    assert done.returncode == 1
    assert done.stderr == (
        "The GSAP 3.15.0 download did not match the checksum npm publishes for it, so it was thrown away. "
        "Run bash bin/fetch_gsap.sh again.\n"
    )
    assert not (gsap_run.vendor / "gsap.min.js").exists()
    assert list(gsap_run.scratch.iterdir()) == []


def test_fetch_gsap_refuses_a_package_of_another_version(gsap_run):
    make_registry(gsap_run.registry, "3.15.0", tarball_version="3.12.0")
    done = gsap_run()
    assert done.returncode == 1
    assert done.stderr == "The package that came down is GSAP 3.12.0, not 3.15.0, so it was thrown away.\n"
    assert not (gsap_run.vendor / "gsap.min.js").exists()


def test_fetch_gsap_refuses_a_file_that_is_not_gsap(gsap_run):
    make_registry(gsap_run.registry, "3.15.0", dist="console.log('hello');\n")
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
    make_registry(gsap_run.registry, "3.15.0")
    done = gsap_run(GSAP_VERSION="")
    assert done.returncode == 0, done.stderr
    assert gsap_run.requests()[0] == "https://registry.npmjs.org/gsap/3.15.0"


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


def test_third_party_notes_record_the_gsap_version_the_script_fetches(gsap_run):
    make_registry(gsap_run.registry, "3.15.0")
    gsap_run()
    version = re.search(r"/gsap/(\d+\.\d+\.\d+)$", gsap_run.requests()[0]).group(1)
    assert version == "3.15.0"
    notes = (ROOT / "THIRD_PARTY.md").read_text(encoding="utf-8")
    gsap_section = notes.split("## GSAP", 1)[1].split("\n## ", 1)[0]
    assert version in gsap_section
    assert "bin/fetch_gsap.sh" in gsap_section and "kit/vendor/" in gsap_section
    assert "https://gsap.com/standard-license" in gsap_section
    for earlier in ("## HyperFrames", "## Fonts", "## Humanizer"):
        assert earlier in notes, "the other sections stay"
