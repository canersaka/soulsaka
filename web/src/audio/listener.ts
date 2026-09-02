/** Always-on listener: WebAudio level detection that cuts speech into WAV segments.
 *
 * An AnalyserNode drives the voice-activity gate (RMS in dBFS against a threshold with a
 * 300 ms hangover); a worklet (or ScriptProcessor fallback) taps raw PCM so each segment
 * carries 250 ms of pre-roll. Segments are capped at 30 s and uploaded as 16 kHz WAV.
 */

import { downsample, wavBlob } from './wav';
import { MIC_CONSTRAINTS } from './recorder';

export const HANGOVER_MS = 300;
export const PREROLL_MS = 250;
export const MAX_SEGMENT_MS = 30_000;
export const MIN_SPEECH_MS = 500;
const TICK_MS = 40;
const WORKLET_BATCH = 2048;

export interface SegmentInfo {
  durationS: number;
  startedAt: string;
}

export interface ListenerCallbacks {
  onLevel: (db: number, speaking: boolean) => void;
  onSegment: (blob: Blob, info: SegmentInfo) => void;
  onError: (e: unknown) => void;
}

const WORKLET_SOURCE = `
class SoulsakaTap extends AudioWorkletProcessor {
  constructor() { super(); this.buf = new Float32Array(${WORKLET_BATCH}); this.n = 0; }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) {
      for (let i = 0; i < ch.length; i++) {
        this.buf[this.n++] = ch[i];
        if (this.n === this.buf.length) { this.port.postMessage(this.buf); this.buf = new Float32Array(${WORKLET_BATCH}); this.n = 0; }
      }
    }
    return true;
  }
}
registerProcessor('soulsaka-tap', SoulsakaTap);
`;

export class Listener {
  thresholdDb: number;
  private cb: ListenerCallbacks;
  private ctx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private analyser: AnalyserNode | null = null;
  private tap: AudioNode | null = null;
  private timer = 0;
  private buf = new Float32Array(2048);
  private rate = 48000;
  private preroll: Float32Array[] = [];
  private prerollSamples = 0;
  private segment: Float32Array[] = [];
  private active = false;
  private lastVoice = 0;
  private speechStart = 0;
  private segmentStartedAt = '';

  constructor(thresholdDb: number, cb: ListenerCallbacks) {
    this.thresholdDb = thresholdDb;
    this.cb = cb;
  }

  get running(): boolean {
    return this.ctx !== null;
  }

  async start(): Promise<void> {
    if (this.ctx) return;
    this.stream = await navigator.mediaDevices.getUserMedia(MIC_CONSTRAINTS);
    const ctx = new AudioContext();
    this.ctx = ctx;
    if (ctx.state === 'suspended') await ctx.resume();
    this.rate = ctx.sampleRate;
    const source = ctx.createMediaStreamSource(this.stream);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 2048;
    analyser.smoothingTimeConstant = 0;
    this.analyser = analyser;
    this.buf = new Float32Array(analyser.fftSize);
    const sink = ctx.createGain();
    sink.gain.value = 0;
    sink.connect(ctx.destination);
    source.connect(analyser);
    analyser.connect(sink);
    this.tap = await this.makeTap(ctx);
    source.connect(this.tap);
    this.tap.connect(sink);
    this.reset();
    this.timer = window.setInterval(() => this.tick(), TICK_MS);
  }

  stop(): void {
    window.clearInterval(this.timer);
    this.timer = 0;
    if (this.active) this.finish();
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    this.tap?.disconnect();
    this.tap = null;
    this.analyser = null;
    const ctx = this.ctx;
    this.ctx = null;
    void ctx?.close().catch(() => undefined);
    this.reset();
  }

  private reset(): void {
    this.preroll = [];
    this.prerollSamples = 0;
    this.segment = [];
    this.active = false;
    this.lastVoice = 0;
    this.speechStart = 0;
  }

  private async makeTap(ctx: AudioContext): Promise<AudioNode> {
    if (typeof AudioWorkletNode !== 'undefined' && ctx.audioWorklet) {
      try {
        const url = URL.createObjectURL(new Blob([WORKLET_SOURCE], { type: 'application/javascript' }));
        await ctx.audioWorklet.addModule(url);
        URL.revokeObjectURL(url);
        const node = new AudioWorkletNode(ctx, 'soulsaka-tap', {
          numberOfInputs: 1,
          numberOfOutputs: 1,
          channelCount: 1,
        });
        node.port.onmessage = (e: MessageEvent<Float32Array>) => this.onPcm(e.data);
        return node;
      } catch {
        /* fall back below */
      }
    }
    const sp = ctx.createScriptProcessor(4096, 1, 1);
    sp.onaudioprocess = (e: AudioProcessingEvent) => {
      this.onPcm(new Float32Array(e.inputBuffer.getChannelData(0)));
    };
    return sp;
  }

  private onPcm(chunk: Float32Array): void {
    if (this.active) {
      this.segment.push(chunk);
      return;
    }
    this.preroll.push(chunk);
    this.prerollSamples += chunk.length;
    const keep = (PREROLL_MS / 1000) * this.rate;
    while (this.preroll.length > 1 && this.prerollSamples - (this.preroll[0]?.length ?? 0) >= keep) {
      const dropped = this.preroll.shift();
      this.prerollSamples -= dropped?.length ?? 0;
    }
  }

  private levelDb(): number {
    const a = this.analyser;
    if (!a) return -100;
    a.getFloatTimeDomainData(this.buf);
    let sum = 0;
    for (let i = 0; i < this.buf.length; i++) {
      const v = this.buf[i] ?? 0;
      sum += v * v;
    }
    const rms = Math.sqrt(sum / this.buf.length);
    return 20 * Math.log10(Math.max(rms, 1e-6));
  }

  private tick(): void {
    if (!this.ctx) return;
    const now = performance.now();
    const db = this.levelDb();
    const speaking = db >= this.thresholdDb;
    if (speaking) {
      this.lastVoice = now;
      if (!this.active) this.begin(now, true);
    } else if (this.active && now - this.lastVoice > HANGOVER_MS) {
      this.finish();
    }
    if (this.active && now - this.speechStart >= MAX_SEGMENT_MS) {
      this.finish();
      if (speaking) this.begin(now, false);
    }
    this.cb.onLevel(db, this.active);
  }

  private begin(now: number, withPreroll: boolean): void {
    this.active = true;
    this.speechStart = now;
    this.lastVoice = now;
    this.segment = withPreroll ? this.preroll.slice() : [];
    const prerollMs = withPreroll ? (this.prerollSamples / this.rate) * 1000 : 0;
    this.segmentStartedAt = new Date(Date.now() - prerollMs).toISOString();
    this.preroll = [];
    this.prerollSamples = 0;
  }

  private finish(): void {
    const speechMs = performance.now() - this.speechStart - HANGOVER_MS;
    const chunks = this.segment;
    this.segment = [];
    this.active = false;
    if (speechMs < MIN_SPEECH_MS || chunks.length === 0) return;
    try {
      const pcm = downsample(chunks, this.rate);
      const blob = wavBlob(pcm);
      this.cb.onSegment(blob, {
        durationS: pcm.length / 16000,
        startedAt: this.segmentStartedAt,
      });
    } catch (e) {
      this.cb.onError(e);
    }
  }
}
