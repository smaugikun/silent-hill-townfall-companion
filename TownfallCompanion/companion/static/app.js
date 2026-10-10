import { $, createGameClock, deviceHeading, devicePitch, norm180, norm360, clamp01, postControl, watchStillTime,
  worldBearing } from "./util.js";
import { SIGNAL_RANGE, drawScreen } from "./screen.js";
import { centreHeld, control, onControlChange, onTelemetry, onVisibilityChange, phoneShows, sayMode, scannerView,
  setMiniGameShown, setPutDown, setRaises, setSelector, setSteering, steers } from "./scanner.js";
import { listenMotion, onPickup, onPutDown, pickup, pickupPosition, setAutoPickup, setStandAvOut, wakeSteering }
  from "./pickup.js";
import { bindControls } from "./controls.js";
import { canVibrate, controlHaptic, haptics, newSignalHaptic, setHaptics, signalHaptic } from "./haptics.js";
import { resumeSound, setSound, sound, soundPlaying, unlockSound, updateSound } from "./sound.js";
import { createCrtvStream } from "./crtv-stream.js";
import { alignmentSettings, createAlignment, setAlignmentSensitivity } from "./alignment.js";

const crtvStream = createCrtvStream();
const alignment = createAlignment({send: postControl});
const screenCanvas = $("screen");
const screenContext = screenCanvas.getContext("2d");

let state = {}; // the bridge's latest, from its first update on; every read copes with what is missing
// When the game read the latest sample, on the phone's clock (ms): from its stamp (state.t), else
// when it arrived. Speech is timed by it.
const gameClock = createGameClock();
let sampledAt = 0;

// Where each enemy is relative to the player, nearest first.
function contacts(enemies) {
  const p = state.player || {x: 0, y: 0};
  return enemies.map(e => {
    const dx = (+e.x || 0) - (+p.x || 0);
    const dy = (+e.y || 0) - (+p.y || 0);
    return {e, dx, dy, dist: Math.hypot(dx, dy)};
  }).sort((a, b) => a.dist - b.dist);
}

// The game paused (its menu): the world's clock (state.world) stands still while the samples keep
// coming. The phone holds everything meanwhile: the screen, sound, turning the player, raising the CRTV.
const worldStill = watchStillTime(250);
let paused = false;
// The samples stopped (quitting from the pause menu, which lets the game run a moment more, or loading):
// the screen and sound hold too, instead of playing on until the bridge calls the game gone after 2 s.
// The mod samples every 100 ms but rewrites an unchanged sample only every 0.5 s (main.lua KEEPALIVE_S),
// as when the player stands still in a mini-game: a gap is only one well past that.
const SAMPLE_GAP_MS = 1500;
let lastSampleT = null, sampleArrivedAt = -Infinity;
let holdMedia = false;     // paused, or no samples coming: set once a frame by render()

// --- The dial ---
//
// Its stations as the in-game CRTV's dial shows them (UCRTVTuningDisplayWidget's needles): monsters,
// waypoints, and waypoints not found yet, each in the game's own colour (sent by tf_crtv.lua;
// style.css has stand-ins until then).

function markerFor(source) {
  const marker = document.createElement("i");
  const kind = !source.kind ? "enemy" : source.found ? "waypoint" : "undiscovered";
  marker.className = `marker ${kind}${source.tuned ? " tuned" : ""}`;
  marker.style.left = `${clamp01(source.channel) * 100}%`;
  marker.style.opacity = String(0.85 + 0.15 * clamp01(source.signal));
  return marker;
}

// Linear RGB (as Unreal's FLinearColor) to the sRGB a page draws in.
const srgb = (v) => Math.round(255 * (v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055));
let coloursKey = "null";
function useNeedleColours(colours) {
  const key = JSON.stringify(colours ?? null);
  if (key === coloursKey) return;
  coloursKey = key;
  const dial = document.querySelector(".dial");
  for (const kind of ["enemy", "waypoint", "undiscovered"]) {
    const c = colours?.[kind];
    if (c) dial.style.setProperty(`--${kind}`, `rgb(${c.map(v => srgb(clamp01(v))).join(",")})`);
    else dial.style.removeProperty(`--${kind}`);
  }
}

let markersKey = "";
// Marks on the dial: monsters the scanner picks up, and, while it is on, every waypoint signal the
// game has active, whether in reach yet or not: those are the important channels.
function updateDial(view) {
  const needle = $("needle");
  needle.style.left = `${view.dial * 100}%`;
  needle.classList.toggle("idle", !view.active);
  const marks = [
    ...view.enemies.filter(e => e.channel != null && (e.detected || e.tuned)),
    ...(view.active ? view.signals.filter(s => s.channel != null) : []),
  ];
  const key = marks.map(m => `${m.channel}:${m.tuned}:${m.found}:${clamp01(m.signal).toFixed(1)}`).join();
  if (key !== markersKey) {
    markersKey = key;
    $("markers").replaceChildren(...marks.map(markerFor));
  }
}

// --- Phone rotation: only in VIEW, relative to a reference ---
//
// Entering VIEW and the ⌖ button pair the phone's heading with straight ahead (the way the character looks), and
// how it is held then with level. From then on the CRTV looks as far from where the character looks now (turned with
// the mouse) as the phone has turned since, and as far up or down as it has tilted (updateLook). Steering, the
// character turns along with the phone instead (followGame keeps the two together). Magnetic north never matters.

const HEADING_SMOOTHING_MS = 60; // the heading follows the sensor this smoothly, against its jitter
let sensorsOn = false;
let sensorState = "off"; // off | waiting | on | denied | none
let sensorHeading = null; // the latest reading
let phoneHeading = null;  // smoothed, once a frame
let phoneMovedAt = 0;     // performance.now() when the phone last turned noticeably
let smoothedAt = 0
let sensorPitch = null, phonePitch = null; // how far up the phone looks: the latest reading, smoothed
let reference = null;    // {phone: straight ahead; level: the pitch held level; world: steering, the yaw paired}
let wantReference = false;
let sensorAt = -Infinity;
let sensorAngles = [], sensorChangedAt = -Infinity; // a sensor that goes on reporting the same angles has stopped

function takeReference() {
  reference = {phone: phoneHeading, level: phonePitch ?? sensorPitch ?? 0, world: Number(state.player?.yaw ?? 0)};
  wantReference = false;
}

function orientationHandler(e) {
  const h = typeof e.alpha === "number" && typeof e.beta === "number" && typeof e.gamma === "number"
    ? deviceHeading(e.alpha, e.beta, e.gamma) : e.webkitCompassHeading;
  if (typeof h !== "number" || !Number.isFinite(h)) return;

  const now = performance.now();
  sensorAt = now;
  if (e.alpha !== sensorAngles[0] || e.beta !== sensorAngles[1] || e.gamma !== sensorAngles[2]) sensorChangedAt = now;
  sensorAngles = [e.alpha, e.beta, e.gamma];
  let moved = 0;
  if (typeof e.beta === "number" && typeof e.gamma === "number") {
    const pitch = devicePitch(e.beta, e.gamma);
    if (sensorPitch != null) moved = Math.max(moved, Math.abs(pitch - sensorPitch));
    sensorPitch = pitch;
  }
  if (sensorHeading != null) moved = Math.max(moved, Math.abs(norm180(h - sensorHeading)));
  if (moved > 0.5) phoneMovedAt = now;
  if (moved >= 1.0) wakeSteering();

  sensorHeading = h;
  if (phoneHeading == null) phoneHeading = h;
  sensorState = "on";
  if (wantReference) takeReference();
}

function smoothHeading(now) {
  if (sensorHeading == null || phoneHeading == null) return;
  const k = 1 - Math.exp(-Math.max(0, now - smoothedAt) / HEADING_SMOOTHING_MS);
  smoothedAt = now;
  phoneHeading = norm360(phoneHeading + k * norm180(sensorHeading - phoneHeading));
  if (sensorPitch != null) phonePitch = phonePitch == null ? sensorPitch : phonePitch + k * (sensorPitch - phonePitch);
}

// While steering, yaw follows the phone. Held still, the phone's heading settles on where the game says the player
// looks (not while it moves: the game's heading arrives a moment late then).
const SETTLE_AFTER_MS = 300;
const SETTLE_GAIN = 0.25;
function followGame() {
  if (!steers() || !reference || !state.gameLive) return;
  if (performance.now() - phoneMovedAt < SETTLE_AFTER_MS) return;
  reference.world = norm360(reference.world + SETTLE_GAIN * norm180(Number(state.player?.yaw ?? 0) - scannerHeading()));
}

// Only "deviceorientation": on Android its alpha is relative (gyro, steady indoors), and mixing it
// with the compass-based "deviceorientationabsolute" would make the heading jump between the two.
function useSensors(on) {
  if (on === sensorsOn) return;
  sensorsOn = on;
  if (!on) {
    window.removeEventListener("deviceorientation", orientationHandler, true);
    sensorState = "off";
    sensorHeading = phoneHeading = sensorPitch = phonePitch = reference = null;
    sensorAngles = [];
    return;
  }
  if (typeof DeviceOrientationEvent === "undefined") { sensorState = "none"; return; }
  sensorState = "waiting";
  const listen = () => { if (sensorsOn) window.addEventListener("deviceorientation", orientationHandler, true); };
  // iOS asks first, and only from a tap: moving the selector is one.
  if (typeof DeviceOrientationEvent.requestPermission === "function") {
    DeviceOrientationEvent.requestPermission()
      .then((result) => { if (result === "granted") listen(); else sensorState = "denied"; })
      .catch(() => { sensorState = "denied"; });
  } else {
    listen();
  }
}

// As soon as the sensor has read.
function pairHeading() {
  wantReference = true;
  if (phoneHeading != null) takeReference();
}

// The hint to line the phone up with ⌖: the first time VIEW starts, and when the phone has pointed far
// off straight ahead for a while (not steering), as when its straight ahead is no longer the screen's.
// It goes after CENTER_HINT_MS, or with ⌖.
const CENTER_HINT_MS = 6000;
const OFF_CENTER_DEG = 50, OFF_CENTER_MS = 10000, REMIND_EVERY_MS = 60000;
const HINTS = {
  start: "Hold the phone as you like to look at it, point it at your screen, and tap ⌖ (or here) to line them up.",
  off: "Pointing far to the side for a while? Point the phone at your screen and tap ⌖ (or here) to line it up again.",
};
let centerHintTimer = null, centerHinted = false, offCenterSince = null, remindedAt = -Infinity;
function showCenterHint(which) {
  clearTimeout(centerHintTimer);
  $("centerHint").hidden = !which;
  $("recenter").classList.toggle("prompting", Boolean(which)); // ⌖ lights up while the hint asks for it
  if (!which) return;
  $("centerHint").textContent = HINTS[which];
  centerHintTimer = setTimeout(() => showCenterHint(null), CENTER_HINT_MS);
}

function watchCentering(now) {
  const off = sensorsOn && reference && phoneHeading != null && !steers()
    && Math.abs(norm180(phoneHeading - reference.phone)) > OFF_CENTER_DEG;
  offCenterSince = off ? offCenterSince ?? now : null;
  if (off && now - offCenterSince > OFF_CENTER_MS && now - remindedAt > REMIND_EVERY_MS) {
    remindedAt = now;
    showCenterHint("off");
  }
}

// Centring by itself: most of the time the phone isn't scanning to the side it points about at the screen, held the
// way you like. So while it points within AUTO_CENTER_DEG of straight ahead, straight ahead slowly follows it, and
// level its tilt (AUTO_CENTER_S: the time it takes to come most of the way). That takes up the sensor's slow drift and
// how you sit; ⌖ is the quick fix. Scanning further aside leaves both be, and pointing a little aside for a while
// moves them only a little. Steering, the character turns with the phone instead, and only the level follows.
const AUTO_CENTER_DEG = 25, AUTO_LEVEL_DEG = 20, AUTO_CENTER_S = 120;
let autoCenteredAt = 0;
function autoCenter(now) {
  const k = Math.min(0.5, Math.max(0, now - autoCenteredAt) / 1000) / AUTO_CENTER_S;
  autoCenteredAt = now;
  if (!sensorsOn || !reference || phoneHeading == null) return;
  const aside = norm180(phoneHeading - reference.phone);
  if (!steers() && Math.abs(aside) < AUTO_CENTER_DEG) reference.phone = norm360(reference.phone + k * aside);
  const tilt = phonePitch == null ? Infinity : phonePitch - reference.level;
  if (Math.abs(tilt) < AUTO_LEVEL_DEG) reference.level += k * tilt;
}

function recenter() {
  if (!sensorsOn) return;
  pairHeading();
  showCenterHint(null);
  controlHaptic("recenter");
}

// Where the scanner points in the game world. Without the sensor (AV OUT, or no reading yet) it is
// where the player looks; in VIEW as far from it as the phone has turned. Steering, the character turns
// along: from the yaw paired with the phone, kept with the game's by followGame.
function scannerHeading() {
  const yaw = Number(state.player?.yaw ?? 0);
  if (!sensorsOn || phoneHeading == null || !reference) return yaw;
  return norm360((steers() ? reference.world : yaw) + norm180(phoneHeading - reference.phone));
}

// Steering (VIEW, "The phone steers your character", in hand): the character turns and looks up and down by as much
// as the phone did since the one before, its heading and its tilt (so the mouse or controller still adds normally).
// Held still, a keepalive goes once a second so the next move remains continuous. Four seconds still rests steering;
// either the motion sensor or the orientation sensor wakes it as soon as the phone moves again. Not in the
// mini-game: tilting the phone moves its picture there, as the mouse does in the game.
const STEER_MS = 60;
const STEER_KEEPALIVE_MS = 1000;
let lastSteer = {at: 0, heading: null, tilt: null};
function maybeSteer() {
  if (state.crtv?.active && state.crtv?.signalType === "waypoint") return;
  if (!steers() || paused || !sensorsOn || phoneHeading == null || !state.gameLive || miniGameRuns()) return;
  const now = performance.now();
  if (now - lastSteer.at < STEER_MS) return;
  const tilt = phonePitch == null ? null : Math.round(Math.max(-90, Math.min(90, phonePitch)) * 10) / 10;
  const moved = lastSteer.heading == null || Math.abs(norm180(phoneHeading - lastSteer.heading)) >= 0.5
    || (tilt != null && lastSteer.tilt != null && Math.abs(tilt - lastSteer.tilt) >= 0.5);
  if (!moved && now - lastSteer.at < STEER_KEEPALIVE_MS) return;
  lastSteer = {at: now, heading: phoneHeading, tilt};
  postControl(tilt == null ? {type: "steer", yaw: phoneHeading} : {type: "steer", yaw: phoneHeading, pitch: tilt});
}

// The tuned monster pulses, faster and stronger the closer it is and the better the phone points at it.
let lastPulseAt = 0;
function signalPulse(nearest, heading) {
  if (!nearest) return;
  const error = Math.abs(norm180(worldBearing(nearest.dx, nearest.dy) - heading));
  const strength = Math.max(0, 1 - error / 75) * Math.max(0, 1 - nearest.dist / SIGNAL_RANGE);
  if (strength < .16) return;
  const now = performance.now();
  if (now - lastPulseAt > 1100 - strength * 850 && signalHaptic(strength)) lastPulseAt = now;
}

// --- Keeping the screen on, full screen ---

let wakeLock = null;
let wakeText = "tap the screen";
// Chrome only offers the Wake Lock API on secure pages (docs/PHONE_SETUP.md: the Chrome flag that makes the
// companion's address count as one).
async function keepAwake() {
  if (!navigator.wakeLock) {
    wakeText = "not on this page (needs a secure page)";
    return;
  }
  try {
    wakeLock = await navigator.wakeLock.request("screen");
    wakeText = "yes";
    wakeLock.addEventListener("release", () => { wakeText = "released"; });
  } catch (err) {
    wakeText = err.message;
  }
}
// The browser drops the lock whenever the page is hidden, and Android may suspend its sound; both
// come back on return.
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible") return;
  if (wakeLock) keepAwake();
  resumeSound();
});

// On a phone the device takes the whole screen: no browser bars, held upright.
function goFullScreen() {
  if (!matchMedia("(pointer: coarse)").matches || document.fullscreenElement) return;
  document.documentElement.requestFullscreen?.({navigationUI: "hide"})
    .then(() => screen.orientation?.lock?.("portrait"))
    .catch(() => {});
}

// --- Sound only on the phone ---
//
// While the game's sound comes through to the phone, the phone asks the game every 2 s to keep its own quiet
// (tf_audio.lua); the game gives it back 5 s after the last request, so a phone that closes or drops off the Wi-Fi
// leaves the game audible.
const GAME_SOUND_REQUEST_MS = 2000;
let gameSoundAsked = false, soundWasPlaying = false;

// Whether the phone plays the CRTV's sound: in VIEW unless switched off. AV OUT: the PC has all of it.
const phonePlays = () => control.selector === "VIEW" && sound.inView;

// Turned all the way down, the phone doesn't take the sound away from the game.
function requestGameSound() {
  const quiet = phonePlays() && sound.muteGame && sound.volume > 0 && soundPlaying() && bridgeOnline
    && state.gameLive && state.player?.alive !== false && document.visibilityState === "visible";
  if (quiet || gameSoundAsked) postControl({type: "audio", muteGame: quiet});
  gameSoundAsked = quiet;
}
setInterval(requestGameSound, GAME_SOUND_REQUEST_MS);

// --- Text on the CRT ---

let osd = {text: "", until: 0};
let osdMode = "AV OUT"; // the mode it last showed; the page starts in AV OUT
let shownMessage = null, shownOsd = null;

function screenText(now) {
  const message = !bridgeOnline ? "CONNECTION LOST\nRECONNECTING…"
    : bridgeOutdated() ? "RESTART THE COMPANION"
    : !state.gameLive ? "WAITING FOR GAME"
    : "";
  if (message !== shownMessage) $("screenMessage").textContent = shownMessage = message;
  const corner = message ? "" : now < osd.until ? osd.text : paused ? "PAUSED" : "";
  if (corner !== shownOsd) $("osd").textContent = shownOsd = corner;
}

// --- Settings ---

function sensorText() {
  if (sensorState === "waiting") return window.isSecureContext ? "waiting for a reading" : "blocked (needs a secure page)";
  return {off: "off in AV OUT", on: "on", denied: "not allowed", none: "none on this device"}[sensorState];
}

function crtvText(view, monster, signal) {
  if (!phoneShows()) return "off in AV OUT: on the PC only";
  if (!view.active) return "standby";
  const who = view.source === "game" ? "in the game" : "on the phone";
  const what = monster ? `tuned to a monster, ${monster.dist.toFixed(0)} m`
    : signal ? `tuned to ${signal.e.id}, ${signal.dist.toFixed(0)} m` : "scanning";
  return `${what}, ${who}`;
}

function renderSettings() {
  $("setRaise").checked = control.raises;
  $("setMiniGame").checked = control.miniGameShown;
  $("setSync").checked = control.steering;
  $("setAutoPickup").checked = pickup.auto;
  for (const radio of document.querySelectorAll('input[name="standAvOut"]')) {
    radio.checked = (radio.value === "avout") === pickup.standAvOut;
    radio.disabled = !pickup.auto;
  }
  $("setSoundInView").checked = sound.inView;
  $("setSoundMuteGame").checked = sound.muteGame;
  $("setSoundMuteGame").disabled = !sound.inView; // the phone never plays
  $("setSoundVolume").value = String(Math.round(sound.volume * 100));
  $("setAlignSensitivity").value = String(alignmentSettings.sensitivity);
  $("setControlHaptics").checked = haptics.control;
  $("setSignalHaptics").checked = haptics.signal;
  for (const radio of document.querySelectorAll('input[name="intensity"]')) radio.checked = radio.value === haptics.intensity;
}

function renderStatus(view, monster, signal, heading) {
  $("stBridge").textContent = !bridgeOnline ? "offline, reconnecting"
    : bridgeOutdated() ? `restart it (version ${bridgeVersion()}, needs ${NEEDS_BRIDGE})` : `connected, ${location.host}`;
  $("stGame").textContent = state.gameLive ? "live" : "no data";
  $("stCrtv").textContent = crtvText(view, monster, signal);
  $("stStream").textContent = crtvStream.state === "live" ? `${crtvStream.rate()} pictures a second` : crtvStream.state;
  $("stTilt").textContent = tiltOff ? `off: ${tiltOff}` : "on";
  $("stSensor").textContent = sensorText();
  $("stPickup").textContent = pickupText();
  $("stHeading").textContent = sensorsOn && reference
    ? `${Math.round(heading)}° (player ${Math.round(state.player?.yaw || 0)}°)` : `player's, ${Math.round(heading)}°`;
  $("stWake").textContent = wakeText;
  $("stSound").textContent = `${sound.state}; the game's CRTV ${state.audio?.gameSoundOff ? "silent" : "audible"}`;
  $("stCutscene").textContent = state.cutscene?.sequence ?? "none";
}

function pickupText() {
  if (pickup.tilt == null) return "no motion sensor reading";
  const how = pickup.down ? "lying flat" : pickup.resting ? "standing still" : pickup.down == null ? "" : "in hand";
  const turning = pickup.turning == null ? "no gyroscope" : `turning ${pickup.turning.toFixed(1)}°/s`;
  return `${how ? how + ", " : ""}${Math.round(pickup.tilt)}° from lying flat, ${turning}`;
}

const settings = $("settings");
// Android's back gesture closes the settings (they are a step in the history) instead of leaving full
// screen or the page; closing them is a tap, so full screen comes back if the phone left it meanwhile.
function openSettings() {
  renderSettings();
  settings.hidden = false;
  history.pushState({settings: true}, "");
}
function closeSettings() {
  if (settings.hidden) return;
  settings.hidden = true;
  goFullScreen();
  if (history.state?.settings) history.back();
}
window.addEventListener("popstate", () => { settings.hidden = true; });
$("settingsOpen").addEventListener("click", openSettings);
$("settingsClose").addEventListener("click", closeSettings);
settings.addEventListener("click", (ev) => { if (ev.target === settings) closeSettings(); });
$("setRaise").addEventListener("change", (ev) => setRaises(ev.target.checked));
$("setMiniGame").addEventListener("change", (ev) => setMiniGameShown(ev.target.checked));
$("setSync").addEventListener("change", (ev) => setSteering(ev.target.checked));
$("setAlignSensitivity").addEventListener("input", (ev) => setAlignmentSensitivity(ev.target.value));
$("setAutoPickup").addEventListener("change", (ev) => { setAutoPickup(ev.target.checked); renderSettings(); });
for (const radio of document.querySelectorAll('input[name="standAvOut"]')) {
  radio.addEventListener("change", () => setStandAvOut(radio.value === "avout"));
}
// Four seconds still rests steering; stand detection is separate and quicker. With Auto pickup, setting the
// phone on a stand selects VIEW or AV OUT as the stand setting says; taking it off, or picking it up, is VIEW.
// Not while the game's mini-game runs: its image stages are played by tilting the phone and holding it still,
// which would read as laying it flat or setting it down, and switching away cancels the mini-game.
const miniGameRuns = () => Boolean(state.crtv?.miniGame);
onPutDown((down) => setPutDown(down && !miniGameRuns()));
onPickup((held, first, stand) => {
  if (!pickup.auto || first || miniGameRuns()) return;
  const position = pickupPosition(held, stand, pickup.standAvOut);
  if (control.selector === position) return;
  controlHaptic("lock");
  setSelector(position);
});
listenMotion();
$("setSoundInView").addEventListener("change", (ev) => { setSound("inView", ev.target.checked); requestGameSound(); renderSettings(); });
$("setSoundMuteGame").addEventListener("change", (ev) => { setSound("muteGame", ev.target.checked); requestGameSound(); });
$("setSoundVolume").addEventListener("input", (ev) => setSound("volume", Number(ev.target.value) / 100));
$("setSoundVolume").addEventListener("change", requestGameSound); // the game's sound comes back at 0, and goes again above it
$("setControlHaptics").addEventListener("change", (ev) => setHaptics("control", ev.target.checked));
$("setSignalHaptics").addEventListener("change", (ev) => setHaptics("signal", ev.target.checked));
for (const radio of document.querySelectorAll('input[name="intensity"]')) {
  radio.addEventListener("change", () => { setHaptics("intensity", radio.value); controlHaptic("lock"); });
}
$("recenter").addEventListener("click", recenter);
$("centerHint").addEventListener("click", recenter);
// A long buzz that ignores the button-press switch: whether the phone vibrates for this page at all.
$("testVibration").addEventListener("click", () => {
  const was = haptics.control;
  haptics.control = true;
  controlHaptic("test");
  haptics.control = was;
});
$("vibrationNote").textContent = canVibrate
  ? "Nothing on Test: check the phone's own \"Vibration & haptics\" settings, and that it isn't on silent or battery saver."
  : "This browser can't vibrate.";
$("secureHint").hidden = window.isSecureContext;

// A new waypoint frequency buzzes, in either selector position, as the game's CRTV beeps and buzzes
// for one. Those there when the game's data first comes (the page opened) set the baseline; one seen
// before never buzzes again, after a level load either, while one that came up during a load does.
let knownSignals = null; // ids of the waypoint signals seen, since the baseline
function announceNewSignals() {
  if (!state.gameLive || state.player?.alive === false || paused) return; // none while dead/paused
  const ids = (state.signals || []).map(s => s.id);
  if (knownSignals == null) {
    knownSignals = new Set(ids);
    return;
  }
  const fresh = ids.filter(id => !knownSignals.has(id));
  for (const id of fresh) knownSignals.add(id);
  if (fresh.length) newSignalHaptic();
}

// A signal found in the mini-game clicks into place, like the selector.
let lastSignalType = "none";
function onSignalType(type) {
  if (lastSignalType === "waypoint" && type === "waypoint_tuned") controlHaptic("lock");
  lastSignalType = type;
}

// --- Rendering, connection, wiring ---

// Once a frame. An error in one frame goes to the game's log, and the next frame comes all the same.
function render(now) {
  requestAnimationFrame(render);
  try {
    frame(now);
  } catch (error) {
    reportProblem(`page error: ${error?.message ?? error} ${error?.stack?.split("\n")[1]?.trim() ?? ""}`);
  }
}

// Problems on the page, for the game's log (tf_commands.lua): each at most every 10 s, in plain characters.
const problemsAt = new Map();
// `kind` groups notes whose text changes (a count): one of a kind every 10 s.
function reportProblem(text, kind = text) {
  const clean = String(text).replace(/[^ -~]/g, "?").replace(/["\\]/g, "'").trim().slice(0, 200);
  const now = performance.now();
  if (!clean || now - (problemsAt.get(kind) ?? -Infinity) < 10000) return;
  problemsAt.set(kind, now);
  postControl({type: "note", text: clean});
}
window.addEventListener("error", (ev) => reportProblem(`page error: ${ev.message} ${ev.filename?.split("/").pop()}:${ev.lineno}`));
window.addEventListener("unhandledrejection", (ev) => reportProblem(`page error: ${ev.reason?.message ?? ev.reason}`));

let lastFrameAt = performance.now();
function frame(now) {
  lastFrameAt = now;
  smoothHeading(now);
  // Death is not the same as lost telemetry: Townfall can keep the gameplay pawn/state alive long enough
  // for the old scanner view to keep moving. Explicit player.alive shuts the phone CRTV down immediately.
  const connected = bridgeOnline && !bridgeOutdated() && state.gameLive;
  const alive = state.player?.alive !== false;
  const live = connected && alive;
  holdMedia = paused || !alive || (connected && now - sampleArrivedAt > SAMPLE_GAP_MS);
  const view = scannerView(state, live);
  const monsters = contacts(view.enemies.filter(e => e.tuned));
  // A monster outranks a waypoint signal; their channels shouldn't overlap anyway.
  const signal = monsters.length ? null : contacts(view.signals.filter(s => s.tuned))[0] ?? null;
  const heading = scannerHeading();
  const strongest = [...view.enemies, ...view.signals].reduce((m, s) => Math.max(m, clamp01(s.signal)), 0);
  // While the game's CRTV is up the screen is the game's own, mini-games included (the stream); until a picture
  // of it has come, and while the CRTV is down, a CRTV without a picture (static).
  const streaming = live && phoneShows() && view.source === "game";
  crtvStream.update(streaming, holdMedia);
  const streamed = streaming && crtvStream.draw(screenContext, screenCanvas.width, screenCanvas.height);
  $("crtv").classList.toggle("native-feed", streamed);
  if (!streamed) drawScreen(view.active, strongest, connected && !alive);
  updateDial(view);
  // In AV OUT the phone stays dark and silent: the PC has it all.
  updateSound(live && phoneShows() && phonePlays());
  // The game's own goes quiet once the phone's comes through, and comes back at once when it stops.
  if (soundPlaying() !== soundWasPlaying) {
    soundWasPlaying = soundPlaying();
    requestGameSound();
  }
  screenText(now);
  if (!settings.hidden) renderStatus(view, monsters[0], signal, heading);

  if (!paused && alive) signalPulse(monsters[0], heading); // none while paused/dead
  watchCentering(now);
  autoCenter(now);
  if (alive) maybeSteer();
}

// The mini-game's image follows the phone's turn from its pose as the alignment screen opened (alignment.js), from
// the sensor's latest angles, on a timer of its own: it goes on even when the browser holds back the page's frames.
const ALIGN_TICK_MS = 20;
let tiltOff = "not in view"; // alignmentOff's latest
setInterval(() => {
  const now = performance.now();
  const wasOff = tiltOff;
  tiltOff = alignmentOff(now, state.player?.alive !== false);
  if (wasOff && !tiltOff && wakeText !== "yes") reportProblem(`tilt on, but the screen may sleep: wake lock ${wakeText}`);
  const [alpha, beta, gamma] = sensorAngles;
  const angles = Number.isFinite(beta) && Number.isFinite(gamma) ? [alpha ?? 0, beta, gamma] : null;
  const game = state.crtv?.miniGame;
  alignment.update(tiltOff, angles, game?.mode === 1 && (game.stage === 2 || game.stage === 3));
  updateLook(now);
}, ALIGN_TICK_MS);

// The game's CRTV, the picture the phone shows, looks where the phone points (tf_native.lua M.look). Across: not
// steering, as far from where the character faces as the phone points from the screen (since ⌖ paired the two);
// turned with the mouse, the character takes the CRTV along, so the phone pointing at the screen is always where he
// looks and pointing behind you behind him. Steering, the character turns with the phone instead. Up and down: as
// far as the phone is tilted from how ⌖ found it held, so the way you like to hold it is level, and a monster ahead
// is in the middle of the picture, however the character holds the radio. During a mini-game the CRTV keeps the
// game's own direction, which the game tunes by. Once it stops looking, an "off" goes out; the game also lets a look
// go that isn't repeated within 2 s.
// To half a degree: a held hand trembles finer, and each new look costs the game a file write.
const LOOK_SEND_MS = 50, LOOK_KEEPALIVE_MS = 1000;
let lookSent, lookSentAt = -Infinity; // "yaw pitch" as last sent; null: that it stopped looking
function updateLook(now) {
  const looking = control.selector === "VIEW" && sensorsOn && sensorState === "on" && reference
    && phoneHeading != null && phonePitch != null && state.crtv?.active && !miniGameRuns();
  const half = (v) => Math.round(v * 2) / 2;
  const look = !looking ? null : {yaw: steers() ? 0 : half(norm180(phoneHeading - reference.phone)),
                                   pitch: half(Math.max(-90, Math.min(90, phonePitch - reference.level)))};
  const said = look && `${look.yaw} ${look.pitch}`;
  if (said === null && lookSent === null) return;
  if (now - lookSentAt < (said === lookSent ? LOOK_KEEPALIVE_MS : LOOK_SEND_MS)) return;
  lookSent = said;
  lookSentAt = now;
  postControl({type: "look", on: look !== null, yaw: look?.yaw ?? 0, pitch: look?.pitch ?? 0});
}

// What could stop the tilt, for the game's log: the browser holding back the page's frames, the page hidden while
// the tilt is on, and requests the companion refuses or never answers.
let framesHeld = false, alignmentTrouble = {refused: 0, lost: 0};
setInterval(() => {
  const held = !document.hidden && performance.now() - lastFrameAt > 1500;
  if (held && !framesHeld) reportProblem(`page frames stopped while visible (tilt ${tiltOff ?? "on"})`);
  framesHeld = held;
  const {refused, lost, status} = alignment.stats;
  if (refused > alignmentTrouble.refused || lost > alignmentTrouble.lost) {
    reportProblem(`tilt requests refused ${refused} (last status ${status}), unanswered ${lost}`, "tilt requests");
    alignmentTrouble = {refused, lost};
  }
}, 500);
document.addEventListener("visibilitychange", () => {
  if (document.hidden && !tiltOff) reportProblem("page hidden while the tilt was on (screen off or another app)");
});

// Why the phone's pose doesn't move the mini-game's image now (bridge.py ALIGN_STATES), or null: it does.
function alignmentOff(now, alive) {
  if (control.selector !== "VIEW") return "not in view";
  if (!alive) return "player dead";
  if (!state.gameLive) return "no game data";
  if (!state.crtv?.active) return "crtv down";
  if (centreHeld()) return "centre held";
  if (paused) return "game paused";
  if (holdMedia) return "game samples stopped";
  if (document.hidden) return "page hidden";
  if (!sensorsOn || sensorState !== "on") return "motion sensor off";
  if (now - sensorAt >= 300) return "motion sensor quiet";
  if (now - sensorChangedAt >= 1000) return "motion sensor unchanged";
  return null;
}

// The bridge version this page needs (bridge.py BRIDGE_VERSION, raised with every change to what the two say to
// each other): a bridge still running from before an update gets the page to say RESTART THE COMPANION.
const NEEDS_BRIDGE = 31;
let bridgeOnline = false;
let everOnline = false;
let pageVersion = null; // the bridge's page files when this page loaded (bridge.py page_version)
const bridgeVersion = () => state.bridge ?? 1; // a bridge that doesn't say counts as version 1
const bridgeOutdated = () => bridgeOnline && bridgeVersion() < NEEDS_BRIDGE;

// The bridge's feed. It sends a ping every 5 s when nothing else comes (bridge.py EVENT_PING_S); a feed silent for
// FEED_SILENT_MS is a connection Wi-Fi dropped without either end noticing: closed and opened anew.
const FEED_SILENT_MS = 12000;
let feed = null, feedHeardAt = 0;
setInterval(() => {
  if (feed && performance.now() - feedHeardAt > FEED_SILENT_MS) {
    feed.close();
    bridgeOnline = false;
    connect();
  }
}, 2000);

function connect() {
  const es = feed = new EventSource("/events");
  feedHeardAt = performance.now();
  es.addEventListener("ping", () => { feedHeardAt = performance.now(); });
  es.onopen = () => {
    feedHeardAt = performance.now();
    if (!bridgeOnline && everOnline) controlHaptic("reconnect");
    bridgeOnline = everOnline = true;
  };
  es.onmessage = (ev) => {
    feedHeardAt = performance.now();
    try {
      state = JSON.parse(ev.data);
      // The bridge came back with other page files (it was updated): this page is out of date.
      if (state.page) {
        pageVersion ??= state.page;
        if (state.page !== pageVersion) return location.reload();
      }
      const arrived = performance.now();
      if (state.t !== lastSampleT) {
        lastSampleT = state.t;
        sampleArrivedAt = arrived;
      }
      if (typeof state.t === "number") gameClock.sample(state.t, arrived);
      sampledAt = gameClock.phoneTime(state.t) ?? arrived;
      paused = worldStill.update(state.world, sampledAt);
      useNeedleColours(state.crtv?.needleColours);
      announceNewSignals();
      onTelemetry(state, paused);
      // The mini-game over: the phone counts as put down again if it lies or rests.
      if (!miniGameRuns()) setPutDown(pickup.down === true || pickup.resting);
      followGame();
      onSignalType(state.crtv?.signalType ?? "none");
    } catch (_) {}
  };
  es.onerror = () => { bridgeOnline = false; };
}

let shownSelector = control.selector, wasSteering = steers();
onControlChange((c) => {
  useSensors(c.selector === "VIEW");
  const entering = c.selector === "VIEW" && shownSelector !== "VIEW";
  if (entering) pairHeading();
  // Steering starts or stops (picked up, put down, switched): the view carries on from where it looks.
  if (steers() !== wasSteering && reference) {
    reference.world = Number(state.player?.yaw ?? 0);
    lastSteer = {at: 0, heading: null};
  }
  wasSteering = steers();
  // ⌖ in VIEW, where the phone's direction counts; the hint the first time VIEW starts. Turning needs the
  // rotation sensor, which needs a secure page (docs\PHONE_SETUP.md).
  const canTurn = c.selector === "VIEW" && window.isSecureContext;
  $("recenter").hidden = !canTurn;
  if (entering && canTurn && !centerHinted) {
    centerHinted = true;
    showCenterHint("start");
  }
  if (c.selector !== "VIEW") showCenterHint(null);
  shownSelector = c.selector;
  // Shown when the mode changes: also when the phone, put down, stops turning the player, and starts again.
  const mode = c.selector === "VIEW" ? (steers() ? "VIEW · STEERING" : "VIEW") : "AV OUT";
  if (mode !== osdMode) osd = {text: mode, until: performance.now() + 2000};
  osdMode = mode;
  requestGameSound(); // the game gets its sound back at once where the phone stops playing it
  renderSettings();
});
// A long press on the device must not open Android's menu (save image, open video...); only the
// settings keep theirs, for copying.
document.addEventListener("contextmenu", (ev) => { if (!ev.target.closest(".sheet")) ev.preventDefault(); });
// The game hears the phone's mode again every MODE_REPEAT_MS, so a phone that stopped saying it (its page closed or
// frozen, the Wi-Fi gone) has its CRTV put away (scanner.js sayMode); a hidden page counts as AV OUT. Leaving the
// page hands the game back at once, like moving to AV OUT, sound included.
const MODE_REPEAT_MS = 2000;
setInterval(() => sayMode(), MODE_REPEAT_MS);
document.addEventListener("visibilitychange", onVisibilityChange);
window.addEventListener("pagehide", () => {
  sayMode(true);
  if (gameSoundAsked) postControl({type: "audio", muteGame: false});
});
// A wake lock, full screen, sound and (on iOS) the motion sensor need a tap first; any later tap brings
// back sound Android suspended.
document.addEventListener("pointerup", () => { keepAwake(); goFullScreen(); unlockSound(); listenMotion(); }, {once: true});
document.addEventListener("pointerdown", resumeSound);
bindControls();
connect();
requestAnimationFrame(render);
