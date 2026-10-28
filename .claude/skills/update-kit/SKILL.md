---
name: update-kit
description: Update the kit's own files to the latest release without touching the creator's files. Use when the creator says "update the kit", "get the latest version", "is there a new version", "check for updates", "upgrade the kit" or "apply the update".
---

# update-kit

Replaces the kit's files (skills, code, scripts, docs) with the latest release. Everything of the creator's stays where it is: `me/`, `work/`, `research/`, `.env`, and anything that is not a kit file, such as `.venv` and `node_modules`. If the creator edited a kit file, the update copies it to `me/kit-backups/<date>/` before replacing it.

## Steps

1. Read the creator's rules for this skill with `kit.rules.read("update-kit")`. In a shell: `uv run python -m kit.rules read update-kit`. Follow them.
2. Look first. This changes nothing:

   ```bash
   uv run python bin/update_kit.py
   ```

   It downloads the latest release and lists what would be added, replaced and removed, and which of those files the creator edited. If it prints a network problem, pass it on in the same words. Someone with no internet access can download the release file elsewhere and pass it with `--from <file>`. To try a particular release, use `--ref <tag>`.
3. Tell the creator in plain words how many files change, which of their edits would be replaced (each is backed up first), and whether the dependencies changed.
4. Decide whether to go ahead. "Update the kit" is the go-ahead, so apply right after the check. Pause first when the check lists edits to kit files, because those are about to be replaced. Name them and ask. "Is there a new version" and "check for updates" only ask for the check, so stop after step 3.
5. Apply it:

   ```bash
   uv run python bin/update_kit.py --apply
   ```

6. Report what the output says: the counts, the files that were backed up and where, and the dependency line. When it says the dependencies changed, run `uv sync` and then `npm install`. Run `bash bin/check_adapters.sh` and report any problem it lists.

## Notes

- A kit file the creator edited is replaced even when the release did not change it, so the kit always matches the release. Preferences belong in rules, not in kit files: a "going forward" correction goes to `me/rules/`, which updates never touch.
- If the kit was cloned with git, the update still works on the files directly. Git shows the updated files as modified, which is expected.
- After an interruption, run it again. It picks up where it stopped.

## Feedback

When the creator corrects how this skill updates the kit, fix it now and record the rule with `kit.rules.add("update-kit", "<rule>")`. In a shell: `uv run python -m kit.rules add update-kit "<rule>"`. Write one or two plain sentences. If the rule contradicts an older one for this skill, remove the older one first. Say in one line what you recorded.
