// The phone's CRTV state machine (companion/static/scanner.js) without a browser: what it tells the game when the AV
// OUT / VIEW selector or "In VIEW, show the game's CRTV on the monitor" changes. Putting away what the phone switched
// on is the mod's (tf_crtv.lua, test_mod.py): the phone says its mode and switches the CRTV on. Run by
// test_scanner.py, or directly: node --test tests/scanner.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";
import { pathToFileURL } from "node:url";
import path from "node:path";

const SCANNER = path.resolve(import.meta.dirname, "../TownfallCompanion/companion/static/scanner.js");

// A page-less world: a clock the test moves, and the commands the page sends to the bridge.
let clock = 0;
let sent = [];
Object.defineProperty(globalThis, "performance", { value: { now: () => clock }, configurable: true, writable: true });
globalThis.fetch = async (url, init) => { sent.push(JSON.parse(init.body)); return { ok: true }; };
globalThis.localStorage = { getItem: () => null, setItem: () => {} };

let loads = 0;
async function phone({ raises = false } = {}) {
  clock = 0;
  sent = [];
  delete globalThis.document;
  const scanner = await import(`${pathToFileURL(SCANNER).href}?run=${loads++}`); // a fresh page each time
  if (raises) scanner.setRaises(true);
  sent = [];
  const world = {
    // The game's telemetry, ~10 times a second: the CRTV up or down; the phone's state follows it.
    crtv(active, ms = 0, frequency = 0, paused = false) {
      for (let spent = 0; spent === 0 || spent < ms; spent += 100) {
        scanner.onTelemetry({ crtv: { active, frequency }, cutscene: null }, paused);
        clock += 100;
      }
    },
    sent: () => sent.splice(0),
    commands: () => sent.splice(0).filter((c) => c.type === "crtv"),
  };
  return { scanner, world };
}

const raise = (animate) => ({ type: "crtv", active: true, animate, frequency: 0 });
const mode = (selector, onMonitor, miniGameShown = false) => ({ type: "mode", selector, onMonitor, miniGameShown });

test("VIEW says so first, then switches the CRTV on the way the setting says", async () => {
  for (const raises of [false, true]) {
    const { scanner, world } = await phone({ raises });
    scanner.setSelector("VIEW");
    assert.deepEqual(world.sent(), [mode("VIEW", raises), raise(raises)]);
  }
});

test("AV OUT says so and asks nothing of the CRTV: the mod puts away what the phone switched on", async () => {
  const { scanner, world } = await phone({ raises: true });
  scanner.setSelector("VIEW");
  world.crtv(true, 1000);
  world.sent();
  scanner.setSelector("AV_OUT");
  assert.deepEqual(world.sent(), [mode("AV_OUT", true)]);
  world.crtv(true, 3000);
  world.crtv(false, 3000);
  assert.deepEqual(world.sent(), []); // and nothing is switched on again in AV OUT
});

test("a CRTV that is up already is left as it is", async () => {
  const { scanner, world } = await phone();
  world.crtv(true, 500); // raised with the radio button
  scanner.setSelector("VIEW");
  assert.deepEqual(world.sent(), [mode("VIEW", false)]);
});

test("the setting is said at once, and the CRTV the mod put away comes on again the new way", async () => {
  for (const raises of [false, true]) {
    const { scanner, world } = await phone({ raises });
    scanner.setSelector("VIEW");
    world.crtv(true, 4000);
    world.sent();
    scanner.setRaises(!raises);
    assert.deepEqual(world.sent(), [mode("VIEW", !raises)]);
    world.crtv(false, 400);
    assert.deepEqual(world.sent(), []);                // the game's own state has to settle first
    world.crtv(false, 400);
    assert.deepEqual(world.commands(), [raise(!raises)]);
  }
});

test("put away in the game, the CRTV comes on again, not while the game is paused", async () => {
  const { scanner, world } = await phone();
  scanner.setSelector("VIEW");
  world.crtv(true, 4000);
  world.sent();
  world.crtv(false, 3000, 0, true); // the pause menu
  assert.deepEqual(world.commands(), []);
  world.crtv(false, 100);
  assert.deepEqual(world.commands(), [raise(false)]);
});

test("a hidden page tells the game AV OUT; in sight again, VIEW, and the CRTV comes on", async () => {
  const { scanner, world } = await phone();
  globalThis.document = { hidden: false };
  scanner.setSelector("VIEW");
  world.crtv(true, 1000);
  world.sent();
  document.hidden = true; // the screen off, or another app
  scanner.onVisibilityChange();
  assert.deepEqual(world.sent(), [mode("AV_OUT", false)]);
  world.crtv(false, 3000); // the mod put it away
  assert.deepEqual(world.sent(), []);
  document.hidden = false;
  scanner.onVisibilityChange();
  assert.deepEqual(world.sent(), [mode("VIEW", false), raise(false)]);
});

test("the mode is said again as it is, and leaving the page says AV OUT", async () => {
  const { scanner, world } = await phone({ raises: true });
  scanner.setSelector("VIEW");
  world.sent();
  scanner.sayMode();
  scanner.sayMode(true);
  assert.deepEqual(world.sent(), [mode("VIEW", true), mode("AV_OUT", true)]);
});

test("tuning switches a CRTV that is down on in VIEW the way the setting says; in AV OUT only the dial moves", async () => {
  for (const raises of [false, true]) {
    const { scanner, world } = await phone({ raises });
    scanner.setSelector("VIEW");
    world.crtv(false, 100);
    world.sent();
    scanner.tune(0.01);
    assert.deepEqual(world.commands().map(({ animate, active }) => [active, animate]), [[true, raises]]);
    scanner.setSelector("AV_OUT");
    world.sent();
    clock += 1000;
    scanner.tune(0.01);
    assert.deepEqual(world.commands().map(({ active }) => active), [undefined]);
  }
});

test("after a quick tune the needle follows the game's dial at once, not the phone's own from a moment before", async () => {
  const { scanner, world } = await phone();
  scanner.setSelector("VIEW");
  world.crtv(true, 1000, 0.5);
  world.sent();
  scanner.tune(0.01);                       // goes out at once
  scanner.tune(0.01);                       // a moment later: held back for SEND_MS
  world.crtv(true, 100, 0.5);
  assert.equal(scanner.control.dial, 0.52); // the game's telemetry is a step behind the phone's tuning
  scanner.pressFineTune("left");            // the game jumps to the next frequency
  world.crtv(true, 100, 0.15);
  assert.equal(scanner.control.dial, 0.15);
  await new Promise((resolve) => setTimeout(resolve, 150));
  assert.deepEqual(world.commands(), [{ type: "crtv", frequency: 0.51 }]); // the held-back one doesn't pull it back
});

test("the mini-game setting is said at once, and leaves the CRTV as it is", async () => {
  const { scanner, world } = await phone();
  scanner.setSelector("VIEW");
  world.crtv(true, 4000);
  world.sent();
  scanner.setMiniGameShown(true);
  assert.deepEqual(world.sent(), [mode("VIEW", false, true)]);
  world.crtv(true, 3000);
  assert.deepEqual(world.sent(), []);
});
