// The phone's heading and pitch (companion/static/util.js) without a browser: where the phone points stays put
// however far it is tilted, and how far up it points follows the back of the phone.
// Run by test_scanner.py, or directly: node --test tests/heading.test.mjs
import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

const { deviceHeading, devicePitch, norm180 } = await import(
  pathToFileURL(path.resolve(import.meta.dirname, "../TownfallCompanion/companion/static/util.js")).href);

// Sensor angles for a phone held upright facing `heading`, then tilted about its own x axis by `tilt` (the top
// towards you, the back up) and leaned about its own z axis by `lean` (the top to the right).
const rad = (d) => d * Math.PI / 180, deg = (r) => r * 180 / Math.PI;
const mul = (a, b) => a.map((row) => [0, 1, 2].map((j) => row.reduce((s, v, k) => s + v * b[k][j], 0)));
const rx = (d) => [[1, 0, 0], [0, Math.cos(rad(d)), -Math.sin(rad(d))], [0, Math.sin(rad(d)), Math.cos(rad(d))]];
const ry = (d) => [[Math.cos(rad(d)), 0, Math.sin(rad(d))], [0, 1, 0], [-Math.sin(rad(d)), 0, Math.cos(rad(d))]];
const rz = (d) => [[Math.cos(rad(d)), -Math.sin(rad(d)), 0], [Math.sin(rad(d)), Math.cos(rad(d)), 0], [0, 0, 1]];
const angles = (m) => [deg(Math.atan2(-m[0][1], m[1][1])), deg(Math.asin(m[2][1])), deg(Math.atan2(-m[2][0], m[2][2]))];
function held(heading, tilt = 0, lean = 0) {
  const upright = mul(mul(rz(360 - heading), rx(90)), ry(0));
  return angles(mul(mul(upright, rx(tilt)), rz(-lean)));
}

const close = (actual, expected, label) => assert.ok(Math.abs(norm180(actual - expected)) < 0.5, `${label}: ${actual} vs ${expected}`);

test("the heading stays put however far the phone is tilted back or forward, without a jump", () => {
  for (const heading of [0, 40, 135, 250]) {
    for (let tilt = -88; tilt <= 88; tilt += 4) {
      const [alpha, beta, gamma] = held(heading, tilt);
      close(deviceHeading(alpha, beta, gamma), heading, `heading ${heading}, tilted ${tilt}`);
    }
  }
});

test("leaning the phone sideways doesn't turn its heading much", () => {
  const [alpha, beta, gamma] = held(90, 20, 15);
  assert.ok(Math.abs(norm180(deviceHeading(alpha, beta, gamma) - 90)) < 10);
});

test("the pitch: level held upright, up tilted back, down tilted forward", () => {
  for (const tilt of [-60, -20, 0, 30, 70]) {
    const [, beta, gamma] = held(10, tilt);
    assert.ok(Math.abs(devicePitch(beta, gamma) - tilt) < 0.5, `tilted ${tilt}: ${devicePitch(beta, gamma)}`);
  }
});
