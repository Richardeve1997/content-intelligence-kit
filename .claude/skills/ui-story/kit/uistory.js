/* UI story kit: seek-safe GSAP helpers for the graphics layer.

   Every helper takes the ONE paused root timeline and an absolute (composition) time in seconds.
   Layout windows come from lib/layout.js (written by layout.py) and are drawn by composite_v2.py, so
   UIS.modeAt(t) only tells a scene which layout it sits in.

   Hidden starting states are applied straight away with gsap.set at build time, never by CSS transforms
   and never as tl.set(..., 0): a zero-length set at position 0 does not render while the playhead sits
   exactly on 0, so frame 0 would show every hidden layer. The tweens then record the hidden state as their
   start, so any frame can be rendered in any order and seeking back hides things again.

   Write an entrance as gsap.set(el, {opacity: 0}) and then tl.to(el, {opacity: 1, ...}, t). A
   tl.fromTo() whose opacity lives only in the FROM values shows in a snapshot but can vanish in the
   render, because the renderer seeks to the end first and then steps from 0 (seek_parity_gate.py
   catches it).

   Timings that read well:
     scene or card entrance  0.12-0.20 s  opacity 0 -> 1 and scale 0.9 -> 1, power3.out
     row or list build       0.18 s rise, one row per spoken word
     tick or chip pop        0.22-0.26 s  back.out
     layout switch           a hard cut, never a wipe
     captions                a hard swap between groups, no entrance animation
*/
(function(){
  const U = {};
  const q = (s) => (typeof s === 'string' ? document.querySelector(s) : s);
  const qa = (s) => (typeof s === 'string' ? Array.from(document.querySelectorAll(s)) : Array.from(s));

  // ---- word cues: window.UIS_WORDS = [[word, start, end], ...] written by build_captions.py ----
  const norm = (w) => String(w).toLowerCase().replace(/[^a-z0-9#]/g, '');
  U.cue = function(phrase, nth, which){
    // start (default) or end time of the nth (1-based) occurrence of `phrase` in the base transcript
    const W = window.UIS_WORDS || [];
    const toks = String(phrase).split(/\s+/).map(norm).filter(Boolean);
    let hit = 0; nth = nth || 1;
    for (let i = 0; i + toks.length <= W.length; i++){
      let ok = true;
      for (let k = 0; k < toks.length; k++){ if (norm(W[i+k][0]) !== toks[k]) { ok = false; break; } }
      if (ok && ++hit === nth) return which === 'end' ? W[i+toks.length-1][2] : W[i][1];
    }
    throw new Error('UIS.cue: phrase not found: "' + phrase + '" #' + nth);
  };
  U.cueEnd = (phrase, nth) => U.cue(phrase, nth, 'end');
  // snap a time to the 60 fps frame grid, so the graphics change on the same frame as the compositor
  U.snap = (t) => Math.round(t * 60) / 60;

  // ---- layout: windows live in lib/layout.js; the compositor draws canvas, PIP and face ----
  U.modeAt = function(t){
    const L = window.UIS_LAYOUT || [];
    for (const w of L){ if (t >= w[1] && t < w[2]) return w[0]; }
    return L.length ? L[L.length - 1][0] : 'split';
  };
  U.windows = function(mode){ return (window.UIS_LAYOUT || []).filter(w => w[0] === mode).map(w => [w[1], w[2]]); };
  // one window as {mode, t0, t1, name, at}, found by its name in layout.json, by the phrase it starts at, or by a
  // time inside it. Never address a window by its place in UIS_LAYOUT: a window added before it moves that place.
  const phrase = (p) => String(p).split(/\s+/).map(norm).filter(Boolean).join(' ');
  U.win = function(key){
    const L = window.UIS_LAYOUT || [];
    const as = (w) => ({mode: w[0], t0: w[1], t1: w[2], name: w[3] == null ? null : w[3], at: w[4] == null ? null : w[4]});
    if (typeof key === 'number'){
      const hit = L.find((w, i) => key >= w[1] && (key < w[2] || (i === L.length - 1 && key <= w[2])));
      if (hit) return as(hit);
      throw new Error('UIS.win: no layout window holds ' + key + ' s');
    }
    const wanted = phrase(key);
    const hits = L.filter(w => wanted && [w[3], w[4]].some(label => label != null && phrase(label) === wanted));
    if (hits.length === 1) return as(hits[0]);
    if (!hits.length) throw new Error('UIS.win: no layout window is named "' + key + '" or starts at that phrase');
    throw new Error('UIS.win: ' + hits.length + ' layout windows answer to "' + key + '"; give one a "name" in layout.json');
  };

  // ---- scene visibility: plain (non-clip) layers switched with hard cuts ----
  U.show = function(tl, el, tin, tout){
    if (tin > 0) { gsap.set(el, {autoAlpha: 0}); tl.set(el, {autoAlpha: 1}, tin); }
    if (tout != null) tl.set(el, {autoAlpha: 0}, tout);
  };

  // ---- entrances ----
  U.pop = function(tl, el, t, o){
    o = o || {};
    gsap.set(el, {opacity: 0, scale: o.from != null ? o.from : 0.9, transformOrigin: o.origin || '50% 50%'});
    tl.to(el, {opacity: 1, scale: 1, duration: o.dur || 0.18, ease: o.ease || 'power3.out'}, t);
  };
  U.fade = function(tl, el, t, dur){
    gsap.set(el, {opacity: 0});
    tl.to(el, {opacity: 1, duration: dur || 0.12, ease: 'none'}, t);
  };
  U.rise = function(tl, el, t, o){
    o = o || {};
    gsap.set(el, {opacity: 0, y: o.y != null ? o.y : 14});
    tl.to(el, {opacity: 1, y: 0, duration: o.dur || 0.18, ease: 'power2.out'}, t);
  };
  U.tick = function(tl, el, t){
    gsap.set(el, {opacity: 0, scale: 0.35});
    tl.to(el, {opacity: 1, scale: 1, duration: 0.24, ease: 'back.out(2.6)'}, t);
  };
  U.chip = function(tl, el, t, rot){
    gsap.set(el, {opacity: 0, scale: 0.55, rotation: rot || -4, transformOrigin: '50% 50%'});
    tl.to(el, {opacity: 1, scale: 1, rotation: 0, duration: 0.26, ease: 'back.out(2.2)'}, t);
  };
  U.out = function(tl, el, t, dur){ tl.to(el, {opacity: 0, duration: dur || 0.1, ease: 'none'}, t); };

  // ---- a list that fills: rows appear on their word times, with an optional tick per row ----
  U.fill = function(tl, rows, times, tickSel){
    qa(rows).forEach((r, i) => {
      if (times[i] == null) return;
      U.rise(tl, r, times[i]);
      const tk = tickSel ? r.querySelector(tickSel) : null;
      if (tk) U.tick(tl, tk, times[i] + 0.22);
    });
  };

  // ---- typed text: one span per character, revealed with tl.set (seek-safe, no onUpdate) ----
  // data-uis-typed and data-uis-count tell the reading-load audit which is which: both are one span a piece
  U.type = function(tl, el, text, t, cps){
    el = q(el); cps = cps || 22;
    el.setAttribute('data-uis-typed', '');
    el.textContent = '';
    const spans = [];
    for (const ch of text){ const s = document.createElement('span'); s.textContent = ch; el.appendChild(s); spans.push(s); }
    spans.forEach((s, i) => { gsap.set(s, {display: 'none'}); tl.set(s, {display: 'inline'}, t + i / cps); });
    return t + text.length / cps;
  };

  // ---- a count that climbs to its value on the word (the final width is what must fit the card) ----
  U.count = function(tl, el, to, t, o){
    o = o || {};
    el = q(el);
    const from = o.from || 0, dur = o.dur || 0.8, steps = o.steps || 24, fmt = o.format || ((v) => Math.round(v).toLocaleString('en-US'));
    const prefix = o.prefix || '', suffix = o.suffix || '';
    el.setAttribute('data-uis-count', '');
    el.textContent = '';
    for (let i = 0; i <= steps; i++){
      const s = document.createElement('span');
      const v = from + (to - from) * (1 - Math.pow(1 - i / steps, 3));
      s.textContent = prefix + fmt(i === steps ? to : v) + suffix;
      s.style.display = 'none';
      el.appendChild(s);
      if (i === 0) { gsap.set(s, {display: 'inline'}); }
      tl.set(s, {display: 'inline'}, t + (dur * i) / steps);
      if (i < steps) tl.set(s, {display: 'none'}, t + (dur * (i + 1)) / steps);
    }
  };

  // ---- focus: dim the others, keep one ----
  U.dimOthers = function(tl, els, keep, t, level){
    qa(els).forEach((e) => { if (e !== q(keep)) tl.to(e, {opacity: level != null ? level : 0.22, filter: 'grayscale(1)', duration: 0.2, ease: 'power1.out'}, t); });
  };
  U.light = function(tl, el, t){  // grey to full colour, on its word
    gsap.set(el, {opacity: 0.3, filter: 'grayscale(1)'});
    tl.to(el, {opacity: 1, filter: 'grayscale(0)', duration: 0.18, ease: 'power2.out'}, t);
  };
  U.zoomTo = function(tl, el, t, s, x, y){
    tl.to(el, {scale: s || 1.8, x: x || 0, y: y || 0, duration: 0.35, ease: 'power3.inOut'}, t);
  };

  // ---- morph: move and scale one element onto a target box (a logo into a card header) ----
  U.morph = function(tl, el, t, to, dur){
    tl.to(el, {x: to.x || 0, y: to.y || 0, scale: to.scale || 1, duration: dur || 0.22, ease: 'power3.inOut'}, t);
  };

  // ---- a stroke that draws (a strike-through, an arrow, a connector) ----
  U.draw = function(tl, path, t, dur){
    path = q(path); const L = path.getTotalLength ? path.getTotalLength() : 400;
    // the gap is longer than the path: a bare L dasharray repeats the dash, and the next dash's round cap
    // shows as a dot at the end of the path before the stroke draws
    gsap.set(path, {strokeDasharray: L + ' ' + (L + 20), strokeDashoffset: L});
    tl.to(path, {strokeDashoffset: 0, duration: dur || 0.22, ease: 'power2.out'}, t);
  };

  // ---- ring or highlight box ----
  U.ring = function(tl, el, t){ U.pop(tl, el, t, {from: 1.08, dur: 0.2}); };
  U.hl = U.ring;

  // ---- product in use ----
  // a camera move onto a detail of the footage: animate the .zoomer wrapper, never the timed <video>.
  // It lands on the spoken word and HOLDS (no drifting pan).
  U.detail = function(tl, zoomer, t, s, x, y, dur){
    gsap.set(zoomer, {transformOrigin: '0 0'});
    tl.to(zoomer, {scale: s || 1.6, x: x || 0, y: y || 0, duration: dur || 0.5, ease: 'power3.inOut'}, t);
  };
  U.undetail = function(tl, zoomer, t, dur){
    tl.to(zoomer, {scale: 1, x: 0, y: 0, duration: dur || 0.4, ease: 'power3.inOut'}, t);
  };
  // the cursor glides to (x, y) in its parent's coordinates; a click ripple can follow on arrival
  U.cursor = function(tl, el, t, x, y, dur){
    tl.to(el, {left: x, top: y, duration: dur || 0.45, ease: 'power2.inOut'}, t);
    return t + (dur || 0.45);
  };
  U.click = function(tl, el, t){
    gsap.set(el, {opacity: 0, scale: 0.3});
    tl.to(el, {opacity: 1, scale: 1, duration: 0.12, ease: 'power2.out'}, t)
      .to(el, {opacity: 0, scale: 1.5, duration: 0.3, ease: 'power1.out'}, t + 0.12);
  };

  // ---- karaoke captions: the spoken-word plate steps from word to word.
  // window.UIS_CAPWORDS = [[plate_id, on, off], ...] from build_captions.py. Call once, before the timeline registers.
  U.captions = function(tl){
    (window.UIS_CAPWORDS || []).forEach(([id, s, e]) => {
      const pl = document.getElementById(id);
      if (!pl) return;
      gsap.set(pl, {opacity: 0});
      tl.set(pl, {opacity: 1}, s);
      tl.set(pl, {opacity: 0}, e);
    });
  };

  window.UIS = U;
})();
