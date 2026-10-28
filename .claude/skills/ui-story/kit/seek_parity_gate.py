#!/usr/bin/env python3
"""seek_parity_gate.py: catch "the snapshot shows it, the render does not" BEFORE a heavy render.

`tl.fromTo(el, {opacity: 1, ...}, {..., immediateRender: false})` over a `gsap.set(el, {opacity: 0})`, with
opacity only in the FROM values, shows in `hyperframes snapshot` but not in `hyperframes render`. The
renderer first seeks the timeline to its end, then renders 0 to the end frame by frame (each frame is
totalTime(t + 0.001) then totalTime(t)). Crossing the tween backwards reverts its start values to opacity 0,
and going forward again never applies them. The fix: gsap.set(el, {opacity: 0}); tl.to(el, {opacity: 1}, t).

This gate loads the composition twice in a headless browser:
  reference  a fresh page, seeking forward sparsely to each probe time (what snapshot sees)
  render     seek to the end, back to 0, then every frame from 0 to the probe time at 60 fps with the
             renderer's double seek
and compares the effective opacity (its own times its parents', visibility and display) and the transform
of every element with an id at each probe time. Caption plates are left out (they have their own gate).

    python seek_parity_gate.py <hf dir>/index.html [--step 0.5] [--times t1,t2,...]   (exit 1 on any difference)
"""
import argparse
import sys
import time
from pathlib import Path

from browser import NoBrowser, launch
from playwright.sync_api import sync_playwright

FINGERPRINT = r"""() => {
  const out = {};
  document.querySelectorAll('[id]').forEach(e => {
    if (e.closest('svg') && e.tagName.toLowerCase() !== 'svg') return;
    if (/^cap/.test(e.id)) return;
    let op = 1, n = e;
    while (n && n.nodeType === 1) {
      const cs = getComputedStyle(n);
      if (cs.display === 'none') { op = 0; break; }
      op *= +cs.opacity; n = n.parentElement;
    }
    const cs = getComputedStyle(e);
    if (cs.visibility === 'hidden') op = 0;
    out[e.id] = [Math.round(op * 100) / 100, cs.transform];
  });
  return out;
}"""


def matrix(value):
    if not value or value == "none":
        return [1, 0, 0, 1, 0, 0]
    if value.startswith("matrix("):
        return [float(v) for v in value[7:-1].split(",")]
    if value.startswith("matrix3d("):
        v = [float(x) for x in value[9:-1].split(",")]
        return [v[0], v[1], v[4], v[5], v[12], v[13]]
    return [1, 0, 0, 1, 0, 0]


def main():
    ap = argparse.ArgumentParser(description="Compare snapshot seeks with the renderer's seek order.")
    ap.add_argument("html")
    ap.add_argument("--step", type=float, default=0.5)
    ap.add_argument("--times", default="")
    a = ap.parse_args()
    page_uri = Path(a.html).resolve().as_uri()
    bad = 0
    try:
        with sync_playwright() as p:
            browser = launch(p)

            def load():
                page = browser.new_page(viewport={"width": 1080, "height": 1920})
                page.add_init_script("window.__timelines = window.__timelines || {};")
                page.goto(page_uri)
                time.sleep(1.5)
                return page

            ref = load()
            dur = ref.evaluate("window.__timelines.main.duration()")
            times = [float(x) for x in a.times.split(",") if x] or [round(i * a.step, 3) for i in range(1, int(dur / a.step))]
            reference = {}
            for t in times:
                ref.evaluate("t => { const tl = window.__timelines.main; tl.pause(); tl.totalTime(t + 0.001, true); tl.totalTime(t, false); }", t)
                reference[t] = ref.evaluate(FINGERPRINT)
            ren = load()
            ren.evaluate("() => { const tl = window.__timelines.main; tl.pause(); tl.totalTime(tl.duration(), true); tl.totalTime(0, true); }")
            k = 0
            for t in times:
                k1 = int(round(t * 60))
                ren.evaluate("([a, z]) => { const tl = window.__timelines.main; for (let k = a; k <= z; k++) { const t = k / 60; tl.totalTime(t + 0.001, true); tl.totalTime(t, false); } }", [k, k1])
                k = k1 + 1
                rendered = ren.evaluate(FINGERPRINT)
                for i, (op, tr) in reference[t].items():
                    if i not in rendered:
                        continue
                    op2, tr2 = rendered[i]
                    m1, m2 = matrix(tr), matrix(tr2)
                    moved = max(abs(m1[4] - m2[4]), abs(m1[5] - m2[5]))
                    scaled = max(abs(m1[0] - m2[0]), abs(m1[3] - m2[3]))
                    if abs(op - op2) > 0.08 or moved > 3 or scaled > 0.03:
                        bad += 1
                        if bad <= 60:
                            print(f"DIFF t={t:.3f} #{i}: snapshot opacity {op} {tr} | render opacity {op2} {tr2}")
            browser.close()
    except NoBrowser as problem:
        print(problem)
        sys.exit(1)
    print(f"seek parity: {len(times)} probe times, {bad} difference(s)")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
