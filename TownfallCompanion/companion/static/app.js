import { $, createGameClock, deviceHeading, devicePitch, norm180, norm360, clamp01, postControl, watchStillTime, worldBearing }
  from "./util.js";
import { SIGNAL_RANGE, drawFineTune, drawScreen } from "./screen.js";
import { control, letGo, onControlChange, onTelemetry, phoneShows, scannerView, setAvOut, setPutDown,
  setRaises, setSelector, setSteering, steers } from "./scanner.js";
import { listenMotion, onPickup, onPutDown, pickup, pickupPosition, setAutoPickup, setStandAvOut, wakeSteering } from "./pickup.js";
import { bindControls } from "./controls.js";
import { canVibrate, controlHaptic, haptics, newSignalHaptic, setHaptics, signalHaptic } from "./haptics.js";
import { lineFor, resumeSound, setSound, sound, soundReady, talking, unlockSound, updateDialogue,
  updateLine, updateSound } from "./sound.js";
import { createFollower } from "./finetune.js";

let state = {}; // the bridge's latest, from its first update on; every read copes with what is missing
// When the game read the latest sample, on the phone's clock (ms): from its stamp (state.t), else
// when it arrived. Speech and the fine-tune box are timed by it.
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

// --- The game's CRTV videos, converted to MP4 and served by the bridge at /clips/ ---

const clip = $("clip");
const CLIP_DRIFT_S = 1; // further apart than this from the game's video, the clip jumps to its time
let clipSrc = "";
let clipPlaying = false;
let clipSeek = null;
const videoStill = watchStillTime();
let clipHeld = false; // stopped here while the game is paused or its video stands still

// The game paused (its menu): the world's clock (state.world) stands still while the samples keep
// coming. The phone holds everything meanwhile: videos, sound, turning the player, raising the CRTV.
const worldStill = watchStillTime(250);
let paused = false;
// The samples stopped (quitting from the pause menu, which lets the game run a moment more, or loading):
// the videos and sound hold too, instead of playing on until the bridge calls the game gone after 2 s.
const SAMPLE_GAP_MS = 500; // the mod samples every 100 ms
let lastSampleT = null, sampleArrivedAt = -Infinity;
let holdMedia = false;     // paused, or no samples coming: set once a frame by render()

// Showing the game's CRTV, the screen plays whatever the game's screen plays (crtv.video, at
// crtv.videoTime); the phone's own scanner plays the clip of what it is tuned to. A video such as
// "Bink/Enraged_Focused" is /clips/Bink/Enraged_Focused.mp4; without a converted clip the request
// fails and the drawn look stays. While the game is paused, or its video stands still, so does the clip,
// on the same picture.
function updateClip(video, time) {
  const src = video ? `/clips/${video.split("/").map(encodeURIComponent).join("/")}.mp4` : "";
  if (src === clipSrc) {
    const still = videoStill.update(time, sampledAt);
    const held = holdMedia || still;
    if (held !== clipHeld) {
      clipHeld = held;
      if (held) clip.pause();
      else clip.play().catch(() => {});
    }
    if (!held && time != null && clipPlaying && clipDrift(time) > CLIP_DRIFT_S) seekClip(time);
    return;
  }
  clipSrc = src;
  clipHeld = false;
  clipPlaying = false;
  clip.classList.remove("on");
  if (!src) {
    clip.pause();
    clip.removeAttribute("src");
    clip.load();
    return;
  }
  clipSeek = time;
  clip.src = src;
  clip.play().catch(() => {});
}

function seekClip(time) {
  if (Number.isFinite(clip.duration) && clip.duration > 0) clip.currentTime = time % clip.duration;
}

// How far the clip is from the game's video, around the loop: one that just started over is 0.05 s
// from a game at 9.95 s of a 10 s video, not 9.9 s.
function clipDrift(time) {
  const apart = clip.currentTime - time, loop = clip.duration;
  return Number.isFinite(loop) && loop > 0 ? Math.abs(apart - loop * Math.round(apart / loop)) : Math.abs(apart);
}

clip.addEventListener("loadedmetadata", () => { if (clipSeek != null) seekClip(clipSeek); clipSeek = null; });
clip.addEventListener("playing", () => { clipPlaying = true; clip.classList.add("on"); });
clip.addEventListener("error", () => { clipPlaying = false; clip.classList.remove("on"); });

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
// Entering VIEW and the ⌖ button pair the phone's heading and tilt with straight ahead: the way the
// character looks. From then on the scanner looks as far from where the character looks now (turned with
// the mouse, or the camera tilted) as the phone has turned and tilted since. Steering, the character
// turns along with the phone instead (followGame keeps the two together). Magnetic north never matters.

const HEADING_SMOOTHING_MS = 60; // the heading follows the sensor this smoothly, against its jitter
let sensorsOn = false;
let sensorState = "off"; // off | waiting | on | denied | none
let sensorHeading = null; // the latest reading
let phoneHeading = null;  // smoothed, once a frame
let phoneMovedAt = 0;     // performance.now() when the phone last turned noticeably
let smoothedAt = 0
let sensorPitch = null, phonePitch = null; // how far up the phone looks: the latest reading, smoothed
let reference = null;    // {phone, phonePitch: straight ahead; world: steering, the yaw the phone's heading pairs with}
let wantReference = false;

function takeReference() {
  reference = {phone: phoneHeading, phonePitch, world: Number(state.player?.yaw ?? 0),
               worldPitch: Number(state.player?.pitch ?? 0)};
  wantReference = false;
}

function orientationHandler(e) {
  const h = typeof e.alpha === "number" && typeof e.beta === "number" && typeof e.gamma === "number"
    ? deviceHeading(e.alpha, e.beta, e.gamma) : e.webkitCompassHeading;
  if (typeof h !== "number" || !Number.isFinite(h)) return;

  const now = performance.now();
  let moved = 0;
  if (typeof e.beta === "number" && typeof e.gamma === "number") {
    const pitch = devicePitch(e.beta, e.gamma);
    if (sensorPitch != null) moved = Math.max(moved, Math.abs(pitch - sensorPitch));
    sensorPitch = pitch;
    phonePitch ??= sensorPitch;
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
  if (sensorPitch != null && phonePitch != null) phonePitch += k * (sensorPitch - phonePitch);
}

// While steering, yaw follows the phone. The phone's tilt still controls only the scanner picture.
// Held still, the scanner settles on where the game says the player looks (not while it moves: the
// game's heading arrives a moment late then).
const SETTLE_AFTER_MS = 300;
const SETTLE_GAIN = 0.25;
function followGame() {
  if (!steers() || !reference || !state.gameLive) return;
  if (performance.now() - phoneMovedAt < SETTLE_AFTER_MS) return;
  reference.world = norm360(reference.world + SETTLE_GAIN * norm180(Number(state.player?.yaw ?? 0) - scannerHeading()));
  reference.worldPitch += SETTLE_GAIN * (Number(state.player?.pitch ?? 0) - scannerPitch());
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
  start: "Point the phone at your screen, the way your character looks, and tap ⌖ to line them up.",
  off: "Pointing far to the side for a while? Point the phone at your screen and tap ⌖ to line it up again.",
};
let centerHintTimer = null, centerHinted = false, offCenterSince = null, remindedAt = -Infinity;
function showCenterHint(which) {
  clearTimeout(centerHintTimer);
  $("centerHint").hidden = !which;
  if (!which) return;
  $("centerHint").textContent = HINTS[which];
  centerHintTimer = setTimeout(() => { $("centerHint").hidden = true; }, CENTER_HINT_MS);
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

// How far up the scanner looks: the camera's pitch, and in VIEW as far from it as the phone has tilted.
function scannerPitch() {
  const pitch = Number(state.player?.pitch ?? 0);
  if (!sensorsOn || phonePitch == null || reference?.phonePitch == null) return pitch;
  return (steers() ? reference.worldPitch : pitch) + phonePitch - reference.phonePitch;
}

// Steering (VIEW, "The phone steers your character", in hand): yaw follows the phone by the difference
// from the previous heading (so mouse/controller turning still adds normally). Held still, a keepalive goes
// once a second so the next move remains continuous. Four seconds still rests steering; either the motion
// sensor or the orientation sensor wakes it as soon as the phone moves again.
const STEER_MS = 60;
const STEER_KEEPALIVE_MS = 1000;
let lastSteer = {at: 0, heading: null};
function maybeSteer() {
  if (!steers() || paused || !sensorsOn || phoneHeading == null || !state.gameLive) return;
  const now = performance.now();
  if (now - lastSteer.at < STEER_MS) return;
  const moved = lastSteer.heading == null || Math.abs(norm180(phoneHeading - lastSteer.heading)) >= 0.5;
  if (!moved && now - lastSteer.at < STEER_KEEPALIVE_MS) return;
  lastSteer = {at: now, heading: phoneHeading};
  postControl({type: "steer", yaw: phoneHeading});
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
// Chrome only offers the Wake Lock API on secure pages (see README for the http:// workaround).
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
// While the phone plays the CRTV's sound, it asks the game every 2 s to keep its own quiet
// (tf_audio.lua); the game gives it back 5 s after the last request, so a phone that closes or
// drops off the Wi-Fi leaves the game audible.
const GAME_SOUND_REQUEST_MS = 2000;
let gameSoundAsked = false;
let talkingNow = null; // what talks on the phone now: a cutscene's dialogue track or a waypoint's line
let crtvVideoNow = false; // the phone plays the game's CRTV screen video out loud (its converted copy)

// Whether the phone plays the CRTV's sound, talking included: in VIEW unless switched off, in AV OUT
// only when set to show picture and sound. Whatever the phone doesn't play stays in the game.
const phonePlays = () => control.selector === "VIEW" && sound.inView; // AV OUT: the PC has all the sound

// The game's talking and its CRTV screen's video go quiet only while the phone plays them: a line the
// phone hasn't got, or a video that isn't converted, stays in the game. Turned all the way down, the
// phone doesn't take the sound away from the game either.
function requestGameSound() {
  const quiet = phonePlays() && sound.muteGame && sound.volume > 0 && soundReady() && bridgeOnline
    && state.gameLive && state.player?.alive !== false && document.visibilityState === "visible";
  // A waypoint's line is asked for before it starts, while the game's CRTV is up (outside a cutscene): the
  // game's copy goes quiet in the same moment, not a round trip after, which let its first words out.
  const linesComing = Boolean(state.crtv?.active) && !state.cutscene?.sequence;
  if (quiet || gameSoundAsked) {
    postControl({type: "audio", muteGame: quiet, dialogue: quiet && (talkingNow != null || linesComing),
      video: quiet && crtvVideoNow});
  }
  gameSoundAsked = quiet;
}
setInterval(requestGameSound, GAME_SOUND_REQUEST_MS);

// What a waypoint says right now (the mod's {line, id, ms, clear}), the one tuned to first.
function spokenLine(signals) {
  const speaking = (signals || []).filter(s => s.dialogue);
  return (speaking.find(s => s.tuned) ?? speaking[0])?.dialogue ?? null;
}

// --- Text on the CRT ---

let osd = {text: "", until: 0};
let osdMode = "AV OUT"; // the mode it last showed; the page starts in AV OUT
let shownMessage = null, shownOsd = null;

function screenText(now) {
  const message = !bridgeOnline ? "CONNECTION LOST\nRECONNECTING…"
    : bridgeOutdated() ? "RESTART THE BRIDGE"
    : !state.gameLive ? "WAITING FOR GAME"
    : "";
  if (message !== shownMessage) $("screenMessage").textContent = shownMessage = message;
  const corner = message ? "" : now < osd.until ? osd.text : paused ? "PAUSED" : state.demo ? "DEMO" : "";
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
  $("setSync").checked = control.steering;
  for (const radio of document.querySelectorAll('input[name="avOut"]')) radio.checked = radio.value === control.avOut;
  $("setAutoPickup").checked = pickup.auto;
  for (const radio of document.querySelectorAll('input[name="standAvOut"]')) {
    radio.checked = (radio.value === "avout") === pickup.standAvOut;
    radio.disabled = !pickup.auto;
  }
  $("setSoundInView").checked = sound.inView;
  $("setSoundMuteGame").checked = sound.muteGame;
  $("setSoundMuteGame").disabled = !sound.inView; // the phone never plays
  $("setSoundVolume").value = String(Math.round(sound.volume * 100));
  $("setControlHaptics").checked = haptics.control;
  $("setSignalHaptics").checked = haptics.signal;
  for (const radio of document.querySelectorAll('input[name="intensity"]')) radio.checked = radio.value === haptics.intensity;
}

function renderStatus(view, monster, signal, heading) {
  $("stBridge").textContent = !bridgeOnline ? "offline, reconnecting"
    : bridgeOutdated() ? `restart it (version ${bridgeVersion()}, needs ${NEEDS_BRIDGE})` : `connected, ${location.host}`;
  $("stGame").textContent = state.demo ? "demo game" : state.gameLive ? "live" : "no data";
  $("stCrtv").textContent = crtvText(view, monster, signal);
  $("stSensor").textContent = sensorText();
  $("stPickup").textContent = pickupText();
  $("stHeading").textContent = sensorsOn && reference
    ? `${Math.round(heading)}° (player ${Math.round(state.player?.yaw || 0)}°)` : `player's, ${Math.round(heading)}°`;
  $("stWake").textContent = wakeText;
  $("stSound").textContent = `${sound.state}; the game's CRTV ${state.audio?.gameSoundOff ? "silent" : "audible"}`;
  $("stCutscene").textContent = state.cutscene?.sequence ?? "none";
  $("stTalk").textContent = talkText();
}

function pickupText() {
  if (pickup.tilt == null) return "no motion sensor reading";
  const how = pickup.down ? "lying flat" : pickup.resting ? "standing still" : pickup.down == null ? "" : "in hand";
  const turning = pickup.turning == null ? "no gyroscope" : `turning ${pickup.turning.toFixed(1)}°/s`;
  return `${how ? how + ", " : ""}${Math.round(pickup.tilt)}° from lying flat, ${turning}`;
}

// Settings: what talks and whether the phone keeps in step with the game.
function talkText() {
  const now = talking();
  if (now) {
    const drift = !now.followed ? "playing through (the game gives no position to follow)"
      : now.drift == null ? "starting" : Math.abs(now.drift) < 0.03 ? "in step with the game"
      : `${Math.round(Math.abs(now.drift) * 1000)} ms ${now.drift > 0 ? "ahead of" : "behind"} the game, catching up`;
    return `${now.name}: ${drift}`;
  }
  const said = spokenLine(state.signals);
  if (said) {
    const name = lineFor(said);
    return name ? `line ${name}, heard in the game (the phone isn't playing sound now)`
      : `line "${said.line}" (dialogue "${said.id}"), not in the game's sound banks: heard in the game`;
  }
  if (!state.cutscene?.sequence) return "quiet";
  if (!phonePlays() || !soundReady()) return "a cutscene, heard in the game";
  return sound.muteGame && state.audio?.cutsceneDialogue !== true
    ? "a cutscene, its dialogue in the game only (the game can't silence it here)"
    : "a cutscene without a dialogue track here";
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
$("setSync").addEventListener("change", (ev) => setSteering(ev.target.checked));
for (const radio of document.querySelectorAll('input[name="avOut"]')) {
  radio.addEventListener("change", () => setAvOut(radio.value)); // the game's sound follows (onControlChange)
}
// The bridge's simulated game, for trying the phone without Townfall; the real game wins over it.
$("setAutoPickup").addEventListener("change", (ev) => { setAutoPickup(ev.target.checked); renderSettings(); });
for (const radio of document.querySelectorAll('input[name="standAvOut"]')) {
  radio.addEventListener("change", () => setStandAvOut(radio.value === "avout"));
}
// Four seconds still rests steering; stand detection is separate and quicker. With Auto pickup, setting the
// phone on a stand selects VIEW or AV OUT as the stand setting says; taking it off, or picking it up, is VIEW.
onPutDown(setPutDown);
onPickup((held, first, stand) => {
  if (!pickup.auto || first) return;
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

// --- The fine-tune mini-game ---
//
// The box is drawn by a follower (finetune.js) that runs its motion smoothly between the updates.
let tuneShown = null; // the game's last fineTune, while the mini-game is on
let follower = null;

function onFineTune(tune) {
  if (!tune) { tuneShown = follower = null; return; }
  follower ??= createFollower();
  follower.sample(tune.box, sampledAt);
  tuneShown = tune;
}

function fineTuneAt(now) {
  return tuneShown && {...tuneShown, box: follower.at(now)};
}

// A new waypoint frequency buzzes, in either selector position, as the game's CRTV beeps and buzzes
// for one. Those there when the game's data first comes (the page opened, the demo switched to the
// game or back) set the baseline; one seen before never buzzes again, after a level load either,
// while one that came up during a load does.
let knownSignals = null; // ids of the waypoint signals seen, since the baseline
let knownFrom = null;    // whose they are: the demo's or the game's
function announceNewSignals() {
  if (!state.gameLive || state.player?.alive === false || paused) return; // none while dead/paused
  const ids = (state.signals || []).map(s => s.id);
  if (knownSignals == null || knownFrom !== state.demo) {
    knownSignals = new Set(ids);
    knownFrom = state.demo;
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

function render(now) {
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
  drawScreen(view, monsters, strongest, heading, scannerPitch(), now, clipPlaying, connected && !alive);
  const tune = live && view.source === "game" && fineTuneAt(now);
  if (tune) drawFineTune(tune);
  updateDial(view);
  // In AV OUT set to show nothing, the phone stays dark and silent: the PC has it all.
  const shown = live && phoneShows();
  // A cutscene's screen video comes first, in either selector position; then the in-game CRTV's, or
  // the phone scanner's own.
  const cutscene = shown ? state.cutscene : null;
  if (!shown) updateClip(null, null);
  else if (cutscene?.video) updateClip(cutscene.video, cutscene.videoTime);
  else if (view.source === "game") updateClip(state.crtv?.video, state.crtv?.videoTime);
  else updateClip((monsters[0] ?? signal)?.e.video, null);
  // The story clips carry their soundtracks; they play out loud with the rest of the phone's sound. Not a
  // cutscene's: the game plays that sound too and can't safely be made to stop (tf_audio.lua).
  const playing = shown && phonePlays() && soundReady();
  clip.muted = !playing || Boolean(cutscene?.video);
  clip.volume = sound.volume;
  // Showing the game's CRTV, the clip is the video its screen plays: only then may the game's go quiet.
  const crtvVideo = view.source === "game" && !cutscene?.video && clipPlaying && !clip.muted;
  if (crtvVideo !== crtvVideoNow) { crtvVideoNow = crtvVideo; requestGameSound(); }
  // The talking plays along wherever the rest of the sound does (phonePlays), in step with the
  // game by when it read how far in it was: a cutscene's dialogue, and what a waypoint says. A
  // cutscene's dialogue only where the game can drop it (the mod says), or keeps its sound anyway:
  // never heard twice.
  const sceneOnPhone = playing && (!sound.muteGame || state.audio?.cutsceneDialogue === true);
  const scene = updateDialogue(cutscene?.sequence ? {...cutscene, at: sampledAt} : null, sceneOnPhone, holdMedia);
  const said = shown ? spokenLine(state.signals) : null;
  const line = updateLine(said && {...said, at: sampledAt}, playing, holdMedia);
  if ((line ?? scene) !== talkingNow) { talkingNow = line ?? scene; requestGameSound(); }
  updateSound({
    playing: playing && !holdMedia, // the talking holds by itself: its time stands still too
    game: view.source === "game",
    active: view.active,
    dial: view.dial,
    strongest,
    monster: monsters.length > 0,
    waypoint: signal ? (signal.e.found ? "saved" : "unsaved") : null,
    fineTune: Boolean(tune),
    videoSound: clipPlaying && !clip.muted,
    talking: talkingNow != null,
  });
  screenText(now);
  if (!settings.hidden) renderStatus(view, monsters[0], signal, heading);

  if (!paused && alive) signalPulse(monsters[0], heading); // none while paused/dead
  watchCentering(now);
  if (alive) maybeSteer();
  requestAnimationFrame(render);
}

// The bridge version this page needs (bridge.py BRIDGE_VERSION): phone commands, waypoint signals, no stale
// files, the F key, sound, the game's clock stamp, the game's sounds read from its banks (WAV), a CRTV
// command that only moves the dial.
const NEEDS_BRIDGE = 10;
let bridgeOnline = false;
let everOnline = false;
let pageVersion = null; // the bridge's page files when this page loaded (bridge.py page_version)
const bridgeVersion = () => state.bridge ?? 1; // a bridge that doesn't say counts as version 1
const bridgeOutdated = () => bridgeOnline && bridgeVersion() < NEEDS_BRIDGE;

function connect() {
  const es = new EventSource("/events");
  es.onopen = () => {
    if (!bridgeOnline && everOnline) controlHaptic("reconnect");
    bridgeOnline = everOnline = true;
  };
  es.onmessage = (ev) => {
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
      followGame();
      onFineTune(state.crtv?.fineTune);
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
    reference.worldPitch = Number(state.player?.pitch ?? 0);
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
// Leaving the page hands the game back, like moving to AV OUT, sound included.
window.addEventListener("pagehide", () => {
  letGo();
  if (gameSoundAsked) postControl({type: "audio", muteGame: false});
});
// A wake lock, full screen, sound and (on iOS) the motion sensor need a tap first; any later tap brings
// back sound Android suspended.
document.addEventListener("pointerup", () => { keepAwake(); goFullScreen(); unlockSound(); listenMotion(); }, {once: true});
document.addEventListener("pointerdown", resumeSound);
bindControls();
connect();
requestAnimationFrame(render);
