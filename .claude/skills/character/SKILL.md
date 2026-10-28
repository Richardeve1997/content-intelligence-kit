---
name: character
description: Build the creator's pixel character for the mini me style from 3 to 5 photos of themselves or a written description. An image tool draws a character sheet the creator approves, then 13 poses they approve one by one (any of them drawn again on request); the kit adds mouth and blink states and in-between drawings, then packs the character for the mini me videos. Use when the creator says "make my character", "build my mini me", "draw me as a pixel character", "redraw the shrug", "the hair on my character is wrong" or "change my character's outfit", or when onboarding or the mini-me skill needs a character that is not built yet.
---

# character

The character lives in `me/character/`. When it is done it has 14 core poses on one shared canvas (245 x 284 art pixels, feet on row 280): idle, talking, point_left, point_right, point_up, thumbs_up, shrug, thinking, mind_blown, celebrate, hold_card, typing, facepalm and wave. Talking and the three points also get in-between drawings, so they move smoothly. The mouth opens and closes and the eyes blink through small patches cut from the creator's own face. The pack step writes `me/character/manifest.json`, which the mini me videos read through `window.Character` in `.claude/skills/mini-me/kit/character/character.js`.

The image tool draws, and the kit does the rest. It snaps each picture onto a true pixel grid, locks the colours of the approved sheet, stands every pose on the same feet and copies the approved face onto every pose that looks at the viewer, so the character stays the same from pose to pose. When nothing needs drawing again, a character takes 9 image calls: one sheet (which also gives the idle pose), four pose sheets for the other 13 poses, and four sets of in-betweens. Each redraw adds one call.

## Before you start

The character steps need the `character` dependency group. Check that at least 5 GB of disk is free, then run `uv sync --group character --inexact`. A bare `uv sync` removes the group again, and without `--inexact` the sync removes groups installed earlier, so always write both.

The creator picked an image tool during setup (`image_provider` in `me/profile.json`):

- `manual`, the default and free: the kit writes each prompt to a file. They paste it into ChatGPT or Gemini, attach the pictures it lists, and save the picture they get back into `me/character/inbox/`.
- `codex`: a signed-in Codex CLI draws each picture with its image tool, on their ChatGPT plan. If Codex answers that the model is not supported on their plan, set `CODEX_IMAGE_MODEL` in `.env` to a model their Codex offers, or to `default` to use the model their own Codex settings name.
- `openai` or `gemini`: the provider's image API, with `OPENAI_API_KEY` or `GEMINI_API_KEY` in `.env`. Each picture is billed by the provider.

## Steps

1. Read the creator's rules for this skill with `kit.rules.read("character")`. In a shell: `uv run python -m kit.rules read character`. Follow them.
2. See where things stand: `uv run python -m kit.character status`. It names the image tool it will use and how far the character has got. If it prints "No image tool found, so I'll give you the prompts to paste into ChatGPT or Gemini.", tell the creator exactly that and carry on with the manual tool. If a character already exists, pick up at the step it names.
3. Collect what the character is drawn from, one question at a time:
   - Photos, if they want a likeness: 3 to 5 photos of themselves only, face clearly visible, from a few angles. Never use a photo of anyone else. Tell them the photos go to the image tool they chose and are kept in `me/character/photos/`. With the manual tool, copies also sit in `me/character/inbox/` for attaching, and the kit deletes them once the picture is back.
   - A description in their own words: hair, skin tone, glasses, build, roughly how old they look, and anything else that makes it them. Without photos, this is all the image tool has, so ask for a little more detail.
   - The outfit: what the character wears in every video.

   Then start the character. Use single quotes so their words stay as typed:

   ```bash
   uv run python -m kit.character start --description '<their words>' --outfit '<the outfit>' --photos <photo files>
   ```

   Leave out `--photos` when they gave none. `--colours` (8 to 64, default 32) caps the palette, and `--tool` overrides the profile's image tool for this character. Words with an apostrophe in them ("I'm", "O'Neill") break single quotes, so give that one value as `-` and pass the words on standard input:

   ```bash
   uv run python -m kit.character start --outfit '<the outfit>' --description - <<'WORDS'
   <their words, apostrophes and all>
   WORDS
   ```

   The same works for `describe` and for a redraw `--note`, one value per command. When the description and the outfit both have an apostrophe, give `start` the description on standard input and a stand-in outfit, then put the real outfit in with `describe`, also on standard input, before the sheet is drawn:

   ```bash
   uv run python -m kit.character start --outfit 'to follow' --description - <<'WORDS'
   <their words, apostrophes and all>
   WORDS
   uv run python -m kit.character describe --outfit - <<'WORDS'
   <the outfit, apostrophes and all>
   WORDS
   ```
4. Draw the sheet: `uv run python -m kit.character sheet`. It writes two boards, and you look at both yourself before you show them:
   - `me/character/sheet/board.png`: the picture the tool drew, the front view snapped to pixels on a light and a dark background, the whole sheet, and the palette. Ask whether it looks like them. When they want changes, put the changes into their description or outfit with `uv run python -m kit.character describe --description '<new words>'` (or `--outfit`), then run `sheet` again. It draws from the last sheet and asks the image tool to change only what the description changed.
   - `me/character/faces/board.png`, the face check: the head on a numbered grid with a box round the eyes, a box round the mouth and a line where the head ends, beside the mouth closed, half open and open, and a blink, then the same four states at the scale the end of a video shows them. These are what will move in every video, so check them now, at the size they will be seen, before any pose is paid for.

   When a box sits in the wrong place, or the mouth or the blink looks wrong on that board, mark the face by hand. Read the boxes off the grid as x,y,w,h (left, top, width and height, in the grid's numbers; one box goes round both eyes) and run `uv run python -m kit.character faces --eyes x,y,w,h --mouth x,y,w,h`. The neck line should sit just under the chin, where the neck or a collar starts: everything above it is the head that the kit copies onto every pose. When it sits anywhere else (on the top, on the shoulders or across the face), read the first row under the chin off the grid and run `uv run python -m kit.character faces --neck <row>`. A beard that hangs well below the chin is not measured, so for one, mark the eyes, the mouth and the neck by hand. Each mark can be given alone. The marks are kept in the character record and every later step uses them; `uv run python -m kit.character faces --clear` drops them. When the sheet step says the face could not be measured, draw the sheet again with the whole face in view (a smaller hat or none, no hair over the eyes), or mark both boxes by hand the same way.
5. When they are happy with both boards, approve the sheet: `uv run python -m kit.character approve sheet`. This locks the palette, stands the front view on the canvas as the idle pose and builds the face states from the face as measured or marked. The idle pose is approved with the sheet, so 13 poses are left to draw, 14 in all. A sheet whose face is neither measured nor marked is not approved, and the message says what to do.
6. Draw the other 13 poses: `uv run python -m kit.character poses`. Open `me/character/poses/board.png`, look at it, and show it. Ask which poses to keep.
   - Approve the good ones by name, for example `uv run python -m kit.character approve poses talking wave thumbs_up`, or all of them with `approve poses all`.
   - For each one they reject, ask what to change and mark it: `uv run python -m kit.character redraw shrug --note 'Raise both hands higher.'`. Then run `poses` again. Only the marked poses are drawn, up to three to a call, each with its note, and the others stay as they are.
   - The board marks a pose "own face" when its head could not be matched to the front view. That pose keeps its own face, and its mouth does not move. It marks a pose "cut off" when it did not fit the canvas. Offer to draw either again.

   Repeat until all 14 poses are approved.
7. The face states were built when the sheet was approved. If the open mouth drops too far or not far enough, run `uv run python -m kit.character faces --open 2` (or `--open 4`) and show `me/character/faces/board.png` again.
8. Draw the in-betweens: `uv run python -m kit.character moves`. Each move gets a preview at `me/character/moves/<move>/<move>.gif`. Show them. To draw one again: `uv run python -m kit.character redraw point_left --move --note '<what to change>'`, then `moves` again. Approve them with `uv run python -m kit.character approve moves all`. If a pose with in-betweens is drawn again later, `moves` draws its in-betweens again too.
9. Pack the character: `uv run python -m kit.character pack`. The pack also measures the plain card the `hold_card` pose holds up, so a mini me video can write its words on it, and says what it found. When it found no card, a mini me page in that pose drops its card in beside the character; for words on the card, draw `hold_card` again with a large plain white card and pack again. Then check that the runtime reads it: `node .claude/skills/mini-me/kit/character/test_state.mjs me/character/manifest.json` must end with "all passed". If it fails, show the failing lines and fix the cause before the character is used in a video.
10. Tell the creator in a line or two what was made and how many image calls it took. `status` prints the count.

## Snapping again for free

The pictures the image tool drew stay in `me/character/raw/`. The sheet, any pose or any move can be snapped again from its picture with no image call: `uv run python -m kit.character sheet --resnap` (only before the sheet is approved), `uv run python -m kit.character poses --resnap shrug wave`, or `uv run python -m kit.character moves --resnap point_up`. Leave out the names to snap every pose or move again. Use it after a kit update that improves the snapping, or when a result looks wrong but the picture behind it looks right. A pose or move snapped again waits for approval again.

## With the manual tool

A drawing command that is waiting for a picture exits with status 3 and prints, for each picture, the prompt file in `me/character/inbox/`, the pictures to attach in order, and the name to save the result under. Copies of the pictures to attach wait in the inbox beside the prompt as `<name>.attach-1.png`, `<name>.attach-2.jpg` and so on, each keeping its own file type. For a sheet drawn from photos, those copies are the creator's photos. The kit deletes the copies once the picture is back. Pass that on in plain words: paste the prompt into ChatGPT or Gemini, attach the pictures in that order, download the picture it makes and save it with that name in `me/character/inbox/` (a .png, .jpg or .webp file is fine). When they say done, run the same command again. For the poses there are four prompts at once, and they can do all four before saying done.

## Things to keep

- The photos stay in `me/character/photos/`. The kit's only other copies are the manual tool's attachments in `me/character/inbox/`, which it deletes once the picture is back. Never copy the photos anywhere else or send them to any tool other than the one the creator chose.
- Never edit the files in `me/character/` by hand. Redraw, approve and pack again instead.
- The canvas size, the feet row and the 14 pose names are what the mini me style builds on. Do not rename or drop a pose.
- Prompts live in `kit/character/prompts/` and only fill in the creator's own words. The creator's changes go into their description, their outfit or a redraw note, never into the prompt files.

## Feedback

When the creator corrects how this skill behaves, fix what is in front of them first, then record the lasting part with `kit.rules.add("character", "<rule>")`. In a shell: `uv run python -m kit.rules add character '<rule>'`, with single quotes so `$` and backticks stay as typed. Write one or two plain sentences, 300 characters at most. If the rule contradicts an older one for this skill, find the older one with `uv run python -m kit.rules read character --numbered` and remove it first with `uv run python -m kit.rules remove character <number>`. Say in one line what you recorded. A change to how the character looks (the hair, a pose, the outfit) is not a rule: it goes into a redraw.
