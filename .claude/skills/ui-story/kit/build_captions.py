#!/usr/bin/env python3
"""build_captions.py: the word transcript becomes the karaoke caption track of a UI story composition.

  * dark theme (split and face, where the caption sits over the creator): white type at 84 px in the
    creator's display font at weight 600, a soft ink halo, and the SPOKEN word re-set in ink on a plate in
    the canvas colour, stepping word to word on the aligned word times
  * light theme (canvas, over the light grid): ink type with a soft light lift, the spoken word white on an
    ink plate. A group never mixes themes: a word on the far side of a layout cut starts a new group, and
    the groups on either side are clamped to end and start ON the cut frame
  * groups: at most 3 words and 18 characters, broken at sentence punctuation, commas and pauses of 0.35 s
    or more. Case as spoken, punctuation dropped (apostrophes curled; 90,000, 4.6 and dotted names kept).
    One line at y 1570; a group too wide for the 880 px column is set smaller (measured with the real
    font, never below 60 px), never wrapped
  * hard swaps between groups, joined end to start when the gap is under 0.28 s, so the caption never
    blinks off for a frame or two (caption_blink_check.py must report 0)

It needs karaoke.css linked after uistory.css and UIS.captions(tl) called before the timeline registers.
scaffold.py and template.html set up both.

Writes
  <hf>/index.html                  caption clips between <!-- CAPTIONS:BEGIN --> and <!-- CAPTIONS:END -->
  <hf>/lib/words.js                window.UIS_WORDS = [[word, start, end], ...]       (UIS.cue)
  <hf>/lib/capwords.js             window.UIS_CAPWORDS = [[plate_id, on, off], ...]   (UIS.captions)
  <hf>/lib/captions.json           the groups, for review
  <captions-dir>/transcript.json   {"captions": [{text, start, end, words}]}         (the speech gate reads it)

corrections.json ({"misheard": "correct"}) fixes words the speech model spells wrong, on screen only;
lib/words.js keeps the words as heard, so cue phrases are written the way the model heard them.

    python build_captions.py --words <base.words.json> --hf <hf dir> --layout <layout.resolved.json>
        [--captions-dir <dir>] [--corrections corrections.json] [--duration D] [--blocks blocks.json]
        [--light-modes canvas] [--tones '[{"from": 0, "to": 9, "theme": "light"}]']
"""
import argparse
import json
import math
import re
from pathlib import Path

from kit import brand, config

SENT_END = re.compile(r"[.!?,;:]['\"’”)]*$")
PUNCT = re.compile(r"[.,](?!\w)|[!?;:\"“”]")
SIZE, TRACK, GAP_EM, MAX_W = 84, -0.018, 0.24, 880


def load_words(path):
    data = json.loads(Path(path).read_text())
    if isinstance(data, dict) and "words" in data:
        data = data["words"]
    if isinstance(data, dict):
        data = data.get("captions") or data.get("phrases") or data.get("segments") or []
    out = []
    for item in data:
        if isinstance(item, dict) and isinstance(item.get("words"), list):
            for w in item["words"]:
                out.append((w.get("w", w.get("word")), float(w.get("s", w.get("start"))), float(w.get("e", w.get("end")))))
        else:
            out.append((item.get("w", item.get("word")), float(item.get("s", item.get("start"))),
                        float(item.get("e", item.get("end")))))
    return [(str(w).strip(), s, e) for w, s, e in out if str(w).strip()]


def load_corrections(path):
    """Whole-word, case-insensitive rules. A rule whose fix starts with the misheard text is guarded so a
    second pass does not apply it twice."""
    if not path or not Path(path).exists():
        return []
    rules = []
    for wrong, right in json.loads(Path(path).read_text()).items():
        pattern = rf"\b{re.escape(wrong)}\b"
        if right.lower().startswith(wrong.lower()) and len(right) > len(wrong):
            pattern += rf"(?!{re.escape(right[len(wrong):])})"
        rules.append((pattern, right))
    return rules


def phrase_fix(words, path):
    """Multi-word corrections ("mid journey" -> "Midjourney") applied to the word list itself: the same word
    count swaps word by word, a one-word fix merges the span into one timed word."""
    if not path or not Path(path).exists():
        return words
    norm = lambda t: re.sub(r"[^a-z0-9]", "", str(t).lower())
    for wrong, right in json.loads(Path(path).read_text()).items():
        ks, vs = wrong.split(), right.split()
        if len(ks) < 2:
            continue
        out, i = [], 0
        while i < len(words):
            seg = words[i : i + len(ks)]
            if len(seg) == len(ks) and all(norm(w[0]) == norm(x) for w, x in zip(seg, ks)):
                trail = re.search(r"[^\w]*$", seg[-1][0]).group(0)
                if len(vs) == len(ks):
                    out += [(vs[j] + (trail if j == len(ks) - 1 else ""), seg[j][1], seg[j][2]) for j in range(len(ks))]
                elif len(vs) == 1:
                    out.append((vs[0] + trail, seg[0][1], seg[-1][2]))
                else:
                    out += seg
                i += len(ks)
            else:
                out.append(words[i])
                i += 1
        words = out
    return words


def fix(text, rules):
    for pattern, right in rules:
        text = re.sub(pattern, right, text, flags=re.IGNORECASE)
    return text


def disp_word(word, rules):
    return PUNCT.sub("", fix(word, rules).replace("—", " ")).strip().replace("'", "’")


def group(words, rules, max_words, max_chars, pause, theme_of=None):
    groups, cur = [], []

    def shown(ws):
        return " ".join(x for x in (disp_word(w, rules) for w, _, _ in ws) if x)

    for w, s, e in words:
        if cur:
            gap = s - cur[-1][2]
            text = shown(cur + [(w, s, e)])
            if (gap >= pause or SENT_END.search(cur[-1][0]) or len(text.split()) > max_words or len(text) > max_chars
                    or (theme_of and theme_of((w, s, e)) != theme_of(cur[0]))):
                groups.append(cur)
                cur = []
        cur.append((w, s, e))
    if cur:
        groups.append(cur)
    return groups


def display_font_file() -> Path:
    """The file of the creator's display font, which the captions are set in."""
    return brand.font_file(config.load_profile().fonts.display)


_font = None


def _caption_font():
    """The display font at weight 600 (on its weight axis when it is a variable font)."""
    from PIL import ImageFont

    font = ImageFont.truetype(str(display_font_file()), 100)
    try:
        axes = font.get_variation_axes()
    except (OSError, AttributeError):
        return font
    values = []
    for axis in axes:
        name = axis.get("name", b"")
        name = name.decode("utf-8", "replace") if isinstance(name, bytes) else str(name)
        values.append(600 if name.lower() in ("weight", "wght") else axis.get("default", axis.get("minimum", 0)))
    try:
        font.set_variation_by_axes(values)
    except OSError:
        pass
    return font


def width(text, size):
    """The rendered width of `text` at `size` px, with the caption tracking and word gap."""
    global _font
    if _font is None:
        _font = _caption_font()
    tokens = text.split()
    w = sum(_font.getlength(t) * size / 100 + TRACK * size * len(t) for t in tokens)
    return w + GAP_EM * size * (len(tokens) - 1)


def main():
    ap = argparse.ArgumentParser(description="The karaoke caption track for a UI story composition.")
    ap.add_argument("--words", required=True)
    ap.add_argument("--hf", required=True)
    ap.add_argument("--layout", help="layout.resolved.json: the caption theme follows the layout windows")
    ap.add_argument("--light-modes", default="canvas", help="layout modes, comma separated, that take the light caption")
    ap.add_argument("--captions-dir")
    ap.add_argument("--corrections")
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--max-words", type=int, default=3)
    ap.add_argument("--max-chars", type=int, default=18)
    ap.add_argument("--pause", type=float, default=0.35)
    ap.add_argument("--clear", type=float, default=0.10)
    ap.add_argument("--clear-gap", type=float, default=0.28)
    ap.add_argument("--duration", type=float)
    ap.add_argument("--blocks", help="blocks.json: pauses are judged in KEPT time, so a pause the gap trims shrink "
                    "under --clear-gap joins the groups instead of blanking for a few frames")
    ap.add_argument("--tones", default="", help='JSON [{"from", "to", "theme": "dark|light"}], overrides the layout rule')
    a = ap.parse_args()

    hf = Path(a.hf)
    rules = load_corrections(a.corrections)
    raw_words = load_words(a.words)  # as heard: lib/words.js keeps these for UIS.cue and check_cues.py
    words = phrase_fix(raw_words, a.corrections)
    fps = a.fps
    fr = 1.0 / fps
    snap = lambda t: math.floor(t * fps) / fps
    light_modes = {m.strip() for m in a.light_modes.split(",") if m.strip()}
    keep = None
    if a.blocks:
        kept_blocks = json.loads(Path(a.blocks).read_text())
        kept_blocks = kept_blocks.get("A", kept_blocks) if isinstance(kept_blocks, dict) else kept_blocks
        keep = [(float(x), 1e9 if y == "end" else float(y)) for x, y in kept_blocks]

    def kept(t0, t1):
        """Seconds of [t0, t1] that survive the gap trims (plain t1 - t0 without --blocks)."""
        if keep is None:
            return t1 - t0
        return sum(max(0.0, min(t1, e) - max(t0, s)) for s, e in keep)

    wins = json.loads(Path(a.layout).read_text())["windows"] if a.layout else []
    tones = json.loads(a.tones) if a.tones else []

    def theme_at(t):
        for r in tones:
            if r["from"] <= t < r["to"]:
                return r["theme"]
        for w in wins:
            if w["t0"] <= t < w["t1"]:
                return "light" if w["mode"] in light_modes else "dark"
        return "dark"

    theme_of = (lambda w: theme_at((w[1] + w[2]) / 2)) if (wins or tones) else None
    groups = group(words, rules, a.max_words, a.max_chars, a.pause, theme_of)
    # layout cuts where the theme flips, as frame numbers (the compositor switches a window at round(t0 * fps))
    flips = sorted({round(w["t0"] * fps) for w in wins if w["t0"] > 0 and theme_at(w["t0"] - 0.5 * fr) != theme_at(w["t0"])})
    caps = []
    for i, g in enumerate(groups):
        start = max(0.0, snap(g[0][1] - 0.5 * fr))
        nxt = groups[i + 1][0][1] if i + 1 < len(groups) else None
        if nxt is not None and kept(g[-1][2], nxt) < a.clear_gap:
            end = max(0.0, snap(nxt - 0.5 * fr))
        else:
            end = g[-1][2] + a.clear
            if nxt is not None:
                end = min(end, snap(nxt - 0.5 * fr))
        if a.duration:
            end = min(end, a.duration)
        toks = [(disp_word(w, rules), s, e) for w, s, e in g]
        toks = [t for t in toks if t[0]]
        caps.append({"text": " ".join(t[0] for t in toks), "start": round(start, 4), "end": round(end, 4),
                     "tokens": toks, "theme": theme_of(g[0]) if theme_of else "dark",
                     "words": [{"w": w, "s": round(s, 3), "e": round(e, 3)} for w, s, e in g]})
    n_clamped = 0
    for bf in flips:
        b = round(bf / fps, 4)
        old_t, new_t = theme_at(b - 0.5 * fr), theme_at(b)
        before = [k for k, c in enumerate(caps) if c["start"] < b - 1e-6 and c["theme"] == old_t]
        i = before[-1] if before else None
        j = (i + 1) if i is not None else 0
        if i is not None and caps[i]["end"] > b:
            caps[i]["end"] = b
            n_clamped += 1
        if j < len(caps) and caps[j]["theme"] == new_t and kept(b, caps[j]["start"]) < a.clear_gap:
            if caps[j]["start"] != b:
                caps[j]["start"] = b
                n_clamped += 1
            if i is not None and kept(caps[i]["end"], b) < a.clear_gap:
                caps[i]["end"] = b
    lines, capwords, n_light, n_small = [], [], 0, 0
    esc = lambda x: x.replace("&", "&amp;").replace("<", "&lt;")
    for i, c in enumerate(caps):
        # Frame-exact boundaries (measured on the pinned HyperFrames 0.8.68): a clip shows while
        # data-start <= t < data-start + data-duration, and a GSAP tl.set at T applies from the first frame
        # with time >= T. So clip edges and plate switches are both written a quarter frame EARLY: a group
        # shows from frame fs up to frame fe - 1, its plates switch on the same frames, and two groups that
        # touch swap on one frame with no frame between them.
        fs, fe = round(c["start"] * fps), max(round(c["start"] * fps) + 1, round(c["end"] * fps))
        q = lambda f: max(0.0, (f - 0.25) / fps) if f > 0 else 0.0
        c_start = q(fs)
        dur = q(fe) - c_start
        size = SIZE
        while size > 60 and width(c["text"], size) > MAX_W:
            size -= 2
        n_small += size < SIZE
        spans = []
        toks = c["tokens"]
        for k, (t, s, _end) in enumerate(toks):
            pid = f"cap{i:03d}w{k}"
            on = c["start"] if k == 0 else max(c["start"], snap(s - 0.5 * fr))
            off = c["end"] if k + 1 == len(toks) else max(on + fr, snap(toks[k + 1][1] - 0.5 * fr))
            off = min(off, c["end"])
            capwords.append([pid, round(q(round(on * fps)), 4), round(q(round(off * fps)), 4)])
            spans.append(f'<span class="w">{esc(t)}<span class="pl" id="{pid}">{esc(t)}</span></span>')
        cls = "clip cap k" + (" light" if c["theme"] == "light" else "")
        n_light += c["theme"] == "light"
        style = f' style="font-size:{size}px"' if size != SIZE else ""
        lines.append(f'<div id="cap{i:03d}" class="{cls}" data-start="{c_start:.4f}" data-duration="{dur:.4f}" '
                     f'data-track-index="90"><span class="line"{style}>{"".join(spans)}</span></div>')

    index = hf / "index.html"
    page = index.read_text()
    b, e = "<!-- CAPTIONS:BEGIN -->", "<!-- CAPTIONS:END -->"
    if b not in page or e not in page:
        raise SystemExit("index.html needs the <!-- CAPTIONS:BEGIN --> and <!-- CAPTIONS:END --> markers (see template.html)")
    page = page.split(b)[0] + b + "\n" + "\n".join(lines) + "\n" + e + page.split(e)[1]
    for need in ("karaoke.css", "lib/capwords.js", "UIS.captions(tl)"):
        if need not in page:
            print(f"WARNING: index.html has no {need!r}; template.html shows where it goes")
    index.write_text(page)

    lib = hf / "lib"
    lib.mkdir(exist_ok=True)
    (lib / "words.js").write_text("window.UIS_WORDS=" + json.dumps([[w, round(s, 3), round(e, 3)] for w, s, e in raw_words]) + ";\n")
    (lib / "capwords.js").write_text("window.UIS_CAPWORDS=" + json.dumps(capwords) + ";\n")
    (lib / "captions.json").write_text(json.dumps([{k: v for k, v in c.items() if k != "tokens"} for c in caps], indent=1))
    if a.captions_dir:
        folder = Path(a.captions_dir)
        folder.mkdir(parents=True, exist_ok=True)
        transcript = [{"text": fix(" ".join(w["w"] for w in c["words"]), rules), "start": c["start"], "end": c["end"],
                       "words": c["words"]} for c in caps]
        (folder / "transcript.json").write_text(json.dumps({"captions": transcript}, indent=1))
    n = len(caps)
    longest = max(caps, key=lambda c: len(c["text"]))["text"] if caps else ""
    print(f"captions: {n} groups ({n_light} light, {n_small} set smaller to fit), "
          f"{sum(len(c['tokens']) for c in caps) / max(1, n):.2f} words per group, longest '{longest}'; "
          f"{len(words)} words; {len(flips)} theme cuts, {n_clamped} groups clamped to a cut")


if __name__ == "__main__":
    main()
