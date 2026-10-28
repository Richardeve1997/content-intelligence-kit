"""bin/update_kit.py: replace the kit's own files from a release and leave the creator's files alone.

Kit files are every path except me/, work/, research/, .env and .git/ at the top of the kit folder.
Nothing here touches the network. Releases are small tarballs built on the fly in the shape of
GitHub's archive downloads (one top-level folder, a global header, a link and an executable), the
"latest release" lookup is a fake, and a guard fails any test that tries to open a connection.
"""

import hashlib
import importlib.util
import io
import json
import os
import socket
import ssl
import subprocess
import sys
import tarfile
import tempfile
import types
import urllib.error
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "bin" / "update_kit.py"
EM_DASH, EN_DASH = chr(0x2014), chr(0x2013)  # written as code points so this file holds no dash
TODAY = "2026-10-28"
KEPT_HEADING = "Kept your edits (the release did not change these files):"
BACKUPS = f"me/kit-backups/{TODAY}"

V1 = {
    ".env.example": "API_KEY=\n",
    ".claude/skills/alpha/SKILL.md": "alpha one\n",
    ".claude/skills/beta/SKILL.md": "beta one\n",
    ".claude/skills/old/SKILL.md": "old one\n",
    "CLAUDE.md": "claude one\n",
    "bin/run.sh": "#!/bin/sh\necho one\n",
    "kit/core.py": "VALUE = 1\n",
    "kit/research/rank.py": "RANK = 1\n",  # kit-owned although its folder shares a name with research/
    "me.example/profile.json": "{}\n",
    "pyproject.toml": "[project]\nname = 'demo'\n",
}
LINKS = {".agents/skills": "../.claude/skills"}
EXECUTABLE = {"bin/run.sh"}

V2 = {
    **V1,
    ".env.example": "API_KEY=\nOTHER_KEY=\n",
    ".claude/skills/alpha/SKILL.md": "alpha two\n",
    ".claude/skills/gamma/SKILL.md": "gamma two\n",
    "kit/core.py": "VALUE = 2\n",
    "kit/research/rank.py": "RANK = 2\n",
    "pyproject.toml": "[project]\nname = 'demo'\nversion = '2'\n",
}
del V2[".claude/skills/old/SKILL.md"]
ADDED = [".claude/skills/gamma/SKILL.md"]
REPLACED = [".claude/skills/alpha/SKILL.md", ".env.example", "kit/core.py", "kit/research/rank.py", "pyproject.toml"]
REMOVED = [".claude/skills/old/SKILL.md"]

CREATOR = {
    "me/profile.json": b'{"name": "Someone"}\n',
    "me/voice/VOICE.md": b"My voice.\n",
    "me/rules/post.md": b"- 2026-10-01: Keep it plain.\n",
    "work/example-reel/final.mp4": bytes(range(256)) * 8,
    "work/example-reel/take/take-01.mov": b"raw take",
    "research/runs/2026-10-27/ideas.json": b"[]\n",
    ".env": b"APIFY_TOKEN=not-a-real-token\n",
}
HOSTILE = {
    "me/profile.json": "overwritten\n",
    "me/brand/tokens.css": "overwritten\n",
    "work/example-reel/final.mp4": "overwritten\n",
    "research/runs/2026-10-27/ideas.json": "overwritten\n",
    ".env": "OVERWRITTEN=1\n",
    ".git/config": "overwritten\n",
    "Me/profile.json": "overwritten\n",  # macOS treats these as the same folder and file
    ".ENV": "OVERWRITTEN=1\n",
}


# --- helpers --------------------------------------------------------------------------------------------


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def link_sha(target: str) -> str:
    return sha(b"symlink:" + target.encode())


def make_release(path, tag, files, *, links=None, executable=(), top=None, extra=()):
    """A release file shaped like GitHub's: one top-level folder and a global header with the commit."""
    top = top or f"content-intelligence-kit-{tag}"
    with tarfile.open(path, "w:gz", format=tarfile.PAX_FORMAT, pax_headers={"comment": "a" * 40}) as tar:
        folder = tarfile.TarInfo(top)
        folder.type, folder.mode = tarfile.DIRTYPE, 0o755
        tar.addfile(folder)
        for name, body in sorted(files.items()):
            data = body.encode() if isinstance(body, str) else body
            info = tarfile.TarInfo(f"{top}/{name}")
            info.size = len(data)
            info.mode = 0o755 if name in executable else 0o644
            tar.addfile(info, io.BytesIO(data))
        for name, target in (links or {}).items():
            info = tarfile.TarInfo(f"{top}/{name}")
            info.type, info.linkname, info.mode = tarfile.SYMTYPE, target, 0o777
            tar.addfile(info)
        for info, data in extra:
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return Path(path)


def write(root: Path, files: dict) -> None:
    for rel, body in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body if isinstance(body, bytes) else body.encode())


def snapshot(root: Path) -> dict:
    """Everything under root: type, content and modification time. Equal snapshots mean nothing changed."""
    state = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            state[rel] = ("link", os.readlink(path))
        elif path.is_dir():
            state[rel] = ("dir",)
        else:
            state[rel] = ("file", path.stat().st_mode, path.stat().st_mtime_ns, sha(path.read_bytes()))
    return state


def listing(root: Path, *folders: str) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for f in folders for p in (root / f).rglob("*"))


def plain(text: str) -> str:
    assert "Traceback" not in text
    assert EM_DASH not in text and EN_DASH not in text
    return text


def manifest_of(root: Path) -> dict:
    return json.loads((root / ".kit-manifest.json").read_text(encoding="utf-8"))


def expected_manifest(files: dict, links: dict) -> dict:
    found = {name: sha(body.encode() if isinstance(body, str) else body) for name, body in files.items()}
    found.update({name: link_sha(target) for name, target in links.items()})
    return dict(sorted(found.items()))


# --- fixtures -------------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to open a network connection")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


@pytest.fixture
def update_kit(monkeypatch):
    """The script, loaded fresh for each test. KIT_ME is cleared so backups land in <kit>/me, and the date is fixed."""
    monkeypatch.delenv("KIT_ME", raising=False)
    spec = importlib.util.spec_from_file_location("update_kit_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        monkeypatch.setattr(module, "_today", lambda: date(2026, 10, 28))
        yield module
    finally:
        sys.modules.pop(spec.name, None)


@pytest.fixture
def releases(tmp_path):
    return types.SimpleNamespace(
        v1=make_release(tmp_path / "v1.tar.gz", "v1", V1, links=LINKS, executable=EXECUTABLE),
        v2=make_release(tmp_path / "v2.tar.gz", "v2", V2, links=LINKS, executable=EXECUTABLE),
    )


@pytest.fixture
def installed(tmp_path, update_kit, releases, capsys):
    """A kit folder with release v1 installed by the script itself, as a first install."""
    root = tmp_path / "kit"
    root.mkdir()
    assert update_kit.main(["--apply", "--from", str(releases.v1)], root=root) == 0
    capsys.readouterr()
    return root


def apply(update_kit, root, release, capsys, *flags):
    code = update_kit.main(["--apply", "--from", str(release), *flags], root=root)
    out = capsys.readouterr()
    return code, plain(out.out), plain(out.err)


def check(update_kit, root, release, capsys, *flags):
    code = update_kit.main(["--from", str(release), *flags], root=root)
    out = capsys.readouterr()
    return code, plain(out.out), plain(out.err)


# --- installing -----------------------------------------------------------------------------------------


def test_a_first_install_into_an_empty_folder(update_kit, releases, tmp_path, capsys):
    root = tmp_path / "fresh"
    root.mkdir()

    code, out, err = apply(update_kit, root, releases.v1, capsys)

    assert (code, err) == (0, "")
    assert "Updated to release v1" in out
    for rel, body in V1.items():
        assert (root / rel).read_text() == body
    assert os.access(root / "bin/run.sh", os.X_OK)
    assert not os.access(root / "kit/core.py", os.X_OK)
    link = root / ".agents/skills"
    assert link.is_symlink() and os.readlink(link) == "../.claude/skills"
    assert manifest_of(root) == expected_manifest(V1, LINKS)
    # nothing is created in the creator's folders, and nothing is left over
    assert not (root / "me").exists() and not (root / "work").exists() and not (root / ".env").exists()
    assert sorted(p.name for p in root.iterdir()) == sorted(
        {".agents", ".claude", ".env.example", ".kit-manifest.json", "CLAUDE.md", "bin", "kit", "me.example", "pyproject.toml"}
    )


# --- the creator's files --------------------------------------------------------------------------------


def test_update_never_touches_creator_paths(update_kit, installed, releases, tmp_path, capsys):
    write(installed, CREATOR)
    before_listing = listing(installed, "me", "work", "research")
    hostile = make_release(tmp_path / "v2-hostile.tar.gz", "v2", {**V2, **HOSTILE}, links=LINKS, executable=EXECUTABLE)

    code, out, err = apply(update_kit, installed, hostile, capsys)

    assert (code, err) == (0, "")
    for rel, body in CREATOR.items():
        assert (installed / rel).read_bytes() == body, f"{rel} changed"
    assert listing(installed, "me", "work", "research") == before_listing
    assert not (installed / ".git").exists()
    # kit files whose names only look similar were updated
    assert (installed / "kit/research/rank.py").read_text() == "RANK = 2\n"
    assert (installed / ".env.example").read_text() == V2[".env.example"]
    # and the manifest does not record any creator path
    recorded = manifest_of(installed)
    assert not [p for p in recorded if p.split("/")[0].casefold() in {"me", "work", "research", ".env", ".git"}]
    assert "The release also held 8 files under your own folders" in out
    assert "Your me, work and research folders and your .env were not touched." in out


def test_a_manifest_that_names_creator_paths_cannot_make_the_update_delete_them(update_kit, installed, releases, tmp_path, capsys):
    write(installed, CREATOR)
    outside = tmp_path / "outside.txt"
    outside.write_text("keep me\n")
    forged = manifest_of(installed)
    forged.update({rel: "0" * 64 for rel in [*CREATOR, ".git/config", "../outside.txt", "/etc/hosts", "kit/../../outside.txt"]})
    (installed / ".kit-manifest.json").write_text(json.dumps(forged))

    code, out, err = apply(update_kit, installed, releases.v2, capsys)

    assert (code, err) == (0, "")
    for rel, body in CREATOR.items():
        assert (installed / rel).read_bytes() == body
    assert outside.read_text() == "keep me\n"
    assert (installed / "kit/core.py").read_text() == "VALUE = 2\n"


def test_files_in_kit_areas_that_are_not_part_of_the_kit_are_left_alone(update_kit, installed, releases, capsys):
    extras = {
        ".venv/lib/site.py": "venv\n",
        "node_modules/pkg/index.js": "module\n",
        "kit/__pycache__/core.cpython-311.pyc": "cache\n",
        "kit/vendor/gsap.min.js": "// fetched at setup\n",
        "kit/my_extra.py": "MINE = True\n",
        ".claude/settings.local.json": "{}\n",
        "notes.txt": "mine\n",
    }
    write(installed, extras)

    code, out, err = apply(update_kit, installed, releases.v2, capsys)

    assert code == 0
    for rel, body in extras.items():
        assert (installed / rel).read_text() == body
    assert "my_extra" not in out and "notes.txt" not in out


# --- local edits ----------------------------------------------------------------------------------------


def test_update_backs_up_local_edit(update_kit, installed, releases, capsys, monkeypatch):
    alpha = installed / ".claude/skills/alpha/SKILL.md"  # the release changes this one
    beta = installed / ".claude/skills/beta/SKILL.md"  # the release leaves this one as it was
    alpha.write_text("alpha one, edited by the creator\n")
    beta.write_text("beta one, edited too\n")
    backup_alpha = installed / BACKUPS / ".claude/skills/alpha/SKILL.md"

    at_first_replacement = []
    real_replace = os.replace

    def spy(source, destination, *args, **kwargs):
        if not at_first_replacement:
            at_first_replacement.append(backup_alpha.is_file())
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(update_kit.os, "replace", spy)

    code, out, err = apply(update_kit, installed, releases.v2, capsys)

    assert (code, err) == (0, "")
    assert at_first_replacement == [True], "the edit must be copied before any file is replaced"
    assert backup_alpha.read_text() == "alpha one, edited by the creator\n"
    assert alpha.read_text() == "alpha two\n"
    # an edit to a file the release did not change is left alone, and needs no backup
    assert beta.read_text() == "beta one, edited too\n"
    assert sorted(p.relative_to(installed / BACKUPS).as_posix() for p in (installed / BACKUPS).rglob("*") if p.is_file()) == [
        ".claude/skills/alpha/SKILL.md"
    ]
    # the output lists the backup, and says so before it reports the update as done
    assert f"Copied your edits to {BACKUPS}/ before replacing the files:" in out
    notice = out.index("Copied your edits")
    assert notice < out.index(".claude/skills/alpha/SKILL.md", notice)
    assert notice < out.index("Updated to release v2")
    assert "Updated to release v2: 1 added, 5 replaced, 1 removed, 4 already the same, 1 kept as you had them." in out
    kept = out[out.index(KEPT_HEADING) :]
    assert "  .claude/skills/beta/SKILL.md\n" in kept and ".claude/skills/alpha/SKILL.md" not in kept


def test_an_edit_the_release_did_not_change_is_kept_listed_and_never_called_a_match(update_kit, installed, releases, capsys):
    beta = installed / ".claude/skills/beta/SKILL.md"
    beta.write_text("beta one, edited\n")
    before = snapshot(installed)

    for how in (check, apply):
        code, out, err = how(update_kit, installed, releases.v1, capsys)  # the release this kit already has
        assert (code, err) == (0, "")
        assert "already matches" not in out and "Nothing to do" not in out
        assert out.startswith("Release v1 has nothing new for your kit. Your edits stay as you had them.\n")
        assert out.endswith(f"{KEPT_HEADING}\n  .claude/skills/beta/SKILL.md\n")
        assert snapshot(installed) == before, "nothing is written, not even backups or the manifest"
    assert beta.read_text() == "beta one, edited\n"


def test_kept_edits_are_listed_by_check_and_by_apply_next_to_the_changes(update_kit, installed, releases, capsys):
    (installed / ".claude/skills/beta/SKILL.md").write_text("beta one, edited\n")
    (installed / "CLAUDE.md").write_text("claude one, edited\n")

    code, out, err = check(update_kit, installed, releases.v2, capsys)
    assert "Release v2 against your kit: 1 to add, 5 to replace, 1 to remove, 3 already the same, 2 kept as you had them." in out
    kept = out[out.index(KEPT_HEADING) :]
    assert "  .claude/skills/beta/SKILL.md\n  CLAUDE.md\n" in kept
    assert out.index("To remove:") < out.index(KEPT_HEADING) < out.index("Nothing was changed")

    code, out, err = apply(update_kit, installed, releases.v2, capsys)
    assert "1 added, 5 replaced, 1 removed, 3 already the same, 2 kept as you had them." in out
    assert "  .claude/skills/beta/SKILL.md\n  CLAUDE.md\n" in out[out.index(KEPT_HEADING) :]
    assert (installed / "CLAUDE.md").read_text() == "claude one, edited\n"


def test_the_manifest_keeps_the_release_digest_of_a_file_the_creator_edited(update_kit, installed, releases, tmp_path, capsys):
    beta = installed / ".claude/skills/beta/SKILL.md"
    beta.write_text("beta one, edited\n")
    apply(update_kit, installed, releases.v2, capsys)
    assert manifest_of(installed) == expected_manifest(V2, LINKS), "the manifest records the release, not the edited file"
    assert beta.read_text() == "beta one, edited\n"

    # a later release that does change the file replaces the edit, after a backup
    v3 = make_release(tmp_path / "v3.tar.gz", "v3", {**V2, ".claude/skills/beta/SKILL.md": "beta three\n"}, links=LINKS, executable=EXECUTABLE)
    code, out, err = apply(update_kit, installed, v3, capsys)
    assert code == 0
    assert beta.read_text() == "beta three\n"
    assert (installed / BACKUPS / ".claude/skills/beta/SKILL.md").read_text() == "beta one, edited\n"
    assert KEPT_HEADING not in out


def test_a_file_the_creator_deleted_stays_deleted_when_the_release_did_not_change_it(update_kit, installed, releases, capsys):
    (installed / ".claude/skills/beta/SKILL.md").unlink()

    code, out, err = apply(update_kit, installed, releases.v2, capsys)

    assert (code, err) == (0, "")
    assert not (installed / ".claude/skills/beta/SKILL.md").exists()
    assert "  .claude/skills/beta/SKILL.md (you deleted it)\n" in out[out.index(KEPT_HEADING) :]
    assert "Restored" not in out
    assert manifest_of(installed) == expected_manifest(V2, LINKS)


def test_a_deleted_file_comes_back_when_the_release_changed_it(update_kit, installed, releases, capsys):
    (installed / ".claude/skills/alpha/SKILL.md").unlink()

    code, out, err = check(update_kit, installed, releases.v2, capsys)
    assert "1 to add, 1 to restore, 4 to replace, 1 to remove, 5 already the same." in out
    heading = "To restore (you deleted these, and the release changed them):"
    assert "  .claude/skills/alpha/SKILL.md\n" in out[out.index(heading) :]
    assert not (installed / ".claude/skills/alpha/SKILL.md").exists(), "check changes nothing"

    code, out, err = apply(update_kit, installed, releases.v2, capsys)
    assert (code, err) == (0, "")
    assert (installed / ".claude/skills/alpha/SKILL.md").read_text() == "alpha two\n"
    assert "1 added, 1 restored, 4 replaced, 1 removed, 5 already the same." in out
    assert "  .claude/skills/alpha/SKILL.md\n" in out[out.index("Restored (you deleted these, and the release changed them):") :]
    assert "Copied your edits" not in out, "a deleted file has nothing to back up"


def test_without_a_manifest_a_deleted_file_is_simply_added_again(update_kit, installed, releases, capsys):
    (installed / ".kit-manifest.json").unlink()
    (installed / ".claude/skills/beta/SKILL.md").unlink()
    code, out, err = apply(update_kit, installed, releases.v2, capsys)
    assert code == 0
    assert (installed / ".claude/skills/beta/SKILL.md").read_text() == "beta one\n"
    assert KEPT_HEADING not in out


def test_backups_go_to_the_me_folder_that_kit_me_points_at(update_kit, installed, releases, tmp_path, capsys, monkeypatch):
    elsewhere = tmp_path / "elsewhere" / "me"
    monkeypatch.setenv("KIT_ME", str(elsewhere))
    (installed / ".claude/skills/alpha/SKILL.md").write_text("alpha one, edited\n")

    code, out, err = apply(update_kit, installed, releases.v2, capsys)

    assert code == 0
    assert (elsewhere / "kit-backups" / TODAY / ".claude/skills/alpha/SKILL.md").read_text() == "alpha one, edited\n"
    assert not (installed / "me").exists()
    assert f"Copied your edits to {elsewhere}/kit-backups/{TODAY}/ before replacing the files:" in out


def test_an_edit_to_a_file_the_release_removes_is_backed_up_then_removed(update_kit, installed, releases, capsys):
    old = installed / ".claude/skills/old/SKILL.md"
    old.write_text("old one, edited\n")

    code, out, err = apply(update_kit, installed, releases.v2, capsys)

    assert code == 0
    assert (installed / BACKUPS / ".claude/skills/old/SKILL.md").read_text() == "old one, edited\n"
    assert not old.exists()
    assert ".claude/skills/old/SKILL.md" in out[out.index("Copied your edits") :]


def test_a_file_the_release_removes_goes_when_it_is_unedited_and_its_empty_folder_goes_too(update_kit, installed, releases, capsys):
    (installed / ".claude/skills/old/.DS_Store").write_text("junk\n")
    (installed / ".claude/skills/old/__pycache__").mkdir()
    (installed / ".claude/skills/old/__pycache__/x.pyc").write_text("cache\n")

    code, out, err = apply(update_kit, installed, releases.v2, capsys)

    assert code == 0
    assert not (installed / ".claude/skills/old").exists()
    assert (installed / ".claude/skills").is_dir()
    assert not (installed / "me").exists(), "no backup folder is made when nothing was edited"


def test_a_folder_with_other_files_in_it_stays_after_the_release_removes_its_file(update_kit, installed, releases, capsys):
    (installed / ".claude/skills/old/my-notes.md").write_text("mine\n")
    code, out, err = apply(update_kit, installed, releases.v2, capsys)
    assert code == 0
    assert not (installed / ".claude/skills/old/SKILL.md").exists()
    assert (installed / ".claude/skills/old/my-notes.md").read_text() == "mine\n"


def test_a_second_backup_on_the_same_day_does_not_overwrite_the_first(update_kit, installed, releases, tmp_path, capsys):
    alpha = installed / ".claude/skills/alpha/SKILL.md"
    alpha.write_text("first edit\n")
    apply(update_kit, installed, releases.v2, capsys)
    alpha.write_text("second edit\n")
    v3 = make_release(tmp_path / "v3.tar.gz", "v3", {**V2, ".claude/skills/alpha/SKILL.md": "alpha three\n"}, links=LINKS, executable=EXECUTABLE)

    apply(update_kit, installed, v3, capsys)

    saved = {p.name: p.read_text() for p in (installed / BACKUPS / ".claude/skills/alpha").iterdir()}
    assert saved == {"SKILL.md": "first edit\n", "SKILL.1.md": "second edit\n"}
    assert alpha.read_text() == "alpha three\n"


def test_without_a_manifest_every_difference_counts_as_an_edit(update_kit, installed, releases, capsys):
    (installed / ".kit-manifest.json").unlink()

    code, out, err = apply(update_kit, installed, releases.v2, capsys)

    assert (code, err) == (0, "")
    assert "There is no .kit-manifest.json" in out
    saved = sorted(p.relative_to(installed / BACKUPS).as_posix() for p in (installed / BACKUPS).rglob("*") if p.is_file())
    assert saved == REPLACED
    assert (installed / "kit/core.py").read_text() == "VALUE = 2\n"
    assert (installed / ".claude/skills/old/SKILL.md").exists(), "without a manifest nothing can be told to have been removed"
    assert manifest_of(installed) == expected_manifest(V2, LINKS)


@pytest.mark.parametrize("damage", ["not json at all", "[1, 2, 3]", '{"kit/core.py": 7}'])
def test_a_damaged_manifest_is_treated_like_a_missing_one(update_kit, installed, releases, capsys, damage):
    (installed / ".kit-manifest.json").write_text(damage)
    code, out, err = apply(update_kit, installed, releases.v2, capsys)
    assert (code, err) == (0, "")
    assert ".kit-manifest.json" in out and "every file that differs" in out
    assert manifest_of(installed) == expected_manifest(V2, LINKS)


# --- checking ---------------------------------------------------------------------------------------------


def test_check_is_read_only(update_kit, installed, releases, tmp_path, capsys, monkeypatch):
    (installed / ".claude/skills/alpha/SKILL.md").write_text("alpha one, edited\n")
    write(installed, CREATOR)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    before = snapshot(installed)

    code, out, err = check(update_kit, installed, releases.v2, capsys, "--check")
    default_code, default_out, default_err = check(update_kit, installed, releases.v2, capsys)

    assert (code, err) == (0, "") and (default_code, default_err) == (0, "")
    assert default_out == out, "check is the default"
    assert snapshot(installed) == before
    assert not (installed / "me/kit-backups").exists()
    assert list(scratch.iterdir()) == [], "temporary files are cleaned up"
    # it says what would happen
    assert "Release v2 against your kit: 1 to add, 5 to replace, 1 to remove, 5 already the same." in out
    for heading, paths in (("To add:", ADDED), ("To replace:", REPLACED), ("To remove:", REMOVED)):
        section = out[out.index(heading) :]
        for path in paths:
            assert f"  {path}\n" in section
    assert f"You edited 1 of these files. Each edit would be copied to {BACKUPS}/ before the file is replaced:" in out
    assert out.count(".claude/skills/alpha/SKILL.md") == 2  # once as a change, once as an edit
    assert out.rstrip().endswith("Nothing was changed. Run this again with --apply to update.")


def test_check_says_so_when_the_kit_already_matches(update_kit, installed, releases, capsys):
    apply(update_kit, installed, releases.v2, capsys)
    code, out, err = check(update_kit, installed, releases.v2, capsys)
    assert (code, err) == (0, "")
    assert out == "Your kit already matches release v2. Nothing to do.\n"


def test_applying_twice_changes_nothing_the_second_time(update_kit, installed, releases, capsys):
    apply(update_kit, installed, releases.v2, capsys)
    before = snapshot(installed)

    code, out, err = apply(update_kit, installed, releases.v2, capsys)

    assert (code, err) == (0, "")
    assert out == "Your kit already matches release v2. Nothing to do.\n"
    assert snapshot(installed) == before


def test_the_manifest_after_an_update_records_the_release_digest_of_every_file(update_kit, installed, releases, capsys):
    apply(update_kit, installed, releases.v2, capsys)
    assert manifest_of(installed) == expected_manifest(V2, LINKS)
    for rel, digest in manifest_of(installed).items():
        path = installed / rel
        actual = link_sha(os.readlink(path)) if path.is_symlink() else sha(path.read_bytes())
        assert actual == digest, rel


def test_a_long_list_is_cut_after_fifty_names(update_kit, installed, tmp_path, capsys):
    many = {**V2, **{f"docs/page-{n:02d}.md": "page\n" for n in range(60)}}
    big = make_release(tmp_path / "big.tar.gz", "v3", many, links=LINKS, executable=EXECUTABLE)
    code, out, err = apply(update_kit, installed, big, capsys)
    assert code == 0
    # 60 new pages and the new skill make 61 added files, listed in order: the skill, then pages 00 to 48
    assert "  docs/page-48.md\n" in out and "  docs/page-49.md\n" not in out
    assert "  ...and 11 more\n" in out
    assert (installed / "docs/page-59.md").read_text() == "page\n", "every file is installed, whatever the report shows"


def test_the_report_lists_what_changed(update_kit, installed, releases, capsys):
    code, out, err = apply(update_kit, installed, releases.v2, capsys)
    assert code == 0
    assert "Updated to release v2: 1 added, 5 replaced, 1 removed, 5 already the same." in out
    for heading, paths in (("Added:", ADDED), ("Replaced:", REPLACED), ("Removed:", REMOVED)):
        section = out[out.index(heading) :]
        for path in paths:
            assert f"  {path}\n" in section
    assert "Copied your edits" not in out


def test_changed_dependencies_are_pointed_out(update_kit, installed, releases, tmp_path, capsys):
    code, out, err = check(update_kit, installed, releases.v2, capsys)
    assert "run: uv sync --group video --group research --inexact, then npm install" in out
    assert "If you built a mini me character, add --group character to that command." in out
    assert out.index("uv sync") < out.index("Nothing was changed")

    code, out, err = apply(update_kit, installed, releases.v2, capsys)
    assert "Run: uv sync --group video --group research --inexact, then npm install." in out
    assert "add --group character to that command" in out
    same_dependencies = make_release(
        tmp_path / "v3.tar.gz", "v3", {**V2, "kit/core.py": "VALUE = 3\n"}, links=LINKS, executable=EXECUTABLE
    )
    code, out, err = apply(update_kit, installed, same_dependencies, capsys)
    assert "uv sync" not in out


def test_permissions_and_links_are_kept(update_kit, installed, releases, tmp_path, capsys):
    (installed / "bin/run.sh").chmod(0o644)
    apply(update_kit, installed, releases.v2, capsys)
    assert os.access(installed / "bin/run.sh", os.X_OK), "a file the release marks executable is executable"
    link = installed / ".agents/skills"
    assert link.is_symlink() and link.resolve() == (installed / ".claude/skills").resolve()


# --- release files that cannot be used ----------------------------------------------------------------------


def _file(name, size=1):
    info = tarfile.TarInfo(name)
    info.size = size
    return info, b"x" * size


def _link(name, target, kind=tarfile.SYMTYPE):
    info = tarfile.TarInfo(name)
    info.type, info.linkname = kind, target
    return info, None


def _special(name, kind):
    info = tarfile.TarInfo(name)
    info.type = kind
    return info, None


def unsafe_members(tmp_path):
    top = "content-intelligence-kit-v9"
    return {
        "path leaves the folder": [_file(f"{top}/../escape.txt")],
        "absolute path": [_file(str(tmp_path / "absolute-escape.txt"))],
        "link points out": [_link(f"{top}/docs", "../../outside")],
        "link is absolute": [_link(f"{top}/docs", str(tmp_path))],
        "hard link": [_link(f"{top}/docs", "kit/core.py", tarfile.LNKTYPE)],
        "device file": [_special(f"{top}/dev", tarfile.CHRTYPE)],
        "second top-level folder": [_file("other-folder/file.txt")],
        "file beside the folder": [_file("README.md")],
        "file below a link": [_file(f"{top}/.agents/skills/sneaky.md")],
    }


UNSAFE = list(unsafe_members(Path("x")))


@pytest.mark.parametrize("name", UNSAFE)
def test_an_unsafe_release_file_is_refused_before_anything_is_written(update_kit, installed, tmp_path, capsys, name):
    bad = make_release(tmp_path / "bad.tar.gz", "v9", V2, links=LINKS, executable=EXECUTABLE, extra=unsafe_members(tmp_path)[name])
    before = snapshot(installed)

    code, out, err = apply(update_kit, installed, bad, capsys)

    assert code == 1
    assert out == ""
    assert len(err.strip().splitlines()) == 1
    assert "release" in err.lower() and "Nothing was changed" in err
    assert snapshot(installed) == before
    assert not (tmp_path / "escape.txt").exists() and not (tmp_path / "absolute-escape.txt").exists()


def test_a_file_that_is_not_a_release_is_refused(update_kit, installed, tmp_path, capsys):
    text = tmp_path / "notes.txt"
    text.write_text("this is not a tarball\n")
    code, out, err = apply(update_kit, installed, text, capsys)
    assert code == 1 and out == ""
    assert err.startswith(f"{text} is not a kit release file")
    assert len(err.strip().splitlines()) == 1


def test_a_cut_off_release_file_is_refused(update_kit, installed, releases, tmp_path, capsys):
    data = releases.v2.read_bytes()
    cut = tmp_path / "cut.tar.gz"
    cut.write_bytes(data[: len(data) // 2])
    before = snapshot(installed)
    code, out, err = apply(update_kit, installed, cut, capsys)
    assert code == 1 and len(err.strip().splitlines()) == 1
    assert snapshot(installed) == before


def test_a_missing_release_file_is_reported(update_kit, installed, tmp_path, capsys):
    code, out, err = apply(update_kit, installed, tmp_path / "nothing.tar.gz", capsys)
    assert code == 1 and out == ""
    assert "Could not find the release file" in err


def test_a_folder_where_the_release_has_a_file_is_reported_before_anything_changes(update_kit, installed, releases, capsys):
    (installed / "kit/core.py").unlink()
    (installed / "kit/core.py").mkdir()
    before = snapshot(installed)
    code, out, err = apply(update_kit, installed, releases.v2, capsys)
    assert code == 1
    assert "kit/core.py is a folder" in err and "Nothing was changed" in err
    assert snapshot(installed) == before


def test_a_file_where_the_release_needs_a_folder_is_reported(update_kit, installed, releases, tmp_path, capsys):
    (installed / "docs").write_text("a file, not a folder\n")
    v3 = make_release(tmp_path / "v3.tar.gz", "v3", {**V2, "docs/guide.md": "guide\n"}, links=LINKS, executable=EXECUTABLE)
    before = snapshot(installed)
    code, out, err = apply(update_kit, installed, v3, capsys)
    assert code == 1 and "docs" in err and "Nothing was changed" in err
    assert snapshot(installed) == before


def test_the_update_never_writes_through_a_link_that_leaves_the_kit_folder(update_kit, installed, releases, tmp_path, capsys):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (installed / "linked").symlink_to(outside)
    v3 = make_release(tmp_path / "v3.tar.gz", "v3", {**V2, "linked/file.txt": "should not land outside\n"}, links=LINKS, executable=EXECUTABLE)
    before = snapshot(installed)
    code, out, err = apply(update_kit, installed, v3, capsys)
    assert code == 1 and "leaves the kit folder" in err
    assert list(outside.iterdir()) == []
    assert snapshot(installed) == before


LINK_TARGETS = ["../me", "../work", "../research/runs", "../Me", "../.git", ".."]


@pytest.mark.parametrize("target", LINK_TARGETS)
def test_the_update_never_writes_through_a_link_into_the_creators_folders(update_kit, installed, releases, tmp_path, capsys, target):
    write(installed, CREATOR)
    (installed / "kit/x").symlink_to(target)
    through = {"..": "kit/x/.env"}.get(target, "kit/x/profile.json")
    v3 = make_release(tmp_path / "v3.tar.gz", "v3", {**V2, through: "overwritten\n"}, links=LINKS, executable=EXECUTABLE)
    before = snapshot(installed)

    code, out, err = apply(update_kit, installed, v3, capsys)

    assert code == 1 and out == ""
    assert len(err.strip().splitlines()) == 1
    assert "link into your own folders" in err and "Nothing was changed" in err
    for rel, body in CREATOR.items():
        assert (installed / rel).read_bytes() == body
    assert snapshot(installed) == before


def test_an_absolute_link_into_the_creators_folders_is_refused_too(update_kit, installed, releases, tmp_path, capsys):
    write(installed, CREATOR)
    (installed / "kit/x").symlink_to(installed / "me")
    v3 = make_release(tmp_path / "v3.tar.gz", "v3", {**V2, "kit/x/profile.json": "overwritten\n"}, links=LINKS, executable=EXECUTABLE)
    code, out, err = apply(update_kit, installed, v3, capsys)
    assert code == 1 and "link into your own folders" in err
    assert (installed / "me/profile.json").read_bytes() == CREATOR["me/profile.json"]


def test_a_manifest_entry_cannot_reach_a_creator_file_through_a_link(update_kit, installed, releases, capsys):
    write(installed, CREATOR)
    (installed / "kit/x").symlink_to("../me")
    forged = manifest_of(installed)
    forged["kit/x/profile.json"] = "0" * 64  # not in the release, so the update would remove it
    (installed / ".kit-manifest.json").write_text(json.dumps(forged))
    before = snapshot(installed)

    code, out, err = apply(update_kit, installed, releases.v2, capsys)

    assert code == 1 and "link into your own folders" in err
    assert (installed / "me/profile.json").read_bytes() == CREATOR["me/profile.json"]
    assert snapshot(installed) == before


def test_a_link_that_stays_in_the_kit_and_lands_on_a_kit_file_is_fine(update_kit, installed, releases, tmp_path, capsys):
    (installed / "kit/inner").mkdir()
    (installed / "kit/alias").symlink_to("inner")
    v3 = make_release(tmp_path / "v3.tar.gz", "v3", {**V2, "kit/alias/new.py": "NEW = 1\n"}, links=LINKS, executable=EXECUTABLE)
    code, out, err = apply(update_kit, installed, v3, capsys)
    assert (code, err) == (0, "")
    assert (installed / "kit/inner/new.py").read_text() == "NEW = 1\n"


# --- finding the release on GitHub ---------------------------------------------------------------------------


class Served:
    """A stand-in for the network: it answers by URL and remembers what was asked."""

    def __init__(self, routes):
        self.routes = routes
        self.asked = []

    def __call__(self, url):
        self.asked.append(url)
        answer = self.routes.get(url)
        if isinstance(answer, BaseException):
            raise answer
        if answer is None:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return io.BytesIO(answer)


def lookup(update_kit, tag):
    return json.dumps({"tag_name": tag, "name": "ignored"}).encode()


def test_the_latest_release_is_looked_up_and_applied(update_kit, installed, releases, capsys, monkeypatch):
    served = Served(
        {
            update_kit.API_URL: lookup(update_kit, "v2"),
            update_kit.TARBALL_URL.format(tag="v2"): releases.v2.read_bytes(),
        }
    )
    monkeypatch.setattr(update_kit, "_open", served)

    code = update_kit.main(["--apply"], root=installed)
    out = plain(capsys.readouterr().out)

    assert code == 0
    assert served.asked == [update_kit.API_URL, update_kit.TARBALL_URL.format(tag="v2")]
    assert (installed / "kit/core.py").read_text() == "VALUE = 2\n"
    assert "Updated to release v2" in out


def test_ref_names_the_release_and_skips_the_lookup(update_kit, installed, releases, capsys, monkeypatch):
    served = Served({update_kit.TARBALL_URL.format(tag="v2"): releases.v2.read_bytes()})
    monkeypatch.setattr(update_kit, "_open", served)

    code = update_kit.main(["--ref", "v2"], root=installed)

    assert code == 0
    assert served.asked == [update_kit.TARBALL_URL.format(tag="v2")]
    assert "Nothing was changed" in capsys.readouterr().out
    assert (installed / "kit/core.py").read_text() == "VALUE = 1\n"


def test_the_addresses_come_from_one_repository_name(update_kit):
    assert update_kit.REPO.endswith("/content-intelligence-kit") and update_kit.REPO.count("/") == 1
    assert update_kit.API_URL == f"https://api.github.com/repos/{update_kit.REPO}/releases/latest"
    assert update_kit.TARBALL_URL == f"https://github.com/{update_kit.REPO}/archive/refs/tags/{{tag}}.tar.gz"
    source = SCRIPT.read_text(encoding="utf-8")
    assert source.count(update_kit.REPO) == 1, "the repository name is written once"
    assert f"github.com/{update_kit.REPO}" in source, "and only as part of its GitHub address"


NOT_FOUND = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
FAILURES = {
    "no internet": (urllib.error.URLError(socket.gaierror(8, "nodename nor servname provided")), "Could not reach GitHub"),
    "timeout": (TimeoutError("timed out"), "took too long"),
    "rate limit": (urllib.error.HTTPError("u", 403, "rate limit exceeded", {}, None), "limiting requests"),
    "too many requests": (urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None), "limiting requests"),
    "no release yet": (NOT_FOUND, "No release has been published yet"),
    "server error": (urllib.error.HTTPError("u", 502, "Bad Gateway", {}, None), "error (502)"),
    "certificate": (urllib.error.URLError(ssl.SSLCertVerificationError(1, "certificate verify failed")), "certificate"),
    "connection reset": (ConnectionResetError(54, "Connection reset by peer"), "Could not download"),
}


@pytest.mark.parametrize("name", list(FAILURES))
def test_a_network_failure_is_one_plain_sentence(update_kit, installed, capsys, monkeypatch, name):
    error, words = FAILURES[name]
    monkeypatch.setattr(update_kit, "_open", Served({update_kit.API_URL: error}))
    before = snapshot(installed)

    code = update_kit.main(["--apply"], root=installed)
    out = capsys.readouterr()

    assert code == 1
    assert out.out == ""
    assert len(plain(out.err).strip().splitlines()) == 1
    assert words in out.err
    assert snapshot(installed) == before


@pytest.mark.parametrize(
    "answer, words",
    [
        (b"<html>gateway error</html>", "not what the update expected"),
        (b"[]", "not what the update expected"),
        (b'{"name": "no tag here"}', "not what the update expected"),
        (b'{"tag_name": "../../etc"}', "not a release name"),
        (b'{"tag_name": ""}', "not a release name"),
    ],
)
def test_an_answer_the_update_cannot_use_is_one_plain_sentence(update_kit, installed, capsys, monkeypatch, answer, words):
    monkeypatch.setattr(update_kit, "_open", Served({update_kit.API_URL: answer}))
    code = update_kit.main(["--apply"], root=installed)
    out = capsys.readouterr()
    assert code == 1 and out.out == ""
    assert len(plain(out.err).strip().splitlines()) == 1 and words in out.err


def test_a_failed_download_is_one_plain_sentence(update_kit, installed, capsys, monkeypatch):
    served = Served({update_kit.API_URL: lookup(update_kit, "v2"), update_kit.TARBALL_URL.format(tag="v2"): ConnectionResetError(54, "reset")})
    monkeypatch.setattr(update_kit, "_open", served)
    code = update_kit.main(["--apply"], root=installed)
    out = capsys.readouterr()
    assert code == 1 and len(plain(out.err).strip().splitlines()) == 1
    assert "Could not download" in out.err


@pytest.mark.parametrize("junk", [b"<html>not a tarball</html>", b""])
def test_a_download_that_is_not_a_release_does_not_name_a_temporary_file(update_kit, installed, capsys, monkeypatch, junk):
    monkeypatch.setattr(update_kit, "_open", Served({update_kit.TARBALL_URL.format(tag="v2"): junk}))
    before = snapshot(installed)
    code = update_kit.main(["--ref", "v2", "--apply"], root=installed)
    out = capsys.readouterr()
    assert code == 1 and out.out == ""
    assert out.err.strip() == "The file GitHub sent is not a kit release file, or it was cut off. Try again in a minute."
    assert snapshot(installed) == before


def test_a_release_that_does_not_exist_is_named(update_kit, installed, capsys, monkeypatch):
    monkeypatch.setattr(update_kit, "_open", Served({}))
    code = update_kit.main(["--ref", "v404"], root=installed)
    out = capsys.readouterr()
    assert code == 1
    assert out.err.strip() == "Release v404 was not found. Check the name of the release and try again."


@pytest.mark.parametrize("ref", ["../x", "a/b", "v1 2", "", "-v1", "x" * 80, "v1;rm"])
def test_a_release_name_that_is_not_a_plain_tag_is_refused_without_a_request(update_kit, installed, capsys, monkeypatch, ref):
    served = Served({})
    monkeypatch.setattr(update_kit, "_open", served)
    code = update_kit.main([f"--ref={ref}"], root=installed)
    out = capsys.readouterr()
    assert code == 1 and served.asked == []
    assert "not a release name" in out.err


def test_the_options_that_cannot_go_together_are_refused(update_kit, installed, releases, capsys):
    for flags in (
        ["--check", "--apply"],
        ["--apply", "--write-manifest"],
        ["--ref", "v1", "--from", str(releases.v1)],
    ):
        with pytest.raises(SystemExit) as caught:
            update_kit.main(flags, root=installed)
        assert caught.value.code == 2
        assert "not allowed with" in capsys.readouterr().err


# --- the manifest of a release ---------------------------------------------------------------------------------


def git(path: Path, *args: str) -> None:
    """Run git in a throwaway repo without anyone's hooks or signing settings."""
    settings = ["-c", "user.name=Throwaway", "-c", "user.email=throwaway", "-c", "core.hooksPath=/dev/null"]
    subprocess.run(["git", *settings, "-c", "commit.gpgsign=false", *args], cwd=path, check=True)


def git_repo(path: Path, files: dict, links: dict | None = None) -> Path:
    """A committed repo holding `files` and `links`."""
    path.mkdir()
    git(path, "init", "-q")
    write(path, files)
    for name, target in (links or {}).items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).symlink_to(target)
    git(path, "add", "-A")
    git(path, "commit", "-qm", "files")
    return path


needs_git = pytest.mark.skipif(subprocess.run(["which", "git"], capture_output=True).returncode != 0, reason="git is not installed")


@needs_git
def test_write_manifest_records_every_kit_file_sorted(update_kit, tmp_path, capsys):
    files = {**V1, **{k: v for k, v in CREATOR.items()}}
    repo = git_repo(tmp_path / "repo", files, LINKS)

    code = update_kit.main(["--write-manifest"], root=repo)
    out = capsys.readouterr()

    assert (code, plain(out.err)) == (0, "")
    assert out.out.strip() == f"Wrote .kit-manifest.json with {len(V1) + len(LINKS)} files."
    raw = (repo / ".kit-manifest.json").read_text(encoding="utf-8")
    recorded = json.loads(raw)
    assert recorded == expected_manifest(V1, LINKS)
    assert list(recorded) == sorted(recorded), "paths are sorted"
    assert raw.endswith("}\n") and raw == json.dumps(recorded, indent=2) + "\n"
    assert all(len(digest) == 64 for digest in recorded.values())


@needs_git
def test_regenerating_the_manifest_gives_the_same_file_when_the_tree_is_unchanged(update_kit, tmp_path, capsys):
    repo = git_repo(tmp_path / "repo", V1, LINKS)
    update_kit.main(["--write-manifest"], root=repo)
    first = (repo / ".kit-manifest.json").read_bytes()

    update_kit.main(["--write-manifest"], root=repo)
    assert (repo / ".kit-manifest.json").read_bytes() == first

    # also once the manifest itself is tracked: it never lists itself
    git(repo, "add", "-A")
    update_kit.main(["--write-manifest"], root=repo)
    assert (repo / ".kit-manifest.json").read_bytes() == first
    assert ".kit-manifest.json" not in json.loads(first)
    capsys.readouterr()


@needs_git
def test_changing_one_file_changes_only_its_entry(update_kit, tmp_path, capsys):
    repo = git_repo(tmp_path / "repo", V1, LINKS)
    update_kit.main(["--write-manifest"], root=repo)
    before = manifest_of(repo)

    (repo / "kit/core.py").write_text("VALUE = 99\n")
    update_kit.main(["--write-manifest"], root=repo)
    after = manifest_of(repo)

    assert [path for path in after if after[path] != before[path]] == ["kit/core.py"]
    assert after["kit/core.py"] == sha(b"VALUE = 99\n")
    capsys.readouterr()


@needs_git
def test_write_manifest_skips_untracked_and_deleted_files(update_kit, tmp_path, capsys):
    repo = git_repo(tmp_path / "repo", V1, LINKS)
    (repo / "untracked.txt").write_text("not in git\n")
    (repo / "kit/core.py").unlink()
    update_kit.main(["--write-manifest"], root=repo)
    recorded = manifest_of(repo)
    assert "untracked.txt" not in recorded and "kit/core.py" not in recorded
    assert "kit/research/rank.py" in recorded
    capsys.readouterr()


@needs_git
def test_write_manifest_warns_about_uncommitted_changes(update_kit, tmp_path, capsys):
    repo = git_repo(tmp_path / "repo", V1, LINKS)
    code = update_kit.main(["--write-manifest"], root=repo)
    assert "uncommitted" not in capsys.readouterr().out

    (repo / "kit/core.py").write_text("VALUE = 5\n")
    update_kit.main(["--write-manifest"], root=repo)
    assert "Some tracked files have uncommitted changes" in capsys.readouterr().out
    assert code == 0


def test_write_manifest_needs_a_git_checkout(update_kit, tmp_path, capsys):
    folder = tmp_path / "not-a-checkout"
    folder.mkdir()
    code = update_kit.main(["--write-manifest"], root=folder)
    out = capsys.readouterr()
    assert code == 1 and len(plain(out.err).strip().splitlines()) == 1
    assert "git checkout" in out.err
    assert not (folder / ".kit-manifest.json").exists()


@needs_git
def test_an_update_writes_the_same_manifest_that_write_manifest_would(update_kit, installed, releases, capsys):
    apply(update_kit, installed, releases.v2, capsys)
    written_by_the_update = (installed / ".kit-manifest.json").read_bytes()

    # Treat the updated folder as a checkout of release v2 and regenerate the manifest from it.
    git(installed, "init", "-q")
    git(installed, "add", "-A")
    update_kit.main(["--write-manifest"], root=installed)

    assert (installed / ".kit-manifest.json").read_bytes() == written_by_the_update
    capsys.readouterr()


# --- running the installed copy as a program ----------------------------------------------------------------------


def test_the_installed_script_works_on_the_folder_it_sits_in(tmp_path):
    script = SCRIPT.read_text(encoding="utf-8")
    first = make_release(tmp_path / "first.tar.gz", "v1", {**V1, "bin/update_kit.py": script}, links=LINKS, executable=EXECUTABLE)
    second = make_release(tmp_path / "second.tar.gz", "v2", {**V2, "bin/update_kit.py": script}, links=LINKS, executable=EXECUTABLE)
    root = tmp_path / "kit"
    (root / "bin").mkdir(parents=True)
    (root / "bin/update_kit.py").write_text(script)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}

    def run(*args):
        return subprocess.run(
            [sys.executable, "-W", "error", str(root / "bin/update_kit.py"), *args],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
        )

    installed_first = run("--apply", "--from", str(first))
    assert installed_first.returncode == 0, installed_first.stderr
    looked = run("--from", str(second))
    assert looked.returncode == 0, looked.stderr
    assert "Release v2 against your kit" in looked.stdout
    assert (root / "kit/core.py").read_text() == "VALUE = 1\n"

    applied = run("--apply", "--from", str(second))
    assert applied.returncode == 0, applied.stderr
    assert (root / "kit/core.py").read_text() == "VALUE = 2\n"
    assert (root / ".kit-manifest.json").is_file()
    failed = run("--from", str(tmp_path / "missing.tar.gz"))
    assert failed.returncode == 1 and "Traceback" not in failed.stderr
