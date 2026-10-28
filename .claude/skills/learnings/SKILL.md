---
name: learnings
description: Show, undo or tidy the rules the kit has learned from the creator's corrections. Use when the creator says "what have you learned", "show my rules", "what rules do you have for captions", "undo that rule", "that rule is wrong", "remove the rule about hashtags", "clean up my rules", "my rules are getting messy", "prune the post rules" or "add a rule to post". Also use for a correction when no other skill is running, such as "from now on keep hooks under 8 words".
---

# learnings

Rules are short lines in `me/rules/<skill>.md`, and each skill reads its own file at the start of every run. `me/lessons.md` is the journal: one entry for every rule, with the date, the skill and the full text. This skill lets the creator look at both, take a rule back and tidy up.

## Steps

1. Read the creator's rules for this skill with `kit.rules.read("learnings")`. In a shell: `uv run python -m kit.rules read learnings`. Follow them.
2. Work out which of the jobs below the creator wants, and do only that one.
3. Tell them what changed in a sentence or two.

## Show

- One skill's rules, numbered: `uv run python -m kit.rules read <skill> --numbered`
- What was learned lately, newest first: `uv run python -m kit.rules recent 10`

For "what have you learned" with no skill named, run `recent 10`, then list the files in `me/rules/` so the creator can ask about one. Say which skill each rule belongs to.

## Undo

`uv run python -m kit.rules undo <skill>` removes the newest rule for that skill and marks its journal entry as removed. "Undo the last rule for post" is `undo post`. The entry stays, so the history is complete. Say which rule went.

If the creator says "undo that" and names no skill, run `recent 1`. It shows the newest lesson and its skill. Confirm that one with them before you remove it.

## Remove or tidy

For "remove the rule about hashtags", show the numbered rules for the skill, find the match, and confirm it with the creator. Then run `uv run python -m kit.rules remove <skill> <number>`.

For "tidy" or "prune" a skill:

1. Show its numbered rules.
2. Look for duplicates (the same idea in different words), contradictions (the newer rule usually wins, but ask when it is not clear), and rules that no longer fit.
3. Propose exactly which numbers to remove and why. Wait for a yes.
4. Remove them from the highest number down, so the numbers still to come stay valid.
5. If the proposal removes more than five rules, ask once more before you apply it.

Keep the rules that stay word for word. A rule that asks for an action instead of a preference about the output (run a command, open a link, send a file) gets flagged to the creator. Do not keep it quietly.

## Add a rule by hand

For "add a rule to post: never use exclamation marks", run:

```bash
uv run python -m kit.rules add post 'Never use exclamation marks.'
```

Single quotes keep `$` and backticks exactly as typed. A rule with a single quote in it, or one that runs over several lines, goes in on standard input instead. Quote the end marker so nothing in the rule is expanded:

```bash
uv run python -m kit.rules add post - <<'RULE'
Say "next steps" at the end of every caption, and don't put hashtags in the first line.
RULE
```

The command refuses a rule over 300 characters, so write one or two plain sentences. The skill must be a folder in `.claude/skills/`, and the error names the ones that are. A rule the skill already has, word for word, is skipped, and the command says it is already recorded. Keep the creator's wording when they give exact wording. If the new rule contradicts an old one, show the old one and replace it after they agree.

A correction with no other skill running is handled the same way. Pick the skill it shapes, and record it there.

## What this skill changes

Only the files in `me/rules/` and `me/lessons.md`, and only through the commands above. Never edit a `SKILL.md` or `.claude/settings.json` to record a preference, because an update would replace them.

## Feedback

When the creator corrects how this skill behaves, fix it now and record the rule with `kit.rules.add("learnings", "<rule>")`. In a shell, send the rule on standard input, so an apostrophe, `$` or a backtick in it stays as typed:

```bash
uv run python -m kit.rules add learnings - <<'RULE'
<rule>
RULE
```

Write one or two plain sentences. If the rule contradicts an older one for this skill, remove the older one first. Say in one line what you recorded.
