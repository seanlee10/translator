// Converts Float32 mono audio to Int16 PCM and posts chunks of 1024 samples
// (the actual duration depends on the AudioContext's negotiated sample
// rate: ~64ms at 16kHz, ~21ms at 48kHz).
const CHUNK_SAMPLES = 1024;

class PCMWorklet extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Int16Array(CHUNK_SAMPLES);
    this.offset = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;

    for (let i = 0; i < channel.length; i++) {
      const clamped = Math.max(-1, Math.min(1, channel[i]));
      this.buffer[this.offset++] =
        clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;

      if (this.offset === CHUNK_SAMPLES) {
        const out = this.buffer.slice();
        this.port.postMessage(out.buffer, [out.buffer]);
        this.offset = 0;
      }
    }
    return true;
  }
}

registerProcessor("pcm-worklet", PCMWorklet);
