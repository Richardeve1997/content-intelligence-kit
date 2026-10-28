/* Character: the creator's pixel character for HyperFrames compositions. window.Character.
 *
 * Load order in the host index.html head: GSAP, character/manifest.js (written by `python -m kit.character pack`),
 * then this file. No closing script tag may ever appear in this file, because the HyperFrames compiler inlines it
 * into the page.
 *
 * Safe to seek by construction. Nothing here reads the time, draws random numbers, loads data over the network or
 * waits for an animation frame. script() and speak() only RECORD a plan; one paused GSAP "driver" tween per mount
 * calls render(t) whenever the timeline is rendered, and render(t) is a pure function of t: which move is playing,
 * which drawing of it, where the character stands, which mouth shape shows and whether a blink is on. Seeking to any
 * time gives the same picture.
 *
 * Built in: idle is a still drawing with blinks only (no breathing, bobbing or lift-and-settle). Scales are whole
 * numbers only; the strips are scaled by CSS with image-rendering: pixelated, never by a fraction. The mouth moves
 * only on drawings that carry a mouth anchor, and a blink never lands on an open mouth.
 *
 *   Character.configure({ base: "character/" })              // where strips/ and faces/ live (default shown)
 *   await Character.ready()                                    // decode every strip and patch before building timelines
 *   const ch = Character.mount(parentEl, { scale: 3, x: 540, y: 1900, flip: false })
 *   Character.script(tl, [{ t: 0, move: "talking" }, { t: 1.2, move: "point_left", hold: 0.8 }, ...], { character: ch, until })
 *   Character.speak(tl, { envelope, fps: 24, t0: 0.4 }, ch)   // or { words, t0 }: closed / mid / open with hysteresis
 *   Character.at(words, "launch", { nth: 0, edge: "start", offset: -0.1 })   // seconds, so a move can land on a word
 *
 * x is where the head-centre column sits on screen, y is where the feet stand. Both are screen px in the host frame.
 * A move name is a move from the manifest ("talking", "point_up"), or any core pose ("shrug" or "pose:shrug"), which
 * holds that drawing until the next item.
 */
(function () {
  "use strict";

  var TICK = 24; // the authored drawing rate; the plan is evaluated per tick, render() runs at any frame rate
  var CFG = { base: "character/" };
  var BLINK_TICKS = 3;
  var THRESH = { midUp: 0.15, openUp: 0.62, openDown: 0.45, midDown: 0.08, minHold: 2 };
  var ALIAS = { talk: "talking", jump: "celebrate", celebrate_jump: "celebrate", typing_laptop: "typing", turn_back: "turn" };

  function manifest() {
    var M = window.CHARACTER_MANIFEST;
    if (!M) throw new Error("Character: load character/manifest.js before character.js");
    return M;
  }
  function configure(o) { Object.assign(CFG, o || {}); }
  /** The drawing set a name refers to: { key, e }. key is the move name, or "pose:<name>" for a held pose. */
  function resolve(name) {
    var M = manifest();
    if (name.indexOf("pose:") === 0) {
      var p = M.poses[ALIAS[name.slice(5)] || name.slice(5)];
      if (p) return { key: "pose:" + (ALIAS[name.slice(5)] || name.slice(5)), e: p };
    } else {
      var real = ALIAS[name] || name;
      if (M.moves[real]) return { key: real, e: M.moves[real] };
      if (M.poses[real]) return { key: "pose:" + real, e: M.poses[real] };
    }
    throw new Error("Character: unknown move '" + name + "'. Known: " + Object.keys(M.moves).join(", ") +
      ", pose:" + Object.keys(M.poses).join(", pose:"));
  }
  function entry(name) { return resolve(name).e; }
  var r3 = function (x) { return Math.round(x * 1000) / 1000; };
  var clamp = function (v, a, b) { return Math.max(a, Math.min(b, v)); };

  // ---- a deterministic generator (a small LCG), so blinks repeat exactly on every render ------------------------------
  function lcg(seed) {
    var s = (seed >>> 0) || 1;
    return function () { s = (Math.imul(s, 1664525) + 1013904223) >>> 0; return s / 4294967296; };
  }

  // ---- loading -----------------------------------------------------------------------------------------------------
  var LOADED = null;
  function urls() {
    var M = manifest(), u = [];
    Object.keys(M.moves).forEach(function (k) { u.push(CFG.base + M.moves[k].strip); });
    Object.keys(M.poses).forEach(function (k) { u.push(CFG.base + M.poses[k].strip); });
    Object.keys(M.faces).forEach(function (k) { u.push(CFG.base + "faces/" + k + ".png"); });
    return u;
  }
  /** Resolves when every strip and face patch is loaded and decoded, so the first rendered frame is never blank. */
  function ready() {
    if (LOADED) return LOADED;
    LOADED = Promise.all(urls().map(function (u) {
      return new Promise(function (res) {
        var im = new Image();
        im.onload = function () { (im.decode ? im.decode().catch(function () {}) : Promise.resolve()).then(res); };
        im.onerror = function () { res(); };
        im.src = u;
      });
    }));
    return LOADED;
  }

  // ---- the plan ----------------------------------------------------------------------------------------------------
  /** One move's drawings as a list of drawing indexes per 1/24 s tick, with the hold, loop and reverse options applied. */
  function tickList(name, o) {
    var e = entry(name), seq = e.sequence.map(function (s) { return [s[0], s[1]]; });
    if (e.kind === "pose") return [0];
    var ticks = [];
    if (e.kind === "loop") {
      seq.forEach(function (s) { for (var i = 0; i < s[1]; i++) ticks.push(s[0]); });
      return ticks; // looped by the caller
    }
    var holdIdx = e.hold;
    var natural = seq.reduce(function (a, s) { return a + s[1]; }, 0);
    var want = null;
    if (o.dur != null) want = Math.round(o.dur * TICK);
    var hold = o.hold != null ? Math.round(o.hold * TICK) : null;
    // a looped part (a range of the sequence): repeat it, to a count or until the move reaches the wanted length
    if (e.loop) {
      var a = e.loop[0], b = e.loop[1], pre = seq.slice(0, a), mid = seq.slice(a, b), post = seq.slice(b);
      var midT = mid.reduce(function (t, s) { return t + s[1]; }, 0);
      var count = typeof o.loop === "number" ? o.loop : (e.natural_loops || 3);
      if (want != null && typeof o.loop !== "number") {
        var rest = pre.concat(post).reduce(function (t, s) { return t + s[1]; }, 0);
        count = Math.max(1, Math.round((want - rest) / midT));
      }
      var out = pre.slice();
      for (var c = 0; c < count; c++) out = out.concat(mid);
      seq = out.concat(post);
      holdIdx = null;
    } else if (holdIdx != null) {
      if (hold != null) seq[holdIdx][1] = Math.max(1, hold);
      else if (want != null && want > natural) seq[holdIdx][1] += want - natural;
    }
    seq.forEach(function (s) { for (var i = 0; i < s[1]; i++) ticks.push(s[0]); });
    if (o.reverse) ticks.reverse();
    return ticks;
  }

  /** Normalise the script items into segments with resolved x, y, flip, ticks and end time. */
  function plan(inst, items) {
    var segs = [], x = inst.x0, y = inst.y0, flip = inst.flip0, sc = inst.scale;
    items.slice().sort(function (a, b) { return a.t - b.t; }).forEach(function (it) {
      var name = it.move || "idle";
      var found = resolve(name), e = found.e;
      if (it.x != null) x = it.x;
      if (it.y != null) y = it.y;
      if (it.flip != null) flip = !!it.flip;
      var s = { t: it.t, move: name, mv: found.key, x: x, y: y, flip: flip, e: e };
      if (found.key === "walk") {
        var to = it.toX != null ? it.toX : x, dist = Math.abs(to - x);
        var dur = it.dur != null ? it.dur : (dist ? dist / (e.travel_px_per_frame * sc * TICK) : 1);
        // drawings per second chosen from distance and time, so the planted foot keeps pace with the ground
        s.rate = dist ? dist / dur / (e.travel_px_per_frame * sc) : TICK;
        if (it.flip == null && to !== x) flip = to < x;       // the strip faces right: mirror it to walk left
        s.flip = flip; s.to = to; s.dur = dur; s.end = it.t + dur; s.x1 = to; x = to;
      } else {
        s.ticks = tickList(name, { dur: it.dur, hold: it.hold, loop: it.loop, reverse: it.reverse || name === "turn_back" });
        s.looped = e.kind === "loop";
        s.end = s.looped ? (it.dur != null ? it.t + it.dur : Infinity) : (e.kind === "pose" ? Infinity : it.t + s.ticks.length / TICK);
      }
      segs.push(s);
    });
    // a segment ends where the next begins
    for (var i = 0; i < segs.length - 1; i++) if (segs[i].end > segs[i + 1].t) segs[i].end = segs[i + 1].t;
    return segs;
  }

  function segAt(inst, t) {
    for (var i = inst.segs.length - 1; i >= 0; i--) if (inst.segs[i].t <= t + 1e-9) return inst.segs[i];
    return null;
  }

  /** Pure: the character's state at timeline time t. */
  function stateAt(inst, t) {
    var seg = segAt(inst, t), k = Math.floor(t * TICK + 1e-6);
    var S = { move: "idle", key: "idle", draw: 0, x: inst.x0, y: inst.y0, flip: inst.flip0, tick: k };
    if (seg) {
      S.x = seg.x; S.y = seg.y; S.flip = seg.flip;
      var local = t - seg.t;
      if (seg.mv === "walk") {
        if (t < seg.end - 1e-9) {
          S.move = S.key = "walk";
          S.x = seg.x + (seg.to - seg.x) * (seg.dur ? clamp(local / seg.dur, 0, 1) : 0);
          S.draw = Math.floor(local * seg.rate + 1e-6) % seg.e.frames;
        } else S.x = seg.x1;                             // the walk is over: standing on the spot where it ended
      } else if (t < seg.end - 1e-9) {
        var lk = Math.floor(local * TICK + 1e-6);
        S.move = S.key = seg.mv;
        S.draw = seg.looped ? seg.ticks[lk % seg.ticks.length] : seg.ticks[Math.min(lk, seg.ticks.length - 1)];
      }                                                  // a one-shot that has finished leaves the idle drawing: no settle
    }
    var e = entry(S.key);
    var fi = (e.frame_info || [])[S.draw] || {};
    S.mouth = "closed";
    if (fi.mouth && inst.mouth && inst.mouth[k]) S.mouth = inst.mouth[k];
    S.blink = !!(fi.eyes && inst.blinks && inst.blinks[k]);
    S.fi = fi;
    S.e = e;
    return S;
  }

  // ---- speech ------------------------------------------------------------------------------------------------------
  function mouthFromEnvelope(env, fps, t0) {
    var out = {}, s = "closed", hold = 99;
    var t0k = Math.round(t0 * TICK);
    var nTicks = Math.ceil(env.length / fps * TICK);
    for (var j = 0; j < nTicks; j++) {
      var a = Math.floor(j / TICK * fps), b = Math.max(a + 1, Math.ceil((j + 1) / TICK * fps)), L = 0;
      for (var q = a; q < b && q < env.length; q++) L = Math.max(L, env[q]);
      var nx = s;
      if (s === "closed") nx = L >= THRESH.openUp ? "open" : (L >= THRESH.midUp ? "mid" : "closed");
      else if (s === "mid") nx = L >= THRESH.openUp ? "open" : (L < THRESH.midDown ? "closed" : "mid");
      else nx = L < THRESH.midDown ? "closed" : (L < THRESH.openDown ? "mid" : "open");
      hold++;
      if (nx !== s && hold >= THRESH.minHold) { s = nx; hold = 0; }
      if (s !== "closed") out[t0k + j] = s;
    }
    return out;
  }
  function mouthFromWords(words, t0) {
    var out = {};
    words.forEach(function (w, i) {
      var a = Math.round((t0 + w.s) * TICK), b = Math.round((t0 + w.e) * TICK) - 1;
      var pat = i % 2 ? ["open", "mid", "open", "mid"] : ["mid", "open", "mid", "closed"];
      for (var k = a, j = 0; k <= b; k++, j++) { var m = pat[j % pat.length]; if (k === b) m = "closed"; if (m !== "closed") out[k] = m; }
    });
    return out;
  }
  function normWords(words) {
    return (words || []).map(function (x) {
      return { w: String(x.w != null ? x.w : x.word != null ? x.word : x.text || ""), s: Number(x.s != null ? x.s : x.start), e: Number(x.e != null ? x.e : x.end) };
    }).filter(function (x) { return x.w; });
  }

  /** Blink every 3 to 5 s from a fixed seed; never on a tick where the mouth is open (it waits for the next closed tick). */
  function buildBlinks(inst) {
    var rnd = lcg(inst.seed), out = {}, tick = Math.round((1.2 + 1.5 * rnd()) * TICK), end = Math.ceil(inst.until * TICK);
    while (tick < end) {
      var s = tick, guard = 0;
      while (guard++ < 96) {
        var open = false;
        for (var j = 0; j < BLINK_TICKS; j++) if (inst.mouth && inst.mouth[s + j] === "open") open = true;
        if (!open) break;
        s++;
      }
      for (var j2 = 0; j2 < BLINK_TICKS; j2++) out[s + j2] = true;
      tick = s + Math.round((3.0 + 2.0 * rnd()) * TICK);
    }
    inst.blinks = out;
  }

  // ---- DOM ---------------------------------------------------------------------------------------------------------
  function el(tag, css, parent) {
    var n = document.createElement(tag);
    n.style.cssText = css;
    if (parent) parent.appendChild(n);
    return n;
  }
  function mount(parent, o) {
    o = o || {};
    var M = manifest(), sc = o.scale || 2;
    if (sc !== Math.round(sc) || sc < 1) throw new Error("Character: scale must be a whole number (2, 3 or 4), got " + sc);
    if (typeof parent === "string") parent = document.querySelector(parent);
    var CW = M.canvas[0], CH = M.canvas[1];
    var root = el("div", "position:absolute;left:0;top:0;width:" + CW * sc + "px;height:" + CH * sc + "px;pointer-events:none;" +
      "image-rendering:pixelated;", parent);
    root.className = "character";
    root.setAttribute("data-layout-allow-overflow", "");   // a character can start off canvas on purpose
    var divs = {};
    function strip(name, e) {
      var d = el("div", "position:absolute;left:0;top:0;width:" + CW * sc + "px;height:" + CH * sc + "px;display:none;" +
        "background:url(" + CFG.base + e.strip + ") 0 0 / " + e.frames * CW * sc + "px " + CH * sc + "px no-repeat;" +
        "image-rendering:pixelated;", root);
      d.setAttribute("data-layout-allow-overflow", "");
      divs[name] = d;
    }
    Object.keys(M.moves).forEach(function (k) { strip(k, M.moves[k]); });
    Object.keys(M.poses).forEach(function (k) { strip("pose:" + k, M.poses[k]); });
    var patch = {};
    ["mouth_mid", "mouth_open", "eyes_closed"].forEach(function (k) {
      var sz = M.faces[k];
      patch[k] = el("div", "position:absolute;display:none;width:" + sz[0] * sc + "px;height:" + sz[1] * sc + "px;" +
        "background:url(" + CFG.base + "faces/" + k + ".png) 0 0 / " + sz[0] * sc + "px " + sz[1] * sc + "px no-repeat;image-rendering:pixelated;", root);
    });
    var inst = { root: root, divs: divs, patch: patch, scale: sc, x0: o.x || 0, y0: o.y || 0, flip0: !!o.flip, segs: [], mouth: {},
      blinks: {}, seed: o.seed != null ? o.seed : 7, until: 0, cur: null, drivers: [] };
    root.style.left = Math.round(inst.x0 - entry("idle").ref_col * sc) + "px";
    root.style.top = Math.round(inst.y0 - M.baseline_row * sc) + "px";
    LAST = inst;
    inst.segs = [];
    render(inst, 0);
    return inst;
  }
  var LAST = null;

  function render(inst, t) {
    var S = stateAt(inst, t), M = manifest(), sc = inst.scale, e = S.e;
    Object.keys(inst.divs).forEach(function (k) {
      var show = k === S.key;
      if (inst.divs[k]._on !== show) { inst.divs[k].style.display = show ? "block" : "none"; inst.divs[k]._on = show; }
    });
    inst.divs[S.key].style.backgroundPosition = -S.draw * M.canvas[0] * sc + "px 0px";
    // x is the head-centre column, y the feet; the root is mirrored about that same column, so a flip never moves it
    inst.root.style.left = Math.round(S.x - e.ref_col * sc) + "px";
    inst.root.style.top = Math.round(S.y - M.baseline_row * sc) + "px";
    inst.root.style.transformOrigin = Math.round(e.ref_col * sc) + "px 0px";
    inst.root.style.transform = S.flip ? "scaleX(-1)" : "none";
    var fi = S.fi, pm = inst.patch;
    ["mouth_mid", "mouth_open", "eyes_closed"].forEach(function (k) { pm[k].style.display = "none"; });
    if (fi.mouth && S.mouth !== "closed") {
      var p = pm["mouth_" + S.mouth];
      p.style.left = fi.mouth[0] * sc + "px"; p.style.top = fi.mouth[1] * sc + "px"; p.style.display = "block";
    }
    if (fi.eyes && S.blink) {
      pm.eyes_closed.style.left = fi.eyes[0] * sc + "px"; pm.eyes_closed.style.top = fi.eyes[1] * sc + "px";
      pm.eyes_closed.style.display = "block";
    }
    return S;
  }

  /** One paused tween per mount drives render(t); its length is the end of the plan (or opts.until, the composition length). */
  function bind(tl, inst) {
    var D = Math.max(inst.until, 0.05);
    if (!inst.driver) {
      var proxy = { p: 0 };
      inst.driver = tl.to(proxy, { p: 1, duration: D, ease: "none", onUpdate: function () { render(inst, this.time()); } }, 0);
    } else {
      inst.driver.duration(D);
    }
    render(inst, 0);
  }
  function horizon(inst, extra) {
    var end = 0;
    inst.segs.forEach(function (s) { if (isFinite(s.end)) end = Math.max(end, s.end); end = Math.max(end, s.t); });
    Object.keys(inst.mouth).forEach(function (k) { end = Math.max(end, (+k + 1) / TICK); });
    inst.until = Math.max(inst.until, end + 0.5, extra || 0);
  }

  /** Schedule moves. items: [{ t, move, x?, y?, flip?, toX?, dur?, hold?, loop?, reverse? }]. opts: { character, until }. */
  function script(tl, items, opts) {
    opts = opts || {};
    var inst = opts.character || LAST;
    if (!inst) throw new Error("Character.script: mount() first");
    inst.segs = plan(inst, items);
    horizon(inst, opts.until);
    buildBlinks(inst);
    bind(tl, inst);
    return inst;
  }
  /** Move the mouth. { envelope: [0..1], fps, t0 } (a loudness envelope of the voice-over) or { words: [{word,start,end}], t0 }. */
  function speak(tl, o, inst) {
    inst = inst || LAST;
    if (!inst) throw new Error("Character.speak: mount() first");
    var t0 = o.t0 || 0, m;
    if (o.envelope) m = mouthFromEnvelope(o.envelope, o.fps || 24, t0);
    else m = mouthFromWords(normWords(o.words), t0);
    Object.keys(m).forEach(function (k) { inst.mouth[k] = m[k]; });
    horizon(inst, o.until);
    buildBlinks(inst);
    bind(tl, inst);
    return inst;
  }
  /** Seconds of a spoken word, so a move can land on it: Character.at(words, "launch", { offset: -0.1 }). */
  function at(words, text, o) {
    o = o || {};
    var w = null;
    var key = function (s) { return String(s).toLowerCase().replace(/[^a-z0-9]/g, ""); }, seen = 0;
    normWords(words).some(function (x) { if (key(x.w) === key(text) && seen++ === (o.nth || 0)) { w = x; return true; } return false; });
    if (!w) throw new Error("Character.at: word not found: " + text);
    return r3((o.edge === "end" ? w.e : w.s) + (o.offset || 0));
  }
  /** Seconds a walk takes at the authored cadence over a distance in screen px (so the feet land flat). */
  function walkTime(dist, scale) {
    var M = manifest();
    if (!M.moves.walk) throw new Error("Character.walkTime: this character has no walk move");
    return r3(Math.abs(dist) / (M.moves.walk.travel_px_per_frame * (scale || 2) * TICK));
  }
  function moves() { var M = manifest(); return Object.keys(M.moves).concat(Object.keys(M.poses).map(function (k) { return "pose:" + k; })); }

  window.Character = { configure: configure, ready: ready, mount: mount, script: script, speak: speak, at: at, walkTime: walkTime,
    moves: moves, stateAt: stateAt, _plan: plan, _mouthFromEnvelope: mouthFromEnvelope, _buildBlinks: buildBlinks, THRESH: THRESH, TICK: TICK };
})();
