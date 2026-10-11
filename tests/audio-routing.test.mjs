import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

// Exercise the app's routing and event wiring with a phone whose timers can be suspended.
const app = readFileSync(new URL("../TownfallCompanion/companion/static/app.js", import.meta.url), "utf8");
const start = app.indexOf("// --- Sound only on the phone ---");
const end = app.indexOf("// --- Text on the CRT ---", start);
assert.ok(start >= 0 && end > start, "the app's audio routing section exists");

function phone() {
  const sent = [], listeners = new Map();
  let tick;
  const world = {
    control: {selector: "VIEW"},
    sound: {inView: true, muteGame: true, volume: 1},
    playing: true,
    soundPlaying: () => world.playing,
    bridgeOnline: true,
    state: {gameLive: true, player: {alive: true}},
    document: {
      visibilityState: "visible",
      addEventListener: (name, callback) => listeners.set(name, callback),
    },
    postControl: (body) => sent.push(JSON.parse(JSON.stringify(body))),
    setInterval: (callback, interval) => {
      assert.equal(interval, 2000);
      tick = callback;
    },
  };
  vm.runInNewContext(app.slice(start, end), world, {filename: "app.js (audio routing)"});
  return {
    world, sent,
    tick: () => tick(),
    visibility: (value) => {
      world.document.visibilityState = value;
      assert.ok(listeners.has("visibilitychange"), "audio listens for visibility changes");
      listeners.get("visibilitychange")();
    },
  };
}

test("a playing phone renews PC muting while visible", () => {
  const page = phone();
  page.tick();
  page.tick();
  assert.deepEqual(page.sent, [{type: "audio", muteGame: true}, {type: "audio", muteGame: true}]);
});

test("screen-off restores PC sound without another timer tick", () => {
  const page = phone();
  page.tick();
  page.visibility("hidden");
  assert.deepEqual(page.sent, [{type: "audio", muteGame: true}, {type: "audio", muteGame: false}]);
  page.tick();
  assert.equal(page.sent.length, 2, "a hidden phone does not renew the mute");
});

test("waking the phone only takes sound back from the PC once it plays again", () => {
  const page = phone();
  page.tick();
  page.visibility("hidden");
  page.world.playing = false;
  page.visibility("visible");
  page.tick();
  assert.equal(page.sent.length, 2, "PC stays audible while the phone waits for sound");
  page.world.playing = true;
  page.tick();
  assert.deepEqual(page.sent.at(-1), {type: "audio", muteGame: true});
});
