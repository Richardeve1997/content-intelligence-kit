---
name: update-kit
description: Update the kit's own files to the latest release without touching the creator's files. Use when the creator says "update the kit", "get the latest version", "is there a new version", "check for updates", "upgrade the kit" or "apply the update".
---

# update-kit

Replaces the kit's files (skills, code, scripts, docs) with the latest release. Everything of the creator's stays where it is: `me/`, `work/`, `research/`, `.env`, and anything that is not a kit file, such as `.venv` and `node_modules`. A kit file the creator edited is left as they edited it, unless the release changed that file too. Then the update copies the edited file to `me/kit-backups/<date>/` before replacing it.

## Steps

1. Read the creator's rules for this skill with `kit.rules.read("update-kit")`. In a shell: `uv run python -m kit.rules read update-kit`. Follow them.
2. Check how the kit was installed. Run `git remote get-url origin` in the kit folder.
   - If it prints the kit's own address, https://github.com/Richardeve1997/content-intelligence-kit (with or without `.git` on the end, or the same repository over SSH), the folder is a clone of the kit and `git pull` updates it. The creator's folders are gitignored, so a pull leaves them alone. Once the creator says yes, run `git pull --ff-only` instead of the steps below. If the pull changed `pyproject.toml`, `uv.lock`, `package.json` or `package-lock.json`, run `uv sync --group video --group research --inexact` (with `--group character` added if they built a mini me character), then `npm ci`.
   - Anything else, an error or another address, means a copy, and the steps below update it. An unzipped download has no git history and a new repository may have no origin, so git answers with an error. A copy made with GitHub's "Use this template" is a git repository whose origin is the creator's own, so a pull there would never bring a release. The update script makes the same check and stops with a note when it runs in a clone of the kit.

   If `git pull` refuses because files are in its way, git names them. Ask the creator first, then deal with each one the way the update script deals with an edit the release changed. For a kit file they changed, copy it to `me/kit-backups/<today's date>/` under the same path and put the kit's version back with `git checkout -- <file>`. For an untracked file of theirs that sits where the release adds one, move that file into the same backup folder, because there is no kit version to put back. Then run `git pull --ff-only` again, and repeat if it names more files. Tell them where the copies are, and that a lasting preference belongs in their rules, which updates never touch. If the pull refuses because the folder has commits of its own, explain that and stop. Merge only when they ask for it.
3. Look first. This changes nothing:

   ```bash
   uv run python bin/update_kit.py
   ```

   It downloads the latest release and lists what would be added, restored, replaced and removed. It also lists the files the creator edited that the release did not change ("Kept your edits"), and the edited files it would replace. If it prints a network problem, pass it on in the same words. Someone with no internet access can download the release file elsewhere and pass it with `--from <file>`. To try a particular release, use `--ref <tag>`.
4. Tell the creator in plain words how many files change, which of their edits stay as they are, which edits would be replaced (each is backed up first), and whether the dependencies changed.
5. Decide whether to go ahead. "Update the kit" is the go-ahead, so apply right after the check. Pause first when the check lists edits that would be replaced, because the release changed those files too. Name them and ask. Files under "Kept your edits" need no pause, since they stay as they are. "Is there a new version" and "check for updates" only ask for the check, so stop after step 4.
6. Apply it:

   ```bash
   uv run python bin/update_kit.py --apply
   ```

7. Report what the output says: the counts, the files that were backed up and where, the kept edits, and the dependency line. When it says the dependencies changed, run `uv sync --group video --group research --inexact` and then `npm ci`, and add `--group character` to the sync if they built a mini me character. Keep `--inexact`: without it uv removes the groups the command does not name. Run `bash bin/check_adapters.sh` and report any problem it lists.

## Notes

- A file the creator edited and the release did not change stays as it is, and the update says so every time. A file the creator deleted stays deleted unless the release changed it, and then it comes back and is reported. Preferences belong in rules, not in kit files: a "going forward" correction goes to `me/rules/`, which updates never touch.
- With no `.kit-manifest.json` the update cannot tell edits from changes, so it backs up and replaces every file that differs from the release.
- After an interruption, run it again. It picks up where it stopped.

## Feedback

When the creator corrects how this skill updates the kit, fix it now and record the rule with `kit.rules.add("update-kit", "<rule>")`. In a shell, send the rule on standard input, so an apostrophe, `$` or a backtick in it stays as typed:

```bash
uv run python -m kit.rules add update-kit - <<'RULE'
<rule>
RULE
```

Write one or two plain sentences. If the rule contradicts an older one for this skill, remove the older one first. Say in one line what you recorded.
