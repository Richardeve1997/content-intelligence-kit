---
name: mini-me
description: Make a faceless mini me Reel, where the creator's pixel character does the talking and nothing is filmed. It writes the narration in the creator's voice from a research idea, makes the voice-over (their own phone recording by default, or an ElevenLabs voice with their key), turns every spoken phrase into a giant keyword headline on a flat field that flips dark and light, and stages the character big and cropped at the waist with a sticker outline, a new pose every page and receipts and stamps that arrive because of what it does. It ends on a punch-in under the creator's profile card, then renders, finishes and runs the gates and the review loop. Use when the creator says "make number 3 a mini me video", "mini me video", "make a video with my character", "faceless video for number 2", "make it without filming", "turn this idea into a mini me", or drops a voice-over recording into work/. It needs Claude Max (or a paid ChatGPT plan in Codex), because a build is a long run.
---

# Mini me

The creator's pixel character tells the story, so nobody films anything. Each spoken phrase becomes one page: a flat field in the creator's dark or light colour, the phrase's keyword in giant capitals across the top, and the character big at the bottom, cropped at the waist, in a pose picked for what the phrase says. Receipts, stamps and objects arrive because of the pose: it points left and the card slides in from the left. The fields flip on every page with a whoosh, and the words on screen are the headlines, so there is no caption track. The colours, fonts and handle all come from the creator's profile and tokens.

It needs Claude Max, or a paid ChatGPT plan when it runs in Codex. A build writes a composition and renders it.

Everything for one video lives in `work/<video>/`:

| Path | What it holds |
|---|---|
| `idea.json` | the idea, one entry from a research run |
| `script.md` | the narration under `## Narration`, and notes on facts and receipts |
| `take/` | the creator's voice-over recording, when they record it (a link or the file they dropped) |
| `vo.m4a`, `words.json`, `vo.json` | the finished voice-over, every word timed, and how it was made |
| `beats.json` | the pages: one per spoken phrase |
| `build/hf/` | the HyperFrames project; `lib/scenes.js` is this Reel's own scenes |
| `build/` | `picture.mp4` (the render), `hits.json`, `edit.mov`, `mix.json` |
| `final.mp4` | the finished Reel |
| `review/` | the contact sheet, the open and the review's pass marker |

## Steps

1. Read the creator's rules for this skill with `kit.rules.read("mini-me")`. In a shell: `uv run python -m kit.rules read mini-me`. Follow them; they win over anything below.
2. Check the character exists: `me/character/manifest.json`. If it does not, say so in one sentence and run the `character` skill first.
3. Write the narration (see "The narration") into `work/<video>/script.md`. Show it to the creator and wait for their yes, because the voice-over reads it word for word.
4. Make the voice-over (see "The voice-over"). Stop and wait when it asks for a recording.
5. Make the pages and sharpen them (see "The pages").
6. Write the scenes for this Reel in `build/hf/lib/scenes.js` (see "The scenes"), then check, render and finish (see "The build").
7. Run the checks in "Before handing over", then the `review` skill. Fix what it finds and review again, at most 5 cycles.
8. Give the creator the path to `work/<video>/final.mp4` and say what the review found.

## The narration

1. Save the idea and print the brief: `uv run python .claude/skills/mini-me/kit/brief.py --number 3` (it copies idea 3 of the latest research run to `work/<video>/idea.json` and creates `take/`). For a video that already has `idea.json`, pass its name: `brief.py <video>`. The brief holds the idea, the audience, the closing line from the creator's call to action, their rules and their voice model, `me/voice/VOICE.md`.
2. Write the narration fresh, in the creator's voice as `VOICE.md` describes it. It is said word for word, so write it the way they talk.
   - The first sentence is the hook. Say the problem or the result plainly, so a viewer knows the topic in three seconds with the sound off (the first page shows it).
   - Short phrases. Every phrase becomes a page with at most four words on it, so write phrases of three to six words with a comma or a full stop between them.
   - One idea per sentence. About 60 to 110 words, which is 20 to 40 seconds at the style's pace.
   - Name real things: the product, the number, the place. Each fact needs a source; list them under `## Notes` with what each page could show.
   - Write numbers as digits when they matter on screen ("3 of my 5 plants", "21 days"). A number of 20 or more can count up on its page, from 10; 1 to 19 show whole, because counting up to 3 would flash 1 and 2 under the words first, and counting to 12 would flash 10 and 11. A year such as 2024 always shows whole.
   - End on the closing line from the brief. A comment call to action says the keyword ("Comment GROW and I'll send you the checklist."); a follow says "Follow for more." in the creator's words. That line becomes the closing page.
3. Put the narration through the `humanizer` skill in embedded mode. Keep every fact, number, name and the call-to-action wording. Use no em or en dashes.
4. Save `work/<video>/script.md` in the shape the brief prints and show it to the creator.

## The voice-over

`uv run python -m kit.voice auto <video>` makes it from whatever the video has, in this order:

1. A recording in `work/<video>/take/`: the creator's own voice, recorded on their phone. This is the default and it is free. A light chain cleans it (a high-pass for rumble, gentle noise reduction, a soft compressor). The file is read in place and never changed.
2. ElevenLabs, when the profile says `voice_over` is `elevenlabs` or `clone` and `ELEVENLABS_API_KEY` is in `.env`. It reads the narration from `script.md`: with `elevenlabs`, in one of the account's stock voices; with `clone`, in the copy of the creator's voice saved in `me/voice/elevenlabs.json`. With `clone` and no copy saved yet, it stops and says so instead of using a stock voice.
3. Otherwise it stops and says what to do. With no key it says exactly: "No ElevenLabs key, so record the voice-over on your phone and drop it in work/<video>/take/" (with the video's name filled in). Pass that on, and carry on when the creator says the file is there.

Ask the creator to record in a quiet room, phone held a hand's width from the mouth, reading the narration straight through. A retake of a sentence is fine: tell them to pause, then say the whole sentence again, and cut the bad one out before dropping the file in.

A copy of the creator's voice is made only with their consent, which the profile records as `voice_over: "clone"`. Right before making it, ask them once, plainly: "This uploads these recordings of your voice to ElevenLabs so it can make a copy of your voice. Shall I go ahead?" Run `uv run python -m kit.voice clone '<their name>' <sample files>` only after a clear yes, with one to two minutes of their voice alone. It saves the voice for later videos, and it is used only while `voice_over` stays `clone`.

The pace. The voice-over is made at its final pace before anything is timed to it, so the pictures land on the words as they are heard and the finish runs at 1.0. ElevenLabs reads at its speed setting of 1.15, which sounds more natural than stretching the sound afterwards. A recording gets the same 1.15 with a pitch-keeping tempo change, the factor a filmed Reel is sped up by. Pauses over 0.35 s are cut to 0.24 s, because the style has no dead air. `python -m kit.voice record <video> --keep-pace` keeps the creator's own pace when they ask for it. When a recording is paced, the voice step prints one line saying so. After the creator's first voice-over, tell them in one sentence that their recording was sped up to 1.15x with long pauses cut, and that they can keep their own pace (`python -m kit.voice auto <video> --keep-pace`, or say "keep my pace").

The voice step writes `vo.m4a` at -13 LUFS, and `words.json` with every word timed by the speech model and aligned. Read `words.json` against the narration: a name the model spelt wrong is fine to leave, because the pages are edited by hand next. With ElevenLabs the step also names the voice that read it. The creator never picks a stock voice, so after their first ElevenLabs voice-over, tell them its name.

## The pages

`uv run python .claude/skills/mini-me/kit/beats.py <video>` turns `words.json` into `beats.json`: one page per spoken phrase. Each page is `{"t", "text", "pose", "field", "prop"}`: Pages are numbered from 1, and the same numbers are used everywhere: in `beats.py`, `check.py`, the stage's notes and `Stage.page(n)`.

| Key | What it is |
|---|---|
| `t` | when the page lands, in seconds (the first page is at 0, on screen from the first frame) |
| `text` | the phrase's keyword headline in capitals, four words at most. Wrap one word in stars (`BASIL *DIES*`) to put it on an accent plate |
| `pose` | one of the 14 core poses, a new one every page |
| `field` | `dark` or `light`, taking turns |
| `prop` | what arrives because of the pose (below) |

The first draft comes from word lists. Read every page and sharpen it before writing scenes: a headline is the keyword of the phrase (`3 OF 5`, `WEEK THREE`, `HERE'S WHY`), never a running caption. Keep `t` as it is unless two pages need joining. Then check it: `beats.py <video> --check`. Running `beats.py` again on pages you edited keeps your version as `beats.backup.json`.

Pick the pose for what the phrase means:

| Pose | When |
|---|---|
| `point_up` | the hook, pointing at its headline; a number or a first step |
| `point_left`, `point_right` | showing something: the card slides in from the side it points to |
| `thinking` | a question |
| `shrug` | "maybe", "it depends", something nobody knows |
| `facepalm` | the problem or the mistake the video fixes |
| `mind_blown` | a big number or a surprise |
| `thumbs_up` | a payoff that works |
| `celebrate` | a win, with a burst of sparkles |
| `hold_card` | a checklist, a plan, the thing they get. Give the page a card prop: its words go on the card the character holds up. The draft picks this pose only when the pack measured that card. Without one, a hold_card page with a card is refused by `beats.py --check`, `install.py` and `check.py`, because the card would drop in beside a blank card: pick another pose for the card, or draw hold_card again with a large plain white card and pack again |
| `typing` | writing, a prompt, a search, a message |
| `wave` | hello and goodbye |
| `idle` | the close: a big head-and-shoulders punch-in where the mouth does the work; or when nothing else fits |
| `talking` | the in-between, when nothing else fits |

Props:

| Prop | What arrives |
|---|---|
| `card: <a few words>` or `card: assets/<file>.png` | a white receipt card from the side the pose points to, or dropping from the top. On a `hold_card` page the words, or the picture, go on the card the character holds up instead |
| `stamp` or `stamp: <TEXT>` | a stamp slams down beside the head with a thud (the page's accent word by default) |
| `count` | one number in the headline counts up: the accented one, or the first. Only a number of 20 or more counts, and it counts up from 10; 1 to 19 show whole, and so do a year such as 2024 and any number on the first page, so frame 0 reads right. `check.py` notes a count it left whole |
| `burst` | sparkles burst around the head |
| `bubble: <text>` | a speech bubble by the head, such as `?` or `!` |
| `cta` | the closing call to action (one page, made from the closing line) |
| `none` | nothing extra |

## The look

These are the house rules of the style. They are what makes it work, so keep them unless the creator's rules say otherwise.

- Two flat fields, the creator's `--field-dark` and `--field-light`, each with a faint dot grid. The field flips on every page with a push and speed lines, and a whoosh lands on every flip. Nothing sits still: a new page, pose and field about every 1.5 to 2.5 seconds.
- The headline is the keyword of the spoken phrase, at most four words, in `--font-display` capitals at the heaviest weight the font has, fitted to the width from y 190 down to about y 620. Sizes mix inside a lockup: a short word set huge under a smaller line reads well. One accent word at most, on an accent plate. There is no caption track: the headlines carry the words.
- The character is big and cropped at the waist. The stage measures the waist from the idle drawing (just under the hands, arms down) and shows the character down to it at the largest scale from 8 down (6 to 8 is the look) that keeps the crown at y 960 or lower, so a big-headed character still shows its shoulders and chest, not just hair and face. A white sticker outline 12 px wide follows the silhouette, with a 3 px edge in the ink colour on the light field, built from the sprite's own pixels so it stays hard. The pose always stays inside x 90 to 990, and the mouth moves with the voice.
- A new pose every page, chosen for meaning. What arrives on screen is caused by the pose: it points and the card comes from that side; it holds up a card and the words are written on it; it thinks and a question mark pops up; a stamp lands and the character flinches; it celebrates and sparkles burst. Cards sit between the headline and the head, and tuck behind the head when they reach down to it.
- Receipts are real: a screenshot of the actual page, the product's real mark, a number with its source. A card holds about six words at most, at 26 px or larger. Drawn objects and props are fine when nothing real exists; never invent a number, a quote, a review or a product screen.
- The close is a punch-in: the character grows to scale 10 to 12 with the bottom of its mouth at y 1660, well inside the frame and clear of the phone's caption area, its head tucked behind a white card in front of it. The card shows the creator's avatar (their logo, or their initial on the accent), their name and their Instagram handle with its @ (only the Instagram handle; with none, the name alone). A follow call to action presses the Follow button on the spoken "follow". A comment call to action types the keyword into a comment box on the spoken keyword and presses Post. A pose too wide for the close gives way to idle; when even that cannot be scale 10, `check.py` prints a note saying why.
- Sound: the voice is forward at -13 LUFS. A whoosh on every field flip and a thud on every stamp and press, both made by `kit.sfx` from numbers. The bed, when the creator has music in `me/music/`, sits at least 15 dB under the voice (`ship.py` puts it 18 dB under) and fades out at the end. The Reel ends on the last word.
- The safe area: nothing within 90 px of either side, no text under 26 px, and nothing above y 190, where the phone shows its status bar.

## The scenes

`install.py` writes `build/hf/` and a starter `lib/scenes.js`. The stage builds every page on its own: the field, the headline, the character in its pose and the prop from `beats.json`. Write `scenes.js` for this Reel to add what each page needs on top: the real receipts, the objects, a second stamp, a chip. It is never overwritten, so it keeps your work when the project is installed again.

```js
Stage.page(2, {                          // the page number, from 1, as beats.py and check.py print it
  build: function (c) {
    Stage.card(c, { img: "assets/watering-can.png" });     // from the side the character points to
    Stage.stamp(c, "6 AM", { t: c.T("morning") });         // lands on the spoken word, with a thud
  }
});
Stage.page(5, { lines: ["3 OF 5", "*PLANTS*"], char: { pose: "celebrate" } });
```

- `c.T("word")` is when a word is said (`"phrase#2"` for its second time, `c.T.e(...)` for its end); `c.keyAt` is when the pose lands; `c.t0` and `c.t1` are the page's start and end. Time everything from these, never from typed seconds.
- `c.zone` is the free space between the headline and the head. `c.side` is where the pose points. `c.back` is behind the character and `c.front` in front of it.
- `Stage.card`, `Stage.stamp`, `Stage.bubble` and `Stage.chip` build and animate in one call. `c.A` has the motion helpers (`pop`, `rise`, `slide`, `drop`, `slam`, `punch`, `shake`, `count`, `burst`). `c.A.count` follows the same rule as the prop: under 20 it leaves the number whole, and 20 or more counts up from 10, unless you give it a starting number. `c.hit("thud", t)` adds a sound to `hits.json`.
- Every motion is a `fromTo` with both ends given. A value set only in the from-vars never applies in the render.
- Keep everything inside x 90 to 990. The receipt layers are clipped just inside those lines, so anything that strays shows cut off.
- A picture a scene uses is saved under `build/sources/` with its URL and date in `build/sources/SOURCES.md`, then copied into `build/hf/assets/`: HyperFrames loads only files inside the project folder.
- `Stage.page(n, { prop: "none" })` turns a page's prop off; `char: { scale: 6, x: 600 }` moves the character (it is kept inside the safe lines).
- `Stage.heldCard(c, { text: "..." })` or `{ img: "assets/<file>.png" }` writes on the card a `hold_card` pose holds up, fitted to its clear face at 26 px or larger. `c.held` is that face on screen, or null when the pack measured no card. A card prop on a `hold_card` page does this by itself.

The full list of what `c` holds is at the top of `stage.js`.

## The build

Heavy steps (the voice-over, the render, the finish and the speech gate) each take a heavy-job slot from `kit.media`, so at most two run at once, and each checks for 5 GB of free disk first. Run them one after another, and delete test renders once they have been looked at.

```bash
K=.claude/skills/mini-me/kit; V=<video>; W=work/$V

# 1. The narration brief, then script.md (written, humanized and approved)
uv run python $K/brief.py --number 3

# 2. The voice-over: the recording in take/, or ElevenLabs (--keep-pace keeps a recording's own pace)
uv run python -m kit.voice auto $V

# 3. The pages: draft, sharpen by hand, check
uv run python $K/beats.py $V
uv run python $K/beats.py $V --check

# 4. The project, then this Reel's scenes in $W/build/hf/lib/scenes.js
uv run python $K/install.py $V

# 5. Load it headless: page errors, layout, and the hit times; look at a few frames
uv run python $K/check.py $V --at 0.2 1 2 3 4
npx hyperframes lint $W/build/hf

# 6. Render, then the sound, the finish and the speech gate
uv run python $K/render.py $V
uv run python $K/ship.py $V                   # --mood <folder> picks the bed's folder, --music none for no bed

# 7. The gates and the review sheet
bash $K/gates.sh $V
```

Run `install.py` again after any change to the voice-over, `words.json`, `beats.json`, the profile or the character, then `check.py` again. `check.py` writes `build/hits.json` from the page itself, so the sound follows what is on screen. `ship.py` reads it, makes it again when it is older than `beats.json` or `scenes.js`, and refuses a render older than `beats.json`, `scenes.js` or the voice-over, because that render does not show the latest changes.

## Things that go wrong

- A cue phrase the narration never says throws inside the stage, and `check.py` prints which one. Copy cues from `words.json`, spelt the way the speech model heard them.
- A GSAP timeline is a thenable. Never return one from an async function or from a page script you evaluate: the call waits forever.
- A pose that is too wide for scale 6 (both arms out) shrinks to fit, and `check.py` notes it. Pick a narrower pose for that page instead.
- The closing punch-in needs a compact pose. When the page's pose is too wide for scale 10 to 12 the close uses idle, and when the head cannot fit between the card and the mouth line at scale 10, it uses a smaller scale with a note saying why.
- A headline of four long words gets small. Cut it to the two words that carry the phrase.
- A `hold_card` page shows a blank card when its prop is not a card (`check.py` notes it). When the pack measured no card on the pose, a `hold_card` page with a card is refused by `beats.py --check`, `install.py` and `check.py`. Give the page another pose, or draw `hold_card` again with a large plain white card and pack again.
- A page shorter than about half a second flashes by. Join it to the page next to it in `beats.json`.
- HyperFrames keeps a frame cache in the system temp folder (`hyperframes-extract-cache-*`), which is safe to delete when no render is running.
- After `ship.py` passes, delete `build/picture.mp4` and `build/edit.mov` once the creator has signed off; keep `vo.m4a`, `words.json`, `beats.json` and `build/hf/`.

## Before handing over

- [ ] The narration was approved by the creator and went through the humanizer, with no em or en dashes
- [ ] The open reads on mute: the first page's headline says the topic, already on screen at frame 0
- [ ] Every page has a headline of four words or fewer, and a new pose chosen for what it says
- [ ] The fields flip on every page, with a whoosh on each flip
- [ ] The character is big (scale 6 to 8) and cropped at its waist, with shoulders and chest in view, the white outline and the ink edge on the light field
- [ ] What arrives on each page is caused by the pose, and every receipt is real and in `build/sources/SOURCES.md`
- [ ] The close shows the creator's name and handle, and the follow press or the typed keyword lands on its word
- [ ] `check.py` and `beats.py --check` pass, and `npx hyperframes lint` shows no errors
- [ ] `ship.py` passed the speech gate; `gates.sh` passes (format, margins, no caption track, no frozen stretch, -13 LUFS, the bed measured 15 dB under, picture and sound together, the end)

Then run the `review` skill on `work/<video>/final.mp4`. Its rubric items 2 to 5 belong to UI story; for this style check the fields flipping, a headline page per phrase, the character big and cropped at the waist, and no caption track.

## The kit

Everything is in `.claude/skills/mini-me/kit/`. Run the scripts with `uv run python`; each one answers `--help`.

| File | What it does |
|---|---|
| `brief.py` | saves the chosen idea and prints the brief for the narration |
| `beats.py` | `words.json` into `beats.json`, one page per spoken phrase; `--check` checks an edited one |
| `install.py` | the HyperFrames project: tokens and fonts, GSAP, the character, the data, `index.html`, a starter `scenes.js` |
| `stage.js`, `stage.css` | the style: fields, headlines, the character's placement and outline, props, transitions, the close |
| `check.py` | loads the composition headless, checks it, and writes `build/hits.json` |
| `render.py` | renders the composition to `build/picture.mp4` with the kit's pinned HyperFrames, in a heavy-job slot |
| `ship.py` | the sound effects, the bed (measured), the finish at 1.0 and the speech gate, into `final.mp4` |
| `gates.sh` | the checks on `final.mp4`, then the contact sheet for the review |
| `margin_scan.py` | every frame checked for anything inside the 90 px side margins |
| `test_clip.py` | a short clip of the character saying one line, for onboarding |
| `character/` | the character runtime, `character.js` (the `character` skill packs the character it reads) |

Shared helpers in `kit/`: `kit.voice` (the voice-over), `kit.sfx` (the whoosh and the thud), `kit.transcribe` (word timings), `kit.finish` (bed and loudness), `kit.qa_captions` (the speech gate), `kit.music` (the creator's beds) and `kit.media` (heavy-job slots, free disk, `probe`).

## Feedback

When the creator corrects something about a narration, a page or a finished Reel, fix it in the video first. Then record the lasting part with `kit.rules.add("mini-me", "<rule>")`. In a shell: `uv run python -m kit.rules add mini-me '<rule>'`, with single quotes so `$` and backticks stay as typed. Write one or two plain sentences, 300 characters at most, with no dates and no quotes from the creator. A change to how the character itself looks is not a rule: it goes to the `character` skill as a redraw. If the rule contradicts an older one for this skill, remove the older one first: `uv run python -m kit.rules read mini-me --numbered` shows the numbers, and `uv run python -m kit.rules remove mini-me <number>` removes one. Say in one line what you recorded.
