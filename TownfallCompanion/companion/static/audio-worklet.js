// The AudioWorklet that plays the game's sound (audio-stream.js posts it here as interleaved Float32 samples). Runs
// on the browser's audio thread.
import { FrameBuffer } from "./audio-buffer.js";

class GameSound extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.buffer = new FrameBuffer(options.processorOptions);
    this.port.onmessage = ({data}) => {
      if (data === "reset") this.buffer = new FrameBuffer(options.processorOptions);
      else this.buffer.push(data);
    };
    this.reportedAt = 0;
    this.wasPlaying = false;
  }

  process(inputs, outputs) {
    this.buffer.pull(outputs[0]);
    if (this.buffer.playing !== this.wasPlaying || currentTime - this.reportedAt >= 1) {
      this.wasPlaying = this.buffer.playing; // report startup/underrun at once, not a second after it happened
      this.reportedAt = currentTime;
      const {frames, underruns, dropped, playing} = this.buffer;
      this.port.postMessage({frames, underruns, dropped, playing});
    }
    return true;
  }
}

registerProcessor("tfc-game-sound", GameSound);
