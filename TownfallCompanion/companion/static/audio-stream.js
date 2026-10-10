// The game's sound, live (/api/audio/stream): "TFAU", the sample rate (uint32) and the channels (uint16), little-endian,
// then 16-bit samples, interleaved, as long as the stream lasts. They go to the AudioWorklet (audio-worklet.js), which
// plays them through a short buffer (audio-buffer.js). update() once a frame says whether the phone plays the sound:
// it opens the stream, and again if it ends or stalls; volume() sets how loud.
const RETRY_MS = 1000;
const STALL_MS = 3000;   // not a byte for this long: a connection Wi-Fi dropped, opened anew
const TARGET_S = 0.12;   // the buffer the sound plays behind: enough for Wi-Fi's unevenness, close to the picture
const LIMIT_S = 0.4;     // later than this, it catches up

export function parseHeader(bytes) {
  if (bytes.length < 10 || String.fromCharCode(...bytes.subarray(0, 4)) !== "TFAU") return null;
  const view = new DataView(bytes.buffer, bytes.byteOffset, 10);
  const rate = view.getUint32(4, true), channels = view.getUint16(8, true);
  return rate >= 8000 && rate <= 192000 && channels >= 1 && channels <= 8 ? {rate, channels} : null;
}

// 16-bit little-endian samples to floats; an odd byte at the end is kept for the next read.
export function toFloats(bytes) {
  const count = Math.floor(bytes.length / 2);
  const view = new DataView(bytes.buffer, bytes.byteOffset, count * 2);
  const samples = new Float32Array(count);
  for (let i = 0; i < count; ++i) samples[i] = view.getInt16(i * 2, true) / 32768;
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
  let format = null;

  // The audio graph for `rate`: made once, on a tap (browsers start sound only then), again if the rate changes.
  async function graph(rate, channels) {
    if (node && format?.rate === rate && format?.channels === channels) return node;
    const audio = context ?? new AudioContext({sampleRate: rate, latencyHint: "interactive"});
    await audio.audioWorklet.addModule(new URL("./audio-worklet.js", import.meta.url));
    node?.disconnect();
    node = new AudioWorkletNode(audio, "tfc-game-sound", {
      outputChannelCount: [Math.min(channels, 2)],
      processorOptions: {channels, target: Math.round(TARGET_S * rate), limit: Math.round(LIMIT_S * rate),
                         capacity: rate * 2},
    });
    node.port.onmessage = ({data}) => { stream.health = data; };
    gain = audio.createGain();
    gain.gain.value = loudness;
    node.connect(gain).connect(audio.destination);
    format = {rate, channels};
    stream.context = audio;
    return node;
  }

  async function run(signal) {
    const response = await request("/api/audio/stream", {cache: "no-store", signal});
    if (!response.ok || !response.body) throw new Error(`audio HTTP ${response.status}`);
    const reader = response.body.getReader();
    let buffer = new Uint8Array(0), player = null;
    for (;;) {
      const {done, value} = await reader.read();
      if (done || signal.aborted) return;
      bytesAt = now();
      buffer = joined(buffer, value);
      if (!player) {
        const header = parseHeader(buffer);
        if (!header) {
          if (buffer.length >= 10) throw new Error("not the game's sound");
          continue;
        }
        player = await graph(header.rate, header.channels);
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
  }

  const stream = {
    state: "off", // off | waiting | live | retrying
    health: null, // the worklet's last word: {frames, underruns, dropped, playing}
    context: null,
    update(on) {
      if (on !== wanted) {
        wanted = on;
        controller?.abort();
        controller = null;
        retryAt = 0;
        stream.state = on ? "waiting" : "off";
        if (!on) node?.port.postMessage("reset");
      }
      if (controller && now() - bytesAt > STALL_MS) {
        controller.abort();
        controller = null;
        stream.state = "waiting";
      }
      if (!wanted || controller || now() < retryAt) return;
      const current = controller = new AbortController();
      bytesAt = now();
      run(current.signal)
        .catch((error) => { if (error.name !== "AbortError") stream.state = "retrying"; })
        .finally(() => {
          if (controller !== current) return;
          controller = null;
          retryAt = now() + RETRY_MS;
          if (stream.state === "live") stream.state = "waiting"; // it ended: the game's sound stopped coming
        });
    },
    volume(value) {
      loudness = value;
      if (gain) gain.gain.value = value;
    },
    // After a tap: browsers keep sound suspended until the page is touched.
    resume() {
      return stream.context?.resume?.();
    },
  };
  return stream;
}
