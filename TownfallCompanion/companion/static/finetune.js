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

// Moves `distance` from `pos` in direction `dir`, bouncing off lo and hi.
function travel(pos, dir, distance, lo, hi) {
  if (hi - lo < 0.01) return {pos, dir};
  let x = pos + dir * distance;
  while (x > hi || x < lo) {
    if (x > hi) { x = 2 * hi - x; dir = -1; } else { x = 2 * lo - x; dir = 1; }
  }
  return {pos: x, dir};
}

export function createFollower() {
  let anchor = null;   // {pos, dir, time}: where the box was, going which way, at `time` (phone ms)
  let last = null;     // the update before: {box, time, step}
  let speed = 0, seenLo = Infinity, seenHi = -Infinity;

  // The ends: those seen, widened by the way the box goes in half the time between two updates.
  const lo = () => Math.max(0, seenLo - speed * UPDATE_MS / 2);
  const hi = () => Math.min(1, seenHi + speed * UPDATE_MS / 2);

  return {
    // An update: the box at `box` (0..1 of the bar), as the game read it at `time` (phone ms).
    sample(box, time) {
      seenLo = Math.min(seenLo, box);
      seenHi = Math.max(seenHi, box);
      let dir = anchor?.dir ?? 1;
      if (last && time > last.time) {
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
      return travel(anchor.pos, anchor.dir, speed * Math.max(0, now + LEAD_MS - anchor.time), lo(), hi()).pos;
    },
  };
}
