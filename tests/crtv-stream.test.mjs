// The phone's reader of the pushed CRTV stream (companion/static/crtv-stream.js) without a browser: length-prefixed
// pictures split anywhere across chunks, the newest one taken, a stale picture dropped, the stream opened again.
// Run by test_scanner.py, or directly: node --test tests/crtv-stream.test.mjs
import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

const { createCrtvStream } = await import(
  pathToFileURL(path.resolve(import.meta.dirname, "../TownfallCompanion/companion/static/crtv-stream.js")).href);

const framed = (...pictures) => {
  const parts = pictures.map((text) => {
    const body = new TextEncoder().encode(text);
    const out = new Uint8Array(4 + body.length);
    new DataView(out.buffer).setUint32(0, body.length);
    out.set(body, 4);
    return out;
  });
  const all = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of parts) { all.set(p, at); at += p.length; }
  return all;
};

function harness(chunks) {
  let clock = 0, opened = 0;
  const decoded = [];
  const stream = createCrtvStream({
    now: () => clock,
    request: async () => {
      opened++;
      return {ok: true, body: new ReadableStream({start(c) { for (const chunk of chunks) c.enqueue(chunk); c.close(); }})};
    },
    decode: async (blob) => { const text = await blob.text(); decoded.push(text); return {text}; },
  });
  const painted = [];
  const context = {save() {}, restore() {}, setTransform() {}, drawImage: (image) => painted.push(image.text)};
  return {stream, decoded, painted, context, advance: (ms) => { clock += ms; }, opened: () => opened};
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 20));

test("pictures split across chunks are put together; of those that came together only the newest is decoded", async () => {
  const all = framed("first", "second", "third");
  const h = harness([all.subarray(0, 3), all.subarray(3, 14), all.subarray(14)]);
  h.stream.update(true);
  await settle();
  assert.equal(h.decoded.at(-1), "third");
  assert.ok(!h.decoded.includes("second"));
  assert.equal(h.stream.draw(h.context, 10, 10), true);
  assert.deepEqual(h.painted, ["third"]);
});

test("a picture older than 3 s isn't painted, and a stream that ended is opened again", async () => {
  const h = harness([framed("only")]);
  h.stream.update(true);
  await settle();
  assert.equal(h.opened(), 1);
  h.advance(3001);
  assert.equal(h.stream.draw(h.context, 10, 10), false);
  h.stream.update(true);
  await settle();
  assert.equal(h.opened(), 2);
});

test("a stream that goes silent without ending (a dropped connection) is given up after 5 s and opened anew", async () => {
  let clock = 0, opened = 0, aborted = 0;
  const stream = createCrtvStream({
    now: () => clock,
    request: async (url, {signal}) => {
      opened++;
      signal.addEventListener("abort", () => { aborted++; });
      return {ok: true, body: new ReadableStream({start(c) { c.enqueue(framed("one")); }})}; // never ends
    },
    decode: async (blob) => ({text: await blob.text()}),
  });
  stream.update(true);
  await settle();
  clock += 4000;
  stream.update(true);
  await settle();
  assert.deepEqual([opened, aborted], [1, 0], "quiet for 4 s: still waiting");
  clock += 1500;
  stream.update(true);
  await settle();
  assert.deepEqual([opened, aborted], [2, 1]);
});

test("not wanted: nothing painted, nothing opened", async () => {
  const h = harness([framed("x")]);
  h.stream.update(false);
  await settle();
  assert.equal(h.opened(), 0);
  assert.equal(h.stream.draw(h.context, 10, 10), false);
});
