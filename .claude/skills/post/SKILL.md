---
name: post
description: Write one caption per platform for a finished video, show every caption for approval, then schedule the video on Instagram, TikTok, YouTube, Facebook, LinkedIn and X through Zernio. Use when the creator says "post it everywhere tomorrow at 6pm", "schedule this", "write the captions", "post it on LinkedIn", "move the TikTok to Friday", "cancel the Instagram one" or "what is scheduled". Without a Zernio key it writes the captions and a file for posting by hand.
---

# post

This skill takes a finished video, `work/<slug>/final.mp4`, to scheduled posts. You write a caption for each platform in the creator's voice and show all of them. When the creator says yes, the kit uploads the video once and schedules one post per platform through Zernio, a paid posting service the creator signs up for and pays for themselves. Each post has its own caption and time, and can be cancelled on its own. Without a Zernio key, the same captions go into a file for posting by hand.

You drive the kit with `uv run python -m kit.post <command>`. A command prints plain sentences. A problem comes back as one sentence and exit status 1. Never print the Zernio key, copy it or write it into a file. It lives in `.env`.

## Steps

1. Read the creator's rules for this skill with `kit.rules.read("post")`. In a shell: `uv run python -m kit.rules read post`. Follow them.
2. Find the video. Use the name the creator gave. If they gave none, use the newest folder in `work/` that holds a `final.mp4`, and ask when two are close. A folder without a `final.mp4` is not finished. Say so and offer the earlier step (`next` knows which).
3. See what is connected with `uv run python -m kit.post check`. A platform that is not connected in Zernio is skipped and named in the report, and the kit never posts to it another way. Tell the creator to connect it inside Zernio. If the command says there is no key, say once that the result will be the captions and a file for posting by hand, and carry on.
4. Get the closing lines and tags. Pick two or three hashtags that fit this video from its idea and script, then run `uv run python -m kit.post lines --tags 'basil windowsill herbs'`, with your own tags. For each platform it prints the closing line to use, the hashtags (the creator's base tags first, then yours) and how many that platform takes.
5. Write a caption for each platform, following "The captions" below. Draw on `me/voice/VOICE.md`, `me/profile.json` and what the video says (`script.md` or `plan.md` in its folder). Save them to `work/<slug>/captions.json`, one entry for each platform in the profile:

```json
{
  "instagram": {"title": null, "body": "The claim.\n\nThe closing line.", "hashtags": ["#one", "#two"]},
  "youtube": {"title": "At most 100 characters", "body": "The description.\n\nThe closing line.", "hashtags": ["#one"]}
}
```

6. Read `.claude/skills/humanizer/SKILL.md` and apply it in embedded mode to every caption. Keep the facts, the keyword and the meaning of the closing line. Use no em dashes or en dashes.
7. Run `uv run python -m kit.post captions <slug>`. Fix each problem it lists, apply the humanizer again to the words you changed, and run it again until it says all captions pass.
8. Show the creator every caption exactly as it will be posted: the text, then the tag line, with the YouTube title on its own, and the time each one goes out. Beside the TikTok caption, show the settings their yes covers: the post is public to everyone, and comments, duets and stitches are on. Then stop and wait. Saying "post it" approves the video, not captions they have not read. Never run `approve` or `schedule` until the creator has read every caption and said yes in this conversation, and never call the Python function directly. Their yes to the TikTok caption and those settings is also TikTok's preview and consent: the kit sets the consent flags only because the creator approved that caption. If they change a word, change it, run step 7 again and show the new version.
9. When they say yes, record it with `uv run python -m kit.post approve <slug>`. The approval covers these exact words. Any later change undoes it, and `schedule` refuses to send until the creator approves again.
10. Turn their words into a time. `me/profile.json` holds their `timezone` and their usual `posting_times`, and "6pm" always means their own 6pm. "Tomorrow at 6pm" is `--on tomorrow --time 18:00`. A named day needs its date, as in `--on 2026-10-30 --time 09:00`. When they give no time ("post it everywhere tomorrow"), leave `--time` off and each platform goes at its usual time. Ask when a platform has no usual time. Add `--platforms instagram tiktok` for fewer platforms. "Everywhere" and "all platforms" mean every platform in the profile. The kit refuses a time less than five minutes ahead, because Zernio posts at once when a time has passed.
11. Schedule it with `uv run python -m kit.post schedule <slug> --on tomorrow --time 18:00`. Add `--dry-run` to show what would happen without sending anything, which suits a creator who wants to see the plan first. A dry run prints each platform's post under its line as it would be sent (the text with its tags, the YouTube title, the TikTok settings and the time), with placeholders where the Zernio account and the uploaded video's address will go.
12. Report one line for each platform from what the command printed: scheduled and when, skipped and why, or failed and why. Say what to do about each failure, using "When something fails".

## The captions

- Write in the creator's voice and keep it short: the claim, one line of substance, then the closing line. Use only what the video says. Do not add numbers, results or claims it does not make.
- The closing line is the last line of the body. Use the one `lines` printed, or the creator's own wording of it. It asks for the comment keyword only on platforms where the creator answers comments, and it is a follow line everywhere else. Never ask for the keyword where nothing answers it.
- Hashtags go in the `hashtags` list, not in the text. Instagram, TikTok and Facebook take at most 5, YouTube 8, X 2 and LinkedIn none. Never use #shorts, #fyp or #foryou.
- YouTube needs a title of at most 100 characters, with no hashtag in it. Its body is the description.
- X takes at most 280 characters for the text and the hashtags together. X counts each link as 23 characters and each emoji as 2. It also counts a character outside the usual Latin ranges as 2, such as a Chinese character, the one-character ellipsis, an arrow, a bullet or the trademark sign, so copy with those uses up more room than it looks. The caption check applies X's counting.
- LinkedIn gets its own copy, never the Instagram caption pasted across, because what Instagram rewards cuts LinkedIn's reach. Leave out hashtags, links of any kind (anything shaped like a web address, with or without https://) and any phrase that asks for comments, likes or tags, such as "comment below", "like for part 2", "tag someone" or "link in bio". Put the claim in the first 150 characters, since only those show before "See more". A real question is welcome. Use plain first person and short paragraphs.
- LinkedIn always ends on a follow line, even when the profile lists it among the platforms where the creator answers comments, because comment prompts cut its reach. The caption check reports that setting as a problem until `linkedin` is taken out of `cta.comment_platforms` in `me/profile.json`.
- Leave no placeholder in the text, such as `<keyword>`.

## When something fails

- Skipped with "not connected in Zernio": the creator connects that platform inside Zernio and asks again. The other platforms are not affected.
- A dropped connection: run the same `schedule` command again. The kit first looks in Zernio for a post with that title and uses it if it finds one. If it finds none, it sends the same request again, and Zernio treats a repeat of that request as one post for 24 hours. After 23 hours with no post found, the kit stops and says to check Zernio, because sending it again could make a second post.
- Zernio gives its own reason in the sentence, such as an account that needs reconnecting or a video it will not take. Pass it on in plain words.
- A line that ends "An earlier attempt may already have made this post, so the next run looks for it before it sends anything" means Zernio refused or turned away a resend. The kit keeps the record in `work/<slug>/post/` on purpose, because the first attempt may have made the post, and the next run looks for that post before it sends anything. If Zernio turned the resend away for a passing reason, such as a rate limit, run the same `schedule` command again later. If it refused the post itself, such as a caption it will not take, fix that reason first (edit the caption, show it to the creator and approve it again), then run `schedule`. Do not delete the record to get past this refusal. Deleting it is only for the message about an attempt "sent more than 23 hours ago", once Zernio has been checked as that message says.
- "Already scheduled" means an earlier run made the post and nothing was repeated. When the line also says Zernio still holds the earlier text, the caption was edited after the post was made. Cancel that post and schedule it again to use the new words.
- A post that failed inside Zernio has to be cancelled before it is scheduled again.

## After it is scheduled

- `uv run python -m kit.post list <slug>` shows each platform as Zernio reports it now, with the address once a post is live.
- `uv run python -m kit.post reschedule <slug> tiktok --on 2026-10-30 --time 09:00` moves one post. Only a post that has not started to publish can move.
- `uv run python -m kit.post cancel <slug> tiktok` cancels one. Scheduling it again afterwards makes a fresh post.
- `reschedule` and `cancel` also reach a post whose create was interrupted, by finding it in Zernio under its title. If Zernio has no post with that title, they say so and change nothing. `list` looks the same way, and says when it found a post after an interrupted attempt.
- To change the words of a scheduled post, cancel it, edit `captions.json`, show the creator, approve and schedule again.

## Without a Zernio key

Once the creator has approved the captions, `schedule` writes `work/<slug>/post/manual.md` and prints one sentence saying so. The file holds each platform's caption exactly as it should be pasted, the YouTube title, the video's path and the day and time to post each one: the time the creator asked for, or their usual time when they gave none. Tell the creator in one sentence that the file is ready. Say that a Zernio key in `.env` lets the kit schedule the posts itself. `uv run python -m kit.post manual <slug>` writes the file without trying to schedule, with each platform's usual time.

## Feedback

When the creator corrects how this skill behaves, fix it now and record the rule with `kit.rules.add("post", "<rule>")`. In a shell, send the rule on standard input, so an apostrophe, `$` or a backtick in it stays as typed:

```bash
uv run python -m kit.rules add post - <<'RULE'
<rule>
RULE
```

Write one or two plain sentences, 300 characters at most, with no dates and no quotes from the creator. If the rule contradicts an older one for this skill, remove the older one first. Say in one line what you recorded.
