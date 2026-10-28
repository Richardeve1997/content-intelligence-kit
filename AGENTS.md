# Codex adapter

This file adds what Codex needs on top of `CLAUDE.md`. It is not a second copy of the instructions.

1. Read `CLAUDE.md` in full before you do anything. It holds the rules for every agent, Codex included. If the two files disagree, `CLAUDE.md` wins.
2. Your skills are in `.agents/skills/`, which is a link to `.claude/skills/`. To change a skill, edit it under `.claude/skills/<name>/`, and never make a copy under `.agents/`. `bash bin/check_adapters.sh` confirms that the link and the skill files are right.
3. Hooks do not run in Codex. Claude Code uses them for a notice at session start, and for a check after each turn that asks for a rule when the creator's last message was a correction. In Codex, run `bash bin/session_start.sh` yourself as your first action. Each skill's own Feedback section is what records corrections here, with `kit.rules.add`.
4. Read tool names by what they do. Read means look at a file or an image, Write and Edit mean change a file, Bash means use the shell, and web tools mean look something up online.
