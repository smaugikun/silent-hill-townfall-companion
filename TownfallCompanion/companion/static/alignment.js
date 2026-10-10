import { loadSetting, rad, saveSetting } from "./util.js";

// The phone's turns for the image stages of the game's fine-tune mini-game. As the alignment screen opens, the phone's
// pose is the neutral one, and from there its turn about its own axes counts: tilting the top towards you or away
// (tilt) and leaning it right or left (lean), however the phone is held, upright or tilted back like the CRTV in the
// character's hands. How far a turn moves the image depends on how fast it is, as gyro aiming does it: slow turns
// move it little, for holding it on the target, quick ones a lot, to cover the range without twisting the wrist far,
// and a resting hand's tremble and drift hardly at all. Light smoothing takes the jitter out. The totals go out with
// how many degrees move the image half its range at a moderate speed (the sensitivity), at most every SEND_MS, and
// the mod moves the image as far as they changed (tf_alignment.lua): tilted back it goes up, forward down, leaned right
// it goes right. `session` is new with every new start: the mod takes the image's position then as the totals' zero.
// Whether the tilt is on, or why not, goes out at once when it changes and every KEEPALIVE_MS, so the game's log tells
// when and why the phone stopped, and when it has gone quiet.
const SEND_MS = 50;
const KEEPALIVE_MS = 1000;
const TIMEOUT_MS = 1000;       // a request out longer than this no longer holds the next one back
const SMOOTHING_MS = 40;
const NEUTRAL_AFTER_OFF_MS = 1000; // off longer than this (the centre held, the sensor gone): a new neutral pose
const SPEED_WINDOW_MS = 150;   // the turning speed over this long: a tremble back and forth adds up to little
const PRECISION_DEG_S = 3;     // slower than this (a resting hand's drift) the image moves less and less
const SLOW_DEG_S = 10, SLOW_GAIN = 0.5; // from the one speed to the other the gain grows from the one to the other
const FAST_DEG_S = 60, FAST_GAIN = 2;
const RESTART_DEG = 60;        // totals settled beyond this start again from 0 (a new session): they stay in range

const SENSITIVITY_KEY = "tfc.alignSensitivity";
export const alignmentSettings = {sensitivity: sensitivityOf(loadSetting(SENSITIVITY_KEY, "2"))};

// 1 to 10: at a moderate speed the image moves half its range for a turn of 60 degrees at 1, 30 at 2, 15 at 4, 6 at 10.
export function setAlignmentSensitivity(value) {
  alignmentSettings.sensitivity = sensitivityOf(value);
  saveSetting(SENSITIVITY_KEY, alignmentSettings.sensitivity);
}

function sensitivityOf(value) {
  const n = Math.round(Number(value));
  return Number.isFinite(n) ? Math.min(10, Math.max(1, n)) : 2;
}

const degreesPerUnit = () => 60 / alignmentSettings.sensitivity;

// The phone's orientation from the sensor's angles (degrees; Z-X'-Y'', as deviceorientation gives them): the matrix
// taking the phone's own axes (x right, y up its screen, z out of the screen) into the world's.
function orientation([alpha, beta, gamma]) {
  const [ca, sa] = [Math.cos(rad(alpha)), Math.sin(rad(alpha))];
  const [cb, sb] = [Math.cos(rad(beta)), Math.sin(rad(beta))];
  const [cg, sg] = [Math.cos(rad(gamma)), Math.sin(rad(gamma))];
  return [
    [ca * cg - sa * sb * sg, -sa * cb, ca * sg + sa * sb * cg],
    [sa * cg + ca * sb * sg, ca * cb, sa * sg - ca * sb * cg],
    [-cb * sg, sb, cb * cg],
  ];
}

// How far the phone has turned since `neutral` (sensor angles, both), about the neutral pose's own axes, degrees:
// tilt positive with the top towards you, lean positive with the top to the right.
export function turnBetween(neutral, angles) {
  const a = orientation(neutral), b = orientation(angles);
  const r = [0, 1, 2].map((i) => [0, 1, 2].map((j) => a[0][i] * b[0][j] + a[1][i] * b[1][j] + a[2][i] * b[2][j]));
  const angle = Math.acos(Math.max(-1, Math.min(1, (r[0][0] + r[1][1] + r[2][2] - 1) / 2)));
  if (angle < 1e-9) return {tilt: 0, lean: 0};
  const scale = angle / (2 * Math.sin(angle)) * 180 / Math.PI;
  return {tilt: (r[2][1] - r[1][2]) * scale, lean: -(r[1][0] - r[0][1]) * scale};
}

// How much a turn at `speed` (degrees a second) counts.
export function gain(speed) {
  const between = Math.min(1, Math.max(0, (speed - SLOW_DEG_S) / (FAST_DEG_S - SLOW_DEG_S)));
  return (SLOW_GAIN + (FAST_GAIN - SLOW_GAIN) * between) * Math.min(1, speed / PRECISION_DEG_S);
}

const clamp = (v, limit) => Math.max(-limit, Math.min(limit, v));

// stats: requests sent, answered, refused (an error status) and lost (no answer), for the page's health notes.
export function createAlignment({send, now = () => performance.now(),
                                 session = Math.floor(Math.random() * 1e9) + 1} = {}) {
  let neutral = null, offSince = null, inStage = false, renew = true, resume = false;
  let last = null, recent = [], aim = {tilt: 0, lean: 0}; // the turn from neutral, lately; the totals it adds up to
  let tilt = 0, lean = 0, turnedAt = 0; // the totals smoothed
  let sentAt = -Infinity, sent = null, out = null, told = null;
  const stats = {sent: 0, answered: 0, refused: 0, lost: 0, status: null};

  function start(time) {
    aim = {tilt: 0, lean: 0};
    tilt = lean = 0;
    turnedAt = time;
    session++;
  }

  // The phone turned to `turn` (from neutral): the totals grow by that much, weighed by its speed. What it turned
  // while the tilt was off doesn't count.
  function follow(time, turn) {
    if (resume) {
      last = turn;
      recent = [];
      resume = false;
    }
    recent.push({time, ...turn});
    while (recent.length > 1 && recent[1].time <= time - SPEED_WINDOW_MS) recent.shift();
    const span = time - recent[0].time;
    const speed = span > 0 ? Math.hypot(turn.tilt - recent[0].tilt, turn.lean - recent[0].lean) * 1000 / span : 0;
    const g = gain(speed);
    aim.tilt += (turn.tilt - last.tilt) * g;
    aim.lean += (turn.lean - last.lean) * g;
    last = turn;
    const k = 1 - Math.exp(-(time - turnedAt) / SMOOTHING_MS);
    turnedAt = time;
    tilt += k * (aim.tilt - tilt);
    lean += k * (aim.lean - lean);
    const settled = Math.abs(aim.tilt - tilt) < .05 && Math.abs(aim.lean - lean) < .05;
    if (settled && Math.max(Math.abs(aim.tilt), Math.abs(aim.lean)) > RESTART_DEG) start(time);
  }

  function post(time, state) {
    const request = out = {at: time};
    const body = {type: "align", roll: clamp(lean, 180), pitch: clamp(tilt, 90), dpu: degreesPerUnit(), session, state};
    sentAt = time;
    sent = body;
    told = state;
    stats.sent++;
    Promise.resolve().then(() => send(body))
      .then((response) => {
        if (response?.ok) stats.answered++;
        else if (response) { stats.refused++; stats.status = response.status; } else stats.lost++;
      }, () => { stats.lost++; })
      .finally(() => { if (out === request) out = null; });
  }

  return {
    stats,
    // off: null while the phone moves the image, else why not (a few lowercase words). angles: the sensor's latest
    // [alpha, beta, gamma] (degrees), or null. imageStage: whether the game is in the mini-game's image stages.
    update(off, angles, imageStage) {
      const time = now();
      const state = off ?? "on";
      if (imageStage && !inStage) renew = true; // the alignment screen opened
      inStage = imageStage;
      if (off) {
        offSince ??= time;
        resume = true;
      } else {
        if (offSince != null && time - offSince > NEUTRAL_AFTER_OFF_MS) renew = true;
        offSince = null;
        if (angles && renew) {
          neutral = angles;
          last = {tilt: 0, lean: 0};
          recent = [{time, ...last}];
          start(time);
          renew = resume = false;
        } else if (angles && neutral) {
          follow(time, turnBetween(neutral, angles));
        }
      }
      const free = !out || time - out.at >= TIMEOUT_MS;
      const moved = !off && (!sent || Math.abs(clamp(lean, 180) - sent.roll) >= .05
                             || Math.abs(clamp(tilt, 90) - sent.pitch) >= .05 || sent.dpu !== degreesPerUnit());
      // A new state or start goes out at once: the mod logs the one and anchors the image to the other.
      if (state !== told || sent?.session !== session || (free && time - sentAt >= KEEPALIVE_MS)
          || (free && moved && time - sentAt >= SEND_MS)) {
        post(time, state);
      }
    },
  };
}
