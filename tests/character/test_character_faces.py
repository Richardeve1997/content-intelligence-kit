"""kit.character.faces: the face is measured on the creator's own sprite, never taken from fixed rectangles."""

from __future__ import annotations

import pytest

for _name in ("numpy", "PIL", "scipy", "skimage", "sklearn"):
    pytest.importorskip(_name, reason="needs the character group: uv sync --group character --inexact")

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from character_sprites import draw_figure, load_rgba  # noqa: E402
from kit.character import faces  # noqa: E402


def _close(found, drawn, tolerance=2):
    """found is a Box (x, y, w, h); drawn is (x0, y0, x1, y1) inclusive."""
    x0, y0, x1, y1 = drawn
    return (
        abs(found.x - x0) <= tolerance
        and abs(found.y - y0) <= tolerance
        and abs(found.x + found.w - 1 - x1) <= tolerance
        and abs(found.y + found.h - 1 - y1) <= tolerance
    )


def _sprite(tmp_path, pose="idle", shift=(0, 0), glasses=False):
    rgba, info = draw_figure(pose, glasses=glasses)
    dx, dy = shift
    rgba = np.roll(rgba, (dy, dx), axis=(0, 1))
    moved = {k: (v[0] + dx, v[1] + dy, v[2] + dx, v[3] + dy) for k, v in info.items() if k != "air"}
    path = tmp_path / f"{pose}.png"
    Image.fromarray(rgba, "RGBA").save(path)
    return path, moved


@pytest.mark.parametrize("shift", [(0, 0), (-9, 7), (11, -5)], ids=["as drawn", "moved left and down", "moved right and up"])
def test_faces_locate_synthetic(tmp_path, shift):
    path, drawn = _sprite(tmp_path, shift=shift)

    face = faces.locate(path)

    assert _close(face, drawn["face"]), (face, drawn["face"])
    assert _close(face.left_eye, drawn["left_eye"]), (face.left_eye, drawn["left_eye"])
    assert _close(face.right_eye, drawn["right_eye"]), (face.right_eye, drawn["right_eye"])
    assert _close(face.mouth, drawn["mouth"]), (face.mouth, drawn["mouth"])


def test_faces_locate_finds_eyes_behind_glasses(tmp_path):
    """Frames joined by a bridge and running out to the hair make one dark shape that skin does not close in.
    The eyes are still found one on each side, and the blink closes the eyes but keeps the frames."""
    path, drawn = _sprite(tmp_path, glasses=True)
    face = faces.locate(path)
    assert _close(face.left_eye, drawn["left_eye"]), (face.left_eye, drawn["left_eye"])
    assert _close(face.right_eye, drawn["right_eye"]), (face.right_eye, drawn["right_eye"])
    assert _close(face.mouth, drawn["mouth"]), (face.mouth, drawn["mouth"])

    sprite = load_rgba(path)
    states = faces.face_states(sprite, face)
    rect = states.rects["eyes_closed"]
    shut, open_ = states.patches["eyes_closed"], states.patches["eyes_open"]
    x0, y0, x1, y1 = drawn["left_eye"]
    bridge_row = y0 + 1 - rect.y  # the bridge runs along the eye's second row, between the lenses
    bridge = slice(x1 + 2 - rect.x, x1 + 4 - rect.x)
    assert (shut[bridge_row, bridge] == open_[bridge_row, bridge]).all(), "the bridge of the glasses stays"
    pupil = (y0 + 1 - rect.y, x0 + 2 - rect.x)
    assert (shut[pupil] != open_[pupil]).any(), "the pupil is closed over"


def test_the_head_ends_at_the_neck(tmp_path):
    path, drawn = _sprite(tmp_path)
    face = faces.locate(path)
    chin = drawn["face"][3]
    assert chin < face.neck <= chin + 4


def test_face_states_change_only_their_own_cells(tmp_path):
    path, _ = _sprite(tmp_path)
    sprite = load_rgba(path)
    face = faces.locate(path)

    states = faces.face_states(sprite, face)

    assert set(states.patches) == {"mouth_closed", "mouth_mid", "mouth_open", "eyes_open", "eyes_closed"}
    mouth, eyes = states.rects["mouth_closed"], states.rects["eyes_open"]
    assert mouth.y >= eyes.y + eyes.h or eyes.y >= mouth.y + mouth.h, "the mouth and eye patches never overlap"
    colours = {tuple(c) for c in sprite[..., :3][sprite[..., 3] > 0]}
    for name, patch in states.patches.items():
        rect = states.rects[name]
        assert patch.shape == (rect.h, rect.w, 4)
        assert {tuple(c) for c in patch[..., :3][patch[..., 3] > 0]} <= colours, f"{name} adds a colour"
    # the closed states are plain crops; the open ones differ from them
    assert (states.patches["mouth_closed"] == sprite[mouth.y : mouth.y + mouth.h, mouth.x : mouth.x + mouth.w]).all()
    assert (states.patches["mouth_open"] != states.patches["mouth_closed"]).any()
    assert (states.patches["mouth_mid"] != states.patches["mouth_closed"]).any()
    assert (states.patches["eyes_closed"] != states.patches["eyes_open"]).any()


def test_a_sprite_with_no_face_is_a_plain_error(tmp_path):
    blank = np.zeros((60, 40, 4), np.uint8)
    blank[10:50, 10:30] = (90, 90, 90, 255)
    path = tmp_path / "blob.png"
    Image.fromarray(blank, "RGBA").save(path)
    with pytest.raises(faces.FaceNotFound) as caught:
        faces.locate(path)
    assert str(caught.value).startswith("The face on the front view could not be measured (")
    assert "Traceback" not in str(caught.value)
    assert "smaller hat" in faces.FIX and "faces --eyes x,y,w,h --mouth x,y,w,h" in faces.FIX


# --- other looks: skin tones, hair, hats, beards --------------------------------------------------------------------

LOOKS = {
    "fair skin, blonde hair": dict(skin=(250, 228, 214), hair=(222, 190, 112)),
    "medium-dark skin, black hair": dict(skin=(141, 85, 56), hair=(30, 26, 28)),
    "deep skin, black hair": dict(skin=(74, 48, 36), hair=(28, 24, 26), mouth=(110, 50, 50)),
    "a cap": dict(hat=14),
    "a tall hat": dict(hat=34),
    "tall hair": dict(hat=30, hat_colour=(104, 62, 40)),
    # a warm top close to a warm skin in lightness but not in hue: the face must not run down into it
    "warm skin, mustard top": dict(skin=(204, 116, 59), shirt=(204, 133, 20)),
}


def _scaled(rgba, info, scale):
    if scale == 1:
        return rgba, info
    big = np.kron(rgba, np.ones((scale, scale, 1), np.uint8))
    boxes = {k: (v[0] * scale, v[1] * scale, v[2] * scale + scale - 1, v[3] * scale + scale - 1)
             for k, v in info.items() if k != "air"}  # fmt: skip
    return big, boxes


@pytest.mark.parametrize("scale", [1, 2], ids=["1x", "2x"])
@pytest.mark.parametrize("look", list(LOOKS))
def test_faces_locate_other_looks(look, scale):
    rgba, drawn = _scaled(*draw_figure("idle", **LOOKS[look]), scale)

    face = faces.locate(rgba)

    assert _close(face, drawn["face"]), (face.box, drawn["face"])
    assert _close(face.left_eye, drawn["left_eye"]), (face.left_eye, drawn["left_eye"])
    assert _close(face.right_eye, drawn["right_eye"]), (face.right_eye, drawn["right_eye"])
    assert _close(face.mouth, drawn["mouth"]), (face.mouth, drawn["mouth"])


def test_no_white_and_no_outline_colour_is_ever_skin():
    """Eye whites sit close to fair skin and black hair close to deep skin; neither may count as a shade of it."""
    from character_sprites import EYE_WHITE, OUTLINE

    fair, _ = draw_figure("idle", skin=(250, 228, 214), hair=(222, 190, 112))
    assert EYE_WHITE not in faces.skin_colours(fair, (250, 228, 214))
    deep, _ = draw_figure("idle", skin=(74, 48, 36), hair=(28, 24, 26), mouth=(110, 50, 50))
    family = faces.skin_colours(deep, (74, 48, 36))
    assert OUTLINE not in family and (28, 24, 26) not in family and (110, 50, 50) not in family
    assert tuple(int(c * 0.92) for c in (74, 48, 36)) in family, "the cheek shade is still skin"


BEARD = (60, 40, 30)


@pytest.mark.parametrize("scale", [1, 2], ids=["1x", "2x"])
def test_faces_locate_finds_the_mouth_inside_a_beard(scale):
    rgba, drawn = _scaled(*draw_figure("idle", hair=BEARD, beard=BEARD), scale)

    face = faces.locate(rgba)

    assert _close(face.left_eye, drawn["left_eye"]) and _close(face.right_eye, drawn["right_eye"])
    assert _close(face.mouth, drawn["mouth"]), (face.mouth, drawn["mouth"])
    assert abs(face.y - drawn["face"][1]) <= 2
    assert face.y1 >= face.mouth.y1 and face.neck >= face.mouth.y1, "the face reaches down past the mouth in the beard"


def test_a_beard_stays_a_beard_when_the_mouth_opens():
    rgba, _ = draw_figure("idle", hair=BEARD, beard=BEARD)
    face = faces.locate(rgba)
    states = faces.face_states(rgba, face)
    skin = faces.skin_colours(rgba, face.skin)
    for name in ("mouth_mid", "mouth_open"):
        rect = states.rects[name]
        before = rgba[rect.y : rect.y1, rect.x : rect.x1]
        after = states.patches[name]
        was_beard = (before[..., :3] == BEARD).all(2)
        now_skin = np.array([[tuple(int(v) for v in c) in skin for c in row] for row in after[..., :3]])
        assert not (was_beard & now_skin).any(), f"{name} paints skin into the beard"
        assert (after != before).any()


BEARDS = {
    "a grey beard on black hair": dict(hair=(30, 26, 28), beard=(150, 150, 150)),
    "a beard a darker shade of the hair": dict(beard=(76, 45, 29)),
    "a beard under a red cap": dict(hat=14, beard=BEARD),
}


@pytest.mark.parametrize("scale", [1, 2], ids=["1x", "2x"])
@pytest.mark.parametrize("cheeks", [True, False], ids=["skin cheeks", "flat top"])
@pytest.mark.parametrize("look", list(BEARDS))
def test_a_beard_of_any_colour_is_followed(look, cheeks, scale):
    """A beard need not share a colour with the hair or a hat, and it may start across the whole face in one row,
    with no skin above the moustache."""
    rgba, drawn = _scaled(*draw_figure("idle", beard_cheeks=cheeks, **BEARDS[look]), scale)

    face = faces.locate(rgba)

    assert _close(face.left_eye, drawn["left_eye"]) and _close(face.right_eye, drawn["right_eye"])
    assert _close(face.mouth, drawn["mouth"]), (face.mouth, drawn["mouth"])
    chin = drawn["face"][3]
    assert chin < face.neck <= chin + 1 + 2 * scale, (face.neck, chin)


@pytest.mark.parametrize(("length", "scale"), [(4, 1), (2, 2), (4, 2)], ids=["4 rows at 1x", "2 rows at 2x", "4 rows at 2x"])
def test_a_beard_hanging_below_the_chin_stays_with_the_head(length, scale):
    """A beard that ends a little way below the chin is followed to its end: the eyes and mouth are found, and the
    head holds the whole beard and stops under it."""
    rgba, drawn = _scaled(*draw_figure("idle", hair=BEARD, beard=BEARD, beard_length=length), scale)

    face = faces.locate(rgba)

    assert _close(face.left_eye, drawn["left_eye"]) and _close(face.right_eye, drawn["right_eye"])
    assert _close(face.mouth, drawn["mouth"]), (face.mouth, drawn["mouth"])
    bottom = drawn["face"][3] + length * scale  # the beard's last row
    assert bottom < face.neck <= bottom + 1 + 2 * scale, (face.neck, bottom)


def test_a_beard_in_many_shades_is_measured_without_an_error():
    """No colour of the first beard row covers a fair share of it, so its commonest colour sets the beard."""
    from character_sprites import OY

    rgba, _ = draw_figure("idle", hair=BEARD, beard=BEARD, beard_cheeks=False)
    shades = [(60 + 3 * i, 40 + 2 * i, 30 + i) for i in range(11)]
    rows = np.arange(rgba.shape[0])[:, None]
    ys, xs = np.nonzero((rgba[..., :3] == BEARD).all(2) & (rows >= OY + 26))
    for k, (y, x) in enumerate(zip(ys, xs)):
        rgba[y, x, :3] = shades[k % len(shades)]
    try:
        faces.locate(rgba)
    except faces.FaceNotFound:
        pass  # a plain sentence for the board is fine; any other error is not


@pytest.mark.parametrize("scale", [1, 2], ids=["1x", "2x"])
def test_a_beard_too_long_to_follow_never_stretches_the_head_into_the_body(scale):
    """A beard hanging well below the chin runs past the search and is left for the creator to mark (the face
    board says so). Whatever is measured then, the head never reaches down into the top."""
    rgba, drawn = _scaled(*draw_figure("idle", hair=BEARD, beard=BEARD, beard_length=12), scale)
    try:
        face = faces.locate(rgba)
    except faces.FaceNotFound:
        return
    assert face.neck <= drawn["body"][1], (face.neck, drawn["body"][1])


# --- where the head ends: short necks, no neck, collars ---------------------------------------------------------------

NECKS = {
    "a short neck": dict(neck=3),
    "a one-row neck": dict(neck=1),
    "no neck": dict(neck=0),
    "a high collar": dict(collar=(64, 116, 178)),  # a turtleneck in the top's own colour, wider than the neck
    "a cream collar on a short neck": dict(neck=2, collar=(236, 228, 210)),
    "no neck, a top the colour of the hair": dict(neck=0, hair=(30, 26, 28), shirt=(30, 26, 28)),
    "a beard and a short neck": dict(neck=2, hair=BEARD, beard=BEARD),
    "a beard and no neck": dict(neck=0, hair=BEARD, beard=BEARD),
}


@pytest.mark.parametrize("scale", [1, 2], ids=["1x", "2x"])
@pytest.mark.parametrize("look", list(NECKS))
def test_the_head_ends_just_under_the_chin_whatever_is_below_it(look, scale):
    """A top right under the chin, a high collar, or a beard over a short neck never stretches the head down
    into the body: the neck row stays within a few rows of the chin, and the eyes and mouth are where drawn."""
    rgba, drawn = _scaled(*draw_figure("idle", **NECKS[look]), scale)

    face = faces.locate(rgba)

    assert _close(face.left_eye, drawn["left_eye"]) and _close(face.right_eye, drawn["right_eye"])
    assert _close(face.mouth, drawn["mouth"]), (face.mouth, drawn["mouth"])
    chin, body = drawn["face"][3], drawn["body"][1]
    assert chin < face.neck <= min(body, chin + 1 + 2 * scale), (face.neck, chin, body)


@pytest.mark.parametrize("scale", [1, 2], ids=["1x", "2x"])
@pytest.mark.parametrize("look", ["a short neck", "a high collar", "no neck, a top the colour of the hair"])
def test_the_head_lock_never_pastes_the_front_views_body_over_a_pose(look, scale):
    """The pose step copies everything above the neck row from the front view onto a pose. With the neck in
    the right place, arms across or beside the chest keep every cell of the body."""
    from kit.character import poses

    idle, drawn = _scaled(*draw_figure("idle", **NECKS[look]), scale)
    face = faces.locate(idle)
    body = drawn["body"][1]
    for pose in ("thumbs_up", "shrug", "hold_card", "wave"):
        before, _ = _scaled(*draw_figure(pose, **NECKS[look]), scale)
        after = before.copy()
        poses.head_lock(after, idle, face, 0, 0)
        changed = (after != before).any(2)
        assert not changed[body:].any(), f"{pose}: {int(changed[body:].sum())} cells of the body changed"


# --- what sits under the chin: tops near the beard's colour, short beards, collars, scarves, short tops ------------------

TOPS_NEAR_THE_BEARD = {
    "a dark brown beard over a black top": dict(hair=(60, 40, 30), beard=(60, 40, 30), shirt=(28, 28, 32)),
    "a black beard over a black top": dict(hair=(30, 26, 28), beard=(30, 26, 28), shirt=(28, 28, 32)),
    "a dark brown beard over a dark brown top": dict(hair=(60, 40, 30), beard=(60, 40, 30), shirt=(72, 52, 40)),
    "a white beard over a white top": dict(beard=(235, 235, 235), shirt=(240, 240, 240)),
}


@pytest.mark.parametrize("scale", [1, 2], ids=["1x", "2x"])
@pytest.mark.parametrize("neck", [0, 1, 2], ids=["no neck", "a one-row neck", "a two-row neck"])
@pytest.mark.parametrize("look", list(TOPS_NEAR_THE_BEARD))
def test_a_top_close_in_colour_to_the_beard_never_swallows_it(look, neck, scale):
    """A top in a colour near the beard's, right under the chin or after a neck too short to narrow the figure, is
    not part of the beard. The beard keeps its own colours, the mouth inside it is found, and the head ends at
    the chin."""
    rgba, drawn = _scaled(*draw_figure("idle", neck=neck, **TOPS_NEAR_THE_BEARD[look]), scale)

    face = faces.locate(rgba)

    assert _close(face.left_eye, drawn["left_eye"]) and _close(face.right_eye, drawn["right_eye"])
    assert _close(face.mouth, drawn["mouth"]), (face.mouth, drawn["mouth"])
    chin, body = drawn["face"][3], drawn["body"][1]
    assert chin < face.neck <= min(body, chin + 1 + 2 * scale), (face.neck, chin, body)


@pytest.mark.parametrize("scale", [1, 2], ids=["1x", "2x"])
@pytest.mark.parametrize("wide", [False, True], ids=["8 wide", "14 wide"])
@pytest.mark.parametrize("cheeks", [True, False], ids=["skin cheeks", "flat top"])
@pytest.mark.parametrize("last_row", [30, 31, 32], ids=["to the mouth", "a row under it", "two rows under it"])
def test_a_short_beard_ending_on_the_mouths_rows_keeps_the_mouth(last_row, cheeks, wide, scale):
    """A moustache with mutton chops: the beard ends on or just under the mouth's rows, so the mouth covers a fair
    share of the beard's cells. It is still the mouth, not one of the beard's colours."""
    figure = draw_figure("idle", hair=BEARD, beard=BEARD, beard_cheeks=cheeks, beard_end=last_row, mouth_wide=wide)
    rgba, drawn = _scaled(*figure, scale)

    face = faces.locate(rgba)

    assert _close(face.left_eye, drawn["left_eye"]) and _close(face.right_eye, drawn["right_eye"])
    assert _close(face.mouth, drawn["mouth"]), (face.mouth, drawn["mouth"])
    chin = drawn["face"][3]
    assert chin < face.neck <= chin + 1 + 2 * scale, (face.neck, chin)


COLLARS = {
    "a cream collar": (236, 228, 210),
    "a red collar": (176, 44, 44),
    "a green collar": (60, 150, 80),
}


@pytest.mark.parametrize("scale", [1, 2], ids=["1x", "2x"])
@pytest.mark.parametrize("neck", [5, 3], ids=["a full neck", "a short neck"])
@pytest.mark.parametrize("look", list(COLLARS))
def test_a_collar_in_its_own_colour_is_not_a_beard(look, neck, scale):
    """A turtleneck in a colour the head does not have, touching the chin and more than half the face wide, is
    clothing: the mouth is drawn in the skin above it, so the head ends at the chin, not under the collar."""
    rgba, drawn = _scaled(*draw_figure("idle", neck=neck, collar=COLLARS[look]), scale)

    face = faces.locate(rgba)

    assert _close(face.left_eye, drawn["left_eye"]) and _close(face.right_eye, drawn["right_eye"])
    assert _close(face.mouth, drawn["mouth"]), (face.mouth, drawn["mouth"])
    chin = drawn["face"][3]
    assert chin < face.neck <= chin + 1 + 2 * scale, (face.neck, chin)


@pytest.mark.parametrize("scale", [1, 2], ids=["1x", "2x"])
@pytest.mark.parametrize("colour", [(236, 228, 210), (176, 44, 44)], ids=["cream", "red"])
def test_a_scarf_hanging_from_the_chin_is_not_a_beard(colour, scale):
    """A scarf nearly as wide as the face, hanging six rows from the chin over the neck and the top, is clothing
    too: the head ends at the chin."""
    rgba, drawn = _scaled(*draw_figure("idle", scarf=colour, scarf_rows=6), scale)

    face = faces.locate(rgba)

    assert _close(face.left_eye, drawn["left_eye"]) and _close(face.right_eye, drawn["right_eye"])
    assert _close(face.mouth, drawn["mouth"]), (face.mouth, drawn["mouth"])
    chin = drawn["face"][3]
    assert chin < face.neck <= chin + 1 + 2 * scale, (face.neck, chin)


@pytest.mark.parametrize("scale", [1, 2], ids=["1x", "2x"])
@pytest.mark.parametrize("look", ["a cream collar", "a red scarf"])
def test_the_head_lock_keeps_the_body_under_a_collar_or_a_scarf(look, scale):
    from kit.character import poses

    kw = dict(collar=COLLARS["a cream collar"]) if look == "a cream collar" else dict(scarf=(176, 44, 44), scarf_rows=6)
    idle, drawn = _scaled(*draw_figure("idle", **kw), scale)
    face = faces.locate(idle)
    body = drawn["body"][1]
    for pose in ("thumbs_up", "shrug", "hold_card", "wave"):
        before, _ = _scaled(*draw_figure(pose, **kw), scale)
        after = before.copy()
        poses.head_lock(after, idle, face, 0, 0)
        changed = (after != before).any(2)
        assert not changed[body:].any(), f"{pose}: {int(changed[body:].sum())} cells of the body changed"


@pytest.mark.parametrize("scale", [1, 2], ids=["1x", "2x"])
@pytest.mark.parametrize("rows", [6, 10, 14], ids=["6 rows", "10 rows", "14 rows"])
@pytest.mark.parametrize("shirt", [None, (240, 240, 240), (176, 44, 44)], ids=["blue", "white", "red"])
def test_a_short_top_right_under_the_chin_is_not_a_beard(shirt, rows, scale):
    """A top with no neck above it that ends inside the rows searched for a beard is still clothing, because the
    mouth is drawn in the skin above it: the head ends at the chin."""
    kw = dict(neck=0, top_rows=rows)
    if shirt is not None:
        kw["shirt"] = shirt
    rgba, drawn = _scaled(*draw_figure("idle", **kw), scale)

    face = faces.locate(rgba)

    assert _close(face.left_eye, drawn["left_eye"]) and _close(face.right_eye, drawn["right_eye"])
    assert _close(face.mouth, drawn["mouth"]), (face.mouth, drawn["mouth"])
    chin = drawn["face"][3]
    assert chin < face.neck <= chin + 1 + 2 * scale, (face.neck, chin)


def test_the_board_mentions_a_beard_only_when_one_was_found_or_dropped():
    """A beardless character gets no beard advice. The note shows when a beard was measured, and says what to mark
    when one was found but its end was not."""
    details = {}
    face, _ = faces.measure(draw_figure("idle")[0], None, details)
    assert face is not None and details["beard"] is None
    assert not any("beard" in line for line in faces.board_notes(face, marked=False, beard=details["beard"]))

    face, _ = faces.measure(draw_figure("idle", hair=BEARD, beard=BEARD)[0], None, details)
    assert details["beard"] == "found"
    found = [line for line in faces.board_notes(face, marked=False, beard="found") if "beard" in line]
    assert found and all("--neck" in line or "neck" in line for line in found)

    face, _ = faces.measure(draw_figure("idle", hair=BEARD, beard=BEARD, beard_length=12)[0], None, details)
    assert face is not None and details["beard"] == "dropped"
    assert _close(face.mouth, draw_figure("idle", hair=BEARD, beard=BEARD, beard_length=12)[1]["mouth"]), (
        "the mouth inside a beard whose end was not found is still measured"
    )
    dropped = [line for line in faces.board_notes(face, marked=False, beard="dropped") if "beard" in line]
    assert dropped and any("mark" in line and "neck" in line for line in dropped)


# --- marking the face by hand -------------------------------------------------------------------------------------------


def test_marks_replace_the_measured_eyes_and_mouth():
    rgba, drawn = draw_figure("idle")
    face = faces.locate(rgba)
    eyes, mouth = faces.parse_box("46,55,30,8"), faces.parse_box("54,68,12,4")

    marked = faces.apply_marks(rgba, face, {"eyes": [eyes.x, eyes.y, eyes.w, eyes.h], "mouth": [mouth.x, mouth.y, mouth.w, mouth.h]})

    assert marked.mouth == mouth
    for eye, half in ((marked.left_eye, (46, 61)), (marked.right_eye, (61, 76))):
        assert half[0] <= eye.x and eye.x1 <= half[1] and eyes.y <= eye.y and eye.y1 <= eyes.y1
    assert _close(marked.left_eye, drawn["left_eye"]) and _close(marked.right_eye, drawn["right_eye"]), (
        "each half of the marked box is tightened to the eye drawn in it"
    )


def test_a_face_that_cannot_be_measured_can_be_marked_by_hand():
    rgba, _ = draw_figure("back")  # the back of the head: there is no face to measure
    face, reason = faces.measure(rgba, None)
    assert face is None and "could not be measured" in reason

    face, reason = faces.measure(rgba, {"eyes": [46, 55, 30, 8]})
    assert face is None and "Mark both the eyes and the mouth" in reason

    face, _ = faces.measure(rgba, {"eyes": [46, 55, 30, 8], "mouth": [54, 68, 12, 4]})
    assert face is not None and face.mouth == faces.Box(54, 68, 12, 4)
    states = faces.face_states(rgba, face)
    assert set(states.patches) == {"mouth_closed", "mouth_mid", "mouth_open", "eyes_open", "eyes_closed"}

    face, _ = faces.measure(rgba, {"eyes": [46, 55, 30, 8], "mouth": [54, 68, 12, 4], "neck": 80})
    assert face.neck == 80


def test_a_marked_neck_row_replaces_the_measured_one():
    rgba, drawn = draw_figure("idle", neck=3)
    face = faces.locate(rgba)

    marked = faces.apply_marks(rgba, face, {"neck": face.neck + 3})

    assert marked.neck == face.neck + 3
    assert (marked.left_eye, marked.right_eye, marked.mouth) == (face.left_eye, face.right_eye, face.mouth)
    assert faces.apply_marks(rgba, face, {"neck": face.mouth.y}).neck == face.mouth.y1, "the head always holds the mouth"


@pytest.mark.parametrize("text", ["1,2,3", "a,b,c,d", "1,2,0,4", "", "1.5,2,3,4"])
def test_a_box_is_four_whole_numbers(text):
    with pytest.raises(faces.KitError) as caught:
        faces.parse_box(text)
    assert "x,y,w,h" in str(caught.value) or "width and a height" in str(caught.value)
