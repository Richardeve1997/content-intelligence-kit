"""Captions for each platform: the rules they follow, the lines and tags the kit proposes, and the
creator's approval of the words.

work/<slug>/captions.json holds one entry for each platform in the profile:

    {"instagram": {"title": null, "body": "...", "hashtags": ["#tag"]}, ...}

The body ends on the call to action. The hashtags sit in their own list, so the kit can count them, keep
them off LinkedIn and place them after a blank line. `check` returns one plain sentence for each rule a
caption breaks, and an empty list means it can be scheduled.

The rules:

* The call to action. The comment keyword appears only on the platforms where the creator answers
  comments (cta.comment_platforms, when cta.type is "comment"). Every other platform ends on a follow
  line. LinkedIn always ends on a follow line, because a request for a comment keyword cuts its reach,
  so listing it in cta.comment_platforms is reported as a problem.
* Hashtags. At most 5 on Instagram, TikTok and Facebook, 8 on YouTube, 2 on X and none on LinkedIn.
  #shorts, #fyp and #foryou are never used. The tags come from the profile's base tags plus two or
  three for the video. They go in the hashtags list, not in the text or in a YouTube title.
* LinkedIn. No hashtags, no links (anything shaped like a web address, with or without https://), none
  of the phrases that ask for comments, likes or tags, and the claim inside the first 150 characters,
  which is all that shows before See more.
* X. The text and the tags together fit in 280 characters as X counts them: each link counts 23, each
  emoji counts 2 and every other character counts 1.
* YouTube. A title of at most 100 characters.
* No em or en dashes, and no placeholder left in the text.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping

from kit import config
from kit.post import payloads, store
from kit.post.errors import PostError

__all__ = [
    "HASHTAG_CAPS",
    "BASE_FIRST",
    "NEVER_TAGS",
    "X_LIMIT",
    "LINKEDIN_VISIBLE",
    "x_length",
    "links_in",
    "display_name",
    "answers_comments",
    "cta_line",
    "select_hashtags",
    "check",
    "load",
    "fingerprint",
    "approve",
    "approval_problem",
]

HASHTAG_CAPS = {"instagram": 5, "tiktok": 5, "youtube": 8, "facebook": 5, "twitter": 2, "linkedin": 0}
# How many of the creator's base tags come first. The video's own tags follow, then any base tags left over.
BASE_FIRST = {"instagram": 3, "tiktok": 3, "youtube": 5, "facebook": 3, "twitter": 1, "linkedin": 0}
NEVER_TAGS = ("#shorts", "#fyp", "#foryou")
X_LIMIT = 280
LINKEDIN_VISIBLE = 150  # characters of a LinkedIn post that show before "See more"

_DISPLAY = {
    "instagram": "Instagram",
    "tiktok": "TikTok",
    "youtube": "YouTube",
    "facebook": "Facebook",
    "linkedin": "LinkedIn",
    "twitter": "X",
}
_DASHES = (chr(0x2014), chr(0x2013))  # em dash, en dash

_INLINE_TAG = re.compile(r"(?<![\w&/#])#(?=\w*[^\W\d_])\w+")
_PLACEHOLDER = re.compile(r"<[A-Za-z][^<>\n]{0,39}>|\{[A-Za-z][^{}\n]{0,39}\}|\b(?:TODO|TBD|FIXME)\b|(?i:lorem ipsum)")
_FOLLOW = re.compile(r"\bfollow\b", re.IGNORECASE)

# A link is anything shaped like a web address: a scheme (https://, ftp://), www., or a name with dots that
# ends in two or more letters, such as linktr.ee, youtu.be or example.me. The ending has to be all
# lower case or all capitals, which keeps a missing space after a full stop ("week three.Then") from
# looking like an address. File names such as README.md and Node.js end the same way, so the common file
# endings are not taken for links.
_LINK = re.compile(
    r"\b[A-Za-z][A-Za-z0-9+.-]*://\S+"
    r"|\bwww\.\S+"
    r"|\b(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+(?P<ending>[a-z]{2,24}|[A-Z]{2,24})\b(?:/\S*)?"
)
_FILE_ENDINGS = frozenset(
    "js jsx ts tsx mjs cjs py rb md json html htm css scss txt pdf png jpg jpeg gif svg csv yml yaml toml xml sql "
    "sh zip mov doc docx xls xlsx ppt pptx".split()
)
_LINK_TRAILING = ".,;:!?)]}'\""

X_LINK_WEIGHT = 23  # X counts every link as 23 characters, however long it is
X_EMOJI_WEIGHT = 2  # and every emoji as 2


def _span(*spans: tuple[int, int]) -> str:
    """A regex character class for spans of code points. They are written with chr() so this file holds no emoji."""
    return "[" + "".join(re.escape(chr(a)) + (f"-{re.escape(chr(b))}" if b != a else "") for a, b in spans) + "]"


_VS16, _ZWJ, _KEYCAP = chr(0xFE0F), chr(0x200D), chr(0x20E3)
_SKIN_TONE = _span((0x1F3FB, 0x1F3FF))
_FLAG = _span((0x1F1E6, 0x1F1FF)) + "{2}"
_TAG_CHARACTERS = _span((0xE0020, 0xE007F))
# Pictographs from U+1F000 up are emoji. Below that, a character is an emoji either because it shows as
# one by default or because the variation selector U+FE0F follows it.
_PICTOGRAPH = _span((0x1F000, 0x1FAFF))
_EMOJI_BY_DEFAULT = _span(
    (0x231A, 0x231B), (0x23E9, 0x23EC), (0x23F0, 0x23F0), (0x23F3, 0x23F3), (0x25FD, 0x25FE), (0x2614, 0x2615),
    (0x2648, 0x2653), (0x267F, 0x267F), (0x2693, 0x2693), (0x26A1, 0x26A1), (0x26AA, 0x26AB), (0x26BD, 0x26BE),
    (0x26C4, 0x26C5), (0x26CE, 0x26CE), (0x26D4, 0x26D4), (0x26EA, 0x26EA), (0x26F2, 0x26F3), (0x26F5, 0x26F5),
    (0x26FA, 0x26FA), (0x26FD, 0x26FD), (0x2705, 0x2705), (0x270A, 0x270B), (0x2728, 0x2728), (0x274C, 0x274C),
    (0x274E, 0x274E), (0x2753, 0x2755), (0x2757, 0x2757), (0x2795, 0x2797), (0x27B0, 0x27B0), (0x27BF, 0x27BF),
    (0x2B1B, 0x2B1C), (0x2B50, 0x2B50), (0x2B55, 0x2B55),
)
_EMOJI_WITH_SELECTOR = _span(
    (0xA9, 0xA9), (0xAE, 0xAE), (0x203C, 0x203C), (0x2049, 0x2049), (0x2122, 0x2122), (0x2139, 0x2139),
    (0x2194, 0x21AA), (0x2328, 0x2328), (0x23CF, 0x23CF), (0x23E9, 0x23FA), (0x24C2, 0x24C2), (0x25AA, 0x25FE),
    (0x2600, 0x27BF), (0x2934, 0x2935), (0x2B05, 0x2B55), (0x3030, 0x3030), (0x303D, 0x303D), (0x3297, 0x3297),
    (0x3299, 0x3299),
)
_ONE_EMOJI = f"(?:{_PICTOGRAPH}|{_EMOJI_BY_DEFAULT}|{_EMOJI_WITH_SELECTOR}{_VS16})"
# One emoji is a flag, a keycap, or a pictograph with an optional skin tone and selector, joined to more
# of the same by zero width joiners (a family, a couple), and X counts the whole of it once.
_EMOJI = re.compile(
    f"{_FLAG}"
    f"|[0-9#*]{_VS16}?{_KEYCAP}"
    f"|{_ONE_EMOJI}{_SKIN_TONE}?{_VS16}?(?:{_ZWJ}{_ONE_EMOJI}{_SKIN_TONE}?{_VS16}?)*{_TAG_CHARACTERS}*"
)


def links_in(text: str) -> list[tuple[int, int, str]]:
    """The links in a text as (start, end, the link), without a full stop or bracket that ends the sentence."""
    found = []
    for match in _LINK.finditer(text):
        ending = match.group("ending")
        if ending is not None and "/" not in match.group(0) and ending.lower() in _FILE_ENDINGS:
            continue
        link = match.group(0).rstrip(_LINK_TRAILING)
        if link:
            found.append((match.start(), match.start() + len(link), link))
    return found


def _weigh(text: str) -> int:
    return len(_EMOJI.sub("", text)) + X_EMOJI_WEIGHT * len(_EMOJI.findall(text))


def x_length(text: str) -> int:
    """How long X counts a post: each link 23, each emoji 2 and every other character 1."""
    total, last = 0, 0
    for start, end, _ in links_in(text):
        total += _weigh(text[last:start]) + X_LINK_WEIGHT
        last = end
    return total + _weigh(text[last:])


# LinkedIn: asks for comments, likes, tags or a visit to the bio are held back by its filter. A keyword
# in such an ask is quoted or in capitals ("GROW", GROW), which keeps ordinary sentences such as "the
# comment section and the DMs" from matching.
_OPEN = f"[\"{chr(0x201C)}'{chr(0x2018)}]"
_CLOSE = f"[\"{chr(0x201D)}'{chr(0x2019)}]"
_KEYWORD = rf"(?:{_OPEN}\w[\w'-]*{_CLOSE}|[A-Z][A-Z0-9]+)"
_BAIT = [
    re.compile(rf"\b(?i:comment)\s+{_KEYWORD}\s+(?i:and|for|to)\b"),
    re.compile(r"\b(?i:comment\s+(?:below|down below))\b"),
    re.compile(r"\b(?i:drop)\s+(?i:a|an|your|me)\b[^.\n]{0,25}\b(?i:below|in the comments?)\b"),
    re.compile(r"(?:^|[.!?])\s*(?i:like)\b[^.\n]{0,20}\b(?i:for\s+part)\b", re.MULTILINE),
    re.compile(r"\b(?i:tag\s+(?:someone|a\s+friend|a\s+colleague|a\s+teammate|your\s+(?:friend|boss|team|colleague)))\b"),
    re.compile(r"\b(?i:link\s+in\s+(?:my\s+|the\s+)?bio)\b"),
    re.compile(rf"\b(?i:type)\s+{_KEYWORD}\s+(?i:below|in the comments?|if)\b"),
]
# LinkedIn: an opening that clears its throat instead of making the claim.
_THROAT_CLEAR = re.compile(
    r"^\W*(?:hey|hi|hello|hiya|okay|ok|um|guys|so,|well,|quick (?:update|thought)|just wanted to"
    r"|i(?:['" + chr(0x2019) + r"]m| am) (?:excited|thrilled|happy) to|excited to|thrilled to|happy to share)(?!\w)",
    re.IGNORECASE,
)


def display_name(platform: str) -> str:
    """The name a platform goes by in a sentence: X for twitter, TikTok, YouTube and so on."""
    return _DISPLAY.get(platform, platform)


# --- the call to action and the tags -------------------------------------------------------------------


def answers_comments(profile, platform: str) -> bool:
    """True where the creator answers comments with their offer, so the caption may ask for the keyword."""
    cta = profile.cta
    return cta.type == "comment" and bool(cta.keyword) and platform in cta.comment_platforms and platform != "linkedin"


def cta_line(profile, platform: str) -> str:
    """The last line of a caption: the comment line where the creator answers comments, a follow line elsewhere."""
    if answers_comments(profile, platform):
        cta = profile.cta
        if cta.payload:
            return f'Comment "{cta.keyword}" for {cta.payload}.'
        return f'Comment "{cta.keyword}" and I will send it to you.'
    handle = getattr(profile.handles, platform, "") or ""
    return f"Follow {handle if handle.startswith('@') else profile.name} for more."


def _is_hashtag(tag) -> bool:
    return isinstance(tag, str) and re.fullmatch(r"#[^\s#]+", tag) is not None and any(c.isalpha() for c in tag)


def select_hashtags(profile, platform: str, video_tags: Iterable[str] = ()) -> list[str]:
    """The tags for one platform: some of the base tags first, then the video's own, then any base tags left.

    Repeats (in any case) and the never-list are dropped, and the platform's cap is respected.
    """
    video = list(video_tags)
    for tag in video:
        if not _is_hashtag(tag):
            raise ValueError(f"{tag!r} is not a hashtag. A hashtag starts with # and has no spaces.")
    cap = HASHTAG_CAPS[platform]
    base = list(profile.hashtags.base)
    first = BASE_FIRST[platform]
    picked: list[str] = []
    seen = set(NEVER_TAGS)
    for tag in base[:first] + video + base[first:]:
        if len(picked) == cap:
            break
        key = tag.casefold()
        if key not in seen:
            seen.add(key)
            picked.append(tag)
    return picked


def _comment_ask(keyword: str) -> re.Pattern:
    """Matches a request to comment the keyword: Comment GROW, comment "grow", comment the word GROW."""
    return re.compile(
        rf"\bcomment\s+(?:(?:the\s+(?:word|keyword)|with|using)\s+)?{_OPEN}?{re.escape(keyword)}(?!\w)", re.IGNORECASE
    )


# --- checking a set of captions ------------------------------------------------------------------------


def check(captions, profile, *, platforms: Iterable[str] | None = None) -> list[str]:
    """Every rule the captions break, as plain sentences in platform order. An empty list means they pass.

    `platforms` limits the check to those platforms, for a video that is going to only some of them.
    """
    if not isinstance(captions, Mapping):
        return ["captions.json must hold one entry for each platform."]
    chosen = None if platforms is None else set(platforms)
    found: list[str] = []
    for platform in profile.platforms:
        if chosen is None or platform in chosen:
            found.extend(_check_platform(captions.get(platform), platform, profile))
    return found


def _check_platform(entry, platform: str, profile) -> list[str]:
    name = display_name(platform)
    if entry is None:
        return [f"{name}: there is no caption."]
    if not isinstance(entry, Mapping):
        return [f"{name}: the caption needs a title, a body and hashtags."]

    found: list[str] = []
    title, body, tags = entry.get("title"), entry.get("body"), entry.get("hashtags")
    body = body.strip() if isinstance(body, str) else ""
    if platform == "linkedin" and profile.cta.type == "comment" and "linkedin" in profile.cta.comment_platforms:
        found.append(
            "LinkedIn always gets a follow line, because comment prompts cut its reach. "
            "Take linkedin out of cta.comment_platforms in me/profile.json."
        )
    shown_tags = _check_tags(tags, platform, name, found)
    in_title = _INLINE_TAG.search(title) if isinstance(title, str) else None
    if in_title:
        found.append(f"{name}: the title has a hashtag ({in_title.group(0)}). Put hashtags in the hashtags list, not in the title.")

    if not body:
        found.append(f"{name}: the body is empty.")
        return found

    text = body + ("\n" + title if isinstance(title, str) else "")
    if any(dash in text for dash in _DASHES):
        found.append(f"{name}: the caption has an em dash or en dash. Use a comma, a colon, a period or brackets instead.")
    placeholder = _PLACEHOLDER.search(text)
    if placeholder:
        found.append(f"{name}: the caption still has a placeholder ({placeholder.group(0)}). Fill it in or take it out.")
    inline = _INLINE_TAG.search(body)
    if inline:
        found.append(f"{name}: the text has a hashtag ({inline.group(0)}). Put hashtags in the hashtags list, not in the text.")

    _check_call_to_action(body, platform, name, profile, found)

    if platform == "youtube":
        headline = title.strip() if isinstance(title, str) else ""
        if not headline:
            found.append("YouTube: the caption needs a title.")
        elif len(headline) > payloads.YOUTUBE_TITLE_MAX:
            found.append(
                f"YouTube: the title is {len(headline)} characters, and YouTube allows {payloads.YOUTUBE_TITLE_MAX}."
            )
    elif platform == "twitter":
        length = x_length(payloads.content_for({"body": body, "hashtags": shown_tags}, "twitter"))
        if length > X_LIMIT:
            found.append(
                f"X: the post counts {length} characters with its hashtags, and X allows {X_LIMIT}. "
                f"X counts each link as {X_LINK_WEIGHT} and each emoji as {X_EMOJI_WEIGHT}."
            )
    elif platform == "linkedin":
        _check_linkedin(body, name, found)
    return found


def _check_tags(tags, platform: str, name: str, found: list[str]) -> list[str]:
    """Check the hashtags list, add what is wrong to `found`, and return the entries that are real tags."""
    if not isinstance(tags, list):
        found.append(f"{name}: hashtags must be a list of tags such as #example, or an empty list for none.")
        return []
    real: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        if not _is_hashtag(tag):
            found.append(f"{name}: {tag!r} is not a hashtag. A hashtag starts with # and has no spaces.")
            continue
        real.append(tag)
        key = tag.casefold()
        if key in NEVER_TAGS:
            found.append(f"{name}: {tag} is on the never list. Leave it out.")
        elif key in seen:
            found.append(f"{name}: {tag} appears twice.")
        seen.add(key)
    cap = HASHTAG_CAPS[platform]
    if len(tags) > cap:
        if cap == 0:
            found.append(f"{name}: {name} gets no hashtags, and this caption has {len(tags)}.")
        else:
            found.append(f"{name}: {len(tags)} hashtags, and {name} takes at most {cap}.")
    return real


def _check_call_to_action(body: str, platform: str, name: str, profile, found: list[str]) -> None:
    last = body.splitlines()[-1]
    keyword = profile.cta.keyword
    ask = _comment_ask(keyword) if keyword else None
    example = cta_line(profile, platform)
    if answers_comments(profile, platform):
        if not ask.search(last):
            found.append(f"{name}: the last line needs the comment call to action, such as: {example}")
        return
    if ask and ask.search(body):
        found.append(
            f"{name}: the caption asks people to comment {keyword}, but nothing answers comments there. "
            f"Use a follow line instead, such as: {example}"
        )
    if not _FOLLOW.search(last):
        found.append(f"{name}: the last line needs a follow line, such as: {example}")


def _check_linkedin(body: str, name: str, found: list[str]) -> None:
    links = links_in(body)
    if links:
        found.append(f"{name}: the caption has a link ({links[0][2]}). Links cut LinkedIn's reach, so leave them out.")
    for pattern in _BAIT:
        phrase = pattern.search(body)
        if phrase:
            found.append(
                f'{name}: "{phrase.group(0).strip(" .!?" + chr(10))}" is engagement bait, and LinkedIn holds back '
                "posts that ask for comments, likes or tags. Ask a real question or say nothing."
            )
            break
    opening = _THROAT_CLEAR.search(body)
    if opening:
        found.append(
            f'{name}: the caption opens with "{opening.group(0).strip()}". Open with the claim itself, '
            f"because only the first {LINKEDIN_VISIBLE} characters show before See more."
        )
    sentence = re.split(r"(?<=[.!?])\s+|\n", body, maxsplit=1)[0]
    if len(sentence) > LINKEDIN_VISIBLE:
        found.append(
            f"{name}: the first sentence is {len(sentence)} characters. Keep the claim inside the first "
            f"{LINKEDIN_VISIBLE}, which is all that shows before See more."
        )


# --- the file and the creator's yes ---------------------------------------------------------------------


def load(slug: str) -> dict:
    """Read work/<slug>/captions.json."""
    path = config.work_dir(slug) / "captions.json"
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise PostError(f"There is no captions.json for {slug} yet. The post skill writes it first.") from None
    except OSError as exc:
        raise PostError(f"captions.json for {slug} could not be read ({exc.strerror or type(exc).__name__}).") from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PostError(
            f"work/{slug}/captions.json is not valid JSON ({exc.msg}, on line {exc.lineno}). "
            "Check for a missing comma, quote or bracket."
        ) from None
    if not isinstance(data, dict):
        raise PostError("captions.json must hold one entry for each platform.")
    return data


def fingerprint(captions) -> str:
    """A short hash of the words, the same however the file is spaced or ordered."""
    canonical = json.dumps(captions, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _approval_path(slug: str) -> Path:
    return config.work_dir(slug) / "captions.approved.json"


def approve(slug: str) -> Path:
    """Record that the creator has read these captions and said yes. A change to any caption undoes it."""
    record = {
        "captions_sha256": fingerprint(load(slug)),
        "approved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    path = _approval_path(slug)
    store.write(path, record)
    return path


def approval_problem(slug: str) -> str | None:
    """Why the captions may not be sent yet, or None when the creator has approved exactly these words."""
    captions = load(slug)
    try:
        record = store.read(_approval_path(slug))
    except PostError:
        record = {}
    if not record.get("captions_sha256"):
        return (
            "The captions have not been approved yet. Show every caption to the creator, and once they say yes "
            f"record it with: uv run python -m kit.post approve {slug}"
        )
    if record["captions_sha256"] != fingerprint(captions):
        return "The captions changed after they were approved. Show the creator the new words and ask again."
    return None
