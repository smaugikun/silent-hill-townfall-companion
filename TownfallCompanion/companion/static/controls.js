// The CRTV's physical controls. The AV OUT / VIEW selector slides along its slot under the finger
// (or flips over on a tap) and clicks into either end. The TUNING buttons sink while held, tune one
// step on a tap and repeat, speeding up, while held. The F key presses in the fine-tune mini-game.
import { $, clamp01, rad } from "./util.js";
import { controlHaptic } from "./haptics.js";
import { confirmFineTune, control, onControlChange, setSelector, tune } from "./scanner.js";

const CANVAS_WIDTH = 1086; // PSD px, the unit of the positions below

// The selector's slot runs up to the right at 45.75 degrees; the knob travels 140 px from VIEW
// (the lower end, where the PSD draws it) to AV OUT, where it sits against the rim as at VIEW.
const SLOT_ANGLE = rad(-45.75);
const SLOT_TRAVEL = 140;
const SLOT = {x: Math.cos(SLOT_ANGLE), y: Math.sin(SLOT_ANGLE)};
const TAP_SLOP_PX = 8; // a touch that moves less than this is a tap

const STEP = 0.005;          // one tap of a TUNING button, on the 0..1 dial
const REPEAT_DELAY_MS = 350; // held this long, it starts repeating...
const REPEAT_MS = [90, 25];  // ...every 90 ms, speeding up to every 25 ms...
const ACCEL_MS = 2000;       // ...over two seconds

const knob = $("partSlider");
// Where the knob sits along the slot: 0 = VIEW, 1 = AV OUT.
const stopOf = (position) => position === "VIEW" ? 0 : 1;

function placeKnob(t) {
  knob.style.setProperty("--knob-x", (t * SLOT_TRAVEL * SLOT.x).toFixed(2));
  knob.style.setProperty("--knob-y", (t * SLOT_TRAVEL * SLOT.y).toFixed(2));
}

// What sliding it feels like: out of its notch, past the middle, against an end.
function feel(drag, t) {
  if (!drag.unlocked && Math.abs(t - drag.from) > 0.12) {
    drag.unlocked = true;
    controlHaptic("unlock");
  }
  if ((drag.t - 0.5) * (t - 0.5) < 0) controlHaptic("detent");
  const atEnd = t < 0.01 || t > 0.99;
  if (atEnd && !drag.atEnd) controlHaptic("end");
  drag.atEnd = atEnd;
}

function bindSelector(hit) {
  let drag = null;
  hit.addEventListener("pointerdown", (ev) => {
    if (drag) return;
    ev.preventDefault();
    try { hit.setPointerCapture(ev.pointerId); } catch {} // keeps the drag when the finger leaves the slot
    const from = stopOf(control.selector);
    const scale = $("crtv").getBoundingClientRect().width / CANVAS_WIDTH;
    drag = {id: ev.pointerId, x: ev.clientX, y: ev.clientY, from, t: from, travelPx: SLOT_TRAVEL * scale,
            moved: false, unlocked: false, atEnd: true};
  });
  hit.addEventListener("pointermove", (ev) => {
    if (ev.pointerId !== drag?.id) return;
    const dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
    if (!drag.moved) {
      if (Math.hypot(dx, dy) < TAP_SLOP_PX) return;
      drag.moved = true;
      knob.classList.add("dragging");
    }
    const t = clamp01(drag.from + (dx * SLOT.x + dy * SLOT.y) / drag.travelPx);
    feel(drag, t);
    drag.t = t;
    placeKnob(t);
  });
  const release = (ev) => {
    if (ev.pointerId !== drag?.id) return;
    const {from, t, moved} = drag;
    drag = null;
    knob.classList.remove("dragging");
    // A tap flips it; let go mid-slot, it springs to the nearer end; a cancelled touch puts it back.
    const to = ev.type === "pointercancel" ? from : moved ? Math.round(t) : 1 - from;
    placeKnob(to);
    if (to !== from) controlHaptic("lock");
    setSelector(to === 0 ? "VIEW" : "AV_OUT");
  };
  for (const type of ["pointerup", "pointercancel", "lostpointercapture"]) hit.addEventListener(type, release);
}

function bindTuning(hit, part, direction) {
  let hold = null;
  const step = () => { if (tune(direction * STEP)) controlHaptic("notch"); };
  const repeat = () => {
    hold.repeating = true;
    step();
    const k = Math.min(1, (performance.now() - hold.since - REPEAT_DELAY_MS) / ACCEL_MS);
    hold.timer = setTimeout(repeat, REPEAT_MS[0] + (REPEAT_MS[1] - REPEAT_MS[0]) * k);
  };
  hit.addEventListener("pointerdown", (ev) => {
    if (hold) return;
    ev.preventDefault();
    try { hit.setPointerCapture(ev.pointerId); } catch {}
    part.classList.add("pressed");
    controlHaptic("press");
    hold = {id: ev.pointerId, since: performance.now(), repeating: false, timer: setTimeout(repeat, REPEAT_DELAY_MS)};
  });
  const release = (ev) => {
    if (ev.pointerId !== hold?.id) return;
    clearTimeout(hold.timer);
    part.classList.remove("pressed");
    if (!hold.repeating && ev.type === "pointerup") step(); // a tap tunes as the button comes back up
    hold = null;
  };
  for (const type of ["pointerup", "pointercancel", "lostpointercapture"]) hit.addEventListener(type, release);
}

// The F key acts the moment it goes down: the mini-game is all about timing.
function bindKey(hit, part, action) {
  let id = null;
  hit.addEventListener("pointerdown", (ev) => {
    if (id !== null) return;
    ev.preventDefault();
    try { hit.setPointerCapture(ev.pointerId); } catch {}
    id = ev.pointerId;
    part.classList.add("pressed");
    controlHaptic("press");
    action();
  });
  const release = (ev) => {
    if (ev.pointerId !== id) return;
    id = null;
    part.classList.remove("pressed");
  };
  for (const type of ["pointerup", "pointercancel", "lostpointercapture"]) hit.addEventListener(type, release);
}

export function bindControls() {
  placeKnob(stopOf(control.selector));
  onControlChange((c) => placeKnob(stopOf(c.selector))); // the knob always shows the state
  bindSelector($("btnView"));
  bindTuning($("btnTuneDown"), $("partTuneDown"), -1);
  bindTuning($("btnTuneUp"), $("partTuneUp"), 1);
  bindKey($("btnKeyF"), $("partKeyF"), confirmFineTune);
}
