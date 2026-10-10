// The phone's player for the game's live sound (companion/static/audio-buffer.js, audio-stream.js) without a browser:
// the buffer it plays from, the stream's header and samples, and the stream read into the player.
// Run by test_scanner.py, or directly: node --test tests/audio.test.mjs
import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

const STATIC = path.resolve(import.meta.dirname, "../TownfallCompanion/companion/static");
const { FrameBuffer } = await import(pathToFileURL(path.join(STATIC, "audio-buffer.js")).href);
const { createAudioStream, parseHeader, toFloats } = await import(pathToFileURL(path.join(STATIC, "audio-stream.js")).href);

const stereo = (frames, value = 0.5) => Float32Array.from({length: frames * 2}, (_, i) => (i % 2 ? -value : value));
const pull = (buffer, frames) => {
  const outputs = [new Float32Array(frames), new Float32Array(frames)];
  buffer.pull(outputs);
  return outputs;
};

test("the buffer plays only once the target is in, then frame by frame, each channel to its output", () => {
  const buffer = new FrameBuffer({channels: 2, target: 100, limit: 400, capacity: 1000});
  buffer.push(stereo(60));
  assert.deepEqual([...pull(buffer, 4)[0]], [0, 0, 0, 0], "not yet");
  buffer.push(stereo(60));
  const [left, right] = pull(buffer, 4);
  assert.deepEqual([[...left], [...right]], [[.5, .5, .5, .5], [-.5, -.5, -.5, -.5]]);
  assert.equal(buffer.frames, 116);
});

test("run dry it is silent, counts it, and waits for the target again", () => {
  const buffer = new FrameBuffer({channels: 2, target: 10, limit: 100, capacity: 200});
  buffer.push(stereo(12));
  const [left] = pull(buffer, 16);
  assert.deepEqual([...left.subarray(10)], [.5, .5, 0, 0, 0, 0]);
  assert.equal(buffer.underruns, 1);
  buffer.push(stereo(5));
  assert.deepEqual([...pull(buffer, 2)[0]], [0, 0], "5 in: still waiting for 10");
});

test("too far behind, the oldest frames go, down to the target", () => {
  const buffer = new FrameBuffer({channels: 2, target: 50, limit: 200, capacity: 1000});
  buffer.push(stereo(150));
  buffer.push(stereo(100, .25));
  assert.equal(buffer.frames, 50);
  assert.equal(buffer.dropped, 200);
  assert.deepEqual([...pull(buffer, 2)[0]], [.25, .25], "the newest kept");
});

test("the header says the rate and the channels; anything else is refused", () => {
  const header = new Uint8Array([84, 70, 65, 85, 0x80, 0xbb, 0, 0, 2, 0]); // "TFAU", 48000, 2
  assert.deepEqual(parseHeader(header), {rate: 48000, channels: 2});
  assert.equal(parseHeader(header.subarray(0, 9)), null);
  assert.equal(parseHeader(new Uint8Array([84, 70, 65, 86, 0x80, 0xbb, 0, 0, 2, 0])), null);
  assert.equal(parseHeader(new Uint8Array([84, 70, 65, 85, 1, 0, 0, 0, 2, 0])), null, "1 Hz");
});

test("16-bit samples become floats, and an odd byte waits for the next read", () => {
  const {samples, rest} = toFloats(new Uint8Array([0x00, 0x40, 0x00, 0xc0, 0xff, 0x7f, 0x12]));
  assert.deepEqual([...samples], [0.5, -0.5, 32767 / 32768]);
  assert.deepEqual([...rest], [0x12]);
});

// A page-less audio graph: what goes to the worklet is kept in `posted`.
function fakeAudio() {
  const posted = [];
  globalThis.AudioWorkletNode = class {
    constructor(context, name, options) { this.options = options; this.port = {postMessage: (m) => posted.push(m)}; }
    connect(next) { return next; }
    disconnect() {}
  };
  const context = {
    audioWorklet: {addModule: async () => {}},
    createGain: () => ({gain: {value: 1}, connect: (destination) => destination}),
    destination: {},
  };
  return {posted, context};
}

const HEADER = [84, 70, 65, 85, 0x80, 0xbb, 0, 0, 2, 0]; // TFAU, 48000 Hz, stereo
// A response handing out `chunks`, then ending (`ends`) or staying open with nothing more, as a live stream does.
const response = (chunks, ends) => ({
  ok: true,
  body: {getReader: () => ({read: () => chunks.length ? Promise.resolve({done: false, value: new Uint8Array(chunks.shift())})
    : ends ? Promise.resolve({done: true}) : new Promise(() => {})})},
});
const settle = async () => { for (let i = 0; i < 40; i++) await new Promise((r) => setTimeout(r, 0)); };

test("the stream goes to the player in whole frames, after the header, however the bytes are split", async () => {
  const {posted, context} = fakeAudio();
  const pcm = [0x00, 0x40, 0x00, 0xc0, 0x00, 0x20, 0x00, 0xe0]; // two frames: (.5, -.5), (.25, -.25)
  const chunks = [HEADER.slice(0, 3), [...HEADER.slice(3), ...pcm.slice(0, 3)], pcm.slice(3)];
  const stream = createAudioStream({request: async () => response(chunks, false), context, now: () => 0});
  stream.update(true);
  await settle();
  assert.equal(stream.state, "live");
  const sent = posted.filter((m) => m instanceof Float32Array).flatMap((m) => [...m]);
  assert.deepEqual(sent, [0.5, -0.5, 0.25, -0.25]);
  assert.equal(posted[0], "reset", "a new stream starts the buffer afresh");
  delete globalThis.AudioWorkletNode;
});

test("a stream that ends or goes silent is no longer live, and is opened again", async () => {
  const {context} = fakeAudio();
  let clock = 0, requests = 0;
  const request = async () => (++requests === 1 ? response([HEADER], true) : response([HEADER], false));
  const stream = createAudioStream({request, context, now: () => clock});
  stream.update(true);
  await settle();
  assert.equal(stream.state, "waiting"); // the game's sound stopped coming: the game must not stay quiet for it
  clock += 1000;
  stream.update(true);
  await settle();
  assert.deepEqual([requests, stream.state], [2, "live"]);
  clock += 3001; // not a byte since
  stream.update(true);
  assert.equal(stream.state, "waiting");
  await settle();
  assert.equal(requests, 3);
  delete globalThis.AudioWorkletNode;
});
