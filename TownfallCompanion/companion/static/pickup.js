// Whether the phone is put down (on a table, a lap, an armrest, a stand) or in hand.
//
// Gravity tells how it lies: laid flat (under FLAT_DEG from level, face up or down) for SETTLE_MS puts it
// down, raised (more than RAISED_DEG) for CONFIRM_MS picks it up, and in between it stays as it was. A
// stand or a holder keeps it raised, so how still it is tells too: a hand always moves it a little, a
// stand doesn't. Steering rests only after REST_MS of stillness. Stand detection is separate and faster:
// a raised phone that settles very still is considered on a stand after a short confirmation. A placement
// bump (when linear acceleration is available) makes that confirmation faster; this avoids tying the stand
// action to the four-second steering timer. Moving/turning it again takes it off the stand.
// Without a gyroscope, only how it lies tells.
//
// Lying flat or resting for 4 s stops character steering (scanner.js steers()); movement wakes steering.
// With Auto pickup, raising/picking up goes to VIEW, lying flat goes to AV OUT, and a detected stand goes
// to whichever position the Stand setting says (VIEW or AV OUT).
import { loadFlag, saveSetting } from "./util.js";

const RAISED_DEG = 30;  // held up to look at, or pointed ahead like the CRTV
const FLAT_DEG = 15;    // lying on a table, a lap, an armrest
const CONFIRM_MS = 250;
const SETTLE_MS = 600;
const GRAVITY_MS = 150; // gravity is the acceleration smoothed over about this long: a knock doesn't tilt it
const TURNING_MS = 50;  // the turning speed, over about this long: short enough to show a still moment
const STILL_DEG_S = 0.5;
const WAKE_DEG_S = 1.0; // once resting, real motion wakes steering immediately
const REST_MS = 4000; // four seconds truly still: then steering rests until the phone moves again
const MOVED_DEG = 6;
const STIR_MS = 2000;

// A stand is a different question from "resting": detect it quickly enough to move the selector.
// There is no browser "phone is in a stand" sensor, so this is deliberately a short settle heuristic.
const STAND_STILL_DEG_S = 0.25;
const STAND_FAST_MS = 350;      // after the small bump typical of putting the phone into a holder
const STAND_FALLBACK_MS = 1200; // gentle placement / browsers without linear acceleration
const STAND_BUMP_MS2 = 0.8;
const STAND_ARM_MS = 1200;
const STAND_RELEASE_DEG_S = 4;
const STAND_RELEASE_MS = 250;

export const pickup = {
  auto: loadFlag("tfc.autoPickup", false), // Auto pickup: the selector follows
  down: null,     // laid flat; null until the sensor has told
  resting: false, // standing still, however it is tilted
  tilt: null,     // degrees from lying flat, once the sensor has read
  turning: null,  // how fast it turns (degrees a second), once the gyroscope has read
  onStand: false, // raised and settled very still
  standAvOut: loadFlag("tfc.standAvOut", false), // Auto pickup: set on a stand, it goes to AV OUT
};

const listeners = [];
// fn(held, first, stand): raised or laid flat, or (stand) taken off or set on a stand; `first` is the first
// reading, which only tells how it lies.
export function onPickup(fn) { listeners.push(fn); }

// Where Auto pickup puts the selector for what onPickup tells ("VIEW" or "AV_OUT"). Picked up, or taken off a
// stand, is VIEW; set on a stand goes where the Stand setting says; laid flat is AV OUT. (Taking it off a stand
// followed the Stand setting too, so with "AV OUT" chosen it never went back to VIEW.)
export function pickupPosition(held, stand, standAvOut) {
  if (held) return "VIEW";
  return stand && !standAvOut ? "VIEW" : "AV_OUT";
}

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

export function wakeSteering() {
  // Steering is driven by deviceorientation, while the rest detector normally uses devicemotion.
  // Some Android devices throttle rotationRate after being still, so either sensor may wake it.
  if (!pickup.resting) return false;
  pickup.resting = false;
  quietSince = null;
  quietAt = performance.now();
  restingGravity = null;
  turnedSinceRest = [0, 0, 0];
  tellPutDown();
  return true;
}

let gravity = null, lastAt = 0;
let turningSince = null; // since when it has lain (or been held) the other way
let quietSince = null;   // since when it has turned slower than STILL_DEG_S
let quietAt = 0;         // when it last did
let restingGravity = null;
let turnedSinceRest = [0, 0, 0]; // degrees turned about each axis since it came to rest (signed: noise evens out)

let standQuietSince = null;
let standArmedUntil = 0;
let standGravity = null;
let standMovedSince = null;

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
  if (flat && pickup.onStand) {
    pickup.onStand = false;
    standQuietSince = standGravity = standMovedSince = null;
  }
  tellPutDown();
  for (const fn of listeners) fn(!pickup.down, first, false);
}

const degreesBetween = (a, b) =>
  Math.acos(Math.min(1, a.reduce((sum, v, i) => sum + v * b[i], 0) / (Math.hypot(...a) * Math.hypot(...b)))) * 180 / Math.PI;

function rest(rate, k, ms, now, acceleration) {
  if (rate?.alpha == null) return;
  const rates = [rate.alpha, rate.beta ?? 0, rate.gamma ?? 0];
  const speed = Math.hypot(...rates);
  pickup.turning = pickup.turning == null ? speed : pickup.turning + k * (speed - pickup.turning);

  // Fast stand detection, independent from the four-second steering-rest timer.
  const raised = pickup.down === false;
  const bump = acceleration?.x == null ? 0
    : Math.hypot(acceleration.x, acceleration.y ?? 0, acceleration.z ?? 0);
  if (raised && bump >= STAND_BUMP_MS2) standArmedUntil = now + STAND_ARM_MS;
  const standQuiet = raised && pickup.turning < STAND_STILL_DEG_S;

  if (!pickup.onStand) {
    standQuietSince = standQuiet ? standQuietSince ?? now : null;
    const settle = now <= standArmedUntil ? STAND_FAST_MS : STAND_FALLBACK_MS;
    if (standQuietSince != null && now - standQuietSince >= settle) {
      pickup.onStand = true;
      standGravity = gravity;
      standMovedSince = null;
      for (const fn of listeners) fn(false, false, true);
    }
  } else {
    const moved = !raised || pickup.turning > STAND_RELEASE_DEG_S
      || (standGravity && degreesBetween(gravity, standGravity) > MOVED_DEG);
    standMovedSince = moved ? standMovedSince ?? now : null;
    if (standMovedSince != null && now - standMovedSince >= STAND_RELEASE_MS) {
      pickup.onStand = false;
      standQuietSince = null;
      standGravity = null;
      standMovedSince = null;
      for (const fn of listeners) fn(true, false, true);
    }
  }

  // Character steering has a deliberately longer rest timer than stand detection.
  const quiet = pickup.turning < STILL_DEG_S;
  quietSince = quiet ? quietSince ?? now : null;
  if (quiet) quietAt = now;

  if (!pickup.resting) {
    if (quietSince != null && now - quietSince >= REST_MS) {
      pickup.resting = true;
      restingGravity = gravity;
      turnedSinceRest = [0, 0, 0];
      tellPutDown();
    }
    return;
  }

  if (pickup.turning >= WAKE_DEG_S) {
    wakeSteering();
    return;
  }

  const seconds = Math.min(ms, 100) / 1000;
  turnedSinceRest = turnedSinceRest.map((v, i) => v + rates[i] * seconds);
  const movedFromRest = degreesBetween(gravity, restingGravity) > MOVED_DEG
    || Math.hypot(...turnedSinceRest) > MOVED_DEG;
  if (!movedFromRest && now - quietAt < STIR_MS) return;

  pickup.resting = false;
  quietSince = null; // resting again takes a fresh REST_MS
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
  rest(e.rotationRate, 1 - Math.exp(-since / TURNING_MS), since, now, e.acceleration);
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
