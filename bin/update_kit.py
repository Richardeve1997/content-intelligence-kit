#!/usr/bin/env python3
"""Update the kit's own files from a published release and leave the creator's files alone.

    python3 bin/update_kit.py                          look at the latest release and list what would change
    python3 bin/update_kit.py --apply                  replace the kit's files with that release
    python3 bin/update_kit.py --ref v1.2.0             use one particular release instead of the latest
    python3 bin/update_kit.py --from release.tar.gz    use a release file that is already on this computer
    python3 bin/update_kit.py --write-manifest         for whoever publishes a release: record every kit file's hash

The kit's files are every path in the kit folder except me/, work/, research/, .env and .git/ at its top.
Those five belong to the creator. Nothing here reads or writes them, except that me/kit-backups/ receives
copies of kit files the creator had edited. Everything else in the folder that no release names
(.venv, node_modules, downloaded tools, the creator's own additions) is left alone, because the update
only looks at paths that the release or the manifest lists.

.kit-manifest.json records the SHA-256 of every kit file as it was last installed. A kit file whose
current hash differs from the manifest has been edited. Before the update replaces or removes an edited
file, it copies the file to me/kit-backups/<date>/<path> and says so. The manifest hashes a link by its
target. After an update, the manifest is rewritten from the files as installed.

A release file is what GitHub serves for a tag: a .tar.gz holding one top-level folder. The whole file is
checked before the first change, so a bad release leaves the kit as it was.

This script uses only the Python standard library, so it runs before the kit's packages are installed.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import http.client
import json
import os
import posixpath
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path, PurePosixPath

# One address, written once. Every other address below is built from it.
RELEASES_URL = "https://github.com/Richardeve1997/content-intelligence-kit"
REPO = RELEASES_URL.split("github.com/", 1)[1]
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
TARBALL_URL = RELEASES_URL + "/archive/refs/tags/{tag}.tar.gz"

MANIFEST = ".kit-manifest.json"
CREATOR_PATHS = ("me", "work", "research", ".env", ".git")
BACKUP_FOLDER = "kit-backups"
DEPENDENCY_FILES = ("pyproject.toml", "uv.lock", "package.json", "package-lock.json")
FOLDER_PREFIX = "content-intelligence-kit-"  # GitHub names the top folder <repository>-<tag>

TIMEOUT = 30  # seconds to wait on GitHub
MAX_DOWNLOAD = 500 * 1024 * 1024
MAX_UNPACKED = 2 * 1024 * 1024 * 1024
LISTED = 50  # files named per section of the report

_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_JUNK = (".DS_Store", "__pycache__")  # left behind in a folder the release emptied; safe to sweep


class UpdateError(Exception):
    """Something the creator can act on. The message is one plain sentence."""


# --- small seams: the network and the date ---------------------------------------------------------------


def _today() -> date:
    return date.today()


def _open(url: str):
    """Open an address and return the response. Tests replace this so that nothing touches the network."""
    accept = "application/vnd.github+json" if url.startswith("https://api.") else "*/*"
    request = urllib.request.Request(url, headers={"User-Agent": "content-intelligence-kit-update", "Accept": accept})
    return urllib.request.urlopen(request, timeout=TIMEOUT)


# --- paths and hashes ---------------------------------------------------------------------------------------


def clean_relative(name: str) -> str | None:
    """`name` as a plain relative path with forward slashes, or None if it could point outside the folder."""
    if not isinstance(name, str) or not name or "\x00" in name or "\\" in name:
        return None
    if name.startswith("/") or re.match(r"[A-Za-z]:", name):
        return None
    parts = PurePosixPath(name).parts
    if not parts or ".." in parts:
        return None
    return "/".join(parts)


def is_creator_path(rel: str) -> bool:
    """True for me/, work/, research/, .env and .git at the top. Case is ignored, because macOS folds it."""
    top = PurePosixPath(rel).parts[0].casefold()
    return top in CREATOR_PATHS


def link_digest(target: str) -> str:
    return hashlib.sha256(b"symlink:" + os.fsencode(target)).hexdigest()


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def local_digest(path: Path) -> str | None:
    """The digest of a file or link in the kit folder. None when nothing is there, or when it is a folder."""
    if path.is_symlink():
        return link_digest(os.readlink(path))
    if path.is_file():
        return file_digest(path)
    return None


def me_dir(root: Path) -> Path:
    override = os.environ.get("KIT_ME", "").strip()
    return Path(override).expanduser().absolute() if override else root / "me"


# --- the manifest -------------------------------------------------------------------------------------------


def format_manifest(entries: dict[str, str]) -> str:
    return json.dumps(dict(sorted(entries.items())), indent=2, ensure_ascii=False) + "\n"


def load_manifest(root: Path) -> tuple[dict[str, str] | None, str | None]:
    """(entries, problem). entries is None when there is no usable manifest, and problem says why."""
    try:
        raw = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, f"There is no {MANIFEST} here"
    except (OSError, ValueError):
        return None, f"{MANIFEST} could not be read"
    if not isinstance(raw, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in raw.items()):
        return None, f"{MANIFEST} could not be read"
    entries = {}
    for name, digest in raw.items():
        rel = clean_relative(name)
        # An entry that names a creator path or leaves the folder is never acted on.
        if rel and rel != MANIFEST and not is_creator_path(rel):
            entries[rel] = digest
    return entries, None


def write_manifest_file(root: Path, entries: dict[str, str]) -> bool:
    """Write the manifest, in one step. False when the file already holds exactly this."""
    text = format_manifest(entries)
    path = root / MANIFEST
    with contextlib.suppress(OSError, ValueError):
        if path.read_text(encoding="utf-8") == text:
            return False
    temporary = path.with_name(MANIFEST + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.replace(temporary, path)
    return True


def tracked_files(root: Path) -> list[str]:
    if not (root / ".git").exists():
        raise UpdateError("Writing the manifest needs the git checkout of the kit, and this folder is not one.")
    try:
        listing = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True)
    except FileNotFoundError:
        raise UpdateError("Writing the manifest needs git, and git is not installed.") from None
    if listing.returncode != 0:
        raise UpdateError("Writing the manifest needs the git checkout of the kit, and git could not read this folder.")
    return [name for name in listing.stdout.decode("utf-8", "surrogateescape").split("\0") if name]


def uncommitted_changes(root: Path) -> bool:
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"], capture_output=True, text=True
    )
    changed = [line[3:] for line in status.stdout.splitlines() if line[3:] != MANIFEST]
    return status.returncode == 0 and bool(changed)


def write_manifest(root: Path) -> int:
    entries = {}
    for name in tracked_files(root):
        rel = clean_relative(name)
        if not rel or rel == MANIFEST or is_creator_path(rel):
            continue
        digest = local_digest(root / rel)
        if digest is not None:  # a file deleted but not yet staged, or a submodule, has no digest
            entries[rel] = digest
    write_manifest_file(root, entries)
    print(f"Wrote {MANIFEST} with {len(entries)} files.")
    if uncommitted_changes(root):
        print(
            "Some tracked files have uncommitted changes, so the manifest may not match the release. "
            "Commit them, then write the manifest again."
        )
    return 0


# --- finding and downloading a release -----------------------------------------------------------------------


def check_tag(tag: str, *, from_github: bool = False) -> str:
    if not isinstance(tag, str) or not _TAG.fullmatch(tag):
        if from_github:
            raise UpdateError(f"GitHub's latest release is called {tag!r}, which is not a release name the update can use.")
        raise UpdateError(f"{tag!r} is not a release name. A release name looks like v1.2.0.")
    return tag


def _network_problem(error: BaseException, what: str, missing: str) -> UpdateError:
    """One plain sentence for a failure while talking to GitHub."""
    if isinstance(error, urllib.error.HTTPError):
        if error.code == 404:
            return UpdateError(missing)
        if error.code in (403, 429):
            return UpdateError(
                "GitHub refused the request, which usually means it is limiting requests from this computer. "
                "Wait a few minutes and try again."
            )
        return UpdateError(f"GitHub answered with an error ({error.code}). Try again later.")
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(reason, ssl.SSLCertVerificationError):
        return UpdateError(
            "Your Python could not verify GitHub's certificate. Run the update through uv instead, "
            "with: uv run python bin/update_kit.py"
        )
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return UpdateError("GitHub took too long to answer. Try again in a minute.")
    if isinstance(error, urllib.error.URLError):
        return UpdateError("Could not reach GitHub. Check your internet connection and try again.")
    detail = getattr(error, "strerror", None) or type(error).__name__
    return UpdateError(f"Could not download {what} ({detail}). Try again.")


def _fetch(url: str, what: str, missing: str, destination: Path | None = None) -> bytes:
    """Read a small answer into memory, or stream a large one to `destination`."""
    try:
        with _open(url) as response:
            if destination is None:
                return response.read(1024 * 1024)
            size = 0
            with destination.open("wb") as out:
                for chunk in iter(lambda: response.read(1024 * 1024), b""):
                    size += len(chunk)
                    if size > MAX_DOWNLOAD:
                        raise UpdateError("The release download is far larger than a kit release should be, so it was stopped.")
                    out.write(chunk)
            return b""
    except UpdateError:
        raise
    except (OSError, http.client.HTTPException) as error:
        raise _network_problem(error, what, missing) from None


def latest_tag() -> str:
    answer = _fetch(
        API_URL,
        "the name of the latest release",
        "No release has been published yet, so there is nothing to update to.",
    )
    try:
        tag = json.loads(answer)["tag_name"]
    except (ValueError, KeyError, TypeError):
        tag = None
    if tag is None:
        raise UpdateError(
            "GitHub's answer was not what the update expected. Try again later, or pass a release file with --from."
        )
    return check_tag(tag, from_github=True)


def download(tag: str, scratch: Path) -> Path:
    destination = scratch / "release.tar.gz"
    _fetch(
        TARBALL_URL.format(tag=tag),
        "the release",
        f"Release {tag} was not found. Check the name of the release and try again.",
        destination,
    )
    return destination


# --- reading a release ------------------------------------------------------------------------------------------


@dataclass
class Item:
    kind: str  # "file" or "link"
    digest: str
    executable: bool = False
    source: Path | None = None  # a file's content, unpacked
    target: str = ""  # where a link points


@dataclass
class Release:
    label: str
    items: dict[str, Item]
    ignored: int = 0  # entries under the creator's own folders, which are never used


def _refuse(what: str) -> UpdateError:
    return UpdateError(f"The release file {what}. Nothing was changed.")


def _link_stays_inside(rel: str, target: str) -> bool:
    if not target or target.startswith("/") or "\\" in target or "\x00" in target:
        return False
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(rel), target))
    return resolved != ".." and not resolved.startswith("../")


def _unpack(tar: tarfile.TarFile, scratch: Path) -> Release:
    scratch.mkdir(parents=True, exist_ok=True)
    top = None
    items: dict[str, Item] = {}
    ignored = 0
    total = 0
    serial = 0
    for member in tar:
        if member.type in (tarfile.XGLTYPE, tarfile.XHDTYPE):
            continue
        name = member.name
        parts = PurePosixPath(name).parts
        if name.startswith("/") or "\\" in name or "\x00" in name or ".." in parts:
            raise _refuse(f"has a path that leaves the kit folder ({name})")
        if not parts or (len(parts) == 1 and not member.isdir()):
            raise _refuse("does not have the layout the update expects, which is one folder that holds the kit")
        if top is None:
            top = parts[0]
        elif parts[0] != top:
            raise _refuse("does not have the layout the update expects, which is one folder that holds the kit")
        if len(parts) == 1 or member.isdir():
            continue
        rel = "/".join(parts[1:])
        if not (member.isfile() or member.issym()):
            raise _refuse(f"contains something other than files and links ({name})")
        if rel == MANIFEST:
            continue
        if is_creator_path(rel):
            ignored += 1
            continue
        if member.issym():
            if not _link_stays_inside(rel, member.linkname):
                raise _refuse(f"has a link that points outside the kit folder ({name})")
            items[rel] = Item("link", link_digest(member.linkname), target=member.linkname)
            continue
        serial += 1
        source = scratch / str(serial)
        digest = hashlib.sha256()
        with tar.extractfile(member) as incoming, source.open("wb") as out:
            for chunk in iter(lambda: incoming.read(1024 * 1024), b""):
                total += len(chunk)
                if total > MAX_UNPACKED:
                    raise _refuse("is far larger than a kit release should be")
                digest.update(chunk)
                out.write(chunk)
        items[rel] = Item("file", digest.hexdigest(), bool(member.mode & 0o111), source=source)

    if top is None or not items:
        raise _refuse("is empty")
    for rel in items:
        for parent in PurePosixPath(rel).parents:
            above = parent.as_posix()
            if above in items:
                raise _refuse(f"lists {rel} below {above}, which is a {items[above].kind}")
    label = top[len(FOLDER_PREFIX) :] if top.startswith(FOLDER_PREFIX) and len(top) > len(FOLDER_PREFIX) else top
    return Release(label, items, ignored)


def read_release(path: Path, scratch: Path, label: str | None = None, *, downloaded: bool = False) -> Release:
    try:
        with tarfile.open(path, "r:*") as tar:
            release = _unpack(tar, scratch)
    except UpdateError:
        raise
    except (tarfile.TarError, EOFError, zlib.error):
        if downloaded:
            raise UpdateError(
                "The file GitHub sent is not a kit release file, or it was cut off. Try again in a minute."
            ) from None
        raise UpdateError(
            f"{path} is not a kit release file, or it was cut off while downloading. Download it again."
        ) from None
    except OSError as error:
        raise UpdateError(
            f"The release file could not be unpacked ({error.strerror or type(error).__name__}). Nothing was changed."
        ) from None
    if label:
        release.label = label
    return release


# --- working out what would change -------------------------------------------------------------------------------


@dataclass
class Plan:
    add: list[str] = field(default_factory=list)
    replace: list[str] = field(default_factory=list)
    remove: list[str] = field(default_factory=list)
    same: list[str] = field(default_factory=list)
    edited: list[str] = field(default_factory=list)  # replaced or removed files the creator had changed
    permissions: list[str] = field(default_factory=list)  # same content, but the executable bit differs

    @property
    def empty(self) -> bool:
        return not (self.add or self.replace or self.remove)

    def changes_dependencies(self) -> bool:
        return any(rel in DEPENDENCY_FILES for rel in self.add + self.replace)


def _check_place(root: Path, rel: str) -> None:
    """Refuse a path whose folders are in the way: a file where a folder is needed, or a link that leaves the kit."""
    real_root = os.path.realpath(root)
    folder = root
    for part in PurePosixPath(rel).parts[:-1]:
        folder = folder / part
        if not (folder.is_symlink() or folder.exists()):
            return  # nothing from here down exists yet
        resolved = os.path.realpath(folder)
        if resolved != real_root and not resolved.startswith(real_root + os.sep):
            raise UpdateError(
                f"{rel} would be written through a link that leaves the kit folder, so the update stopped. "
                "Nothing was changed."
            )
        if not folder.is_dir():
            shown = folder.relative_to(root).as_posix()
            raise UpdateError(
                f"{shown} is a file in your kit, but the release needs a folder there. "
                "Move it away, then run the update again. Nothing was changed."
            )


def _is_executable(path: Path) -> bool:
    return bool(path.stat().st_mode & 0o111)


def make_plan(root: Path, release: Release, installed: dict[str, str] | None) -> Plan:
    plan = Plan()
    for rel, item in sorted(release.items.items()):
        _check_place(root, rel)
        path = root / rel
        here = local_digest(path)
        if here is None and path.exists():
            raise UpdateError(
                f"{rel} is a folder in your kit, but the release has a file with that name. "
                "Move the folder away, then run the update again. Nothing was changed."
            )
        if here is None:
            plan.add.append(rel)
        elif here == item.digest:
            plan.same.append(rel)
            if item.kind == "file" and _is_executable(path) != item.executable:
                plan.permissions.append(rel)
        else:
            plan.replace.append(rel)
            if installed is None or installed.get(rel) != here:
                plan.edited.append(rel)
    if installed is not None:
        for rel in sorted(installed):
            if rel in release.items:
                continue
            _check_place(root, rel)
            here = local_digest(root / rel)
            if here is None:
                continue  # already gone, or a folder, which is left alone
            plan.remove.append(rel)
            if here != installed[rel]:
                plan.edited.append(rel)
    plan.edited.sort()
    return plan


# --- making the changes -------------------------------------------------------------------------------------------


def backup_location(root: Path) -> tuple[Path, str]:
    base = me_dir(root) / BACKUP_FOLDER / _today().isoformat()
    try:
        shown = base.relative_to(root).as_posix()
    except ValueError:
        shown = str(base)
    return base, shown


def _backup_name(wanted: Path, here: str | None) -> Path | None:
    """Where to put a copy: the wanted name, or a numbered one if an earlier, different copy is there.
    None when the very same content is already saved there."""
    if not os.path.lexists(wanted):
        return wanted
    if local_digest(wanted) == here:
        return None
    number = 1
    while True:
        candidate = wanted.with_name(f"{wanted.stem}.{number}{wanted.suffix}")
        if not os.path.lexists(candidate):
            return candidate
        if local_digest(candidate) == here:
            return None
        number += 1


def back_up_edits(root: Path, plan: Plan) -> None:
    base, _ = backup_location(root)
    for rel in plan.edited:
        source = root / rel
        destination = _backup_name(base / rel, local_digest(source))
        if destination is None:
            continue
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.is_symlink():
                os.symlink(os.readlink(source), destination)
            else:
                shutil.copy2(source, destination)
        except OSError as error:
            raise UpdateError(
                f"Could not copy your edit of {rel} to the backup folder ({error.strerror or type(error).__name__}), "
                "so nothing was replaced."
            ) from None


def install(root: Path, rel: str, item: Item) -> None:
    destination = root / rel
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix=destination.name + ".", suffix=".kit-update", dir=destination.parent)
    os.close(handle)
    try:
        if item.kind == "link":
            os.unlink(name)
            os.symlink(item.target, name)
        else:
            shutil.copyfile(item.source, name)
            os.chmod(name, 0o755 if item.executable else 0o644)
        os.replace(name, destination)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(name)
        raise


def sweep_empty_folders(root: Path, folder: Path) -> None:
    """Remove a folder the release emptied, and its parents in turn. Stray .DS_Store and __pycache__ do not count."""
    while folder != root and folder.is_dir() and not folder.is_symlink():
        entries = list(folder.iterdir())
        if any(entry.name not in _JUNK for entry in entries):
            return
        for entry in entries:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        folder.rmdir()
        folder = folder.parent


def remove(root: Path, rel: str) -> None:
    with contextlib.suppress(FileNotFoundError):
        os.unlink(root / rel)
    sweep_empty_folders(root, (root / rel).parent)


# --- the report ----------------------------------------------------------------------------------------------------


def _lists(plan: Plan, done: bool) -> list[str]:
    headings = (
        ("Added:" if done else "To add:", plan.add),
        ("Replaced:" if done else "To replace:", plan.replace),
        ("Removed:" if done else "To remove:", plan.remove),
    )
    lines: list[str] = []
    for heading, names in headings:
        if names:
            lines += ["", heading, *[f"  {name}" for name in names[:LISTED]]]
            if len(names) > LISTED:
                lines.append(f"  ...and {len(names) - LISTED} more")
    return lines


def _counts(plan: Plan, verbs: tuple[str, str, str]) -> str:
    return (
        f"{len(plan.add)} {verbs[0]}, {len(plan.replace)} {verbs[1]}, {len(plan.remove)} {verbs[2]}, "
        f"{len(plan.same)} already the same"
    )


def _notes(release: Release, problem: str | None) -> list[str]:
    notes = []
    if problem:
        notes.append(f"{problem}, so every file that differs from the release counts as an edit and is backed up first.")
    if release.ignored:
        many = "files" if release.ignored != 1 else "file"
        notes.append(
            f"The release also held {release.ignored} {many} under your own folders (me, work, research, .env, .git). "
            "They were ignored."
        )
    return notes


def report_check(plan: Plan, release: Release, shown: str, notes: list[str]) -> None:
    if notes:
        print("\n".join(notes))
    if plan.empty:
        print(f"Your kit already matches release {release.label}. Nothing to do.")
        return
    lines = [f"Release {release.label} against your kit: {_counts(plan, ('to add', 'to replace', 'to remove'))}."]
    lines += _lists(plan, done=False)
    if plan.edited:
        lines += [
            "",
            f"You edited {len(plan.edited)} of these files. Each edit would be copied to {shown}/ "
            "before the file is replaced:",
            *[f"  {name}" for name in plan.edited],
        ]
    lines += ["", "Your me, work and research folders and your .env are never touched."]
    if plan.changes_dependencies():
        lines.append(
            "This release changes the kit's dependencies, so after you apply it, run: uv sync, then npm install."
        )
    lines.append("Nothing was changed. Run this again with --apply to update.")
    print("\n".join(lines))


def apply_update(root: Path, release: Release, plan: Plan, shown: str, notes: list[str]) -> None:
    if notes:
        print("\n".join(notes))
    if plan.edited:
        back_up_edits(root, plan)
        print(f"Copied your edits to {shown}/ before replacing the files:")
        print("\n".join(f"  {name}" for name in plan.edited))
    for rel in plan.remove:
        remove(root, rel)
    for rel in plan.add + plan.replace:
        install(root, rel, release.items[rel])
    for rel in plan.permissions:
        os.chmod(root / rel, 0o755 if release.items[rel].executable else 0o644)
    write_manifest_file(root, {rel: item.digest for rel, item in release.items.items()})
    if plan.empty:
        print(f"Your kit already matches release {release.label}. Nothing to do.")
        return
    lines = [f"Updated to release {release.label}: {_counts(plan, ('added', 'replaced', 'removed'))}."]
    lines += _lists(plan, done=True)
    lines += ["", "Your me, work and research folders and your .env were not touched."]
    if plan.changes_dependencies():
        lines.append("The kit's dependencies changed. Run: uv sync, then npm install.")
    print("\n".join(lines))


# --- the command line -----------------------------------------------------------------------------------------------


def update(root: Path, apply_it: bool, ref: str | None, source: str | None) -> int:
    with tempfile.TemporaryDirectory(prefix="kit-update-") as folder:
        scratch = Path(folder)
        label = None
        if source:
            tarball = Path(source).expanduser()
            if not tarball.is_file():
                raise UpdateError(f"Could not find the release file {tarball}.")
        else:
            label = check_tag(ref) if ref is not None else latest_tag()
            tarball = download(label, scratch)
        release = read_release(tarball, scratch / "unpacked", label, downloaded=not source)
        installed, problem = load_manifest(root)
        plan = make_plan(root, release, installed)
        notes = _notes(release, problem if plan.replace else None)  # only worth saying when a file differs
        _, shown = backup_location(root)
        if apply_it:
            apply_update(root, release, plan, shown, notes)
        else:
            report_check(plan, release, shown, notes)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="update_kit.py",
        description="Update the kit's own files from a release. The creator's files are never changed.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="list what would change and change nothing (the default)")
    mode.add_argument("--apply", action="store_true", help="replace the kit's files with the release")
    mode.add_argument(
        "--write-manifest",
        action="store_true",
        help="for publishing a release: record the hash of every kit file in .kit-manifest.json",
    )
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--ref", metavar="TAG", help="use this release instead of the latest one")
    where.add_argument("--from", dest="source", metavar="FILE", help="use a release file (.tar.gz) on this computer")
    return parser


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    args = build_parser().parse_args(argv)
    folder = Path(root) if root is not None else Path(__file__).resolve().parent.parent
    try:
        if args.write_manifest:
            return write_manifest(folder)
        return update(folder, args.apply, args.ref, args.source)
    except UpdateError as error:
        print(error, file=sys.stderr)
    except OSError as error:
        where = f" ({error.filename})" if error.filename else ""
        print(
            f"The update stopped because of a file problem: {error.strerror or type(error).__name__}{where}. "
            "Run it again, and it picks up where it stopped.",
            file=sys.stderr,
        )
    except KeyboardInterrupt:
        print("Stopped. Run it again, and it picks up where it stopped.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
