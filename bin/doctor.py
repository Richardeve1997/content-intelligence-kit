#!/usr/bin/env python3
"""Check this Mac for everything the kit needs, and say how to fix what is missing.

    python3 bin/doctor.py            a table with one row per check
    python3 bin/doctor.py --json     the same rows as JSON

Each row is {"check": ..., "status": "ok" | "missing" | "warn", "fix": ...}.

  ok       the check passes, and "fix" is empty.
  missing  a required check fails. "fix" is the exact command to run, or a sentence when no command can help.
  warn     worth a look but it does not stop anything: low disk space, an ffmpeg without the zscale
           filter (HDR phone videos then keep washed-out colours), or, inside Codex's sandbox, a tool that
           cannot start there (HyperFrames and Chromium). One run outside the sandbox checks those.

Under the table, one line names the optional keys that are not set yet (Apify, an image model,
ElevenLabs, Zernio). A key that is not set is not a problem, so it is a line and not a row, and
the JSON holds the rows only.

The command exits 0 when no row is missing and 1 otherwise. Rows come in the order the fixes have to
be made, so working down the table works.

It uses only the Python standard library and nothing from the kit, so the Python that ships with a
Mac can run it on a fresh clone, before the kit's own environment exists. Keys are named and never
shown: a value is never printed.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent

GIB = 1024 ** 3
DISK_WARN_GB = 20
DISK_FAIL_GB = 5
NODE_MIN = 22
BREW_PREFIX = "/opt/homebrew/"

# The fixes that are commands. ONBOARDING.md shows every one of them.
# A new Mac has a python3 that only asks to install Apple's command line developer tools. This installs them.
FIX_PYTHON3 = "xcode-select --install"
FIX_BREW = '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
FIX_BREW_PATH = (
    "echo 'eval \"$(/opt/homebrew/bin/brew shellenv)\"' >> ~/.zprofile && "
    'eval "$(/opt/homebrew/bin/brew shellenv)"'
)
FIX_FFMPEG = "brew install ffmpeg"
FIX_FFMPEG_AGAIN = "brew reinstall ffmpeg"
# Homebrew's plain ffmpeg is built without the zimg library, so it has no zscale filter and cannot tone-map an
# HDR phone video. ffmpeg-full has it, and videotoolbox too. Homebrew keeps ffmpeg-full out of the way of the
# plain ffmpeg ("keg-only"), so the plain one is unlinked and the full one linked in its place.
FIX_FFMPEG_FULL = "brew install ffmpeg-full && brew unlink ffmpeg && brew link --force ffmpeg-full"
FIX_FFMPEG_FULL_FRESH = "brew install ffmpeg-full && brew link --force ffmpeg-full"
FIX_UV = "brew install uv"
FIX_PYTHON = "uv python install 3.11"
FIX_NODE = "brew install node"
FIX_NODE_NEWER = "brew upgrade node"
FIX_HYPERFRAMES = "npm ci"
# --inexact keeps any group installed earlier. Without it, uv removes what the command does not name.
FIX_SYNC = "uv sync --group video --group research --inexact"
FIX_CHROMIUM = "uv run python -m playwright install chromium"
FIX_GSAP = "bash bin/fetch_gsap.sh"
FIX_COMMANDS = (
    FIX_PYTHON3,
    FIX_BREW,
    FIX_BREW_PATH,
    FIX_FFMPEG,
    FIX_FFMPEG_AGAIN,
    FIX_FFMPEG_FULL,
    FIX_FFMPEG_FULL_FRESH,
    FIX_UV,
    FIX_PYTHON,
    FIX_NODE,
    FIX_NODE_NEWER,
    FIX_HYPERFRAMES,
    FIX_SYNC,
    FIX_CHROMIUM,
    FIX_GSAP,
)

# The fixes that are sentences, because no command can help.
FIX_MAC = "The kit needs a Mac with an Apple silicon chip (M1 or newer). This computer cannot run it."
FIX_ROSETTA = (
    "This terminal app is running under Rosetta, so the kit would install Intel versions of its tools. "
    "Quit the app, untick Open using Rosetta in its Get Info window in Finder, and open it again."
)
FIX_DISK = (
    "Heavy jobs need at least 5 GB free, and 20 GB or more is comfortable. "
    "Empty the Trash, then move old videos and downloads to an external drive or cloud storage."
)

# Codex runs each command in a sandbox and sets CODEX_SANDBOX for it. Inside it HyperFrames cannot reach the
# Keychain and Chromium cannot register its Mach port, so their rows cannot be checked there. A failure in the
# sandbox says nothing about the Mac, so those rows are warnings that say so, and the rest of the check still counts.
SANDBOX_VARIABLE = "CODEX_SANDBOX"
SANDBOX_REFUSAL = re.compile(r"operation not permitted|permission denied", re.IGNORECASE)  # the sandbox refusing a write
FIX_OUTSIDE_SANDBOX = (
    "Not checked inside Codex's sandbox. Approve running python3 bin/doctor.py outside the sandbox to check it."
)
SANDBOX_NOTE = (
    "Some rows cannot be checked inside Codex's sandbox. One run of python3 bin/doctor.py outside the sandbox "
    "gives the full check. Inside the sandbox, start the kit's uv commands with UV_NO_CACHE=1."
)

# The optional keys. The names that count come from .env.example, and these are the ones to use when that file cannot be read.
KNOWN_KEYS = ("APIFY_TOKEN", "OPENAI_API_KEY", "GEMINI_API_KEY", "ELEVENLABS_API_KEY", "ZERNIO_API_KEY")

# Run by the kit's own Python. It prints one line, KIT_DOCTOR_REPORT followed by JSON, saying for each
# package whether it is "ok", "missing" or "broken" (installed but it will not import).
REPORT_MARK = "KIT_DOCTOR_REPORT"
REPORT_SCRIPT = '''
import importlib
import importlib.util
import json
import os


def state(name):
    try:
        if importlib.util.find_spec(name) is None:
            return "missing"
    except BaseException:
        return "broken"
    try:
        importlib.import_module(name)
    except BaseException:
        return "broken"
    return "ok"


report = {}
for name in ("mlx_whisper", "faster_whisper", "whisperx", "torch", "yt_dlp", "playwright"):
    report[name] = state(name)
if report["playwright"] == "ok" and os.environ.get("CODEX_SANDBOX", "").strip():
    report["chromium"] = "not checked"  # Codex's sandbox stops Chromium from starting, so a launch proves nothing
elif report["playwright"] == "ok":
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            playwright.chromium.launch().close()
        report["chromium"] = "ok"
    except BaseException:
        report["chromium"] = "missing"
print("KIT_DOCTOR_REPORT " + json.dumps(report))
'''

Row = Dict[str, str]
Result = Tuple[str, str]  # (status, fix)


# --- what the checks look at --------------------------------------------------------------------------------


class Probe:
    """Everything the checks learn about this Mac. Tests hand the checks a fake one instead."""

    def __init__(self, root: Path = ROOT, progress: bool = True):
        self.root = Path(root)
        self.progress = progress  # say so on stderr before the slow part. JSON mode turns this off

    def system(self) -> str:
        return platform.system()

    def machine(self) -> str:
        return platform.machine()

    def apple_silicon_hardware(self) -> bool:
        """True on a Mac with an Apple silicon chip, even when this program runs under Rosetta."""
        result = self.run(["sysctl", "-n", "hw.optional.arm64"], timeout=10)
        return bool(result) and result[0] == 0 and result[1].strip() == "1"

    def which(self, name: str) -> Optional[str]:
        return shutil.which(name)

    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def run(self, argv: Sequence[str], timeout: float = 30) -> Optional[Tuple[int, str]]:
        """(exit status, output) of a program, or None when it will not start or takes too long."""
        try:
            done = subprocess.run(
                list(argv),
                cwd=str(self.root),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                text=True,
                errors="replace",
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return done.returncode, done.stdout

    def free_gb(self) -> float:
        return shutil.disk_usage(str(self.root)).free / GIB

    def file_size(self, relative: str) -> int:
        try:
            return (self.root / relative).stat().st_size
        except OSError:
            return 0

    def read_text(self, relative: str) -> Optional[str]:
        try:
            return (self.root / relative).read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            return None

    def environ(self, name: str) -> Optional[str]:
        return os.environ.get(name)

    def kit_python(self) -> Optional[str]:
        """The Python of the kit's own environment, or None when it has not been created yet."""
        chosen = os.environ.get("UV_PROJECT_ENVIRONMENT", "").strip()
        folder = (self.root / chosen) if chosen else (self.root / ".venv")
        python = folder / "bin" / "python"
        return str(python) if python.exists() else None

    def python_report(self) -> Optional[Dict[str, str]]:
        """What the kit's Python can import, or None when there is no environment or it gave no answer."""
        python = self.kit_python()
        if not python:
            return None
        if self.progress:
            sys.stderr.write("Checking the Python tools. The first run after an install can take a minute...\n")
        result = self.run([python, "-I", "-c", REPORT_SCRIPT], timeout=300)
        if result is None or result[0] != 0:
            return None
        lines = [line for line in result[1].splitlines() if line.startswith(REPORT_MARK + " ")]
        if not lines:
            return None
        try:
            report = json.loads(lines[-1][len(REPORT_MARK) + 1 :])
        except ValueError:
            return None
        return report if isinstance(report, dict) else None


# --- the checks ---------------------------------------------------------------------------------------------


def check_mac(probe: Probe) -> Result:
    if probe.system() != "Darwin":
        return "missing", FIX_MAC
    if probe.machine() == "arm64":
        return "ok", ""
    return "missing", FIX_ROSETTA if probe.apple_silicon_hardware() else FIX_MAC


def check_python3(probe: Probe) -> Result:
    """Passes when `python3 --version` answers with a Python 3 version.

    The machine check and the Claude Code hooks run on python3. Until Apple's command line developer tools
    are installed, the python3 on a new Mac only asks to install them and then stops, without running anything.
    """
    path = probe.which("python3")
    result = probe.run([path, "--version"], timeout=20) if path else None
    if result and result[0] == 0 and re.match(r"Python 3\.\d+", result[1].strip()):
        return "ok", ""
    return "missing", FIX_PYTHON3


def check_brew(probe: Probe) -> Result:
    if probe.which("brew"):
        return "ok", ""
    if probe.exists(BREW_PREFIX + "bin/brew"):
        return "missing", FIX_BREW_PATH
    return "missing", FIX_BREW


def check_ffmpeg(probe: Probe) -> Result:
    path = probe.which("ffmpeg")
    if not path:
        return "missing", FIX_FFMPEG
    result = probe.run([path, "-hide_banner", "-encoders"], timeout=30)
    if result and result[0] == 0 and "h264_videotoolbox" in result[1]:
        return "ok", ""
    if path.startswith(BREW_PREFIX):
        return "missing", FIX_FFMPEG_AGAIN
    return "missing", (
        f"{FIX_FFMPEG} (then remove or rename the old ffmpeg at {path}, which has no h264_videotoolbox, "
        "and open a new terminal)"
    )


def check_zscale(probe: Probe) -> Result:
    """Passes when ffmpeg has the zscale filter, which turns an HDR phone video into normal colours.

    Without it the kit still edits the video, but HDR colours can look washed out. Turning HDR video off
    on the phone avoids that too, so this is a warning and never a stop.
    """
    path = probe.which("ffmpeg")
    if not path:
        return "warn", FIX_FFMPEG_FULL_FRESH  # the ffmpeg row above says to install one, and this build would do
    result = probe.run([path, "-hide_banner", "-filters"], timeout=30)
    if result and result[0] == 0 and any(line.split()[1:2] == ["zscale"] for line in result[1].splitlines()):
        return "ok", ""
    if path.startswith(BREW_PREFIX):
        return "warn", FIX_FFMPEG_FULL
    return "warn", (
        f"{FIX_FFMPEG_FULL_FRESH} (then remove or rename the old ffmpeg at {path}, which has no zscale filter, "
        "and open a new terminal)"
    )


def check_uv(probe: Probe) -> Result:
    return ("ok", "") if probe.which("uv") else ("missing", FIX_UV)


def check_python(probe: Probe) -> Result:
    """Asks uv without its cache: an agent's sandbox cannot write the cache in the home folder, and uv then
    fails as if Python 3.11 were missing. A read-only sandbox refuses even the temporary folder that asks for,
    and then the row is not checked rather than missing."""
    if not probe.which("uv"):
        return "missing", FIX_PYTHON
    result = probe.run(["uv", "python", "find", "--no-cache", "3.11"], timeout=30)
    if result and result[0] == 0 and result[1].strip():
        return "ok", ""
    if result and in_codex_sandbox(probe) and SANDBOX_REFUSAL.search(result[1]):
        return "warn", FIX_OUTSIDE_SANDBOX
    return "missing", FIX_PYTHON


def check_node(probe: Probe) -> Result:
    path = probe.which("node")
    if not path:
        return "missing", FIX_NODE
    result = probe.run([path, "--version"], timeout=20)
    found = re.match(r"v?(\d+)\.", result[1].strip()) if result and result[0] == 0 else None
    if not found:
        return "missing", FIX_NODE
    return ("ok", "") if int(found.group(1)) >= NODE_MIN else ("missing", FIX_NODE_NEWER)


def pinned_hyperframes(probe: Probe) -> Optional[str]:
    """The HyperFrames version package.json pins, from devDependencies or dependencies. None when there is none."""
    try:
        packages = json.loads(probe.read_text("package.json") or "")
    except ValueError:
        return None
    if not isinstance(packages, dict):
        return None
    for section in ("devDependencies", "dependencies"):
        group = packages.get(section)
        pin = group.get("hyperframes") if isinstance(group, dict) else None
        if isinstance(pin, str) and pin.strip():
            return pin.strip().lstrip("^~=v")
    return None


def in_codex_sandbox(probe: Probe) -> bool:
    """True when Codex runs this check inside its sandbox, where some tools cannot start at all."""
    return bool((probe.environ(SANDBOX_VARIABLE) or "").strip())


def check_hyperframes(probe: Probe, pin: Optional[str]) -> Result:
    # Look on disk first: asking npx about a package that is not installed would go to the network.
    if not pin or not probe.file_size("node_modules/hyperframes/package.json") or not probe.which("npx"):
        return "missing", FIX_HYPERFRAMES
    if in_codex_sandbox(probe):  # HyperFrames reaches for the Keychain as it starts, which the sandbox refuses
        return "warn", FIX_OUTSIDE_SANDBOX
    result = probe.run(["npx", "--no-install", "hyperframes", "--version"], timeout=60)
    found = re.search(r"\d+\.\d+\.\d+", result[1]) if result and result[0] == 0 else None
    return ("ok", "") if found and found.group(0) == pin else ("missing", FIX_HYPERFRAMES)


def check_chromium(report: Optional[Dict[str, str]], sandboxed: bool = False) -> Result:
    if not report or report.get("playwright") != "ok":
        return "missing", FIX_SYNC
    if sandboxed:
        return "warn", FIX_OUTSIDE_SANDBOX
    return ("ok", "") if report.get("chromium") == "ok" else ("missing", FIX_CHROMIUM)


def check_gsap(probe: Probe) -> Result:
    return ("ok", "") if probe.file_size("kit/vendor/gsap.min.js") > 0 else ("missing", FIX_GSAP)


def check_package(report: Optional[Dict[str, str]], *names: str) -> Result:
    """Passes when the kit's Python can import at least one of the packages."""
    if report and any(report.get(name) == "ok" for name in names):
        return "ok", ""
    return "missing", FIX_SYNC


def gigabytes(value: float) -> str:
    """A size in GB for a sentence, rounded down to one decimal so it never reads as more than there is."""
    return f"{math.floor(round(value * 10, 6)) / 10:g}"


def check_disk(probe: Probe) -> Result:
    """The row names the space that is free now, so a warning is never read as being under the 5 GB floor."""
    free = probe.free_gb()
    if free >= DISK_WARN_GB:
        return "ok", ""
    return ("warn" if free >= DISK_FAIL_GB else "missing"), f"Free now: {gigabytes(free)} GB. {FIX_DISK}"


# --- optional keys ------------------------------------------------------------------------------------------

_NAME_LINE = re.compile(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)")


def _has_value(raw: str) -> bool:
    raw = raw.strip()
    if raw[:1] in ("'", '"'):
        end = raw.find(raw[0], 1)
        if end != -1:
            return bool(raw[1:end].strip())
    if raw.startswith("#"):
        return False
    return bool(re.split(r"\s#", raw, maxsplit=1)[0].strip())


def _dotenv_lines(text: Optional[str]) -> List[Tuple[str, str]]:
    found = []
    for line in (text or "").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = _NAME_LINE.fullmatch(line)
        if match:
            found.append((match.group(1), match.group(2)))
    return found


def key_names(example: Optional[str]) -> List[str]:
    """The key names listed in .env.example, in order. The five known names when the file cannot be read."""
    names = []
    for name, _ in _dotenv_lines(example):
        if name not in names:
            names.append(name)
    return names or list(KNOWN_KEYS)


def keys_that_are_set(probe: Probe, names: Sequence[str]) -> List[str]:
    """Which of the keys have a value, from the environment or from .env. The values themselves are never kept."""
    present = {}
    for name, raw in _dotenv_lines(probe.read_text(".env")):
        present[name] = _has_value(raw)  # a later line wins, so a blank line clears an earlier value
    found = []
    for name in names:
        from_environment = (probe.environ(name) or "").strip()
        if from_environment or present.get(name):
            found.append(name)
    return found


def optional_keys_not_set(probe: Probe) -> List[str]:
    """The optional keys that have no value yet, by name and in the order .env.example lists them."""
    names = key_names(probe.read_text(".env.example"))
    have = set(keys_that_are_set(probe, names))
    return [name for name in names if name not in have]


def footer_line(probe: Probe) -> Optional[str]:
    """The one line under the table about optional keys, or None when every one of them is set."""
    try:
        missing = optional_keys_not_set(probe)
    except Exception:  # the footer is a courtesy and must never stop the checks
        return None
    if not missing:
        return None
    return "Optional keys not set: " + ", ".join(missing) + ". Add them to .env when you want those features."


# --- putting the rows together --------------------------------------------------------------------------------


def _row(label: str, required: bool, check: Callable[[], Result]) -> Row:
    try:
        status, fix = check()
    except Exception as exc:  # a check must never end in a traceback
        status = "missing" if required else "warn"
        fix = (
            f"This check could not run ({type(exc).__name__}). "
            "Run python3 bin/doctor.py again, and tell your agent if it keeps failing."
        )
    return {"check": label, "status": status, "fix": fix}


def collect(probe: Probe) -> List[Row]:
    """Every row, in the order the fixes have to be made."""
    pin = pinned_hyperframes(probe)
    try:
        report = probe.python_report() if probe.kit_python() else None
    except Exception:
        report = None
    checks: List[Tuple[str, Callable[[], Result]]] = [
        ("macOS on Apple silicon", lambda: check_mac(probe)),
        ("python3 command", lambda: check_python3(probe)),
        ("Homebrew", lambda: check_brew(probe)),
        ("ffmpeg with h264_videotoolbox", lambda: check_ffmpeg(probe)),
        ("HDR tone mapping (zscale filter)", lambda: check_zscale(probe)),
        ("uv", lambda: check_uv(probe)),
        ("Python 3.11", lambda: check_python(probe)),
        ("Node 22 or newer", lambda: check_node(probe)),
        (f"HyperFrames CLI {pin}" if pin else "HyperFrames CLI at the pinned version", lambda: check_hyperframes(probe, pin)),
        ("Chromium for Playwright", lambda: check_chromium(report, in_codex_sandbox(probe))),
        ("GSAP in kit/vendor", lambda: check_gsap(probe)),
        ("Transcription (mlx-whisper or faster-whisper)", lambda: check_package(report, "mlx_whisper", "faster_whisper")),
        ("WhisperX word alignment", lambda: check_package(report, "whisperx")),
        ("torch", lambda: check_package(report, "torch")),
        ("yt-dlp", lambda: check_package(report, "yt_dlp")),
        ("Free disk space", lambda: check_disk(probe)),
    ]
    return [_row(label, True, check) for label, check in checks]


def format_table(rows: List[Row]) -> str:
    status_width = max(len("status"), *(len(row["status"]) for row in rows))
    check_width = max(len("check"), *(len(row["check"]) for row in rows))
    lines = [f"{'status':<{status_width}}  {'check':<{check_width}}  fix", f"{'-' * status_width}  {'-' * check_width}  ---"]
    for row in rows:
        lines.append(f"{row['status']:<{status_width}}  {row['check']:<{check_width}}  {row['fix']}".rstrip())
    return "\n".join(lines)


def summary(rows: List[Row]) -> str:
    sentence = _summary_sentence(rows)
    if any(row["fix"] == FIX_OUTSIDE_SANDBOX for row in rows):
        return f"{sentence}\n{SANDBOX_NOTE}"
    return sentence


def _summary_sentence(rows: List[Row]) -> str:
    missing = sum(1 for row in rows if row["status"] == "missing")
    if missing == 1:
        return "1 check needs fixing. Fix it, then run python3 bin/doctor.py again."
    if missing:
        return f"{missing} checks need fixing. Fix them in the order shown, then run python3 bin/doctor.py again."
    if any(row["status"] == "warn" for row in rows):
        return "Nothing required is missing. The rows marked warn are worth a look, but they do not stop you."
    return "Everything the kit needs is installed."


def main(argv: Optional[List[str]] = None, probe: Optional[Probe] = None, out: Any = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 bin/doctor.py",
        description="Check this Mac for everything the kit needs and say how to fix what is missing.",
    )
    parser.add_argument("--json", action="store_true", help="print the rows as JSON instead of a table")
    args = parser.parse_args(argv)

    out = out or sys.stdout
    probe = probe or Probe(progress=not args.json)
    rows = collect(probe)
    if args.json:
        print(json.dumps(rows, indent=2), file=out)
    else:
        print("Machine check for the content intelligence kit\n", file=out)
        print(format_table(rows), file=out)
        footer = footer_line(probe)
        if footer:
            print("\n" + footer, file=out)
        print("\n" + summary(rows), file=out)
    return 1 if any(row["status"] == "missing" for row in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
