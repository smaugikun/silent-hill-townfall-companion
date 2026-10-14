import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../TownfallCompanion/companion/static/controls.js", import.meta.url), "utf8");
const body = source.slice(source.indexOf("function bindTuning("), source.indexOf("// Fine-tune inputs"));
function harness() {
  const events = new Map(), windowEvents = new Map(), documentEvents = new Map(), timers = new Map();
  let id = 0, steps = 0;
  const classes = new Set();
  const context = {
    STEP: .01, REPEAT_DELAY_MS: 100, REPEAT_MS: [100, 50], ACCEL_MS: 1000,
    performance: {now: () => 1000}, tune: () => { steps++; return true; }, controlHaptic() {},
    setTimeout: callback => { timers.set(++id, callback); return id; }, clearTimeout: id => timers.delete(id),
    window: {addEventListener: (name, fn) => windowEvents.set(name, fn)},
    document: {hidden: false, addEventListener: (name, fn) => documentEvents.set(name, fn)},
    hit: {addEventListener: (name, fn) => events.set(name, fn), setPointerCapture() {}},
    part: {classList: {add: name => classes.add(name), remove: name => classes.delete(name)}},
  };
  vm.runInNewContext(body + "\nbindTuning(hit, part, 1)", context);
  return {context, timers, classes, get steps() { return steps; },
    down: () => events.get("pointerdown")({pointerId: 1, preventDefault() {}}),
    blur: () => windowEvents.get("blur")(),
    hide: () => { context.document.hidden = true; documentEvents.get("visibilitychange")(); },
  };
}

test("tuning repeat stops on blur or screen hide even if the pointer release was lost", () => {
  for (const event of ["blur", "hide"]) {
    const world = harness(); world.down();
    const queued = [...world.timers.values()][0];
    world[event]();
    assert.equal(world.timers.size, 0);
    assert.equal(world.classes.has("pressed"), false);
    queued(); // a callback already dispatched before cancellation is also harmless
    assert.equal(world.steps, 0);
    assert.equal(world.timers.size, 0);
  }
});
