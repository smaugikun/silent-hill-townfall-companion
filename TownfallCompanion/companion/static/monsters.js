// The monsters as the CRTV sees them: an interpretation of each kind of monster in the game, 3D models made
// with Meshy and animated in Blender; the game's models are not included. Each plays from sheets of frames
// with these numbers: FRAMES frames, COLUMNS to a row, a loop at FPS, PX_PER_M pixels to the metre, the
// ground on each frame's bottom edge. A kind has a sheet standing (<kind>.webp), walking (<kind>-walk.webp)
// and, if it runs, running (<kind>-run.webp), and the walk and run seen from behind too, for a monster
// moving away (<kind>-walk-away.webp, <kind>-run-away.webp).

const FRAMES = 36, COLUMNS = 6, FPS = 12, PX_PER_M = 128;
const ROWS = Math.ceil(FRAMES / COLUMNS);

// The game's five kinds of monster, and those that also run. KINDS isn't needed to draw them: the tests
// (tests/test_package.py) check the sprite sheets and GAME_NAMES against it.
const KINDS = ["lunger", "sorrowful", "fallen", "widow", "weight"];
const RUNS = ["lunger", "widow"];

// The game's names for its monsters, found in an enemy's actor name or its CRTV clip, and the creature each
// shows as; the first that fits counts. A Lunger is the actor "Fearful_…" with the clip
// "Bink/Enraged_Focused"; that the clip "TheWall_Focused" is The Weight's is a guess. Any other monster shows
// as a Lunger, the most common one.
const GAME_NAMES = [
  ["fearful", "lunger"], ["enraged", "lunger"], ["lunger", "lunger"],
  ["sorrowful", "sorrowful"], ["fallen", "fallen"], ["widow", "widow"],
  ["weight", "weight"], ["wall", "weight"],
];

export function monsterKind(enemy) {
  const name = `${enemy.id ?? ""} ${enemy.video ?? ""}`.toLowerCase();
  return GAME_NAMES.find(([word]) => name.includes(word))?.[1] ?? "lunger";
}

// How a monster moves, from the positions the game sends (about 10 a second): `gait` "" standing, "walk"
// or "run", and `away` while it moves away from the player (dx, dy: where it is from the player, metres).
// Its velocity is eased over updates, and each change needs a clear margin, so it doesn't flicker between
// two strides, or between front and back while it crosses.
const WALK = [0.3, 0.5], RUN = [2.2, 2.8]; // m/s: [back down below, up above]
const AWAY = [0.1, 0.3];                    // how straight away from the player it heads: 1 straight, 0 across
const STILL_MS = 600;                       // no new position for this long: it stands
const motion = new Map();                   // enemy id -> {x, y, at, vx, vy, gait, away}

export function monsterGait(enemy, kind, now, dx, dy) {
  let m = motion.get(enemy.id);
  if (!m) {
    if (motion.size > 64) motion.clear();
    m = {x: enemy.x, y: enemy.y, at: now, vx: 0, vy: 0, gait: "", away: false};
    motion.set(enemy.id, m);
  } else if (enemy.x !== m.x || enemy.y !== m.y) {
    const seconds = Math.max(0.05, (now - m.at) / 1000);
    m.vx += ((enemy.x - m.x) / seconds - m.vx) * 0.5;
    m.vy += ((enemy.y - m.y) / seconds - m.vy) * 0.5;
    Object.assign(m, {x: enemy.x, y: enemy.y, at: now});
  } else if (now - m.at > STILL_MS) {
    m.vx = m.vy = 0;
  }
  const speed = Math.hypot(m.vx, m.vy);
  if (RUNS.includes(kind) && (speed > RUN[1] || m.gait === "run" && speed > RUN[0])) m.gait = "run";
  else if (speed > WALK[1] || m.gait !== "" && speed > WALK[0]) m.gait = "walk";
  else m.gait = "";
  const heading = (m.vx * dx + m.vy * dy) / ((speed || 1) * (Math.hypot(dx, dy) || 1));
  m.away = m.gait !== "" && (heading > AWAY[1] || m.away && heading > AWAY[0]);
  return {gait: m.gait, away: m.away};
}

// A sheet, loaded when it's first wanted: the phone holds only the ones it has needed.
const sheets = {};
function sheet(name) {
  if (!sheets[name]) {
    sheets[name] = new Image();
    sheets[name].src = `/monster/${name}.webp`;
  }
  const image = sheets[name];
  return image.complete && image.naturalWidth > 0 ? image : null;
}

// The sheet to draw a kind with as it moves (monsterGait): that one once loaded, meanwhile the same gait
// from the front, or the standing one; null while none is there. {image, w, h} (a frame's size in px) and
// metres (its height).
export function monsterFrame(kind, {gait = "", away = false} = {}) {
  const name = gait ? `${kind}-${gait}` : kind;
  const image = (away && gait ? sheet(`${name}-away`) : null) ?? sheet(name) ?? sheet(kind);
  if (!image) return null;
  const w = image.naturalWidth / COLUMNS, h = image.naturalHeight / ROWS;
  return {image, w, h, metres: h / PX_PER_M};
}

// Draws the frame for `now` (ms) into `c`, `height` px tall with its top left at (x, y). `seed` keeps each
// monster out of step with the others, and every other one is mirrored.
export function drawMonster(c, frame, x, y, height, now, seed) {
  const i = Math.floor(now / 1000 * FPS + seed) % FRAMES;
  const w = height * frame.w / frame.h;
  c.save();
  c.translate(x, y);
  if (seed % 2) {
    c.translate(w, 0);
    c.scale(-1, 1);
  }
  c.drawImage(frame.image, (i % COLUMNS) * frame.w, Math.floor(i / COLUMNS) * frame.h, frame.w, frame.h, 0, 0, w, height);
  c.restore();
}
