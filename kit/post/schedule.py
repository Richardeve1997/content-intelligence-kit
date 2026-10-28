"""Scheduling a finished video on each platform through Zernio, and looking after what was scheduled.

`schedule` works in this order:

1. It checks everything that can be checked on this machine: the platforms, the time, the captions and
   the video. If any of that is wrong, nothing is sent.
2. Unless it is a dry run, it checks that the creator approved these exact captions.
3. With no Zernio key it writes the captions and the video's path to work/<slug>/post/manual.md, for
   posting by hand, and raises NoZernio.
4. It asks Zernio which platforms are connected. A platform that is not connected is skipped and
   reported, never posted some other way.
5. It uploads the video once. Every platform's post uses that one upload.
6. It creates one post for each platform, each with its own caption and time, so each can be moved or
   cancelled alone.

The records in work/<slug>/post/ make a rerun safe. <platform>.json is written before the create is sent
and holds the request id and the post's title ("<name> <platform> <slug>"). If the answer to a create
never arrives, the next run looks for a post with that title in Zernio and uses it, and only sends the
create again, with the same request id, when there is none. media.json remembers the upload.

`schedule` returns one Result for each platform. A problem with one platform (not connected, refused by
Zernio, a damaged record) is that platform's Result, and the others carry on.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo

from kit import config, zernio
from kit.post import captions, payloads, store
from kit.post.errors import CaptionProblems, PostError

__all__ = [
    "Result",
    "accounts_check",
    "default_when",
    "describe_time",
    "local_time",
    "platform_names",
    "write_manual",
    "schedule",
    "list_scheduled",
    "reschedule",
    "cancel",
]

MIN_LEAD = timedelta(minutes=5)  # the least notice a post can be scheduled with
CREATE_MARGIN = timedelta(seconds=60)  # the least time left when a create is sent, after a slow upload
TOLERANCE = timedelta(seconds=1)  # how far Zernio's time may differ from the one that was asked for
LOCK = ".post.lock"  # in the video's folder, so work/<slug>/post/ appears only once something is recorded in it
AGAIN = "Run the same command again: it looks for the post in Zernio before it sends anything."
CANCEL_FIRST = "To try again, cancel it first and then schedule it again."
PASSED = (
    "The time you picked has passed or is too close to schedule. Pick a time at least five minutes from now, "
    "because Zernio posts at once when a time has passed."
)


@dataclass(frozen=True)
class Result:
    """What happened to one platform.

    status          scheduled, published, failed, skipped, cancelled, dry run or unknown
    post_id         Zernio's id for the post, when there is one
    url             the post's address once it is live
    reason          why it was skipped or failed, or a note about the post
    scheduled_for   the time it is set for, in the creator's own time zone
    """

    platform: str
    status: str
    post_id: str | None = None
    url: str | None = None
    reason: str | None = None
    scheduled_for: str | None = None


# --- times -------------------------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def local_time(moment, tzname: str) -> datetime:
    """The moment in the creator's time zone. A time with no zone is read as being in it."""
    if not isinstance(moment, datetime):
        raise PostError("The time to post has to be a date and a time.")
    zone = ZoneInfo(tzname)
    return moment.replace(tzinfo=zone) if moment.tzinfo is None else moment.astimezone(zone)


def _require_lead(moment: datetime, margin: timedelta) -> None:
    if moment - _now() < margin:
        raise PostError(PASSED)


def _parse(text) -> datetime | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def describe_time(moment: datetime) -> str:
    """A time in a sentence: Thu 29 Oct 18:00 (Europe/London)."""
    zone = getattr(moment.tzinfo, "key", None) or moment.strftime("%Z")
    return f"{moment:%a %d %b %H:%M} ({zone})"


def default_when(profile, platform: str, day: date) -> datetime:
    """The creator's usual posting time for a platform on a given day, in their own time zone."""
    usual = profile.posting_times.get(platform)
    if not usual:
        raise PostError(
            f"There is no usual posting time for {captions.display_name(platform)} in your profile. "
            "Give a time, such as 6pm."
        )
    hour, minute = (int(part) for part in usual.split(":"))
    return datetime.combine(day, time(hour, minute), tzinfo=ZoneInfo(profile.timezone))


# --- small helpers -----------------------------------------------------------------------------------------


def _platform(name) -> str:
    text = str(name).strip().casefold()
    text = "twitter" if text == "x" else text
    if text not in config.PLATFORMS:
        raise PostError(
            f"{name!r} is not a platform the kit posts to. "
            "Use instagram, tiktok, youtube, facebook, linkedin or twitter (X)."
        )
    return text


def platform_names(platforms, profile) -> list[str]:
    if platforms is None:
        names = list(profile.platforms)
    else:
        items = [platforms] if isinstance(platforms, str) else platforms
        names = list(dict.fromkeys(_platform(item) for item in items))
    if not names:
        raise PostError("Say which platforms to post on.")
    for name in names:
        if name not in profile.platforms:
            raise PostError(
                f"{captions.display_name(name)} is not one of the platforms in your profile "
                f"({', '.join(profile.platforms)}). Add it to platforms in me/profile.json to post there."
            )
    return names


def _folder(slug: str) -> Path:
    """The video's folder in work/. A name with no folder is most likely a typing slip."""
    folder = config.work_dir(slug)
    if not folder.is_dir():
        raise PostError(f"There is no video called {slug} in work/. Check the name against the folders there.")
    return folder


def _video(folder: Path, slug: str) -> Path:
    video = folder / "final.mp4"
    if not video.is_file():
        raise PostError(f"work/{slug}/final.mp4 is missing. The video has to be finished before it can be posted.")
    return video


def _archive(path: Path, label: str) -> None:
    """Keep a record that no longer applies, renamed, instead of deleting it."""
    path.replace(path.with_name(f"{path.stem}.{label}.json"))


def _plain(text) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").casefold())


def _profile_id(account: Mapping) -> str | None:
    value = account.get("profileId")
    value = value.get("_id") if isinstance(value, dict) else value
    return value if isinstance(value, str) and value else None


def _pick_account(candidates: list[dict], handle: str) -> tuple[dict | None, str | None]:
    """The account to post to, or None and why not. Two accounts are told apart by the profile's handle."""
    if not candidates:
        return None, "not connected in Zernio"
    if len(candidates) == 1:
        return candidates[0], None
    wanted = _plain(handle)
    matching = [a for a in candidates if wanted and wanted in (_plain(a.get("username")), _plain(a.get("displayName")))]
    if len(matching) == 1:
        return matching[0], None
    return None, "more than one account is connected in Zernio, and none matches the handle in your profile"


def _state(post: Mapping, platform: str) -> tuple[str, str | None, str | None]:
    """(status, address, reason) for a post as Zernio reports it."""
    wanted = payloads.ZERNIO_PLATFORM[platform]
    entry = next((e for e in post.get("platforms") or [] if isinstance(e, dict) and e.get("platform") == wanted), {})
    status, on_platform = post.get("status"), entry.get("status")
    if status == "published" or on_platform == "published":
        return "published", entry.get("platformPostUrl") or post.get("platformPostUrl"), None
    if status in ("failed", "partial") or on_platform == "failed":
        said = entry.get("errorMessage") or post.get("errorMessage")
        return "failed", None, str(said) if said else "Zernio reports that the post failed. Open it in Zernio to see why."
    if status == "scheduled":
        return "scheduled", None, None
    if status == "publishing":
        return "scheduled", None, "Zernio is publishing it now."
    if status == "cancelled":
        return "cancelled", None, None
    if status in ("draft", "pending_approval"):
        held = "a draft" if status == "draft" else "waiting for approval"
        return "unknown", None, f"Zernio is holding it as {held}, not scheduled."
    return "unknown", None, f"Zernio reports the state {status!r}."


# --- connected accounts -------------------------------------------------------------------------------------


def accounts_check(client: zernio.Client | None = None) -> dict[str, str]:
    """Which platforms are connected in Zernio: {"instagram": "connected", "tiktok": "not connected", ...}.

    It makes one read-only call, needs no profile, and shows nothing but the status. Raises NoZernio when
    there is no key.
    """
    client = client or zernio.Client.from_env()
    found = client.accounts()
    return {name: "connected" if found.get(name) else "not connected" for name in config.PLATFORMS}


# --- the file for posting by hand ---------------------------------------------------------------------------------


def _fenced(text: str) -> list[str]:
    return ["```text", text, "```"]


def write_manual(slug: str, *, profile=None) -> Path:
    """Write work/<slug>/post/manual.md: each platform's caption and the video's path, ready to post by hand."""
    profile = profile or config.load_profile()
    folder = _folder(slug)
    data = captions.load(slug)
    problems = captions.check(data, profile)
    if problems:
        raise CaptionProblems(problems)
    video = _video(folder, slug)
    lines = [f"# Post {slug} by hand", "", f"The video: {video}", ""]
    if profile.posting_times:
        lines += [f"Your usual time for each platform is in brackets, in {profile.timezone}.", ""]
    for platform in profile.platforms:
        entry = data[platform]
        usual = profile.posting_times.get(platform)
        lines += [f"## {captions.display_name(platform)}" + (f" ({usual})" if usual else ""), ""]
        if platform == "youtube":
            lines += ["Title:", "", *_fenced(payloads.youtube_title(entry)), "", "Description:", ""]
        lines += [*_fenced(payloads.content_for(entry, platform)), ""]
    path = folder / "post" / "manual.md"
    store.write_text(path, "\n".join(lines))
    return path


# --- scheduling ---------------------------------------------------------------------------------------------------


class _Run:
    """One call to `schedule`: the video, the captions and the client that every platform shares."""

    def __init__(self, *, slug, profile, folder, video, captions_data, when, client):
        self.slug, self.profile, self.folder, self.video = slug, profile, folder, video
        self.post_dir = folder / "post"
        self.captions, self.when, self.client = captions_data, when, client
        self.zone = ZoneInfo(profile.timezone)
        self._media: str | None = None
        self._media_error: Exception | None = None
        self._seconds: float | None = None

    # the upload is made once, and the failure of an upload once is the failure of them all
    def media_url(self) -> str:
        if self._media_error is not None:
            raise self._media_error
        if self._media is None:
            try:
                self._media = self._upload()
            except (zernio.ZernioError, PostError) as exc:
                self._media_error = exc
                raise
        return self._media

    def _upload(self) -> str:
        info = self.video.stat()
        key = {"path": self.video.name, "size": info.st_size, "mtime": int(info.st_mtime)}
        path = self.post_dir / "media.json"
        record = store.read(path)
        if record.get("url") and all(record.get(name) == value for name, value in key.items()):
            return record["url"]
        url = self.client.upload(self.video)
        store.write(path, {**key, "url": url})
        return url

    def seconds(self) -> float:
        if self._seconds is None:
            self._seconds = payloads.video_seconds(self.video)
        return self._seconds

    def platform(self, name: str, candidates: list[dict]) -> Result:
        account, why = _pick_account(candidates, getattr(self.profile.handles, name, ""))
        if account is None:
            return Result(name, "skipped", reason=why)
        try:
            return self._schedule(name, account)
        except (zernio.ZernioError, PostError) as exc:
            return Result(name, "failed", reason=str(exc))

    def _schedule(self, platform: str, account: dict) -> Result:
        path = self.post_dir / f"{platform}.json"
        title = f"{self.profile.name} {platform} {self.slug}"
        receipt = store.read(path)

        if receipt.get("post_id"):
            outcome = self._existing(platform, receipt)
            if outcome is not None:
                return outcome
            _archive(path, "stale")  # Zernio no longer has that post, so this one starts again
            receipt = {}
        if not receipt:
            receipt = {
                "slug": self.slug,
                "platform": platform,
                "title": title,
                "request_id": str(uuid.uuid4()),
                "account_id": account["_id"],
            }
        elif receipt.get("create_attempted"):
            # An earlier run sent the create and never heard back. The post may exist.
            found = self.client.find_post_by_title(receipt.get("title") or title, profile_id=_profile_id(account))
            if found and found.get("_id"):
                receipt["post_id"] = found["_id"]
                store.write(path, receipt)
                outcome = self._existing(platform, receipt)
                if outcome is not None:
                    return outcome
                receipt.pop("post_id")  # it vanished again, so the create goes ahead

        _require_lead(self.when, CREATE_MARGIN)
        media = self.media_url()
        _require_lead(self.when, CREATE_MARGIN)  # a long upload can use up the notice
        body = payloads.build_body(
            platform,
            title=receipt["title"],
            slug=self.slug,
            caption=self.captions[platform],
            account_id=account["_id"],
            media_url=media,
            scheduled_for=self.when.isoformat(),
            timezone=self.profile.timezone,
            seconds=self.seconds() if platform == "facebook" else None,
        )
        receipt["account_id"] = account["_id"]
        receipt["create_attempted"] = True
        store.write(path, receipt)  # the request id is on disk before the request leaves
        try:
            created = self.client.create_post(body, receipt["request_id"])
        except zernio.ZernioError as exc:
            return self._create_failed(platform, path, receipt, exc)
        saved = zernio.post_of(created)
        post_id = saved.get("_id") or created.get("postId")
        if not post_id:
            return Result(platform, "failed", reason=f"Zernio did not say which post it saved. {AGAIN}")
        receipt["post_id"] = post_id
        receipt["scheduled_for"] = self.when.isoformat()
        store.write(path, receipt)
        return self._confirm(platform, post_id, saved)

    def _create_failed(self, platform: str, path: Path, receipt: dict, exc: zernio.ZernioError) -> Result:
        body = exc.body if isinstance(exc.body, dict) else {}
        if exc.status == 409 and body.get("existingPostId"):
            # Zernio already holds this exact caption and video. If it is our own post, use it.
            try:
                other = self.client.get_post(body["existingPostId"])
            except zernio.ZernioError:
                other = {}
            if other.get("title") == receipt["title"]:
                receipt["post_id"] = body["existingPostId"]
                store.write(path, receipt)
                outcome = self._existing(platform, receipt)
                if outcome is not None:
                    return outcome
                receipt.pop("post_id")
        if exc.uncertain:
            return Result(platform, "failed", reason=f"{exc} {AGAIN}")
        path.unlink(missing_ok=True)  # Zernio refused it, so nothing exists to remember
        return Result(platform, "failed", reason=str(exc))

    def _confirm(self, platform: str, post_id: str, fallback: dict) -> Result:
        """Read the new post back and report it as Zernio holds it."""
        try:
            post = self.client.get_post(post_id)
        except zernio.ZernioError:
            post = fallback
        if not post.get("status"):
            return Result(
                platform, "unknown", post_id,
                reason="Zernio saved the post, but the kit could not read it back. Check it in Zernio.",
            )
        status, url, reason = _state(post, platform)
        actual = _parse(post.get("scheduledFor"))
        shown = None if actual is None else actual.astimezone(self.zone).isoformat()
        if status == "scheduled":
            if actual is None or abs(actual - self.when) > TOLERANCE:
                said = "no time" if actual is None else describe_time(actual.astimezone(self.zone))
                return Result(
                    platform, "failed", post_id, reason=f"Zernio set it for {said}, not {describe_time(self.when)}. Check it in Zernio.",
                    scheduled_for=shown,
                )
            return Result(platform, "scheduled", post_id, scheduled_for=shown)
        if status == "published":
            return Result(platform, "published", post_id, url, "Zernio published it at once, not at the time you chose.", shown)
        return Result(platform, "failed", post_id, None, reason or f"Zernio shows it as {post.get('status')}.", shown)

    def _existing(self, platform: str, receipt: dict) -> Result | None:
        """The state of a post made on an earlier run, or None when Zernio no longer has it."""
        post_id = receipt["post_id"]
        try:
            post = self.client.get_post(post_id)
        except zernio.ZernioError as exc:
            if exc.status == 404:
                return None
            raise
        status, url, reason = _state(post, platform)
        actual = _parse(post.get("scheduledFor"))
        shown = None if actual is None else actual.astimezone(self.zone).isoformat()
        if status == "scheduled":
            if actual is not None and abs(actual - self.when) > TOLERANCE:
                note = (
                    f"Already scheduled for {describe_time(actual.astimezone(self.zone))} instead. "
                    "To move it, ask to reschedule it."
                )
            else:
                note = "Already scheduled from an earlier run."
            return Result(platform, "scheduled", post_id, None, note, shown)
        if status == "published":
            return Result(platform, "published", post_id, url, "Already posted.", shown)
        held = reason or f"Zernio shows it as {post.get('status')}."
        return Result(platform, "failed", post_id, None, f"{held} {CANCEL_FIRST}", shown)


def schedule(
    slug: str,
    platforms: Iterable[str] | None,
    when: datetime,
    *,
    dry_run: bool = False,
    client: zernio.Client | None = None,
) -> list[Result]:
    """Schedule work/<slug>/final.mp4 on each of `platforms` at `when`, and return one Result for each.

    `platforms` are names from the profile; None means all of them, and "x" is accepted for twitter.
    `when` is a date and time. One with no time zone is read in the creator's zone (profile.timezone), and
    one with a zone is the same moment wherever it is written. It has to be at least five minutes ahead,
    because Zernio publishes a post at once when its time has passed.

    With dry_run nothing is sent, not even a read, and no key is needed: the Results say what would happen.
    Otherwise the creator must have approved the captions as they stand now (captions.approve), or it
    raises PostError before anything is sent. `client` is for tests and for callers that already hold
    one; by default the key comes from ZERNIO_API_KEY.

    Raises PostError for a platform, time, video, record or approval that cannot be used, CaptionProblems
    when a caption breaks a rule, ZernioError when the account lookup fails, and NoZernio when there is no
    key (after writing manual.md).
    """
    profile = config.load_profile()
    folder = _folder(slug)
    names = platform_names(platforms, profile)
    when = local_time(when, profile.timezone)
    _require_lead(when, MIN_LEAD)
    data = captions.load(slug)
    problems = captions.check(data, profile, platforms=names)
    if problems:
        raise CaptionProblems(problems)
    video = _video(folder, slug)

    if dry_run:
        note = f"Would schedule for {describe_time(when)}. Nothing was sent."
        return [Result(name, "dry run", reason=note, scheduled_for=when.isoformat()) for name in names]

    # Nothing is sent, and no file for posting by hand is written, until the creator has read these exact
    # captions and said yes. Their yes to the TikTok caption is also the preview and consent TikTok asks for.
    unapproved = captions.approval_problem(slug)
    if unapproved:
        raise PostError(unapproved)

    if client is None:
        try:
            client = zernio.Client.from_env()
        except zernio.NoZernio:
            write_manual(slug, profile=profile)
            raise zernio.NoZernio(
                "No Zernio key is set, so nothing was scheduled. "
                f"The captions and the video are in work/{slug}/post/manual.md, ready to post by hand."
            ) from None

    with store.locked(folder / LOCK):
        found = client.accounts()
        run = _Run(slug=slug, profile=profile, folder=folder, video=video, captions_data=data, when=when, client=client)
        return [run.platform(name, found[name]) for name in names]


# --- looking at, moving and cancelling what was scheduled ------------------------------------------------------------


def _receipt(folder: Path, platform: str) -> tuple[Path, dict]:
    path = folder / "post" / f"{platform}.json"
    return path, store.read(path)


def list_scheduled(slug: str, *, client: zernio.Client | None = None) -> list[Result]:
    """What was scheduled for this video, as Zernio reports it now. Needs a key only when there is something to look up."""
    profile = config.load_profile()
    folder = _folder(slug)
    records = [(name, *_receipt(folder, name)) for name in config.PLATFORMS]
    records = [(name, receipt) for name, _, receipt in records if receipt]
    if any(receipt.get("post_id") for _, receipt in records) and client is None:
        client = zernio.Client.from_env()
    zone = ZoneInfo(profile.timezone)
    results = []
    for name, receipt in records:
        post_id = receipt.get("post_id")
        if not post_id:
            results.append(
                Result(name, "unknown", reason="An earlier attempt was interrupted before Zernio confirmed it. Run schedule again to check.")
            )
            continue
        try:
            post = client.get_post(post_id)
        except zernio.ZernioError as exc:
            if exc.status == 404:
                results.append(Result(name, "cancelled", post_id, reason="Zernio no longer has this post, so it will not go out."))
            else:
                results.append(Result(name, "unknown", post_id, reason=str(exc)))
            continue
        status, url, reason = _state(post, name)
        actual = _parse(post.get("scheduledFor"))
        shown = None if actual is None else actual.astimezone(zone).isoformat()
        results.append(Result(name, status, post_id, url, reason, shown))
    return results


def reschedule(slug: str, platform: str, when: datetime, *, client: zernio.Client | None = None) -> Result:
    """Move a scheduled post to a new time. Only a post that is still waiting to go out can be moved."""
    profile = config.load_profile()
    folder = _folder(slug)
    name = platform_names([platform], profile)[0]
    when = local_time(when, profile.timezone)
    _require_lead(when, MIN_LEAD)
    path, receipt = _receipt(folder, name)
    if not receipt.get("post_id"):
        return Result(name, "skipped", reason=f"Nothing is scheduled on {captions.display_name(name)} for {slug}.")
    client = client or zernio.Client.from_env()
    with store.locked(folder / LOCK):
        receipt = store.read(path)
        post_id = receipt["post_id"]
        try:
            post = client.get_post(post_id)
            status, url, reason = _state(post, name)
            if status == "published":
                return Result(name, "failed", post_id, url, "It has already gone out, so it cannot be moved.")
            if post.get("status") != "scheduled":
                held = reason or f"Zernio shows it as {post.get('status')}."
                return Result(name, "failed", post_id, None, f"{held} It cannot be moved. {CANCEL_FIRST}")
            client.update_post(post_id, {"isDraft": False, "scheduledFor": when.isoformat(), "timezone": profile.timezone})
            post = client.get_post(post_id)
        except zernio.ZernioError as exc:
            if exc.status == 404:
                return Result(name, "failed", post_id, None, "Zernio no longer has this post. Schedule it again.")
            return Result(name, "failed", post_id, None, str(exc))
        actual = _parse(post.get("scheduledFor"))
        if post.get("status") != "scheduled" or actual is None or abs(actual - when) > TOLERANCE:
            return Result(
                name, "failed", post_id,
                reason=f"Zernio did not move it to {describe_time(when)}. Check it in Zernio before you try again.",
            )
        receipt["scheduled_for"] = when.isoformat()
        store.write(path, receipt)
        return Result(name, "scheduled", post_id, scheduled_for=actual.astimezone(when.tzinfo).isoformat())


def cancel(slug: str, platform: str, *, client: zernio.Client | None = None) -> Result:
    """Cancel a scheduled post by deleting it from Zernio. A post that has started to publish cannot be cancelled."""
    profile = config.load_profile()
    folder = _folder(slug)
    name = platform_names([platform], profile)[0]
    path, receipt = _receipt(folder, name)
    if not receipt.get("post_id"):
        return Result(name, "skipped", reason=f"Nothing is scheduled on {captions.display_name(name)} for {slug}.")
    client = client or zernio.Client.from_env()
    with store.locked(folder / LOCK):
        receipt = store.read(path)
        post_id = receipt["post_id"]
        try:
            post = client.get_post(post_id)
        except zernio.ZernioError as exc:
            if exc.status != 404:
                return Result(name, "failed", post_id, None, str(exc))
            _archive(path, "cancelled")
            return Result(name, "cancelled", post_id, reason="Zernio no longer had it.")
        status, url, _ = _state(post, name)
        if post.get("status") in ("published", "publishing", "partial") or status == "published":
            return Result(
                name, "failed", post_id, url,
                "It has already started to publish, so it cannot be cancelled. Remove it in the app if it should come down.",
            )
        try:
            client.delete_post(post_id)
        except zernio.ZernioError as exc:
            return Result(name, "failed", post_id, None, str(exc))
        _archive(path, "cancelled")
        return Result(name, "cancelled", post_id)
