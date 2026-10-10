export const $ = (id) => document.getElementById(id);

export function norm360(v) { return ((v % 360) + 360) % 360; }
export function norm180(v) { const n = norm360(v); return n > 180 ? n - 360 : n; }
export function rad(v) { return v * Math.PI / 180; }
export function clamp01(v) { return Math.max(0, Math.min(1, Number(v) || 0)); }

// Phone frame: +Y = north/0deg, +X = east/90deg, bearings clockwise.
export function worldBearing(dx, dy) {
  return norm360(Math.atan2(dx, dy) * 180 / Math.PI);
}

// The heading (degrees, clockwise) of the way the phone points, from its orientation angles (W3C
// DeviceOrientation, intrinsic Z-X'-Y''). Held up like the CRTV that is the back of the phone; alpha alone
// swings wildly once the phone stands upright. The more the back points up or down, the more the top edge
// counts, the way it then points away from you: lying face up the top edge, tilted back until the back looks at
// the sky the bottom edge. So the heading stays put however far the phone is tilted, without a jump.
export function deviceHeading(alpha, beta, gamma) {
  const a = rad(alpha), b = rad(beta), g = rad(gamma);
  const ca = Math.cos(a), sa = Math.sin(a), cb = Math.cos(b), sb = Math.sin(b), cg = Math.cos(g), sg = Math.sin(g);
  const backX = -ca * sg - sa * sb * cg, backY = -sa * sg + ca * sb * cg, backUp = -cb * cg; // in east, north, up
  const topX = -sa * cb, topY = ca * cb;
  return worldBearing(backX - backUp * topX, backY - backUp * topY);
}

// How far up the back of the phone points (degrees, down negative), from the same angles: held upright
// it looks at the horizon, tilted back it looks up, lying face up it looks at the floor.
export function devicePitch(beta, gamma) {
  const up = -Math.cos(rad(beta)) * Math.cos(rad(gamma));
  return Math.asin(Math.max(-1, Math.min(1, up))) * 180 / Math.PI;
}

// The game's clock (the telemetry's `t`, seconds) on the phone's (performance.now(), ms). Samples reach
// the phone 50-200 ms late and unevenly; the quickest one so far was the least late, so its offset is
// kept. A clock that goes back (the game restarted) starts over.
export function createGameClock() {
  let offset = null, last = -Infinity;
  return {
    sample(t, arrivedAt) {
      if (t < last) offset = null;
      last = t;
      offset = offset == null ? arrivedAt - t * 1000 : Math.min(offset, arrivedAt - t * 1000);
    },
    // When game time t was, on the phone's clock; null before the first sample.
    phoneTime(t) { return offset == null || t == null ? null : t * 1000 + offset; },
  };
}

// Whether a time the game reports (its world clock, how far into a line) stands still, as it does in the
// game's pause menu: the same value in samples at least `holdMs` apart (the mod samples every 100 ms).
// `at` is when the game read it, on the phone's clock: the telemetry's clock runs on through a pause.
export function watchStillTime(holdMs = 150) {
  let value = null, since = null, still = false;
  return {
    update(time, at) {
      if (time == null || at == null) {
        value = since = null;
        return (still = false);
      }
      if (time !== value) {
        value = time;
        since = at;
        still = false;
      } else if (at - since >= holdMs) {
        still = true;
      }
      return still;
    },
  };
}

// To the bridge; a lost one is simply sent again on the next change. One that gets no answer is given up after
// REQUEST_TIMEOUT_MS: hanging on (Wi-Fi drops one now and then), it would keep one of the few connections the browser
// allows per address, and with all of them stuck nothing more goes out. The short answer is read whole, so the
// connection is free for the next one. While the page is hidden or closing, keepalive lets the last one (handing
// the game back) still go out. Resolves to the response, or undefined if there was none.
const REQUEST_TIMEOUT_MS = 2000;
function postJson(path, body) {
  return fetch(path, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
    keepalive: globalThis.document?.visibilityState === "hidden",
    signal: AbortSignal.timeout?.(REQUEST_TIMEOUT_MS),
  }).then(async (response) => { await response.arrayBuffer(); return response; }).catch(() => {});
}

export const postControl = (body) => postJson("/api/control", body);

// Settings survive a reload where the browser allows it. true/false are kept as "1"/"0".
export function loadSetting(key, fallback) {
  try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; }
}
export function loadFlag(key, fallback) {
  return loadSetting(key, fallback ? "1" : "0") === "1";
}
export function saveSetting(key, value) {
  try { localStorage.setItem(key, typeof value === "boolean" ? (value ? "1" : "0") : String(value)); } catch {}
}
