#!/usr/bin/env bash
# What a session should know before it starts.
#
# It prints one of two things:
#   - "This kit is not set up yet. Say: set me up." when onboarding has not finished, or
#   - the five newest lessons the kit learned from the creator's corrections.
# When setup is done and nothing has been learned yet, it prints nothing. It always exits 0.
#
# It uses only the shell, so it works before the kit's Python packages are installed.
# Claude Code runs it when a session starts. The kit sets up no hooks for Codex, so Codex runs it by hand.

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
me="${KIT_ME:-$root/me}"
case "$me" in "~"*) me="$HOME${me#"~"}" ;; esac

if ! grep -Eq '"onboarded"[[:space:]]*:[[:space:]]*true' "$me/profile.json" 2>/dev/null; then
  echo "This kit is not set up yet. Say: set me up."
  exit 0
fi

# Each lesson in me/lessons.md has a heading: "## 2026-10-28 14:05 · the first sentence of the rule".
# A lesson that was taken back ends its heading with "(removed <date>)", and is left out here.
if [ -f "$me/lessons.md" ]; then
  recent="$(grep '^## ' "$me/lessons.md" | grep -v ' (removed [0-9-]*)$' | tail -n 5 | sed 's/^## /- /')"
  if [ -n "$recent" ]; then
    echo "Recent lessons from the creator's corrections:"
    echo "$recent"
  fi
fi
exit 0
