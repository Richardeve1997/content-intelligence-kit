# Setting up the kit

Setup is one conversation with Claude Code or Codex, held inside this folder. It checks your Mac, asks about you one topic at a time, writes your answers into a folder called `me/`, and ends with a test clip in your own colours. Set aside 30 to 45 minutes. It can take longer if your Mac has to download the Python tools, and a bit longer again if you build the mini me character during setup.

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

1. Welcome. A short explanation of what the kit does, what version 1 includes, how long setup takes, and which paid tools are optional.
2. Machine check. One table of checks with the fix for each, run with your permission. See below.
3. Who you are. Your name or brand, your handles, what your videos are about, why you are credible, who they are for, the platforms you post on, and how your videos end: a plain follow, or a comment keyword and what you send to people who use it.
4. Your look. Your colours (a light canvas, an ink, an accent, and a dark and a light field for mini me), your fonts and your logo. If you give a website, it can pull the palette and logo from it. You see a preview card before anything is saved, and it warns you about colours that are hard to read together.
5. Your voice. One to three scripts or transcripts of yours, links to your own videos to transcribe, or a few adjectives and sample sentences. This becomes the voice model that scripts and captions are written from.
6. Your watch list. Between 5 and 30 creators on Instagram, TikTok or YouTube whose videos you want the research to learn from.
7. Posting. Your time zone, your usual posting time for each platform and a few hashtags for your posts. If you want the kit to post for you, also your Zernio key and a check of which platforms you have connected there. Without Zernio, every video still ends with the MP4 and a file with a caption and your usual time for each platform, ready to post by hand.
8. Styles. UI story needs nothing generated. You get filming tips, including turning HDR video off on your phone (on an iPhone: Settings, Camera, Record Video, HDR Video off; if Camera is not in the main list, look under Apps), and you can record a 20 second test take. Mini me builds your character from photos or from a description, and you approve the character sheet, then the poses, then the in-between drawings. You also choose how the voice-over is made.
9. Test clip. A short clip in your brand for each style you chose. The UI story clip uses your test take if you filmed one, and a sample card with a scratch voice if you did not. The mini me clip needs the finished character.
10. Tour. How to talk to the kit, and what to say next: "run my research".

Your profile and brand files are written at the end of step 8, because the test clips need them.

## The machine check

You can run it yourself at any time. It changes nothing:

```bash
python3 bin/doctor.py
```

It lists each check with its status (ok, missing or warn) and the fix for anything missing. Fix the rows in the order shown, because later ones depend on earlier ones, then run it again. Under the table, one line names the optional keys that are not set yet. `python3 bin/doctor.py --json` prints the rows as JSON, and only the rows.

| Check | What it is for | Fix when it is missing |
|---|---|---|
| macOS on Apple silicon | Version 1 runs only on a Mac with an M1 chip or newer. | No command can fix this. |
| Homebrew | Installs most of the tools below. | `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"` |
| Homebrew, installed but not found | The terminal does not know where Homebrew is. | `echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile && eval "$(/opt/homebrew/bin/brew shellenv)"` |
| ffmpeg with h264_videotoolbox | Encodes video on the Mac's own hardware. | `brew install ffmpeg`, or `brew reinstall ffmpeg` if ffmpeg is there but built without that encoder |
| HDR tone mapping (zscale filter) | Turns an HDR phone video into normal colours. Homebrew's plain ffmpeg lacks it, so HDR footage can look washed out. This row only warns: turning HDR video off on your phone avoids the problem too, and so does the fix. | `brew install ffmpeg-full && brew unlink ffmpeg && brew link --force ffmpeg-full`, or `brew install ffmpeg-full && brew link --force ffmpeg-full` if you have no ffmpeg yet. It is a big download, and Homebrew has a ready-made copy only for macOS 15 and newer: on an older macOS it builds the full ffmpeg from source, which takes a long time. |
| uv | Runs the kit's Python. | `brew install uv` |
| Python 3.11 | The version the kit is pinned to. | `uv python install 3.11` |
| Node 22 or newer | Runs HyperFrames. Node 20 is too old. | `brew install node`, or `brew upgrade node` for an older one |
| HyperFrames | Renders the animations to video. The kit pins one exact version. | `npm ci` |
| Chromium for Playwright | Draws cards and previews. | `uv run python -m playwright install chromium` |
| GSAP | The animation library. It is downloaded, not shipped with the kit. | `bash bin/fetch_gsap.sh` |
| Transcription, WhisperX, torch and yt-dlp | Turn speech into text, line the captions up with the words, and download audio from links. | `uv sync --group video --group research --inexact` |
| Free disk space | Renders and encodes need room. A warning appears under 20 GB, and it counts as missing under 5 GB. | Empty the Trash and move old videos and downloads elsewhere. |

The matting model is not in the table. Its weights download the first time a video needs them.

## Keys

Every key is optional, and each has a free way round it. Costs change, so the Costs section of the README has the current numbers.

| Key | What it unlocks | Without it |
|---|---|---|
| `APIFY_TOKEN` | Researching Instagram and TikTok creators | Research uses YouTube creators and links you paste. |
| `OPENAI_API_KEY` or `GEMINI_API_KEY` | Drawing the mini me character with an image model (a signed-in Codex CLI works too) | The kit prints prompts that you paste into ChatGPT or Gemini. |
| `ELEVENLABS_API_KEY` | A stock or cloned ElevenLabs voice for mini me | You record the voice-over on your phone. |
| `ZERNIO_API_KEY` | Scheduling posts on six platforms | You post the MP4 by hand, with the captions from a file the kit writes. |

Keys live in a file called `.env` in this folder. Git ignores it, and it never leaves your Mac. Create it and fill it in yourself:

1. Run `cp -n .env.example .env`.
2. Open `.env` in any text editor.
3. Paste each key after its `=` and save.

Do not paste a key into the chat. The machine check names the optional keys that are not set yet and never shows a value.

## Where your answers go

Everything setup writes lives in `me/` (your profile, brand, voice, watch list, rules and lessons) and in `.env`. An update to the kit never touches either one, or the `work/` and `research/` folders.

## Stopping, resuming and starting over

- To carry on after a break, say "set me up" again. It reads `me/.onboarding.json`, tells you which step comes next and picks up there.
- To change one thing later, such as a colour or a handle, just tell your agent.
- To start over, delete `me/.onboarding.json`. Your other files stay.
