---
name: ui-story
description: Plan and edit a talking-head Reel in the UI story style. The plan step writes talking points and a film guide in the creator's voice from a chosen idea. The edit step turns their raw take into a finished vertical Reel. The real thing they talk about builds up on a light grid above them, their head breaking out of a rounded picture-in-picture, with full-screen moments and punch-ins, karaoke captions, motion graphics in their accent colour and a closer with their call to action. It runs at 1.15x with a music bed and normalised loudness, then the gates and the review loop check it. Use when the creator says "plan the video for number 2", "plan this idea", "write my talking points", "here's my raw take, edit it", "edit this take", "edit my video" or "do the UI story edit", or drops a filmed take into work/. It needs Claude Max (or a paid ChatGPT plan in Codex), because an edit is a long run.
---

# UI story

The creator talks to camera. This skill plans what they say, then edits the take into a vertical Reel with three layouts. In split, the real thing they talk about builds up on a light grid above them while they sit in a rounded picture-in-picture (PIP) below, their head breaking out above its top edge. Canvas is the grid alone, for a diagram or a list. Face is the creator full screen, for their opinions. The look comes from their brand tokens, so no colour, font or handle is fixed in the kit.

It needs Claude Max, or a paid ChatGPT plan when it runs in Codex. An edit is a long run with several renders.

Everything for one video lives in `work/<video>/`:

| Path | What it holds |
|---|---|
| `idea.json` | the idea, one entry from a research run |
| `plan.md` | the talking points and the film guide |
| `take/` | links to the raw take, never copies |
| `build/` | everything made on the way: base, words, matte, layout, graphics, composite |
| `final.mp4` | the finished Reel |
| `review/` | the review sheet, the open strip and the safe-area frames |

## Steps

1. Read the creator's rules for this skill with `kit.rules.read("ui-story")`. In a shell: `uv run python -m kit.rules read ui-story`. Follow them; they win over anything below.
2. Work out which part they want. "Plan the video for number 2" is the plan. "Here's my raw take, edit it" is the edit. When `plan.md` exists and `take/` holds a file, it is the edit.
3. For the plan, follow "The plan". Stop when `plan.md` is written and shown to the creator: filming is theirs.
4. For the edit, follow "The look" and "The edit", in order. Run the checks in "Before handing over", then the `review` skill. Fix what it finds and review again, at most 5 cycles.
5. Give the creator the path to `work/<video>/final.mp4` and say what the review found.

## The plan

1. Save the idea and print the brief: `uv run python .claude/skills/ui-story/kit/plan_brief.py --number 2` (it copies idea 2 of the latest research run to `work/<video>/idea.json` and creates `take/`). For a video that already has `idea.json`, pass its name instead: `plan_brief.py <video>`. The brief holds the idea, the audience, the call to action and its closing line, the creator's rules and their voice model, `me/voice/VOICE.md`.
2. Write the talking points fresh, in the creator's voice as `VOICE.md` describes it. They are points, not a script: the creator says them in their own words.
   - The first point is the hook. Say the problem or the result plainly in the first sentence, so a viewer knows the topic in three seconds.
   - One idea per point, in the order they will be said. Six to ten points fit a 45 to 75 second Reel.
   - Mark the points where something real can be shown (the product, a page, a number with a source). The style needs a real thing to show on most beats.
   - The last point is the close. Start from the closing line in the brief, which comes from the creator's call to action (`profile.cta`): "Comment KEYWORD and I'll send you ..." for a comment CTA, a follow otherwise. Say it the creator's way.
3. Write the film guide: the standard notes below, plus anything this video needs (a prop, a screen to show, a second location).
   - Film vertical, in 4K if the phone can, at 30 or 60 fps. Lock the phone on a stand.
   - Turn HDR video off first. On an iPhone: Settings, Camera, Record Video, HDR Video off (if Camera is not in the main list, look under Apps). An HDR take can come out washed out, and the HDR row of the machine check says whether this Mac can fix that.
   - Face a window or a soft light. Avoid a bright window behind you.
   - Frame from the chest up, with a hand's width of room above your head. Your head breaks out of the picture-in-picture, and the cards sit above it.
   - Say each point, then pause for a breath. The pauses are where the cuts go.
   - If you stumble, pause, then say the whole sentence again. The edit keeps the clean one.
   - A natural sign-off at the end can stay in.
4. Put the plan through the `humanizer` skill in embedded mode. Keep every fact, number, link and the call-to-action wording. Use no em or en dashes.
5. Save `work/<video>/plan.md` in the shape the brief prints (talking points, film guide, on screen) and show it to the creator. Tell them to film it and say "here's my raw take, edit it". Link the take into `take/` with `ln -s` when they give a path. Never copy, move or delete a raw take.

## The look

These are the house rules of the style. They are what make it work, so keep them unless the creator's rules say otherwise.

### Does the video fit

The style needs a real thing to show on most beats. Pick the split of layouts by the kind of video:

| Kind of video | How the style maps | Split / face / canvas share |
|---|---|---|
| A product, tool or repo | a motion graphic of the problem, a proof card, the product card; then its real UI and footage of it in use | 55 / 25 / 20 |
| A news roundup | one recurring timeline that lights a new date per item; each launch's real visual | 50 / 25 / 25 |
| An idea led by a person | their real post or talk first, then the idea as real objects and screens | 50 / 30 / 20 |
| An opinion with no product | more face; the visuals are the receipts the words name (a post, a chart, a paper) | 40 / 45 / 15 |
| A numbered list | one recurring grid, a different item lit each time it returns | 55 / 20 / 25 |

When fewer than about half the beats have something real to show, say so. Lean on face windows with punch-ins and canvas diagrams of the idea, or suggest a mini me video for that idea.

### The three layouts

| Layout | What it is |
|---|---|
| split | The creator's canvas colour with a 2 px grid every 90 px in their accent at 9%. Below it, the rounded, bottom-flush PIP (x 90 to 990, top corners rounded 56, a soft shadow). Their matted head breaks out above the PIP edge (an overhang of about 320 px) and is painted after the graphics, so a card that reaches into that band tucks behind the head. The geometry is solved per take: card ink stays above the predicted crown minus 35 px (`--zone-bottom`). |
| canvas | The grid canvas only. Content sits in x 90 to 990, y 220 to 1480. Use it for diagrams, icon lists and three-step explainers, never for a text card. Fill the zone: a card that leaves a third of it empty reads as unfinished, so scale the card up. |
| face | The creator full screen, the whole filmed frame. It carries 3 to 5 punch-ins (1.10 to 1.18x, eased in 0.35 s, cut back on a word gap or a layout cut) and 1 to 3 slow pushes (1.00 to about 1.06 over 2 to 5 s), cut from the full-resolution take. Nothing goes over the face; at most a small chip or logo in y 190 to 490. A face run longer than about 5 s carries a chip or a punch-in. |

Layouts switch with a hard cut on the start of a phrase. Split is the spine. Face is for opinions, reactions, the problem line, the payoff phrase and every re-hook ("but here's the thing"), each with a punch-in. When the creator introduces the subject ("this is the tool that..."), stay in split so they are on screen.

### The open

The first four seconds make the topic clear with the sound off. Three beats:

1. 0 to about 2.5 s: a motion graphic of the problem or idea of THIS video. Frame 0 is already moving. Almost no text, no logo card. It pictures the hook's own noun (the product's real page, the set of things the hook names) and carries the subject's real mark at its centre.
2. About 2.5 to 3 s: one proof card. A real, sourced fact in about seven words or fewer (a loss, a number, a headline), designed rather than recorded, with its source as one small pill. It backs the first claim the creator says. If no fact makes the idea clearer, skip it and go straight to the subject card.
3. About 4.5 s: the subject card above the creator. Its logo, its name, one plain-language line, and badges with social proof (stars, users), held long enough to read.

The first real footage lands early: by the subject card at the latest, the viewer has seen the real thing (its UI, its site, footage of it in use), not only drawings of it.

Test it: mute the render and look at frames 0.2, 1, 2, 3, 4 and 5 s (the review's `open.jpg`). A stranger has to be able to say what the video is about. The creator's head outranks the opener graphic: leave about 60 px of canvas above their highest crown.

### Pace and reading load

- One idea per visual, and about six words per visual at most, on screen at least 0.35 s per word plus 0.6 s. `audit_dom.py` measures it; keep its table with the sources. A short row is fine only when the speech binds it (two cards on one sentence). Fix a short row by timing first: start the card on the sentence that already names the idea.
- A new visual about every two seconds of the finished Reel, and a build (a row, a tick, a chip, typed text, a cursor click) about every 0.8 s between them. A face window counts as a new visual.
- Nothing in split or canvas holds still for more than 3 s. A card that waits for a later word gets a build meanwhile: a ring walking the rows, a tile filling, a mark popping in. A count is not a waiting build: it climbs in under a second and lands on its word, so its passing values never read as numbers. `check_static.py` fails a still zone.
- A list populates above the creator in split: an icon and one to three words per row, each on its spoken word, held through the sentence. Then cut to face with a punch-in on the payoff phrase.
- When the words promise a few things to wait for ("two things you need to know"), show the list with the names blurred until each one is said. Never blur a source, a number or the call to action.
- Several videos or resources show as a carousel, held through the sentence, cutting to face on the payoff.
- A dense stretch gets a face cut in the middle, then goes back to the graphic a step further on.

### Real things on screen

- Show the real thing. For every product, tool or person named, use the highest step you can source: its own demo or launch video; a screen recording made with `record_page.py`; real screenshots; a mockup rebuilt from its real strings and layout. A named product's UI and marks are its own, never a stand-in. Save each file under `build/sources/` and list its URL, date and what it shows in `build/sources/SOURCES.md`. Then copy the clips and images a scene uses into `build/hf/assets/` and point the scene at the copy: HyperFrames only loads files inside the project folder.
- Screen recordings are for pictures, video and UI flows only: an image, a clip, a panel filling, a click. Never scroll or highlight running text (docs, articles, tables, config files). A text source becomes a designed card instead: a quote card (the sentence, the person's name and role, the company mark), a headline card (headline, outlet, date), a stat card, an icon list, or a logo with a three-step explainer. A recording that needs reading becomes a picture of the outcome it claims.
- The one exception is a prompt. When the creator says "this prompt" or "paste this", show their real prompt as a tall designed card that scrolls smoothly through that sentence. Its label is the read; the body is texture.
- Product footage plays in a `.vidcard` (a rounded app window) with accent motion graphics on top: a chip naming the action on its word, a ring on the UI element, a cursor glide and click, a zoom to the detail that lands on its word and holds. No drifting pans.
- Plain language on screen: "Free and open source" with a star count, not a licence name or a technical metric.
- Every number on screen is the one the creator says; a fact they give no number for shows the sourced one from `SOURCES.md`. Never invent a number, a rank or a quote. A small spoken slip is not corrected on screen.
- One fact shows one number. When the creator says a number, every card that shows that fact uses it, the subject card's badges included, since they come first. A fuller live figure from the source goes in `SOURCES.md`, not on another card.
- No grey credit lines and no "illustrative" tags on screen. Credits and notes go in `SOURCES.md`; a stat may keep one readable source pill.
- When the creator cites their own experience, show it: the real marks of the places they name, with their titles.
- When the creator quotes someone and a recording of that person exists, play the clip above them in split with its voice about 2 dB under theirs, in step word for word. Show what the recording shows, with a name chip that gives the person's real title.

### Building in sync with the words

- Every row, tick, chip, typed title or click lands on the word that names it. Cue scenes with spoken phrases (`UIS.cue`, `layout.json`), never typed seconds.
- Never open a scene empty: its main object is on screen the frame it lands, and its content arrives with it. Items named later wait dimmed and light up on their word. Logos landing in a grid pop up big and settle into their slots from the cut frame.
- A ring or highlight is placed from measured boxes (`record_page.py --mark`), padded about 10 px, and never crops what it points at. Never ring a person. A logo badge sits above any outline, and no chip or logo covers a person's head.
- Connectors join the boxes they connect, and only when the words join them. No decorative dotted lines.
- A callback is fine: the opener graphic or the proof card can return when the words come back to it.

### Captions

Karaoke captions, one line centred at y 1570 in every layout: white type in the creator's display font at weight 600 with a soft ink halo, and the word being spoken re-set in ink on a plate in their canvas colour. Over the canvas the group flips to ink type with the spoken word in white on an ink plate. Groups hold at most 3 words and 18 characters, swap hard, and never use the accent. `build_captions.py` writes them, `karaoke.css` styles them and `UIS.captions(tl)` steps the plate.

### The closer

The last beat shows the creator's call to action, built by `closer.py` from `profile.cta`. A comment CTA types the keyword into a comment box, rings it in the accent and labels it "Comment KEYWORD". A follow CTA shows their profile row and presses Follow. The video is one file for every platform, so a comment CTA shows its keyword everywhere; on platforms where nobody answers comments (the ones not in `cta.comment_platforms`), the post caption for that platform carries a follow line instead. There is no outro card: the Reel ends on the last spoken word, or on a natural sign-off.

### Sound

- Tight cuts: every gap between words over 0.22 s is cut to about 0.15 s (`make_blocks.py`), and `check_trims.py` proves no trim cut speech. Never cut a line the next one leans on: read each join back as a cold listener.
- Hide every take join inside a face window with a layout cut or a punch-in change on the next word, and trim the breath or click in the gap.
- The finish runs at 1.15x, never faster. The bed comes from `kit.music.pick()` (the creator's `me/music/` folders, skipping the last track used) or there is none. It sits at `--music-lufs -24` under the voice with `--mix-peak 0.74` and a 0.3 s fade-in; a denser track starts about 6 dB lower (`--music-lufs -30`). The finished Reel lands at -13 LUFS with the true peak at or below -1.5 dBTP. The kit ships no music and no sound effects.

## The edit

Heavy steps (cutting the base, transcription, matting, the graphics render, the composite, the finish) each take a heavy-job slot from `kit.media`, so at most two run at once, and each checks for 5 GB of free disk first. Run them one after another, and delete test renders when they have been looked at. Link the raw take; never move, copy or delete it.

```bash
K=.claude/skills/ui-story/kit; V=<video>; W=work/$V; B=$W/build

# 1. A first EDL from the raw take, then a clean one
uv run python $K/prep.py $V --edl
#    read $B/raw.lines.txt; delete false starts and repeats from $B/edl.json

# 2. The base, the clean voice, the words, the restart check, the matte and the PIP geometry
uv run python $K/prep.py $V
#    read $B/restarts.log; when it flags a restart, fix the EDL and run prep again; a repeat the creator meant stays ("much, much")
uv run python -m kit.media probe $B/base.mp4          # D, the base duration in seconds

# 3. Layout: write $B/layout.json from $B/base.lines.txt (cues are words as spoken)
uv run python $K/seg_starts.py --base $B/base.mp4 --edl $B/edl.json --out $B/seg_starts.json
uv run python $K/scaffold.py $B/hf <D + 2/60>
uv run python $K/layout.py --layout $B/layout.json --words $B/base.words.json --duration <D> \
  --out $B/layout.resolved.json --hf $B/hf --edl-in $B/edl.json --edl-out $B/edl-zoom.json \
  --seg-starts $B/seg_starts.json
uv run python $K/cut_base.py $W/take/<raw> $B/edl-zoom.json $B/zbase.mp4     # the base with the face zooms
uv run python $K/measure_crowns.py --matte $B/matte.mp4 --layout $B/layout.resolved.json --out $B/geometry.json

# 4. Gap trims and captions
uv run python $K/make_blocks.py $B/clean.wav $B/base.words.json $B/layout.resolved.json $B/blocks.json
uv run python $K/check_trims.py --audio $B/clean.wav --blocks $B/blocks.json
uv run python $K/build_captions.py --words $B/base.words.json --hf $B/hf --layout $B/layout.resolved.json \
  --captions-dir $B/captions --blocks $B/blocks.json --duration <D + 2/60>

# 5. Scenes: write them into $B/hf/index.html (or index.src.html, then fill_times.py)
uv run python $K/closer.py --start <t> --html          # the closer scene; --js prints its timeline lines
npx hyperframes lint $B/hf
uv run python $K/check_scenes.py $B/hf/index.html
uv run python $K/check_cues.py $B/hf/index.html $B/base.words.json
uv run python $K/check_js.py $B/hf/index.html
uv run python $K/seek_parity_gate.py $B/hf/index.html --step 0.5
mkdir -p $B/sources                                   # prep.py makes it too
uv run python $K/audit_dom.py $B/hf/index.html $B/blocks.json $B/base.words.json > $B/sources/reading-load.md

# 6. Look before the heavy render: snapshot, then composite one frame over the real take
npx hyperframes snapshot $B/hf --at 1.0,3.0,5.0 --no-end --describe false -o $B/snaps
uv run python $K/composite_v2.py --base $B/zbase.mp4 --matte $B/matte.mp4 --layout $B/layout.resolved.json \
  --geometry $B/geometry.json --gfx $B/snaps/frame-00-at-1s.png --from 1.0 --out $B/check-1.0.png

# 7. Render the graphics, check the render against the snapshots, then ship
uv run python -m kit.media run -- npx hyperframes render --format webm --crf 10 --fps 60 --workers 1 \
  --output $B/gfx.webm $B/hf
uv run python $K/snap_render_gate.py $B/snaps $B/gfx.webm --out $B/srg
uv run python $K/ship.py $V --test 0 12                # a short window first; look at it
uv run python $K/ship.py $V                            # composite, finish at 1.15x, the speech gate
uv run python $K/postcheck.py $V                       # format, loudness, captions, stills, safe area, sheet
```

`build/layout.json` lists the layout windows in order. Each one starts at its cue minus `lead` (or at `t` seconds) and runs until the next one starts. Cues are phrases as the speech model heard them (copy them from `base.lines.txt`); `nth` picks a later time the phrase was said. Zooms go in face windows only: a punch eases in on its cue and cuts back at `until` (or the end of the window), and a slow push drifts from its cue (or the window start) to the end of the window. Start a window with `"t"` on a take join (from `seg_starts.json`) so no frame of the next take shows in the previous layout. `layout.py --help` prints the same format.

```json
{"lead": 0.12,
 "windows": [
   {"mode": "split", "t": 0},
   {"mode": "face", "at": "one big problem", "zooms": [
     {"kind": "punch", "at": "big problem", "z": 1.14, "until": "you're stuck"},
     {"kind": "slow", "at": "you're stuck", "z": 1.06}]},
   {"mode": "canvas", "at": "four things"},
   {"mode": "split", "t": 14.13}]}
```

Notes on the recipe:

- `--describe false` stops the snapshot from sending frames to an outside vision service.
- Without face zooms, skip the zoomed base: `ship.py` uses `base.mp4` when there is no `zbase.mp4`.
- The geometry in step 3 is measured over the split windows. Put its zone line (`:root{--zone-bottom: Npx}`) in the composition's style block.
- `ship.py` keeps the bed this video already has. The first ship picks one with `kit.music.pick()` (skipping the last track used) and logs it, and the ledger keeps one row per video, so every re-ship sounds the same. Pass `--mood <folder>` to pick from another mood, or `--music none` for no bed.
- The new render becomes `final.mp4` only after it passes the speech gate. At that moment `ship.py` deletes `review/review.json`, because the review passed the previous final and has to run again. A render that fails stays as `build/final.next.mp4` to listen to, and `final.mp4` is left as it was.
- After the composite passes, delete `build/gfx.webm`, the snapshots and the test windows. Keep the base, the zoomed base, the matte and the words until the creator has signed off.

## The kit

Everything is in `.claude/skills/ui-story/kit/`. Run the scripts with `uv run python`; each one answers `--help`.

| File | What it does |
|---|---|
| `plan_brief.py` | saves the chosen idea and prints the brief for the plan |
| `closer.py` | the closer's copy, scene and timeline lines, from `profile.cta` |
| `prep.py` | EDL proposal, base, clean voice, words, restart check, matte and geometry, resumable |
| `propose_edl.py` | a first EDL from the raw take's words: one keep segment per stretch of speech |
| `cut_base.py` | the 1080x1920, 60 fps base from the keep segments, with the opening push-in and the face zooms |
| `light_voice.py` | the light voice chain: high-pass, gentle compression, -13 LUFS |
| `check_restarts.py` | finds hidden false starts that whole-take transcription smooths over |
| `measure_crowns.py` | the crown off the matte, and the PIP geometry for this take |
| `seg_starts.py` | where each keep segment really starts in the base, so zooms land on time |
| `layout.py` | `layout.json` into windows, `lib/layout.js` and the face zooms for `cut_base.py` |
| `scaffold.py` | a HyperFrames project with the creator's tokens and fonts copied in, and GSAP |
| `template.html` | the composition the scaffold starts from |
| `uistory.css` | cards, rows, chips, pills, rings, `.vidcard`, cursor, the closer; every colour from the tokens |
| `uistory.js` | seek-safe GSAP helpers: `cue`, `show`, `pop`, `rise`, `tick`, `chip`, `fill`, `type`, `count`, `light`, `detail`, `cursor`, `click`, `ring`, `captions` |
| `karaoke.css` | the karaoke caption look |
| `build_captions.py` | the karaoke caption track, `lib/words.js` and the transcript the speech gate reads |
| `fill_times.py` | `{{ c("phrase") }}` cue expressions into frame-snapped seconds |
| `make_blocks.py` | the gap trims, with a speech guard |
| `composite_v2.py` | canvas, PIP, head break-out, graphics and voice in one pass; one frame to a PNG in seconds |
| `record_page.py` | a screen recording of a real page, for pictures, video and UI flows, with measured marks |
| `browser.py` | starts the headless browser the page checks use |
| `ship.py` | composite, finish and the speech gate into `final.mp4` |
| `postcheck.py` | the gates on `final.mp4`, and the review sheet |
| `check_scenes.py` | every scene and caption sits directly in `#root` (an unclosed div blanks later scenes) |
| `check_cues.py` | every cue phrase is in the words; ambiguous cues named |
| `check_js.py` | the page loads with no error, the timeline registers and no id repeats |
| `seek_parity_gate.py` | what a snapshot shows, the render shows too |
| `snap_render_gate.py` | the rendered graphics match the snapshots |
| `audit_dom.py` | the reading-load audit, from the real composition |
| `check_trims.py` | no gap trim cuts speech |
| `check_static.py` | no split or canvas zone holds still over 3 s |
| `caption_blink_check.py` | no one-frame hole in the captions |
| `ig_safe_check.py` | frames as a tall phone crops them |
| `test_clip.py` | a 5 to 10 second test clip in the creator's brand, made once during setup: a branded card with karaoke captions over their test take, or over a sample card with a scratch voice |

Shared helpers in `kit/`: `kit.transcribe` (word timings), `kit.matte` (the person matte: Robust Video Matting through torch.hub at run time, GPL-3.0 and never stored in the repo; MediaPipe as the fallback), `kit.finish` (speed, bed, loudness), `kit.qa_captions` (the speech gate), `kit.music` (the creator's beds) and `kit.media` (the encoder, heavy-job slots, free disk, `probe`).

HyperFrames rules for the composition: one `index.html`, a transparent root, `data-duration` set to the base plus two frames, scenes as plain layers switched with `UIS.show`, and only captions and `<video>` elements as `.clip`. Every `<video>` needs an `id`, or the renderer freezes it. Camera moves animate the `.zoomer` wrapper, never the timed video. Local files only.

## Things that go wrong

- Snap every scene time to the frame grid (`UIS.snap`, or `fill_times.py`). A time with four decimals can flip the graphics a frame after the compositor.
- A caption group that ends within about three frames of a layout cut has to hold to the cut, or the captions blink. `build_captions.py --blocks` handles it when the trims are passed in.
- Write an entrance as `gsap.set(el, {opacity: 0})` then `tl.to(el, {opacity: 1}, t)`. A `fromTo()` with opacity only in its from values shows in a snapshot and vanishes in the render. `seek_parity_gate.py` catches it.
- Keep every chip, ring and card inside x 90 to 990 at every moment; fly-ins drop from above, never from outside the safe area.
- A count that climbs is checked at its final width, not its first frame. Labels and badges are 26 px or larger.
- Elements made by script need `position:absolute` in their style.
- When a chip replaces another in a face window, hide the first at least 0.1 s before the second appears.
- The speech model cannot read digits well and can invent words in silence at a take join. Check the words file around numbers and joins, and drop invented words before building the captions. A word the model spells wrong goes in `build/captions/corrections.json` (`{"misheard": "correct"}`); cue phrases still use the spelling as heard.
- A long render with transparency needs `--workers 1`: more workers try to keep every frame on disk first.
- `cut_base.py` writes a new base to a part file (`base.part.mp4`) and swaps it in only when ffmpeg finishes, so a stopped cut leaves the earlier base as it was. Stopping the script does not always stop its ffmpeg: stop that too, and delete the part file it leaves.
- Free disk before a render, not after. HyperFrames keeps a frame cache in the system temp folder (`hyperframes-extract-cache-*`) that is safe to delete when no render is running.

## Before handing over

- [ ] The open reads on mute: frame 0 moving, the problem graphic, the proof card (or none), the subject card by about 4.5 s
- [ ] The reading-load table is in `build/sources/`, every visual about six words or fewer and long enough
- [ ] No screen recording of scrolled or highlighted text; text sources are designed cards
- [ ] Split frames show the rounded PIP with a clean head break-out, no clipped crown, no card ink under the head
- [ ] Face windows carry the punch-ins and slow pushes, sharp, cutting back on a word gap or a layout cut
- [ ] Real footage or real UI on the product beats, with an accent chip, ring or cursor on its word
- [ ] Karaoke captions at y 1570 in every layout, light over the canvas, `caption_blink_check.py` reports 0
- [ ] Tight cuts, `check_trims.py` passes, no pause over about 0.45 s between sentences
- [ ] `check_static.py` passes; no bare face run over about 5 s
- [ ] Every number on screen matches the spoken one, one fact shows one number on every card, and each traces to `build/sources/SOURCES.md`
- [ ] The closer shows the creator's call to action from their profile
- [ ] Nothing meaningful within 90 px of either side (`ig_safe_check.py` frames in `review/safe/`)
- [ ] `ship.py` passed the speech gate; `postcheck.py` passes (1080x1920, 60 fps, -13 LUFS within 1 LU, true peak at or below -1.5 dBTP)
- [ ] On-screen copy written by you went through the humanizer, with no em or en dashes; spoken words, quotes and real UI text are as they were

Then run the `review` skill on `work/<video>/final.mp4`.

## Feedback

When the creator corrects something about a plan or an edit, fix it in the video first. Then record the lasting part with `kit.rules.add("ui-story", "<rule>")`. In a shell, send the rule on standard input, so an apostrophe, `$` or a backtick in it stays as typed:

```bash
uv run python -m kit.rules add ui-story - <<'RULE'
<rule>
RULE
```

Write one or two plain sentences, with no dates and no quotes from the creator. If it contradicts an older rule for this skill, remove the older one first: `uv run python -m kit.rules read ui-story --numbered` shows the numbers, and `uv run python -m kit.rules remove ui-story <number>` removes one. Say in one line what you recorded.
