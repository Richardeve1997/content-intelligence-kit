/* Mini me stage: the big character style. window.Stage. One composition, one paused GSAP timeline.
 *
 * Load order in index.html (install.py writes it): vendor/gsap.min.js, character/manifest.js,
 * character/character.js, lib/data.js, stage.js, lib/scenes.js, then `await Stage.boot("#mm")` and the timeline
 * registered as window.__timelines.main. No closing script tag may appear in this file: HyperFrames inlines it.
 *
 * window.MM_DATA (lib/data.js) holds the voice-over's words, the pages from beats.json, the creator's name and
 * handle for the closing card, the lip-sync envelope and the measured outline of every pose. Every page becomes
 * one scene: a flat field (dark or light, flipping every page), the page's headline, the character in the page's
 * pose and the prop that arrives because of the pose. lib/scenes.js, written for each Reel, adds the rest with
 *
 *   Stage.page(n, { lines, char, trans, prop, build })     n is the page number, from 1, as beats.py and check.py print it
 *
 *     lines  ["WEEK", "*THREE*"] or [{ t: "WEEK", size: 150 }]: the headline lines (default: fitted from text)
 *     char   { pose, scale, x, crown, flip }: overrides for the character. A page shows it down to its waist (measured
 *            from the idle drawing) at the largest scale from 8 down that keeps the crown at y 960 or lower; the close
 *            puts the mouth at y 1660 at scale 10 to 12, with the head tucked behind the profile card
 *     trans  "down" | "up" | "cut" | "flash": how the scene arrives (default: pushes taking turns)
 *     prop   a prop string, as in beats.json, replacing the page's own
 *     build  function (c) { ... }: receipts, stamps and objects for this page (see c below)
 *
 *   c = { n, page, t0, t1, dur, field, keyAt, side, char, charEl, zone, tl, T, A, back, front, win, hit }
 *     t0, t1  the page's start and end (seconds)      keyAt  when the pose lands (a point reaches its key)
 *     side    "left" | "right" | "up" | null: where the pose points, so a card can arrive from there
 *     char    { scale, x, y, crown, left, right }: the character on screen (crown = top of the head); charEl its layer
 *     zone    { top, bottom, left, right }: the free space between the headline and the head
 *     held    { left, top, w, h }: the clear face of the card the character holds up (the hold_card pose, measured when
 *             the character was packed), or null
 *     back    layer behind the character (cards tuck behind the head); front: in front (stamps, bursts)
 *     T(cue)  seconds of a spoken word: a word index, "phrase" or "phrase#2"; T.e(cue) is its end
 *     A       motion helpers (all fromTo with both ends given): pop rise slide drop slam punch shake out count burst;
 *             count leaves a number under 20 whole and counts a bigger one up from 10, unless given a start
 *     win(el, a, b)  show an element only from a to b      hit(kind, t)  a whoosh or thud at t (written to hits.json)
 *
 *   Helpers that build and animate in one call: Stage.card(c, o), Stage.stamp(c, text, o), Stage.bubble(c, text, o),
 *   Stage.chip(c, html, o), Stage.heldCard(c, { text } or { img }) (the words or picture on the card the character
 *   holds up; a card prop on a hold_card page does this by itself). Stage.el / Stage.h / Stage.box make elements.
 *
 * Safe to seek: what is visible is a pure function of time, set by one director tween, and every motion is a
 * fromTo with both ends given (a value set only in the from-vars never applies in a sequential render).
 */
(function () {
  "use strict";
  var W = 1080, H = 1920, SAFE_L = 92, SAFE_R = 988, RING = 12, EDGE = 3, OUT = RING + EDGE;
  var HEAD_TOP = 190, HEAD_BOTTOM = 620, FIT_W = 884, MAX_SIZE = 240, MIN_SIZE = 84, LEAD_KEY = 0.15, COUNT_FROM = 10, COUNT_MIN = 20;
  // a page shows the character down to its waist, at the largest scale that keeps the crown at y 960 or lower
  var PAGE_SCALES = [8, 7, 6, 5, 4], CROWN_MIN = 960, CROWN_MAX = 1320;
  // the close: scale 10 to 12 with the mouth's bottom edge at y 1660, clear of the phone's caption area
  var CLOSE_SCALES = [11, 12, 10], CLOSE_LOWER = [9, 8], MOUTH_Y = 1660, CARD_H = 300, HELD_MIN_SIZE = 26;
  var D = null, WORDS = [], DUR = 0, tl = null, root = null, TOK = {};
  var OVERRIDES = {}, SC = [], WINS = [], HITS = [], PROBLEMS = [];
  var r3 = function (x) { return Math.round(x * 1000) / 1000; };
  var clamp = function (v, a, b) { return Math.max(a, Math.min(b, v)); };
  var norm = function (s) { return String(s).toLowerCase().replace(/[’']/g, "").replace(/[^a-z0-9$%#+]/g, ""); };

  // ---- spoken-word cues ----------------------------------------------------------------------------------------------
  function idx(cue) {
    if (typeof cue === "number") return cue;
    var m = /^(.*?)(?:#(\d+))?$/.exec(String(cue)), toks = m[1].split(/\s+/).map(norm).filter(Boolean), nth = +(m[2] || 1), seen = 0;
    for (var i = 0; i <= WORDS.length - toks.length; i++) {
      var ok = true;
      for (var k = 0; k < toks.length; k++) if (norm(WORDS[i + k].w) !== toks[k]) { ok = false; break; }
      if (ok && ++seen === nth) return i;
    }
    throw new Error("Stage: the words never say \"" + cue + "\". Copy the cue from words.json.");
  }
  function T(cue, off) { return r3(WORDS[idx(cue)].s + (off || 0)); }
  T.e = function (cue, off) { return r3(WORDS[idx(cue)].e + (off || 0)); };
  /** the first word at or after t whose bare text is one of `names`, or null */
  function wordAfter(t, names) {
    for (var i = 0; i < WORDS.length; i++) if (WORDS[i].s >= t - 0.05 && names.indexOf(norm(WORDS[i].w)) >= 0) return WORDS[i];
    return null;
  }

  // ---- DOM helpers ---------------------------------------------------------------------------------------------------
  function el(tag, cls, parent, css) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (css) n.style.cssText = css;
    if (parent) parent.appendChild(n);
    return n;
  }
  function h(parent, html, css) {
    var t = document.createElement("div");
    t.innerHTML = String(html).trim();
    var n = t.firstChild;
    if (css) n.style.cssText += ";" + css;
    if (parent) parent.appendChild(n);
    return n;
  }
  function box(x, y, w, hh) {
    return "left:" + x + "px;top:" + y + "px;" + (w != null ? "width:" + w + "px;" : "") + (hh != null ? "height:" + hh + "px;" : "");
  }
  function esc(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"); }
  function token(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  var SPARK = "M12 0C12.8 7.2 16.8 11.2 24 12C16.8 12.8 12.8 16.8 12 24C11.2 16.8 7.2 12.8 0 12C7.2 11.2 11.2 7.2 12 0Z";
  function spark(size, colour) {
    return '<svg viewBox="0 0 24 24" width="' + size + '" height="' + size + '" style="display:block"><path d="' + SPARK + '" fill="' + colour + '"/></svg>';
  }

  // ---- visibility: one director tween shows what belongs to each time --------------------------------------------------
  function show(n, on) {
    if (n._on === on) return;
    n._on = on;
    n.style.display = on ? (n._disp || "block") : "none";
  }
  function director(t) {
    for (var i = 0; i < SC.length; i++) show(SC[i].el, t >= SC[i].vis0 - 1e-6 && t < SC[i].vis1 - 1e-6);
    for (var w = 0; w < WINS.length; w++) show(WINS[w][0], t >= WINS[w][1] - 1e-6 && t < WINS[w][2] - 1e-6);
  }
  /** show an element only from a to b (b null: to the end) */
  function win(n, a, b, disp) {
    n._disp = disp || n._disp || "block";
    n.style.display = "none";
    n._on = false;
    WINS.push([n, a, b == null ? 1e9 : b]);
    return n;
  }
  function hit(kind, t, gain) {
    if (kind !== "whoosh" && kind !== "thud") throw new Error("Stage.hit: use whoosh or thud, not " + kind);
    if (t >= 0 && t < DUR) HITS.push({ kind: kind, t: r3(t), gain: gain != null ? gain : (kind === "whoosh" ? 0.36 : 0.5) });
  }

  // ---- motion helpers (always fromTo with both ends) --------------------------------------------------------------------
  var A = {
    pop: function (n, t, o) { o = o || {}; tl.fromTo(n, { scale: o.from != null ? o.from : 0.2, opacity: 0, rotation: o.rot || 0 }, { scale: 1, opacity: 1, rotation: o.rot || 0, duration: o.d || 0.22, ease: o.ease || "back.out(2.4)" }, t); },
    rise: function (n, t, o) { o = o || {}; tl.fromTo(n, { y: o.y != null ? o.y : 90, opacity: 0, rotation: o.rot || 0 }, { y: 0, opacity: 1, rotation: o.rot || 0, duration: o.d || 0.26, ease: o.ease || "power3.out" }, t); },
    /** in from a side: dir -1 from the left, 1 from the right */
    slide: function (n, t, dir, o) { o = o || {}; tl.fromTo(n, { x: (dir || 1) * (o.dist || 900), rotation: o.rot || 0 }, { x: 0, rotation: o.rot || 0, duration: o.d || 0.28, ease: o.ease || "power3.out" }, t); },
    drop: function (n, t, o) { o = o || {}; tl.fromTo(n, { y: -(o.dist || 700), rotation: o.rot || 0 }, { y: 0, rotation: o.rot || 0, duration: o.d || 0.3, ease: o.ease || "back.out(1.4)" }, t); },
    /** a stamp: falls in big, lands hard on t, two-frame overshoot */
    slam: function (n, t, o) {
      o = o || {};
      var r = o.rot || 0;
      tl.fromTo(n, { scale: o.from || 2.4, opacity: 0, rotation: r + (o.spin || 0) }, { scale: 1, opacity: 1, rotation: r, duration: 0.12, ease: "power3.in" }, t - 0.12);
      tl.fromTo(n, { scale: 1 }, { scale: 0.93, duration: 0.034, ease: "none", immediateRender: false }, t);
      tl.fromTo(n, { scale: 0.93 }, { scale: 1, duration: 0.07, ease: "power2.out", immediateRender: false }, t + 0.034);
    },
    punch: function (n, t, o) {
      o = o || {};
      tl.fromTo(n, { scale: 1 }, { scale: o.to || 1.12, duration: 0.07, ease: "power2.out", immediateRender: false }, t);
      tl.fromTo(n, { scale: o.to || 1.12 }, { scale: 1, duration: 0.16, ease: "power2.out", immediateRender: false }, t + 0.07);
    },
    /** a short shake on y (never sideways, so nothing crosses the side margins) */
    shake: function (n, t, o) {
      o = o || {};
      var a = o.amp || 14, k = o.n || 5, d = o.d || 0.045;
      for (var i = 0; i < k; i++) {
        var from = i === 0 ? 0 : (i % 2 ? a : -a) * (1 - (i - 1) / k), to = i === k - 1 ? 0 : (i % 2 ? -a : a) * (1 - i / k);
        tl.fromTo(n, { y: from }, { y: to, duration: d, ease: "none", immediateRender: false }, t + i * d);
      }
    },
    out: function (n, t, o) { o = o || {}; tl.fromTo(n, { opacity: 1, scale: 1 }, { opacity: 0, scale: o.to != null ? o.to : 0.4, duration: o.d || 0.18, ease: "power2.in", immediateRender: false }, t); },
    /** count a number up to the one written in the element, landing exactly on it. Without `from`, a number under
     *  20 stays whole (counting to 3 would show 1 and 2 first, and 12 would show 10 and 11 under the spoken word)
     *  and a bigger one counts up from 10, so no single digit ever shows and a count has at least ten steps.
     *  Returns false when it left the number whole. */
    count: function (n, t, d, from) {
      var text = n.getAttribute("data-to") || n.textContent, m = /^(\D*)([\d,]*\.?\d+)(.*)$/.exec(text);
      if (!m) return false;
      var target = parseFloat(m[2].replace(/,/g, "")), decimals = (m[2].split(".")[1] || "").length, commas = m[2].indexOf(",") >= 0;
      if (from == null) {
        if (target < COUNT_MIN) return false;
        from = COUNT_FROM;
      }
      var fmt = function (v) {
        var s = v.toFixed(decimals);
        if (commas) { var p = s.split("."); p[0] = p[0].replace(/\B(?=(\d{3})+(?!\d))/g, ","); s = p.join("."); }
        return m[1] + s + m[3];
      };
      var p = { v: from };
      n.textContent = fmt(p.v);
      tl.to(p, { v: target, duration: d || 0.6, ease: "power2.out", onUpdate: function () { n.textContent = fmt(p.v); } }, t);
      return true;
    },
    /** sparkles thrown out from a point, in white and the accent */
    burst: function (layer, x, y, t, o) {
      o = o || {};
      var n = o.n || 8, R = o.r || 220, size = o.size || 52, cols = o.colors || ["#ffffff", TOK.accent, "#ffffff"];
      for (var i = 0; i < n; i++) {
        var ang = (i / n) * Math.PI * 2 + (o.a0 || 0.35), rr = R * (0.72 + 0.28 * ((i * 37) % 10) / 10), sz = Math.round(size * (0.6 + 0.5 * ((i * 53) % 10) / 10));
        var g = h(layer, '<div style="position:absolute;' + box(x - sz / 2, y - sz / 2, sz, sz) + '">' + spark(sz, cols[i % cols.length]) + "</div>");
        tl.fromTo(g, { x: 0, y: 0, scale: 0.2, opacity: 1, rotation: 0 }, { x: Math.cos(ang) * rr, y: Math.sin(ang) * rr, scale: 1, opacity: 1, rotation: 40, duration: 0.3, ease: "power3.out" }, t);
        tl.fromTo(g, { opacity: 1, scale: 1 }, { opacity: 0, scale: 0.3, duration: 0.3, ease: "power1.in", immediateRender: false }, t + (o.hold || 0.42));
        win(g, t, t + (o.hold || 0.42) + 0.3);
      }
    },
    /** vertical speed lines for a flip, kept inside the safe columns */
    speed: function (layer, t, d, dir, xs) {
      (xs || [150, 330, 540, 750, 930]).forEach(function (x, i) {
        var hh = 420 + ((i * 131) % 5) * 90, s = el("div", "mm-streak", layer, "left:" + x + "px;height:" + hh + "px;");
        var y0 = dir > 0 ? -hh - ((i * 97) % 4) * 160 : H + ((i * 97) % 4) * 160, y1 = dir > 0 ? H + 200 : -hh - 200;
        tl.fromTo(s, { y: y0 }, { y: y1, duration: d * (0.8 + 0.25 * ((i * 7) % 3)), ease: "none" }, t + ((i * 5) % 3) * 0.02);
        win(s, t, t + d + 0.16);
      });
    }
  };

  // ---- headlines -------------------------------------------------------------------------------------------------------
  var PROBE = null, WIDTHS = {};
  /** words with the accent marked: *one* word, or a *run of words* between two stars */
  function tokens(text) {
    var out = [], inside = false;
    String(text).trim().split(/\s+/).filter(Boolean).forEach(function (w) {
      var opens = w.charAt(0) === "*", closes = w.length > 1 && w.charAt(w.length - 1) === "*";
      var bare = w.replace(/^\*|\*$/g, "");
      if (opens) inside = true;
      if (bare) out.push({ w: bare, acc: inside });
      if (closes) inside = false;
    });
    return out;
  }
  function lineHtml(words) {
    var html = [], run = [];
    function word(t) {
      var text = esc(t.w);
      return /^\$?\d[\d,]*\.?\d*%?$/.test(t.w) ? '<span class="mm-num" data-to="' + text + '">' + text + "</span>" : text;
    }
    function flush() { if (run.length) { html.push('<span class="mm-acc">' + run.join(" ") + "</span>"); run = []; } }
    words.forEach(function (t) { if (t.acc) run.push(word(t)); else { flush(); html.push(word(t)); } });
    flush();
    return html.join(" ");
  }
  function widthAt200(html) {
    if (WIDTHS[html] != null) return WIDTHS[html];
    var ln = el("span", "mm-ln", PROBE, "font-size:200px;position:absolute;left:0;top:0;width:auto");
    var inner = el("i", "", ln);
    inner.innerHTML = html;
    WIDTHS[html] = inner.getBoundingClientRect().width;
    PROBE.removeChild(ln);
    return WIDTHS[html];
  }
  /** every way to break n words into 1 to 3 lines, in order */
  function splits(words) {
    var out = [[words]];
    for (var a = 1; a < words.length; a++) {
      out.push([words.slice(0, a), words.slice(a)]);
      for (var b = a + 1; b < words.length; b++) out.push([words.slice(0, a), words.slice(a, b), words.slice(b)]);
    }
    return out;
  }
  /** the line layout with the biggest smallest line that fits x 98 to 982 and y 190 to 620 */
  function layoutLines(text) {
    var words = tokens(text), best = null;
    splits(words).forEach(function (lines) {
      var sizes = lines.map(function (ws) {
        var w = widthAt200(lineHtml(ws));
        return Math.min(MAX_SIZE, Math.floor(200 * FIT_W / Math.max(w, 1)));
      });
      var height = sizes.reduce(function (a, s) { return a + s * 0.88; }, 0);
      var shrink = height > HEAD_BOTTOM - HEAD_TOP ? (HEAD_BOTTOM - HEAD_TOP) / height : 1;
      sizes = sizes.map(function (s) { return Math.floor(s * shrink); });
      var low = Math.min.apply(null, sizes), score = low - 6 * (lines.length - 1);
      if (!best || score > best.score) best = { score: score, lines: lines.map(function (ws, i) { return { html: lineHtml(ws), size: sizes[i] }; }) };
    });
    return best.lines;
  }
  function authoredLines(lines) {
    return lines.map(function (L) {
      if (typeof L === "string") L = { t: L };
      var html = lineHtml(tokens(L.t));
      var size = L.size || Math.min(MAX_SIZE, Math.floor(200 * FIT_W / Math.max(widthAt200(html), 1)));
      return { html: html, size: size };
    });
  }

  // ---- the character -----------------------------------------------------------------------------------------------------
  function entryFor(pose) {
    var M = window.CHARACTER_MANIFEST;
    if (M.moves[pose] && M.moves[pose].kind !== "pose") return { name: pose, key: pose, e: M.moves[pose] };
    if (M.poses[pose]) return { name: "pose:" + pose, key: "pose:" + pose, e: M.poses[pose] };
    throw new Error("Stage: the character has no pose called " + pose + ". Use one of: " + Object.keys(M.poses).join(", "));
  }
  /** where x can go for this outline at this scale and crown, so the outline stays inside x 90 to 990 */
  function across(key, sc, crown, flip) {
    var X = D.extents[key], F = D.figure, last = Math.min(X.rows.length - 1, F.top + Math.floor((H - crown) / sc));
    var lo = Infinity, hi = -Infinity;
    for (var r = 0; r <= last; r++) {
      var row = X.rows[r];
      if (!row) continue;
      var a = flip ? -row[1] : row[0], b = flip ? -row[0] : row[1];
      lo = Math.min(lo, a); hi = Math.max(hi, b);
    }
    if (lo === Infinity) { lo = -10; hi = 10; }
    return { lo: lo, hi: hi, xmin: SAFE_L + OUT - lo * sc, xmax: SAFE_R - OUT - hi * sc };
  }
  function place(key, sc, crown, o) {
    var F = D.figure, room = across(key, sc, crown, o.flip);
    if (room.xmin > room.xmax) return null;
    var x = clamp(o.x != null ? o.x : W / 2, room.xmin, room.xmax);
    if (o.x != null && Math.abs(x - o.x) > 0.5) PROBLEMS.push("note: the character was moved from x " + o.x + " to x " + Math.round(x) + " to stay inside the safe lines.");
    return { scale: sc, x: Math.round(x), y: Math.round(crown + (D.baseline - F.top) * sc), crown: Math.round(crown),
      left: Math.round(x + room.lo * sc - OUT), right: Math.round(x + room.hi * sc + OUT),
      rows: [F.top, Math.min(F.feet, F.top + Math.floor((H - crown) / sc))] };
  }
  /** a page: the largest scale that shows the character down to its waist with its crown at y 960 or lower */
  function fitPage(key, o, n) {
    var F = D.figure, scales = o.scale ? [o.scale] : PAGE_SCALES;
    for (var k = 0; k < scales.length; k++) {
      var sc = scales[k], crown = o.crown != null ? o.crown : H - (F.waist - F.top) * sc;
      if (o.crown == null && crown < CROWN_MIN) {
        if (!o.scale) continue;  // the head would sit too high to bring the waist into the frame: a smaller scale
        crown = CROWN_MIN;
      }
      var where = place(key, sc, Math.min(crown, CROWN_MAX), o);
      if (where) return where;
    }
    var last = scales[scales.length - 1], fallback = Math.max(CROWN_MIN, Math.min(CROWN_MAX, H - (F.waist - F.top) * last));
    var room = across(key, last, fallback, o.flip);
    PROBLEMS.push("page " + n + ": the pose " + key + " is wider than the space between x 90 and 990 even at scale " + last + ". Pick another pose for that page.");
    return { scale: last, x: Math.round((room.xmin + room.xmax) / 2), y: Math.round(fallback + (D.baseline - F.top) * last), crown: Math.round(fallback),
      left: 0, right: W, rows: [F.top, F.feet] };
  }
  /** the close: the mouth's bottom edge at y 1660 and the eyes below the card. { where, why } (where is null when none fits) */
  function fitClose(key, e, o, cardBottom, scales) {
    var F = D.figure, M = window.CHARACTER_MANIFEST, fi = (e.frame_info || [])[0] || {};
    var mouth = fi.mouth ? fi.mouth[1] + M.faces.mouth_open[1] : F.neck - 4;
    var eyes = fi.eyes ? fi.eyes[1] : F.top + Math.round(0.6 * (F.neck - F.top));
    var why = "";
    for (var k = 0; k < scales.length; k++) {
      var sc = scales[k], crown = o.crown != null ? o.crown : MOUTH_Y - (mouth - F.top) * sc;
      if (!o.scale && o.crown == null && crown + (eyes - F.top) * sc < cardBottom + 30) {
        why = "the character's eyes would sit behind the profile card";
        continue;
      }
      var where = place(key, sc, crown, o);
      if (!where) { why = "the character would cross the safe lines at the sides"; continue; }
      where.mouth = Math.round(crown + (mouth - F.top) * sc);
      where.eyes = Math.round(crown + (eyes - F.top) * sc);
      return { where: where, why: why };
    }
    return { where: null, why: why };
  }
  /** the close shows the head big: the page's pose when it fits at scale 10 to 12, else idle, else talking; then smaller */
  function shootClose(s, o) {
    var bottom = s.cardTop + CARD_H, wanted = o.pose || s.page.pose, tries = o.pose ? [o.pose] : [wanted, "idle", "talking"];
    var scales = o.scale ? [o.scale] : CLOSE_SCALES, first = null;
    for (var i = 0; i < tries.length; i++) {
      var found = entryFor(tries[i]), got = fitClose(found.key, found.e, o, bottom, scales);
      if (!first) first = got;
      if (got.where) {
        if (i > 0) PROBLEMS.push("note: page " + s.n + ": the pose " + wanted + " is too wide for the close at scale 10 to 12, so it uses " + tries[i] + ".");
        return { found: found, where: got.where };
      }
    }
    var f0 = entryFor(tries[0]), lower = fitClose(f0.key, f0.e, o, bottom, CLOSE_LOWER);
    var where = lower.where || fitPage(f0.key, o, s.n);
    PROBLEMS.push("note: page " + s.n + ": the close uses scale " + where.scale + ", because at scale 10 to 12 " +
      (first.why || "the character does not fit the frame") + ".");
    return { found: f0, where: where };
  }
  function shoot(s, o) {
    var found, where;
    if (s.kind === "cta") {
      var chosen = shootClose(s, o);
      found = chosen.found; where = chosen.where;
    } else {
      found = entryFor(o.pose || s.page.pose);
      where = fitPage(found.key, o, s.n);
    }
    var poseName = found.name.replace("pose:", "");
    var holder = el("div", "mm-ch", s.chl, "filter:url(#mm-stk-" + s.field + ")");
    var inst = Character.mount(holder, { scale: where.scale, x: where.x, y: where.y, flip: !!o.flip, seed: 7 + s.i * 3 });
    var e = found.e, items, keyAt = s.t0;
    if (e.kind === "oneshot") {
      var pre = 0;
      for (var i = 0; i < e.hold; i++) pre += e.sequence[i][1];
      var start = s.t0 + LEAD_KEY - pre / 24;
      items = [{ t: r3(start), move: found.name, hold: r3(s.t1 - s.t0 + 1.0) }];
      keyAt = r3(start + pre / 24);
    } else if (e.kind === "loop") {
      items = [{ t: r3(s.t0 - 0.06), move: found.name, dur: r3(s.t1 - s.t0 + 0.5) }];
    } else {
      items = [{ t: r3(s.t0 - 0.06), move: found.name }];
    }
    Character.script(tl, items, { character: inst, until: DUR });
    if (D.envelope && D.envelope.envelope && D.envelope.envelope.length) {
      Character.speak(tl, { envelope: D.envelope.envelope, fps: D.envelope.fps, t0: 0, until: DUR }, inst);
    }
    if (inst.driver && inst.driver.duration() > DUR) inst.driver.duration(DUR);  // the Reel ends at DUR
    var side = /left/.test(poseName) ? "left" : /right/.test(poseName) ? "right" : /up|hold_card|celebrate/.test(poseName) ? "up" : null;
    if (o.flip && side === "left") side = "right"; else if (o.flip && side === "right") side = "left";
    // the clear face of the card this drawing holds up, on screen: a canvas cell lands at x + (col - ref_col) * scale
    // from the head column and y + (row - baseline) * scale from the feet; a flip mirrors it about the head column
    var fi0 = (e.frame_info || [])[0] || {}, held = null;
    if (fi0.card) {
      var cx = fi0.card[0], cw = fi0.card[2], sc = where.scale;
      held = { left: Math.round(where.x + (o.flip ? e.ref_col - cx - cw : cx - e.ref_col) * sc), top: Math.round(where.y + (fi0.card[1] - D.baseline) * sc),
        w: cw * sc, h: fi0.card[3] * sc };
    }
    return { el: holder, inst: inst, pose: poseName, keyAt: keyAt, side: side, char: where, held: held };
  }

  // ---- props: what arrives because of the pose ---------------------------------------------------------------------------
  function content(prop) { var i = String(prop).indexOf(":"); return i < 0 ? "" : String(prop).slice(i + 1).trim(); }
  function card(c, o) {
    o = o || {};
    var text = o.text != null ? o.text : "", img = o.img;
    var w = o.w || 760, x = o.x != null ? o.x : Math.round(W / 2 - w / 2), y = o.y != null ? o.y : c.zone.top;
    var n;
    if (img) {
      var hh = o.h || Math.max(160, c.zone.bottom - y);
      n = h(o.layer || c.back, '<div class="mm-card img" style="' + box(x, y, w, hh) + '"><img src="' + esc(img) + '" alt=""></div>');
    } else {
      var size = o.size || (String(text).length <= 14 ? 76 : String(text).length <= 28 ? 64 : 56);
      n = h(o.layer || c.back, '<div class="mm-card" style="' + box(x, y) + "max-width:" + w + "px;font-size:" + size + 'px">' + esc(text) + "</div>");
    }
    if (o.x == null) {
      var measured = n.getBoundingClientRect().width;  // centred on the frame once its real width is known
      if (measured > 0) n.style.left = Math.round(W / 2 - measured / 2) + "px";
    }
    var t = o.t != null ? o.t : c.keyAt;
    var from = o.from || (c.side === "left" ? "left" : c.side === "right" ? "right" : "top");
    if (from === "left") A.slide(n, t, -1, { rot: o.rot || -3 });
    else if (from === "right") A.slide(n, t, 1, { rot: o.rot || 3 });
    else if (from === "pop") A.pop(n, t, { rot: o.rot || 0 });
    else if (from === "drop") A.drop(n, t, { rot: o.rot || 2, dist: y + 400 });
    else A.rise(n, t, { y: 140, rot: o.rot || 2, d: 0.3, ease: "back.out(1.6)" });  // up from below, never through the headline
    return n;
  }
  /** the page's card written on the card the character holds up (c.held): a few words fitted to its clear face at a
   *  readable size, or a picture. It sits in the character's layer, so it moves when the character flinches. Returns
   *  the element, or null when the words do not fit at 26 px, so the caller can drop a card in instead. */
  function heldCard(c, o) {
    o = o || {};
    var r = c.held;
    if (!r) return null;
    var pad = Math.round(Math.min(r.w, r.h) * 0.08), bw = r.w - 2 * pad, bh = r.h - 2 * pad, n;
    if (o.img) {
      n = h(o.layer || c.charEl, '<div class="mm-held img" style="' + box(r.left + pad, r.top + pad, bw, bh) + '"><img src="' + esc(o.img) + '" alt=""></div>');
    } else {
      n = h(o.layer || c.charEl, '<div class="mm-held" style="' + box(r.left + pad, r.top + pad, bw, bh) + '"><span></span></div>');
      var span = n.firstChild, size = Math.min(76, Math.floor(bh * 0.6));
      span.textContent = String(o.text != null ? o.text : "");
      for (; size >= HELD_MIN_SIZE; size -= 2) {
        span.style.fontSize = size + "px";
        if (span.scrollWidth <= bw + 1 && span.getBoundingClientRect().height <= bh + 1) break;
      }
      if (size < HELD_MIN_SIZE) { n.parentNode.removeChild(n); return null; }
    }
    A.pop(n, o.t != null ? o.t : c.keyAt + 0.05, { from: 0.7, d: 0.2, ease: "back.out(2)" });
    return n;
  }
  function stamp(c, text, o) {
    o = o || {};
    var n = h(o.layer || c.front, '<div class="mm-stamp">' + esc(text) + "</div>");
    var size = o.size || 104;
    n.style.fontSize = size + "px";
    var w = Math.min(820, n.getBoundingClientRect().width || size * 0.62 * String(text).length + 96);
    var left = c.side === "right" || (c.side == null && c.n % 2);
    var x = o.x != null ? o.x : (left ? 130 : 950 - w);
    var y = o.y != null ? o.y : Math.round((c.zone.top + c.zone.bottom) / 2 - size * 0.7);
    x = clamp(x, SAFE_L + 30, SAFE_R - 30 - w);
    n.style.left = x + "px"; n.style.top = y + "px";
    var t = o.t != null ? o.t : c.keyAt + 0.12;
    A.slam(n, t, { rot: o.rot != null ? o.rot : (left ? -7 : 6) });
    if (c.charEl && o.jolt !== false) A.shake(c.charEl, t + 0.02, { amp: 10, n: 4, d: 0.04 });  // the character flinches
    hit("thud", t, o.gain);
    return n;
  }
  function bubble(c, text, o) {
    o = o || {};
    var right = c.char.x < W / 2 + 1;
    var n = h(o.layer || c.front, '<div class="mm-bubble' + (right ? "" : " tail-right") + '">' + esc(text) + "</div>");
    var w = n.getBoundingClientRect().width || 150;
    var x = o.x != null ? o.x : (right ? Math.min(SAFE_R - 40 - w, c.char.x + 150) : Math.max(SAFE_L + 40, c.char.x - 150 - w));
    var y = o.y != null ? o.y : Math.max(c.zone.top, c.char.crown - 210);
    n.style.left = x + "px"; n.style.top = y + "px";
    A.pop(n, o.t != null ? o.t : c.t0 + 0.12, { from: 0.3 });
    return n;
  }
  function chip(c, html, o) {
    o = o || {};
    var n = h(o.layer || c.front, '<div class="mm-chip' + (o.accent ? " acc" : "") + '" style="' + box(o.x || 130, o.y || c.zone.top) + '">' + html + "</div>");
    A.pop(n, o.t != null ? o.t : c.keyAt, { from: 0.4, rot: o.rot || 0 });
    return n;
  }
  function headWord(s) {
    var acc = /\*([^*]+)\*/.exec(s.page.text);
    if (acc) return acc[1].trim();
    var words = String(s.page.text).replace(/\*/g, "").split(/\s+/);
    return words[words.length - 1];
  }
  function defaultProp(s, c) {
    var kind = String(s.prop).split(":")[0].trim().toLowerCase(), what = content(s.prop);
    if (kind === "stamp") stamp(c, what || headWord(s));
    else if (kind === "count") {
      // one number counts: the accented one, else the first, and only when it is 20 or more ("3 OF 5" and "12 YEARS" stay whole)
      var num = s.pageEl.querySelector(".mm-acc .mm-num") || s.pageEl.querySelector(".mm-num");
      if (!num) PROBLEMS.push("note: page " + s.n + " counts, but its headline has no number.");
      else if (s.i === 0) PROBLEMS.push("note: page 1 shows its number whole, so the first frame reads right.");
      else if (!A.count(num, s.t0 + 0.02, Math.min(0.7, s.dur * 0.6))) {
        PROBLEMS.push("note: page " + s.n + " counts " + num.textContent + ", which is under 20, so it shows whole instead.");
      }
    }
    else if (kind === "burst") A.burst(c.front, c.char.x, c.char.crown + 140, c.keyAt + 0.05, { r: 260 });
    else if (kind === "bubble") bubble(c, what || "?");
    else if (kind === "card" && what) {
      // on a hold_card page the words (or the picture) go on the card the character holds up
      var picture = /\.(png|jpe?g|webp|gif|svg)$/i.test(what);
      if (c.held && heldCard(c, picture ? { img: what } : { text: what })) return;
      if (c.held) {
        s.heldNote = true;
        PROBLEMS.push("note: page " + s.n + ": the words " + JSON.stringify(what) + " do not fit on the card the character holds at " + HELD_MIN_SIZE + " px, so the card drops in beside it instead. Cut them to a few words.");
      }
      if (picture) card(c, { img: what }); else card(c, { text: what });
    } else if (kind === "card") PROBLEMS.push("note: page " + s.n + " has a card with nothing on it. Name what it shows in beats.json.");
  }

  // ---- the closing call to action ------------------------------------------------------------------------------------------
  function closer(s, c) {
    var P = D.profile, cta = D.cta || { type: "follow" }, top = s.cardTop;
    var av = P.avatar ? '<img src="' + esc(P.avatar) + '" alt="">' : esc(P.initial || "");
    var lower = cta.type === "comment" && cta.keyword
      ? '<div class="box"><span class="typed"></span><span class="post">Post</span></div>'
      : '<div class="btn"><span class="a">Follow</span><span class="b">Following</span></div>';
    // in front of the character: the big head tucks behind the card, which stays readable
    var card0 = h(c.front, '<div class="mm-cta' + (P.handle ? "" : " no-handle") + '" style="top:' + top + 'px"><div class="av">' + av +
      '</div><div class="nm">' + esc(P.name) + '</div><div class="hd">' + esc(P.handle || "") + "</div>" + lower + "</div>");
    s.ctaEl = card0;
    A.rise(card0, s.t0 - 0.02, { y: -80, d: 0.22 });
    var press;
    if (cta.type === "comment" && cta.keyword) {
      var kw = String(cta.keyword), said = wordAfter(s.t0, [norm(kw)]) || wordAfter(s.t0, ["comment"]);
      var t0 = said ? said.s : s.t0 + 0.5, t1 = said ? Math.max(said.e, t0 + 0.3) : t0 + 0.5;
      var typed = card0.querySelector(".typed"), p = { v: 0 };
      typed.textContent = "";
      tl.to(p, { v: kw.length, duration: t1 - t0, ease: "none", onUpdate: function () { typed.textContent = kw.slice(0, Math.round(p.v)); } }, t0);
      press = t1 + 0.2;
      var post = card0.querySelector(".post");
      tl.fromTo(post, { scale: 1 }, { scale: 0.9, duration: 0.05, ease: "power1.in", immediateRender: false }, press - 0.05);
      tl.fromTo(post, { scale: 0.9 }, { scale: 1, duration: 0.16, ease: "back.out(3)", immediateRender: false }, press);
    } else {
      var follow = wordAfter(s.t0, ["follow", "following"]);
      press = follow ? follow.s + 0.14 : s.t0 + 0.6;
      var btn = card0.querySelector(".btn");
      win(btn.querySelector(".a"), -1, press, "inline");
      win(btn.querySelector(".b"), press, null, "inline");
      tl.fromTo(btn, { backgroundColor: TOK.accent, color: TOK["on-accent"] }, { backgroundColor: TOK.ink, color: "#ffffff", duration: 0.04, ease: "none", immediateRender: false }, press);
      tl.fromTo(btn, { scale: 1 }, { scale: 0.93, duration: 0.05, ease: "power1.in", immediateRender: false }, press - 0.05);
      tl.fromTo(btn, { scale: 0.93 }, { scale: 1, duration: 0.16, ease: "back.out(3)", immediateRender: false }, press);
    }
    // the cursor's tip (19, 9 px into its box) lands on the middle of the button it presses, measured without transforms
    var target = cta.type === "comment" && cta.keyword ? card0.querySelector(".post") : card0.querySelector(".btn");
    var holder = target.offsetParent === card0 ? null : target.offsetParent;
    var cx = card0.offsetLeft + (holder ? holder.offsetLeft : 0) + target.offsetLeft + target.offsetWidth / 2;
    var cy = card0.offsetTop + (holder ? holder.offsetTop : 0) + target.offsetTop + target.offsetHeight / 2;
    var cur = h(c.front, '<svg class="mm-cursor" viewBox="0 0 24 24" style="' + box(Math.round(cx - 19), Math.round(cy - 9)) + '"><path d="M5 2.5v17l4.6-4.3 2.9 6.6 3-1.3-2.9-6.5H19z" fill="#ffffff" stroke="' + esc(TOK.ink) + '" stroke-width="1.6" stroke-linejoin="round"/></svg>');
    tl.fromTo(cur, { x: 150, y: 300 }, { x: 0, y: 0, duration: 0.3, ease: "power3.out" }, press - 0.45);
    tl.fromTo(cur, { scale: 1 }, { scale: 0.82, duration: 0.05, ease: "none", immediateRender: false }, press - 0.05);
    tl.fromTo(cur, { scale: 0.82 }, { scale: 1, duration: 0.1, ease: "power1.out", immediateRender: false }, press);
    win(cur, press - 0.45, null);
    A.burst(c.front, Math.round(cx), Math.round(cy), press, { n: 9, r: 170, size: 44 });
    hit("thud", press, 0.4);
    // the punch-in: the character rises into the lower half under the card
    tl.fromTo(s.shot.el, { y: 560 }, { y: 0, duration: 0.3, ease: "power4.out" }, s.t0 - 0.04);
  }

  // ---- building ---------------------------------------------------------------------------------------------------------
  function buildScene(page, i) {
    // pages are numbered from 1 everywhere: Stage.page(1, ...), beats.py, check.py and every message
    var n = i + 1, o = OVERRIDES[n] || {}, next = D.pages[i + 1];
    var s = { i: i, n: n, page: page, t0: i === 0 ? 0 : r3(page.t), t1: next ? r3(next.t) : DUR, field: page.field === "light" ? "light" : "dark",
      prop: o.prop != null ? o.prop : page.prop, o: o };
    s.dur = r3(s.t1 - s.t0);
    s.kind = String(s.prop).split(":")[0].trim().toLowerCase();
    s.el = el("div", "mm-sc " + s.field, root);
    s.el.id = "mm-p" + n;
    s.sci = el("div", "mm-sci", s.el);
    s.dots = el("div", "mm-dots", s.sci);
    s.back = el("div", "mm-back", s.sci);
    s.chl = el("div", "mm-chl", s.sci);
    s.front = el("div", "mm-front", s.sci);
    s.hl = el("div", "mm-hl", s.sci);
    s.pageEl = el("div", "mm-pg", s.hl);
    var lines = o.lines ? authoredLines(o.lines) : layoutLines(page.text);
    var y = 0;
    s.lines = lines.map(function (L) {
      var ln = el("span", "mm-ln", s.pageEl, "font-size:" + L.size + "px");
      var inner = el("i", "", ln);
      inner.innerHTML = L.html;
      ln._size = L.size;
      y += L.size * 0.88;
      return ln;
    });
    s.headBottom = HEAD_TOP + Math.round(y);
    if (!String(page.text || "").trim() && !o.lines) PROBLEMS.push("page " + n + " has no headline.");
    SC.push(s);
    return s;
  }
  function animateScene(s) {
    var o = s.o;
    // the headline: readable on frame 0 for the first page, a quick drop-in for the rest
    s.lines.forEach(function (ln, k) {
      var t = s.t0 + k * 0.045;
      if (s.i === 0) tl.fromTo(ln, { y: 26 }, { y: 0, duration: 0.5, ease: "power3.out" }, 0);
      else tl.fromTo(ln, { scaleY: 1.3, opacity: 0, y: 34 }, { scaleY: 1, opacity: 1, y: 0, duration: 0.17, ease: "power3.out" }, t);
    });
    s.cardTop = Math.max(s.headBottom + 24, 440);  // the close's card, right under its headline
    s.shot = shoot(s, Object.assign({}, o.char || {}));
    var zone = { top: s.headBottom + 30, bottom: Math.max(s.headBottom + 200, s.shot.char.crown + 40), left: 120, right: 960 };
    var c = { n: s.n, page: s.page, t0: s.t0, t1: s.t1, dur: s.dur, field: s.field, keyAt: s.shot.keyAt, side: s.shot.side,
      char: s.shot.char, charEl: s.shot.el, held: s.shot.held, zone: zone, tl: tl, T: T, A: A, back: s.back, front: s.front,
      win: win, hit: hit, el: el, h: h, box: box };
    s.c = c;
    // a slow vertical drift on every layer, so no page ever sits dead still (sideways would cross the margins)
    var d0 = Math.max(0, s.t0 - 0.06), dd = Math.max(0.05, Math.min(s.dur + 0.2, DUR - d0));
    tl.fromTo(s.dots, { y: 0 }, { y: -14, duration: dd, ease: "none" }, d0);
    tl.fromTo(s.back, { y: 9 }, { y: -9, duration: dd, ease: "none" }, d0);
    tl.fromTo(s.front, { y: 6 }, { y: -6, duration: dd, ease: "none" }, d0);
    tl.fromTo(s.hl, { y: 4 }, { y: -6, duration: dd, ease: "none" }, d0);
    if (s.kind === "cta") closer(s, c);
    else if (!o.build) defaultProp(s, c);
    if (o.build) o.build(c);
  }
  function transitions() {
    var fx = el("div", "", root, "position:absolute;left:0;top:0;width:1080px;height:1920px;");
    var flash = el("div", "", root);
    flash.id = "mm-flash";
    SC.forEach(function (s, i) { s.trans = s.o.trans || (i === 0 ? "cut" : s.kind === "cta" ? "down" : (i % 2 ? "down" : "up")); });
    SC.forEach(function (s, i) {
      var prev = SC[i - 1], next = SC[i + 1], push = s.trans === "down" || s.trans === "up";
      s.vis0 = i === 0 ? -1 : s.t0 - (push ? 0.05 : 0);
      s.vis1 = next ? next.t0 + ((next.trans === "flash" || next.trans === "cut") ? 0 : 0.075) : DUR + 1;
      if (!prev) return;
      if (prev.field !== s.field) hit("whoosh", s.t0);
      if (push) {
        var dir = s.trans === "down" ? 1 : -1, t = s.t0 - 0.05;
        // the new scene slides in whole, field and all, over the old one, whose content slides out under it.
        // 16 px steps: the seam between two fields lands on a macroblock row, so it encodes clean
        tl.fromTo(s.el, { y: -H * dir }, { y: 0, duration: 0.125, ease: "power2.out", snap: { y: 16 } }, t);
        tl.fromTo(prev.sci, { y: 0 }, { y: H * dir, duration: 0.125, ease: "power2.out", snap: { y: 16 }, immediateRender: false }, t);
        var holder = el("div", s.field, fx, "position:absolute;left:0;top:0;width:1080px;height:1920px;");
        A.speed(holder, t - 0.01, 0.15, dir);
      } else if (s.trans === "flash") {
        win(flash, s.t0 - 0.017, s.t0);
      }
    });
  }
  function filters() {
    var NS = "http://www.w3.org/2000/svg", svg = document.createElementNS(NS, "svg");
    svg.setAttribute("width", "0"); svg.setAttribute("height", "0");
    svg.style.position = "absolute";
    var ring = '<feMorphology in="SourceAlpha" operator="dilate" radius="' + RING + '" result="d"/><feFlood flood-color="#ffffff" result="w"/>' +
      '<feComposite in="w" in2="d" operator="in" result="ring"/>';
    var head = 'filterUnits="userSpaceOnUse" x="0" y="0" width="1080" height="1920" color-interpolation-filters="sRGB"';
    svg.innerHTML = "<defs>" +
      '<filter id="mm-stk-dark" ' + head + ">" + ring + '<feMerge><feMergeNode in="ring"/><feMergeNode in="SourceGraphic"/></feMerge></filter>' +
      '<filter id="mm-stk-light" ' + head + '><feMorphology in="SourceAlpha" operator="dilate" radius="' + (RING + EDGE) + '" result="d2"/>' +
      '<feFlood flood-color="' + esc(TOK.ink) + '" result="k"/><feComposite in="k" in2="d2" operator="in" result="edge"/>' + ring +
      '<feMerge><feMergeNode in="edge"/><feMergeNode in="ring"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>';
    root.appendChild(svg);
  }

  async function boot(sel) {
    D = window.MM_DATA;
    WORDS = D.words || [];
    DUR = D.duration;
    root = typeof sel === "string" ? document.querySelector(sel) : sel;
    ["ink", "accent", "on-accent", "field-dark", "field-light", "on-field-dark", "canvas"].forEach(function (k) { TOK[k] = token("--" + k); });
    root.style.setProperty("--mm-head-weight", String(D.head && D.head.weight || 900));
    document.documentElement.style.setProperty("--mm-head-weight", String(D.head && D.head.weight || 900));
    await Character.ready();
    try {
      var weight = String(D.head && D.head.weight || 900);
      await Promise.all([document.fonts.load(weight + " 200px " + token("--font-display")), document.fonts.load("800 52px " + token("--font-display")),
        document.fonts.load("700 56px " + token("--font-body")), document.fonts.load("500 34px " + token("--font-body")),
        document.fonts.load("500 28px " + token("--font-mono"))]);
      await document.fonts.ready;
    } catch (e) { /* the sizes are measured below either way */ }
    tl = gsap.timeline({ paused: true });
    Stage.tl = tl;
    PROBE = el("div", "", root);
    PROBE.id = "mm-probe";
    filters();
    var scenes = D.pages.map(buildScene);
    // measure with every scene laid out, then hide them again
    scenes.forEach(function (s) { s.el.style.display = "block"; });
    scenes.forEach(animateScene);
    scenes.forEach(function (s) { s.el.style.display = "none"; s.el._on = false; });
    transitions();
    root.removeChild(PROBE);
    // every picture a scene uses is decoded before the first frame, so no frame shows a blank card
    await Promise.all(Array.prototype.map.call(root.querySelectorAll("img"), function (img) {
      return img.decode ? img.decode().catch(function () {}) : Promise.resolve();
    }));
    var proxy = { p: 0 };
    tl.to(proxy, { p: 1, duration: DUR, ease: "none", onUpdate: function () { director(this.time()); } }, 0);
    tl.timeScale(1);
    var late = tl.getChildren(true, true, true).filter(function (ch) { return ch.startTime() + ch.duration() > DUR + 0.25; });
    if (late.length) PROBLEMS.push("note: " + late.length + " animation(s) run past the end of the Reel and are cut off there.");
    director(0);
    Stage.hits = HITS.slice().sort(function (a, b) { return a.t - b.t; });
    Stage.ready = true;
    // no return value on purpose: a GSAP timeline is a thenable, and an async function that returns one never resolves
  }

  /** what the page check reads: problems, and where everything landed on each page */
  function audit() {
    var problems = PROBLEMS.slice(), pages = [];
    if (D.figure && D.figure.waist_measured === false) {
      problems.push("note: the character's waist could not be measured from its idle drawing (no hands hang at its sides), " +
        "so each page is cut halfway between its neck and its feet and the crop may be off: check the frames to see " +
        "that the shoulders and the waist are in view.");
    }
    SC.forEach(function (s) {
      var shown = s.el.style.display;
      s.el.style.display = "block";
      var lines = s.lines.map(function (ln) {
        var r = ln.firstChild.getBoundingClientRect();
        return { left: Math.round(r.left), right: Math.round(r.right), top: Math.round(r.top), bottom: Math.round(r.bottom), size: ln._size };
      });
      s.el.style.display = shown;
      var p = { n: s.n, t0: s.t0, t1: s.t1, field: s.field, pose: s.shot.pose, prop: s.prop, char: s.shot.char, held: s.shot.held || null,
        lines: lines, headBottom: s.headBottom };
      if (s.ctaEl) p.cta = { name: s.ctaEl.querySelector(".nm").textContent, handle: s.ctaEl.querySelector(".hd").textContent };
      pages.push(p);
      if (s.shot.pose === "hold_card" && !s.heldNote && !s.el.querySelector(".mm-held")) {
        problems.push("note: page " + p.n + ": the hold_card pose holds a blank card" + (s.shot.held
          ? ". Give the page a card prop with the words to write on it, or pick another pose."
          : ", and no card was measured on it when the character was packed, so nothing can be written on it. Pick another pose, or pack the character again."));
      }
      lines.forEach(function (L) {
        if (L.left < 90 || L.right > 990) problems.push("page " + p.n + ": the headline reaches x " + L.left + " to " + L.right + ", outside 90 to 990.");
      });
      if (s.headBottom > HEAD_BOTTOM + 12) problems.push("page " + p.n + ": the headline runs down to y " + s.headBottom + ", past 620.");
      if (lines.some(function (L) { return L.size < MIN_SIZE; })) {
        problems.push("note: page " + p.n + ": the headline is set small to fit. Cut it to the two words that carry the phrase.");
      }
      var sc = s.shot.char.scale;
      if (s.kind !== "cta" && (sc < 6 || sc > 8)) problems.push("note: page " + p.n + ": the character is at scale " + sc + " (6 to 8 is the look) to fit the pose.");
    });
    return { problems: problems, pages: pages, hits: Stage.hits || [] };
  }

  window.Stage = { page: function (n, o) { OVERRIDES[n] = Object.assign(OVERRIDES[n] || {}, o); }, boot: boot, audit: audit, T: T, A: A,
    el: el, h: h, box: box, win: win, hit: hit, card: card, heldCard: heldCard, stamp: stamp, bubble: bubble, chip: chip, idx: idx,
    hits: [], ready: false, tl: null };
})();
