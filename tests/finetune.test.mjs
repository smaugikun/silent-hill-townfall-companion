// The phone's fine-tune mini-game box (companion/static/finetune.js) without a browser: where the bar is, and how the
// box is run between the game's updates. Run by test_scanner.py, or directly: node --test tests/finetune.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";
import path from "node:path";
import { pathToFileURL } from "node:url";

const { createCalibration, createFollower } = await import(
  pathToFileURL(path.resolve(import.meta.dirname, "../TownfallCompanion/companion/static/finetune.js")).href);

const near = (actual, expected, tolerance = 1e-9) =>
  assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} is not ${expected} (within ${tolerance})`);

const pingPong = (i, lo, hi, period = 80) => lo + (hi - lo) * (1 - Math.abs(((i / period) % 2) - 1)); // one end to the other in `period` updates

test("positions the game gives beyond the bar are placed by the box's own travel, not folded back", () => {
  // The box really travels 0.2..1.8 in the bar's units (the bar's slot is only half as wide as its travel): it
  // used to vanish at 1.0 and come out of the other side.
  const calibration = createCalibration();
  const follower = createFollower();
  const shown = [];
  for (let i = 0; i <= 320; i++) {
    const {box} = calibration.map(pingPong(i, 0.2, 1.8), 1.0);
    follower.sample(box, i * 100);
    for (const extra of [0, 25, 50, 75]) shown.push({i, v: follower.at(i * 100 + extra)});
  }
  assert.ok(shown.every(({v}) => v >= 0 && v <= 1), "inside the bar");
  // Once it has turned at both ends (after about 160 updates) its places are the bar's: the ends reach the ends, the
  // middle of its travel is the middle of the bar, and it never jumps.
  near(calibration.map(0.2, 1.0).box, 0, 1e-9);
  near(calibration.map(1.8, 1.0).box, 1, 1e-9);
  near(calibration.map(1.0, 1.0).box, 0.5, 1e-9);
  const late = shown.filter(({i}) => i > 200);
  for (let k = 1; k < late.length; k++) assert.ok(Math.abs(late[k].v - late[k - 1].v) < 0.1, `a jump at update ${late[k].i}`);
});

test("the diamond keeps its place against the box when the bar is calibrated", () => {
  const calibration = createCalibration();
  for (let i = 0; i <= 200; i++) calibration.map(pingPong(i, 0.2, 1.8), 1.0);
  const here = calibration.map(0.6, 1.0);
  near(here.zone, 0.5, 1e-9);                          // the diamond at 1.0 of a travel from 0.2 to 1.8
  near(here.box, (0.6 - 0.2) / 1.6, 1e-9);
});

test("before it has turned, the bar's own 0..1 holds, widened by where the box has been", () => {
  const calibration = createCalibration();
  near(calibration.map(0.3, 0.5).box, 0.3);          // within the bar's own places
  near(calibration.map(0.35, 0.5).zone, 0.5);
  const beyond = calibration.map(1.4, 0.5);          // it goes past the bar: it stands at the end it is heading for, not folded
  near(beyond.box, 1);
});

test("what was learned stays for the next attempt", () => {
  const calibration = createCalibration();
  for (let i = 0; i <= 200; i++) calibration.map(pingPong(i, 0.2, 1.8), 1.0);
  calibration.restart();                             // the mini-game ended; the next one begins
  near(calibration.map(1.0, 1.0).box, 0.5, 1e-9);    // and the first update is placed right at once
  near(calibration.map(1.8, 1.0).box, 1, 1e-9);
});

test("a box that goes round the bar has both ends after one round", () => {
  const calibration = createCalibration();
  for (let i = 0; i <= 120; i++) calibration.map(0.1 + 1.4 * ((i / 80) % 1), 0.8);  // 0.1 .. 1.5, then round again
  near(calibration.map(0.1, 0.8).box, 0, 1e-9);
  near(calibration.map(1.5, 0.8).box, 1, 1e-9);
});

test("a place nowhere near the bar is not the box, and doesn't stretch it", () => {
  const calibration = createCalibration();
  calibration.map(0.5, 0.5);
  calibration.map(9999, 0.5);
  near(calibration.map(0.5, 0.5).box, 0.5);
  const {box, zone} = createCalibration().map(-0.4, 1.7);
  assert.deepEqual([box, zone], [0, 1]);             // places beyond the bar are held at its ends
});

test("a box that goes to and fro is run on between updates and turns at the ends", () => {
  const follower = createFollower();
  // 0.5 per second, bouncing between 0 and 1: a sample every 100 ms.
  const position = (ms) => 1 - Math.abs(((ms / 2000) % 2) - 1);
  for (let ms = 0; ms <= 4000; ms += 100) follower.sample(position(ms), ms);
  for (const ms of [4050, 4090]) {
    const drawn = follower.at(ms);
    assert.ok(drawn >= 0 && drawn <= 1);
    near(drawn, position(ms + 150), 0.1);
  }
});

test("a box that goes round the bar comes out of the other end instead of turning back", () => {
  const follower = createFollower();
  const position = (ms) => (ms / 2000) % 1; // a sawtooth: 0..1 in two seconds, then 0 again
  for (let ms = 0; ms <= 5000; ms += 100) follower.sample(position(ms), ms);
  // Just after a wrap (at 6000 ms the box is at 0): never drawn going back down from the end.
  for (let ms = 5000; ms <= 5990; ms += 10) {
    const drawn = follower.at(ms + 5);
    assert.ok(drawn >= 0 && drawn <= 1);
  }
  const before = [];
  for (let ms = 5900; ms <= 6100; ms += 20) before.push(follower.at(ms));
  // Between the last update (5000) and the next the drawn box carries on rising and is never drawn falling:
  const rising = before.filter((v, i) => i > 0 && v < before[i - 1] - 0.6).length; // the jump back to 0 is the wrap
  assert.ok(rising <= 1);
});

test("a feed that stalls stops the box instead of running it off along the bar", () => {
  const follower = createFollower();
  for (let ms = 0; ms <= 1000; ms += 100) follower.sample(ms / 2000, ms);
  const soon = follower.at(1100 + 100);
  const later = follower.at(1100 + 20000);
  near(later, follower.at(1100 + 400), 1e-9); // no further than a little past the last update
  assert.ok(Math.abs(later - soon) < 0.3);
});
