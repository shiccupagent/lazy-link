// AudioWorklet: hands the mic's raw samples (channel 0) to the page while it is recording.
class Capture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.on = false;
    this.port.onmessage = (e) => { this.on = e.data; };
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (this.on && ch) this.port.postMessage(ch.slice(0));
    return true;
  }
}
registerProcessor('capture', Capture);
