// The CRTV's sound on the phone: the game's own, live (/api/audio/stream, audio-stream.js), as the game mixes it
// (native/audio.c): the CRTV with the voices heard through it, the waypoints' and transmissions' talking, and the
// CRTV's videos. Whether the phone plays it is app.js's phonePlays(); while the phone's comes through, the game can
// keep its own quiet ("Silence it in the game").
import { createAudioStream } from "./audio-stream.js";
import { loadFlag, loadSetting, saveSetting } from "./util.js";

export const sound = {
  // Whether the phone plays the sound in VIEW.
  inView: loadFlag("tfc.soundInView", true),
  muteGame: loadFlag("tfc.soundMuteGame", true),
  volume: Number(loadSetting("tfc.soundVolume", "0.8")),
  state: "tap the screen", // for Settings
};

const stream = createAudioStream();
stream.volume(sound.volume);

export function setSound(key, value) {
  sound[key] = value;
  saveSetting({inView: "tfc.soundInView", muteGame: "tfc.soundMuteGame", volume: "tfc.soundVolume"}[key], value);
  if (key === "volume") stream.volume(value);
}

// Browsers start sound only once the page has been tapped: app.js calls this on the first tap.
let tapped = false;
export function unlockSound() {
  tapped = true;
}

// Android suspends the page's sound when the screen dims or another app comes up: app.js calls this on every tap and
// whenever the page is shown again.
export function resumeSound() {
  stream.resume()?.catch?.(() => {});
}

// Whether the game's sound comes through to the phone now.
export const soundPlaying = () => stream.state === "live";

// Once a frame: whether the phone plays the game's sound now.
export function updateSound(playing) {
  stream.update(playing && tapped);
  const health = stream.health;
  sound.state = !tapped ? "tap the screen"
    : stream.state === "live" ? `playing${health?.underruns ? `, ${health.underruns} gaps` : ""}`
    : stream.state === "retrying" ? "no sound from the game yet, trying again"
    : stream.state === "waiting" ? "connecting" : "off";
}
