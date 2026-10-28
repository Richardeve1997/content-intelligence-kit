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
- Keys go in `.env`, and the creator types them there themselves: `cp -n .env.example .env`, open `.env` in any text editor, paste the key after the `=`, save. Never print a key, never repeat one back, and never write one anywhere except `.env`. If a key lands in the chat anyway, put it in `.env` without repeating it. `python3 bin/doctor.py` lists the key names that are set and never shows a value.

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
| Zernio | schedules posts on six platforms | they post the MP4 and the caption files by hand |

Do not quote prices, because they change. The Costs section of the README has the current ones.

Nothing is saved yet. Steps 1 and 2 are saved together when the machine check passes, because saving needs the kit's Python environment.

### Step 2: Machine check

1. Run `python3 bin/doctor.py` from the kit folder. It uses only the Python that comes with a Mac, so it works before anything is installed. If macOS offers to install the command line developer tools, tell them to say yes, then run it again.
2. Show the table: each check, its status and, when it is not ok, its fix. Say in one plain line what each missing row is for. ffmpeg builds and checks the video. Node and HyperFrames render the animations. The Python tools transcribe speech and line the captions up with the words.
3. Ask permission before you run any fix, and name what it installs. Fix the rows in the order shown, because later rows depend on earlier ones. Run one fix at a time and read its result before the next. If Homebrew's installer asks for the Mac password, ask the creator to run that one command in their own Terminal window and tell you when it finishes.
4. The Python tools (`uv sync`) come to around 2 GB and take several minutes, and Chromium adds a few hundred megabytes more. That install can run in the background while you start the questions in step 3. Save nothing until it has finished.
5. Run the doctor again. Repeat until no row says `missing`. A `warn` row can stay: low disk space (heavy jobs need at least 5 GB free, and 20 GB is comfortable) and optional keys. Ask whether they want to use any optional key now. If so, walk them through `.env` as above, then run the doctor to see the key listed as set. If not, move on. They can add a key whenever they like.
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

1. Their Zernio key goes in `.env`, typed by them, as described above. Run the doctor to see that `ZERNIO_API_KEY` is set.
2. Check which platforms they have connected inside Zernio with `kit.post.accounts_check()`. In a shell: `uv run python -c "from kit import post; print(post.accounts_check())"`. If `kit.post` is not there yet in this copy of the kit, say so in one plain sentence, keep their key and posting times, and go on. For a platform that is not connected, tell them to connect it inside Zernio. The kit reports a platform that is not connected and skips it. It never routes around it.
3. Ask for their default posting time on each platform, as a 24 hour time such as 18:00 in their own time zone. If they have no preference, suggest 18:00 as a starting point and tell them they can change it later.

If they do not, say that every video still ends with the MP4 and one caption file for each platform, ready to post by hand, and leave the posting times empty.

Both ways, ask for two more things:

- Their time zone. Run `readlink /etc/localtime`. The part after `zoneinfo/` is the zone name, such as Europe/London. Confirm it with them.
- Three to five base hashtags that fit their niche. Each starts with # and has no spaces. They go on every post except LinkedIn, which gets none.

```bash
uv run python -m kit.onboarding save posting - <<'EOF'
{"timezone": "Europe/London", "posting_times": {"instagram": "18:00", "tiktok": "18:00"}, "hashtags": {"base": ["#balconygarden", "#growyourown"]}}
EOF
```

Use `"posting_times": {}` when they post by hand.

### Step 8: Styles

Ask which styles they want: UI story, mini me, or both.

- UI story needs nothing generated. Give them the filming tips: vertical, 4K if the phone can, natural light, chest up, and room above the head. Offer to record a 20 second test take now. They film it on the phone and tell you where the file is. Never copy or move it. Link to it, or give its path to the test clip script.
- Mini me builds a character from 3 to 5 photos of them, or from a description if they would rather not share photos. Use the `character` skill: they approve the character sheet, then the poses, and anything they reject is drawn again. If the `character` skill is not there yet in `.claude/skills/`, say so in one plain sentence and tell them an update adds it. They can still choose mini me.
- For mini me, also ask how they want to make the voice-over. `record` is the default and is free: they record it on their phone. `elevenlabs` is a stock ElevenLabs voice with their own key. `clone` is a copy of their own voice, which asks for their consent first.
- And ask which tool draws the character. `manual` is the default: the kit prints prompts, they paste them into ChatGPT or Gemini and drop the images in a folder. `openai` and `gemini` need the matching key in `.env`. `codex` uses a signed-in Codex CLI.

```bash
uv run python -m kit.onboarding save styles - <<'EOF'
{"styles": ["ui-story", "mini-me"], "voice_over": "record", "image_provider": "manual"}
EOF
```

Now write the profile. The test clips read it, so it has to exist before step 9:

```bash
uv run python -m kit.onboarding finish
```

It checks every answer and writes `me/profile.json` and `me/brand/tokens.css`. If an answer is missing or wrong, it writes nothing and prints one sentence for each problem. Go back to the step that owns the answer, ask again, save that step again, and run `finish` again. When it succeeds the kit counts as set up. Tell the creator, with a short summary of the profile in plain words.

### Step 9: Test clip

Make a short clip in their brand for each style they chose. These are heavy jobs, so run them one at a time and check that at least 5 GB of disk is free first.

- UI story: `.claude/skills/ui-story/kit/test_clip.py`. It makes a branded card with captions over their test take, or over a sample card if they skipped the take.
- Mini me: `.claude/skills/mini-me/kit/test_clip.py`. It makes their character saying one line under a headline page. The character has to be built first.

Run `uv run python <script> --help` to see what a script takes, then run it. If a script is not there yet in this copy of the kit, say so in one plain sentence, tell them an update adds it, and go on.

Give them the full path of each clip and ask them to watch it. Do the colours, fonts and captions look like them? If not, go back to step 4, preview a change, save the look, run `finish` again, and make the clip again. When they say it looks right, or when there was nothing to run, save:

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

## Coming back later

- Setup left unfinished: `uv run python -m kit.onboarding status` shows where it stopped. Carry on at the next step.
- One change after setup, such as an accent colour or a handle: change that field in `me/profile.json` with the creator, then run `uv run python -m kit.onboarding tokens`. It writes the brand tokens again and prints any colour pair that is hard to read. Do not run `finish` again for this. It rebuilds the profile from the answers saved during setup, and that would put the old answers back over the edit.
- Starting over: ask first. Delete `me/.onboarding.json` and nothing else. Their other files stay.

## Feedback

When the creator corrects how this skill behaves, fix it now and record the rule with `kit.rules.add("onboard", "<rule>")`. In a shell: `uv run python -m kit.rules add onboard "<rule>"`. Write one or two plain sentences. If the rule contradicts an older one for this skill, remove the older one first. Say in one line what you recorded.
