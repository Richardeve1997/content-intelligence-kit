---
name: next
description: Say what the next step is for the video in progress. Use when the creator says "what's next", "what now", "where are we", "continue", "keep going", "pick up where we left off", "what should I do next", or sends any vague forward-moving message without naming a step. It only reads the newest folder in work/ and answers. It never starts a render or any other heavy job.
---

# next

Looks at the newest video folder in `work/`, works out which files exist, and names the next step. It reads files and answers. It starts nothing heavy: a render, an encode or a transcription waits for the creator's go-ahead and runs through the skill that owns it.

## Steps

1. Read the creator's rules for this skill with `kit.rules.read("next")`. In a shell: `uv run python -m kit.rules read next`. Follow them.
2. Run this from the kit folder. It prints the video it looked at, its state and the files it found, and uses only the standard library.

```python
import json
import os
from pathlib import Path

work = Path("work")
folders = [p for p in work.iterdir() if p.is_dir() and not p.name.startswith(".")] if work.is_dir() else []
folders.sort(key=lambda p: max(c.lstat().st_mtime for c in [p, *p.iterdir()]), reverse=True)
slug = os.environ.get("SLUG") or (folders[0].name if folders else None)
folder = work / slug if slug else None
take = folder / "take" if folder else None
take_files = [f for f in take.iterdir() if not f.name.startswith(".")] if take and take.is_dir() else []


def has(name):
    return folder is not None and (folder / name).exists()


if folder is None:
    state = "no_video"
elif not folder.is_dir():
    state = "no_such_video"
elif not has("idea.json"):
    state = "no_idea"
elif has("final.mp4") and has("post"):
    state = "posted"
elif has("final.mp4") and has("captions.json"):
    state = "captions_ready"
elif has("final.mp4"):
    state = "final_ready"
elif has("script.md"):
    state = "mini_me_in_progress"
elif has("plan.md") and take_files:
    state = "take_ready"
elif has("plan.md"):
    state = "needs_take"
else:
    state = "needs_plan"

print(f"video: {slug or 'none'}")
print(f"state: {state}")
markers = ["idea.json", "plan.md", "script.md", "take", "final.mp4", "captions.json", "post"]
print("found: " + (", ".join(m for m in markers if has(m)) or "nothing"))
for f in take_files:
    if f.is_symlink() and not f.exists():
        print(f"warning: take/{f.name} points to a file that is not there any more")
if has("idea.json"):
    try:
        idea = json.loads((folder / "idea.json").read_text(encoding="utf-8"))
        print(f"idea: {idea['n']}, {idea['title']}")
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
| `no_idea` | The folder has no `idea.json`. | The kit does not know which idea this video belongs to. Pick an idea from the latest research run, or say "run my research". |
| `needs_plan` | The idea is saved. Nothing is planned yet. | Pick a style for the idea the script printed: "plan the video for number N" (`ui-story`) or "make number N a mini me video" (`mini-me`). |
| `needs_take` | `plan.md` is written and `take/` has no file. | Film the take with the film guide in `plan.md`, then say "here's my raw take, edit it". Filming is the creator's job, so stop here. |
| `take_ready` | `take/` holds a take and there is no `final.mp4`. | Edit it (`ui-story`). |
| `mini_me_in_progress` | `script.md` is written and there is no `final.mp4`. | The narration is ready. The voice-over and the build come next (`mini-me`). |
| `final_ready` | `final.mp4` exists and there are no captions. | The video is finished. Captions come next. The `post` skill writes one per platform for approval. |
| `captions_ready` | `captions.json` exists and nothing is scheduled. | Show the captions for approval, then ask when to post, as in "post it everywhere tomorrow at 6pm" (`post`). |
| `posted` | `post/` exists. | This video is posted or scheduled. Start another: "run my research", or pick another number from the latest run. |

A `warning:` line means a file in `take/` is a link to a raw take that has moved or been deleted. Tell the creator which one, and ask where the file is now. Never copy, move or delete a raw take.

## Feedback

When the creator corrects how this skill answers, fix it now and record the rule with `kit.rules.add("next", "<rule>")`. In a shell: `uv run python -m kit.rules add next '<rule>'`. Write one or two plain sentences. If the rule contradicts an older one for this skill, remove the older one first. Say in one line what you recorded.
