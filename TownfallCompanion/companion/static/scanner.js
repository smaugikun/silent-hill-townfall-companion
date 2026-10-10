// The CRTV the phone shows, and who is in charge of it.
//
// The phone's dial and the in-game CRTV's are one: tuned on the phone, the game's CRTV is tuned too, up
// or down, so it comes up where the phone left it; tuned in the game while it is up, the phone's needle
// follows. The AV OUT / VIEW selector decides the rest. VIEW: the CRTV is on the phone. While the in-game
// CRTV is up the phone shows its screen (the stream), mini-games included, and the D-pad presses the game's
// fine-tune keys. In VIEW the phone switches the game's CRTV on and keeps it on (put away with the radio button or by
// the game, it comes on again; not over a cutscene or the pause menu), also while the phone is put down. With "In
// VIEW, show the game's CRTV on the monitor" (raises) the character raises it with his animation, as the radio button
// does; without, it is switched on silently and nothing of it shows on the monitor, in the mini-game neither.
// "Turning the phone turns the character" (steering) is apart from that: in VIEW, with the phone in hand (pickup.js),
// turning and tilting it turns the player and looks up and down (app.js).
// AV OUT: the PC has the CRTV, raised and lowered with the radio button, picture and sound; the phone's screen stays
// dark.
// The phone says its mode to the game (sayMode) and the mod does the rest, as it alone knows what the phone switched
// on: it puts that away once the phone is out of VIEW, or once the monitor is to show it the other way (the phone
// then switches it on again, the new way), and never a CRTV the player raised.
import { clamp01, loadFlag, postControl, saveSetting } from "./util.js";

const SEND_MS = 100;         // tuning goes to the bridge at most this often
// After tuning, the phone's dial wins over the game's until the game reports it: the telemetry comes
// a few hundred ms behind. A game that hasn't taken it after HOLD_MS has refused it: the dial follows the game.
const HOLD_MS = 2000;
const SAME_DIAL = 0.002;     // the telemetry has the dial to 3 decimals
const RAISE_GRACE_MS = 2000; // how long the game gets to show a CRTV the phone switched on
// The CRTV put away in the game (letting go of the radio button, which also cuts a waypoint off mid-line) goes up
// again after REHOLD_MS; REHOLD_TRIES times in a row at most, in case the game keeps putting it away (an
// interaction, something scripted). Counted afresh once it has stayed up STEADY_MS.
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
  raises: loadFlag("tfc.raise", false),  // "In VIEW, show the game's CRTV on the monitor" (its raise animation)
  miniGameShown: loadFlag("tfc.miniGameShown", false), // "In VIEW, show the game's CRTV during fine tuning"
  dial: 0,             // 0..1, the phone's and the game's; the game reports 0 while lowered, the needle stays put
  tunedAt: -Infinity,  // performance.now() of the last tuning step
  putDown: false,      // the phone lies on a table or stands on a stand: it doesn't turn the player
};

let gameCrtv = {};
let inCutscene = false;
let raisedAt = -Infinity;   // when the phone last asked the game to switch its CRTV on
let tunedWhileDown = false; // the phone tuned the game's CRTV while it was down
let upSince = null, downSince = null, reholds = 0; // the in-game CRTV's state and how long it has been in it
const listeners = [];

export function onControlChange(fn) { listeners.push(fn); }
const changed = () => { for (const fn of listeners) fn(control); };

const inView = () => control.selector === "VIEW";

// The phone switches the in-game CRTV on and keeps it on in VIEW, while the page is in sight (a hidden page, the
// screen off or another app, shows nothing): its voices, sound and mini-game work. Whether the character shows it
// on the monitor, with the raise animation, is the setting "In VIEW, show the game's CRTV".
const holdsCrtv = () => inView() && globalThis.document?.hidden !== true;

// The phone steers the player: VIEW, with "The phone steers your character", the phone in hand.
export const steers = () => inView() && control.steering && !control.putDown;

// Whether the phone shows anything: only in VIEW (in AV OUT the PC has the picture).
export const phoneShows = () => inView();

// Every telemetry update, from app.js; `paused`: the game is (its world clock stands still), and the
// CRTV isn't switched on again meanwhile.
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
      sendCrtv(false, true);
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
    const raising = now - raisedAt <= RAISE_GRACE_MS;
    if (holdsCrtv() && !paused && !raising && !inCutscene && now - downSince > REHOLD_MS && reholds < REHOLD_TRIES) {
      reholds++;
      raise();
    }
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
// Throttled, but the last position always goes out, so the game ends where the needle stopped. `switchOn`: the
// in-game CRTV is switched on too, with the character's animation (the monitor shows it) or silently, as the
// setting says; otherwise only its dial moves.
function sendCrtv(switchOn, immediately) {
  clearTimeout(pendingSend);
  const send = () => {
    lastSent = performance.now();
    postControl(switchOn ? {type: "crtv", active: true, animate: control.raises, frequency: control.dial}
      : {type: "crtv", frequency: control.dial});
  };
  const wait = SEND_MS - (performance.now() - lastSent);
  if (immediately || wait <= 0) send();
  else pendingSend = setTimeout(send, wait);
}

function raise() {
  raisedAt = performance.now();
  sendCrtv(true, true);
}

// The phone's mode, for the game: whether the CRTV is the phone's (VIEW with the page in sight), and whether in VIEW
// the monitor shows it, and in the mini-game. Said before any raise, at once when it changes, and again every couple
// of seconds (app.js): the mod takes a phone that stops saying it for gone, and the game is as it was without the
// phone. `leaving`: the page goes away, and hands the game back.
export function sayMode(leaving = false) {
  postControl({type: "mode", selector: holdsCrtv() && !leaving ? "VIEW" : "AV_OUT", onMonitor: control.raises,
               miniGameShown: control.miniGameShown});
}

// Switching on (VIEW, in sight): unless it is up already. Not over a cutscene; the rehold switches it on once that
// is over.
function hold() {
  if (!holdsCrtv() || gameCrtv.active) return;
  reholds = 0;
  if (!inCutscene) raise();
}

export function setSelector(position) {
  if (position === control.selector) return;
  control.selector = position;
  sayMode();
  hold();
  changed();
}

// The page hidden or in sight again (app.js): hidden, the game hears AV OUT; in sight, the CRTV comes on again.
export function onVisibilityChange() {
  sayMode();
  hold();
}

// Put down, on a table or a stand, or picked up again (pickup.js): only the turning depends on it.
export function setPutDown(down) {
  if (down === control.putDown) return;
  control.putDown = down;
  changed();
}

// The mod puts away a CRTV the phone switched on the other way, so the monitor starts or stops showing it now, not at
// the next raise; the rehold switches it on again the new way once the game reports it down.
export function setRaises(on) {
  if (on === control.raises) return;
  control.raises = on;
  saveSetting("tfc.raise", on);
  reholds = 0;
  sayMode();
  changed();
}

// Whether the monitor shows the CRTV and the hands holding it while the mini-game runs in VIEW: the mod shows or hides
// them at once.
export function setMiniGameShown(on) {
  if (on === control.miniGameShown) return;
  control.miniGameShown = on;
  saveSetting("tfc.miniGameShown", on);
  sayMode();
  changed();
}

export function setSteering(on) {
  if (on === control.steering) return;
  control.steering = on;
  saveSetting("tfc.sync", on);
  changed();
}

// The D-pad's centre: the press in the in-game fine-tune mini-game; held, it stores a signal in the advanced
// mini-game, so letting go is sent too. Every press goes to the game, which decides by its own CRTV (and logs it):
// the phone's idea of it may be a moment behind, as right after the game put the CRTV away and up again.
let centreSent = false;
// While the centre is held (storing a signal) the phone's pose doesn't move the game's image (app.js).
export const centreHeld = () => centreSent;
export function confirmFineTune() {
  centreSent = true;
  postControl({type: "confirm"});
}

export function releaseFineTune() {
  if (centreSent) postControl({type: "release"});
  centreSent = false;
}

// The D-pad's arrows: the mini-game's arrow keys, sent as the centre is. Outside the mini-game, left and right are the
// game's quick tune, which jumps its dial to the next frequency: the needle then follows the game's dial at once, not
// the phone's own from a tuning a moment before (HOLD_MS), and that tuning doesn't go out after and pull it back.
export function pressFineTune(direction) {
  if (!["up", "down", "left", "right"].includes(direction)) return;
  if (direction === "left" || direction === "right") {
    clearTimeout(pendingSend);
    control.tunedAt = -Infinity;
  }
  postControl({type: "fine_tune", direction});
}

// One tuning step from the phone's TUNING buttons, in either selector position: the game's dial moves
// with the phone's, up or down. In VIEW a CRTV that is down comes on with it, not over a cutscene. Returns whether
// the dial passed a notch.
export function tune(delta) {
  const dial = Math.round(clamp01(control.dial + delta) * 1e4) / 1e4; // no float drift from repeated steps
  if (dial === control.dial) return false; // at the end of the dial
  const notch = Math.floor(dial / NOTCH) !== Math.floor(control.dial / NOTCH);
  control.dial = dial;
  control.tunedAt = performance.now();
  const raiseIt = holdsCrtv() && !gameCrtv.active && !inCutscene;
  if (!gameCrtv.active && !raiseIt) tunedWhileDown = true;
  if (raiseIt && control.tunedAt - raisedAt > RAISE_GRACE_MS) raisedAt = control.tunedAt;
  sendCrtv(raiseIt);
  return notch;
}
