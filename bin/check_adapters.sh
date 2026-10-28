#!/usr/bin/env bash
# Checks that Claude Code and Codex see the same skills, and that every skill is set up the same way.
#
#   bash bin/check_adapters.sh [kit folder]
#
# It checks that:
#   - .agents/skills is a link that leads to .claude/skills, not a copy;
#   - every folder in .claude/skills has a SKILL.md whose frontmatter has a name and a description;
#   - every SKILL.md except the humanizer's reads the creator's rules (kit.rules.read) and has a
#     heading that contains Feedback.
#
# With no problems it prints one line and exits 0. Otherwise it lists every problem and exits 1.
# The humanizer is a third party's skill and is kept as it came, so it is exempt from the last check.

root="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
skills="$root/.claude/skills"
link="$root/.agents/skills"
problems=()
checked=0

problem() { problems[${#problems[@]}]="$1"; }

# frontmatter_problems <file> <label>: one line for each thing wrong with the frontmatter block.
frontmatter_problems() {
  awk -v label="$2" '
    { sub(/\r$/, "") }
    NR == 1 {
      if ($0 != "---") { print label " has no frontmatter. It must start with a --- line, then name and description, then another --- line."; stopped = 1; exit }
      next
    }
    /^---[ \t]*$/ { closed = 1; exit }
    block && /^[^ \t]/ { block = 0 }
    /^name:/ { value = $0; sub(/^name:[ \t]*/, "", value); if (value != "") has_name = 1 }
    /^description:/ {
      value = $0; sub(/^description:[ \t]*/, "", value)
      if (value ~ /^[|>][+-]?[ \t]*$/ || value == "") block = 1
      else has_description = 1
    }
    block && /^[ \t]+[^ \t]/ { has_description = 1; block = 0 }
    END {
      if (stopped) exit
      if (!closed) { print label " has a frontmatter block that never closes with a --- line."; exit }
      if (!has_name) print label " has no name in its frontmatter."
      if (!has_description) print label " has no description in its frontmatter."
    }
  ' "$1"
}

if [ ! -d "$skills" ]; then
  problem ".claude/skills is missing."
fi

if [ -L "$link" ]; then
  leads_to="$(cd "$link" 2>/dev/null && pwd -P)"
  expected="$(cd "$skills" 2>/dev/null && pwd -P)"
  if [ -z "$leads_to" ] || [ "$leads_to" != "$expected" ]; then
    problem ".agents/skills does not lead to .claude/skills. Point it there with: ln -sfn ../.claude/skills .agents/skills"
  fi
elif [ -e "$link" ]; then
  problem ".agents/skills is a folder, not a link. Codex must see the same skills as Claude Code, so replace it with: ln -s ../.claude/skills .agents/skills"
else
  problem ".agents/skills is missing. Create it with: ln -s ../.claude/skills .agents/skills"
fi

if [ -d "$skills" ]; then
  for folder in "$skills"/*/; do
    [ -d "$folder" ] || continue
    name="$(basename "$folder")"
    file="$folder/SKILL.md"
    label=".claude/skills/$name/SKILL.md"
    checked=$((checked + 1))
    if [ ! -f "$file" ]; then
      problem ".claude/skills/$name has no SKILL.md."
      continue
    fi
    while IFS= read -r line; do
      [ -n "$line" ] && problem "$line"
    done < <(frontmatter_problems "$file" "$label")
    [ "$name" = "humanizer" ] && continue
    if ! grep -Fq 'kit.rules.read(' "$file"; then
      problem "$label never reads the creator's rules. Its first step should call kit.rules.read(\"$name\")."
    fi
    if ! grep -Eq '^#{1,6}[[:space:]].*Feedback' "$file"; then
      problem "$label has no Feedback section. End it with a heading that contains Feedback."
    fi
  done
fi

if [ "${#problems[@]}" -gt 0 ]; then
  if [ "${#problems[@]}" -eq 1 ]; then echo "Found 1 problem:"; else echo "Found ${#problems[@]} problems:"; fi
  for line in "${problems[@]}"; do echo "- $line"; done
  exit 1
fi

if [ "$checked" -eq 1 ]; then
  echo "Adapters look right: .agents/skills leads to .claude/skills, and 1 skill is set up alike."
else
  echo "Adapters look right: .agents/skills leads to .claude/skills, and $checked skills are set up alike."
fi
