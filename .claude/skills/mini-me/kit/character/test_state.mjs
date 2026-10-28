// Node check of character.js against a packed character: the manifest's shape and files, then the pure parts of the
// runtime (same picture for the same time, holds, loops, held poses, mouth hysteresis, blinks that wait for a closed
// mouth, anchors) and one render through a stand-in DOM and timeline.
//
//   node test_state.mjs [path/to/me/character/manifest.json]
//
// With no path it reads $KIT_ME/character/manifest.json, or me/character/manifest.json at the top of the kit.
// Exits 0 when every check passes.
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const meDir = process.env.KIT_ME || path.join(here, "..", "..", "..", "..", "..", "me");
const manifestPath = path.resolve(process.argv[2] || path.join(meDir, "character", "manifest.json"));
if (!fs.existsSync(manifestPath)) {
  console.log("No packed character at " + manifestPath + ". Pack it first: uv run python -m kit.character pack");
  process.exit(1);
}
const folder = path.dirname(manifestPath);
const M = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
const CORE = ["idle", "talking", "point_left", "point_right", "point_up", "thumbs_up", "shrug", "thinking", "mind_blown",
  "celebrate", "hold_card", "typing", "facepalm", "wave"];
const MOVES = ["idle", "talking", "point_left", "point_right", "point_up"];

let fails = 0;
const ok = (c, m) => { if (!c) { fails++; console.log("FAIL", m); } else console.log("ok  ", m); };

// ---- a stand-in page: just enough DOM and GSAP timeline for mount, script and render --------------------------------
function node() {
  return { style: {}, children: [], appendChild(c) { this.children.push(c); return c; }, setAttribute() {}, className: "" };
}
function load(manifest) {
  const win = { CHARACTER_MANIFEST: manifest };
  const doc = { createElement: node, querySelector: () => node() };
  const ctx = vm.createContext({ window: win, document: doc, Image: function () {}, Promise, Math, Object, Array, JSON,
    Infinity, isFinite, String, Number, Error });
  vm.runInContext(fs.readFileSync(path.join(here, "character.js"), "utf8"), ctx);
  return win.Character;
}
function timeline() {
  const tweens = [];
  return {
    tweens,
    to(target, vars) {
      const tw = { vars, at: 0, length: vars.duration, duration(d) { this.length = d; return this; }, time() { return this.at; } };
      tweens.push(tw);
      return tw;
    },
  };
}
function seek(tl, t) { tl.tweens.forEach((tw) => { tw.at = t; tw.vars.onUpdate.call(tw); }); }

// ---- the manifest ---------------------------------------------------------------------------------------------------
ok(JSON.stringify(M.canvas) === "[245,284]" && M.baseline_row === 280 && M.native_fps === 24,
  "canvas 245 x 284, feet on row 280, 24 drawings a second");
ok(Array.isArray(M.palette) && M.palette.length > 1 && M.palette.every((c) => /^#[0-9a-f]{6}$/.test(c)),
  "the locked palette is a list of " + (M.palette || []).length + " hex colours");
ok(CORE.every((n) => M.poses[n]), "every core pose is packed as a held pose");
ok(MOVES.every((n) => M.moves[n]), "idle, talking and the three points are packed as moves");
const faceNames = ["mouth_closed", "mouth_mid", "mouth_open", "eyes_open", "eyes_closed"];
ok(faceNames.every((k) => Array.isArray(M.faces[k]) && M.faces[k][0] > 0 && M.faces[k][1] > 0), "face patch sizes for every state");
ok(faceNames.every((k) => fs.existsSync(path.join(folder, "faces", k + ".png"))), "every face patch file is there");

function pngSize(file) {
  const b = fs.readFileSync(file);
  return [b.readUInt32BE(16), b.readUInt32BE(20)];
}
const entries = Object.entries(M.moves).concat(Object.entries(M.poses).map(([k, v]) => ["pose:" + k, v]));
let shapeProblems = [];
for (const [name, e] of entries) {
  const file = path.join(folder, e.strip);
  if (!fs.existsSync(file)) { shapeProblems.push(name + ": no strip"); continue; }
  const [w, h] = pngSize(file);
  if (w !== 245 * e.frames || h !== 284) shapeProblems.push(name + ": strip is " + w + " x " + h);
  if (!Array.isArray(e.frame_info) || e.frame_info.length !== e.frames) shapeProblems.push(name + ": frame_info length");
  if (!e.sequence.every(([f, ticks]) => f >= 0 && f < e.frames && ticks >= 1)) shapeProblems.push(name + ": sequence");
  if (e.kind === "oneshot" && !(e.hold >= 0 && e.hold < e.sequence.length)) shapeProblems.push(name + ": hold");
  for (const fi of e.frame_info) {
    if (fi.mouth) {
      const [mw, mh] = M.faces.mouth_open;
      if (fi.mouth[0] < 0 || fi.mouth[1] < 0 || fi.mouth[0] + mw > 245 || fi.mouth[1] + mh > 284) shapeProblems.push(name + ": mouth off the canvas");
    }
    if (fi.eyes) {
      const [ew, eh] = M.faces.eyes_closed;
      if (fi.eyes[0] < 0 || fi.eyes[1] < 0 || fi.eyes[0] + ew > 245 || fi.eyes[1] + eh > 284) shapeProblems.push(name + ": eyes off the canvas");
    }
  }
  if (!(e.ref_col > 0 && e.ref_col < 245)) shapeProblems.push(name + ": ref_col");
}
ok(shapeProblems.length === 0, "every strip, sequence, hold and anchor is well formed" + (shapeProblems.length ? ": " + shapeProblems.join("; ") : ""));
ok(M.moves.talking.kind === "loop" && ["point_left", "point_right", "point_up"].every((n) => M.moves[n].kind === "oneshot"),
  "talking loops and the points play once");
ok(M.moves.idle.frame_info[0].mouth && M.moves.idle.frame_info[0].eyes, "idle carries the mouth and eye anchors");

// ---- the plan ---------------------------------------------------------------------------------------------------------
const C = load(M);
const inst = { scale: 2, x0: 300, y0: 1430, flip0: false, segs: [], mouth: {}, blinks: {}, seed: 7, until: 0 };
inst.segs = C._plan(inst, [
  { t: 0, move: "talking", dur: 2 }, { t: 2, move: "point_left", hold: 1.0 }, { t: 4.5, move: "pose:shrug" },
  { t: 5, move: "thumbs_up" }, { t: 6, move: "talk" }, { t: 7.5, move: "idle" },
]);
const a = C.stateAt(inst, 2.37), b = (C.stateAt(inst, 6.1), C.stateAt(inst, 2.37));
ok(JSON.stringify([a.key, a.draw, a.x, a.flip]) === JSON.stringify([b.key, b.draw, b.x, b.flip]), "stateAt is a pure function of t");
const talkDraws = new Set();
for (let t = 0; t < 2; t += 1 / 60) talkDraws.add(C.stateAt(inst, t).draw);
ok(C.stateAt(inst, 1.0).key === "talking" && talkDraws.size === M.moves.talking.frames, "talking loops through all its drawings (" + talkDraws.size + ")");
const point = inst.segs[1];
const keyDrawing = M.moves.point_left.sequence[M.moves.point_left.hold][0];
ok(point.ticks.filter((v) => v === keyDrawing).length === 24, "point_left with hold 1.0 holds the key drawing for 24 ticks");
ok(C.stateAt(inst, 2 + point.ticks.length / 24 + 0.05).key === "idle", "a one-shot that has finished stands on idle");
ok(C.stateAt(inst, 4.6).key === "pose:shrug" && C.stateAt(inst, 4.99).key === "pose:shrug", "a held pose stays until the next item");
ok(C.stateAt(inst, 5.5).key === "pose:thumbs_up", "a core pose named without pose: is held too");
ok(C.stateAt(inst, 6.5).key === "talking", "talk is another name for talking");
let threw = "";
try { C._plan(inst, [{ t: 0, move: "moonwalk" }]); } catch (err) { threw = String(err.message); }
ok(threw.indexOf("unknown move 'moonwalk'") >= 0 && threw.indexOf("pose:wave") >= 0, "an unknown move names the known ones");

// ---- speech and blinks ------------------------------------------------------------------------------------------------
const flicker = [];
for (let i = 0; i < 96; i++) flicker.push(i % 2 ? 0.2 : 0.18);
ok(Object.values(C._mouthFromEnvelope(flicker, 24, 0)).every((v) => v === "mid"), "a 0.18 / 0.20 flicker stays half open (no chatter)");
const env = [];
for (let i = 0; i < 240; i++) env.push(0.5 + 0.45 * Math.sin(i / 5));
const mouth = C._mouthFromEnvelope(env, 24, 0);
let flips = 0, prev = null;
for (let k = 0; k < 240; k++) { const v = mouth[k] || "closed"; if (prev && v !== prev) flips++; prev = v; }
ok(flips < 40, "a slow 0.5 +/- 0.45 sine changes shape " + flips + " times in 10 s (minimum hold 2 ticks)");
inst.mouth = mouth; inst.until = 12; C._buildBlinks(inst);
let clash = 0;
Object.keys(inst.blinks).forEach((k) => { if (inst.mouth[k] === "open") clash++; });
ok(Object.keys(inst.blinks).length > 0 && clash === 0, "blinks are scheduled (" + Object.keys(inst.blinks).length / 3 + ") and none lands on an open mouth");
const starts = Object.keys(inst.blinks).map(Number).sort((x, y) => x - y).filter((k, i, arr) => i === 0 || k - arr[i - 1] > 1);
const gaps = [];
for (let i = 1; i < starts.length; i++) gaps.push((starts[i] - starts[i - 1]) / 24);
ok(gaps.every((g) => g >= 2.9 && g <= 5.6), "blinks come every 3 to 5 s: " + gaps.map((g) => g.toFixed(1)).join(", "));
const ownFace = CORE.find((n) => !M.poses[n].frame_info[0].mouth);
if (ownFace) {
  const still = { scale: 2, x0: 0, y0: 0, flip0: false, mouth: {}, blinks: {}, seed: 7, until: 0 };
  still.segs = C._plan(still, [{ t: 0, move: "pose:" + ownFace }]);
  for (let k = 0; k < 48; k++) still.mouth[k] = "open";
  ok(C.stateAt(still, 1).mouth === "closed", "the mouth never moves on a drawing without a mouth anchor (" + ownFace + ")");
}

// ---- walking: this character has no walk; a stand-in walk checks the walking code still works ------------------------
threw = "";
try { C.walkTime(400, 2); } catch (err) { threw = String(err.message); }
ok(M.moves.walk ? true : threw.indexOf("no walk move") >= 0, "walkTime says plainly when there is no walk move");
const withWalk = JSON.parse(JSON.stringify(M));
withWalk.moves.walk = Object.assign({}, withWalk.moves.talking, { kind: "loop", travel_px_per_frame: 10, sequence: withWalk.moves.talking.sequence.map(([f]) => [f, 1]) });
const W = load(withWalk);
const walker = { scale: 2, x0: 300, y0: 1430, flip0: false, segs: [], mouth: {}, blinks: {}, seed: 7, until: 0 };
walker.segs = W._plan(walker, [{ t: 0, move: "walk", x: -120, toX: 300, dur: 0.9 }, { t: 0.9, move: "talking" }]);
ok(W.stateAt(walker, 0).x === -120 && Math.abs(W.stateAt(walker, 0.45).x - 90) < 1e-6 && W.stateAt(walker, 0.95).key === "talking",
  "a walk moves x from start to end, then hands over");
ok(Math.abs(walker.segs[0].rate * 10 * 2 * 0.9 - 420) < 1e-6, "walk rate x travel x time = distance (the feet keep pace)");
ok(Math.abs(W.walkTime(480, 2) - 1) < 1e-9, "walkTime is distance over the authored cadence");

// ---- one render through the stand-in page -----------------------------------------------------------------------------
const tl = timeline();
const ch = C.mount(node(), { scale: 2, x: 300, y: 1430 });
C.script(tl, [{ t: 0, move: "talking", dur: 2 }, { t: 2, move: "point_right", hold: 0.5 }, { t: 4, move: "pose:wave" }], { character: ch, until: 6 });
C.speak(tl, { envelope: env, fps: 24, t0: 0 }, ch);
ok(tl.tweens.length === 1 && tl.tweens[0].length >= 6, "one paused driver tween per character, as long as the composition");
seek(tl, 0.5);
const shown = Object.keys(ch.divs).filter((k) => ch.divs[k].style.display === "block");
ok(shown.length === 1 && shown[0] === "talking", "only the playing move's strip is shown (" + shown.join(", ") + ")");
const s05 = C.stateAt(ch, 0.5);
ok(ch.divs.talking.style.backgroundPosition === -s05.draw * 245 * 2 + "px 0px", "the strip is moved to the drawing for the time");
let opened = null;
for (let k = 0; k < 48; k++) { if (ch.mouth[k] === "open" && C.stateAt(ch, k / 24).fi.mouth) { opened = k; break; } }
if (opened !== null) {
  seek(tl, opened / 24 + 1e-4);
  const fi = C.stateAt(ch, opened / 24 + 1e-4).fi;
  ok(ch.patch.mouth_open.style.display === "block" && ch.patch.mouth_open.style.left === fi.mouth[0] * 2 + "px",
    "an open mouth shows its patch at the anchor, at the character's scale");
}
seek(tl, 4.5);
ok(ch.divs["pose:wave"].style.display === "block" && ch.divs.talking.style.display === "none", "a held pose shows its own strip");
const left = ch.root.style.left;
seek(tl, 4.6);
ok(ch.root.style.left === left, "a held pose does not move: no breathing or bobbing");

console.log(fails ? fails + " FAILED" : "all passed");
process.exit(fails ? 1 : 0);
