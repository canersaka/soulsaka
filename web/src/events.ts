/** Live updates from GET /api/events with reconnect and catch-up through /api/sync. */

import { api, authState, requestRaw } from './api';
import { readLocal, writeLocal } from './auth';
import { parseJSON, readSSE } from './sse';
import {
  connection,
  corpusTick,
  deviceTick,
  mergeCaptures,
  mergeMemories,
  online,
  patchMemory,
  removeMemory,
  speakerTick,
  trainingTick,
  upsertCapture,
  upsertMemory,
} from './store';
import type { CaptureOut, MemoryOut } from './types';

const KEY_LAST_SYNC = 'soulsaka.lastSync';
const MIN_BACKOFF = 1_000;
const MAX_BACKOFF = 30_000;

// JSON boundary: event payloads are loosely typed on purpose.
type EventPayload = Record<string, unknown>;

let running = false;
let bound = false;
let stopped = false;
let controller: AbortController | null = null;
let backoff = MIN_BACKOFF;
let wake: (() => void) | null = null;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    const t = setTimeout(() => {
      wake = null;
      resolve();
    }, ms);
    wake = () => {
      clearTimeout(t);
      wake = null;
      resolve();
    };
  });
}

/** Reconnect immediately if we are waiting between attempts. */
export function pokeEvents(): void {
  if (wake) wake();
}

export async function catchUp(): Promise<void> {
  const since = readLocal(KEY_LAST_SYNC);
  const out = await api.sync(since);
  mergeMemories(out.memories);
  mergeCaptures(out.captures);
  writeLocal(KEY_LAST_SYNC, out.server_time);
}

function str(v: unknown): string | undefined {
  return typeof v === 'string' ? v : undefined;
}

function handleEvent(name: string, raw: string): void {
  const ev = parseJSON<EventPayload>(raw);
  if (!ev) return;
  switch (name) {
    case 'hello':
      connection.value = 'live';
      backoff = MIN_BACKOFF;
      void catchUp().catch(() => undefined);
      return;
    case 'capture': {
      const uid = str(ev.uid);
      if (!uid) return;
      const patch: Partial<CaptureOut> & { uid: string } = { uid };
      const status = str(ev.status);
      if (status) patch.status = status;
      if (typeof ev.text === 'string') patch.text = ev.text;
      if (Array.isArray(ev.memory_uids)) patch.memory_uids = ev.memory_uids.map(String);
      if (typeof ev.speaker_is_me === 'boolean' || ev.speaker_is_me === null) {
        patch.speaker_is_me = ev.speaker_is_me as boolean | null;
      }
      if (typeof ev.reason === 'string') patch.error = ev.reason;
      upsertCapture(patch);
      // The event is partial; fetch the full row so timestamps and metadata are right.
      void api
        .capture(uid)
        .then((full) => upsertCapture(full))
        .catch(() => undefined);
      if (status === 'done') corpusTick.value++;
      return;
    }
    case 'memory': {
      const uid = str(ev.uid);
      if (!uid) return;
      if (ev.deleted === true) {
        removeMemory(uid);
        return;
      }
      const patch: Partial<MemoryOut> = {};
      if (typeof ev.text === 'string') patch.text = ev.text;
      if (typeof ev.kind === 'string') patch.kind = ev.kind;
      if (typeof ev.archived === 'boolean') patch.archived = ev.archived;
      if (typeof ev.ts === 'string') patch.updated_at = ev.ts;
      patchMemory(uid, patch);
      void api
        .memory(uid)
        .then((full) => upsertMemory(full))
        .catch(() => undefined);
      return;
    }
    case 'corpus':
      corpusTick.value++;
      return;
    case 'device':
      deviceTick.value++;
      return;
    case 'speaker_profile':
      speakerTick.value++;
      return;
    case 'training':
      trainingTick.value++;
      return;
    default:
      return;
  }
}

async function connectOnce(): Promise<void> {
  controller = new AbortController();
  connection.value = connection.value === 'live' ? 'live' : 'connecting';
  const res = await requestRaw('/api/events', { signal: controller.signal });
  if (res.status === 401) {
    stopped = true;
    return;
  }
  if (!res.ok || !res.body) throw new Error(`events: ${res.status}`);
  await readSSE(res.body, handleEvent, controller.signal);
}

async function loop(): Promise<void> {
  while (!stopped) {
    try {
      await connectOnce();
    } catch {
      /* fall through to reconnect */
    }
    if (stopped) break;
    connection.value = 'down';
    await sleep(online.value ? backoff : MAX_BACKOFF);
    backoff = Math.min(backoff * 2, MAX_BACKOFF);
  }
  if (authState.value !== 'unauthorized') connection.value = 'down';
}

export function startEvents(): void {
  stopped = false;
  if (!bound) {
    bound = true;
    window.addEventListener('online', () => {
      backoff = MIN_BACKOFF;
      pokeEvents();
    });
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible' && connection.value !== 'live') {
        backoff = MIN_BACKOFF;
        pokeEvents();
      }
    });
  }
  if (running) return;
  running = true;
  backoff = MIN_BACKOFF;
  void loop().finally(() => {
    running = false;
  });
}

export function stopEvents(): void {
  stopped = true;
  controller?.abort();
  pokeEvents();
}
