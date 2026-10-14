// The CRTV's sound on the phone: the game's own, live (/api/audio/stream, audio-stream.js), as the game mixes it
// (native/audio.c): the CRTV with the voices heard through it, the waypoints' and transmissions' talking, and the
// CRTV's videos. Whether the phone plays it is app.js's phonePlays(); while the phone's comes through, the game can
// keep its own quiet ("Silence it in the game").
import { createAudioStream } from "./audio-stream.js";
import { createAudioHandoff } from "./audio-handoff.js";
import { loadFlag, loadSetting, saveSetting } from "./util.js";

export const sound = {
  // Whether the phone plays the sound in VIEW.
  inView: loadFlag("tfc.soundInView", true),
  muteGame: loadFlag("tfc.soundMuteGame", true),
  volume: Number(loadSetting("tfc.soundVolume", "0.8")),
  removeHum: loadFlag("tfc.soundRemoveHum", true), // the CRTV's steady hum filtered out (audio-stream.js)
  state: "tap the screen", // for Settings
};

const stream = createAudioStream();
const claim = createAudioHandoff();
let audible = false;
stream.volume(sound.volume);
stream.removeHum(sound.removeHum);

export function setSound(key, value) {
  sound[key] = value;
  saveSetting({inView: "tfc.soundInView", muteGame: "tfc.soundMuteGame", volume: "tfc.soundVolume",
               removeHum: "tfc.soundRemoveHum"}[key], value);
  if (key === "volume") stream.volume(audible ? value : 0);
  if (key === "removeHum") stream.removeHum(value);
}

// Browsers start sound only once the page has been tapped: app.js calls this on the first tap.
let tapped = false;
export function unlockSound() {
  tapped = true;
  resumeSound();
}

// Android suspends the page's sound when the screen dims or another app comes up: app.js calls this on every tap and
// whenever the page is shown again.
export function resumeSound() {
  stream.resume()?.catch?.(() => {});
}

// Whether the game's sound comes through to the phone now.
export const soundPlaying = () => audible && stream.playing();

export function takesGameSound(wanted) {
  return claim({wanted: wanted && tapped && sound.inView && sound.muteGame && sound.volume > 0,
    running: stream.context?.state === "running", available: stream.state !== "unsupported",
    playing: stream.playing()});
}

// Once a frame: whether the phone plays the game's sound now.
export function updateSound(playing, warm = playing) {
  const nextAudible = playing && tapped && globalThis.document?.hidden !== true;
  if (nextAudible && !audible) stream.resetOutput(); // discard audio already heard on the PC before the handoff
  audible = nextAudible;
  // Keep a muted player ready in AV OUT, so VIEW doesn't need a fetch/worklet/buffer startup after raising the CRTV.
  const prepare = warm && sound.inView && sound.muteGame && sound.volume > 0 && globalThis.document?.hidden !== true;
  stream.volume(audible ? sound.volume : 0);
  stream.update((audible || prepare) && tapped);
  const health = stream.health;
  sound.state = !tapped ? "tap the screen" : !playing ? "off"
    : stream.state === "live" && stream.context?.state !== "running" ? "tap to resume sound"
    : stream.playing() ? `playing${health?.underruns ? `, ${health.underruns} gaps` : ""}`
    : stream.state === "live" ? "buffering"
    : stream.state === "unsupported" ? "this browser cannot play live sound"
    : stream.state === "retrying" ? "no sound from the game yet, trying again"
    : stream.state === "waiting" ? "connecting" : "off";
}
