"""The character commands the `character` skill runs. A skill cannot call Python directly.

    python -m kit.character status
    python -m kit.character start --description TEXT --outfit TEXT [--photos FILE ...] [--colours N] [--tool NAME]
    python -m kit.character describe [--description TEXT] [--outfit TEXT]
    python -m kit.character sheet [--resnap]
    python -m kit.character faces [--eyes x,y,w,h] [--mouth x,y,w,h] [--neck ROW] [--clear] [--open ROWS]
    python -m kit.character approve sheet
    python -m kit.character poses [NAME ...] [--resnap]
    python -m kit.character approve poses NAME ... | all
    python -m kit.character redraw NAME [--move] [--note TEXT]
    python -m kit.character moves [NAME ...] [--resnap]
    python -m kit.character approve moves NAME ... | all
    python -m kit.character pack

A description, an outfit or a note given as - is read from standard input, so words with an apostrophe
("I'm", "O'Neill") need no shell quoting: pass them in a here-document.

Every command works on me/character/ (KIT_ME moves the me folder). It prints plain sentences and exits
with 0 when the step is done, 1 when something is wrong, and 3 when it is waiting for a picture the
creator has to make by hand with the manual tool.
"""

from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

from kit import config
from kit.character import providers, store
from kit.config import KitError

WAITING = 3
MISSING_GROUP = "Run: uv sync --group character --inexact to install the packages the character steps need."
GROUP_MODULES = ("numpy", "PIL", "scipy", "skimage", "sklearn")
# The first command after the install loads the drawing packages while the Mac checks every new file, which can
# take a minute with nothing on the screen. A load slower than this says so. A warm load takes a few seconds.
SLOW_LOAD_SECONDS = 10.0
SLOW_LOAD_NOTE = "Loading the drawing tools. The first run after an install can take a minute."


def _import_tools() -> None:
    """Import the character steps, which bring numpy, Pillow, scipy and scikit-image with them."""
    from kit.character import faces, moves, poses, sheet  # noqa: F401


def _load_tools() -> None:
    """Load the drawing tools before the command runs, and say so when that is slow."""
    timer = threading.Timer(SLOW_LOAD_SECONDS, lambda: print(SLOW_LOAD_NOTE, file=sys.stderr, flush=True))
    timer.daemon = True
    timer.start()
    try:
        _import_tools()
    finally:
        timer.cancel()


def _preferred(folder: Path) -> str | None:
    """The image tool to use: the one this character was started with, else the profile's choice."""
    if store.exists(folder):
        tool = store.load(folder).get("tool")
        if tool:
            return tool
    try:
        return config.load_profile().image_provider
    except KitError:
        return None


def _tool(folder: Path) -> providers.Provider:
    choice = providers.choose(_preferred(folder), folder=folder)
    if choice.message:
        print(choice.message)
    return choice.provider


def _shown(path: Path) -> str:
    try:
        return str(Path(path).relative_to(config.me_dir().parent))
    except ValueError:
        return str(path)


def _words(value: str | None, *, stdin_used: list[bool]) -> str | None:
    """The value as given, or standard input when it is -. Only one value per command can come from there."""
    if value != "-":
        return value
    if stdin_used[0]:
        raise KitError("Only one of the description, the outfit and the note can be read from standard input at a time.")
    stdin_used[0] = True
    return sys.stdin.read().strip()


def _waiting(waits) -> int:
    for wait in waits:
        print(wait)
    return WAITING


def _face_lines(folder: Path, note: str | None = None) -> None:
    from kit.character import faces

    board = folder / faces.BOARD
    if note:
        print(note)
    elif board.is_file():
        print(f"Check the face here before approving: {_shown(board)}. The boxes must sit on the eyes and the mouth, "
              "and the neck line just under the chin.")  # fmt: skip


def _card_line(manifest: Path) -> str:
    """What the pack found on the hold_card pose: the clear face of the card it holds up, which a video writes on."""
    import json

    data = json.loads(Path(manifest).read_text(encoding="utf-8"))
    card = data["poses"]["hold_card"]["frame_info"][0].get("card")
    if card:
        return f"The hold_card pose holds a card {card[2]} x {card[3]} cells, which a video can write its words on."
    return ("No plain card was found on the hold_card pose, so a video cannot write on it: a card on that page drops in "
            "beside the character instead. For words on the card, draw hold_card again with a large plain white card, "
            "then pack again.")


def _pack_again(folder: Path) -> None:
    """Pack again after the face changed. The pack holds each face patch's size and its place on every drawing, so
    an older pack would draw the new face stretched and out of place."""
    from kit.character import pack

    try:
        manifest = pack(folder)
    except KitError as problem:
        print(f"The packed character still has the old face, and it cannot be packed again yet. {problem} "
              "Then run: uv run python -m kit.character pack")  # fmt: skip
        return
    print(f"Packed the character again, so the videos use the new face: {_shown(manifest)}")
    print(_card_line(manifest))


def _status(folder: Path) -> int:
    from kit.character.poses import MOVE_NAMES, POSE_NAMES

    choice = providers.choose(_preferred(folder), folder=folder)
    print(choice.message or f"Image tool: {choice.name}.")
    if not store.exists(folder):
        print("There is no character yet. The first step is a description or 3 to 5 photos, then: start.")
        return 0
    state = store.load(folder)
    print(f"Character folder: {_shown(folder)}. Image calls so far: {state.get('calls', 0)}.")
    sheet = state.get("sheet", {})
    if not sheet.get("round"):
        print("Next: draw the sheet.")
        return 0
    marked = " (marked by hand)" if state.get("face_marks") else ""
    if not sheet.get("approved"):
        print(f"The sheet (round {sheet['round']}) is waiting for approval: {_shown(folder / 'sheet' / 'board.png')}.")
        if sheet.get("face_note"):
            print(f"The face is not ready: {sheet['face_note']}")
        else:
            print(f"Face check{marked}: {_shown(folder / 'faces' / 'board.png')}.")
        return 0
    approved = [n for n in POSE_NAMES if state["poses"].get(n, {}).get("approved")]
    drawn = [n for n in POSE_NAMES if state["poses"].get(n, {}).get("round")]
    print(f"Poses: {len(drawn)} of {len(POSE_NAMES)} drawn, {len(approved)} approved (idle comes with the sheet).")
    redraws = [n for n in POSE_NAMES if state["poses"].get(n, {}).get("redraw")]
    if redraws:
        print("To draw again: " + ", ".join(redraws) + ".")
    built = [m for m in MOVE_NAMES if state["moves"].get(m, {}).get("built")]
    print(f"Face states: {'built' if state.get('faces', {}).get('built') else 'not built'}{marked}. "
          f"In-betweens: {len(built)} of {len(MOVE_NAMES)} moves.")  # fmt: skip
    if (folder / "manifest.json").is_file():
        print(f"Packed: {_shown(folder / 'manifest.json')}.")
    return 0


def _run(args: argparse.Namespace) -> int:
    from kit.character import faces, moves, poses, sheet

    folder = store.character_dir()
    stdin_used = [False]
    if args.command == "status":
        return _status(folder)
    if args.command == "start":
        state = sheet.start(folder, description=_words(args.description, stdin_used=stdin_used),
                            outfit=_words(args.outfit, stdin_used=stdin_used), photos=args.photos or (),
                            colours=args.colours, tool=args.tool)  # fmt: skip
        photos = f" and {len(state['photos'])} photos" if state["photos"] else ""
        print(f"Started the character from the description{photos}. Next: draw the sheet.")
        return 0
    if args.command == "describe":
        sheet.describe(folder, description=_words(args.description, stdin_used=stdin_used),
                       outfit=_words(args.outfit, stdin_used=stdin_used))  # fmt: skip
        print("Updated the description. Draw the sheet again to see the change.")
        return 0
    if args.command == "sheet":
        if args.resnap:
            board = sheet.make(folder, None, redraw=False)
            print(f"The last sheet picture is snapped again. Look at it here: {_shown(board)}")
        else:
            try:
                board = sheet.make(folder, _tool(folder))
            except providers.WaitingForImage as wait:
                return _waiting([wait])
            print(f"The sheet is drawn. Look at it here: {_shown(board)}")
        _face_lines(folder, store.load(folder)["sheet"].get("face_note"))
        return 0
    if args.command == "faces":
        board = faces.make(folder, eyes=faces.parse_box(args.eyes) if args.eyes else None,
                           mouth=faces.parse_box(args.mouth) if args.mouth else None, neck=args.neck,
                           clear=args.clear, open_rows=args.open)  # fmt: skip
        state = store.load(folder)
        state["sheet"]["face_note"] = None
        store.save(folder, state)
        how = "marked by hand" if state.get("face_marks") else "as measured"
        print(f"The face is ready ({how}). Look at the boxes, the neck line and the face states here: {_shown(board)}")
        if (folder / "manifest.json").is_file():
            _pack_again(folder)
        return 0
    if args.command == "approve":
        if args.what == "sheet":
            summary = sheet.approve(folder)
            print(f"Approved the sheet. The palette is locked at {summary['palette']} colours, and the idle pose is "
                  "approved with it.")  # fmt: skip
            print(f"Check the face before drawing the poses: {_shown(Path(summary['face_board']))}")
        elif args.what == "poses":
            names = poses.approve(folder, args.names or ["all"])
            print("Approved: " + ", ".join(names) + ".")
        else:
            names = moves.approve(folder, args.names or ["all"])
            print("Approved: " + ", ".join(names) + ".")
        return 0
    if args.command == "poses":
        if args.resnap:
            result = poses.make(folder, None, args.names or None, redraw=False)
        else:
            result = poses.make(folder, _tool(folder), args.names or None)
        return _report(result, "poses")
    if args.command == "redraw":
        note = _words(args.note, stdin_used=stdin_used)
        if args.move:
            if args.name not in poses.MOVE_NAMES:
                raise KitError(
                    f"{args.name} has no in-betweens: only {', '.join(poses.MOVE_NAMES)} have them. "
                    f"To draw the {args.name} pose again, leave out --move."
                )
            moves.redraw(folder, args.name, note)
            print(f"{args.name}'s in-betweens will be drawn again. Run: moves")
        else:
            poses.redraw(folder, args.name, note)
            print(f"{args.name} will be drawn again on its own. Run: poses")
        return 0
    if args.command == "moves":
        if args.resnap:
            result = moves.make(folder, None, args.names or None, redraw=False)
        else:
            result = moves.make(folder, _tool(folder), args.names or None)
        return _report(result, "moves")
    if args.command == "pack":
        from kit.character import pack

        manifest = pack(folder)
        print(f"Packed the character: {_shown(manifest)}")
        print(_card_line(manifest))
        return 0
    raise AssertionError(args.command)


def _report(result, what: str) -> int:
    if result.done:
        print("Drawn: " + ", ".join(result.done) + ".")
    for note in result.notes:
        print(note)
    if result.board is not None and result.board.is_file():
        print(f"Look at them here: {_shown(result.board)}")
    if what == "moves" and result.done:
        print("Each move has a preview in its folder under moves/.")
    if result.waiting:
        return _waiting(result.waiting)
    if not result.done and not result.notes:
        print(f"Nothing to draw: every one of the {what} is drawn.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m kit.character", description="Build the mini me character.")
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")
    commands.add_parser("status", help="say where the character is and which image tool will draw")
    one = commands.add_parser("start", help="begin a character from a description and, optionally, photos")
    one.add_argument("--description", required=True, help="the creator's words, or - to read them from standard input")
    one.add_argument("--outfit", required=True, help="what the character wears, or - for standard input")
    one.add_argument("--photos", nargs="*", type=Path)
    one.add_argument("--colours", type=int, default=32)
    one.add_argument("--tool", choices=providers.NAMES)
    one = commands.add_parser("describe", help="change the description before the sheet is drawn again")
    one.add_argument("--description", help="the new words, or - for standard input")
    one.add_argument("--outfit", help="the new outfit, or - for standard input")
    one = commands.add_parser("sheet", help="draw the character sheet, or draw it again")
    one.add_argument("--resnap", action="store_true", help="snap the last sheet picture again, with no image call")
    one = commands.add_parser("faces", help="measure the face again, or mark the eyes, the mouth or the neck row by hand")
    one.add_argument("--eyes", help="one box round both eyes, x,y,w,h on the face board's grid")
    one.add_argument("--mouth", help="the box round the mouth, x,y,w,h on the face board's grid")
    one.add_argument("--neck", type=int, help="the first row under the chin, read off the face board's grid")
    one.add_argument("--clear", action="store_true", help="drop the marks and measure the face again")
    one.add_argument("--open", type=int, help="how many rows the open mouth drops (1 to 4)")
    one = commands.add_parser("approve", help="approve the sheet, poses or moves")
    one.add_argument("what", choices=("sheet", "poses", "moves"))
    one.add_argument("names", nargs="*")
    one = commands.add_parser("poses", help="draw the poses that need drawing")
    one.add_argument("names", nargs="*")
    one.add_argument("--resnap", action="store_true", help="snap the pictures already drawn again, with no image call")
    one = commands.add_parser("redraw", help="mark a pose (or with --move, a move) to be drawn again")
    one.add_argument("name")
    one.add_argument("--note", help="what to change, in the creator's words, or - for standard input")
    one.add_argument("--move", action="store_true", help="draw the move's in-betweens again, not the pose")
    one = commands.add_parser("moves", help="draw the in-betweens for talking and the points")
    one.add_argument("names", nargs="*")
    one.add_argument("--resnap", action="store_true", help="snap the strips already drawn again, with no image call")
    commands.add_parser("pack", help="pack the character for the runtime")
    args = parser.parse_args(argv)
    try:
        _load_tools()
        return _run(args)
    except ModuleNotFoundError as problem:
        if (problem.name or "").split(".")[0] in GROUP_MODULES:
            print(MISSING_GROUP, file=sys.stderr)
            return 1
        raise
    except KitError as problem:
        print(problem, file=sys.stderr)
    except OSError as problem:
        print(f"A file could not be used ({problem.strerror or type(problem).__name__}).", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
