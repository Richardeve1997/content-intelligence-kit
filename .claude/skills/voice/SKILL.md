---
name: voice
description: Build or refresh the creator's voice model, the short guide the kit reads before it writes any script or caption. Use when the creator says "build my voice model", "learn how I sound", "here are some of my scripts", "refresh my voice", "my voice file is wrong", "make the scripts sound more like me", or when onboarding reaches the voice step. It works from pasted scripts or transcripts, links to their own videos, or a few adjectives and sample sentences, and writes me/voice/VOICE.md.
---

# voice

The voice model is `me/voice/VOICE.md`: a page of concrete observations about how the creator writes and speaks, in five sections. The `ui-story` and `mini-me` skills write scripts from it, and the `post` skill writes captions from it. A model is only as good as its samples, so ask for real ones before you fall back to adjectives.

## Steps

1. Read the creator's rules for this skill with `kit.rules.read("voice")`. In a shell: `uv run python -m kit.rules read voice`. Follow them.
2. Look at what exists. If `me/voice/VOICE.md` is there, this is a refresh: the new samples join the old ones in `me/voice/samples/`, and you rewrite the whole model from all of them. If nothing is there, this is a first model.
3. Collect samples. Ask which of these they have, one at a time, and use every real sample they give you:
   - Scripts or transcripts, theirs, pasted into the chat. One to three is enough. More is better.
   - Links to their own videos, to transcribe. Only their own: a link to someone else's video says nothing about how they sound, so do not use it. Check that `kit/transcribe.py` exists. If it does, download the audio into a temporary folder under `work/` with `uv run yt-dlp --no-playlist -x --audio-format wav -o "work/voice-samples/%(id)s.%(ext)s" <link>` (at least 5 GB of disk free first), transcribe each file with `kit.transcribe.words(path, align=False)`, join the word texts into plain text, and delete the audio. If a download fails, or `kit.transcribe` is not there yet, say so in one plain sentence and ask them to paste the text instead. Most video pages let them copy a transcript.
   - No samples at all: three adjectives that describe how they sound, and three to five sentences written the way they would say them.
4. Save every sample exactly as received in `me/voice/samples/`, as `sample-01.txt`, `sample-02.txt` and so on. Never edit a sample, and never overwrite one. A refresh adds files after the last number.
5. Read all the samples, then write `me/voice/VOICE.md` in this layout:

```markdown
# Voice model: <their name>

Built from <what you used, for example "2 scripts and 1 video transcript">.

## Tone

## Rhythm

## Sentence shapes

## Openings and closings

## Never does
```

   - Write instructions a script writer can follow, in the present tense: "Opens on the result and what it cost." Be concrete. Every claim should be visible in at least one sample. Do not add a trait the samples do not show.
   - Under Sentence shapes, quote two or three short lines from the samples, in quotation marks and word for word, as anchors.
   - Under Never does, list what the samples avoid or what the creator says they would never say.
   - Aim for 150 to 400 words in all. A longer model gets skimmed.
   - If the model comes only from adjectives and sample sentences, say so on the "Built from" line, keep the claims to what those support, and suggest a refresh after they have posted a few videos.
   - `me.example/voice/VOICE.md` shows the layout. Its contents are for a made-up creator and never go into a real model.
6. Before you save, read `.claude/skills/humanizer/SKILL.md` and apply it in embedded mode to your own wording. Leave the quoted samples alone, and keep the creator's habits: the model describes their voice, including informal or unusual choices, so do not sand them off. Use no em dashes or en dashes in your prose. Repeat the pass whenever the wording changes.
7. Show them the file. It is short, so show all of it. Ask what is wrong, fix it, and ask again until they say it sounds like them.
8. When a refresh changes the model, say in a line or two what changed. When onboarding called you, hand back to the `onboard` skill, which saves the step.

Samples and the model stay in `me/`. Do not copy them anywhere else.

## Feedback

When the creator corrects how this skill behaves, fix it now and record the rule with `kit.rules.add("voice", "<rule>")`. In a shell, send the rule on standard input, so an apostrophe, `$` or a backtick in it stays as typed:

```bash
uv run python -m kit.rules add voice - <<'RULE'
<rule>
RULE
```

Write one or two plain sentences. If the rule contradicts an older one for this skill, remove the older one first. Say in one line what you recorded.
