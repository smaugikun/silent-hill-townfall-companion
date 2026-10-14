import assert from "node:assert/strict";
import test from "node:test";
import { createScheduledOutput } from "../TownfallCompanion/companion/static/audio-output.js";
import { createAudioStream } from "../TownfallCompanion/companion/static/audio-stream.js";
import { motionPermission } from "../TownfallCompanion/companion/static/motion-access.js";

function audio() {
  const sources = [];
  return {
    currentTime: 0, state: "running", sampleRate: 44100, destination: {}, sources,
    createGain: () => ({gain: {value: 1}, connect(next) { return next; }, disconnect() {}}),
    createBuffer(channels, frames, rate) {
      const data = Array.from({length: channels}, () => new Float32Array(frames));
      return {data, sampleRate: rate, getChannelData: c => data[c]};
    },
    createBufferSource() {
      const source = {connect() {}, disconnect() {}, stop() { this.stopped = true; }, start(at) { this.at = at; }};
      sources.push(source);
      return source;
    },
    resume() { this.state = "running"; return Promise.resolve(); },
  };
}
const settle = async () => { for (let i = 0; i < 12; i++) await new Promise(resolve => setTimeout(resolve, 0)); };
const stereo = frames => Float32Array.from({length: frames * 2}, (_, i) => i % 2 ? -.5 : .5);

test("HTTP output plays stereo at the stream rate even when the phone uses a different hardware rate", () => {
  const context = audio(), output = createScheduledOutput(context, 48000, 2);
  output.connect({});
  output.port.postMessage(stereo(4000));
  assert.equal(context.sources.length, 0, "wait for the startup buffer");
  output.port.postMessage(stereo(2000));
  assert.ok(context.sources.length > 0);
  const source = context.sources[0];
  assert.equal(source.buffer.sampleRate, 48000, "native Web Audio resamples to 44100");
  assert.equal(source.buffer.data[0][0], .5);
  assert.equal(source.buffer.data[1][0], -.5);
  assert.equal(output.isPlaying(), false, "scheduled but not audible yet");
  context.currentTime = source.at + .001;
  assert.equal(output.isPlaying(), true);
  output.port.postMessage("reset");
  assert.equal(output.isPlaying(), false);
  assert.ok(context.sources.every(source => source.stopped));
});

test("low-rate mono output starts at the target and caps queued audio after a backlog", () => {
  const context = audio();
  let health;
  const output = createScheduledOutput(context, 8000, 1, data => health = data);
  output.connect({});
  output.port.postMessage(new Float32Array(960).fill(.25));
  assert.equal(context.sources.length, 3);
  assert.equal(context.sources[0].buffer.data.length, 1);
  output.port.postMessage(new Float32Array(10000));
  assert.ok(health.frames <= 1040);
  assert.ok(health.dropped > 0);
  assert.ok(context.sources[0].stopped);
  output.disconnect();
  assert.equal(output.isPlaying(), false);
});

const header = [84,70,65,85,0x80,0xbb,0,0,2,0];
function response(bytes) {
  let first = true, resolveRead, cancels = 0, releases = 0;
  const reader = {
    read() { if (first) { first = false; return Promise.resolve({done: false, value: bytes}); }
      return new Promise(resolve => resolveRead = resolve); },
    cancel() { cancels++; resolveRead?.({done: true}); return Promise.resolve(); },
    releaseLock() { releases++; },
  };
  return {ok: true, body: {getReader: () => reader}, get cancels() { return cancels; }, get releases() { return releases; }};
}

test("a suspended phone stays inaudible to PC routing; resume plays HTTP audio and disabling releases its reader", async () => {
  const context = audio();
  context.state = "suspended";
  const pcm = new Uint8Array(6000 * 4);
  const bytes = new Uint8Array(header.length + pcm.length);
  bytes.set(header); bytes.set(pcm, header.length);
  const incoming = response(bytes);
  const stream = createAudioStream({context, request: async () => incoming});
  stream.update(true);
  await settle();
  assert.equal(stream.state, "live");
  context.currentTime = .02;
  assert.equal(stream.playing(), false);
  await stream.resume();
  assert.equal(stream.playing(), true);
  stream.update(false);
  await settle();
  assert.equal(stream.playing(), false);
  assert.ok(incoming.cancels > 0);
  assert.equal(incoming.releases, 1);
});

test("Safari audio context is created and resumed synchronously from the tap before fetching", async () => {
  const order = [];
  globalThis.webkitAudioContext = class {
    constructor() {
      order.push("create"); Object.assign(this, audio());
      this.resume = () => { order.push("resume"); return Promise.resolve(); };
    }
  };
  try {
    const stream = createAudioStream({request: async () => { order.push("fetch"); return response(new Uint8Array(header)); }});
    const resumed = stream.resume();
    assert.deepEqual(order, ["create", "resume"]);
    await resumed;
    stream.update(true);
    await settle();
    assert.deepEqual(order, ["create", "resume", "fetch"]);
    stream.update(false);
    await settle();
  } finally { delete globalThis.webkitAudioContext; }
});

test("disabling while a worklet is loading never delivers stale samples or revives playback", async () => {
  const context = audio(), posted = [];
  context.sampleRate = 48000;
  let loaded;
  context.audioWorklet = {addModule: () => new Promise(resolve => loaded = resolve)};
  globalThis.AudioWorkletNode = class {
    constructor() { this.port = {postMessage: data => posted.push(data)}; }
    connect(next) { return next; }
    disconnect() {}
  };
  try {
    const incoming = response(new Uint8Array([...header, 0,64,0,64]));
    const stream = createAudioStream({context, request: async () => incoming});
    stream.update(true); await settle();
    stream.update(false); loaded(); await settle();
    assert.equal(stream.state, "off");
    assert.equal(stream.playing(), false);
    assert.equal(posted.filter(data => data instanceof Float32Array).length, 0);
    assert.equal(incoming.releases, 1);
  } finally { delete globalThis.AudioWorkletNode; }
});

test("motion requests can retry after a rejected pre-tap attempt and deduplicate the permission prompt", async () => {
  let asks = 0, resolveRequest;
  const Event = {requestPermission() {
    asks++;
    return asks === 1 ? Promise.reject(new Error("needs tap")) : new Promise(resolve => resolveRequest = resolve);
  }};
  assert.equal(await motionPermission(Event), false);
  const first = motionPermission(Event), second = motionPermission(Event);
  assert.equal(first, second);
  resolveRequest("granted");
  assert.equal(await first, true);
  assert.equal(await motionPermission(Event), true);
  assert.equal(asks, 2);
});
