// The CRTV's sound on the phone: the game's own sounds (the bridge reads them from the game's sound
// banks as they're asked for, game_sounds.py; served at /sounds/ as WAV), played for what the phone's
// screen shows. Only the ones played here are fetched. The game mixes them in FMOD by rules the mod
// can't see; this follows the same states: static while searching (louder as signals come closer), the
// monster's signal when tuned to one, a waypoint's signal, the fine-tune loop, clicks as the needle
// passes the notches, the CRTV going up and down (the in-game CRTV's own are left to the PC when the phone
// has its sound: the game can't silence them). The story videos carry their own soundtracks
// (app.js lets the clip play out loud), and the talking (a waypoint's lines, a cutscene's dialogue)
// plays in step with the game. Whether the phone plays at all is app.js's phonePlays(): in VIEW
// unless switched off here (sound.inView), in AV OUT only when set to show picture and sound.
import { clamp01, loadFlag, loadSetting, saveSetting, watchStillTime } from "./util.js";
import { NOTCH } from "./scanner.js";

const FADE_S = 0.12;   // loops fade in and out this quickly

// Loop -> its sound; how loud each plays is decided in updateSound().
const LOOPS = {
  static: "CRTV_NoSignal_Loop",
  monster: "CRTV_EnemyTunning_Loop",
  signal: "CRTV_TempSignal_Loop",
  fineTune: "CRTV_FineTuning_Loop",
  stored: "CRTV_SignalStored_Loop",
};
// One-shot sounds -> the start of their names (their variants: _01, _02...).
const SHOTS = {
  rise: "PROP_HAP_CRTV_Rise",
  lower: "PROP_HAP_CRTV_Lower",
  monsterFound: "CRTV_EnemyTunning_Transition_Flicker",
  signalFound: "CRTV_TempSignal_Flicker",
  stored: "CRTV_FineTuning_Success_Beep",
  click: "CRTV_Tuning_StaticClick",
};
const used = (name) => Object.values(LOOPS).includes(name) || Object.values(SHOTS).some(start => name.startsWith(start));

export const sound = {
  // Whether the phone plays the sound in VIEW.
  inView: loadFlag("tfc.soundInView", true),
  muteGame: loadFlag("tfc.soundMuteGame", true),
  volume: Number(loadSetting("tfc.soundVolume", "0.8")),
  state: "tap the screen", // for Settings: tap the screen | loading | ready | unavailable (why)
};

export function setSound(key, value) {
  sound[key] = value;
  saveSetting({inView: "tfc.soundInView", muteGame: "tfc.soundMuteGame", volume: "tfc.soundVolume"}[key], value);
}

let audio = null, master = null;
const buffers = new Map(); // sound name -> AudioBuffer
let dialogueTracks = [];   // the cutscenes' dialogue tracks, <Cutscene>_71_DX
let lineNames = new Set(); // the spoken lines (Dialogue_EN.bank), as Dialoc names them
const loops = {};          // loop -> its gain node

// Browsers start sound only from a tap: app.js calls this on the first one.
export async function unlockSound() {
  if (audio) return;
  const Context = window.AudioContext || window.webkitAudioContext;
  if (!Context) { sound.state = "unavailable"; return; }
  audio = new Context();
  master = audio.createGain();
  master.gain.value = 0;
  master.connect(audio.destination);
  routeTalking();
  sound.state = "loading";
  try {
    const response = await fetch("/sounds/sounds.json");
    const catalogue = await response.json();
    if (!response.ok) throw new Error(catalogue.error);
    dialogueTracks = catalogue.dialogue;
    lineNames = new Set(catalogue.lines);
    await Promise.all(catalogue.sounds.filter(used).map(async (name) => {
      const data = await (await fetch(`/sounds/${encodeURIComponent(name)}.wav`)).arrayBuffer();
      buffers.set(name, await audio.decodeAudioData(data));
    }));
  } catch (err) {
    sound.state = `unavailable: ${err.message || "the bridge has none"}`;
    return;
  }
  for (const [loop, name] of Object.entries(LOOPS)) {
    const buffer = buffers.get(name);
    if (!buffer) continue;
    const source = audio.createBufferSource();
    source.buffer = buffer;
    source.loop = true;
    loops[loop] = audio.createGain();
    loops[loop].gain.value = 0;
    source.connect(loops[loop]).connect(master);
    source.start();
  }
  sound.state = `ready, ${buffers.size} sounds`;
}

// Plays the sound, or one of those whose names start with it (the variants: _01, _02...).
function play(prefix, gain = 1) {
  const names = [...buffers.keys()].filter(n => n.startsWith(prefix));
  if (!names.length) return;
  const source = audio.createBufferSource();
  source.buffer = buffers.get(names[Math.floor(Math.random() * names.length)]);
  const level = audio.createGain();
  level.gain.value = gain;
  source.connect(level).connect(master);
  source.start();
}

export const soundReady = () => audio?.state === "running" && buffers.size > 0;

// Android suspends the page's sound when the screen dims or another app comes up, and it stayed off.
// app.js calls this on every tap and whenever the page is shown again.
export function resumeSound() {
  if (audio && audio.state !== "running" && audio.state !== "closed") audio.resume().catch(() => {});
}

let last = null;
const UNDER_TALK = 0.35; // the CRTV's loops under speech, so it is heard
// Once a frame, with what the screen shows: {playing (phonePlays()), game (the in-game CRTV's view),
// active, dial, strongest, monster, waypoint: null | "unsaved" | "saved", fineTune, videoSound (a clip
// playing out loud), talking}.
export function updateSound(now) {
  if (!soundReady()) return;
  master.gain.setTargetAtTime(now.playing ? sound.volume : 0, audio.currentTime, FADE_S);
  const was = last ?? now;
  last = now;
  // The in-game CRTV's one-shot sounds (going up and down, a signal coming in, clicks) can't be silenced
  // in the game: when it is that CRTV's (or just was) and the phone has taken the game's sound over, they
  // stay on the PC alone.
  if (!(sound.muteGame && (now.game || was.game))) {
    if (now.active !== was.active) play(now.active ? SHOTS.rise : SHOTS.lower);
    if (now.monster && !was.monster) play(SHOTS.monsterFound);
    if (now.waypoint && !was.waypoint) play(SHOTS.signalFound);
    if (now.waypoint === "saved" && was.waypoint === "unsaved") play(SHOTS.stored);
    if (now.active && Math.floor(now.dial / NOTCH) !== Math.floor(was.dial / NOTCH)) play(SHOTS.click, 0.6);
  }

  const tuned = now.monster || now.waypoint;
  const levels = {
    static: !now.active || now.videoSound ? 0 : tuned ? 0.1 : 0.25 + 0.45 * clamp01(now.strongest),
    monster: now.monster ? 0.9 : 0,
    signal: now.waypoint === "unsaved" && !now.fineTune ? 0.7 : 0,
    fineTune: now.fineTune ? 0.8 : 0,
    stored: now.waypoint === "saved" && !now.videoSound ? 0.6 : 0,
  };
  for (const [loop, gain] of Object.entries(levels)) {
    loops[loop]?.gain.setTargetAtTime(now.talking ? gain * UNDER_TALK : gain, audio.currentTime, FADE_S);
  }
}

// --- Talking: a cutscene's dialogue, a waypoint's line ---
//
// Streamed by an audio element each (a cutscene's track runs minutes long) and kept in step with the
// game: the mod reads how far in the game is, stamped with the game's clock, which app.js maps to the
// phone's. So the phone knows where the game is in it right now, however late the reading arrived,
// and plays that much further on as its own sound output lags. It starts where the game is (loading
// the file put it 80 ms behind, which took a second to catch up: most lines last 1-3 s); after that a
// small drift is caught up by playing a little faster or slower (the pitch stays), a big one jumps.
// While the game is paused (its menu), or its time in the track stops after running, the track waits.
// Some of the game's sounds never say how far in they are (their time stays where it started): those can't
// be followed, so the track plays through from the start, as the game's does.
const SEEK_S = 0.15;   // further off than this, jump
const STEADY_S = 0.02; // this close is in step
const MAX_RATE = 0.08; // catching up plays at most 8% faster or slower

// How long the phone takes to sound what it plays (Web Audio's own estimate; 0 where it has none).
const outputDelay = () => (audio?.baseLatency || 0) + (audio?.outputLatency || 0);

function syncedTrack(folder) {
  const element = new Audio();
  element.preload = "auto";
  element.preservesPitch = true;
  let started = false;    // put where the game is, once it could be
  let firstTime = null;   // the game's time in it as first read
  const stopped = watchStillTime();
  const track = {
    element,
    name: null,      // what plays
    drift: null,     // how far ahead of the game it plays (s), once it does
    followed: false, // the game's time in it moves, so the track keeps in step with it
    audible() {
      // Do not tell the game to mute its copy until this element has actually started producing audio.
      // On a cache miss the bridge may need a moment to decode the WAV; background video conversion can
      // make that longer. Keeping the game's line audible meanwhile avoids losing short dialogue entirely.
      return Boolean(track.name && element.readyState >= 2 && !element.paused && !element.ended);
    },
    // Plays `next` (a file name in /sounds/<folder>/, or null for nothing), where the game is `time`
    // seconds in as of `at` (performance.now() ms); `held`: the game is paused. Returns what plays.
    update(next, time, at, held = false) {
      if (next !== track.name) {
        track.name = next;
        track.drift = null;
        track.followed = false;
        firstTime = null;
        started = false;
        element.playbackRate = 1;
        if (!next) {
          element.pause();
          element.removeAttribute("src");
          element.load();
          return null;
        }
        element.src = `/sounds/${folder}/${encodeURIComponent(next)}.wav`;
        element.play().catch(() => {});
      }
      if (!track.name || time == null || at == null || element.readyState === 0) return track.name;
      if (firstTime == null) firstTime = time;
      else if (time !== firstTime) track.followed = true;
      if (held || (track.followed && stopped.update(time, at))) {
        if (!element.paused) element.pause();
        return track.name;
      }
      if (!track.followed) {
        if (!started) element.currentTime = time + outputDelay();
        started = true;
        if (element.paused && !element.ended) element.play().catch(() => {});
        return track.name;
      }
      const target = time + (performance.now() - at) / 1000 + outputDelay();
      if (target >= element.duration) return track.name; // the game is past its end: this one finishes
      const drift = element.currentTime - target;
      track.drift = drift;
      let rate = 1;
      if (!started || Math.abs(drift) > SEEK_S) element.currentTime = target;
      else if (Math.abs(drift) > STEADY_S) rate = Math.round((1 - Math.max(-MAX_RATE, Math.min(MAX_RATE, drift))) * 100) / 100;
      started = true;
      if (element.playbackRate !== rate) element.playbackRate = rate;
      // Ended, or stopped by the browser, while the game is still in it (a line said twice in a row).
      if (element.paused) element.play().catch(() => {});
      return track.name;
    },
  };
  return track;
}

// Each cutscene's dialogue is one track in the game (Cinematics_EN.bank: WakeUp_71_DX, ...), played
// along with the cutscene, found by name in its level sequence's.
const dialogue = syncedTrack("dialogue");
const cutsceneTracks = new Map(); // level sequence name -> dialogue track or null

// A waypoint's line (Dialogue_EN.bank), as the game says it: tuned in, clear; not quite, through a
// radio's narrow band and quieter, as the distorted one sounds in the game.
const line = syncedTrack("lines");
let lineFilter = null, lineLevel = null;

// Both through Web Audio, so they follow the master volume and share its output delay.
function routeTalking() {
  audio.createMediaElementSource(dialogue.element).connect(master);
  lineFilter = audio.createBiquadFilter();
  lineFilter.frequency.value = 1400;
  lineFilter.Q.value = 0.8;
  lineLevel = audio.createGain();
  audio.createMediaElementSource(line.element).connect(lineFilter).connect(lineLevel).connect(master);
}

const normalized = (s) => s.toLowerCase().replace(/[^a-z0-9]/g, "");

// The dialogue track of a level sequence: the longest track name found in the sequence's name.
function dialogueFor(sequence) {
  if (!cutsceneTracks.has(sequence)) {
    const name = normalized(sequence);
    let best = null;
    for (const track of dialogueTracks) {
      const base = normalized(track.replace(/_\d+_DX$/, ""));
      if (base && name.includes(base) && (!best || base.length > best.base.length)) best = {track, base};
    }
    cutsceneTracks.set(sequence, best?.track ?? null);
  }
  return cutsceneTracks.get(sequence);
}

// Once a frame, with the cutscene running ({sequence, sequenceTime, at}: at is when the game was that
// far in, on the phone's clock) or null, whether the phone plays sound now, and whether the game is
// paused. Returns the dialogue track playing, or null.
export function updateDialogue(cutscene, playing, held) {
  const name = playing && cutscene?.sequence && soundReady() ? dialogueFor(cutscene.sequence) : null;
  dialogue.update(name, cutscene?.sequenceTime, cutscene?.at, held);
  return dialogue.audible() ? dialogue.name : null;
}

// The file of what a waypoint says ({line, id} from the mod: the programmer sound, else the dialogue
// ID, whichever the game's banks have), or null.
export const lineFor = (said) => [said?.line, said?.id].find(name => name && lineNames.has(name)) ?? null;

// Once a frame, with what a waypoint says ({line, id, ms, clear, at}) or null, whether the phone plays
// sound now, and whether the game is paused. Returns the line playing, or null.
export function updateLine(said, playing, held) {
  const name = playing && soundReady() ? lineFor(said) : null;
  if (name) {
    lineFilter.type = said.clear ? "allpass" : "bandpass";
    lineLevel.gain.value = said.clear ? 1 : 0.6;
  }
  line.update(name, name ? said.ms / 1000 : null, said?.at, held);
  return line.audible() ? line.name : null;
}

// For Settings: what talks on the phone and how far ahead of the game it is ({name, drift, followed}), or null.
export function talking() {
  const track = line.name ? line : dialogue.name ? dialogue : null;
  return track && {name: track.name, drift: track.drift, followed: track.followed};
}
