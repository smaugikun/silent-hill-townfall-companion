import { $, clamp01, hash, norm180, rad, worldBearing } from "./util.js";
import { drawMonster, monsterFrame, monsterGait, monsterKind } from "./monsters.js";

// Metres. The game's CRTV loses an enemy's signal at 35 m outdoors.
export const SIGNAL_RANGE = 35;

const canvas = $("screen");
const ctx = canvas.getContext("2d");

// Static is drawn small and scaled up, so it stays coarse like on the in-game CRTV.
const noise = document.createElement("canvas");
noise.width = 130;
noise.height = 88;
const noiseCtx = noise.getContext("2d");
const noiseImage = noiseCtx.createImageData(noise.width, noise.height);

function drawNoise(level, tint) {
  const d = noiseImage.data;
  for (let i = 0; i < d.length; i += 4) {
    const v = Math.random() * 255;
    d[i] = tint ? v : v * 0.9;
    d[i + 1] = tint ? v * 0.25 : v;
    d[i + 2] = tint ? v * 0.25 : v * 0.92;
    d[i + 3] = 255;
  }
  noiseCtx.putImageData(noiseImage, 0, 0);
  ctx.save();
  ctx.imageSmoothingEnabled = false;
  ctx.globalAlpha = level;
  ctx.drawImage(noise, 0, 0, canvas.width, canvas.height);
  ctx.restore();
}

// Stand-in for the tuned-in look when the game's clip isn't available: rolling bands over a red wash.
function drawRedWash(now) {
  const w = canvas.width, h = canvas.height;
  ctx.fillStyle = "rgba(210,25,20,.18)";
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = "rgba(255,70,60,.12)";
  for (let i = 0; i < 3; i++) {
    const y = ((now / 12 + i * h / 3) % (h + 40)) - 20;
    ctx.fillRect(0, y, w, 16);
  }
}

// Tuned to a monster, the CRTV sees it: a dark figure where it stands, as through a camera pointing
// where the scanner points (the in-game CRTV draws its monsters live too; no clip has them). A monster
// off to the side shows as an arrow at that edge. The figures are monsters.js's.
const FOV = rad(45);             // the screen's width, as a camera's: zoomed in, like the game's
const EYE = 1.6, TALL = 1.9;     // metres: the scanner's height above the ground, a person's height
const MIN_HEIGHT = 0.5;          // of the screen for a person's height: however far, the figure stays plain to see
const PAD = 14;                  // room around the figure for its glow

const glowing = document.createElement("canvas"); // the figure with its glow, around the whole of it
const glowingCtx = glowing.getContext("2d");

// The figure torn into bands that slip sideways now and then, like a picture that won't hold. `phase`
// keeps each monster out of step with the others.
function drawSilhouette(frame, x, footY, height, now, phase) {
  glowing.width = Math.ceil(height * frame.w / frame.h) + 2 * PAD;
  glowing.height = Math.ceil(height) + 2 * PAD;
  glowingCtx.shadowColor = "rgba(255,40,30,.75)";
  glowingCtx.shadowBlur = 12;
  drawMonster(glowingCtx, frame, PAD, PAD, height, now, phase);

  const band = Math.max(3, Math.round(height / 40));
  const seed = Math.floor(now / 90);
  const left = x - glowing.width / 2, top = footY - glowing.height + PAD;
  ctx.save();
  ctx.globalAlpha = 0.8 + 0.15 * Math.sin(now / 70);
  for (let y = 0; y < glowing.height; y += band) {
    const r = hash(seed * 131 + y);
    const slip = r > 0.94 ? (r - 0.97) * 400 : (r - 0.5) * 4;
    ctx.drawImage(glowing, 0, y, glowing.width, band, left + slip, top + y, glowing.width, band);
  }
  ctx.restore();
}

// `pitch`: how far up the view looks (degrees, down negative): the horizon, and the monsters on it, move
// down as it looks up, as in the game's camera.
function drawMonsterView(monsters, heading, pitch, now) {
  const w = canvas.width, h = canvas.height;
  const focal = (w / 2) / Math.tan(FOV / 2);
  const horizon = h * 0.42 + focal * Math.tan(rad(Math.max(-60, Math.min(60, pitch))));
  ctx.fillStyle = "rgba(255,120,110,.95)";
  for (const {e, dx, dy, dist} of [...monsters].sort((a, b) => b.dist - a.dist)) {
    const angle = rad(norm180(worldBearing(dx, dy) - heading));
    if (Math.abs(angle) < FOV / 2 + 0.15) {
      const kind = monsterKind(e), frame = monsterFrame(kind, monsterGait(e, kind, now, dx, dy));
      if (!frame) continue; // its figure is still loading
      const depth = Math.max(0.8, dist * Math.cos(angle));
      const height = Math.min(h * 3, Math.max(h * MIN_HEIGHT * frame.metres / TALL, focal * frame.metres / depth));
      drawSilhouette(frame, w / 2 + focal * Math.tan(angle), horizon + height * EYE / frame.metres, height, now,
        [...String(e.id)].reduce((n, c) => n + c.charCodeAt(0), 0));
    } else {
      const edge = angle > 0 ? w - 30 : 30, tip = angle > 0 ? 26 : -26;
      ctx.beginPath();
      ctx.moveTo(edge + tip, horizon); ctx.lineTo(edge, horizon - 26); ctx.lineTo(edge, horizon + 26);
      ctx.closePath(); ctx.fill();
    }
  }
  ctx.font = "30px ui-monospace, Consolas, monospace";
  ctx.fillText(`${Math.round(monsters[0].dist)} m`, 16, h - 16);
}

// Until the dial is on a channel the screen is only static, which gets stronger as monsters and
// signals come closer, like on the in-game CRTV. No monsters. A playing clip (the game's video)
// shows through; a tuned monster stands in it, a waypoint is just its video. Switched on it is
// plainly snowing even with nothing near: fainter, a scanning CRTV looked switched off.
// `view` is from scannerView(); `monsters` are the tuned monsters' contacts(); `strongest` the strongest
// signal on it, 0..1; `heading` and `pitch` where the view looks.
export function drawScreen(view, monsters, strongest, heading, pitch, now, clipPlaying) {
  if (clipPlaying) {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
  } else {
    ctx.fillStyle = monsters.length ? "#1c0505" : "#0a0c0b";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    drawNoise(view.active ? 0.3 + 0.45 * strongest : 0.04, monsters.length > 0);
    if (monsters.length) drawRedWash(now);
  }
  if (monsters.length) drawMonsterView(monsters, heading, pitch, now);
}

// The fine-tune mini-game as the in-game CRTV shows it: the game's text on a blue box, and a yellow
// bar with the diamond to hit and the box running along it (positions 0..1 along the bar).
export function drawFineTune({box, zone, text}) {
  const w = canvas.width;
  const left = w * 0.1, right = w * 0.9, top = 262;
  ctx.fillStyle = "rgba(24,52,160,.85)";
  ctx.fillRect(left, top, right - left, 86);
  ctx.fillStyle = "#ffd84a";
  ctx.font = "bold 26px ui-monospace, Consolas, monospace";
  ctx.textAlign = "center";
  ctx.fillText(text || "FINE TUNE", w / 2, top + 32);
  ctx.textAlign = "start";

  const barLeft = left + 24, barRight = right - 24, barY = top + 62;
  const at = (v) => barLeft + clamp01(v) * (barRight - barLeft);
  ctx.strokeStyle = "#ffd84a";
  ctx.lineWidth = 4;
  ctx.beginPath(); ctx.moveTo(barLeft, barY); ctx.lineTo(barRight, barY); ctx.stroke();
  const zx = at(zone);
  ctx.beginPath();
  ctx.moveTo(zx, barY - 11); ctx.lineTo(zx + 11, barY); ctx.lineTo(zx, barY + 11); ctx.lineTo(zx - 11, barY);
  ctx.closePath(); ctx.fill();
  const bx = at(box);
  ctx.fillStyle = "#2f7d2c";
  ctx.fillRect(bx - 8, barY - 13, 16, 26);
  ctx.lineWidth = 3;
  ctx.strokeRect(bx - 8, barY - 13, 16, 26);
}
