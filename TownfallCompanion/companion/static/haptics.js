// Vibration, in two groups that can be switched off separately: the CRTV's controls, and the
// scanner's signal. A web page can only set how long the phone vibrates, not how hard, so the
// intensity setting lengthens or shortens every pulse. Many phones' motors don't move at all for
// less than about 25 ms (7-16 ms pulses aren't felt), so none is shorter.
import { loadFlag, loadSetting, saveSetting } from "./util.js";

// Vibrate/pause/vibrate... in ms, at medium intensity.
const PATTERNS = {
  press: [30],            // a TUNING button or the F key goes down
  notch: [25],            // the dial passes a notch while tuning
  unlock: [35],           // the selector comes out of its position
  detent: [25],           // the selector passes the middle of the slot
  end: [40],              // the selector reaches the end of the slot
  lock: [70],             // the selector clicks into AV OUT or VIEW, a signal is found
  recenter: [40, 90, 40],
  reconnect: [30],
  test: [200],
  newSignal: [90, 110, 90, 110, 160], // a new waypoint frequency (Signal group)
};
const INTENSITY = {low: 0.7, medium: 1, high: 1.5};
const SHORTEST_MS = 25;

// Whether this browser can vibrate at all (Chrome on Android can; iPhones can't).
export const canVibrate = typeof navigator.vibrate === "function";

export const haptics = {
  control: loadFlag("tfc.hapticsControl", true),
  signal: loadFlag("tfc.hapticsSignal", true),
  intensity: loadSetting("tfc.hapticsIntensity", "medium"),
};

export function setHaptics(key, value) {
  haptics[key] = value;
  saveSetting({control: "tfc.hapticsControl", signal: "tfc.hapticsSignal", intensity: "tfc.hapticsIntensity"}[key], value);
}

function vibrate(pattern) {
  const k = INTENSITY[haptics.intensity] ?? 1;
  navigator.vibrate?.(pattern.map((ms, i) => i % 2 ? ms : Math.max(SHORTEST_MS, Math.round(ms * k))));
}

// Every vibrate() cuts off the one still running, so signal pulses wait until a control's or an
// alert's is over.
let busyUntil = 0;

function vibrateWhole(pattern) {
  busyUntil = performance.now() + pattern.reduce((a, b) => a + b, 0) + 60;
  vibrate(pattern);
}

export function controlHaptic(event) {
  if (haptics.control) vibrateWhole(PATTERNS[event]);
}

// One pulse of the scanner's signal, 0..1 strong. False if it didn't go out.
export function signalHaptic(strength) {
  if (!haptics.signal || performance.now() < busyUntil) return false;
  vibrate([30 + 50 * strength]);
  return true;
}

// A new waypoint frequency came up: the in-game CRTV beeps and buzzes for one
// (CRTVNewWaypointSignalBeep / ...Vibrate), and the phone buzzes, down or up, to be picked up.
export function newSignalHaptic() {
  if (haptics.signal) vibrateWhole(PATTERNS.newSignal);
}
