"""The gitleaks config: the default rules plus two narrow exceptions.

The release manifest pairs a SHA-256 with every file path, and one path contains the word "tokens", which
the default generic-api-key rule reads as a key. One earlier test fixture is a made-up value that rule also
reports. The config allows exactly those two and nothing wider: the manifest by path, and the fixture only
for its file in its commit, and both only for the generic-api-key rule, so any other rule still reports what
it finds there. The scoping is checked by running gitleaks on throwaway repositories, so the values used
here are built at run time and never written into this file.
"""

import hashlib
import json
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / ".gitleaks.toml"
GITLEAKS = shutil.which("gitleaks")
needs_gitleaks = pytest.mark.skipif(GITLEAKS is None, reason="gitleaks is not installed")


def test_the_config_extends_the_default_rules_and_names_two_exceptions():
    config = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    assert set(config) == {"title", "extend", "allowlists"}
    assert config["extend"] == {"useDefault": True}
    first, second = config["allowlists"]

    assert set(first) == {"description", "targetRules", "paths"}
    assert first["targetRules"] == ["generic-api-key"], "only the rule that misreads the hashes"
    assert first["paths"] == [r"^\.kit-manifest\.json$"]

    assert set(second) == {"description", "targetRules", "condition", "commits", "paths"}, (
        "no pattern or stop word that could hide a value"
    )
    assert second["targetRules"] == ["generic-api-key"], "only the rule that reported the fixture"
    assert second["condition"] == "AND", "the commit and the path must both match"
    assert len(second["commits"]) == 1 and len(second["commits"][0]) == 40
    assert set(second["commits"][0]) <= set("0123456789abcdef")
    assert second["paths"] == [r"^tests/test_config\.py$"]


# --- what the exceptions allow, run through gitleaks ---------------------------------------------------------------------


def manifest_like() -> str:
    entries = {
        "kit/config.py": hashlib.sha256(b"one").hexdigest(),
        "me.example/brand/tokens.css": hashlib.sha256(b"two").hexdigest(),
        "pyproject.toml": hashlib.sha256(b"three").hexdigest(),
    }
    return json.dumps(entries, indent=2) + "\n"


def token_shaped() -> str:
    """A value only the github-pat rule reports: its prefix and 36 letters and digits. Built at run time."""
    return "ghp" + "_" + hashlib.sha256(b"another rule").hexdigest()[:36]


def made_up_secret() -> str:
    """A value shaped like a key, with enough variety that the generic rule reports it. A bare hash is not reported."""
    return f'secret = "dk_{hashlib.sha256(b"scoping fixture").hexdigest()[:20]}-xq"\n'


def commit(repo: Path, files: dict[str, str]) -> None:
    for name, body in files.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    settings = ["-c", "user.name=Throwaway", "-c", "user.email=throwaway", "-c", "core.hooksPath=/dev/null"]
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", *settings, "-c", "commit.gpgsign=false", "commit", "-qm", "files"], cwd=repo, check=True)


def findings(repo: Path, config: Path) -> list[tuple[str, str]]:
    """(rule, file) for each thing gitleaks reports anywhere in the history of `repo`."""
    report = repo.parent / f"{repo.name}-report.json"
    done = subprocess.run(
        [GITLEAKS, "detect", "--source", str(repo), "--config", str(config), "--log-opts=--all", "--redact",
         "--no-banner", "--report-format", "json", "--report-path", str(report), "--exit-code", "0"],
        capture_output=True,
        text=True,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    found = json.loads(report.read_text(encoding="utf-8")) if report.exists() else []
    return sorted((item["RuleID"], item["File"]) for item in found)


@pytest.fixture
def repo(tmp_path):
    folder = tmp_path / "repo"
    folder.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=folder, check=True)
    return folder


@pytest.fixture
def default_rules_only(tmp_path):
    path = tmp_path / "default-only.toml"
    path.write_text("[extend]\nuseDefault = true\n", encoding="utf-8")
    return path


@needs_gitleaks
def test_without_the_exceptions_both_kinds_of_value_are_reported(repo, default_rules_only):
    commit(repo, {".kit-manifest.json": manifest_like(), "tests/test_config.py": made_up_secret()})
    assert findings(repo, default_rules_only) == [
        ("generic-api-key", ".kit-manifest.json"),
        ("generic-api-key", "tests/test_config.py"),
    ]


@needs_gitleaks
def test_the_manifest_is_allowed_by_its_path(repo):
    commit(repo, {".kit-manifest.json": manifest_like()})
    assert findings(repo, CONFIG) == []


@needs_gitleaks
def test_another_rule_still_reports_what_it_finds_in_the_manifest(repo, default_rules_only):
    entries = json.loads(manifest_like())
    entries["kit/notes.txt"] = token_shaped()
    commit(repo, {".kit-manifest.json": json.dumps(entries, indent=2) + "\n"})
    assert ("github-pat", ".kit-manifest.json") in findings(repo, default_rules_only), "the probe is a real finding"
    assert findings(repo, CONFIG) == [("github-pat", ".kit-manifest.json")]


@needs_gitleaks
def test_the_same_content_in_any_other_file_is_still_reported(repo):
    commit(repo, {"notes/manifest.json": manifest_like(), "kit-manifest.json": manifest_like()})
    assert findings(repo, CONFIG) == [("generic-api-key", "kit-manifest.json"), ("generic-api-key", "notes/manifest.json")]


@needs_gitleaks
def test_the_test_fixture_is_not_allowed_outside_its_commit(repo):
    commit(repo, {"tests/test_config.py": made_up_secret()})
    assert findings(repo, CONFIG) == [("generic-api-key", "tests/test_config.py")], "right file, wrong commit"


@needs_gitleaks
def test_a_made_up_value_in_another_file_is_still_reported(repo):
    commit(repo, {"tests/test_other.py": made_up_secret(), "kit/settings.py": made_up_secret()})
    assert findings(repo, CONFIG) == [("generic-api-key", "kit/settings.py"), ("generic-api-key", "tests/test_other.py")]
