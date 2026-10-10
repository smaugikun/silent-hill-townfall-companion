import { $ } from "./util.js";

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

function drawNoise(level) {
  const d = noiseImage.data;
  for (let i = 0; i < d.length; i += 4) {
    const v = Math.random() * 255;
    d[i] = v * 0.9;
    d[i + 1] = v;
    d[i + 2] = v * 0.92;
    d[i + 3] = 255;
  }
  noiseCtx.putImageData(noiseImage, 0, 0);
  ctx.save();
  ctx.imageSmoothingEnabled = false;
  ctx.globalAlpha = level;
  ctx.drawImage(noise, 0, 0, canvas.width, canvas.height);
  ctx.restore();
}

// The screen while the game's picture of its CRTV hasn't come (or the CRTV is down): a CRTV without a picture.
// Static, stronger as signals come closer, as on the in-game CRTV; faint while it is off; dark once the player died.
// `strongest` is the strongest signal, 0..1.
export function drawScreen(active, strongest, stopped = false) {
  ctx.fillStyle = stopped ? "#080908" : "#0a0c0b";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  if (!stopped) drawNoise(active ? 0.3 + 0.45 * strongest : 0.04);
}
