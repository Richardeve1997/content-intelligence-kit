# Codex adapter

This file adds what Codex needs on top of `CLAUDE.md`. It is not a second copy of the instructions.

1. Read `CLAUDE.md` in full before you do anything. It holds the rules for every agent, Codex included. If the two files disagree, `CLAUDE.md` wins.
2. Your skills are in `.agents/skills/`, which is a link to `.claude/skills/`. To change a skill, edit it under `.claude/skills/<name>/`, and never make a copy under `.agents/`. `bash bin/check_adapters.sh` confirms that the link and the skill files are right.
3. The kit sets up no hooks for Codex. Claude Code uses them for a notice at session start, and for a check after each turn that asks for a rule when the creator's last message was a correction. In Codex, run `bash bin/session_start.sh` yourself as your first action. Each skill's own Feedback section is what records corrections here, with `kit.rules.add`.
4. Read tool names by what they do. Read means look at a file or an image, Write and Edit mean change a file, Bash means use the shell, and web tools mean look something up online.
5. Codex usually runs your commands in a sandbox, and sets `CODEX_SANDBOX` for them. The sandbox can write only in this folder and the temporary folder (nowhere at all when it is read-only), and cannot reach the internet or the Keychain. Inside it:
   - Put `UV_NO_CACHE=1` in front of each command you run for the kit, as in `UV_NO_CACHE=1 uv run python -m kit.onboarding status`. uv cannot write its cache from the sandbox, and this gives it a temporary one. A uv command the kit runs for you picks it up too.
   - Renders (HyperFrames and Chromium) and anything that reaches the internet (installs, downloads, research, voice-overs, posting) cannot run. For those, and for any command that stops on a sandbox error such as "Operation not permitted", "Permission denied" or a crash as it starts, ask the creator to approve running that command outside the sandbox, then run it there.
   - A sandbox error never means a tool is missing, so never offer to install one because of it. `python3 bin/doctor.py` marks the rows it cannot check in the sandbox, and one run outside the sandbox gives the full check.
