// The phone's turns for the mini-game's image stages (companion/static/alignment.js) without a browser: measured from
// the pose as the alignment screen opened, about the phone's own axes however it is held, weighed by their speed and
// lightly smoothed; sent at most every 50 ms and when they changed; whether the tilt is on goes out at once and every
// second.
// Run by test_scanner.py, or directly: node --test tests/alignment.test.mjs
import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

const { alignmentSettings, createAlignment, gain, setAlignmentSensitivity, turnBetween } = await import(
  pathToFileURL(path.resolve(import.meta.dirname, "../TownfallCompanion/companion/static/alignment.js")).href);

// Sensor angles (deviceorientation's alpha, beta, gamma) for a pose, and for that pose turned about the phone's own
// x (tilt, the top towards you) or z axis (the top to the left), so tests can hold the phone any way.
const rad = (d) => d * Math.PI / 180, deg = (r) => r * 180 / Math.PI;
const mul = (a, b) => a.map((row) => [0, 1, 2].map((j) => row.reduce((s, v, k) => s + v * b[k][j], 0)));
const rx = (d) => [[1, 0, 0], [0, Math.cos(rad(d)), -Math.sin(rad(d))], [0, Math.sin(rad(d)), Math.cos(rad(d))]];
const ry = (d) => [[Math.cos(rad(d)), 0, Math.sin(rad(d))], [0, 1, 0], [-Math.sin(rad(d)), 0, Math.cos(rad(d))]];
const rz = (d) => [[Math.cos(rad(d)), -Math.sin(rad(d)), 0], [Math.sin(rad(d)), Math.cos(rad(d)), 0], [0, 0, 1]];
const matrix = ([a, b, g]) => mul(mul(rz(a), rx(b)), ry(g));
const angles = (m) => [deg(Math.atan2(-m[0][1], m[1][1])), deg(Math.asin(m[2][1])), deg(Math.atan2(-m[2][0], m[2][2]))];
const turned = (pose, {tilt = 0, lean = 0}) => angles(mul(mul(matrix(pose), rx(tilt)), rz(-lean)));

const close = (actual, expected, label) => assert.ok(Math.abs(actual - expected) < 0.05, `${label}: ${actual} vs ${expected}`);

test("the turn is about the phone's own axes, however it is held", () => {
  for (const pose of [[0, 90, 0], [30, 40, 0], [-50, 20, 15], [10, 75, -30]]) { // upright, tilted back, nearly flat
    const back = turnBetween(pose, turned(pose, {tilt: 12}));
    close(back.tilt, 12, `tilt back from ${pose}`);
    close(back.lean, 0, `no lean from ${pose}`);
    const right = turnBetween(pose, turned(pose, {lean: 9}));
    close(right.lean, 9, `lean right from ${pose}`);
    close(right.tilt, 0, `no tilt from ${pose}`);
    close(turnBetween(pose, turned(pose, {tilt: -20})).tilt, -20, `tilt forward from ${pose}`);
  }
});

function harness({answer = () => Promise.resolve({ok: true})} = {}) {
  let clock = 0;
  const sent = [];
  const alignment = createAlignment({
    now: () => clock,
    session: 42,
    send: (body) => { sent.push(body); return answer(body); },
  });
  return {
    sent,
    stats: alignment.stats,
    // on: true, or false (the centre held), or why it is off
    async at(ms, on, pose, imageStage = true) {
      clock = ms;
      alignment.update(on === true ? null : on === false ? "centre held" : on, pose, imageStage);
      await new Promise((resolve) => setTimeout(resolve, 0));
    },
    // the same pose for a while: the smoothing settles
    async hold(from, ms, pose) {
      for (let t = from; t <= from + ms; t += 20) await this.at(t, true, pose);
    },
    // turning steadily from one turn (from `pose`) to another over `ms`
    async turn(from, ms, pose, start, end) {
      const [a, b] = [{...STILL, ...start}, {...STILL, ...end}];
      for (let t = from; t <= from + ms; t += 20) {
        const f = (t - from) / ms;
        await this.at(t, true, turned(pose, {tilt: a.tilt + f * (b.tilt - a.tilt), lean: a.lean + f * (b.lean - a.lean)}));
      }
    },
  };
}

const HELD = [20, 55, 5]; // tilted back, a little turned and leaned: how a hand holds it
const STILL = {tilt: 0, lean: 0};

test("the gain: next to nothing for a resting hand, low for slow turns, high for quick ones", () => {
  assert.equal(gain(0), 0);
  close(gain(1.5), 0.25, "drifting");
  close(gain(5), 0.5, "slow");
  close(gain(35), 1.25, "moderate");
  close(gain(100), 2, "quick");
});

test("the pose as the alignment screen opens is neutral; tilting back counts up, forward down, leaning right right",
     async () => {
  setAlignmentSensitivity(2);
  const h = harness();
  await h.at(0, true, HELD, false);
  await h.at(20, true, HELD, true); // the screen opened
  assert.equal(h.sent.at(-1).session, 44, "a neutral pose at the start and one as the screen opened");
  assert.deepEqual([h.sent.at(-1).roll, h.sent.at(-1).pitch, h.sent.at(-1).dpu], [0, 0, 30]);
  await h.hold(40, 400, turned(HELD, {tilt: 10}));
  assert.ok(h.sent.at(-1).pitch > 5, `back: ${h.sent.at(-1).pitch}`);
  await h.hold(460, 400, HELD);
  close(h.sent.at(-1).pitch, 0, "back as quickly as it went: where it started");
  await h.hold(880, 400, turned(HELD, {tilt: -10}));
  assert.ok(h.sent.at(-1).pitch < -5, `forward: ${h.sent.at(-1).pitch}`);
  await h.hold(1300, 400, turned(HELD, {lean: 6}));
  assert.ok(h.sent.at(-1).roll > 3, `leaned right: ${h.sent.at(-1).roll}`);
  close(h.sent.at(-1).pitch, 0, "upright again");
});

test("a quick turn moves the image four times as far as the same turn made slowly", async () => {
  const quick = harness(), slow = harness();
  await quick.at(0, true, HELD);
  await quick.hold(20, 400, turned(HELD, {tilt: 10}));
  await slow.at(0, true, HELD);
  await slow.turn(20, 2000, HELD, STILL, {tilt: 10}); // 5 degrees a second
  await slow.hold(2040, 400, turned(HELD, {tilt: 10}));
  close(quick.sent.at(-1).pitch, 20, "quick");
  close(slow.sent.at(-1).pitch, 5, "slow");
});

test("a resting hand's tremble and drift hardly move it, and a movement is smoothed", async () => {
  const h = harness();
  await h.at(0, true, HELD);
  for (let t = 20; t <= 3000; t += 20) { // trembling 0.5 degrees at 9 Hz, and sagging 1 degree a second
    const shake = 0.5 * Math.sin(2 * Math.PI * 9 * t / 1000);
    await h.at(t, true, turned(HELD, {tilt: shake - t / 1000, lean: shake}));
  }
  // 3 degrees of sag in all: well under 2 count, at the default sensitivity under 0.07 of the image's half range
  assert.ok(Math.abs(h.sent.at(-1).pitch) < 2 && Math.abs(h.sent.at(-1).roll) < 1,
            `held still: ${h.sent.at(-1).pitch}, ${h.sent.at(-1).roll}`);
  const before = h.sent.at(-1).pitch;
  await h.at(3020, true, turned(HELD, {tilt: 17}));
  const first = h.sent.at(-1).pitch - before;
  assert.ok(first > 0 && first < 30, `on its way, not there at once: ${first}`);
});

test("turned one way quickly and back slowly, the totals start again before they leave their range", async () => {
  const h = harness();
  await h.at(0, true, HELD);
  let t = 20;
  for (let round = 0; round < 4; round++) {
    await h.hold(t, 300, turned(HELD, {tilt: 40}));
    await h.turn(t + 320, 4000, HELD, {tilt: 40, lean: 0}, STILL); // 10 degrees a second
    t += 4340;
  }
  assert.ok(h.sent.every((body) => Math.abs(body.pitch) <= 90), "within what the companion takes");
  const starts = h.sent.filter((body, i) => i > 0 && body.session !== h.sent[i - 1].session);
  assert.ok(starts.length >= 2, `started again: ${starts.length}`);
  assert.ok(starts.every((body) => body.pitch === 0), "from 0, where the image is");
});

test("off for more than a second (the centre held), the pose then is neutral again; turns while off don't count",
     async () => {
  const h = harness();
  await h.at(0, true, HELD);
  await h.at(20, false, turned(HELD, {tilt: 30}));
  await h.at(500, true, turned(HELD, {tilt: 30}));
  assert.equal(h.sent.at(-1).session, 43, "a short hold keeps the neutral pose");
  assert.equal(h.sent.at(-1).pitch, 0, "and the image where it was");
  await h.at(520, false, turned(HELD, {tilt: 30}));
  await h.at(1600, true, turned(HELD, {tilt: 30}));
  assert.equal(h.sent.at(-1).session, 44);
  assert.equal(h.sent.at(-1).pitch, 0);
});

test("at most every 50 ms, and only when it turned", async () => {
  const h = harness();
  await h.at(0, true, HELD);
  await h.at(20, true, turned(HELD, {tilt: 10}));
  assert.equal(h.sent.length, 1, "too soon");
  await h.at(60, true, turned(HELD, {tilt: 10}));
  assert.equal(h.sent.length, 2);
  await h.hold(80, 300, turned(HELD, {tilt: 10}));
  const settled = h.sent.length;
  await h.at(400, true, turned(HELD, {tilt: 10}));
  assert.equal(h.sent.length, settled, "not turned");
});

test("a request that never answers holds the next one back for a second only", async () => {
  const h = harness({answer: () => new Promise(() => {})});
  await h.at(0, true, HELD);
  await h.at(60, true, turned(HELD, {tilt: 10}));
  await h.at(500, true, turned(HELD, {tilt: 20}));
  assert.equal(h.sent.length, 1);
  await h.at(1100, true, turned(HELD, {tilt: 25}));
  assert.equal(h.sent.length, 2);
});

test("refused and lost requests are counted", async () => {
  let answer = {ok: false, status: 400};
  const h = harness({answer: () => Promise.resolve(answer)});
  await h.at(0, true, HELD);
  answer = undefined; // postControl's answer when the request failed
  await h.at(60, true, turned(HELD, {tilt: 10}));
  assert.deepEqual({...h.stats}, {sent: 2, answered: 0, refused: 1, lost: 1, status: 400});
});

test("the state goes out again every second, turning or not, on or off", async () => {
  const h = harness();
  await h.at(0, true, HELD);
  await h.at(500, true, HELD);
  assert.equal(h.sent.length, 1);
  await h.at(1000, true, HELD);
  await h.at(1500, "motion sensor unchanged", HELD);
  await h.at(2600, "motion sensor unchanged", HELD);
  assert.deepEqual(h.sent.map((body) => body.state), ["on", "on", "motion sensor unchanged", "motion sensor unchanged"]);
});

test("why it is off goes out at once, even with a request out", async () => {
  const h = harness({answer: () => new Promise(() => {})});
  await h.at(0, true, HELD);
  await h.at(10, "game paused", HELD);
  await h.at(20, "game paused", HELD);
  await h.at(30, true, HELD);
  assert.deepEqual(h.sent.map((body) => body.state), ["on", "game paused", "on"]);
});

test("the sensitivity setting is sent along and stays within 1 to 10", async () => {
  setAlignmentSensitivity(10);
  const h = harness();
  await h.at(0, true, HELD);
  assert.equal(h.sent.at(-1).dpu, 6);
  setAlignmentSensitivity(1);
  await h.at(60, true, HELD);
  assert.equal(h.sent.at(-1).dpu, 60, "a new setting goes out at once");
  setAlignmentSensitivity(99);
  assert.equal(alignmentSettings.sensitivity, 10);
  setAlignmentSensitivity("x");
  assert.equal(alignmentSettings.sensitivity, 2);
});
