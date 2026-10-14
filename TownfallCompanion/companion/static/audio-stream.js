// Live PCM from the game: AudioWorklet where the browser offers it (secure pages), native scheduled buffers elsewhere.
import { createScheduledOutput } from "./audio-output.js";

const RETRY_MS = 1000, STALL_MS = 3000, TARGET_S = .12, LIMIT_S = .4;
// "Remove the hum": the CRTV's static carries a steady hum at whole multiples of 50 Hz, loudest from 250 to 600 Hz
// and heard up to about 1300 Hz (measured from the game's sound). A narrow notch on each takes it out and leaves the
// rest; switched off, each notch becomes an allpass, which leaves the sound as it is.
const HUM_HZ = 50, HUM_FIRST = 100, HUM_LAST = 1300, HUM_Q = 40;

export function parseHeader(bytes) {
  if (bytes.length < 10 || String.fromCharCode(...bytes.subarray(0, 4)) !== "TFAU") return null;
  const view = new DataView(bytes.buffer, bytes.byteOffset, 10);
  const rate = view.getUint32(4, true), channels = view.getUint16(8, true);
  return rate >= 8000 && rate <= 192000 && channels >= 1 && channels <= 8 ? {rate, channels} : null;
}

export function toFloats(bytes) {
  const count = Math.floor(bytes.length / 2);
  const view = new DataView(bytes.buffer, bytes.byteOffset, count * 2);
  const samples = new Float32Array(count);
  for (let i = 0; i < count; i++) samples[i] = view.getInt16(i * 2, true) / 32768;
  return {samples, rest: bytes.subarray(count * 2)};
}

const joined = (a, b) => {
  if (!a.length) return b;
  const both = new Uint8Array(a.length + b.length);
  both.set(a);
  both.set(b, a.length);
  return both;
};

export function createAudioStream({request = fetch, now = () => performance.now(), context = null} = {}) {
  let wanted = false, controller = null, retryAt = 0, bytesAt = 0, node = null, gain = null, loudness = 1;
  let format = null, building = null, notches = [], dehum = true;

  function audioContext() {
    if (stream.context) return stream.context;
    const Constructor = globalThis.AudioContext ?? globalThis.webkitAudioContext;
    if (!Constructor) throw Object.assign(new Error("This browser cannot play live sound."), {unsupported: true});
    return (stream.context = new Constructor({latencyHint: "interactive"}));
  }

  async function makeGraph(rate, channels) {
    const audio = audioContext();
    let output;
    if (audio.audioWorklet && typeof globalThis.AudioWorkletNode === "function" && (!audio.sampleRate || audio.sampleRate === rate)) {
      try {
        await audio.audioWorklet.addModule(new URL("./audio-worklet.js", import.meta.url));
        output = new AudioWorkletNode(audio, "tfc-game-sound", {
          outputChannelCount: [Math.min(channels, 2)],
          processorOptions: {channels, target: Math.round(TARGET_S * rate), limit: Math.round(LIMIT_S * rate), capacity: rate * 2},
        });
        output.port.onmessage = ({data}) => { if (node === output) stream.health = data; };
      } catch {} // A missing/blocked worklet still has the HTTP-compatible output path.
    }
    if (!output) {
      if (!audio.createBuffer || !audio.createBufferSource) {
        throw Object.assign(new Error("This browser cannot play live sound."), {unsupported: true});
      }
      output = createScheduledOutput(audio, rate, channels, data => { if (node === output) stream.health = data; });
    }
    node?.port.postMessage("reset");
    node?.disconnect();
    for (const notch of notches) notch.disconnect?.();
    gain?.disconnect?.();
    gain = audio.createGain();
    gain.gain.value = loudness;
    notches = [];
    if (audio.createBiquadFilter) { // every current browser has it; without it, the sound as it is
      for (let hz = HUM_FIRST; hz <= HUM_LAST; hz += HUM_HZ) {
        const notch = audio.createBiquadFilter();
        notch.type = dehum ? "notch" : "allpass";
        notch.frequency.value = hz;
        notch.Q.value = HUM_Q;
        notches.push(notch);
      }
    }
    notches.reduce((from, notch) => from.connect(notch), output).connect(gain).connect(audio.destination);
    node = output;
    format = {rate, channels};
    stream.health = null;
    return node;
  }

  async function graph(rate, channels) {
    if (building) await building;
    if (node && format?.rate === rate && format?.channels === channels) return node;
    building = makeGraph(rate, channels);
    try { return await building; } finally { building = null; }
  }

  async function run(current) {
    const response = await request("/api/audio/stream", {cache: "no-store", signal: current.signal});
    if (!response.ok || !response.body) throw new Error("audio HTTP " + response.status);
    const reader = response.body.getReader();
    const cancel = () => { Promise.resolve(reader.cancel?.()).catch(() => {}); };
    current.signal.addEventListener("abort", cancel, {once: true});
    let buffer = new Uint8Array(0), player = null;
    const active = () => wanted && controller === current && !current.signal.aborted;
    try {
      while (active()) {
        const {done, value} = await reader.read();
        if (done || !active()) return;
        bytesAt = now();
        buffer = joined(buffer, value);
        if (!player) {
          const header = parseHeader(buffer);
          if (!header) {
            if (buffer.length >= 10) throw new Error("not the game's sound");
            continue;
          }
          player = await graph(header.rate, header.channels);
          if (!active()) return;
          player.port.postMessage("reset");
          buffer = buffer.subarray(10);
          stream.state = "live";
        }
        const frameBytes = 2 * format.channels;
        const whole = buffer.length - buffer.length % frameBytes;
        if (!whole) continue;
        const {samples} = toFloats(buffer.subarray(0, whole));
        buffer = buffer.slice(whole);
        player.port.postMessage(samples, [samples.buffer]);
      }
    } finally {
      current.signal.removeEventListener("abort", cancel);
      try { await reader.cancel?.(); } catch {}
      reader.releaseLock?.();
    }
  }

  const stream = {
    state: "off", health: null, context,
    playing() {
      return wanted && stream.state === "live" && stream.context?.state === "running"
        && (node?.isPlaying ? node.isPlaying() : stream.health?.playing === true);
    },
    resetOutput() {
      node?.port.postMessage("reset");
      stream.health = null;
    },
    update(on) {
      if (on !== wanted) {
        wanted = on;
        controller?.abort();
        controller = null;
        retryAt = 0;
        stream.state = on ? "waiting" : "off";
        if (!on) { node?.port.postMessage("reset"); stream.health = null; }
      }
      if (controller && now() - bytesAt > STALL_MS) {
        controller.abort();
        controller = null;
        stream.state = "waiting";
        node?.port.postMessage("reset");
        stream.health = null;
      }
      if (!wanted || controller || now() < retryAt || stream.state === "unsupported") return;
      const current = controller = new AbortController();
      bytesAt = now();
      run(current)
        .catch(error => {
          if (controller !== current || current.signal.aborted) return;
          stream.state = error.unsupported ? "unsupported" : "retrying";
        })
        .finally(() => {
          if (controller !== current) return;
          controller = null;
          retryAt = now() + RETRY_MS;
          node?.port.postMessage("reset");
          stream.health = null;
          if (stream.state === "live") stream.state = "waiting";
        });
    },
    volume(value) {
      loudness = Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : .8;
      if (gain) gain.gain.value = loudness;
    },
    removeHum(on) {
      dehum = Boolean(on);
      for (const notch of notches) notch.type = dehum ? "notch" : "allpass";
    },
    // Called directly in the tap handler: Safari needs creation/resume inside the user gesture, before fetch.
    resume() {
      try { return Promise.resolve(audioContext().resume?.()); }
      catch (error) { return Promise.reject(error); }
    },
  };
  return stream;
}
