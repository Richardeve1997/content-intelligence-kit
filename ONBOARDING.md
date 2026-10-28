# Setting up the kit

Setup is one conversation with Claude Code or Codex inside this folder. It checks your Mac, asks about you one topic at a time, writes your answers into a folder called `me/`, and ends with a test clip in your own colours. Set aside 30 to 45 minutes. It takes longer when your Mac has to download the Python tools, and longer again if you build the mini me character during setup.

Progress is saved after every step. You can stop whenever you like, close the window, and say "set me up" later to carry on where you left off.

## Start

1. Open this folder in Claude Code or Codex.
2. Say: set me up.

Both agents read the kit's skills from this folder, so the `onboard` skill starts when you say that. If yours does not, paste this as your first message:

```
Read .claude/skills/onboard/SKILL.md and follow it from step 1.
```

## What you need

- A Mac with an Apple silicon chip (M1 or newer). Version 1 does not run anywhere else.
- At least 5 GB of free disk space to start, and 20 GB or more to work comfortably. The machine check tells you where you stand.
- An internet connection for the downloads.
- Nothing else. It helps to have a website to take your colours from, 3 to 5 photos for the mini me character, and 20 seconds of test footage, and you can skip all three.

## What happens, step by step

Your agent asks one question at a time and waits for the answer. When a step ends, it tells you in a few lines what it saved.

1. Welcome. What the kit does, the two video styles, how long setup takes, and which paid tools are optional. Nothing is saved yet: steps 1 and 2 are saved together once the machine check passes.
2. Machine check. One table of checks, with a fix for each row that needs one. Your agent asks before it runs a fix, says what it installs, and runs one fix at a time. If Homebrew's installer asks for your Mac's password, you run that one command in your own Terminal window. The Python tools come to around 2 GB and take several minutes, and Chromium adds a few hundred megabytes more, so the questions in step 3 can start while they download. See "The machine check" below.
3. Who you are, asked in this order.
   - Your name or brand, the way it should appear on screen.
   - The platforms you post on, out of Instagram, TikTok, YouTube, Facebook, LinkedIn and X.
   - Your handle on each one. LinkedIn has no @ handle, so for LinkedIn you give the last part of your profile or page address: yourname from linkedin.com/in/yourname, or yourpage from linkedin.com/company/yourpage.
   - What your videos are about, and why people should listen to you.
   - Who the videos are for, and what they want from them.
   - How your videos end: on a plain follow, or on a comment keyword. For a keyword you give the word (such as GROW), what you send to people who comment it, and the platforms where you or a tool really answer those comments. The call to action asks viewers to comment the keyword only on those platforms.
4. Your look: colours, fonts and a logo.
   - Five colours: a light canvas (the background behind graphics and captions), an ink (dark text on that canvas), one bright accent for highlights, and a dark and a light field (the backgrounds mini me flips between). If you give a website, your agent can pull the palette and logo from it.
   - Three fonts: one for headlines and captions, one for body text and one mono font. The kit ships Inter Tight, Inter and JetBrains Mono. For a font of your own, your agent makes the folder `me/brand/fonts/` and you copy the file into it: a .woff2, .woff, .ttf or .otf font you have the right to use.
   - A logo is optional. If you have one, it goes into `me/brand/logo/` the same way, as a PNG or SVG.
   - You see a preview card in your browser before anything is saved. It names any pair of colours that is hard to read, and you decide whether to change them.
5. Your voice. One to three scripts or transcripts of yours, links to your own videos to transcribe, or a few adjectives and sample sentences. They become `me/voice/VOICE.md`, the voice model that scripts and captions are written from. You read it and say what is wrong until it sounds like you.
6. Your watch list. Between 5 and 30 creators on Instagram, TikTok or YouTube whose videos you admire or compete with, as handles or profile links. Fewer than 5 is fine to start with: research still runs, but its ideas rest on fewer videos, so add more as you find them. Instagram and TikTok research needs an Apify key, and each run has a spending cap of 25 cents on Instagram and 50 cents on TikTok unless you change it. YouTube creators cost nothing. With no Apify key yet, your Instagram and TikTok names stay on the list for when you add one. Your agent also asks once whether you already have video ideas, and writes any you give into `me/ideas.md`, which research reads.
7. Posting. Your usual posting time for each platform, as a 24 hour time such as 18:00 in your own time zone, then your time zone and three to five hashtags for your posts (every platform but LinkedIn gets them). You can skip the times, and then each post needs a time when you ask for it. If you want the kit to post for you, also your Zernio key and a check of which platforms you have connected there. A platform that is not connected is skipped until you connect it inside Zernio. Without Zernio, every video still ends with the MP4 and one file, `post/manual.md` in the video's folder, that holds each platform's caption and the day and time to post it, ready to post by hand.
8. Styles. UI story, mini me, or both.
   - UI story needs nothing generated. You get filming tips, including turning HDR video off on your phone (on an iPhone: Settings, Camera, Record Video, HDR Video off; if Camera is not in the main list, look under Apps), and you can record a 20 second test take. Your agent links to the take where it is and never copies or moves it.
   - For mini me you choose how the voice-over is made: your own phone recording (free, and the default), an ElevenLabs stock voice with your key, or a copy of your own voice, which asks for your consent first. ElevenLabs' free plan includes no commercial licence, so both ElevenLabs choices need a paid plan for the videos you post.
   - You also choose the tool that draws the character: prompts you paste into ChatGPT or Gemini (the default), an OpenAI or Gemini key, or a signed-in Codex CLI. Then your agent builds the character from 3 to 5 photos of you or from a description, and you approve the character sheet, then the poses, then the in-between drawings. To build it later instead, say "make my character" when you are ready.
   - With an ElevenLabs stock voice and the key in `.env`, your agent lists the stock voices on your account and offers two or three that suit your character, each with a link to hear it. The one you pick is saved by name, and every voice-over reads in it until you pick another. For one video in a different voice, the voice step takes `--voice` and that voice's name. With no pick, the kit uses the account's first stock voice by name and says so each time.
   - Both styles can lay a music bed under your voice. The kit ships no music, so add tracks you have the right to use to a mood folder in `me/music/`, such as `me/music/calm/`. Until then your videos have no bed.
9. Test clip. A short clip in your brand for each style you chose, made one at a time.
   - A clip takes about 90 seconds on a fast Mac. The first run also downloads the speech models, which can add several minutes.
   - For each clip you get its path, its length and what it used, plus any note worth knowing, such as a take filmed in HDR that may look washed out.
   - The UI story clip is a branded card with karaoke captions, 5 to 10 seconds long. With a test take, a stretch of it plays in the rounded frame under the card. Without one, a scratch voice reads a sample line.
   - The mini me clip is your character saying one line, so it needs the finished character. It uses your own recording of that line if you make one, your ElevenLabs voice if you chose one and the key is set, and a scratch voice from the Mac otherwise.
   - If a clip does not look like you, your agent goes back to your look, changes it and makes the clips again.
10. Tour. How to talk to the kit, where your things live, and what to say next: "run my research".

Setup writes your profile and brand files at the end of step 8, because the test clips need them.

## The machine check

You can run it yourself at any time. It changes nothing:

```bash
python3 bin/doctor.py
```

Each row of the table says `ok`, `missing` or `warn`. A missing row names something the kit needs, with the command that fixes it, or a sentence when no command can. A warn row is worth a look and does not stop you: low disk space (under 20 GB), the HDR tone mapping row, and the rows marked "Not checked inside Codex's sandbox". Fix the missing rows in the order shown, because later ones depend on earlier ones, then run it again. Setup goes on once no row says missing. Under the table, one line names the optional keys that are not set yet. `python3 bin/doctor.py --json` prints the rows as JSON, and only the rows.

Codex usually runs commands in a sandbox. HyperFrames and Chromium cannot start there, and a read-only sandbox stops the Python 3.11 check as well, so those rows say "Not checked inside Codex's sandbox". That says nothing about your Mac. To settle them, say yes when Codex asks to run the check outside the sandbox, or run `python3 bin/doctor.py` in Terminal yourself.

| Check | What it is for | Fix |
|---|---|---|
| macOS on Apple silicon | Version 1 runs only on a Mac with an M1 chip or newer. | No command can fix this. On an Apple silicon Mac whose terminal app runs under Rosetta, quit the app, untick Open using Rosetta in its Get Info window in Finder, and open it again. |
| python3 command | The machine check and the Claude Code hooks run on the Mac's own `python3`. On a new Mac it only asks to install Apple's command line developer tools. | `xcode-select --install` |
| Homebrew | Installs most of the tools below. | `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"`. When Homebrew is installed but the terminal cannot find it: `echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile && eval "$(/opt/homebrew/bin/brew shellenv)"` |
| ffmpeg with h264_videotoolbox | Encodes video on the Mac's own hardware. | `brew install ffmpeg`, or `brew reinstall ffmpeg` if Homebrew's ffmpeg is there but built without that encoder. When the row names another ffmpeg that lacks the encoder, remove or rename that one after the install and open a new terminal. |
| HDR tone mapping (zscale filter) | Turns an HDR phone video into normal colours. Homebrew's plain ffmpeg lacks it, so HDR footage can look washed out. This row only warns: turning HDR video off on your phone avoids the problem too, and so does the fix. | `brew install ffmpeg-full && brew unlink ffmpeg && brew link --force ffmpeg-full`, or `brew install ffmpeg-full && brew link --force ffmpeg-full` if you have no ffmpeg yet. It is a big download, and Homebrew has a ready-made copy only for macOS 15 and newer: on an older macOS it builds the full ffmpeg from source, which takes a long time. |
| uv | Runs the kit's Python. | `brew install uv` |
| Python 3.11 | The version the kit is pinned to. | `uv python install 3.11` |
| Node 22 or newer | Runs HyperFrames. Node 20 is too old. | `brew install node`, or `brew upgrade node` for an older one |
| HyperFrames CLI, at the version the kit pins | Renders the animations to video. | `npm ci` |
| Chromium for Playwright | Opens the video graphics in a hidden browser to check them, and records real web pages for UI story. | `uv run python -m playwright install chromium`, after the Python tools below |
| GSAP in kit/vendor | The animation library. It is downloaded, not shipped with the kit. | `bash bin/fetch_gsap.sh` |
| Transcription (mlx-whisper or faster-whisper), WhisperX word alignment, torch, yt-dlp | The Python tools. They turn speech into text, line the captions up with the words, run the matting model and download videos from links. | `uv sync --group video --group research --inexact` |
| Free disk space | Renders and encodes need room. The row warns under 20 GB and counts as missing under 5 GB. | Empty the Trash, then move old videos and downloads to an external drive or cloud storage. |

The matting model is not in the table. Its weights download the first time a video needs them.

## Keys

Every key is optional, and each has a free way round it. Costs change, so the Costs section of the README has the current numbers.

| Key | What it unlocks | Without it |
|---|---|---|
| `APIFY_TOKEN` | Researching Instagram and TikTok creators | Research uses YouTube creators and links you paste. |
| `OPENAI_API_KEY` or `GEMINI_API_KEY` | Drawing the mini me character with an image model (a signed-in Codex CLI works too) | The kit prints prompts that you paste into ChatGPT or Gemini. |
| `ELEVENLABS_API_KEY` | A stock or cloned ElevenLabs voice for mini me. ElevenLabs' free plan includes no commercial licence, so the videos you post need a paid plan. | You record the voice-over on your phone. |
| `ZERNIO_API_KEY` | Scheduling posts on six platforms | You post the MP4 by hand, with the captions from a file the kit writes. |

Keys live in one file, `.env` in this folder, and the kit reads them from there and nowhere else. A key set in your shell is not used, and the machine check says so when it finds one. Git ignores `.env`, and it never leaves your Mac. Create it and fill it in yourself:

1. Run `cp -n .env.example .env`.
2. Open `.env` in any text editor.
3. Paste each key after its `=` and save.

Do not paste a key into the chat. The machine check names the optional keys that are not set yet and never shows a value.

## Where your answers go

Apart from the tools the machine check installs, everything setup writes lives in `me/` (your profile, brand, voice, watch list, rules and lessons), in `.env`, and in `work/`. There, `work/onboarding-test/` holds the link to your test take, the test clips and their scratch files, and the voice step and the mini me clip keep their scratch files in `work/voice-samples/` and `work/mini-me-test-clip/`. An update to the kit never touches `me/`, `.env`, `work/` or `research/`.

## Stopping, resuming and starting over

- To carry on after a break, say "set me up" again. It reads `me/.onboarding.json`, tells you which step comes next and picks up there.
- To change one thing later, such as a colour or a handle, tell your agent.
- To make the test clips again, for instance once your mini me character is built, ask for them.
- To start over, delete `me/.onboarding.json`. Your other files stay.
