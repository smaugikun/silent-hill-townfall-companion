// The game's CRTV screen, pushed by the bridge as each picture comes (/api/crtv/stream: a 4-byte big-endian length,
// then the JPEG, again and again). update() once a frame says whether it is wanted: it opens the stream, and again if
// it ends; draw() paints the latest picture, if one has come recently. Pictures that queue up while one is decoded
// are skipped for the newest.
const RETRY_MS = 500;
const STALE_MS = 3000; // an older picture is dropped: the game stopped sending
const STALL_MS = 5000; // not a byte for this long (the bridge ends a stream idle for 3 s): a connection Wi-Fi dropped
                       // without either end noticing, which would keep one of the browser's few: opened anew

export function createCrtvStream({request = fetch, decode = decodeFrame, now = () => performance.now()} = {}) {
  let wanted = false, held = false, image = null, receivedAt = 0, controller = null, retryAt = 0, bytesAt = 0;
  let arrivals = []; // when the pictures of the last second came, for the rate
  const clear = () => { image?.close?.(); image = null; };

  async function run(signal) {
    const response = await request("/api/crtv/stream", {cache: "no-store", signal});
    if (!response.ok || !response.body) throw new Error(`stream HTTP ${response.status}`);
    const reader = response.body.getReader();
    let buffer = new Uint8Array(0);
    for (;;) {
      const {done, value} = await reader.read();
      if (done || signal.aborted) return;
      bytesAt = now();
      buffer = joined(buffer, value);
      let newest = null;
      while (buffer.length >= 4) {
        const length = (buffer[0] << 24 | buffer[1] << 16 | buffer[2] << 8 | buffer[3]) >>> 0;
        if (buffer.length < 4 + length) break;
        newest = buffer.subarray(4, 4 + length);
        buffer = buffer.slice(4 + length);
      }
      if (!newest || held) continue; // held (the game paused): the picture there stays
      const next = await decode(new Blob([newest], {type: "image/jpeg"}));
      if (signal.aborted) { next.close?.(); return; }
      image?.close?.();
      image = next;
      receivedAt = now();
      arrivals = [...arrivals.filter((at) => receivedAt - at < 1000), receivedAt];
      stream.state = "live";
    }
  }

  const stream = {
    state: "off",
    update(on, pause = false) {
      held = pause;
      if (on !== wanted) {
        wanted = on;
        controller?.abort();
        controller = null;
        clear();
        retryAt = 0;
        stream.state = on ? "waiting" : "off";
      }
      if (controller && now() - bytesAt > STALL_MS) {
        controller.abort();
        controller = null;
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
        });
    },
    // Pictures shown in the last second.
    rate() {
      return arrivals.filter((at) => now() - at < 1000).length;
    },
    // Paints the picture over the whole canvas; false (nothing painted) without one.
    draw(context, width, height) {
      if (!wanted || !image || (!held && now() - receivedAt > STALE_MS)) return false;
      context.save();
      context.setTransform(1, 0, 0, 1, 0, 0);
      context.globalAlpha = 1;
      context.drawImage(image, 0, 0, width, height);
      context.restore();
      return true;
    },
  };
  return stream;
}

function joined(a, b) {
  if (!a.length) return b;
  const out = new Uint8Array(a.length + b.length);
  out.set(a);
  out.set(b, a.length);
  return out;
}

async function decodeFrame(blob) {
  if (typeof createImageBitmap === "function") return createImageBitmap(blob);
  const url = URL.createObjectURL(blob);
  try {
    const image = new Image();
    image.src = url;
    await image.decode();
    return image;
  } finally {
    URL.revokeObjectURL(url);
  }
}
