"""Small helpers shared by the research steps: text, dates, web calls and files.

Nothing here knows about one creator or one account. Times are worked out in the zone the creator's
profile names, and a web call that fails comes back as a short plain reason with no address and no key in it.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone, tzinfo
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

from kit import config

WINDOW_DAYS = 14  # a video is "recent" for this long
YOUNG_HOURS = 48  # a video this fresh is still collecting engagement
RESEARCH_PLATFORMS = ("instagram", "tiktok", "youtube")
PLATFORM_LABEL = {"instagram": "Instagram", "tiktok": "TikTok", "youtube": "YouTube"}
ID_PREFIX = {"instagram": "ig", "tiktok": "tt", "youtube": "yt"}

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"
)
MAX_BYTES = 25_000_000  # the most a caption or small file may be
VIDEO_BYTES = 200_000_000  # the most one downloaded video may be

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
MONTHS_LONG = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)  # fmt: skip
DAYS_LONG = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


# --- time ---------------------------------------------------------------------------------------------


def utcnow() -> datetime:
    """The current moment, in UTC. Tests replace this to fix the clock."""
    return datetime.now(timezone.utc)


def tz_of(profile) -> tzinfo:
    """The creator's own time zone. A name this machine cannot read falls back to UTC."""
    try:
        return ZoneInfo(profile.timezone)
    except Exception:
        return timezone.utc


def run_date(profile, now: datetime | None = None) -> str:
    """The calendar day in the creator's zone, as 2026-10-28. It names the run's folder."""
    return (now or utcnow()).astimezone(tz_of(profile)).date().isoformat()


def parse_iso(text) -> datetime | None:
    """A moment from ISO text, in UTC. Text with no zone is taken as UTC. None when it cannot be read."""
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        moment = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)


def _moment(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return parse_iso(value)


def fmt_day(when, tz: tzinfo) -> str:
    """28 Oct, in the zone given."""
    moment = _moment(when)
    if moment is None:
        return ""
    local = moment.astimezone(tz)
    return f"{local.day} {MONTHS[local.month - 1]}"


def fmt_date(when, tz: tzinfo) -> str:
    """28 Oct 2026, in the zone given."""
    moment = _moment(when)
    if moment is None:
        return ""
    return f"{fmt_day(moment, tz)} {moment.astimezone(tz).year}"


def fmt_stamp(when, tz: tzinfo) -> str:
    """28 Oct 13:30, in the zone given."""
    moment = _moment(when)
    if moment is None:
        return ""
    return f"{fmt_day(moment, tz)} {moment.astimezone(tz):%H:%M}"


def fmt_long_day(when, tz: tzinfo) -> str:
    """Wednesday 28 October 2026, in the zone given."""
    moment = _moment(when)
    if moment is None:
        return ""
    local = moment.astimezone(tz)
    return f"{DAYS_LONG[local.weekday()]} {local.day} {MONTHS_LONG[local.month - 1]} {local.year}"


# --- text ---------------------------------------------------------------------------------------------


def short_num(value) -> str:
    """12400 as 12k, 1500 as 1.5k, 3400000 as 3.4M. Text that is not a number gives an empty string."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}M".replace(".0M", "M")
    if number >= 10_000:
        return f"{number / 1000:.0f}k"
    if number >= 1000:
        return f"{number / 1000:.1f}k".replace(".0k", "k")
    return f"{number:.0f}"


def plural(count: int, word: str, many: str | None = None) -> str:
    """1 video, 3 videos."""
    return f"{count} {word if count == 1 else (many or word + 's')}"


def words(text, count: int) -> str:
    """The first `count` words, with an ellipsis when there are more."""
    parts = (text or "").split()
    return " ".join(parts[:count]) + (" ..." if len(parts) > count else "")


def split_opening(text) -> tuple[str, str]:
    """(the opening line, the words that follow it). The opening is the first spoken sentence, joined to the
    next when it is only a word or two ("So." or "Okay."), and cut at 40 words. Spacing is tidied first."""
    clean = " ".join(str(text or "").split())
    parts = re.split(r"(?<=[.!?])\s+", clean) if clean else []
    line = parts[0] if parts else ""
    used = 1
    while len(line.split()) < 4 and used < len(parts):
        line += " " + parts[used]
        used += 1
    rest = " ".join(parts[used:])
    cut = line.split()[40:]
    if cut:  # a long first sentence runs on past the 40 words shown
        rest = " ".join(cut) + (" " + rest if rest else "")
    return words(line, 40), rest


def first_line(text) -> str:
    """The first spoken sentence, joined to the next when it is only a word or two ("So." or "Okay.")."""
    return split_opening(text)[0]


_CALL_TO_ACTION = re.compile(r"^\W*(comment|dm me|link in bio|follow)\b", re.I)
_JUNK_TRANSCRIPT = re.compile(r"^(thanks for watching|thank you for watching|you)[.! ]*$", re.I)


def title_from_caption(caption) -> str:
    """The first caption line that is not a "comment X and I will send it" line, with hashtags removed."""
    lines = [line.strip() for line in (caption or "").splitlines() if line.strip()]
    for line in lines:
        title = re.sub(r"\s*#\S+", "", line).strip()
        if title and not _CALL_TO_ACTION.match(title):
            return title[:160]
    return re.sub(r"\s*#\S+", "", lines[0]).strip()[:160] if lines else ""


def is_usable(transcript) -> bool:
    """True for a transcript with enough words to show how the video opens, and that is not a stray line."""
    if not isinstance(transcript, str):
        return False
    text = transcript.strip()
    return len(text.split()) >= 8 and not _JUNK_TRANSCRIPT.match(text)


def handle_of(raw) -> str:
    """A bare account name from a name, an @name or a profile link."""
    text = str(raw or "").strip()
    if not text:
        return ""
    looks_like_link = "://" in text or text.lower().startswith(("www.", "instagram.com", "tiktok.com", "youtube.com", "youtu.be"))
    if not looks_like_link:
        return text.lstrip("@").strip("/")
    parts = [part for part in urlparse(text if "://" in text else "https://" + text).path.split("/") if part]
    for part in parts:
        if part.startswith("@"):
            return part[1:]
    if len(parts) > 1 and parts[0] in {"channel", "c", "user"}:
        return parts[1]
    return parts[0] if parts else ""


def norm_url(url) -> str:
    """An address with the noise removed, so two spellings of one page compare equal."""
    text = str(url or "").strip()
    if "://" not in text:
        return text.lower()
    parsed = urlparse(text)
    host = parsed.netloc.lower().removeprefix("www.").removeprefix("mobile.")
    if host == "twitter.com":
        host = "x.com"
    return f"{host}{parsed.path.rstrip('/')}"


def safe_name(text: str) -> str:
    """Letters, numbers, dots and dashes only, so a name is safe as a file name."""
    return re.sub(r"[^a-z0-9._-]+", "_", str(text).lower()).strip("._") or "unnamed"


def redact(text: str, *secrets) -> str:
    """The text with each secret replaced, so a key can never reach a report or a message."""
    for secret in secrets:
        if secret and str(secret).strip():
            text = text.replace(str(secret), "<key>")
    return text


def kit_relative(path) -> str:
    """A path from the kit folder down, with forward slashes. Anything outside the kit folder shows its name only."""
    path = Path(path)
    try:
        return path.relative_to(config.kit_root()).as_posix()
    except ValueError:
        return path.name


# --- files --------------------------------------------------------------------------------------------


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


def write_json(path, data, *, indent: int | None = 1) -> None:
    """Write a file in one step, so a stopped run never leaves half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


# --- the web ------------------------------------------------------------------------------------------


class HttpError(Exception):
    """A web call that did not work. str() is a short plain reason with no address and no key in it."""

    def __init__(self, status: int | None, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


def _send(method: str, url: str, *, headers=None, timeout: float = 30, body=None, stream: bool = False):
    sent = {"User-Agent": USER_AGENT}
    sent.update(headers or {})
    options: dict = {"headers": sent, "timeout": timeout}
    if body is not None:
        options["json"] = body
    if stream:
        options["stream"] = True
    try:
        response = requests.request(method, url, **options)
    except requests.Timeout:
        raise HttpError(None, "timed out") from None
    except requests.ConnectionError:
        raise HttpError(None, "no network route") from None
    except requests.RequestException:
        raise HttpError(None, "the request failed") from None
    if not response.ok:
        status = response.status_code
        response.close()
        raise HttpError(status, f"HTTP {status}")
    return response


def http_json(url, method="GET", body=None, headers=None, timeout=30):
    """The parsed JSON answer of a web call."""
    response = _send(method, url, headers=headers, timeout=timeout, body=body)
    try:
        return response.json()
    except ValueError:
        raise HttpError(response.status_code, "the answer was not readable") from None


def http_bytes(url, headers=None, timeout=30, limit=None):
    """The bytes a web address returns. A file over `limit` (25 MB unless told otherwise) is refused."""
    cap = limit if limit is not None else MAX_BYTES
    response = _send("GET", url, headers=headers, timeout=timeout, stream=True)
    chunks, total = [], 0
    with response:
        for chunk in response.iter_content(65536):
            total += len(chunk)
            if total > cap:
                raise HttpError(response.status_code, "the file is too large")
            chunks.append(chunk)
    return b"".join(chunks)


def http_download(url, dest, *, timeout=60, limit=VIDEO_BYTES):
    """Save a web file at `dest`. A failed or oversized download leaves nothing behind."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    try:
        response = _send("GET", url, timeout=timeout, stream=True)
        total = 0
        with response, part.open("wb") as handle:
            for chunk in response.iter_content(65536):
                total += len(chunk)
                if total > limit:
                    raise HttpError(response.status_code, "the file is too large")
                handle.write(chunk)
        os.replace(part, dest)
    finally:
        part.unlink(missing_ok=True)
    return dest


def why(exc: BaseException) -> str:
    """A short plain reason for a failure. Addresses are cut out, because they can carry keys.

    A KitError is already one plain sentence written for the creator, so it is shown whole, with no class name.
    """
    if isinstance(exc, HttpError):
        return str(exc)
    if isinstance(exc, config.KitError):
        return str(exc).strip() or type(exc).__name__
    if isinstance(exc, TimeoutError):
        return str(exc)[:80] or "timed out"
    message = re.sub(r"https?://\S+", "<url>", str(exc)).strip()
    name = type(exc).__name__
    return f"{name}: {message[:80]}" if message else name
