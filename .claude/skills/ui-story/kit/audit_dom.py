#!/usr/bin/env python3
"""audit_dom.py: the reading-load audit, measured from the real composition.

It seeks the paused timeline every --step seconds in a headless browser and records every visible piece of
text at --min-px or larger (so window chrome and tiny labels are skipped). A counter's changing spans are
merged into one entry, named by the value it lands on, and typed text (one span a character) counts as the
whole line it types, from its first letter on screen. UIS.count and UIS.type mark their elements, so a count
to a single digit is never taken for typing. For each text it reports the readable words, the seconds it is on
screen after the gap trims and the speed-up, and the seconds it needs (0.35 s a word plus 0.6 s, counting at
most 8 words), then OK or SHORT (within 0.15 s is fine). Captions are left out.

The house limits it checks against: one idea and about six readable words a visual, on screen long enough
to read. A SHORT row is allowed only when the speech binds it (two cards on one sentence); say so in the
sources file. Fix a short row by timing first: start the chip on the sentence that already names the idea.

    python audit_dom.py <hf dir>/index.html <blocks.json> <base.words.json> [--speed 1.15] [--min-px 24]
        [--step 0.1] [--ignore REGEX]
"""
import argparse
import json
import re
import sys
from collections import OrderedDict
from pathlib import Path

from browser import NoBrowser, launch
from playwright.sync_api import sync_playwright

JS = """([t, minpx]) => {
  const tl = window.__timelines.main; tl.time(t);
  const out = [];
  const seen = new Set();
  for (const el of document.querySelectorAll('#root *')) {
    if (el.closest('.cap')) continue;
    if (['SCRIPT','STYLE','VIDEO','IMG','SVG','PATH','DEFS','G'].includes(el.tagName.toUpperCase()) || el.namespaceURI.includes('svg')) continue;
    let own = '';
    for (const n of el.childNodes) if (n.nodeType === 3) own += n.textContent;
    own = own.replace(/\\s+/g, ' ').trim();
    if (!own) continue;
    let n = el, op = 1, vis = true;
    while (n && n.id !== 'root') {
      const cs = getComputedStyle(n);
      if (cs.display === 'none' || cs.visibility === 'hidden') { vis = false; break; }
      op *= parseFloat(cs.opacity); n = n.parentElement;
    }
    if (!vis || op < 0.55) continue;
    if (parseFloat(getComputedStyle(el).fontSize) < minpx) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4 || r.right < 0 || r.left > 1080 || r.bottom < 0 || r.top > 1920) continue;
    let key = el, text = own, ident = own;
    const par = el.parentElement;
    const kids = el.tagName === 'SPAN' && par ? Array.from(par.querySelectorAll(':scope > span')) : [];
    // UIS.count and UIS.type mark their elements; one span a character means typing only when nothing says count
    const counted = kids.length > 0 && par.hasAttribute('data-uis-count');
    const typed = kids.length > 0 && par.hasAttribute('data-uis-typed');
    const oneChar = kids.length >= 2 && kids.every(k => Array.from(k.textContent).length <= 1);
    if (counted || (!typed && !oneChar && kids.length >= 5)) {
      // a count: one span a value, one shown at a time, so the row is the value it lands on
      key = par; ident = kids[kids.length - 1].textContent.replace(/\\s+/g, ' ').trim(); text = '(counter) ' + ident;
    } else if (typed || oneChar) {
      // typed text: one span a character, so the row is the whole line it types
      key = par; ident = par.textContent.replace(/\\s+/g, ' ').trim(); text = '(typed) ' + ident;
    }
    const sc = el.closest('.scene');
    const id = (key.id || '') + '|' + (sc ? sc.id : '') + '|' + (key.className && key.className.baseVal === undefined ? key.className : '') + '|' + (key.id ? '' : ident.slice(0, 40));
    if (seen.has(id)) continue; seen.add(id);
    out.push({id, text: key === par ? text : own, scene: sc ? sc.id : ''});
  }
  return out;
}"""


def main():
    ap = argparse.ArgumentParser(description="The reading-load audit of a composition.")
    ap.add_argument("index")
    ap.add_argument("blocks")
    ap.add_argument("words")
    ap.add_argument("--speed", type=float, default=1.15)
    ap.add_argument("--min-px", type=float, default=24)
    ap.add_argument("--step", type=float, default=0.1)
    ap.add_argument("--ignore", default=r"^(Ask anything|Add a comment|Post)$", help="texts to leave out (a regex)")
    a = ap.parse_args()
    blocks = json.load(open(a.blocks))["A"]
    D = json.load(open(a.words))["dur"]

    def ship(t):
        total = 0.0
        for s, e in blocks:
            e = D if e == "end" else e
            total += max(0.0, min(t, e) - s)
        return total / a.speed

    rows = OrderedDict()
    try:
        with sync_playwright() as p:
            browser = launch(p)
            page = browser.new_page(viewport={"width": 1080, "height": 1920})
            page.add_init_script("window.__timelines = window.__timelines || {};")
            page.goto(Path(a.index).resolve().as_uri())
            page.wait_for_timeout(1500)
            dur = page.evaluate("window.__timelines.main.duration()")
            t = 0.0
            while t < dur:
                for o in page.evaluate(JS, [t, a.min_px]):
                    row = rows.setdefault(o["id"], {"text": o["text"], "scene": o["scene"], "t": []})
                    if len(o["text"]) > len(row["text"]) and not o["text"].startswith("(counter"):
                        row["text"] = o["text"]
                    row["t"].append(round(t, 3))
                t += a.step
            browser.close()
    except NoBrowser as problem:
        print(problem)
        sys.exit(1)

    ignore = re.compile(a.ignore, re.I)
    print("| Scene | On-screen text | Words | Shown (s, shipped) | Needed (0.35 s a word + 0.6) | OK |")
    print("|---|---|---|---|---|---|")
    short, items = 0, []
    for row in rows.values():
        times = row["t"]
        plain = re.sub(r"^\((?:typed|counter)\)", "", row["text"]).strip()
        if ignore.search(plain) or (len(plain) <= 2 and not re.search(r"\d|%", plain)):
            continue
        words = [w for w in re.split(r"\s+", plain) if re.search(r"[A-Za-z0-9$%]", w)]
        n = max(1, len(words))
        base_on = len(times) * a.step
        if times and (times[-1] - times[0] + a.step) <= base_on + 0.25:
            shown = round(ship(times[0] + base_on) - ship(times[0]), 2)
        else:
            shown = round(sum(ship(x + a.step) - ship(x) for x in times), 2)
        need = round(0.35 * min(n, 8) + 0.6, 2)
        ok = "yes" if shown >= need - 0.15 else "SHORT"
        items.append((times[0], row["scene"], row["text"], n, shown, need, ok))
    for _, scene, text, n, shown, need, ok in sorted(items, key=lambda x: x[0]):
        short += ok != "yes"
        print(f"| {scene} | {text[:90]} | {n} | {shown} | {need} | {ok} |")
    print("short:", short)


if __name__ == "__main__":
    main()
