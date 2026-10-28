"""A small client for the Zernio API, the paid service that posts to social platforms.

The post skill uses it to upload a video once and schedule one post per platform, and the research
pipeline uses it to read a creator's own results. It holds the creator's key and nothing else: no
captions, no schedule rules and no receipts. Those live in kit.post.

How it behaves:

* Every call sends the key as a bearer token to the Zernio address and nowhere else. The video goes
  to a storage address that Zernio hands out, and that request carries no key.
* An answer of 429 means Zernio did nothing with the request. The call waits as long as Retry-After
  asks, never more than a minute, and tries again, up to six tries in all.
* A failure is a ZernioError. Its message is one plain sentence with Zernio's own reason and never
  the key. `uncertain` says whether Zernio may still have acted: a dropped connection, a timeout
  and a server error leave that open, while a rejected request does not.
* `Client.from_env()` reads ZERNIO_API_KEY from .env, the only place the kit reads a key, and raises
  NoZernio when there is none there.
"""

from __future__ import annotations

import math
import mimetypes
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, urlsplit

import requests

from kit import config
from kit.config import KitError

__all__ = ["API", "Client", "NoZernio", "ZernioError", "post_of"]

API = "https://zernio.com/api/v1"

REQUEST_TIMEOUT = 60  # seconds, for an ordinary call
CREATE_TIMEOUT = 300  # a create can wait while Zernio checks the video it is given
# (connect, read). The first number also caps how long the whole video may take to send, which is
# why it is an hour: a long video over a slow home connection needs it.
UPLOAD_TIMEOUT = (3600, 900)
MAX_ATTEMPTS = 6  # tries in all, when Zernio keeps answering 429
MAX_WAIT = 60.0  # the longest wait between two tries, in seconds
RETRY_MARGIN = timedelta(seconds=60)  # a retry is not sent when the post's own time is closer than this
LIST_PAGE_SIZE = 100
LIST_MAX_PAGES = 5  # pages read when looking for a post by its title


# --- errors ----------------------------------------------------------------------------------------


class ZernioError(KitError):
    """Something went wrong with Zernio. The message is one plain sentence and never holds the key.

    status     the HTTP status, or None when there was no answer
    body       Zernio's answer, when it sent one that could be read
    uncertain  True when Zernio may still have acted on the request
    """

    def __init__(self, message: str, *, status: int | None = None, body: Any = None, uncertain: bool = False):
        super().__init__(message)
        self.status = status
        self.body = body
        self.uncertain = uncertain

    def __reduce__(self):
        return (_rebuild, (str(self), self.status, self.body, self.uncertain))


def _rebuild(message, status, body, uncertain):
    return ZernioError(message, status=status, body=body, uncertain=uncertain)


class NoZernio(KitError):
    """There is no Zernio key, so nothing can be sent to Zernio."""

    def __init__(self, message: str | None = None):
        super().__init__(
            message
            or "There is no Zernio key. To post through Zernio, add a line ZERNIO_API_KEY=your key "
            "to the .env file in the kit folder."
        )


# --- the client --------------------------------------------------------------------------------------


class Client:
    """The calls the kit makes to Zernio, for one key. `base` is the address of the API."""

    def __init__(self, key: str, base: str = API):
        if not isinstance(key, str) or not key.strip():
            raise ValueError("A Zernio key is needed.")
        address = base.rstrip("/") if isinstance(base, str) else ""
        parts = urlsplit(address)
        on_this_machine = parts.hostname in ("127.0.0.1", "localhost", "::1")
        if not parts.hostname or not (parts.scheme == "https" or (parts.scheme == "http" and on_this_machine)):
            raise ValueError("The Zernio address must start with https://, so the key is never sent in the clear.")
        self.key = key.strip()
        self.base = address

    def __repr__(self) -> str:
        return f"Client(base={self.base!r})"

    @classmethod
    def from_env(cls, base: str = API) -> Client:
        """A client for the key in ZERNIO_API_KEY in .env, the only place the kit reads it. Raises NoZernio without one."""
        key = config.key("ZERNIO_API_KEY")
        if not key:
            raise NoZernio()
        return cls(key, base)

    # --- calling the API ---------------------------------------------------------------------------

    def call(self, method: str, path: str, **kw) -> dict:
        """Send one request and return Zernio's answer as a dict. Keyword arguments go to requests.

        `headers` are added to the bearer header, and `timeout` replaces the default of 60 seconds.
        When a body carries a scheduledFor time, a retry after a 429 is not sent once that time is less than
        a minute away.
        """
        headers = {"Authorization": f"Bearer {self.key}", **kw.pop("headers", {})}
        timeout = kw.pop("timeout", REQUEST_TIMEOUT)
        due = _due(kw)
        for attempt in range(MAX_ATTEMPTS):
            if attempt and due is not None and due - _now() < RETRY_MARGIN:
                # Zernio publishes at once when a post's time has passed, and the waits between tries add up.
                raise ZernioError(
                    "Zernio kept turning requests away, and the time for this post is now too close to send it. "
                    "Nothing was sent. Pick a later time and ask again."
                )
            try:
                response = requests.request(method, self.base + path, headers=headers, timeout=timeout, **kw)
            except requests.Timeout:
                raise ZernioError("Zernio did not answer in time.", uncertain=True) from None
            except requests.RequestException:
                raise ZernioError("The connection to Zernio dropped before it answered.", uncertain=True) from None
            if response.status_code != 429 or attempt == MAX_ATTEMPTS - 1:
                break
            time.sleep(_wait(response, attempt))
        return self._answer(response)

    def _answer(self, response: requests.Response) -> dict:
        readable, body = _read(response)
        if not response.ok:
            raise self._rejection(response.status_code, body if readable else None)
        if not readable or not (body is None or isinstance(body, dict)):
            raise ZernioError(
                "An answer from Zernio could not be read.", status=response.status_code, uncertain=True
            )
        if body is None:
            return {}
        if body.get("success") is False:
            reason = self._reason(body)
            raise ZernioError(
                "Zernio did not accept the request" + (f": {reason}" if reason else "") + ".",
                status=response.status_code,
                body=body,
            )
        return body

    def _rejection(self, status: int, body: Any) -> ZernioError:
        reason = self._reason(body)
        if status == 401:
            text = f"Zernio did not accept the key (HTTP 401{': ' + reason if reason else ''}). Check ZERNIO_API_KEY in your .env file."
        elif status == 403:
            text = f"Zernio refused the request (HTTP 403{': ' + reason if reason else ''}). The key may not have access to it."
        else:
            text = f"Zernio returned HTTP {status}" + (f": {reason}" if reason else "")
            text = text.rstrip(".") + "."
        # A timeout, a clash with a request still being saved and a server error leave open whether Zernio
        # acted. A refusal does not, and neither does a duplicate that names the post it duplicates.
        uncertain = status in (408, 409) or status >= 500
        if status == 409 and isinstance(body, dict) and body.get("existingPostId"):
            uncertain = False
        return ZernioError(text, status=status, body=body, uncertain=uncertain)

    def _reason(self, body: Any) -> str:
        """Zernio's own words for what went wrong, on one line, without the key."""
        text = ""
        if isinstance(body, dict):
            for name in ("error", "message"):
                value = body.get(name)
                if isinstance(value, dict):
                    value = value.get("message") or value.get("error")
                if isinstance(value, str) and value.strip():
                    text = value
                    break
        return " ".join(text.replace(self.key, "[key]").split())[:200]

    # --- media -------------------------------------------------------------------------------------

    def upload(self, path: Path) -> str:
        """Upload a file to Zernio's storage and return the public address to use in a post."""
        path = Path(path)
        try:
            size = path.stat().st_size
        except OSError:
            raise ZernioError(f"The file {path.name} cannot be read, so nothing was uploaded.") from None
        content_type = mimetypes.guess_type(path.name)[0] or "video/mp4"
        slot = self.call(
            "POST", "/media/presign", json={"filename": path.name, "contentType": content_type, "size": size}
        )
        target, public = slot.get("uploadUrl"), slot.get("publicUrl")
        if not (isinstance(target, str) and target and isinstance(public, str) and public):
            raise ZernioError("Zernio did not give an upload address.")
        try:
            with path.open("rb") as stream:
                # No Authorization header here: the storage address needs none and is not Zernio.
                sent = requests.put(target, data=stream, headers={"Content-Type": content_type}, timeout=UPLOAD_TIMEOUT)
        except (requests.RequestException, OSError):
            raise ZernioError("The file upload was interrupted. Nothing was posted.") from None
        if not sent.ok:
            raise ZernioError(f"The file upload failed (HTTP {sent.status_code}). Nothing was posted.")
        return public

    # --- accounts ----------------------------------------------------------------------------------

    def accounts(self) -> dict[str, list[dict]]:
        """The creator's connected accounts by platform, from one call.

        Every platform the kit posts to is a key, with an empty list when nothing is connected there.
        An account counts as connected when it is active, does not need reconnecting and is not
        switched off. Accounts on platforms the kit does not use are left out.
        """
        found = self.call("GET", "/accounts").get("accounts")
        grouped: dict[str, list[dict]] = {name: [] for name in config.PLATFORMS}
        for account in found if isinstance(found, list) else []:
            if isinstance(account, dict) and account.get("platform") in grouped and _connected(account):
                grouped[account["platform"]].append(account)
        return grouped

    # --- posts -------------------------------------------------------------------------------------

    def create_post(self, body: dict, request_id: str) -> dict:
        """Create a post, sending `request_id` as both X-Request-Id and Idempotency-Key.

        Zernio returns the first post again when the same Idempotency-Key arrives twice within 24 hours,
        even if the body has changed. X-Request-Id does that only when the body is the same, and a changed
        body with the same X-Request-Id makes a new post, so the kit sends both.
        """
        headers = {"X-Request-Id": request_id, "Idempotency-Key": request_id}
        return self.call("POST", "/posts", json=body, headers=headers, timeout=CREATE_TIMEOUT)

    def get_post(self, post_id: str) -> dict:
        """The post itself, as Zernio holds it."""
        answer = self.call("GET", f"/posts/{_segment(post_id)}")
        post = answer.get("post")
        return post if isinstance(post, dict) else answer

    def update_post(self, post_id: str, body: dict) -> dict:
        """Change a post that has not gone out yet, such as its time. Zernio answers with the changed post."""
        return self.call("PUT", f"/posts/{_segment(post_id)}", json=body)

    def delete_post(self, post_id: str) -> None:
        """Delete a post that has not been published. Zernio refuses to delete one that is live."""
        self.call("DELETE", f"/posts/{_segment(post_id)}")

    def find_post_by_title(self, title: str, *, profile_id: str | None = None) -> dict | None:
        """The newest post whose title is exactly `title`, or None.

        It reads the newest posts first, one hundred at a time, and gives up after five pages.
        A post made a moment ago is near the top, which is what recovering an interrupted create needs.
        """
        wanted = " ".join(str(title).split())
        for page in range(1, LIST_MAX_PAGES + 1):
            params: dict[str, Any] = {"limit": LIST_PAGE_SIZE, "page": page, "sortBy": "created-desc"}
            if profile_id:
                params["profileId"] = profile_id
            answer = self.call("GET", "/posts", params=params)
            posts = answer.get("posts") if isinstance(answer.get("posts"), list) else []
            for post in posts:
                if isinstance(post, dict) and " ".join(str(post.get("title") or "").split()) == wanted:
                    return post
            paging = answer.get("pagination") if isinstance(answer.get("pagination"), dict) else {}
            last = paging.get("pages")
            if not posts or (isinstance(last, int) and page >= last):
                break
        return None


def post_of(answer: Mapping) -> dict:
    """The post inside an answer to a create or an update. Zernio calls it post, or existingPost on a repeat."""
    for name in ("post", "existingPost"):
        post = answer.get(name)
        if isinstance(post, dict):
            return post
    return {}


# --- helpers ----------------------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _due(kw: dict) -> datetime | None:
    """The time a request asks Zernio to post at, when its JSON body carries one with a zone on it."""
    body = kw.get("json")
    value = body.get("scheduledFor") if isinstance(body, dict) else None
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else None


def _segment(value: str) -> str:
    return quote(str(value), safe="")


def _connected(account: Mapping) -> bool:
    return bool(account.get("isActive")) and not account.get("needsReconnection") and account.get("enabled") is not False


def _read(response: requests.Response) -> tuple[bool, Any]:
    """(readable, value): an empty answer is readable and has no value."""
    if not response.content.strip():
        return True, None
    try:
        return True, response.json()
    except ValueError:
        return False, None


def _wait(response: requests.Response, attempt: int) -> float:
    """Seconds to wait before trying again: what Retry-After asks, else a growing pause, never over a minute."""
    try:
        seconds = float((response.headers.get("Retry-After") or "").strip())
    except ValueError:
        seconds = math.nan
    if not math.isfinite(seconds):
        seconds = 5.0 * (attempt + 1)
    return min(max(seconds, 0.0), MAX_WAIT)
