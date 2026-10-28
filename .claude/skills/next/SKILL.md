---
name: next
description: Say what the next step is for the video in progress. Use when the creator says "what's next", "what now", "where are we", "continue", "keep going", "pick up where we left off", "what should I do next", or sends any vague forward-moving message without naming a step. It only reads the newest folder in work/ and answers. It never starts a render or any other heavy job.
---

# next

This skill looks at the newest video folder in `work/`, works out which files exist, and names the next step. It reads files and answers. It starts nothing heavy: a render, an encode or a transcription waits for the creator's go-ahead and runs through the skill that owns it.

## Steps

1. Read the creator's rules for this skill with `kit.rules.read("next")`. In a shell: `uv run python -m kit.rules read next`. Follow them.
2. Run this from the kit folder. It prints the video it looked at, its state and the files it found, and uses only the standard library.

```python
import json
import os
from pathlib import Path

PLATFORMS = ("instagram", "tiktok", "youtube", "facebook", "linkedin", "twitter")
# Not videos: setup's test take and clips, the mini me clip's scratch folder, and the voice skill's downloads
SCRATCH = ("onboarding-test", "mini-me-test-clip", "voice-samples")

work = Path("work")
folders = [p for p in work.iterdir() if p.is_dir() and not p.name.startswith(".") and p.name not in SCRATCH] if work.is_dir() else []
folders.sort(key=lambda p: max(c.lstat().st_mtime for c in [p, *p.iterdir()]), reverse=True)
slug = os.environ.get("SLUG") or (folders[0].name if folders else None)
folder = work / slug if slug else None
take = folder / "take" if folder else None
take_files = [f for f in take.iterdir() if not f.name.startswith(".")] if take and take.is_dir() else []


def has(name):
    return folder is not None and (folder / name).exists()


def status_of(path):
    """What a live receipt says: scheduled (Zernio gave the post an id), unconfirmed or unreadable."""
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "unreadable"
    if not isinstance(record, dict):
        return "unreadable"
    return "scheduled" if record.get("post_id") else "unconfirmed"


def receipts():
    """{platform: status} for the records in post/. A cancelled or lost post is put away as
    <platform>.cancelled.json or <platform>.stale.json, so only <platform>.json says something was sent."""
    found = {}
    post = folder / "post" if folder is not None else None
    for platform in PLATFORMS if post is not None and post.is_dir() else ():
        if (post / f"{platform}.json").is_file():
            found[platform] = status_of(post / f"{platform}.json")
        elif (post / f"{platform}.cancelled.json").is_file():
            found[platform] = "cancelled"
        elif (post / f"{platform}.stale.json").is_file():
            found[platform] = "gone"
    return found


# The files decide first, so a video edited from a take with no plan (and so maybe no idea.json) still gets its step.
sent = receipts()
if folder is None:
    state = "no_video"
elif not folder.is_dir():
    state = "no_such_video"
elif has("final.mp4") and "scheduled" in sent.values():
    state = "posted"
elif has("final.mp4") and ("unconfirmed" in sent.values() or "unreadable" in sent.values()):
    state = "post_unsure"
elif has("final.mp4") and has("captions.json"):
    state = "captions_ready"
elif has("final.mp4"):
    state = "final_ready"
elif has("script.md"):
    state = "mini_me_in_progress"
elif take_files:
    state = "take_ready"
elif has("plan.md"):
    state = "needs_take"
elif not has("idea.json"):
    state = "no_idea"
else:
    state = "needs_plan"

print(f"video: {slug or 'none'}")
print(f"state: {state}")
markers = ["idea.json", "plan.md", "script.md", "take", "final.mp4", "captions.json", "post"]
print("found: " + (", ".join(m for m in markers if has(m)) or "nothing"))
if sent:
    print("receipts: " + ", ".join(f"{platform} {status}" for platform, status in sent.items()))
if has("post/manual.md"):
    print("manual: post/manual.md")
if state == "posted":
    for platform, status in sent.items():
        if status == "unconfirmed":
            print(f"warning: post/{platform}.json shows an attempt Zernio never confirmed")
        elif status == "unreadable":
            print(f"warning: post/{platform}.json cannot be read")
for f in take_files:
    if f.is_symlink() and not f.exists():
        print(f"warning: take/{f.name} points to a file that is not there any more")
if has("idea.json"):
    try:
        idea = json.loads((folder / "idea.json").read_text(encoding="utf-8"))
        # an idea from research has its number; one saved for a take with no plan has only its title
        print(f"idea: {idea['n']}, {idea['title']}" if "n" in idea else f"idea: {idea['title']}")
    except (OSError, ValueError, KeyError, TypeError):
        pass
runs = sorted(Path("research/runs").glob("*/ideas.json"))
if runs:
    try:
        print(f"ideas: {len(json.loads(runs[-1].read_text(encoding='utf-8')))} in {runs[-1].parent.as_posix()}")
    except (OSError, ValueError, TypeError):
        pass
```

3. Find the state in the table and tell the creator the next step in one or two sentences, then offer to hand over to the skill named there. If they said "continue" or "keep going", hand over right away for light work such as a plan, a script or captions. A render, an encode or a transcription still waits for their go-ahead, however they phrased the request.
4. To look at a different video, run the script again with `SLUG=<folder name>` set.

| State | What it means | What to say, and who takes over |
|---|---|---|
| `no_video` | `work/` holds no video. | Nothing is in progress. Say "run my research" to find ideas (`research`). If the script printed `ideas:`, they can pick a number from that run instead: "plan the video for number 2" (`ui-story`) or "make number 3 a mini me video" (`mini-me`). |
| `no_such_video` | There is no folder with the name in `SLUG`. | Check the name against the folders in `work/`. |
| `no_idea` | The folder has no `idea.json`, and no plan, script, take or finished video either. | The kit does not know which idea this video belongs to. Pick an idea from the latest research run, or say "run my research". |
| `needs_plan` | The idea is saved. Nothing is planned yet. | Pick a style for the idea the script printed: "plan the video for number N" (`ui-story`) or "make number N a mini me video" (`mini-me`). |
| `needs_take` | `plan.md` is written and `take/` has no file. | Film the take with the film guide in `plan.md`, then say "here's my raw take, edit it". Filming is the creator's job, so stop here. |
| `take_ready` | `take/` holds a take and there is no `final.mp4`. A take filmed with no plan counts too, whether `idea.json` holds only its title or is missing. | Edit it (`ui-story`). |
| `mini_me_in_progress` | `script.md` is written and there is no `final.mp4`. | The narration is ready. The voice-over and the build come next (`mini-me`). |
| `final_ready` | `final.mp4` exists and there are no captions. | The video is finished. Captions come next. The `post` skill writes one per platform for approval. |
| `captions_ready` | `captions.json` exists and no platform has a live receipt in `post/`. A `post/` folder that holds only `media.json`, `manual.md`, or receipts for posts that were cancelled or lost counts as nothing scheduled. | Show the captions for approval, then ask when to post, as in "post it everywhere tomorrow at 6pm" (`post`). If `receipts:` lists a platform as cancelled, say which one and ask whether to schedule it again. If the script printed `manual:`, the captions are already written for posting by hand in that file, so ask whether they have posted it. |
| `posted` | At least one platform has a receipt with a post id in `post/`. | This video is scheduled or posted, on the platforms `receipts:` lists as scheduled. A cancelled platform is not scheduled any more. A `warning:` about a receipt means that platform is not confirmed, so ask the `post` skill to list what is scheduled. Otherwise start another: "run my research", or pick another number from the latest run. |
| `post_unsure` | `post/` holds a receipt for an attempt that was never confirmed, or one that cannot be read, and no confirmed post. | An earlier try to schedule it was cut off, so Zernio may or may not hold the post. Ask the `post` skill to list what is scheduled: it looks in Zernio before it sends anything, so it is safe to run again. |

A `warning:` line about `take/` means a file there is a link to a raw take that has moved or been deleted. Tell the creator which one, and ask where the file is now. Never copy, move or delete a raw take.

A `receipts:` line lists each platform that has a record in `post/`: `scheduled` (Zernio gave the post an id), `unconfirmed` (a send was started and Zernio never confirmed it), `unreadable` (the record is damaged), `cancelled` (the post was taken down) or `gone` (Zernio no longer had it). It says what the kit did, not what Zernio shows now. For that, ask the `post` skill to list the video.

## Feedback

When the creator corrects how this skill answers, fix it now and record the rule with `kit.rules.add("next", "<rule>")`. In a shell, send the rule on standard input, so an apostrophe, `$` or a backtick in it stays as typed:

```bash
uv run python -m kit.rules add next - <<'RULE'
<rule>
RULE
```

Write one or two plain sentences. If the rule contradicts an older one for this skill, remove the older one first. Say in one line what you recorded.
