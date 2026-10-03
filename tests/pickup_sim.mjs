// Drives static/pickup.js with synthetic devicemotion readings, as tests/test_pickup.py asks. Needs Node.js.
// Usage: node pickup_sim.mjs <path to pickup.js>   prints a JSON list of what Auto pickup would select.
import { pathToFileURL } from "node:url";

let t = 0;
Object.defineProperty(globalThis, "performance", { value: { now: () => t }, configurable: true });
let handler = null;
globalThis.window = { addEventListener: (type, fn) => { if (type === "devicemotion") handler = fn; } };
globalThis.DeviceMotionEvent = function DeviceMotionEvent() {};
globalThis.document = { visibilityState: "visible" };

const { pickup, onPickup, listenMotion, pickupPosition } = await import(pathToFileURL(process.argv[2]).href);
pickup.standAvOut = true; // "Set on a stand: AV OUT"
const seen = [];
onPickup((held, first, stand) => { if (!first) seen.push(pickupPosition(held, stand, pickup.standAvOut)); });
listenMotion();

// The phone `deg` from lying flat (gravity on its screen's normal), turning `rate` degrees a second.
function feed(ms, deg, rate = 0) {
  const g = 9.81, a = deg * Math.PI / 180;
  for (const end = t + ms; t < end; t += 16) {
    handler({ accelerationIncludingGravity: { x: 0, y: g * Math.sin(a), z: g * Math.cos(a) },
              rotationRate: { alpha: rate, beta: 0, gamma: 0 }, acceleration: { x: 0, y: 0, z: 0 } });
  }
}

const steps = {};
feed(500, 66);                 // taken out of the pocket, held up at an angle: the first reading
feed(2000, 66);                // set on a stand, 66 degrees from flat, perfectly still
steps.onStand = [...seen];
feed(500, 45, 30);             // lifted off the stand and moved about
steps.pickedUpFromStand = [...seen];
feed(300, 5);                  // laid flat on the table
feed(1500, 5);
steps.laidFlat = [...seen];
feed(600, 60, 20);             // picked up from flat
steps.pickedUpFromFlat = [...seen];
console.log(JSON.stringify(steps));
