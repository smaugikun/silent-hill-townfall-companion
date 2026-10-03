// The CRTV the phone shows, and who is in charge of it.
//
// The phone's dial and the in-game CRTV's are one: tuned on the phone, the game's CRTV is tuned too, up
// or down, so it comes up where the phone left it; tuned in the game while it is up, the phone's needle
// follows. The AV OUT / VIEW selector decides the rest. VIEW: the CRTV is on the phone. While the in-game
// CRTV is up the phone shows it, the fine-tune mini-game too, and F presses the fine-tune key; while it is
// down the phone scans by itself from the telemetry, on the same dial. With "In VIEW, switch on the game's
// CRTV" (raises) the phone switches it on and keeps it on (put away with the controller, it comes on again;
// not over a cutscene or the pause menu), also while the phone is put down. Switched on by the mod it
// works as if raised (the signals talk, the subtitles show, the mini-game runs) but the character doesn't
// hold it up on the monitor. "Turning the phone turns the
// character" (steering) is apart from that: in VIEW, with the phone in hand (pickup.js), turning it turns
// the player (app.js).
// AV OUT: the PC has the CRTV, raised and lowered with the controller; the phone shows its picture or
// stays dark (avOut). Going to AV OUT lowers the in-game CRTV if the phone raised it.
import { clamp01, loadFlag, loadSetting, postControl, saveSetting } from "./util.js";

const SEND_MS = 100;         // tuning goes to the bridge at most this often
// After tuning, the phone's dial wins over the game's until the game reports it: the telemetry comes
// a few hundred ms behind. A game that hasn't taken it after HOLD_MS has refused it: the dial follows the game.
const HOLD_MS = 2000;
const SAME_DIAL = 0.002;     // the telemetry has the dial to 3 decimals
const RAISE_GRACE_MS = 2000; // how long the game gets to show a CRTV the phone raised
// Raising: the CRTV put away in the game (letting go of the controller's trigger, which also cuts a
// waypoint off mid-line) goes up again after REHOLD_MS; REHOLD_TRIES times in a row at most, in case the
// game keeps putting it away (an interaction, something scripted). Counted afresh once it has stayed up
// STEADY_MS.
const REHOLD_MS = 600;
const REHOLD_TRIES = 3;
const STEADY_MS = 3000;
const DEFAULT_TOLERANCE = 0.02; // until the game's per-enemy values arrive
// A notch of the dial: felt while tuning (controls.js), clicked as the needle passes (sound.js).
export const NOTCH = 0.02;

// The phone's operating state, kept only here. The selector starts at AV OUT on every load, so
// opening the page never takes over the game by itself.
export const control = {
  selector: "AV_OUT",  // "AV_OUT" | "VIEW"
  steering: loadFlag("tfc.sync", false), // "The phone steers your character"
  raises: loadFlag("tfc.raise", false),  // "In VIEW, switch on the game's CRTV"
  avOut: loadSetting("tfc.avOut", "picture") === "off" ? "off" : "picture", // in AV OUT: dark, or the picture
  dial: 0,             // 0..1, the phone's and the game's; the game reports 0 while lowered, the needle stays put
  tunedAt: -Infinity,  // performance.now() of the last tuning step
  raisedAt: null,      // when the phone raised the game's CRTV, which it then has to lower again
  putDown: false,      // the phone lies on a table or stands on a stand: it doesn't turn the player
};

let gameCrtv = {};
let inCutscene = false;
let tunedWhileDown = false; // the phone tuned the game's CRTV while it was down
let upSince = null, downSince = null, reholds = 0; // the in-game CRTV's state and how long it has been in it
const listeners = [];

export function onControlChange(fn) { listeners.push(fn); }
const changed = () => { for (const fn of listeners) fn(control); };

const inView = () => control.selector === "VIEW";

// The phone switches the in-game CRTV on and keeps it on: VIEW with "In VIEW, switch on the game's CRTV".
const holdsCrtv = () => inView() && control.raises;

// The phone steers the player: VIEW, with "The phone steers your character", the phone in hand.
export const steers = () => inView() && control.steering && !control.putDown;

// Whether the phone shows anything: always in VIEW, in AV OUT unless set to stay dark.
export const phoneShows = () => inView() || control.avOut !== "off";

// Every telemetry update, from app.js; `paused`: the game is (its world clock stands still), and the
// CRTV isn't raised again meanwhile.
export function onTelemetry(state, paused = false) {
  const wasUp = Boolean(gameCrtv.active);
  gameCrtv = state.crtv || {};
  inCutscene = Boolean(state.cutscene?.sequence);
  const now = performance.now();
  if (gameCrtv.active) {
    // Just raised, after the phone tuned it while it was down: in case the game set its own dial on
    // raising, the phone's goes to it again.
    if (!wasUp && tunedWhileDown) {
      tunedWhileDown = false;
      control.tunedAt = now;
      sendCrtv(null, true);
    }
    const frequency = clamp01(gameCrtv.frequency);
    const waiting = now - control.tunedAt < HOLD_MS && Math.abs(frequency - control.dial) > SAME_DIAL;
    if (!waiting) control.dial = frequency;
  }
  if (gameCrtv.active) {
    downSince = null;
    upSince ??= now;
    if (now - upSince > STEADY_MS) reholds = 0;
  } else {
    upSince = null;
    downSince ??= now;
    const raising = control.raisedAt != null && now - control.raisedAt <= RAISE_GRACE_MS;
    if (holdsCrtv() && !paused && !raising && !inCutscene && now - downSince > REHOLD_MS && reholds < REHOLD_TRIES) {
      reholds++;
      raise();
    }
  }
  // Lowered in the game (by the player, or with the level) after the phone raised it: nothing to release.
  if (!gameCrtv.active && control.raisedAt != null && now - control.raisedAt > RAISE_GRACE_MS) {
    control.raisedAt = null;
  }
}

// The phone's own reading of a monster or a waypoint signal: its reach at its distance
// (rangeSignal, from the mod) and whether the dial is near enough its channel to bring it in. As in the
// game: anywhere within its outer tolerance (it comes in distorted), not only the inner one (clear).
function phoneReading(source, dial) {
  const signal = clamp01(source.rangeSignal);
  const tolerance = Math.max(source.tolerance ?? DEFAULT_TOLERANCE, source.toleranceOuter ?? 0);
  // + 1e-9: the dial's 0.005 steps land on the edge, and 0.62 - 0.6 is a hair over 0.02 in floating point.
  const tuned = signal > 0 && source.channel != null && Math.abs(dial - source.channel) <= tolerance + 1e-9;
  return {...source, signal, detected: signal > 0, tuned};
}

const silent = (source) => ({...source, signal: 0, detected: false, tuned: false});

// {source: "game" | "phone" | "off", active, dial, enemies, signals}, each monster and waypoint
// signal with signal/detected/tuned as this CRTV sees it. "game": the in-game CRTV is up and the phone
// shows it. "phone": VIEW while the in-game CRTV is down, the phone scanning by itself. "off" is standby:
// AV OUT with the in-game CRTV down, or set to stay dark. Not `live` (no game coming through): off,
// whatever the selector.
export function scannerView(state, live) {
  const dial = control.dial;
  if (!live) return {source: "off", active: false, dial, enemies: [], signals: []};
  const enemies = (state.enemies || []).filter(e => e.alive !== false);
  const signals = state.signals || [];
  if (phoneShows() && gameCrtv.active) return {source: "game", active: true, dial, enemies, signals};
  if (inView()) {
    const read = (s) => phoneReading(s, dial);
    return {source: "phone", active: true, dial, enemies: enemies.map(read), signals: signals.map(read)};
  }
  return {source: "off", active: false, dial, enemies: enemies.map(silent), signals: signals.map(silent)};
}

let lastSent = 0;
let pendingSend = null;
// Throttled, but the last position always goes out, so the game ends where the needle stopped. active:
// raise (true) or lower (false) the in-game CRTV; null leaves it as it is and only moves its dial.
function sendCrtv(active, immediately) {
  clearTimeout(pendingSend);
  const send = () => {
    lastSent = performance.now();
    postControl(active == null ? {type: "crtv", frequency: control.dial} : {type: "crtv", active, frequency: control.dial});
  };
  const wait = SEND_MS - (performance.now() - lastSent);
  if (immediately || wait <= 0) send();
  else pendingSend = setTimeout(send, wait);
}

function raise() {
  control.raisedAt = performance.now();
  sendCrtv(true, true);
}

// Raising (VIEW with the setting): raise it unless it is up already. Not over a cutscene; the rehold
// raises it once that is over.
function hold() {
  if (!holdsCrtv() || gameCrtv.active) return;
  reholds = 0;
  if (!inCutscene) raise();
}

// Hands the game back: lowers the CRTV if the phone raised it. Also when the page goes away.
export function letGo() {
  clearTimeout(pendingSend);
  if (control.raisedAt == null) return;
  control.raisedAt = null;
  sendCrtv(false, true);
}

export function setSelector(position) {
  if (position === control.selector) return;
  if (position === "AV_OUT") letGo();
  control.selector = position;
  hold();
  changed();
}

// Put down, on a table or a stand, or picked up again (pickup.js): only the turning depends on it.
export function setPutDown(down) {
  if (down === control.putDown) return;
  control.putDown = down;
  changed();
}

export function setAvOut(value) {
  control.avOut = value;
  saveSetting("tfc.avOut", value);
  changed();
}

export function setRaises(on) {
  if (on === control.raises) return;
  if (!on) letGo();
  control.raises = on;
  saveSetting("tfc.raise", on);
  hold();
  changed();
}

export function setSteering(on) {
  if (on === control.steering) return;
  control.steering = on;
  saveSetting("tfc.sync", on);
  changed();
}

// The F key: the press in the in-game fine-tune mini-game, which runs only while the CRTV is up.
export function confirmFineTune() {
  if (gameCrtv.active) postControl({type: "confirm"});
}

// One tuning step from the phone's TUNING buttons, in either selector position: the game's dial moves
// with the phone's, up or down. Raising (VIEW with the setting), a CRTV that is down comes up with it, not
// over a cutscene. Returns whether the dial passed a notch.
export function tune(delta) {
  const dial = Math.round(clamp01(control.dial + delta) * 1e4) / 1e4; // no float drift from repeated steps
  if (dial === control.dial) return false; // at the end of the dial
  const notch = Math.floor(dial / NOTCH) !== Math.floor(control.dial / NOTCH);
  control.dial = dial;
  control.tunedAt = performance.now();
  const raiseIt = holdsCrtv() && !gameCrtv.active && !inCutscene;
  if (!gameCrtv.active && !raiseIt) tunedWhileDown = true;
  if (raiseIt && control.raisedAt == null) control.raisedAt = control.tunedAt;
  sendCrtv(raiseIt ? true : null);
  return notch;
}
