"""kit.media: the video encoder, the free-disk check and the heavy-job slots.

Renders, encodes, transcription and matting are heavy. At most HEAVY_SLOTS of them run at once across
every process on the machine, and none starts with less than 5 GB of disk free.
"""

import multiprocessing
import os
import subprocess
import sys
import time
from collections import namedtuple
from pathlib import Path

import pytest

from kit import media
from kit.config import KitError

ROOT = Path(__file__).resolve().parent.parent
Usage = namedtuple("Usage", "total used free")
GIB = 1024**3


@pytest.fixture
def slots(tmp_path, monkeypatch):
    """A private lock folder, so tests never touch work/.slots or wait on a real render."""
    folder = tmp_path / "slots"
    monkeypatch.setattr(media, "_slots_dir", lambda: folder)
    monkeypatch.delenv(media.SLOT_ENV, raising=False)
    monkeypatch.delenv("HEAVY_SLOTS", raising=False)
    monkeypatch.setattr(media, "MIN_FREE_GB", 0.0)
    return folder


# --- the encoder ------------------------------------------------------------------------------------


def _as(monkeypatch, system, machine, has_videotoolbox=True):
    monkeypatch.setattr(media.platform, "system", lambda: system)
    monkeypatch.setattr(media.platform, "machine", lambda: machine)
    monkeypatch.setattr(media, "_has_encoder", lambda name: has_videotoolbox)


def test_encoder_args(monkeypatch):
    monkeypatch.delenv("KIT_FORCE_X264", raising=False)
    _as(monkeypatch, "Darwin", "arm64")
    args = media.encoder_args()
    assert args[args.index("-c:v") + 1] == "h264_videotoolbox"
    assert "-pix_fmt" in args and args[args.index("-pix_fmt") + 1] == "yuv420p"

    monkeypatch.setenv("KIT_FORCE_X264", "1")
    args = media.encoder_args()
    assert args[args.index("-c:v") + 1] == "libx264"
    assert "-crf" in args


@pytest.mark.parametrize(
    "system,machine,has_vt",
    [("Linux", "x86_64", False), ("Darwin", "x86_64", True), ("Darwin", "arm64", False), ("Windows", "AMD64", False)],
)
def test_libx264_everywhere_else(monkeypatch, system, machine, has_vt):
    monkeypatch.delenv("KIT_FORCE_X264", raising=False)
    _as(monkeypatch, system, machine, has_vt)
    args = media.encoder_args()
    assert args[args.index("-c:v") + 1] == "libx264"


def test_the_force_switch_only_listens_to_one(monkeypatch):
    _as(monkeypatch, "Darwin", "arm64")
    monkeypatch.setenv("KIT_FORCE_X264", "0")
    args = media.encoder_args()
    assert args[args.index("-c:v") + 1] == "h264_videotoolbox"


def test_the_encoder_args_produce_a_playable_file(tmp_path):
    out = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=128x224:r=30:d=0.5", *media.encoder_args(), str(out)],
        check=True,
    )
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name,pix_fmt",
         "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert probe == "h264,yuv420p"


# --- free disk --------------------------------------------------------------------------------------


def test_require_free_passes_with_room(monkeypatch):
    monkeypatch.setattr(media.shutil, "disk_usage", lambda path: Usage(500 * GIB, 400 * GIB, 40 * GIB))
    media.require_free(5.0)


def test_require_free_stops_with_a_plain_sentence(monkeypatch):
    monkeypatch.setattr(media.shutil, "disk_usage", lambda path: Usage(500 * GIB, 497 * GIB, 3 * GIB))
    with pytest.raises(KitError) as caught:
        media.require_free(5.0)
    message = str(caught.value)
    assert "3.0 GB free" in message and "5 GB" in message
    assert "Traceback" not in message


def test_require_free_defaults_to_five_gb(monkeypatch):
    monkeypatch.setattr(media.shutil, "disk_usage", lambda path: Usage(500 * GIB, 496 * GIB, int(4.9 * GIB)))
    with pytest.raises(media.NotEnoughDisk):
        media.require_free()


# --- heavy slots --------------------------------------------------------------------------------------


def _locked_elsewhere(path) -> bool:
    """True when another process cannot take the lock on this slot file."""
    code = (
        "import fcntl, os, sys\n"
        "fd = os.open(sys.argv[1], os.O_RDWR)\n"
        "try:\n"
        "    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "except OSError:\n"
        "    sys.exit(3)\n"
    )
    return subprocess.run([sys.executable, "-c", code, str(path)]).returncode == 3


def test_a_slot_is_a_locked_file_while_held_and_free_after(slots):
    with media.heavy_slot() as held:
        assert held.is_file() and held.parent == slots and held.name == "slot1.lock"
        assert held.read_text().split()[0] == str(os.getpid())
        assert os.environ[media.SLOT_ENV] == str(held)
        assert _locked_elsewhere(held)
    assert not _locked_elsewhere(held)
    assert held.read_text() == ""
    assert media.SLOT_ENV not in os.environ


def test_waiting_for_a_slot_is_a_note(slots, monkeypatch, capsys):
    # The clip summary in onboarding keeps a failure's first line that is not a note, so the line a job prints
    # while it waits has to be a note: otherwise it can push out the line that says which step stopped.
    real, busy = media._try_take, iter([True])

    def all_busy_once(folder, limit, label):
        return None if next(busy, False) else real(folder, limit, label)

    monkeypatch.setattr(media, "_try_take", all_busy_once)
    with media.heavy_slot(poll=0.01):
        pass
    (line,) = capsys.readouterr().out.splitlines()
    assert line.startswith("note: ") and "heavy-job slot" in line


def test_a_nested_slot_in_the_same_process_does_not_take_a_second_one(slots, monkeypatch):
    monkeypatch.setenv("HEAVY_SLOTS", "1")
    with media.heavy_slot() as outer:
        with media.heavy_slot(timeout=0.5) as inner:
            assert inner == outer
        assert _locked_elsewhere(outer), "leaving the inner block must not free the outer slot"
        assert os.environ[media.SLOT_ENV] == str(outer)


def test_a_child_process_inherits_the_slot(slots, monkeypatch):
    monkeypatch.setenv("HEAVY_SLOTS", "1")
    code = (
        "from pathlib import Path\n"
        "from kit import media\n"
        f"media._slots_dir = lambda: Path({str(slots)!r})\n"
        "media.MIN_FREE_GB = 0.0\n"
        "with media.heavy_slot(timeout=1) as s: print(s)\n"
    )
    with media.heavy_slot() as held:
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, timeout=60)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == str(held)


def _hold(folder, seconds, started):
    from pathlib import Path as P

    from kit import media as m

    m._slots_dir = lambda: P(folder)
    m.MIN_FREE_GB = 0.0
    with m.heavy_slot():
        started.set()
        time.sleep(seconds)


def test_no_more_than_the_limit_run_at_once(slots, monkeypatch):
    monkeypatch.setenv("HEAVY_SLOTS", "2")
    context = multiprocessing.get_context("spawn")
    events = [context.Event() for _ in range(2)]
    holders = [context.Process(target=_hold, args=(str(slots), 4, event)) for event in events]
    for holder in holders:
        holder.start()
    try:
        for event in events:
            assert event.wait(60)
        with pytest.raises(media.SlotTimeout):
            with media.heavy_slot(timeout=0.6, poll=0.1):
                pass
    finally:
        for holder in holders:
            holder.join(30)
    with media.heavy_slot(timeout=5, poll=0.1):
        pass


def test_a_slot_held_by_a_killed_process_is_free_at_once(slots, monkeypatch):
    monkeypatch.setenv("HEAVY_SLOTS", "1")
    code = (
        "import sys, time\n"
        "from pathlib import Path\n"
        "from kit import media\n"
        f"media._slots_dir = lambda: Path({str(slots)!r})\n"
        "media.MIN_FREE_GB = 0.0\n"
        "with media.heavy_slot():\n"
        "    print('held', flush=True)\n"
        "    time.sleep(60)\n"
    )
    holder = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, cwd=ROOT)
    try:
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(media.SlotTimeout):
            with media.heavy_slot(timeout=0.3, poll=0.1):
                pass
        holder.kill()
        holder.wait(10)
        with media.heavy_slot(timeout=2, poll=0.1) as held:
            assert held == slots / "slot1.lock"
    finally:
        holder.kill()
        holder.stdout.close()
        holder.wait(10)


def _enter_and_leave(folder, log, rounds):
    from pathlib import Path as P

    from kit import media as m

    m._slots_dir = lambda: P(folder)
    m.MIN_FREE_GB = 0.0
    for _ in range(rounds):
        with m.heavy_slot(poll=0.005):
            with open(log, "a") as out:
                out.write(f"in {time.monotonic():.6f}\n")
            time.sleep(0.02)
            with open(log, "a") as out:
                out.write(f"out {time.monotonic():.6f}\n")


def test_contending_processes_never_hold_one_slot_together(slots, monkeypatch, tmp_path):
    monkeypatch.setenv("HEAVY_SLOTS", "1")
    log = tmp_path / "log.txt"
    context = multiprocessing.get_context("spawn")
    workers = [context.Process(target=_enter_and_leave, args=(str(slots), str(log), 5)) for _ in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(120)
        assert worker.exitcode == 0
    events = log.read_text().split()
    kinds = events[0::2]
    assert len(kinds) == 40
    assert kinds == ["in", "out"] * 20, "two holders overlapped"


def test_a_slot_checks_the_disk_before_the_job_starts(slots, monkeypatch):
    monkeypatch.setattr(media, "MIN_FREE_GB", 5.0)
    monkeypatch.setattr(media.shutil, "disk_usage", lambda path: Usage(500 * GIB, 498 * GIB, 2 * GIB))
    with pytest.raises(media.NotEnoughDisk):
        with media.heavy_slot():
            pass
    monkeypatch.setattr(media, "MIN_FREE_GB", 0.0)
    monkeypatch.setenv("HEAVY_SLOTS", "1")
    with media.heavy_slot(timeout=0.5, poll=0.05):
        pass  # the refused job gave its slot back


def test_the_command_line_runs_a_command_in_a_slot(slots, tmp_path):
    marker = tmp_path / "ran.txt"
    inner = tmp_path / "inner.py"
    inner.write_text(f"import os\nopen({str(marker)!r}, 'w').write(os.environ[{media.SLOT_ENV!r}])\n")
    outer = tmp_path / "outer.py"
    outer.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "from kit import media\n"
        f"media._slots_dir = lambda: Path({str(slots)!r})\n"
        "media.MIN_FREE_GB = 0.0\n"
        f"raise SystemExit(media.main(['run', '--', sys.executable, {str(inner)!r}]))\n"
    )
    done = subprocess.run([sys.executable, str(outer)], capture_output=True, text=True, cwd=ROOT, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
    assert Path(marker.read_text()).parent == slots


def test_probe_reads_duration_and_size(tmp_path):
    clip = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=96x160:r=30:d=1", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", str(clip)],
        check=True,
    )
    info = media.probe(clip)
    assert (info.width, info.height) == (96, 160)
    assert info.fps == pytest.approx(30)
    assert info.duration == pytest.approx(1.0, abs=0.05)
    assert info.has_audio is False
