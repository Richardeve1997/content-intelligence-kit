"""The character commands, driven the way the skill drives them, with the manual image tool.

The manual tool writes each prompt to me/character/inbox/ and the command exits with status 3 until the
picture is saved there. Here a synthetic picture stands in for the one the creator would make by hand.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

for _name in ("numpy", "PIL", "scipy", "skimage", "sklearn"):
    pytest.importorskip(_name, reason="needs the character group: uv sync --group character")

import numpy as np  # noqa: E402

from character_sprites import FakeProvider  # noqa: E402
from kit.character import __main__ as cli  # noqa: E402


def test_the_manual_tool_waits_for_the_sheet_then_carries_on(me, capsys):
    assert cli.main(["start", "--tool", "manual", "--colours", "16", "--description", "a cheerful example character",
                     "--outfit", "a blue shirt and grey trousers"]) == 0  # fmt: skip
    capsys.readouterr()

    assert cli.main(["sheet"]) == cli.WAITING
    said = capsys.readouterr().out
    prompt = me / "inbox" / "sheet-r1.prompt.txt"
    assert prompt.is_file() and "a cheerful example character" in prompt.read_text()
    assert "me/character/inbox/sheet-r1.prompt.txt" in said and "me/character/inbox/sheet-r1.png" in said
    assert json.loads((me / "character.json").read_text())["calls"] == 0, "waiting is not an image call"

    FakeProvider(scale=4).generate(prompt.read_text(), [], me / "inbox" / "sheet-r1.png")
    assert cli.main(["sheet"]) == 0
    assert "Look at it here: me/character/sheet/board.png" in capsys.readouterr().out
    assert cli.main(["approve", "sheet"]) == 0
    assert "locked at" in capsys.readouterr().out

    assert cli.main(["poses"]) == cli.WAITING
    said = capsys.readouterr().out
    assert said.count("Waiting for a picture.") == 4, "all four pose prompts are written at once"
    assert sorted(p.name for p in (me / "inbox").glob("poses-*.prompt.txt")) == [f"poses-{n}-r1.prompt.txt" for n in (1, 2, 3, 4)]
    assert (me / "inbox" / "poses-1-r1.attach-2.png").is_file(), "the pictures to attach are copied beside the prompt"


def test_a_wrong_step_is_one_plain_sentence(me, capsys):
    assert cli.main(["pack"]) == 1
    err = capsys.readouterr().err
    assert "There is no character yet" in err and "Traceback" not in err
    assert cli.main(["start", "--description", " ", "--outfit", "a coat"]) == 1
    assert "Describe the character" in capsys.readouterr().err


# --- the face check at the sheet gate -----------------------------------------------------------------------------------


class BackFirst(FakeProvider):
    """Draws the back of the head as the front view, so the sheet has no face to measure."""

    def generate(self, prompt, refs, out):
        from character_sprites import compose_row, draw_figure, model_like

        if not out.stem.startswith("sheet"):
            return super().generate(prompt, refs, out)
        row = [draw_figure("back")[:1] + (0,), draw_figure("idle")[:1] + (0,)]
        out.parent.mkdir(parents=True, exist_ok=True)
        model_like(compose_row(row), self.scale, noise=1.0).save(out)
        return out


def _sheet_drawn(me, capsys, tool_class=FakeProvider):
    assert cli.main(["start", "--tool", "manual", "--colours", "16", "--description", "someone", "--outfit", "a shirt"]) == 0
    assert cli.main(["sheet"]) == cli.WAITING
    prompt = me / "inbox" / "sheet-r1.prompt.txt"
    tool_class(scale=4).generate(prompt.read_text(), [], me / "inbox" / "sheet-r1.png")
    capsys.readouterr()
    code = cli.main(["sheet"])
    return code, capsys.readouterr()


def _state(me):
    return json.loads((me / "character.json").read_text())


def test_the_face_check_comes_with_the_sheet_before_approval(me, capsys):
    code, said = _sheet_drawn(me, capsys)
    assert code == 0
    assert "Check the face here before approving: me/character/faces/board.png" in said.out
    assert (me / "faces" / "board.png").is_file() and (me / "faces" / "faces.json").is_file()
    assert _state(me)["sheet"]["approved"] is False, "the face is checked before anything is approved or drawn"
    assert _state(me)["calls"] == 1


def test_marks_are_kept_in_the_record_and_used_by_the_later_steps(me, capsys):
    from kit.character import faces

    _sheet_drawn(me, capsys)
    state = _state(me)
    face, _ = faces.measure(faces.front_canvas(me, state), None)
    mouth = face.mouth.grown(1, 0, 1, 0)
    eyes = face.eyes

    assert cli.main(["faces", "--eyes", eyes.text(), "--mouth", mouth.text()]) == 0
    assert "marked by hand" in capsys.readouterr().out
    assert _state(me)["face_marks"] == {"eyes": [eyes.x, eyes.y, eyes.w, eyes.h], "mouth": [mouth.x, mouth.y, mouth.w, mouth.h]}

    assert cli.main(["approve", "sheet"]) == 0
    said = capsys.readouterr().out
    assert "Check the face before drawing the poses" in said and "idle pose is approved with it" in said
    approved = faces.FaceBox.from_dict(_state(me)["face"])
    assert approved.mouth == mouth, "approval uses the marked mouth"
    patches = json.loads((me / "faces" / "faces.json").read_text())
    assert patches["marked"] is True and patches["patches"]["mouth_closed"]["x"] <= mouth.x

    assert cli.main(["faces", "--clear"]) == 0
    assert "as measured" in capsys.readouterr().out and _state(me)["face_marks"] is None


def test_a_face_that_cannot_be_measured_blocks_approval_until_it_is_marked(me, capsys):
    from character_sprites import draw_figure
    from kit.character import art, faces

    code, said = _sheet_drawn(me, capsys, BackFirst)
    assert code == 0, "the sheet itself is drawn; only its face is missing"
    assert "could not be measured" in said.out and "faces --eyes x,y,w,h --mouth x,y,w,h" in said.out
    assert (me / "faces" / "board.png").is_file(), "a board with a grid to read the boxes off"

    assert cli.main(["approve", "sheet"]) == 1
    err = capsys.readouterr().err
    assert "smaller hat" in err and "faces --eyes x,y,w,h --mouth x,y,w,h" in err
    assert _state(me)["sheet"]["approved"] is False

    # mark the face where it would be: the same figure facing front, placed the same way
    front, _, _ = art.place(art.crop(np.kron(draw_figure("idle")[0], np.ones((2, 2, 1), np.uint8))))
    face = faces.locate(front)
    assert cli.main(["faces", "--eyes", face.eyes.text(), "--mouth", face.mouth.text()]) == 0
    assert cli.main(["approve", "sheet"]) == 0
    assert faces.FaceBox.from_dict(_state(me)["face"]).mouth == face.mouth


def test_a_new_sheet_drops_the_marks_but_a_resnap_keeps_them(me, capsys):
    from kit.character import faces

    _sheet_drawn(me, capsys)
    face, _ = faces.measure(faces.front_canvas(me, _state(me)), None)
    assert cli.main(["faces", "--mouth", face.mouth.text()]) == 0
    assert cli.main(["sheet", "--resnap"]) == 0
    assert _state(me)["face_marks"] == {"mouth": [face.mouth.x, face.mouth.y, face.mouth.w, face.mouth.h]}
    assert cli.main(["sheet"]) == cli.WAITING  # a new round goes to the inbox
    FakeProvider(scale=4).generate((me / "inbox" / "sheet-r2.prompt.txt").read_text(), [], me / "inbox" / "sheet-r2.png")
    assert cli.main(["sheet"]) == 0
    assert _state(me)["face_marks"] is None, "marks read off the old picture are dropped"


@pytest.mark.parametrize("box", ["300,10,4,4", "10,10,4,4x"])
def test_a_mark_off_the_canvas_or_badly_written_is_a_plain_error(me, capsys, box):
    _sheet_drawn(me, capsys)
    assert cli.main(["faces", "--mouth", box]) == 1
    err = capsys.readouterr().err
    assert ("runs off" in err or "x,y,w,h" in err) and "Traceback" not in err


# --- words, redraws and setup ---------------------------------------------------------------------------------------------


def test_words_with_apostrophes_can_come_from_standard_input(me, capsys, monkeypatch):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("I'm O'Neill's baker, with a flour-dusted apron\n"))
    assert cli.main(["start", "--description", "-", "--outfit", "a blue shirt"]) == 0
    assert _state(me)["description"] == "I'm O'Neill's baker, with a flour-dusted apron"
    monkeypatch.setattr("sys.stdin", io.StringIO("both"))
    assert cli.main(["start", "--description", "-", "--outfit", "-"]) == 1
    assert "Only one of" in capsys.readouterr().err


def test_redraw_explains_a_move_that_does_not_exist_and_the_idle_pose(me, capsys):
    assert cli.main(["start", "--description", "someone", "--outfit", "a shirt"]) == 0
    capsys.readouterr()
    assert cli.main(["redraw", "shrug", "--move"]) == 1
    assert "shrug has no in-betweens" in capsys.readouterr().err
    assert cli.main(["redraw", "idle"]) == 1
    err = capsys.readouterr().err
    assert "move the me/character folder somewhere else" in err and "image calls" in err


def test_a_missing_character_group_is_one_plain_sentence(me, tmp_path):
    import subprocess
    import sys

    code = ("import sys; sys.modules['numpy'] = None; from kit.character.__main__ import main; "
            "raise SystemExit(main(['status']))")  # fmt: skip
    root = Path(__file__).resolve().parents[2]
    done = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin", "KIT_ME": str(me.parent), "PYTHONPATH": str(root)})  # fmt: skip
    assert done.returncode == 1
    assert done.stderr.strip() == cli.MISSING_GROUP
    assert "Traceback" not in done.stderr


def test_start_again_with_the_kept_photos_keeps_them(me, tmp_path):
    from PIL import Image

    photos = []
    for n in range(3):
        path = tmp_path / f"snap-{n}.png"
        Image.new("RGB", (20, 20), (40 * n, 90, 120)).save(path)
        photos.append(path)
    assert cli.main(["start", "--description", "someone", "--outfit", "a shirt", "--photos", *map(str, photos)]) == 0
    kept = sorted((me / "photos").iterdir())
    data = [p.read_bytes() for p in kept]

    assert cli.main(["start", "--description", "someone else", "--outfit", "a shirt", "--photos", *map(str, kept)]) == 0

    again = sorted((me / "photos").iterdir())
    assert [p.name for p in again] == [p.name for p in kept] and [p.read_bytes() for p in again] == data
    assert not (me / ".photos-new").exists() and not (me / ".photos-old").exists()
