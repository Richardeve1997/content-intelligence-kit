"""Posting a finished video through Zernio.

    from kit import post
    post.accounts_check()                      # which platforms are connected in Zernio
    post.schedule(slug, platforms, when)       # one upload, one scheduled post for each platform
    post.list_scheduled(slug)                  # what was scheduled, as Zernio reports it now
    post.reschedule(slug, "instagram", when)   # move one post
    post.cancel(slug, "instagram")             # take one post down before it goes out

Each platform's post is made from work/<slug>/final.mp4 and work/<slug>/captions.json, and the records
are kept in work/<slug>/post/.

`post.schedule` is the function. The module that holds it is kit.post.schedule: import from it with
`from kit.post.schedule import ...`, or fetch it as sys.modules["kit.post.schedule"], because the name
`schedule` on this package belongs to the function.

The same operations run from a shell with `python -m kit.post`, which is how the post skill uses them.
"""

from kit.post import captions, payloads
from kit.post.errors import CaptionProblems, PostError
from kit.post.schedule import (
    Result,
    accounts_check,
    cancel,
    default_when,
    describe_time,
    list_scheduled,
    local_time,
    platform_names,
    reschedule,
    schedule,
    write_manual,
)
from kit.zernio import NoZernio, ZernioError

__all__ = [
    "CaptionProblems",
    "NoZernio",
    "PostError",
    "Result",
    "ZernioError",
    "accounts_check",
    "cancel",
    "captions",
    "default_when",
    "describe_time",
    "list_scheduled",
    "local_time",
    "payloads",
    "platform_names",
    "reschedule",
    "schedule",
    "write_manual",
]
