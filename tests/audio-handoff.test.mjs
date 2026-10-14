import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";
import {readFileSync} from "node:fs";
import {createAudioHandoff} from "../TownfallCompanion/companion/static/audio-handoff.js";
import {FrameBuffer} from "../TownfallCompanion/companion/static/audio-buffer.js";

test("startup can mute before the first word but gives the PC back after a failed bounded startup", () => {
  let clock = 0;
  const claim = createAudioHandoff(() => clock);
  const waiting = {wanted: true, running: true, available: true, playing: false};
  assert.equal(claim(waiting), true);
  clock = 450; assert.equal(claim(waiting), true);
  clock = 501; assert.equal(claim(waiting), false);
  assert.equal(claim({...waiting, playing: true}), true);
  assert.equal(claim({...waiting, playing: true, running: false}), false, "suspension overrides the startup/gap allowance");
  assert.equal(claim({...waiting, playing: true, available: false}), false);
});

test("a tiny buffer gap does not send a word to PC speakers; a sustained outage or AV OUT does", () => {
  let clock = 0;
  const claim = createAudioHandoff(() => clock);
  const playing = {wanted: true, running: true, playing: true};
  claim(playing);
  clock = 1000; assert.equal(claim(playing), true);
  clock = 1100; assert.equal(claim({...playing, playing: false}), true);
  clock = 1301; assert.equal(claim({...playing, playing: false}), false);
  assert.equal(claim({...playing, wanted: false}), false);
});

test("worklet playback transitions are reported immediately even before its one-second health report", () => {
  const messages = [];
  let Processor;
  const context = {
    FrameBuffer, currentTime: .02,
    AudioWorkletProcessor: class {constructor() { this.port = {postMessage: data => messages.push(data)}; }},
    registerProcessor: (_, value) => Processor = value,
  };
  const source = readFileSync(new URL("../TownfallCompanion/companion/static/audio-worklet.js", import.meta.url), "utf8");
  vm.runInNewContext(source.replace(/^import .*;\r?\n/m, ""), context);
  const processor = new Processor({processorOptions: {channels: 2, target: 5760, capacity: 96000, limit: 19200}});
  processor.port.onmessage({data: new Float32Array(12000)});
  processor.process([], [[new Float32Array(128), new Float32Array(128)]]);
  assert.equal(messages.at(-1).playing, true);
  context.currentTime = .03;
  processor.process([], [[new Float32Array(16384), new Float32Array(16384)]]);
  assert.equal(messages.at(-1).playing, false);
});

test("AV OUT prepares a silent stream and VIEW reuses it without another fetch or worklet startup", async () => {
  let context, requests = 0, first = true, finish;
  globalThis.AudioContext = class {
    constructor() { context = this; this.state = "running"; this.currentTime = 0; this.sampleRate = 44100; this.destination = {}; }
    resume() { return Promise.resolve(); }
    createGain() { return (this.gain = {gain: {value: 1}, connect(next) { return next; }}); }
    createBuffer(channels, frames, rate) {
      const data = Array.from({length: channels}, () => new Float32Array(frames));
      return {sampleRate: rate, getChannelData: c => data[c]};
    }
    createBufferSource() { return {connect() {}, disconnect() {}, start() {}, stop() {}}; }
  };
  const pcm = new Uint8Array(10 + 6000 * 4);
  pcm.set([84,70,65,85,0x80,0xbb,0,0,2,0]);
  globalThis.fetch = async () => {
    requests++;
    return {ok: true, body: {getReader: () => ({
      read() { if (first) { first = false; return Promise.resolve({done: false, value: pcm}); }
        return new Promise(resolve => finish = resolve); },
      cancel() { finish?.({done: true}); return Promise.resolve(); }, releaseLock() {},
    })}};
  };
  const settle = async () => { for (let i = 0; i < 12; i++) await new Promise(resolve => setTimeout(resolve, 0)); };
  let sound;
  try {
    sound = await import("../TownfallCompanion/companion/static/sound.js?warm-test");
    sound.unlockSound(); sound.updateSound(false, true); await settle();
    context.currentTime = .02;
    assert.equal(context.gain.gain.value, 0);
    assert.equal(sound.soundPlaying(), false);
    assert.equal(sound.takesGameSound(false), false);
    sound.updateSound(true, true);
    assert.ok(context.gain.gain.value > 0);
    assert.equal(sound.soundPlaying(), false, "old PC audio was discarded before making the phone audible");
    assert.equal(sound.takesGameSound(true), true);
    finish({done: false, value: pcm.subarray(10)});
    await settle();
    context.currentTime = .05;
    assert.equal(sound.soundPlaying(), true);
    assert.equal(requests, 1);
    sound.updateSound(false, true);
    assert.equal(context.gain.gain.value, 0);
  } finally {
    sound?.updateSound(false, false); await settle();
    delete globalThis.AudioContext; delete globalThis.fetch;
  }
});
