// The fine-tune mini-game's box, drawn smoothly from telemetry that comes 10 times a second and at
// uneven moments. The box runs to and fro at a steady speed between two ends (so in the demo; how the
// game's moves isn't known): this runs the same motion, learning the ends and the speed from the
// updates, and restarts it from each update. Each update is placed by when the game read it (app.js
// maps the game's clock to the phone's), so its spacing is known however unevenly it arrives; taken
// at arrival, late updates made the box jump. It is drawn LEAD_MS ahead, about as long as a press on
// the F key takes to reach the game; a guess until measured in-game.
const LEAD_MS = 150;
const SPEED_SMOOTHING = 0.3;   // of each measured speed taken in
const MIN_STEP = 0.002;        // smaller moves between updates don't tell a direction
const UPDATE_MS = 100;         // how often the game sends; the box turns up to half of that past the ends seen
const MAX_AHEAD_MS = 500;      // the box is run on this far past the latest update at most: a stalled feed stops it (300 was too tight: jitter, the lead and a frame add up to it, and the box then froze and leapt)
const WRAP_STEP = 0.5;         // a move between two updates bigger than this is the box going round the bar
const MIN_SWEEP = 0.3;         // the box has to have crossed this much of the bar for its travel to count as known
const SANE_RANGE = 5;          // places further from the bar than this (in bars) are not the box

// Moves `distance` from `pos` in direction `dir`, bouncing off lo and hi, or, if the box goes round the bar
// instead of turning (`wraps`), coming out of the other end.
function travel(pos, dir, distance, lo, hi, wraps) {
  if (hi - lo < 0.01) return {pos, dir};
  let x = pos + dir * distance;
  if (wraps) {
    const span = hi - lo;
    return {pos: lo + (((x - lo) % span) + span) % span, dir};
  }
  while (x > hi || x < lo) {
    if (x > hi) { x = 2 * hi - x; dir = -1; } else { x = 2 * lo - x; dir = 1; }
  }
  return {pos: x, dir};
}

// The bar as the box really travels it. The game gives the box's and the diamond's places in its own units, and
// the bar's slot doesn't have to match the box's travel (UE4SS.log 2026-10-06: the box seemed to vanish half way
// along the bar and come out the other side, where positions beyond 0..1 had been folded back). Once the box has
// turned at both ends (or gone round the bar), the extremes it reached are the ends of the bar, for the box and the
// diamond alike, so their places against each other (what the timing is about) stay as the game has them. Before
// that its extremes so far only say how far past the bar's own 0..1 it has been (it would stand at the end it is
// heading for). What was learned stays for the next attempt: the bar doesn't change.
export function createCalibration() {
  let seenLo = Infinity, seenHi = -Infinity;
  let last = null, heading = 0, turns = 0;
  return {
    map(box, zone) {
      if (Math.abs(box) <= SANE_RANGE) {
        seenLo = Math.min(seenLo, box);
        seenHi = Math.max(seenHi, box);
        if (last != null) {
          const step = box - last;
          if (Math.abs(step) > WRAP_STEP) turns += 2; // round the bar: both ends were reached
          else if (Math.abs(step) >= MIN_STEP) {
            if (heading && Math.sign(step) !== heading) turns++;
            heading = Math.sign(step);
          }
        }
        last = box;
      }
      const known = turns >= 2 && seenHi - seenLo >= MIN_SWEEP;
      const from = known ? seenLo : Math.min(0, seenLo), to = known ? seenHi : Math.max(1, seenHi);
      const onBar = (v) => Math.max(0, Math.min(1, (v - from) / (to - from)));
      return {box: onBar(box), zone: onBar(zone)};
    },
    // The mini-game ended: the next update is no step from the last one. The ends learned stay.
    restart() { last = null; heading = 0; },
  };
}

export function createFollower() {
  let anchor = null;   // {pos, dir, time}: where the box was, going which way, at `time` (phone ms)
  let last = null;     // the update before: {box, time, step}
  let speed = 0, seenLo = Infinity, seenHi = -Infinity;
  let wraps = false;   // the box has been seen going round the bar rather than turning at its ends

  // The ends: those seen, widened by the way the box goes in half the time between two updates.
  const lo = () => Math.max(0, seenLo - speed * UPDATE_MS / 2);
  const hi = () => Math.min(1, seenHi + speed * UPDATE_MS / 2);

  return {
    // An update: the box at `box` (0..1 of the bar), as the game read it at `time` (phone ms).
    sample(box, time) {
      seenLo = Math.min(seenLo, box);
      seenHi = Math.max(seenHi, box);
      let dir = anchor?.dir ?? 1;
      if (last && time > last.time && Math.abs(box - last.box) > WRAP_STEP) {
        // Round the bar: it carries on the way it was going, and no speed is measured from the jump.
        wraps = true;
        dir = -Math.sign(box - last.box);
        last = {box, time, step: last.step};
      } else if (last && time > last.time) {
        const step = box - last.box;
        const moved = Math.abs(step) >= MIN_STEP;
        // A turn between the two updates spoils the measured speed; the direction is the new one.
        if (moved && last.step && Math.sign(step) === Math.sign(last.step)) {
          const measured = Math.abs(step) / (time - last.time);
          speed = speed ? speed + SPEED_SMOOTHING * (measured - speed) : measured;
        }
        if (moved) dir = Math.sign(step);
        last = {box, time, step: moved ? step : last.step};
      } else {
        last = {box, time, step: 0};
      }
      anchor = {pos: box, dir, time};
    },

    // Where to draw the box at `now` (phone ms), LEAD_MS ahead; null before any update.
    at(now) {
      if (!anchor) return null;
      const ahead = Math.min(MAX_AHEAD_MS, Math.max(0, now + LEAD_MS - anchor.time));
      return travel(anchor.pos, anchor.dir, speed * ahead, lo(), hi(), wraps).pos;
    },
  };
}
