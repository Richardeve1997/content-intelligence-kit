"""kit.telemetry: the one place every HyperFrames call takes the switches that turn its usage reporting off.

The HyperFrames CLI reports usage unless HYPERFRAMES_NO_TELEMETRY or DO_NOT_TRACK is set. The kit sets both on
every call it makes. The tests for each call (kit.media run, the machine check, the mini me renderer and the UI
story test clip) sit beside the other tests for that code. These check the module itself, and that no other kit
file sets the switches on its own.
"""

import ast
import os
import sys
from pathlib import Path

from kit import telemetry

ROOT = Path(__file__).resolve().parent.parent
MODULE = ROOT / "kit" / "telemetry.py"
SWITCH = "HYPERFRAMES_" + "NO_TELEMETRY"  # built in two parts, so the scan below never finds it in this file


def test_both_switches_are_on():
    assert telemetry.OFF == {SWITCH: "1", "DO_NOT_TRACK": "1"}


def test_env_turns_both_on_and_keeps_everything_else():
    base = {"PATH": "/opt/example/bin", "DO_NOT_TRACK": "0", SWITCH: "false"}
    env = telemetry.env(base)
    assert env == {"PATH": "/opt/example/bin", "DO_NOT_TRACK": "1", SWITCH: "1"}
    assert base["DO_NOT_TRACK"] == "0", "the environment it was given is left as it was"


def test_env_starts_from_this_process_and_changes_nothing_in_it(monkeypatch):
    monkeypatch.setenv("KIT_EXAMPLE_SETTING", "kept")
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    env = telemetry.env()
    assert env["KIT_EXAMPLE_SETTING"] == "kept" and env["DO_NOT_TRACK"] == "1"
    assert "DO_NOT_TRACK" not in os.environ


def test_the_machine_check_can_read_it_with_the_python_a_mac_ships():
    """bin/doctor.py loads this file before the kit's environment exists, on the Mac's own Python 3.9."""
    for path in (ROOT / "kit" / "__init__.py", MODULE):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, feature_version=(3, 9))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                imported.add((node.module or "").split(".")[0])
        assert imported <= set(sys.stdlib_module_names) | {"__future__"}, f"{path.name} imports {imported}"


def test_no_other_kit_file_sets_the_switches_itself():
    """Every call takes the switches from kit.telemetry, so a second copy that could drift never appears."""
    found = []
    for folder in ("kit", "bin", ".claude/skills"):
        for path in sorted((ROOT / folder).rglob("*")):
            if path.suffix not in {".py", ".sh", ".js", ".md"} or path == MODULE or "humanizer" in path.parts:
                continue
            if SWITCH in path.read_text(encoding="utf-8", errors="replace"):
                found.append(path.relative_to(ROOT).as_posix())
    assert found == []
