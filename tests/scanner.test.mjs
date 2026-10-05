// The phone's CRTV state machine (companion/static/scanner.js) without a browser: what it asks the game for
// when the AV OUT / VIEW selector or "In VIEW, show the game's CRTV on the monitor" changes. Run by
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
    commands: () => sent.splice(0).filter((c) => c.type === "crtv"),
  };
  return { scanner, world };
}

const raise = (animate) => ({ type: "crtv", active: true, animate, frequency: 0 });
const lower = (animate) => ({ type: "crtv", active: false, animate, frequency: 0 });

test("VIEW raises the CRTV the way the setting says", async () => {
  for (const raises of [false, true]) {
    const { scanner, world } = await phone({ raises });
    scanner.setSelector("VIEW");
    assert.deepEqual(world.commands(), [raise(raises)]);
  }
});

test("AV OUT lowers a CRTV the phone raised, the way it raised it", async () => {
  for (const raises of [false, true]) {
    const { scanner, world } = await phone({ raises });
    scanner.setSelector("VIEW");
    world.commands();
    world.crtv(true, 1000);
    scanner.setSelector("AV_OUT");
    assert.deepEqual(world.commands(), [lower(raises)]);
  }
});

test("a CRTV the player raised is not the phone's to lower", async () => {
  const { scanner, world } = await phone();
  world.crtv(true, 500);
  scanner.setSelector("VIEW");
  scanner.setRaises(true);
  scanner.setSelector("AV_OUT");
  world.crtv(true, 3000);
  assert.deepEqual(world.commands(), []);
});

test("showing it on the monitor while it is up silently: put away silently, raised with the animation", async () => {
  const { scanner, world } = await phone({ raises: false });
  scanner.setSelector("VIEW");
  world.commands();
  world.crtv(true, 1000);

  scanner.setRaises(true);
  assert.deepEqual(world.commands(), [lower(false)]); // the silent one comes down at once
  world.crtv(false, 400);
  assert.deepEqual(world.commands(), []);             // the game's own state has to settle first
  world.crtv(false, 400);
  assert.deepEqual(world.commands(), [raise(true)]);  // then Bill raises it: the monitor shows it
});

test("hiding it from the monitor while it is up: lowered with the animation, raised again silently", async () => {
  const { scanner, world } = await phone({ raises: true });
  scanner.setSelector("VIEW");
  world.commands();
  world.crtv(true, 1000);

  scanner.setRaises(false);
  assert.deepEqual(world.commands(), [lower(true)]);  // Bill puts it away
  world.crtv(false, 400);
  world.crtv(false, 400);
  assert.deepEqual(world.commands(), [raise(false)]); // and it is on again without showing
  world.crtv(true, 1000);
  scanner.setSelector("AV_OUT");
  assert.deepEqual(world.commands(), [lower(false)]); // lowered as it was last raised
});

test("turning the setting off and leaving VIEW at once still lowers it with the animation, never silently", async () => {
  const { scanner, world } = await phone({ raises: true });
  scanner.setSelector("VIEW");
  world.commands();
  world.crtv(true, 1000);

  scanner.setRaises(false);
  scanner.setSelector("AV_OUT");
  assert.deepEqual(world.commands(), [lower(true)]); // one request, the animated one: nothing flips it silently
  world.crtv(false, 3000);
  assert.deepEqual(world.commands(), []);             // and the phone leaves the game's CRTV down in AV OUT
});

test("back in VIEW before the game has put the CRTV away, the phone keeps it", async () => {
  const { scanner, world } = await phone({ raises: true });
  scanner.setSelector("VIEW");
  world.commands();
  world.crtv(true, 1000);

  scanner.setSelector("AV_OUT");
  assert.deepEqual(world.commands(), [lower(true)]);
  world.crtv(true, 500);   // the game hasn't lowered it yet
  scanner.setSelector("VIEW");
  world.crtv(true, 6000);
  assert.deepEqual(world.commands(), []);              // no more lowering, and nothing to raise
  scanner.setSelector("AV_OUT");
  assert.deepEqual(world.commands(), [lower(true)]);   // it is the phone's again: AV OUT puts it away
});

test("changing the setting changes nothing while the phone isn't holding the CRTV", async () => {
  const { scanner, world } = await phone();
  scanner.setRaises(true);
  scanner.setRaises(false);
  world.crtv(true, 1000);
  scanner.setSelector("AV_OUT");
  assert.deepEqual(world.commands(), []);
});

test("a lowering the game hasn't carried out is asked for again, not for ever, and not once it is down", async () => {
  const { scanner, world } = await phone({ raises: true });
  scanner.setSelector("VIEW");
  world.commands();
  world.crtv(true, 1000);

  scanner.setSelector("AV_OUT");
  assert.deepEqual(world.commands(), [lower(true)]);
  world.crtv(true, 1000);
  assert.deepEqual(world.commands(), []);              // still within its time
  world.crtv(true, 1000);
  assert.deepEqual(world.commands(), [lower(true)]);   // not down yet: again
  world.crtv(true, 6000);
  assert.deepEqual(world.commands(), [lower(true)]);   // once more...
  world.crtv(true, 6000);
  assert.deepEqual(world.commands(), []);              // ...and the game keeps its CRTV after that

  const second = await phone({ raises: true });
  second.scanner.setSelector("VIEW");
  second.world.commands();
  second.world.crtv(true, 1000);
  second.scanner.setSelector("AV_OUT");
  second.world.commands();
  second.world.crtv(false, 3000);                      // it came down
  assert.deepEqual(second.world.commands(), []);
});

test("a paused game can't lower anything: the phone asks again once it runs, with its asks intact", async () => {
  const { scanner, world } = await phone({ raises: true });
  scanner.setSelector("VIEW");
  world.commands();
  world.crtv(true, 1000);

  scanner.setSelector("AV_OUT");
  assert.deepEqual(world.commands(), [lower(true)]);
  world.crtv(true, 20000, 0, true);                    // the pause menu, for 20 s
  assert.deepEqual(world.commands(), []);
  world.crtv(true, 100);                               // back in the game
  assert.deepEqual(world.commands(), [lower(true)]);   // asked again at once...
  world.crtv(true, 2000);
  assert.deepEqual(world.commands(), [lower(true)]);   // ...and once more after that
});

test("a CRTV the player raises while the phone is lowering one is left alone", async () => {
  const { scanner, world } = await phone({ raises: true });
  scanner.setSelector("VIEW");
  world.commands();
  world.crtv(true, 1000);
  scanner.setSelector("AV_OUT");
  world.commands();
  world.crtv(false, 300);  // down for a moment...
  world.crtv(true, 4000);  // ...and the player raises it with the controller
  assert.deepEqual(world.commands(), []);
});

test("tuning raises a CRTV that is down in VIEW the way the setting says", async () => {
  for (const raises of [false, true]) {
    const { scanner, world } = await phone({ raises });
    scanner.setSelector("VIEW");
    world.commands();
    world.crtv(false, 100);
    scanner.tune(0.01);
    assert.deepEqual(world.commands().map(({ animate, active }) => [active, animate]), [[true, raises]]);
    world.crtv(true, 1000);
    scanner.setSelector("AV_OUT");
    assert.deepEqual(world.commands().map(({ animate, active }) => [active, animate]), [[false, raises]]);
  }
});
