#!/usr/bin/env python3
"""record_page.py: a crisp "screen recording" of a real web page, for a product-in-use card.

Use it for pictures, video and UI flows only: a page that shows an image, plays a video, or a UI doing
something. Never use it to scroll or highlight running text (a README, docs, an article, a table): take
the one sentence or number into a designed quote, headline or stat card instead.

It takes a screenshot per frame while the page scrolls on an eased path, and joins them into an H.264
clip with no sound, 1080 px wide.

  --dpr N                  device scale (3 suits a narrow, phone-width viewport)
  --from-text / --to-text  scroll so the first element containing this text sits --offset px below the top
  --js "code"              JavaScript run once after load (hide an overlay, open a tab)
  --mark "a|b|c"           after the last frame, measure where each string sits and write <out>.json with the
                           boxes in OUTPUT pixels (the 1080-wide clip), so a ring or zoom can land on them
                           without guessing. "css::a" limits the search to a selector.
  window.__sync()          is called after every scroll step when the page defines it

A cookie banner is declined with its "Reject" style button when there is one.

    python record_page.py <url> <out.mp4> [--vw 640 --vh 560 --dpr 3] [--secs 4] [--from-text S | --from-y PX]
        [--to-text S | --to-y PX] [--offset -40] [--hold 0.6] [--fps 30] [--wait 4] [--hide CSS,CSS]
        [--mark "a|b"] [--still out.png]
"""
import argparse
import json
import subprocess
import tempfile
import time
from pathlib import Path

from browser import launch
from playwright.sync_api import sync_playwright

REJECT = ["Reject all", "Reject All", "Decline", "Necessary only", "Only necessary", "Reject non-essential",
          "Reject", "Deny", "Decline all"]

FIND_JS = """([s, sel]) => {
  const scope = sel ? (document.querySelector(sel) || document.body) : document.body;
  const w = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = w.nextNode())) {
    const i = n.nodeValue.indexOf(s);
    if (i < 0) continue;
    const el = n.parentElement;
    if (!el || el.offsetParent === null && getComputedStyle(el).position !== 'fixed') continue;
    const r = document.createRange(); r.setStart(n, i); r.setEnd(n, i + s.length);
    const b = r.getBoundingClientRect();
    if (b.width === 0) continue;
    const lines = Array.from(r.getClientRects()).filter(q => q.width > 2).map(q => [q.left, q.top, q.right, q.bottom]);
    return {x0: b.left, y0: b.top, x1: b.right, y1: b.bottom, docY: b.top + window.scrollY, lines: lines};
  }
  const all = Array.from(scope.querySelectorAll('p,li,h1,h2,h3,h4,td,th,span,a,div,code,pre,blockquote'));
  for (const el of all) {
    if (!el.textContent.includes(s)) continue;
    if (Array.from(el.children).some(c => c.textContent.includes(s))) continue;
    const b = el.getBoundingClientRect();
    if (b.width === 0) continue;
    return {x0: b.left, y0: b.top, x1: b.right, y1: b.bottom, docY: b.top + window.scrollY, whole: true};
  }
  return null;
}"""


def ease(x):
    return 4 * x**3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def main():
    ap = argparse.ArgumentParser(description="A screen recording of a real page, for pictures, video and UI flows.")
    ap.add_argument("url")
    ap.add_argument("out")
    ap.add_argument("--vw", type=int, default=640)
    ap.add_argument("--vh", type=int, default=560)
    ap.add_argument("--dpr", type=float, default=3)
    ap.add_argument("--secs", type=float, default=4.0)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--hold", type=float, default=0.6)
    ap.add_argument("--wait", type=float, default=4.0)
    ap.add_argument("--from-text")
    ap.add_argument("--from-y", type=float, default=0)
    ap.add_argument("--to-text")
    ap.add_argument("--to-y", type=float)
    ap.add_argument("--offset", type=float, default=-40, help="px above a text target")
    ap.add_argument("--hide", default="")
    ap.add_argument("--css", default="", help="extra CSS added after load")
    ap.add_argument("--js", default="", help="JavaScript run once after load")
    ap.add_argument("--mark", default="", help='"a|b" or "css::a|b" (search inside a selector)')
    ap.add_argument("--hide-bottom", action="store_true", help="hide fixed layers in the bottom 40%%")
    ap.add_argument("--hide-top", action="store_true", help="hide fixed layers in the top 40%% (sticky headers)")
    ap.add_argument("--still")
    a = ap.parse_args()
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    k = 1080 / a.vw  # output px per CSS px
    with sync_playwright() as p, tempfile.TemporaryDirectory() as td:
        browser = launch(p)
        ctx = browser.new_context(viewport={"width": a.vw, "height": a.vh}, device_scale_factor=a.dpr, locale="en-US",
                                  color_scheme="light")
        page = ctx.new_page()
        page.goto(a.url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(a.wait)
        for label in REJECT:
            try:
                button = page.get_by_role("button", name=label, exact=True)
                if button.count():
                    button.first.click(timeout=1500)
                    time.sleep(0.6)
                    break
            except Exception:
                pass
        if a.hide:
            selectors = ",".join(s.strip() for s in a.hide.split(",") if s.strip())
            page.add_style_tag(content=f"{selectors}{{visibility:hidden !important}}")
        page.add_style_tag(content="html{scroll-behavior:auto !important} ::-webkit-scrollbar{display:none}" + a.css)
        if a.hide_bottom:
            page.evaluate("""() => { for (const el of document.querySelectorAll('body *')) {
                const cs = getComputedStyle(el); if (cs.position !== 'fixed' && cs.position !== 'sticky') continue;
                const r = el.getBoundingClientRect(); if (r.top > innerHeight * 0.6) el.style.visibility = 'hidden'; } }""")
        if a.hide_top:
            page.evaluate("""() => { for (const el of document.querySelectorAll('body *')) {
                const cs = getComputedStyle(el); if (cs.position !== 'fixed' && cs.position !== 'sticky') continue;
                const r = el.getBoundingClientRect(); if (r.top < innerHeight * 0.4 && r.height < innerHeight * 0.6) el.style.visibility = 'hidden'; } }""")
        if a.js:
            page.evaluate(a.js)
        time.sleep(0.5)

        def y_of(text, default):
            if not text:
                return default
            sel, _, t = text.rpartition("::")
            found = page.evaluate(FIND_JS, [t, sel or None])
            if found is None:
                raise SystemExit(f"That text is not on the page: {text!r}")
            return found["docY"] + a.offset

        y0 = y_of(a.from_text, a.from_y)
        y1 = y_of(a.to_text, a.to_y if a.to_y is not None else y0)
        top = page.evaluate("document.documentElement.scrollHeight - window.innerHeight")
        y0, y1 = max(0, min(y0, top)), max(0, min(y1, top))
        n, nh = int(round(a.secs * a.fps)), int(round(a.hold * a.fps))
        for s in range(6):  # pass over the path once so lazy images load
            page.evaluate(f"window.scrollTo(0,{y0 + (y1 - y0) * s / 5})")
            time.sleep(0.4)
        page.evaluate(f"window.scrollTo(0,{y0})")
        time.sleep(1.0)
        marks = [m for m in a.mark.split("|") if m]

        def measure():
            boxes = {}
            for m in marks:
                sel, _, t = m.rpartition("::")
                found = page.evaluate(FIND_JS, [t, sel or None])
                boxes[m] = None if found is None else [round(found[c] * k, 1) for c in ("x0", "y0", "x1", "y1")]
                if found is not None and len(found.get("lines") or []) > 1:
                    boxes[m + " #lines"] = [[round(v * k, 1) for v in q] for q in found["lines"]]
            return boxes

        start_marks = measure()
        for i in range(n):
            x = 0.0 if i < nh else 1.0 if i >= n - nh else ease((i - nh) / max(1, n - 2 * nh - 1))
            page.evaluate(f"window.scrollTo(0,{y0 + (y1 - y0) * x:.1f}); window.__sync && window.__sync()")
            page.screenshot(path=f"{td}/f{i:04d}.png")
        end_marks = measure()
        if a.still:
            page.screenshot(path=a.still)
        browser.close()
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-framerate", str(a.fps), "-i", f"{td}/f%04d.png",
                        "-vf", "scale=1080:-2:flags=lanczos,format=yuv420p", "-c:v", "libx264", "-preset", "slow",
                        "-crf", "16", "-an", "-movflags", "+faststart", str(out)], check=True)
    meta = {"url": a.url, "vw": a.vw, "vh": a.vh, "dpr": a.dpr, "out_w": 1080, "out_h": round(a.vh * k),
            "px_per_css": k, "secs": a.secs, "hold": a.hold, "scroll": [y0, y1],
            "marks_start": start_marks, "marks_end": end_marks, "recorded": time.strftime("%Y-%m-%d %H:%M")}
    out.with_suffix(".json").write_text(json.dumps(meta, indent=1))
    print(f"{out}  scroll {y0:.0f}->{y1:.0f}px over {a.secs}s  marks at the end: {end_marks}")


if __name__ == "__main__":
    main()
