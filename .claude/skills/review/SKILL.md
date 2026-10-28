---
name: review
description: Review a finished Reel before it is called done. It runs the gates (speech, loudness, captions, still frames, safe area), lays the video out as a contact sheet, looks at every frame against the house rubric, and returns a numbered list of fixes, each sent back to the step that owns it. Then it reviews again, at most 5 cycles. Both styles end with it. Use when the creator says "review it", "check the video", "is it good to ship", "does this pass", "grade the final" or "look over the Reel", or when the ui-story or mini-me skill reaches its review step.
---

# Review

Looks at the finished file, `work/<video>/final.mp4`, the way a viewer meets it: sped up, with the music bed, cropped by a tall phone. A render can pass every automated check and still show an empty card or a head cut off at the crown, so the frames are the evidence. Read them.

## Steps

1. Read the creator's rules for this skill with `kit.rules.read("review")`. In a shell: `uv run python -m kit.rules read review`. Apply them as extra rubric items.
2. Run the gates. A UI story Reel: `uv run python .claude/skills/ui-story/kit/postcheck.py <video>`. Any other video:
   - the speech gate, `uv run python -m kit.qa_captions work/<video>/final.mp4 --words <its caption words> --speed <its finish speed>` (it uses `kit.qa_captions` to hear every caption word in the final audio)
   - loudness, `uv run python -m kit.finish measure work/<video>/final.mp4`: -13 LUFS within 1 LU, true peak at or below -1.5 dBTP
   A failed gate is fixed before the visual review. A caption word the audio never says is a lost word, never noise from the speech model.
3. Make the sheet: `uv run python bin/review_frames.py work/<video>/final.mp4`. It writes `work/<video>/review/sheet.jpg` (one frame a second, each stamped with its time) and `open.jpg` (the frames at 0.2, 1, 2, 3, 4 and 5 seconds). Add `--fps 2` for a short or fast Reel.
4. Look at `open.jpg`, then `sheet.jpg`. Write down what is wrong beat by beat before reading the rubric, then check every item below. For a doubtful moment, pull one full-size frame: `ffmpeg -v error -ss <t> -i work/<video>/final.mp4 -frames:v 1 work/<video>/review/t<t>.png`, and look at that.
5. Return a numbered list of issues, or "no issues". Each issue gives the time, the rubric item, what is wrong, the fix and the step to re-run. For example:
   `1. 4.5 s (item 1): the subject card never appears; the proof card holds to 6 s. Fix: add the subject card on "this is". Re-run: scenes, render, ship.`
6. Send each fix to the step that owns it: a scene or a card to the composition and its render, a cut or a trim to the EDL or `blocks.json`, the bed or the level to the finish. After a rebuild the gates run again, and the review looks again only at the windows that changed.
7. Repeat for at most 5 cycles. After the fifth, give the creator the open issues and ask whether to ship as it is.
8. On a clean pass, write `work/<video>/review/review.json` as `{"passed": true, "date": "YYYY-MM-DD", "cycles": N}`. Write it only when the current file passed. A new final deletes it (`ship.py` does this before it replaces `final.mp4`), so the next review starts from no pass.

## The rubric

Times are on the finished file, after the speed-up. Items 2 to 5 are the UI story layout; a mini me video swaps them for its own look (two flat fields, a headline page per phrase, the character big and cropped at the waist, no caption track).

1. The open. A viewer with the sound off can say what the video is about from `open.jpg`. The picture is already moving at frame 0: no logo card, no blank or black frame. In UI story the first stretch is a motion graphic of the problem, then one proof card of about seven words with a real source, then the subject card by about 4.5 s.
2. Layouts. Split frames show the rounded PIP with the creator's head breaking out cleanly above its edge: no clipped crown, no halo, no card ink under the head or across the eyes and mouth. Canvas frames hold a diagram, an icon list or an explainer that fills its zone, never a text card. Face frames show the creator full screen with nothing over the face, and their punch-ins look sharp.
3. Captions. One line centred at y 1570 in every layout. Exactly one word sits on a plate as it is spoken; the group is white with an ink halo over the creator and ink over the canvas. The single plate is correct, never a stray box. Captions never sit over a card and never blink off for a frame (`caption_blink_check.py`).
4. Pace. A new visual about every two seconds. Nothing in split or canvas holds still for over 3 s (`check_static.py`). No face stretch longer than about 5 s without a chip or a punch-in.
5. Reading load. One idea and about six words per visual, on screen long enough to read (0.35 s a word plus 0.6 s). No screen recording of scrolled or highlighted text: text sources are designed quote, headline or stat cards.
6. Real things. Every product, person or company named shows the real thing (its UI, footage, logo or post), when it is said. Every number on screen matches the number spoken, and every source is listed in the video's `SOURCES.md`. No grey credit lines on screen.
7. Brand. Chips, rings, ticks and outlines use the creator's accent and nothing else. No colour outside their tokens appears except in real product art and footage. The fonts are theirs.
8. Safe area. Nothing meaningful within 90 px of the left or right edge (a tall phone crops about 45 px a side; `ig_safe_check.py` marks it) or in the top 150 px under the status bar. Nothing overflows the frame, overlaps, or needs zooming in to read at phone size.
9. The end. The Reel ends on the last spoken word or a natural sign-off, with no outro card. The closer shows the creator's call to action: their keyword typed into a comment box, or their profile with Follow pressed.
10. Sound. -13 LUFS within 1 LU, true peak at or below -1.5 dBTP. When there is a bed, it sits under the voice and fades out at the end instead of stopping dead. No pause over about 0.45 s between sentences, and no twitch or noise at a take join inside a face window.
11. Speech. The speech gate passed on this file after the last rebuild. No sentence was stitched together from fragments to change what the creator said.

## Common mistakes

- Reviewing a test window or the composite instead of `final.mp4`. Speed, the bed and the loudness exist only in the finished file.
- Flagging the missing outro. Ending on the last word is the standard.
- Flagging the single karaoke plate as a stray dark box.
- Trusting a clean set of gates as proof of a good video. Look at the frames.
- Fixing the video inside this skill. The review grades and routes; fixes happen in the step that owns them.
- Extracting every frame again after a fix to one window. Look again only where the change landed.

## Feedback

When the creator disagrees with a verdict or adds something to check, fix the current review first. Then record the lasting part with `kit.rules.add("review", "<rule>")`. In a shell: `uv run python -m kit.rules add review '<rule>'`. Write one or two plain sentences, with no dates and no quotes from the creator. If it belongs to how a style builds a video rather than how it is checked, record it for that skill too (`ui-story` or `mini-me`). If it contradicts an older rule for this skill, remove the older one first: `uv run python -m kit.rules read review --numbered` shows the numbers, and `uv run python -m kit.rules remove review <number>` removes one. Say in one line what you recorded.
