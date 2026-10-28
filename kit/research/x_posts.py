"""The creator's ideas file, and the X posts it links to.

me/ideas.md holds one idea per bullet, and the creator can paste a link to an X post on any line. A post
is read through fxtwitter, then vxtwitter if that fails. Both are unofficial services and may stop
working, so a post that cannot be read is listed as unread with the reason, and the run carries on.
"""

from __future__ import annotations

import concurrent.futures as futures
import re
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from kit.research import common

LINK = re.compile(r"https?://(?:www\.|mobile\.)?(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})/status(?:es)?/(\d+)")
ANY_LINK = re.compile(r"https?://[^\s|>)\]]+")
BULLET = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+")
MAX_NOTE = 400  # characters kept of one note
MAX_POST_TEXT = 600  # characters kept of one post


def find_links(text: str) -> list[tuple[str, str, str]]:
    """X post links in the text as (user, post id, canonical address), each post once."""
    found, seen = [], set()
    for match in LINK.finditer(text or ""):
        user, post_id = match.group(1), match.group(2)
        if post_id not in seen:
            seen.add(post_id)
            found.append((user, post_id, f"https://x.com/{user}/status/{post_id}"))
    return found


def _cut(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def read_ideas_file(path) -> list[dict]:
    """The notes in me/ideas.md: every bullet, and any other line that holds a link.

    Headings and plain paragraphs are not ideas. Each note is {"n", "text", "links", "x_urls"}.
    """
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError:
        return []
    notes = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        links = [url.rstrip(".,;:!?)") for url in ANY_LINK.findall(stripped)]
        if not BULLET.match(line) and not links:
            continue
        notes.append(
            {
                "n": len(notes) + 1,
                "text": _cut(BULLET.sub("", stripped), MAX_NOTE),
                "links": list(dict.fromkeys(links)),
                "x_urls": [url for _, _, url in find_links(stripped)],
            }
        )
    return notes


def _when(value) -> str | None:
    try:
        return parsedate_to_datetime(value).astimezone(timezone.utc).isoformat() if value else None
    except (TypeError, ValueError):
        return None


def _from_fx(data, url: str) -> dict:
    post = (data or {}).get("tweet") if isinstance(data, dict) else None
    if not isinstance(post, dict):
        raise common.HttpError(None, "the post was not found")
    author = post.get("author") or {}
    return {
        "url": url, "id": str(post.get("id")), "status": "read", "via": "fxtwitter",
        "handle": author.get("screen_name"), "author_name": author.get("name"), "followers": author.get("followers"),
        "created_at": _when(post.get("created_at")), "text": _cut(post.get("text"), MAX_POST_TEXT),
        "views": post.get("views"), "likes": post.get("likes"), "reposts": post.get("retweets"),
        "replies": post.get("replies"), "bookmarks": post.get("bookmarks"), "quotes": post.get("quotes"),
    }  # fmt: skip


def _from_vx(data, url: str) -> dict:
    if not isinstance(data, dict) or not data.get("tweetID"):
        raise common.HttpError(None, "the post was not found")
    return {
        "url": url, "id": str(data["tweetID"]), "status": "read", "via": "vxtwitter",
        "handle": data.get("user_screen_name"), "author_name": data.get("user_name"), "followers": None,
        "created_at": _when(data.get("date")), "text": _cut(data.get("text"), MAX_POST_TEXT),
        "views": None, "likes": data.get("likes"), "reposts": data.get("retweets"),
        "replies": data.get("replies"), "bookmarks": None, "quotes": None,
    }  # fmt: skip


def _read_one(link: tuple[str, str, str]) -> dict:
    user, post_id, url = link
    reasons = []
    for api, convert in (
        (f"https://api.fxtwitter.com/{user}/status/{post_id}", _from_fx),
        (f"https://api.vxtwitter.com/{user}/status/{post_id}", _from_vx),
    ):
        try:
            return convert(common.http_json(api, timeout=12), url)
        except Exception as exc:
            reasons.append(common.why(exc))
    return {"url": url, "id": post_id, "handle": user, "status": "unread", "reason": reasons[0] if reasons else "no answer"}


def read_posts(links: list[tuple[str, str, str]], *, offline: bool = False) -> list[dict]:
    """Read each post, in the order given. A post that cannot be read comes back with status "unread" and a reason."""
    if offline:
        return [{"url": url, "id": pid, "handle": user, "status": "unread", "reason": "offline run"} for user, pid, url in links]
    if not links:
        return []
    with futures.ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(_read_one, links))
