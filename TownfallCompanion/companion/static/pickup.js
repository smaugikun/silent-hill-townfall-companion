// Whether the phone is put down (on a table, a lap, an armrest, a stand) or in hand.
//
// Gravity tells how it lies: laid flat (under FLAT_DEG from level, face up or down) for SETTLE_MS puts it
// down, raised (more than RAISED_DEG) for CONFIRM_MS picks it up, and in between it stays as it was. A
// stand or a holder keeps it raised, so how still it is tells too: a hand always moves it a little, a
// stand doesn't. Turning less than STILL_DEG_S for REST_MS, it rests; tilted or turned more than MOVED_DEG
// from how it rested (turning it about the vertical leaves the tilt as it was), or moving STIR_MS without a
// still moment, it is in hand again (taps on the screen shake a stand, but it is still between them).
// Without a gyroscope, only how it lies tells.
//
// Lying flat or resting for 4 s, the phone doesn't turn the character (scanner.js steers()). Moving/picking
// it up clears resting and steering resumes. Resting STAND_MS longer, not
// flat, it stands on a stand (a hand is never that still for so long). With Auto pickup (Settings) raising
// it and laying it flat slide the selector, as the character raises and lowers the CRTV; taking it off a
// stand slides it to VIEW, and setting it on one to AV OUT only if chosen (standAvOut). Only a change
// does, so lying flat when the selector was slid by hand leaves it where it is.
import { loadFlag, saveSetting } from "./util.js";

const RAISED_DEG = 30;  // held up to look at, or pointed ahead like the CRTV
const FLAT_DEG = 15;    // lying on a table, a lap, an armrest
const CONFIRM_MS = 250;
const SETTLE_MS = 600;
const GRAVITY_MS = 150; // gravity is the acceleration smoothed over about this long: a knock doesn't tilt it
const TURNING_MS = 50;  // the turning speed, over about this long: short enough to show a still moment
const STILL_DEG_S = 0.5;
const REST_MS = 4000; // four seconds truly still: then steering rests until the phone moves again
const MOVED_DEG = 6;
const STIR_MS = 2000;
const STAND_MS = 1000; // another second after resting (about 5 s still in all): consider it on a stand

export const pickup = {
  auto: loadFlag("tfc.autoPickup", false), // Auto pickup: the selector follows
  down: null,     // laid flat; null until the sensor has told
  resting: false, // standing still, however it is tilted
  tilt: null,     // degrees from lying flat, once the sensor has read
  turning: null,  // how fast it turns (degrees a second), once the gyroscope has read
  onStand: false, // resting STAND_MS, not flat
  standAvOut: loadFlag("tfc.standAvOut", false), // Auto pickup: set on a stand, it goes to AV OUT
};

const listeners = [];
// fn(held, first, stand): raised or laid flat, or (stand) taken off or set on a stand; `first` is the first
// reading, which only tells how it lies.
export function onPickup(fn) { listeners.push(fn); }

const putDownListeners = [];
// fn(down): put down (laid flat, or resting) or taken in hand again.
export function onPutDown(fn) { putDownListeners.push(fn); }

let putDown = false;
function tellPutDown() {
  const now = pickup.down === true || pickup.resting;
  if (now === putDown) return;
  putDown = now;
  for (const fn of putDownListeners) fn(putDown);
}

let gravity = null, lastAt = 0;
let turningSince = null; // since when it has lain (or been held) the other way
let quietSince = null;   // since when it has turned slower than STILL_DEG_S
let quietAt = 0;         // when it last did
let restingGravity = null;
let turnedSinceRest = [0, 0, 0]; // degrees turned about each axis since it came to rest (signed: noise evens out)
let restingSince = null;

function step(now) {
  const flat = pickup.tilt < FLAT_DEG, raised = pickup.tilt > RAISED_DEG;
  const first = pickup.down == null;
  if (first && !flat && !raised) return;
  if (!first) {
    if (!(pickup.down ? raised : flat)) { turningSince = null; return; }
    turningSince ??= now;
    if (now - turningSince < (pickup.down ? CONFIRM_MS : SETTLE_MS)) return;
    turningSince = null;
  }
  pickup.down = flat;
  tellPutDown();
  for (const fn of listeners) fn(!pickup.down, first, false);
}

const degreesBetween = (a, b) =>
  Math.acos(Math.min(1, a.reduce((sum, v, i) => sum + v * b[i], 0) / (Math.hypot(...a) * Math.hypot(...b)))) * 180 / Math.PI;

function rest(rate, k, ms, now) {
  if (rate?.alpha == null) return;
  const speed = Math.hypot(rate.alpha, rate.beta ?? 0, rate.gamma ?? 0);
  pickup.turning = pickup.turning == null ? speed : pickup.turning + k * (speed - pickup.turning);
  const quiet = pickup.turning < STILL_DEG_S;
  quietSince = quiet ? quietSince ?? now : null;
  if (quiet) quietAt = now;
  if (!pickup.resting) {
    if (quietSince == null || now - quietSince < REST_MS) return;
    pickup.resting = true;
    restingSince = now;
    restingGravity = gravity;
    turnedSinceRest = [0, 0, 0];
  } else {
    const seconds = Math.min(ms, 100) / 1000;
    turnedSinceRest = turnedSinceRest.map((v, i) => v + [rate.alpha, rate.beta ?? 0, rate.gamma ?? 0][i] * seconds);
    const moved = degreesBetween(gravity, restingGravity) > MOVED_DEG || Math.hypot(...turnedSinceRest) > MOVED_DEG;
    if (!moved && now - quietAt < STIR_MS) {
      // Set on a stand (lying flat puts it down by its tilt instead).
      if (quiet && !pickup.onStand && pickup.down === false && now - restingSince >= STAND_MS) {
        pickup.onStand = true;
        for (const fn of listeners) fn(false, false, true);
      }
      return;
    }
    pickup.resting = false;
    quietSince = null; // resting again takes REST_MS of stillness from here
    tellPutDown();
    if (pickup.onStand) {
      pickup.onStand = false;
      for (const fn of listeners) fn(true, false, true);
    }
    return;
  }
  tellPutDown();
}

function motionHandler(e) {
  const g = e.accelerationIncludingGravity;
  if (g?.x == null) return;
  const now = performance.now();
  const since = gravity ? now - lastAt : Infinity;
  lastAt = now;
  const k = 1 - Math.exp(-since / GRAVITY_MS);
  gravity = [g.x, g.y ?? 0, g.z ?? 0].map((v, i) => gravity ? gravity[i] + k * (v - gravity[i]) : v);
  const size = Math.hypot(...gravity);
  if (size < 2) return; // falling, or no real reading
  pickup.tilt = Math.acos(Math.min(1, Math.abs(gravity[2]) / size)) * 180 / Math.PI;
  step(now);
  rest(e.rotationRate, 1 - Math.exp(-since / TURNING_MS), since, now);
}

// Reads the sensor from now on. iOS asks first, and only from a tap; elsewhere it starts on load, so
// the phone knows it lies flat before the selector is first slid to VIEW.
let listening = false;
export function listenMotion() {
  if (listening || typeof DeviceMotionEvent === "undefined") return;
  const listen = () => {
    if (listening) return;
    listening = true;
    window.addEventListener("devicemotion", motionHandler);
  };
  if (typeof DeviceMotionEvent.requestPermission === "function") {
    DeviceMotionEvent.requestPermission().then((r) => { if (r === "granted") listen(); }).catch(() => {});
  } else {
    listen();
  }
}

export function setStandAvOut(on) {
  pickup.standAvOut = on;
  saveSetting("tfc.standAvOut", on);
}

export function setAutoPickup(on) {
  pickup.auto = on;
  saveSetting("tfc.autoPickup", on);
  listenMotion(); // ticking it is a tap
}
