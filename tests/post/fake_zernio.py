"""A stand-in for the Zernio API that runs on this machine.

Tests point the kit's client at it, so nothing here ever reaches the real service. It answers the
calls the kit makes (accounts, an upload address, the upload itself, and creating, reading, listing,
updating and deleting posts) and records every request. Where the kit depends on how the real API
behaves, the stand-in behaves the same way:

* a create that repeats an earlier X-Request-Id and carries the same content returns the first post
  and makes nothing new, while the same X-Request-Id with different content makes a new post;
* a create that repeats an earlier Idempotency-Key returns the first post whatever the body now says,
  and the key takes precedence over X-Request-Id;
* the same caption and video for the same account, sent again, is refused with the first post's id;
* a scheduled time that has already passed publishes the post at once;
* a published post cannot be deleted;
* a list call with a query parameter the real one does not know is refused.

A test can also make the next create lose its answer (with or without the post being saved), have
the API answer 429 for a while, make the post list lag behind (list_lag), hold a create while it looks
around (before_create), or give a canned reply to one address.
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PLATFORMS = ("instagram", "tiktok", "youtube", "facebook", "linkedin", "twitter")

# The only query parameters the real list call accepts. Any other one is refused with a 400.
LIST_PARAMS = frozenset(
    {
        "page", "limit", "offset", "cursor", "fields", "updatedSince", "source", "status", "platform",
        "profileId", "accountId", "createdBy", "fromDate", "toDate", "dateFrom", "dateTo",
        "includeHidden", "search", "tags", "tag", "sortBy",
    }
)
SORTS = frozenset({"scheduled-desc", "scheduled-asc", "created-desc", "created-asc", "status", "platform"})
EDITABLE = frozenset({"draft", "scheduled", "failed", "partial", "cancelled"})

HANG_UP = object()  # answer by closing the connection without a word


def make_account(
    platform: str,
    *,
    active: bool = True,
    needs_reconnection: bool | None = None,
    enabled: bool | None = None,
    username: str = "@examplestudio",
    display_name: str = "Example Studio",
    profile: str = "profile-main",
    account_id: str | None = None,
) -> dict:
    account = {
        "_id": account_id or f"acct-{platform}",
        "platform": platform,
        "profileId": {"_id": profile, "name": "Example profile", "slug": "example-profile"},
        "username": username,
        "displayName": display_name,
        "profileUrl": f"https://{platform}.example.test/examplestudio",
        "isActive": active,
    }
    if needs_reconnection is not None:
        account["needsReconnection"] = needs_reconnection
    if enabled is not None:
        account["enabled"] = enabled
    return account


def default_accounts(skip: tuple[str, ...] = ()) -> list[dict]:
    return [make_account(platform) for platform in PLATFORMS if platform not in skip]


@dataclass
class Seen:
    """One request the stand-in received."""

    method: str
    path: str
    query: dict
    headers: dict  # names in lower case
    body: object = None  # the parsed JSON, when the body was JSON
    size: int = 0


@dataclass
class _Canned:
    method: str | None
    path: re.Pattern
    status: int | object
    body: object
    headers: dict
    times: int
    delay: float


def _utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _parse_utc(text: str | None) -> datetime | None:
    if not text:
        return None
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *args):  # keep the test output quiet
        pass

    def _serve(self):
        fake: FakeZernio = self.server.fake
        url = urlsplit(self.path)
        size = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(size) if size else b""
        body = None
        if raw and "json" in (self.headers.get("Content-Type") or ""):
            try:
                body = json.loads(raw)
            except ValueError:
                body = None
        seen = Seen(
            method=self.command,
            path=url.path,
            query=dict(parse_qsl(url.query, keep_blank_values=True)),
            headers={name.lower(): value for name, value in self.headers.items()},
            body=body,
            size=len(raw),
        )
        fake._record(seen)
        canned = fake._take_canned(seen)
        if canned is not None:
            if canned.delay:
                time.sleep(canned.delay)
            outcome = HANG_UP if canned.status is HANG_UP else (canned.status, canned.body, canned.headers)
        else:
            if fake.before_create is not None and seen.method == "POST" and seen.path == "/api/v1/posts":
                fake.before_create(seen)
            outcome = fake.respond(seen, raw)
        if outcome is HANG_UP:
            self.close_connection = True
            try:
                self.connection.shutdown(2)  # SHUT_RDWR
            except OSError:
                pass
            return
        status, reply, headers = outcome
        if isinstance(reply, bytes):
            payload = reply
        else:
            payload = b"" if reply is None else json.dumps(reply).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = do_PUT = do_DELETE = _serve


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, fake: FakeZernio):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.fake = fake

    def handle_error(self, request, client_address):  # a client that gives up is not a test failure
        pass


class FakeZernio:
    def __init__(self, key: str = "fake-value-for-tests", accounts: list[dict] | None = None):
        self.key = key
        self.accounts = default_accounts() if accounts is None else list(accounts)
        self.posts: dict[str, dict] = {}
        self.seen: list[Seen] = []
        self.uploads: list[dict] = []
        # One entry is used up by each create: "after" saves the post and then loses the answer,
        # "before" loses the answer without saving anything.
        self.drop_creates: list[str] = []
        self.rate_limits = 0  # how many of the next API requests are answered with 429
        self.list_lag = False  # when True the post list shows nothing, as if it had not caught up yet
        # Called with the request when a create arrives, before the stand-in does anything with it and
        # outside its own lock, so a test may hold the request here while it looks at the stand-in.
        self.before_create = None
        self._canned: list[_Canned] = []
        self._request_ids: dict[str, tuple[tuple, str]] = {}
        self._idempotency_keys: dict[str, str] = {}
        self._order: dict[str, int] = {}
        self._count = 0
        self._uploads_issued = 0
        self._lock = threading.RLock()
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None
        self.port = 0

    # --- running it ------------------------------------------------------------------------------

    def start(self) -> FakeZernio:
        self._server = _Server(self)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._thread.join(timeout=5)
            self._server = None

    def __enter__(self) -> FakeZernio:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    @property
    def root(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def base(self) -> str:
        return self.root + "/api/v1"

    def client(self, key: str | None = None):
        from kit.zernio import Client

        return Client(self.key if key is None else key, base=self.base)

    # --- what a test can look at -----------------------------------------------------------------

    def calls(self, method: str | None = None, path: str | None = None) -> list[Seen]:
        with self._lock:
            return [
                s for s in self.seen if (method is None or s.method == method) and (path is None or s.path == path)
            ]

    def creates(self) -> list[Seen]:
        return self.calls("POST", "/api/v1/posts")

    def reply(
        self,
        method: str | None,
        path: str,
        status: int | object = 200,
        body: object = None,
        headers: dict | None = None,
        times: int = 1,
        delay: float = 0.0,
    ) -> None:
        """Answer the next `times` requests that match with a fixed reply. `path` is a regular expression."""
        with self._lock:
            self._canned.append(_Canned(method, re.compile(path), status, body, headers or {}, times, delay))

    def seed_post(
        self, title: str, *, platform: str = "instagram", status: str = "scheduled", account_id: str | None = None
    ) -> dict:
        """Put a post straight into the list, as if an earlier run had made it."""
        with self._lock:
            account = self._account(account_id) if account_id else next(a for a in self.accounts if a["platform"] == platform)
            self._count += 1
            post_id = f"post-{self._count}"
            post = {
                "_id": post_id,
                "title": title,
                "content": "",
                "status": status,
                "scheduledFor": "2030-01-01T10:00:00.000Z",
                "timezone": "UTC",
                "mediaItems": [],
                "platforms": [self._entry(platform, account, {}, "pending")],
                "metadata": {},
            }
            self.posts[post_id] = post
            self._order[post_id] = self._count
            return post

    # --- the handler's side ----------------------------------------------------------------------

    def _record(self, seen: Seen) -> None:
        with self._lock:
            self.seen.append(seen)

    def _take_canned(self, seen: Seen) -> _Canned | None:
        with self._lock:
            for canned in self._canned:
                if canned.times > 0 and canned.path.fullmatch(seen.path) and canned.method in (None, seen.method):
                    canned.times -= 1
                    return canned
        return None

    def respond(self, seen: Seen, raw: bytes):
        with self._lock:
            path = seen.path
            if path.startswith("/upload/"):
                return self._take_upload(seen, raw)
            if not path.startswith("/api/v1/"):
                return 404, {"error": "No such address"}, {}
            if self.rate_limits > 0:
                self.rate_limits -= 1
                return 429, {"error": "Rate limit exceeded"}, {"Retry-After": "0"}
            if seen.headers.get("authorization") != f"Bearer {self.key}":
                return 401, {"error": "Invalid or missing API key"}, {}
            route, method = path[len("/api/v1"):], seen.method
            if route == "/accounts" and method == "GET":
                return self._accounts(seen)
            if route == "/media/presign" and method == "POST":
                return self._presign(seen)
            if route == "/posts" and method == "POST":
                return self._create(seen)
            if route == "/posts" and method == "GET":
                return self._list(seen)
            found = re.fullmatch(r"/posts/([^/]+)", route)
            if found:
                if method == "GET":
                    return self._get(found.group(1))
                if method == "PUT":
                    return self._update(found.group(1), seen)
                if method == "DELETE":
                    return self._delete(found.group(1))
            return 404, {"error": "No such address"}, {}

    # accounts and uploads

    def _account(self, account_id: str) -> dict | None:
        return next((a for a in self.accounts if a["_id"] == account_id), None)

    def _accounts(self, seen: Seen):
        wanted = seen.query.get("platform")
        found = [a for a in self.accounts if wanted is None or a["platform"] == wanted]
        return 200, {"accounts": found, "hasAnalyticsAccess": False}, {}

    def _presign(self, seen: Seen):
        body = seen.body if isinstance(seen.body, dict) else {}
        if not (isinstance(body.get("filename"), str) and body.get("contentType") and isinstance(body.get("size"), int)):
            return 400, {"error": "filename, contentType and size are required"}, {}
        self._uploads_issued += 1
        n = self._uploads_issued
        reply = {"uploadUrl": f"{self.root}/upload/{n}", "publicUrl": f"https://media.example.test/{n}/{body['filename']}"}
        return 200, reply, {}

    def _take_upload(self, seen: Seen, raw: bytes):
        self.uploads.append(
            {
                "path": seen.path,
                "size": len(raw),
                "bytes": raw,
                "content_type": seen.headers.get("content-type"),
                "authorization": seen.headers.get("authorization"),
            }
        )
        return 200, None, {}

    # posts

    @staticmethod
    def _entry(platform: str, account: dict, data: dict, status: str) -> dict:
        entry = {
            "platform": platform,
            "accountId": {k: account[k] for k in ("_id", "platform", "username", "displayName", "isActive")},
            "status": status,
            "platformSpecificData": data,
        }
        return entry

    @staticmethod
    def _when(body: dict) -> datetime | None:
        text = body.get("scheduledFor")
        if not text:
            return None
        moment = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=ZoneInfo(body.get("timezone") or "UTC"))
        return moment

    def _validate_new(self, body: dict) -> str | None:
        entries = body.get("platforms")
        if not body.get("isDraft") and not entries:
            return "platforms is required unless the post is a draft"
        for entry in entries or []:
            if not isinstance(entry, dict) or entry.get("platform") not in PLATFORMS:
                return "each platform needs a platform name Zernio knows"
            if self._account(entry.get("accountId")) is None:
                return "accountId does not match a connected account"
            data = entry.get("platformSpecificData") or {}
            if entry["platform"] == "youtube" and len(data.get("title") or "") > 100:
                return "A YouTube title can be at most 100 characters"
        for item in body.get("mediaItems") or []:
            if not str(item.get("url", "")).startswith("http"):
                return "each media item needs a public https url"
        if not body.get("content") and not body.get("mediaItems"):
            return "content is required unless media is attached"
        try:
            ZoneInfo(body.get("timezone") or "UTC")
            self._when(body)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            return "timezone or scheduledFor is not valid"
        return None

    @staticmethod
    def _fingerprint(body: dict) -> tuple:
        entry = (body.get("platforms") or [{}])[0]
        media = tuple(item.get("url") for item in body.get("mediaItems") or [])
        return entry.get("platform"), entry.get("accountId"), body.get("content"), media

    def _make_post(self, body: dict) -> dict:
        self._count += 1
        post_id = f"post-{self._count}"
        now = datetime.now(timezone.utc)
        when = self._when(body)
        if body.get("isDraft"):
            status = "draft"
        elif body.get("publishNow") or when is None or when <= now:
            status = "published"
        else:
            status = "scheduled"
        entries = []
        for item in body.get("platforms") or []:
            data = dict(item.get("platformSpecificData") or {})
            if item["platform"] == "tiktok" and isinstance(body.get("tiktokSettings"), dict):
                data = {**body["tiktokSettings"], **data}
            entry = self._entry(item["platform"], self._account(item["accountId"]), data, "pending")
            entries.append(entry)
        post = {
            "_id": post_id,
            "title": body.get("title"),
            "content": body.get("content", ""),
            "status": status,
            "scheduledFor": None if when is None else _utc(when),
            "timezone": body.get("timezone") or "UTC",
            "mediaItems": body.get("mediaItems") or [],
            "platforms": entries,
            "metadata": body.get("metadata") or {},
            "createdAt": now.isoformat(),
            "updatedAt": now.isoformat(),
        }
        self._order[post_id] = self._count
        if status == "published":
            self._publish(post)
        return post

    @staticmethod
    def _publish(post: dict) -> None:
        post["status"] = "published"
        for entry in post["platforms"]:
            entry["status"] = "published"
            entry["platformPostId"] = f"native-{post['_id']}"
            entry["platformPostUrl"] = f"https://social.example.test/{entry['platform']}/{post['_id']}"

    def _create(self, seen: Seen):
        body = seen.body
        if not isinstance(body, dict):
            return 400, {"error": "The body must be JSON"}, {}
        problem = self._validate_new(body)
        if problem:
            return 400, {"error": problem}, {}
        if self.drop_creates and self.drop_creates[0] == "before":
            self.drop_creates.pop(0)
            return HANG_UP
        fingerprint = self._fingerprint(body)
        key = seen.headers.get("idempotency-key")
        request_id = None if key else seen.headers.get("x-request-id")  # the key takes precedence
        if key and key in self._idempotency_keys and self._idempotency_keys[key] in self.posts:
            return 200, {"post": self.posts[self._idempotency_keys[key]]}, {}  # whatever the body now says
        if request_id and request_id in self._request_ids:
            earlier, post_id = self._request_ids[request_id]
            if earlier == fingerprint and post_id in self.posts:
                return 200, {"post": self.posts[post_id]}, {}
        for existing in self.posts.values():
            entry = (existing["platforms"] or [{}])[0]
            media = tuple(item.get("url") for item in existing["mediaItems"])
            same = (entry.get("platform"), entry.get("accountId", {}).get("_id"), existing["content"], media)
            if same == fingerprint and existing["status"] != "draft":
                reply = {
                    "error": "Duplicate post: the same content went to this account within 24 hours",
                    "accountId": fingerprint[1],
                    "platform": fingerprint[0],
                    "existingPostId": existing["_id"],
                }
                return 409, reply, {}
        post = self._make_post(body)
        self.posts[post["_id"]] = post
        if key:
            self._idempotency_keys[key] = post["_id"]
        elif request_id:
            self._request_ids[request_id] = (fingerprint, post["_id"])
        if self.drop_creates and self.drop_creates[0] == "after":
            self.drop_creates.pop(0)
            return HANG_UP
        return 201, {"post": post}, {}

    def _get(self, post_id: str):
        post = self.posts.get(post_id)
        if post is None:
            return 404, {"error": "Post not found"}, {}
        return 200, {"post": post}, {}

    def _update(self, post_id: str, seen: Seen):
        post = self.posts.get(post_id)
        if post is None:
            return 404, {"error": "Post not found"}, {}
        if post["status"] not in EDITABLE:
            return 400, {"error": f"A {post['status']} post cannot be edited"}, {}
        body = seen.body if isinstance(seen.body, dict) else {}
        for name in ("content", "title", "mediaItems", "timezone", "metadata"):
            if name in body:
                post[name] = body[name]
        if "scheduledFor" in body:
            moment = self._when({"scheduledFor": body["scheduledFor"], "timezone": body.get("timezone") or post["timezone"]})
            post["scheduledFor"] = None if moment is None else _utc(moment)
        status = post["status"]
        if body.get("isDraft") is True:
            status = "draft"
        elif body.get("isDraft") is False:
            status = "scheduled"
        if status == "scheduled":
            moment = _parse_utc(post["scheduledFor"])
            if body.get("publishNow") or moment is None or moment <= datetime.now(timezone.utc):
                self._publish(post)
                status = "published"
        post["status"] = status
        post["updatedAt"] = datetime.now(timezone.utc).isoformat()
        summary = {k: post[k] for k in ("_id", "content", "status", "scheduledFor")}
        return 200, {"message": "Post updated successfully", "post": summary}, {}

    def _delete(self, post_id: str):
        post = self.posts.get(post_id)
        if post is None:
            return 404, {"error": "Post not found"}, {}
        if post["status"] == "published":
            return 400, {"error": "Published posts cannot be deleted; use the Unpublish endpoint instead."}, {}
        del self.posts[post_id]
        return 200, {"message": "Post deleted successfully"}, {}

    def _list(self, seen: Seen):
        query = seen.query
        unknown = sorted(set(query) - LIST_PARAMS)
        if unknown:
            accepted = ", ".join(sorted(LIST_PARAMS))
            return 400, {"error": f"Unknown query parameter: {', '.join(unknown)}. Accepted: {accepted}"}, {}
        try:
            limit, page = int(query.get("limit", 10)), int(query.get("page", 1))
        except ValueError:
            return 400, {"error": "page and limit must be whole numbers"}, {}
        sort = query.get("sortBy", "scheduled-desc")
        if not 1 <= limit <= 500 or page < 1 or sort not in SORTS:
            return 400, {"error": "page, limit or sortBy is out of range"}, {}
        posts = [] if self.list_lag else list(self.posts.values())
        if "status" in query:
            posts = [p for p in posts if p["status"] == query["status"]]
        if "platform" in query:
            posts = [p for p in posts if any(e["platform"] == query["platform"] for e in p["platforms"])]
        if "accountId" in query:
            posts = [p for p in posts if any(e["accountId"]["_id"] == query["accountId"] for e in p["platforms"])]
        if "profileId" in query and query["profileId"] not in ("", "all"):
            def in_profile(post: dict) -> bool:
                for entry in post["platforms"]:
                    account = self._account(entry["accountId"]["_id"])
                    if account and account["profileId"]["_id"] == query["profileId"]:
                        return True
                return False

            posts = [p for p in posts if in_profile(p)]
        if sort.startswith("created"):
            posts.sort(key=lambda p: self._order[p["_id"]], reverse=sort.endswith("desc"))
        else:
            posts.sort(key=lambda p: p["scheduledFor"] or "", reverse=sort.endswith("desc"))
        total = len(posts)
        pages = max(1, math.ceil(total / limit))
        window = posts[(page - 1) * limit : page * limit]
        return 200, {"posts": window, "pagination": {"page": page, "limit": limit, "total": total, "pages": pages}}, {}
