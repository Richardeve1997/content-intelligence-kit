"""The whole character pipeline with a stand-in image tool, then the pack the runtime reads.

The module builds one synthetic character once (sheet, approval, poses, approval, face states, the
in-betweens and the pack) with `FakeProvider`, which draws block figures instead of calling a model.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

for _name in ("numpy", "PIL", "scipy", "skimage", "sklearn"):
    pytest.importorskip(_name, reason="needs the character group: uv sync --group character --inexact")

import numpy as np  # noqa: E402

import kit.character  # noqa: E402
from character_sprites import FakeProvider, image_size, load_rgba  # noqa: E402
from kit.config import KitError  # noqa: E402
from kit.character import faces, moves, poses, sheet, store  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_CHECK = ROOT / ".claude" / "skills" / "mini-me" / "kit" / "character" / "test_state.mjs"
CORE = ("idle", "talking", "point_left", "point_right", "point_up", "thumbs_up", "shrug", "thinking", "mind_blown",
        "celebrate", "hold_card", "typing", "facepalm", "wave")  # fmt: skip


def _build(folder: Path, tool: FakeProvider) -> Path:
    sheet.start(folder, description="a cheerful example character", outfit="a blue shirt and grey trousers", colours=24)
    sheet.make(folder, tool)
    sheet.approve(folder)
    made = poses.make(folder, tool)
    assert not made.waiting
    poses.approve(folder, list(poses.POSE_NAMES))
    faces.make(folder)
    made = moves.make(folder, tool)
    assert not made.waiting
    return kit.character.pack(folder)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    folder = tmp_path_factory.mktemp("creator") / "character"
    tool = FakeProvider(scale=4)
    manifest = _build(folder, tool)
    return folder, tool, manifest


def _rgba(path: Path) -> np.ndarray:
    return load_rgba(path)


def test_pack_manifest_runs_in_runtime(built):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not installed")
    folder, _, manifest = built
    done = subprocess.run([node, str(RUNTIME_CHECK), str(manifest)], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "all passed" in done.stdout


def test_the_manifest_has_the_pinned_shape(built):
    folder, _, manifest_path = built
    manifest = json.loads(manifest_path.read_text())
    assert manifest_path == folder / "manifest.json"
    assert manifest["canvas"] == [245, 284] and manifest["baseline_row"] == 280 and manifest["native_fps"] == 24
    assert set(manifest["poses"]) == set(CORE)
    assert set(manifest["moves"]) == {"idle", "talking", "point_left", "point_right", "point_up"}
    assert 2 <= len(manifest["palette"]) <= 24
    assert all(len(c) == 7 and c.startswith("#") for c in manifest["palette"])
    for name, entry in list(manifest["moves"].items()) + list(manifest["poses"].items()):
        strip = folder / entry["strip"]
        assert image_size(strip) == (245 * entry["frames"], 284), name
        assert len(entry["frame_info"]) == entry["frames"]
        assert all(0 <= frame < entry["frames"] and ticks >= 1 for frame, ticks in entry["sequence"])
    for name, size in manifest["faces"].items():
        assert list(image_size(folder / "faces" / f"{name}.png")) == size
    assert manifest["moves"]["talking"]["kind"] == "loop"
    point = manifest["moves"]["point_left"]
    assert point["kind"] == "oneshot" and point["frames"] == 4
    assert point["sequence"][point["hold"]][0] == 3, "the hold is on the key drawing"
    # the browser loads the same data by script tag
    script = (folder / "manifest.js").read_text()
    assert script.startswith("/*") and "window.CHARACTER_MANIFEST = " in script


def test_every_pose_stands_on_the_baseline(built):
    folder, _, _ = built
    for name in CORE:
        rgba = _rgba(folder / "poses" / f"{name}.png")
        assert rgba.shape == (284, 245, 4), name
        assert set(np.unique(rgba[..., 3]).tolist()) <= {0, 255}
        lowest = np.nonzero(rgba[..., 3].any(1))[0].max()
        if name == "celebrate":
            assert lowest < 279 - 4, "the jump stays in the air"
        else:
            assert lowest == 279, f"{name} does not stand on row 280"


def test_the_palette_is_locked(built):
    folder, _, manifest_path = built
    palette = {tuple(int(c[i : i + 2], 16) for i in (1, 3, 5)) for c in json.loads(manifest_path.read_text())["palette"]}
    for path in list((folder / "poses").glob("*.png")) + list((folder / "strips").glob("*.png")):
        if path.name == "board.png":
            continue
        rgba = _rgba(path)
        used = {tuple(int(v) for v in c) for c in rgba[..., :3][rgba[..., 3] > 0]}
        assert used <= palette, f"{path.name} has colours outside the locked palette"


def test_head_locked_poses_carry_the_face_anchors(built):
    folder, _, manifest_path = built
    manifest = json.loads(manifest_path.read_text())
    state = store.load(folder)
    for name in CORE:
        info = manifest["poses"][name]["frame_info"][0]
        mode = state["poses"][name]["face"]
        if mode in ("canon", "head"):
            assert info["mouth"] and info["eyes"], name
        else:
            assert info["mouth"] is None and info["eyes"] is None, name
    assert state["poses"]["thinking"]["face"] == "own"
    assert state["poses"]["talking"]["face"] == "head"
    # the face patches land on the face: the closed mouth patch is the pose's own mouth area
    idle = _rgba(folder / "poses" / "idle.png")
    mouth = manifest["poses"]["idle"]["frame_info"][0]["mouth"]
    patch = _rgba(folder / "faces" / "mouth_closed.png")
    h, w = patch.shape[:2]
    assert (idle[mouth[1] : mouth[1] + h, mouth[0] : mouth[0] + w] == patch).all()


def test_the_points_start_on_idle_and_end_on_the_key(built):
    folder, _, _ = built
    idle = _rgba(folder / "poses" / "idle.png")
    for name in ("point_left", "point_right", "point_up"):
        frames = sorted((folder / "moves" / name).glob("frame_*.png"))
        assert len(frames) == 4
        assert (_rgba(frames[0]) == idle).all()
        assert (_rgba(frames[-1]) == _rgba(folder / "poses" / f"{name}.png")).all()
        # the in-betweens stand on the same feet as the end frames
        for frame in frames[1:3]:
            rgba = _rgba(frame)
            assert np.nonzero(rgba[..., 3].any(1))[0].max() == 279
    talking = sorted((folder / "moves" / "talking").glob("frame_*.png"))
    assert len(talking) == 4 and (_rgba(talking[0]) == _rgba(folder / "poses" / "talking.png")).all()


def test_the_boards_are_written_for_each_approval(built):
    folder, _, _ = built
    for board in ("sheet/board.png", "poses/board.png", "faces/board.png"):
        assert image_size(folder / board)[0] > 200, board
    for name in ("talking", "point_left", "point_right", "point_up"):
        assert (folder / "moves" / name / f"{name}.gif").exists()


def test_the_whole_character_took_nine_image_calls(built):
    folder, tool, _ = built
    assert len(tool.calls) == 9, tool.calls
    assert store.load(folder)["calls"] == 9
    sheet_call, *rest = tool.calls
    assert sheet_call == ("sheet-r1.png", 0), "a description-only sheet sends no reference image"
    assert all(refs >= 1 for _, refs in rest), "every later call shows the model the approved character"


def test_a_rejected_pose_is_redrawn_on_its_own(built, tmp_path):
    folder, _, _ = built
    copy = tmp_path / "character"
    shutil.copytree(folder, copy)
    before = store.load(copy)["poses"]

    poses.redraw(copy, "shrug", note="Raise both hands a little higher.")
    tool = FakeProvider(scale=4)
    made = poses.make(copy, tool)

    assert made.done == ["shrug"]
    assert [name for name, _ in tool.calls] == ["pose-shrug-r2.png"]
    after = store.load(copy)["poses"]
    assert after["shrug"]["round"] == 2 and after["shrug"]["approved"] is False
    assert all(after[n] == before[n] for n in CORE if n != "shrug"), "the other poses stay as they were"
    with pytest.raises(KitError) as caught:
        kit.character.pack(copy)
    assert "shrug" in str(caught.value)
    poses.approve(copy, ["shrug"])
    assert kit.character.pack(copy).exists()


def test_the_redraw_note_reaches_the_prompt(built, tmp_path):
    folder, _, _ = built
    copy = tmp_path / "character"
    shutil.copytree(folder, copy)
    poses.redraw(copy, "wave", note="Open the hand wider.")
    seen = {}

    class Recorder(FakeProvider):
        def generate(self, prompt, refs, out):
            seen["prompt"] = prompt
            return super().generate(prompt, refs, out)

    poses.make(copy, Recorder(scale=4))
    assert "1. wave: " in seen["prompt"] and "Open the hand wider." in seen["prompt"]


def _packed_creator(built, tmp_path, monkeypatch) -> Path:
    folder, _, _ = built
    me = tmp_path / "me"
    shutil.copytree(folder, me / "character")
    monkeypatch.setenv("KIT_ME", str(me))
    return me / "character"


def test_a_face_change_after_packing_packs_the_character_again(built, tmp_path, monkeypatch, capsys):
    # The pack holds each face patch's size and where it sits on every drawing, and the runtime stretches the
    # patch to that size. A face changed after packing would otherwise be drawn stretched and out of place.
    from kit.character import __main__ as cli

    copy = _packed_creator(built, tmp_path, monkeypatch)
    before = json.loads((copy / "manifest.json").read_text())["faces"]
    rows = json.loads((copy / "faces" / "faces.json").read_text())["open_rows"]
    assert cli.main(["faces", "--open", "4" if rows != 4 else "2"]) == 0
    said = capsys.readouterr().out
    patches = json.loads((copy / "faces" / "faces.json").read_text())["patches"]
    packed = json.loads((copy / "manifest.json").read_text())["faces"]
    assert packed == {name: [r["w"], r["h"]] for name, r in patches.items()}
    assert packed != before, "the new face reached the pack"
    assert "Packed the character again" in said


def test_a_face_change_on_a_character_that_cannot_be_packed_now_says_the_pack_is_old(built, tmp_path, monkeypatch, capsys):
    from kit.character import __main__ as cli

    copy = _packed_creator(built, tmp_path, monkeypatch)
    poses.redraw(copy, "shrug", note="Raise both hands a little higher.")
    stale = (copy / "manifest.json").read_text()
    rows = json.loads((copy / "faces" / "faces.json").read_text())["open_rows"]
    assert cli.main(["faces", "--open", "4" if rows != 4 else "2"]) == 0
    said = capsys.readouterr().out
    assert "still has the old face" in said and "shrug" in said and "kit.character pack" in said
    assert (copy / "manifest.json").read_text() == stale


def test_pack_says_what_is_missing_on_a_new_character(tmp_path):
    folder = tmp_path / "character"
    sheet.start(folder, description="someone", outfit="something", colours=16)
    with pytest.raises(KitError) as caught:
        kit.character.pack(folder)
    assert "sheet" in str(caught.value)


def test_the_sheet_can_be_snapped_again_without_an_image_call(tmp_path):
    folder = tmp_path / "character"
    tool = FakeProvider(scale=4)
    sheet.start(folder, description="someone", outfit="something", colours=24)
    sheet.make(folder, tool)
    first = load_rgba(folder / "sheet" / "front.png")
    board = sheet.make(folder, None, redraw=False)
    assert board.exists() and len(tool.calls) == 1 and store.load(folder)["calls"] == 1
    assert (load_rgba(folder / "sheet" / "front.png") == first).all()
    assert store.load(folder)["sheet"]["round"] == 1


def test_poses_and_moves_can_be_snapped_again_without_an_image_call(built, tmp_path):
    folder, _, _ = built
    copy = tmp_path / "character"
    shutil.copytree(folder, copy)
    before = store.load(copy)
    shrug = load_rgba(copy / "poses" / "shrug.png")

    made = poses.make(copy, None, ["shrug"], redraw=False)
    assert made.done == ["shrug"], "only the pose asked for is snapped again"
    assert (load_rgba(copy / "poses" / "shrug.png") == shrug).all()
    after = store.load(copy)
    assert after["calls"] == before["calls"]
    assert after["poses"]["shrug"]["approved"] is False
    assert after["poses"]["mind_blown"] == before["poses"]["mind_blown"], "a pose from the same picture is left alone"

    made = moves.make(copy, None, ["point_up"], redraw=False)
    assert made.done == ["point_up"] and store.load(copy)["calls"] == before["calls"]
    with pytest.raises(KitError):
        moves.make(copy, None, ["shrug"], redraw=False)


def test_the_hand_anchors_sit_on_the_hands_that_do_the_move(built):
    folder, _, manifest_path = built
    manifest = json.loads(manifest_path.read_text())
    hand = {n: manifest["poses"][n]["frame_info"][0]["hand"] for n in CORE}
    idle_box = np.nonzero(load_rgba(folder / "poses" / "idle.png")[..., 3].any(0))[0]
    assert hand["point_left"][0] < idle_box.min(), "the point left fingertip is out past the body on the left"
    assert hand["point_right"][0] > idle_box.max(), "the point right fingertip is out past the body on the right"
    top = np.nonzero(load_rgba(folder / "poses" / "idle.png")[..., 3].any(1))[0].min()
    assert hand["point_up"][1] < top, "the point up fingertip is above the head"
    for name in ("wave", "talking", "thumbs_up"):
        x, y = hand[name]
        canvas = load_rgba(folder / "poses" / f"{name}.png")
        assert canvas[y, x, 3] == 255 and x > 122, f"{name}: the anchor is on the raised hand"
    assert hand["shrug"] is None and hand["idle"] is None
