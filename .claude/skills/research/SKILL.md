---
name: research
description: Find out what is working for the creators on the watch list and turn it into five ranked video ideas, then write hooks for the idea they pick. Use when the creator says "run my research", "what should I make videos about", "what's working this week", "what are the creators I watch posting", "give me ideas from my watch list", "give me hooks for number 2", "more hooks for number 3", or "swap number 4". It scores every recent video against that creator's own normal, writes a report in the creator's brand, and also reads their ideas file and their own results.
---

# research

Research has two halves. `kit.research` does the gathering, the counting and the page building, and takes a few minutes, mostly waiting for the scrape. This skill does the reading and the judging: it picks ideas from what the numbers show, writes them in the creator's voice, and writes hooks for the idea they choose.

## How a video is scored

A video's outlier score is (3 x comments + likes) divided by that creator's median, the middle value across their videos on record. A score of 1 is an ordinary video for that creator, and 5 is five times that. Comments count three times as much as likes because they take more effort. A creator with fewer than 3 videos on record is a low sample: their numbers are rough, and one big score from them is weak evidence. A video under two days old is still climbing, so its score can rise.

## What it reads and writes

- It reads `me/watchlist.json` (the creators), `me/ideas.md` (the creator's own ideas, and any X post links they pasted), `me/profile.json` (niche, audience, credibility, time zone and spending caps) and `me/voice/VOICE.md`.
- Keys come from `.env`. `APIFY_TOKEN` adds Instagram and TikTok, and `ZERNIO_API_KEY` adds the creator's own results. YouTube and X links need no key. The creator types a key into `.env` themselves. Never ask for one in the chat, and never repeat one.
- It writes its files inside `research/`. Each run has a folder, `research/runs/<date>/`, named for the day in the creator's time zone. It holds `items.json` (everything the run found, for you to read), `report.html` (the page for the creator) and `ideas.json` (the ideas, which you write). `research/brain/` keeps what earlier runs saw, so each creator's normal stays steady. Never edit it. To forget a creator, delete their file there. The one other place it touches is `work/.slots`, where the heavy-job slot keeps a small lock file while a video is turned into text.
- It reads public pages and the creator's own analytics. It never posts, follows, likes or replies.

## Steps

1. Read the creator's rules for this skill with `kit.rules.read("research")`. In a shell: `uv run python -m kit.rules read research`. Follow them.
2. For "run my research", run `uv run python -m kit.research run` from the kit folder and let it finish. The scrape waits two minutes at most, so do not stop it or start it twice. A creator whose platforms keep stopping at that limit can set `research.scrape_seconds` in `me/profile.json` to a number from 10 to 1800. If it says the kit is not set up, offer onboarding. Then tell the creator what it printed, in your own words: how many videos from how many creators, anything skipped, and the best score. It takes three options. `--no-apify` skips Instagram and TikTok even when there is a key. `--no-transcribe` skips turning videos into text on this machine. `--offline` runs on made-up sample videos with no network, and is only for trying the kit. It writes to `research/samples/<date>/`, apart from the real runs, and nothing that picks an idea reads that folder, so never write ideas there. The first time speech to text runs on a machine, `kit.transcribe` downloads a speech model (hundreds of megabytes, and over a gigabyte on some Macs), so that run is slow and needs a connection.
3. A skipped source is not a failure. If the run printed "Instagram and TikTok skipped: add APIFY_TOKEN to .env to include them", say it in one sentence, say what the key adds, and carry on with what came back. Do the same for "Performance skipped: add ZERNIO_API_KEY to .env to include your own results". Say each once. Speech to text needs the video tools, which a research-only setup does not have, and the run then says "Speech to text skipped: install the video tools with uv sync --group video --inexact". The hooks then come from captions and only the transcripts are lost, so offer the command and carry on. Keep `--inexact` on that command, because without it uv removes the research tools. A notice can also name creators who returned nothing (a private account, a handle that does not exist, a channel with no Shorts) and give the reason when there is one. Pass the names on and ask whether the handle is right. When a notice says a spending cap was reached, it names the setting, `research.cost_cap.<platform>` in `me/profile.json`, which the creator can raise. Other sentences name their own fix, and `python3 bin/doctor.py` names each missing tool.
4. Read `research/runs/<date>/items.json`, whose path the run printed. Its `videos` come best first. Each has its `outlier`, its `hook` (the first spoken line, or the caption, or a YouTube video's title, when there is no transcript), the `low_sample` and `young` flags, `paid` (whether a brand paid for it), and a `transcript` when one was found. After them come `creators`, `x_posts` (each `read` or `unread`), `notes` from `me/ideas.md`, `performance`, and `notices` and `sources`, which say how each platform went. Read nothing else in the folder, and never open `.env`.
5. If the pull is thin, say so. A thin pull has no videos or only a handful, for example a watch list of Instagram creators with no `APIFY_TOKEN`, or a platform that was skipped. Tell the creator in a sentence or two what came back and what would add more: the key, or a few YouTube creators in `me/watchlist.json`. Then lean on what there is: the creator's own notes in `me/ideas.md`, X posts that were read, and `performance`. Write fewer than five ideas if the material is thin, drop the mix rule below, and say in each `why` that the idea rests on a note or a post and not on a watch-list video. Never invent evidence to fill the list.
6. Pick five ideas, using the rules below.
7. Write `research/runs/<date>/ideas.json` in one write. It is a list of ideas, each with `n` (1, 2, 3 and so on, in order, with no gaps), `title`, `angle`, `why`, `format`, `evidence` and `hooks`. Each `evidence` entry has `url`, `creator` and `outlier`, copied from `items.json`. Use 0 as the `outlier` for a source with no score, such as an X post or a note. Start every idea with three hooks.

```json
[{"n": 1, "title": "...", "angle": "...", "why": "...", "format": "Tutorial",
  "evidence": [{"url": "https://...", "creator": "...", "outlier": 5.5}],
  "hooks": ["...", "...", "..."]}]
```

   The shape is fixed because the video styles read this file. "Number 2" always means the idea whose `n` is 2 in the latest run.
8. Read `.claude/skills/humanizer/SKILL.md` and apply it in embedded mode to everything you wrote: titles, angles, reasons and hooks. Keep every number, link and name, and the creator's own voice. Use no em or en dashes. Repeat the pass whenever the wording changes.
9. Run `uv run python -m kit.research report`. It checks `ideas.json` and rebuilds `report.html`. If it lists problems, fix the file and run it again.
10. Tell the creator the page is at `research/runs/<date>/report.html` (`open research/runs/<date>/report.html` on a Mac). Give your first pick and the reason in one sentence, then ask which number they want hooks for.

## Picking the five ideas

- Start from the audience and niche in `me/profile.json`. Drop anything they would not care about, however well it scored: a joke, a giveaway, a sponsored post, a trend from another niche.
- A video whose `paid` is true is a paid partnership, so its angle is an ad's and says nothing about what the audience wants. Never let an idea rest on it alone. When `paid` is null, the kit cannot tell, so look at the video before an idea leans on it.
- Each title names something the audience already knows, such as a tool, a company, a person or a number. Each idea needs a reason to believe it: a result, a receipt, a named source or a before and after. An idea without one is left out.
- Rank on demand (the outlier score, and whether two or more creators have a strong video on the same theme), freshness, fit (the creator can own it, judging from `credibility` in `me/profile.json`) and appeal.
- Mix the sources, unless the pull is thin (step 5). At least two ideas rest on a recent video that ran 2 times normal or more, and at least one rests on something new this run: a fresh video, an X post that was read, or a note in `me/ideas.md`. If fewer than two recent videos reached 2 times normal, use the ones that did, take the rest from the best-scoring recent videos, and give each one's score in `why` so the creator sees the lead is weaker. An X post that was read and fits backs an idea. A note can back an idea on its own when nothing else does. When `performance` shows a pattern or a past winner that matches an idea, say so in `why`.
- Take numbers only from `items.json`, an X post that was read, or a note. A big score from a low sample creator is a rough lead, so say so in `why` or pair it with a second video. A video that is still climbing is evidence that something is new, and not yet proof of demand.
- An X post marked `unread` could not be read. Tell the creator which link, and that pasting its text into `me/ideas.md` lets the next run use it. Do not guess what it said.
- Claims about the creator come only from `credibility` in `me/profile.json`. Never invent results, employers, follower counts or income, in a title or in a hook.

## Writing hooks

Read `me/voice/VOICE.md` first and write the way it describes, including its list of what the creator never does. A hook is two short sentences at most, about eight seconds out loud. Its first sentence names something (a tool, a company, a person) or gives a number, so the topic is clear with the sound off. The three hooks for one idea take different shapes, such as a claim, a contradiction, a result shown first, a named source, or a promise of a short list.

## Hooks for one idea

1. For "give me hooks for number 2", run `uv run python -m kit.research hooks 2`, with the number they said. It prints the idea, the opening lines of its strongest evidence with their scores, the hooks it already has, and which run it used. If it says there is no such idea, or no ideas yet, pass that on and ask what they want.
2. Read `me/voice/VOICE.md` and the creator's rules.
3. Write between 5 and 10 hooks, best first. Take the shape from the opening lines in the brief: what the first sentence does and how long it takes. Never copy a line. Give each hook a different shape, and keep the strongest existing hook if it still wins.
4. Apply the humanizer as in step 8 above.
5. Save them with the hooks on standard input, one per line. The command checks the count, replaces that idea's hooks and rebuilds the report.

```bash
uv run python -m kit.research hooks 2 --save <<'HOOKS'
First hook.
Second hook.
HOOKS
```

6. Reply in one line with the hook you would film first and why.

## Changing an idea, or running again

- For "swap number 4", "move number 3 to the top" or "make number 2 a listicle", edit only what was asked in `ideas.json`. Keep `n` running from 1 with no gaps, apply the humanizer to any new text, then run `uv run python -m kit.research report`. Never make a second report.
- Running again on the same day replaces the pull and the report and keeps the ideas already written. If the new pull changes the picture, say so and offer to rewrite them.
- For "run my research again from what I have, without going online", read the newest real run in `research/runs/`, its `ideas.json` and `items.json`, answer from those, and offer `uv run python -m kit.research report` to rebuild the page. Never use `--offline` for this, because it runs on made-up videos.
- `--date 2026-10-28` on `report` or `hooks` picks an earlier run.

## Feedback

When the creator corrects how this skill behaves, fix it now and record the rule with `kit.rules.add("research", "<rule>")`. In a shell, send the rule on standard input, so an apostrophe, `$` or a backtick in it stays as typed:

```bash
uv run python -m kit.rules add research - <<'RULE'
<rule>
RULE
```

Write one or two plain sentences. If the rule contradicts an older one for this skill, remove the older one first. Say in one line what you recorded.
