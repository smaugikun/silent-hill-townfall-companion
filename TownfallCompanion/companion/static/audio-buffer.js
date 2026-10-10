// The game's sound between its arrival from the bridge and the speaker (audio-worklet.js): frames (interleaved
// samples, one per channel) kept in a ring. Playing starts once `target` frames are in, so the uneven arrival over
// Wi-Fi doesn't cut it up; run dry, it is silent and starts again at `target`. More than `limit` frames in (the page
// was held back, or the game's clock runs a little faster than the phone's) drops the oldest down to `target`: the
// sound stays as late as the picture, not later.
export class FrameBuffer {
  constructor({channels = 2, target = 5760, limit = 19200, capacity = 96000} = {}) {
    this.channels = channels;
    this.target = target;
    this.limit = limit;
    this.ring = new Float32Array(capacity * channels);
    this.capacity = capacity;
    this.read = 0;      // frame positions, counting up for ever
    this.written = 0;
    this.playing = false;
    this.underruns = 0; // how often it ran dry while playing, for the page's status
    this.dropped = 0;   // frames dropped to catch up
  }

  get frames() {
    return this.written - this.read;
  }

  // Interleaved samples, a whole number of frames.
  push(samples) {
    const count = Math.floor(samples.length / this.channels);
    for (let frame = 0; frame < count; ++frame) {
      const at = ((this.written + frame) % this.capacity) * this.channels;
      for (let c = 0; c < this.channels; ++c) this.ring[at + c] = samples[frame * this.channels + c];
    }
    this.written += count;
    if (this.frames > Math.min(this.limit, this.capacity)) {
      const drop = this.frames - this.target;
      this.read += drop;
      this.dropped += drop;
    }
  }

  // Fills `outputs` (one Float32Array per channel, all as long) with the next frames, or silence.
  pull(outputs) {
    const length = outputs[0].length;
    if (!this.playing && this.frames >= this.target) this.playing = true;
    for (let frame = 0; frame < length; ++frame) {
      const have = this.playing && this.read < this.written;
      const at = (this.read % this.capacity) * this.channels;
      for (let c = 0; c < outputs.length; ++c) outputs[c][frame] = have ? this.ring[at + Math.min(c, this.channels - 1)] : 0;
      if (have) this.read++;
    }
    if (this.playing && this.read >= this.written) {
      this.playing = false;
      this.underruns++;
    }
  }
}
