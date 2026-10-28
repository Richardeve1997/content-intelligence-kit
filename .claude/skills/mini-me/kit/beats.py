#!/usr/bin/env python3
"""beats.py: the narration's timed words into headline pages, one page per spoken phrase.

    python beats.py work/<video>/words.json            write work/<video>/beats.json beside it
    python beats.py <video>                            the same, by video name
    python beats.py <video> --check                    check an edited beats.json and print its pages
    options: --cta follow|comment --keyword WORD       (default: the profile's call to action)

beats.json is {"duration": seconds, "pages": [...]}, and every page is

    {"t": 3.42, "text": "WEEK THREE", "pose": "facepalm", "field": "light", "prop": "stamp: DAY 21"}

  t      when the page lands, in seconds on the voice-over (the first page is at 0, on screen from frame 0)
  text   the keyword headline of the phrase, in capitals, four words at most. Wrap one word in *stars* to
         put it on an accent plate.
  pose   one of the 14 core poses, chosen for what the phrase says; never the same twice in a row. The closing
         page is idle: the close is a big head-and-shoulders punch-in where the mouth does the work
  field  dark or light, taking turns, so the background flips on every page
  prop   what arrives because of the pose: none, stamp[: TEXT], count (a number of 20 or more counts up from
         10; 1 to 19 show whole, and so do a year such as 2024 and any number on the first page), burst,
         bubble: TEXT,
         card: <a few words or assets/<file>>, or cta (the closing call to action)

How a phrase is found: the words break at the end of a sentence or clause, at a pause of 0.25 s or more,
and before the closing call to action. A phrase holds at most four content words (small words such as
"the" and "of" do not count), so a long one is split where it joins best: before a word like "because" or
"in", after a comma, at a breath, keeping the halves even. A lone short word ("So,") joins the next phrase
rather than flashing on its own, unless it ends the sentence before it (a last word said after a pause), which
joins the phrase it ends. The headline is the whole phrase when it has four words or fewer, and
otherwise its best run of four words (the most content words, a number when there is one, the later run on a
tie). Small words at either end are dropped when two content words are left. From the sentence with the word
"follow" (or the comment keyword) the rest of the narration is one closing page.

Running it again on a beats.json you have edited keeps your version as beats.backup.json (or
beats.backup-2.json and so on), so nothing you wrote is lost.

The poses and props are a first draft from word lists. Read the pages, then sharpen the headlines and the
poses, and name what each card shows, before writing the scenes.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from kit import config, transcribe
from kit.config import KitError

CORE_POSES = ("idle", "talking", "point_left", "point_right", "point_up", "thumbs_up", "shrug", "thinking",
              "mind_blown", "celebrate", "hold_card", "typing", "facepalm", "wave")  # fmt: skip
FIELDS = ("dark", "light")
CLOSE_POSE = "idle"
PROPS = ("none", "stamp", "count", "burst", "bubble", "card", "cta")
MAX_WORDS = 4
MAX_PHRASE_WORDS = 6
MAX_PHRASE_SECONDS = 2.6
PAUSE = 0.25
SHORTEST = 0.45

STOP = set(
    "a an the and or but so of to in on at for with from by as is are was were be been being am it its it's this "
    "that these those i i'm i've i'll i'd you you're you've your yours we we're our they they're their them he she "
    "his her him my me us do does did doing just very really then than there here's here if about up out into over "
    "can will would should could has have had having get got let's let also too some any all each every more most "
    "much such own same only well like".split()
)
JOINERS = {"because": 3, "but": 3, "so": 3, "and": 2, "or": 2, "which": 3, "that": 2, "when": 3, "while": 3,
           "if": 3, "then": 2, "until": 2, "unless": 3, "after": 2, "before": 2, "in": 1.5, "on": 1.5, "at": 1.5,
           "for": 1.5, "with": 1.5, "to": 1, "from": 1.5, "of": 1, "by": 1.5, "into": 1.5, "without": 2}
LEXICON = [
    ("wave", {"hi", "hello", "hey", "thanks", "thank", "cheers", "bye", "welcome"}),
    ("facepalm", {"dies", "die", "died", "dead", "wrong", "mistake", "mistakes", "fail", "fails", "failed",
                  "problem", "problems", "lost", "lose", "broke", "broken", "waste", "wasted", "worst", "annoying",
                  "stuck", "bad", "rot", "rots", "rotting", "wilt", "wilts", "mould", "mold", "kill", "kills", "killed",
                  "ruin", "ruins", "ruined", "oops", "forgot", "missed"}),
    ("mind_blown", {"million", "millions", "billion", "thousand", "thousands", "huge", "crazy", "insane", "wow",
                    "doubled", "tripled", "massive", "incredible", "unbelievable"}),
    ("celebrate", {"finally", "launched", "launch", "harvest", "success", "celebrate", "won"}),
    ("thumbs_up", {"works", "worked", "easy", "free", "best", "better", "win", "done", "yes", "great", "simple",
                   "fixed", "fix", "saved", "save", "fresh", "healthy", "grow", "grows", "grew", "made", "love",
                   "perfect", "good", "right", "cheap", "fast"}),
    ("shrug", {"maybe", "depends", "unsure", "guess", "perhaps", "whatever", "dunno"}),
    ("typing", {"type", "typed", "write", "wrote", "writing", "prompt", "email", "code", "text", "message",
                "search", "google", "app", "notes", "spreadsheet", "chat", "ask", "asked", "online"}),
    ("hold_card", {"checklist", "guide", "plan", "steps", "card", "recipe", "template", "file", "document", "pdf",
                   "list", "label", "schedule"}),
    ("point_up", {"first", "second", "third", "step", "number", "one", "two", "three", "four", "five", "top"}),
    ("point_left", {"this", "here", "these", "look", "see", "watch", "that's", "there"}),
]
QUESTION_WORDS = {"why", "how", "what", "when", "which", "who", "where"}
NEXT_POSE = {"thumbs_up": "celebrate", "facepalm": "shrug", "mind_blown": "celebrate", "thinking": "shrug",
             "point_up": "point_left", "point_left": "point_right", "point_right": "point_left", "talking": "point_left",
             "typing": "hold_card", "hold_card": "point_up", "wave": "talking", "shrug": "thinking",
             "celebrate": "thumbs_up", "idle": "talking"}
DEFAULT_CYCLE = ("talking", "point_left", "point_right", "point_up")
PROP_FOR_POSE = {"thumbs_up": "stamp", "celebrate": "burst", "mind_blown": "burst", "thinking": "bubble: ?",
                 "shrug": "bubble: ?", "facepalm": "bubble: !", "point_left": "card", "point_right": "card",
                 "point_up": "card", "hold_card": "card", "typing": "card"}


def bare(text: str) -> str:
    """A word as compared: lower case, without punctuation other than ' % and $."""
    return re.sub(r"[^a-z0-9'%$]", "", text.lower().replace("’", "'"))


def shown(text: str) -> str:
    """A word as shown in a headline: capitals, sentence punctuation dropped."""
    return re.sub(r"^[^\w$%']+|[^\w%']+$", "", text.replace("’", "'")).upper()


def content(text: str) -> bool:
    word = bare(text)
    return bool(word) and word not in STOP


# --- phrases --------------------------------------------------------------------------------------------------------


CLOSERS = "\"')]}\u2019\u201d\u00bb"  # a closing quote or bracket can follow the mark that ends a sentence


def _ends_with(word: transcribe.Word, marks: str | tuple[str, ...]) -> bool:
    """Whether the word ends with one of `marks`, even when a closing quote or bracket follows it ('why?"')."""
    return word.text.rstrip().rstrip(CLOSERS).endswith(marks)


def _ends_sentence(word: transcribe.Word) -> bool:
    """Whether the word ends its sentence: a full stop, a question mark or an exclamation mark."""
    return _ends_with(word, (".", "!", "?"))


def held_card_measured() -> bool | None:
    """Whether the creator's packed character holds a measured card on its hold_card drawing (the pack writes its
    clear face into the manifest). None when there is no packed character to read."""
    try:
        path = config.me_dir() / "character" / "manifest.json"
    except KitError:
        return None
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return bool(data["poses"]["hold_card"]["frame_info"][0].get("card"))
    except (KeyError, IndexError, TypeError, ValueError, OSError):
        return None


def _cta_start(words: list[transcribe.Word], cta: str, keyword: str | None) -> int | None:
    """Index of the first word of the closing call to action, or None when the narration has none."""
    triggers = {"follow", "following"} if cta != "comment" else {bare(keyword or ""), "comment"}
    triggers.discard("")
    hits = [i for i, w in enumerate(words) if bare(w.text) in triggers]
    if not hits:
        return None
    trigger = hits[-1]
    after = [w for w in words[trigger + 1 :] if _ends_sentence(w)]
    if len(words) - trigger > 25 or len(after) > 2:
        return None  # the word is said early on, not in the closing line
    start = trigger
    # back to the start of its sentence: a full stop, or a pause when the speech model wrote no punctuation
    while start > 0 and not _ends_sentence(words[start - 1]):
        if words[start].start - words[start - 1].end >= PAUSE or words[trigger].start - words[start - 1].start > 2.5:
            break
        start -= 1
    return start


def _chunks(words: list[transcribe.Word], first: int, last: int) -> list[list[int]]:
    """Indexes first..last split at sentence and clause marks and at pauses."""
    out, current = [], []
    for i in range(first, last + 1):
        current.append(i)
        text = words[i].text.rstrip()
        gap = words[i + 1].start - words[i].end if i < last else 0.0
        clause = text.endswith((",", ";", ":")) and len(current) >= 3
        if i == last or _ends_sentence(words[i]) or clause or gap >= PAUSE:
            out.append(current)
            current = []
    return out


def _too_long(words, group: list[int]) -> bool:
    count = sum(1 for i in group if content(words[i].text))
    seconds = words[group[-1]].end - words[group[0]].start
    return len(group) > 1 and (count > MAX_WORDS or len(group) > MAX_PHRASE_WORDS or seconds > MAX_PHRASE_SECONDS)


def _split(words, group: list[int]) -> list[list[int]]:
    if not _too_long(words, group):
        return [group]
    best, best_score = None, None
    for k in range(1, len(group)):
        left, right = group[:k], group[k:]
        c_left = sum(1 for i in left if content(words[i].text))
        c_right = sum(1 for i in right if content(words[i].text))
        if c_left == 0 or c_right == 0:
            continue
        gap = words[right[0]].start - words[left[-1]].end
        short = sum(1 for side in (left, right) if words[side[-1]].end - words[side[0]].start < 0.6)
        score = (4 * gap + JOINERS.get(bare(words[right[0]].text), 0)
                 + (3 if words[left[-1]].text.rstrip().endswith(",") else 0)
                 - 0.8 * abs(c_left - c_right) - 0.15 * abs(len(left) - len(right))
                 - 3 * short)  # a page on screen for under 0.6 s flashes by
        if best_score is None or score > best_score:
            best, best_score = k, score
    if best is None:
        best = len(group) // 2
    return _split(words, group[:best]) + _split(words, group[best:])


def _join_short(words, groups: list[list[int]]) -> list[list[int]]:
    """A one-word phrase said in under SHORTEST seconds joins the next phrase (or the one before, at the end).

    A short word that ends a sentence whose start is in the phrase before ("nobody knows ... why.", the last
    word said after a pause) joins that phrase instead, so it never heads the next sentence's page."""
    out: list[list[int]] = []
    carry: list[int] = []
    for group in groups:
        group = carry + group
        carry = []
        short = len(group) == 1 and words[group[0]].end - words[group[0]].start < SHORTEST
        if short and out and _ends_sentence(words[group[0]]) and not _ends_sentence(words[out[-1][-1]]):
            out[-1] = out[-1] + group
            continue
        if short:
            carry = group
            continue
        out.append(group)
    if carry:
        if out:
            out[-1] = out[-1] + carry
        else:
            out.append(carry)
    return out


def phrases(words: list[transcribe.Word], *, cta: str = "follow", keyword: str | None = None) -> list[list[int]]:
    """The spoken phrases, as lists of word indexes in order. Together they hold every word once."""
    if not words:
        return []
    closing = _cta_start(words, cta, keyword)
    end = (closing - 1) if closing is not None else len(words) - 1
    groups: list[list[int]] = []
    if end >= 0:
        for chunk in _chunks(words, 0, end):
            groups += _split(words, chunk)
        groups = _join_short(words, groups)
    if closing is not None:
        groups.append(list(range(closing, len(words))))
    return groups


# --- pages -------------------------------------------------------------------------------------------------------------


def _span_score(texts: list[str], a: int, b: int) -> tuple:
    piece = texts[a:b]
    count = sum(1 for t in piece if content(t))
    digits = any(re.search(r"\d", t) for t in piece)
    edges = (not content(piece[0])) + (not content(piece[-1]))
    return (count, digits, -edges, b == len(texts), a)


def headline(words, group: list[int]) -> str:
    """The phrase's headline: the whole phrase when it has four words or fewer, else its best run of four
    (the most content words, a number if there is one, the later run on a tie). Small words at either end
    are dropped when two content words are left."""
    texts = [words[i].text for i in group]
    if len(texts) > MAX_WORDS:
        spans = [(a, b) for a in range(len(texts)) for b in range(a + 1, min(len(texts), a + MAX_WORDS) + 1)]
        a, b = max(spans, key=lambda span: _span_score(texts, *span))
        texts = texts[a:b]
    if sum(1 for t in texts if content(t)) >= 2:
        while not content(texts[0]):
            texts = texts[1:]
        while not content(texts[-1]):
            texts = texts[:-1]
    return " ".join(w for w in (shown(t) for t in texts) if w)


def _next_pose(pose: str, hold_card: bool) -> str:
    """The pose after `pose` when the same one would repeat. hold_card only when the character's card was measured."""
    following = NEXT_POSE.get(pose, "talking")
    return "point_up" if following == "hold_card" and not hold_card else following


def _pose(words, group: list[int], index: int, previous: str | None, hold_card: bool = True) -> str:
    """The pose for a phrase. `hold_card` says whether the character holds a measured card: without one, a plan or
    a checklist is shown on a card that slides in beside a pointing pose instead of the blank card hold_card holds."""
    said = [bare(words[i].text) for i in group]
    if index == 0:
        pose = "point_up"
    elif _ends_with(words[group[-1]], "?") or (said and said[0] in QUESTION_WORDS and len(said) > 1):
        pose = "thinking"
    else:
        pose = next((name for name, lexicon in LEXICON if any(w in lexicon for w in said)), None)
        if pose == "hold_card" and not hold_card:
            pose = "point_left"
        if pose is None and any(re.search(r"\d", w) for w in said):
            pose = "point_up"
        if pose is None:
            cycle = [p for p in DEFAULT_CYCLE if p != previous]
            pose = cycle[index % len(cycle)]
    if pose == "point_left" and previous == "point_left":
        pose = "point_right"
    if pose == previous:
        pose = _next_pose(pose, hold_card)
    return pose


NUMBER = re.compile(r"^\$?(\d[\d,]*(?:\.\d+)?)%?$")
COUNT_FROM = 10  # where a count starts
COUNT_MIN = 20  # a number under this shows whole: counting to 3 would show 1 and 2 first, and 12 would show 10 and 11


YEAR = re.compile(r"(19|20)\d\d")  # four plain digits that read as a year: never counted up from 10


def counted_token(text: str) -> str | None:
    """The number a count would count, as written in the headline: the accented one (between stars), else the first.
    None when there is none."""
    accented, first, inside = None, None, False
    for raw in str(text).split():
        if raw.startswith("*"):
            inside = True
        token = raw.strip("*").rstrip(".,!?;:")
        if NUMBER.match(token):
            first = token if first is None else first
            if inside and accented is None:
                accented = token
        if len(raw) > 1 and raw.endswith("*"):
            inside = False
    return accented if accented is not None else first


def counted_number(text: str) -> float | None:
    """The number a count would count, as a number. None when there is none."""
    token = counted_token(text)
    if token is None:
        return None
    value = float(NUMBER.match(token).group(1).replace(",", ""))
    return int(value) if value == int(value) else value


def is_year(token: str | None) -> bool:
    """Whether a counted token reads as a year (1900 to 2099, written plain: no sign, comma or decimals)."""
    return bool(token) and YEAR.fullmatch(token) is not None


def _prop(text: str, pose: str, first_page: bool = False) -> str:
    number = counted_number(text)
    if number is not None and number >= COUNT_MIN and not first_page and not is_year(counted_token(text)):
        return "count"
    return PROP_FOR_POSE.get(pose, "none")


def _cta_text(cta: str, keyword: str | None) -> str:
    if cta == "comment" and keyword:
        return f"COMMENT {shown(keyword)}"
    return "FOLLOW"


def _profile_cta() -> tuple[str, str | None]:
    try:
        profile = config.load_profile()
    except KitError:
        return "follow", None
    if profile.cta.type == "comment" and profile.cta.keyword:
        return "comment", profile.cta.keyword
    return "follow", None


def build(words: list[transcribe.Word], *, cta: str | None = None, keyword: str | None = None,
          duration: float | None = None, held_card: bool | None = None) -> dict:
    """beats.json for these words. With no `cta`, the call to action comes from the creator's profile. `held_card`
    says whether the character holds a measured card (read from the packed character when not given): without one,
    hold_card is never drafted, because its card would drop in beside a blank card."""
    if not words:
        raise KitError("The words file has no words, so there is nothing to put on the pages.")
    if cta is None:
        cta, keyword = _profile_cta()
    usable = bool(held_card_measured()) if held_card is None else bool(held_card)
    groups = phrases(words, cta=cta, keyword=keyword)
    closing = _cta_start(words, cta, keyword)
    pages, previous = [], None
    for index, group in enumerate(groups):
        is_cta = closing is not None and group[0] == closing
        text = _cta_text(cta, keyword) if is_cta else headline(words, group)
        if is_cta:
            pose = CLOSE_POSE  # the stage's own first choice for the close, so the draft is what renders
            if pages and pages[-1]["pose"] == CLOSE_POSE:
                before = pages[-2]["pose"] if len(pages) > 1 else None
                pages[-1]["pose"] = next(p for p in ("talking", "point_left", "point_right") if p != before)
        else:
            pose = _pose(words, group, index, previous, usable)
            if pose == previous:
                pose = _next_pose(pose, usable)
        pages.append({
            "t": 0.0 if index == 0 else round(words[group[0]].start, 3),
            "text": text,
            "pose": pose,
            "field": FIELDS[index % 2],
            "prop": "cta" if is_cta else _prop(text, pose, first_page=index == 0),
        })
        previous = pose
    length = duration if duration is not None else words[-1].end + 0.3
    return {"duration": round(max(length, words[-1].end), 3), "pages": pages}


# --- checking an edited beats.json --------------------------------------------------------------------------------------


def prop_kind(prop: str) -> str:
    return str(prop).split(":", 1)[0].strip().lower()


def check(beats: dict, held_card: bool | None = None) -> list[str]:
    """Problems with a beats.json, as sentences. Lines starting "note:" are worth a look but do not stop a build.
    `held_card` says whether the character holds a measured card (read from the packed character when not given):
    a page that pairs hold_card with a card while none was measured is a problem, not a note."""
    usable = held_card_measured() if held_card is None else bool(held_card)
    problems: list[str] = []
    pages = beats.get("pages") if isinstance(beats, dict) else None
    if not isinstance(pages, list) or not pages:
        return ["beats.json has no pages. Make it with beats.py from words.json."]
    previous = None
    for n, page in enumerate(pages, 1):
        if not isinstance(page, dict):
            problems.append(f"page {n} is not a page. Each page is {{t, text, pose, field, prop}}.")
            continue
        missing = [key for key in ("t", "text", "pose", "field", "prop") if key not in page]
        if missing:
            problems.append(f"page {n} has no {', '.join(missing)}.")
            continue
        words = str(page["text"]).replace("*", "").split()
        if not words:
            problems.append(f"page {n} has no headline.")
        elif len(words) > MAX_WORDS:
            problems.append(f'page {n} ("{page["text"]}") has more than 4 words. Keep the keyword of the phrase.')
        if page["pose"] not in CORE_POSES:
            problems.append(f'page {n} has the pose {page["pose"]}, which is not one of the 14 core poses: '
                            + ", ".join(CORE_POSES) + ".")
        if page["field"] not in FIELDS:
            problems.append(f'page {n} has the field {page["field"]}. Use dark or light.')
        kind = prop_kind(page["prop"])
        if kind not in PROPS:
            problems.append(f'page {n} has the prop {page["prop"]}, which the stage does not know. '
                            "Use one of: none, stamp, count, burst, bubble, card, cta.")
        elif kind == "count":
            token, number = counted_token(page["text"]), counted_number(page["text"])
            if number is None:
                problems.append(f"note: page {n} counts, but its headline has no number.")
            elif n == 1:
                problems.append("note: page 1 shows its number whole, so the first frame reads right.")
            elif is_year(token):
                problems.append(f"note: page {n} counts {token}, which reads as a year, so it shows whole instead.")
            elif number < COUNT_MIN:
                problems.append(f"note: page {n} counts {number}, which is under 20, so it shows whole instead.")
        if page["pose"] == "hold_card" and kind == "card" and usable is False:
            problems.append(f"page {n} pairs hold_card with a card, but no card was measured on the character's hold_card "
                            "drawing, so the card would drop in beside a blank card. Give the page another pose, or draw "
                            "hold_card again with a large plain white card and pack again.")
        elif kind in ("card", "bubble") and ":" not in str(page["prop"]):
            problems.append(f"note: page {n} has a {kind} with nothing on it yet. Name what it shows, "
                            f"such as {kind}: <a few words>" + (" or card: assets/<file>.png." if kind == "card" else "."))
        t = page["t"]
        if not isinstance(t, (int, float)):
            problems.append(f"page {n} has no time in seconds.")
        elif n == 1 and t != 0:
            problems.append(f"page 1 starts at {t} s. The first page is on screen from frame 0, so its t is 0.")
        elif previous is not None:
            if t <= previous["t"]:
                problems.append(f"page {n} starts at {t} s, before the page before it ({previous['t']} s).")
            elif t - previous["t"] < SHORTEST - 1e-9:
                problems.append(f"note: page {n - 1} is on screen for only {t - previous['t']:.2f} s. "
                                "Join it to a neighbour.")
            if page["field"] == previous["field"]:
                problems.append(f"note: page {n} is on the same field as the page before. The fields take turns.")
            if page["pose"] == previous["pose"]:
                problems.append(f"note: page {n} repeats the pose of the page before. Pick a new pose every page.")
        previous = page
    duration = beats.get("duration")
    if not isinstance(duration, (int, float)) or (previous and duration <= previous["t"]):
        problems.append("beats.json needs a duration in seconds, after the last page starts.")
    return problems


# --- command line -------------------------------------------------------------------------------------------------------


def backup(path: Path) -> Path:
    """Copy `path` beside itself as name.backup.json, or name.backup-2.json when that is taken, and so on."""
    stem, number = path.stem, 1
    while True:
        target = path.with_name(f"{stem}.backup{'' if number == 1 else f'-{number}'}{path.suffix}")
        if not target.exists():
            target.write_bytes(path.read_bytes())
            return target
        number += 1


def _paths(target: str) -> tuple[Path, Path]:
    path = Path(target)
    if path.suffix == ".json" or path.is_file():
        return path, path.with_name("beats.json")
    folder = config.work_dir(target)
    return folder / "words.json", folder / "beats.json"


def show(beats: dict) -> None:
    pages = beats["pages"]
    ends = [p["t"] for p in pages[1:]] + [beats["duration"]]
    print(f"{'#':>3} {'t':>7} {'len':>5}  {'field':5}  {'pose':11}  {'prop':18}  headline")
    for n, (page, end) in enumerate(zip(pages, ends), 1):
        print(f"{n:>3} {page['t']:7.2f} {end - page['t']:5.2f}  {page['field']:5}  {page['pose']:11}  "
              f"{str(page['prop'])[:18]:18}  {page['text']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Headline pages from the narration's words.")
    ap.add_argument("target", help="work/<video>/words.json, or the video name")
    ap.add_argument("--out", type=Path, help="where to write beats.json (default: beside words.json)")
    ap.add_argument("--cta", choices=("follow", "comment"), help="the call to action (default: the profile's)")
    ap.add_argument("--keyword", help="the comment keyword (default: the profile's)")
    ap.add_argument("--check", action="store_true", help="check the existing beats.json instead of writing one")
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        words_path, beats_path = _paths(a.target)
        beats_path = a.out or beats_path
        if a.check:
            if not beats_path.is_file():
                print(f"{beats_path} does not exist yet. Run beats.py without --check first.")
                return 1
            data = json.loads(beats_path.read_text(encoding="utf-8"))
        else:
            if not words_path.is_file():
                print(f"{words_path} does not exist. Make the voice-over first: python -m kit.voice auto <video>.")
                return 1
            raw = json.loads(words_path.read_text(encoding="utf-8"))
            words = transcribe.load(words_path)
            keyword = a.keyword
            cta = a.cta or (None if keyword is None else "comment")
            data = build(words, cta=cta, keyword=keyword, duration=raw.get("dur") if isinstance(raw, dict) else None)
            text = json.dumps(data, indent=1, ensure_ascii=False) + "\n"
            if beats_path.is_file() and beats_path.read_text(encoding="utf-8") != text:
                print(f"kept the pages that were there as {backup(beats_path).name}")
            beats_path.write_text(text, encoding="utf-8")
    except (KitError, ValueError, OSError) as problem:
        print(problem)
        return 1
    show(data)
    problems = check(data)
    for line in problems:
        print(("  " if line.startswith("note:") else "ERROR ") + line)
    errors = [p for p in problems if not p.startswith("note:")]
    if not a.check:
        print(f"wrote {beats_path} ({len(data['pages'])} pages)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
