---
name: onboard
description: Set up the kit for a creator one topic at a time, saving as it goes so they can stop and pick up later. Use when the creator says "set me up", "onboard me", "get started", "set up the kit", "continue setup", "finish setting up" or "redo my look", and whenever kit.config.load_profile() raises NotOnboarded. It checks the Mac, learns who they are, their look, voice, watch list, posting and styles, writes everything into me/, makes a test clip for each style, and ends with a short tour.
---

# onboard

Setup is ten short steps. Ask about one topic at a time, show the creator what you wrote when a step ends, and save before you move on. They can close the window at any point and say "set me up" again later, and setup picks up where they left off. Plan for 30 to 45 minutes, longer if the machine check has a lot to download. Everything you write goes into `me/` or `.env`, and nowhere else.

How to talk during setup:

- Ask one question, wait for the answer, then ask the next. Never send a list of questions.
- Use plain words. The creator does not see field names, file names or JSON unless they ask. Say "your accent colour", not `colours.accent`.
- After each step, say in a few lines what you saved, in their words.
- If they do not know an optional answer, offer a default and move on. If one answer is still open at the end of a step, ask for it in plain text and carry on with whatever does not depend on it.
- Keys go in `.env`, and the creator types them there themselves: `cp -n .env.example .env`, open `.env` in any text editor, paste the key after the `=`, save. Never print a key, never repeat one back, and never write one anywhere except `.env`. If a key lands in the chat anyway, put it in `.env` without repeating it. `python3 bin/doctor.py` ends its table with one line naming the optional keys that are not set yet, and never shows a value.

## Steps

1. Read the creator's rules for this skill with `kit.rules.read("onboard")`. In a shell: `uv run python -m kit.rules read onboard`. Follow them. On a fresh clone `uv` may not be installed yet, and then there are none to read.
2. See where setup stands with `uv run python -m kit.onboarding status`. It says how many steps are done and which one is next. If `uv` or the kit's Python environment does not exist yet, setup is at step 1.
3. Carry on from the next step below. If every step is done, setup is finished: ask what they want to change, and see "Coming back later".
4. Save at the end of each step, with the command in that step. Save only after the creator has seen what you wrote and agreed with it.

## The ten steps

### Step 1: Welcome

Say this in your own words, briefly, the way you would across a table.

- What the kit does: it finds what is working for the creators they watch, plans and edits short vertical videos in two styles, checks each result, and can schedule the posts.
- The two styles. UI story: they film themselves talking, and the kit adds graphics, real footage of what they talk about, and karaoke captions. Mini me: a pixel character of them tells the story over a voice-over, with no camera. They can use either or both.
- Setup takes 30 to 45 minutes, and they can stop at any step.
- Every paid tool is optional, and each has a free way round it:

| Tool | What it does | Without it |
|---|---|---|
| Apify | researches Instagram and TikTok creators | research uses YouTube creators and links they paste |
| An image model (an OpenAI or Gemini key, or a signed-in Codex CLI) | draws the mini me character | the kit prints prompts they paste into ChatGPT or Gemini |
| ElevenLabs | a stock or cloned voice for mini me | they record the voice-over on a phone |
| Zernio | schedules posts on six platforms | they post the MP4 by hand, with the captions from a file the kit writes |

Do not quote prices, because they change. The Costs section of the README has the current ones.

Nothing is saved yet. Steps 1 and 2 are saved together when the machine check passes, because saving needs the kit's Python environment.

### Step 2: Machine check

1. Run `python3 bin/doctor.py` from the kit folder. It uses only the Python that comes with a Mac, so it works before anything is installed. If macOS offers to install the command line developer tools, tell them to say yes, then run it again.
2. Show the table: each check, its status and, when it is not ok, its fix. Say in one plain line what each missing row is for. ffmpeg builds and checks the video. Node and HyperFrames render the animations. The Python tools transcribe speech and line the captions up with the words.
   The HDR tone mapping row only warns. It matters when their phone films HDR video, which an iPhone does by default: without the zscale filter, HDR footage can look washed out. Its fix is a big install (the full ffmpeg brings dozens of other tools and takes several minutes), and Homebrew has a ready-made copy only for macOS 15 and newer: on an older macOS it builds the full ffmpeg from source, which takes a long time. So offer the free way first: turning HDR video off on the phone, which step 8 explains. They can leave the row as a warning. If ffmpeg itself is missing, fix that row first. The HDR row's command installs ffmpeg too, so someone who wants the full build can run it instead of `brew install ffmpeg`.
3. Ask permission before you run any fix, and name what it installs. Fix the rows in the order shown, because later rows depend on earlier ones. Run one fix at a time and read its result before the next. If Homebrew's installer asks for the Mac password, ask the creator to run that one command in their own Terminal window and tell you when it finishes.
4. The Python tools (`uv sync --group video --group research --inexact`) come to around 2 GB and take several minutes, and Chromium adds a few hundred megabytes more. That install can run in the background while you start the questions in step 3. Save nothing until it has finished.
5. Run the doctor again. Repeat until no row says `missing`. A `warn` row can stay: low disk space (heavy jobs need at least 5 GB free, and 20 GB is comfortable), or the HDR tone mapping row. The line under the table about optional keys is information, not a problem. Ask whether they want to use any of those keys now. If so, walk them through `.env` as above, then run the doctor again: the key is gone from that line once it is set. If not, move on. They can add a key whenever they like.
6. The matting model is not in the table. Its weights download the first time a video needs them, so say so if they ask.
7. If the Mac does not have an Apple silicon chip, say plainly that version 1 runs only on Apple silicon and that nothing in setup will change that. Stop there.
8. When no row is missing, save both steps:

```bash
uv run python -m kit.onboarding save welcome
uv run python -m kit.onboarding save machine
```

### Step 3: Who they are

Ask in this order, one question at a time:

1. Their name or brand, the way it should appear on screen.
2. The platforms they post on, from Instagram, TikTok, YouTube, Facebook, LinkedIn and X. At least one.
3. Their handle on each of those platforms, such as @yourname. Every platform on the list needs one.
4. What their videos are about, in a sentence.
5. Why people should listen to them, in a sentence. Use only what they tell you. If they say little, write little.
6. Who the videos are for, and what that audience wants from them.
7. How their videos end. Either a plain follow, or a comment keyword: the word (such as GROW), what they send to people who comment, and the platforms where they or a tool really do reply to those comments. List a platform only if something answers the comments there, because the kit never writes a keyword call to action for a platform where nothing replies.

Platform names are saved in lower case, and X is saved as "twitter".

Keep their wording. If you shorten or join their answers, run the sentences through the `humanizer` skill in embedded mode and keep every fact.

```bash
uv run python -m kit.onboarding save who - <<'EOF'
{
  "name": "Example Studio",
  "platforms": ["instagram", "tiktok"],
  "handles": {"instagram": "@examplestudio", "tiktok": "@examplestudio"},
  "niche": "Growing food on balconies and windowsills",
  "credibility": "Six years of growing vegetables on a three metre balcony",
  "audience": {"who": "People in small flats who want to grow some of their own food", "wants": "Simple projects that work without a garden or a big budget"},
  "cta": {"type": "comment", "keyword": "GROW", "payload": "the balcony planting checklist", "comment_platforms": ["instagram"]}
}
EOF
```

For a plain follow, use `"cta": {"type": "follow", "keyword": null, "payload": null, "comment_platforms": []}`. Tell the creator what you saved, in plain words.

### Step 4: Their look

1. Colours, five of them: a light canvas (the background behind graphics and captions), an ink (dark text on that canvas), one bright accent for highlights, and two field colours, one dark and one light (the backgrounds mini me flips between). Each is a six digit hex code. If they have a website, offer to pull the palette and logo from it: read the page's styles, propose five colours, and say in a line what each one is for. If they have no palette, ask for a colour or a mood they like and propose a set.
2. Fonts, three of them: display (headlines and captions), body, and mono. The kit ships Inter Tight, Inter and JetBrains Mono, so offer those. To use their own, they copy the font file (.woff2, .woff, .ttf or .otf) into `me/brand/fonts/` and give its path, such as `brand/fonts/YourFont.woff2`. Only fonts they have the right to use.
3. A logo, if they have one: a PNG or SVG copied into `me/brand/logo/` and given as `brand/logo/logo.png`. No logo is fine, and the answer is null.
4. Preview it before saving. This writes `me/brand/preview.html` and prints any colour pair that is hard to read:

```bash
uv run python -m kit.onboarding preview - <<'EOF'
{"colours": {"canvas": "<hex>", "ink": "<hex>", "accent": "<hex>", "field_dark": "<hex>", "field_light": "<hex>"},
 "fonts": {"display": "Inter Tight", "body": "Inter", "mono": "JetBrains Mono"}, "logo": null}
EOF
```

5. Fill in each `<hex>` with the creator's code. Show them the card with `open me/brand/preview.html`, which opens it in their browser. If you can show images, take a screenshot of the page and show that instead.
6. The command checks every pair with `kit.brand.check` and prints a sentence for each one that is hard to read. Read each warning to the creator in plain words and ask what they want to change. Offer a fix, for example a darker ink or a different accent, but it is their call. If they keep the colours, that is allowed.
7. When they approve, save the same JSON as their look:

```bash
uv run python -m kit.onboarding save look - <<'EOF'
{"colours": {...}, "fonts": {...}, "logo": null}
EOF
```

### Step 5: Their voice

Hand over to the `voice` skill. It asks for scripts or transcripts, links to their own videos, or a few adjectives and sample sentences, and it writes `me/voice/VOICE.md`. Come back when the creator is happy with that file, then save:

```bash
uv run python -m kit.onboarding save voice
```

### Step 6: Their watch list

1. Ask for 5 to 30 creators they watch on Instagram, TikTok or YouTube. These are people whose videos they admire or compete with. Accept handles, profile links or channel links, and write each as a handle with the @.
2. Explain the cost in plain words. Instagram and TikTok research needs an Apify key. Apify has a free plan with a monthly credit, and each research run has a spending cap: 25 cents on Instagram and 50 cents on TikTok unless they change it. YouTube creators and links they paste cost nothing. If they have no Apify key yet, keep their Instagram and TikTok names in the list anyway for when they add one.
3. Write `me/watchlist.json` in the same shape as `me.example/watchlist.json`: one list of handles for each of `instagram`, `tiktok` and `youtube`, and an empty list for a platform with none.
4. Ask once whether they already have video ideas. Write any they give you as bullets in `me/ideas.md`, under the heading `# Ideas` and one line saying that the research run reads the file. Leave out the example ideas from `me.example/ideas.md`.
5. Read both files back to them in plain words, then save:

```bash
uv run python -m kit.onboarding save watchlist
```

### Step 7: Posting

Ask whether they want the kit to post for them through Zernio.

If they do:

1. Their Zernio key goes in `.env`, typed by them, as described above. Run the doctor: `ZERNIO_API_KEY` is gone from its optional keys line once it is set.
2. Check which platforms they have connected inside Zernio: `uv run python -m kit.onboarding accounts`. It calls `kit.post.accounts_check()` and prints each of their platforms as connected or not connected. For a platform that is not connected, tell them to connect it inside Zernio. The kit reports a platform that is not connected and skips it. It never routes around it. If Zernio turns the key away or cannot be reached, the command prints one sentence and exits with an error: say what it said, keep their key and posting times, and go on.
3. Ask for their default posting time on each platform, as a 24 hour time such as 18:00 in their own time zone. If they have no preference, suggest 18:00 as a starting point and tell them they can change it later.

If they do not, say that every video still ends with the MP4 and a file with a caption for each platform (`post/manual.md` in the video's folder), ready to post by hand, and leave the posting times empty. The same goes for someone who said yes and has no key yet: `accounts` then prints one sentence saying so. Pass it on, ask whether they want to add the key now, and go on either way.

Both ways, ask for two more things:

- Their time zone. Run `readlink /etc/localtime`. The part after `zoneinfo/` is the zone name, such as Europe/London. Confirm it with them.
- Three to five base hashtags that fit their niche. Each starts with # and has no spaces. They go on every post except LinkedIn, which gets none.

The posting times use the same names, and X is saved as "twitter" here too.

```bash
uv run python -m kit.onboarding save posting - <<'EOF'
{"timezone": "Europe/London", "posting_times": {"instagram": "18:00", "tiktok": "18:00"}, "hashtags": {"base": ["#balconygarden", "#growyourown"]}}
EOF
```

Use `"posting_times": {}` when they post by hand.

### Step 8: Styles

Ask which styles they want: UI story, mini me, or both. Then ask what each one needs, one question at a time.

UI story needs nothing generated. Give them the filming tips:

- Film vertical, in 4K if the phone can, in natural light, from the chest up, with room above the head.
- Turn HDR video off on the phone before filming. On an iPhone: Settings, Camera, Record Video, HDR Video off (if Camera is not in the main list, look under Apps). On other phones, look for an HDR video option in the camera's video settings. An HDR take can look washed out after the edit, unless the HDR tone mapping row of the machine check is fixed.
- Offer to record a 20 second test take now, a few sentences about anything. They film it on the phone and tell you where the file is. Never copy or move it. Link it, so the test clip can find it:

```bash
mkdir -p work/onboarding-test/take
ln -s "<the path they gave you>" work/onboarding-test/take/
ls -L work/onboarding-test/take/
```

The last command checks the link: it lists the take, and prints an error for a link that points nowhere. Then the path they gave you has a typo, so ask for it again and make the link again.

If they skip the take, the test clip uses a sample card and a scratch voice instead.

Mini me builds a character from 3 to 5 photos of them, or from a description if they would rather not share photos. Ask two more things first, because both answers go into the profile:

- How they want to make the voice-over. `record` is the default and is free: they record it on their phone. `elevenlabs` is a stock ElevenLabs voice with their own key. `clone` is a copy of their own voice, which asks for their consent first.
- Which tool draws the character. `manual` is the default: the kit prints prompts, they paste them into ChatGPT or Gemini and drop the images in a folder. `openai` and `gemini` need the matching key in `.env`. `codex` uses a signed-in Codex CLI.

The character itself is built after the profile is written, further down.

```bash
uv run python -m kit.onboarding save styles - <<'EOF'
{"styles": ["ui-story", "mini-me"], "voice_over": "record", "image_provider": "manual"}
EOF
```

Now write the profile. The character skill and the test clips both read it, so it has to exist before either:

```bash
uv run python -m kit.onboarding finish
```

It checks every answer and writes `me/profile.json` and `me/brand/tokens.css`. If an answer is missing or wrong, it writes nothing and prints one sentence for each problem. Go back to the step that owns the answer, ask again, save that step again, and run `finish` again. When it succeeds the kit counts as set up. Tell the creator, with a short summary of the profile in plain words.

For mini me, hand over to the `character` skill now. If `.claude/skills/character/SKILL.md` is not there yet in this copy of the kit, say so in one plain sentence and tell them an update adds it. They can still choose mini me, and the mini me test clip waits for the character. Skip the rest of this section and go on to step 9. Otherwise the skill runs its own approval gates, so do not repeat them here, and its SKILL.md has the details. In its order:

1. Start the character: `uv run python -m kit.character start --description '<their words>' --outfit '<the outfit>' --photos <photo files>`. Leave out `--photos` when they gave none.
2. The character sheet and the face check: `uv run python -m kit.character sheet`. They approve the sheet before any pose is drawn: `uv run python -m kit.character approve sheet`.
3. The poses: `uv run python -m kit.character poses`. They approve them by name, as in `approve poses wave thumbs_up`, or all at once with `approve poses all`. Anything they reject is drawn again with `redraw <pose> --note '<what to change>'`.
4. The in-between drawings for the moving poses: `uv run python -m kit.character moves`, then `approve moves all` once they have looked at them.
5. The pack: `uv run python -m kit.character pack`, which writes `me/character/manifest.json`. Then the character skill's separate check, which makes the mini me runtime read the pack: `node .claude/skills/mini-me/kit/character/test_state.mjs me/character/manifest.json`. It must end with "all passed". If it fails, show the failing lines and fix the cause before the character is used in a video.

`uv run python -m kit.character status` shows where things stand at any point. The skill needs its dependency group first: `uv sync --group character --inexact`. Keep `--inexact`, or uv removes the video tools. With the manual image tool it stops and asks them to paste a prompt into ChatGPT or Gemini and save the picture back. Carry on with anything else that does not depend on it while they do.

The handover is done when the pack is made and the check ends with "all passed". If they would rather build the character later, that is fine too: they say "make my character" whenever they are ready.

### Step 9: Test clip

Make a short clip in their brand for each style they chose, with one command:

```bash
uv run python -m kit.onboarding clips
```

The clips are heavy jobs, so it makes them one at a time and first checks that at least 5 GB of disk is free. Each clip takes about 90 seconds on a fast Mac, and the first run also downloads the speech models, which can add several minutes. So run the command with a long timeout (10 minutes), or in the background, and wait for it to finish. Start nothing else heavy meanwhile. The command prints the full path of each clip it makes. It also prints any note a clip script left for the creator, such as an HDR take that may look washed out, a take that could not be used so the sample card was, or a voice copy that is missing. Pass those on in your own words.

- UI story: `.claude/skills/ui-story/kit/test_clip.py`. A branded card with karaoke captions, 5 to 10 seconds long. With their test take in `work/onboarding-test/take/`, it cuts a stretch where they speak and sets it in the rounded picture-in-picture box under the card. Without a take it uses a sample card and a scratch voice. It leaves out the cut-out of their head, so the head stays inside the box. A real edit adds the cut-out.
- Mini me: `.claude/skills/mini-me/kit/test_clip.py`. Their character saying one line under a headline page: "Hi, this is <their name>. Meet my mini me. It tells my stories while I stay off camera." The character has to be packed first. The voice is the first of these that exists: a recording of that line that they give you (`uv run python -m kit.onboarding clips --voice <file>`, worth offering when they chose `record` for the voice-over, so they hear their own voice), an ElevenLabs voice when their profile chose one and the key is in `.env`, and otherwise a scratch voice from the Mac.

When a script is not there yet in this copy of the kit, or the character is not built, the command says so in one plain sentence and goes on with the other style. Pass that on in your own words, tell them an update adds the missing script, and carry on. If a clip fails, the command prints what the script said and exits with status 1: tell them in plain words, offer to try again, and carry on with the rest of setup meanwhile.

Open each clip for them with `open <path>` and ask them to watch it. Do the colours, fonts and captions look like them? If not, go back to step 4, preview a change, save the look, run `finish` again, and make the clips again. When they say it looks right, or when there was nothing to make, save:

```bash
uv run python -m kit.onboarding save test_clip
```

### Step 10: Tour

Tell them how to talk to the kit. They never type commands. Examples to give:

| They say | What happens |
|---|---|
| "run my research" | the kit finds ideas from the creators on their watch list |
| "plan the video for number 2" | a UI story plan and a film guide for idea 2 |
| "here's my raw take, edit it" | the kit edits the take |
| "make number 3 a mini me video" | a mini me video for idea 3 |
| "review it" | a check of the finished video |
| "post it everywhere tomorrow at 6pm" | the captions come back for approval before anything is scheduled |
| "the captions are too long, fix that going forward" | the fix, and a rule so the next video follows it |
| "what's next" | the next step for the video in progress |
| "update the kit" | the latest kit files, with their own files left alone |

Say where their things live: `me/` holds their profile, brand, voice, watch list and rules, `work/` has one folder for each video, `research/` holds what research found, and `.env` holds their keys. An update never touches those.

Save the last step, then tell them what to say next: "run my research".

```bash
uv run python -m kit.onboarding save tour
```

## Fixing a saved answer

Saving merges groups, so a wrong entry inside a saved group stays when you save that group again. To clear a group, save it as null, then save the full group:

```bash
uv run python -m kit.onboarding save posting '{"posting_times": null}'
uv run python -m kit.onboarding save posting '{"posting_times": {"instagram": "17:00"}}'
```

A value that is only wrong, such as a mistyped handle, is fixed by saving the right one over it. A save with a platform name that is not one of the six is refused, and the message lists the right names. `status` lists an answer saved as null as still needed.

## Coming back later

- Setup left unfinished: `uv run python -m kit.onboarding status` shows where it stopped. Carry on at the next step.
- One change after setup, such as an accent colour or a handle: change that field in `me/profile.json` with the creator, then run `uv run python -m kit.onboarding tokens`. It writes the brand tokens again and prints any colour pair that is hard to read. Do not run `finish` again for this. It rebuilds the profile from the answers saved during setup, and that would put the old answers back over the edit.
- The test clips again, for instance once the mini me character is built: `uv run python -m kit.onboarding clips`.
- Starting over: ask first. Delete `me/.onboarding.json` and nothing else. Their other files stay.

## Feedback

When the creator corrects how this skill behaves, fix it now and record the rule with `kit.rules.add("onboard", "<rule>")`. In a shell: `uv run python -m kit.rules add onboard '<rule>'`. Write one or two plain sentences. If the rule contradicts an older one for this skill, remove the older one first. Say in one line what you recorded.
