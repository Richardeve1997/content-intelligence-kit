"""browser.py: start a headless Chromium for the kit's page checks and recordings.

Playwright's own Chromium is used first (`uv run playwright install chromium` downloads it), then an
installed Google Chrome. Both fail as NoBrowser with the command that fixes it.
"""

from __future__ import annotations

ARGS = ["--allow-file-access-from-files"]


class NoBrowser(RuntimeError):
    pass


def launch(playwright, **options):
    """A started browser from a `sync_playwright()` handle."""
    args = list(options.pop("args", [])) + ARGS
    try:
        return playwright.chromium.launch(args=args, **options)
    except Exception as first:
        try:
            return playwright.chromium.launch(channel="chrome", args=args, **options)
        except Exception:
            raise NoBrowser(
                "No browser is available for the page checks. Install one with: uv run playwright install chromium"
            ) from first
