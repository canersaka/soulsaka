/** Push-to-talk recorder on MediaRecorder. */

import { S } from '../strings';

const CANDIDATES = [
  'audio/webm;codecs=opus',
  'audio/webm',
  'audio/mp4',
  'audio/ogg;codecs=opus',
  'audio/ogg',
];

export function pickMime(): string {
  if (typeof MediaRecorder === 'undefined') return '';
  for (const c of CANDIDATES) {
    try {
      if (MediaRecorder.isTypeSupported(c)) return c;
    } catch {
      /* some browsers throw on unknown types */
    }
  }
  return '';
}

export function recordingSupported(): boolean {
  return (
    typeof MediaRecorder !== 'undefined' &&
    typeof navigator !== 'undefined' &&
    !!navigator.mediaDevices?.getUserMedia
  );
}

export const MIC_CONSTRAINTS: MediaStreamConstraints = {
  audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
};

export function micErrorMessage(e: unknown): string {
  const name = e instanceof Error ? e.name : '';
  if (name === 'NotAllowedError' || name === 'SecurityError') return S.errors.micDenied;
  if (name === 'NotFoundError' || name === 'NotSupportedError') return S.errors.micUnsupported;
  return e instanceof Error && e.message ? e.message : S.errors.generic;
}

export interface Recording {
  blob: Blob;
  mime: string;
  durationS: number;
}

export class Recorder {
  private stream: MediaStream | null = null;
  private rec: MediaRecorder | null = null;
  private chunks: Blob[] = [];
  private startedAt = 0;

  get active(): boolean {
    return this.rec !== null && this.rec.state === 'recording';
  }

  async start(): Promise<void> {
    if (!recordingSupported()) throw new Error(S.errors.micUnsupported);
    this.stream = await navigator.mediaDevices.getUserMedia(MIC_CONSTRAINTS);
    const mime = pickMime();
    this.rec = new MediaRecorder(this.stream, mime ? { mimeType: mime } : undefined);
    this.chunks = [];
    this.rec.ondataavailable = (e: BlobEvent) => {
      if (e.data.size > 0) this.chunks.push(e.data);
    };
    this.rec.start(250);
    this.startedAt = performance.now();
  }

  stop(): Promise<Recording> {
    return new Promise((resolve, reject) => {
      const rec = this.rec;
      if (!rec) {
        reject(new Error('not recording'));
        return;
      }
      const finish = (): void => {
        const mime = rec.mimeType || pickMime() || 'audio/webm';
        const blob = new Blob(this.chunks, { type: mime.split(';')[0] });
        const durationS = (performance.now() - this.startedAt) / 1000;
        this.cleanup();
        resolve({ blob, mime: blob.type, durationS });
      };
      rec.onstop = finish;
      rec.onerror = () => finish();
      if (rec.state === 'inactive') finish();
      else rec.stop();
    });
  }

  cancel(): void {
    try {
      if (this.rec && this.rec.state !== 'inactive') this.rec.stop();
    } catch {
      /* ignore */
    }
    this.cleanup();
  }

  private cleanup(): void {
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    this.rec = null;
    this.chunks = [];
  }
}
