// HTTP/Safari fallback: schedule short PCM buffers in Web Audio's native renderer. The browser resamples them
// to the output device's rate. AudioWorklet remains the first choice when available at the stream's rate.
export function createScheduledOutput(audio, rate, channels, report = () => {}) {
  const target = Math.round(rate * .12), limit = Math.round(rate * .4), batch = Math.min(2048, Math.round(rate * .04));
  const outputs = Math.min(channels, 2), sources = new Set();
  let pending = new Float32Array(0), nextAt = 0, started = false, underruns = 0, dropped = 0;

  function stopSources() {
    for (const record of sources) {
      record.source.onended = null;
      try { record.source.stop(); } catch {}
      record.source.disconnect();
    }
    sources.clear();
  }
  const isPlaying = () => [...sources].some(({start, end}) => start <= audio.currentTime && audio.currentTime < end);
  function health() {
    const frames = pending.length / channels + Math.round(Math.max(0, nextAt - audio.currentTime) * rate);
    report({frames, underruns, dropped, playing: isPlaying()});
  }
  function reset() {
    stopSources();
    pending = new Float32Array(0);
    nextAt = 0;
    started = false;
    underruns = dropped = 0;
    health();
  }
  const output = {
    isPlaying,
    port: {postMessage(samples) {
      if (samples === "reset") { reset(); return; }
      const joined = new Float32Array(pending.length + samples.length);
      joined.set(pending);
      joined.set(samples, pending.length);
      pending = joined;
      const time = audio.currentTime;
      if (started && nextAt < time) { started = false; underruns++; }
      const frames = pending.length / channels + Math.max(0, nextAt - time) * rate;
      if (frames > limit) {
        dropped += Math.round(frames - Math.min(target, pending.length / channels));
        stopSources();
        pending = pending.slice(-target * channels);
        nextAt = time;
        started = false;
      }
      if (!started) {
        if (pending.length / channels < target) { health(); return; }
        nextAt = time + .01;
        started = true;
      }
      while (pending.length >= batch * channels) {
        const buffer = audio.createBuffer(outputs, batch, rate);
        for (let c = 0; c < outputs; c++) {
          const channel = buffer.getChannelData(c);
          for (let frame = 0; frame < batch; frame++) channel[frame] = pending[frame * channels + c];
        }
        pending = pending.slice(batch * channels);
        const source = audio.createBufferSource();
        source.buffer = buffer;
        source.connect(output.destination);
        const record = {source, start: nextAt, end: nextAt + batch / rate};
        sources.add(record);
        source.onended = () => { sources.delete(record); source.disconnect(); health(); };
        source.start(nextAt);
        nextAt = record.end;
      }
      health();
    }},
    connect(destination) { output.destination = destination; return destination; },
    disconnect() { reset(); output.destination = null; },
  };
  return output;
}
