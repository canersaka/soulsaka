/** Shared live state: captures, memories, the offline queue and connection status. */

import { signal } from '@preact/signals';
import type { CaptureOut, MemoryOut } from './types';

export type Connection = 'connecting' | 'live' | 'down';

export const captures = signal<CaptureOut[]>([]);
export const memories = signal<MemoryOut[]>([]);
export const connection = signal<Connection>('connecting');
export const online = signal<boolean>(typeof navigator === 'undefined' ? true : navigator.onLine);
/** Bumped by corpus/device/speaker events so pages can refetch. */
export const corpusTick = signal(0);
export const deviceTick = signal(0);
export const speakerTick = signal(0);

const byTimeDesc = (a: { client_ts: string }, b: { client_ts: string }): number =>
  a.client_ts < b.client_ts ? 1 : a.client_ts > b.client_ts ? -1 : 0;

export function upsertCapture(patch: Partial<CaptureOut> & { uid: string }): void {
  const list = captures.value;
  const idx = list.findIndex((c) => c.uid === patch.uid);
  if (idx === -1) {
    if (!patch.client_ts || !patch.kind) return; // partial event for a capture we never saw
    const full: CaptureOut = {
      device_uid: '',
      origin: 'manual',
      status: 'pending',
      received_at: patch.client_ts,
      memory_uids: [],
      ...patch,
      uid: patch.uid,
      kind: patch.kind,
      client_ts: patch.client_ts,
    };
    captures.value = [full, ...list].sort(byTimeDesc).slice(0, 200);
    return;
  }
  const prev = list[idx];
  if (!prev) return;
  const next = [...list];
  next[idx] = { ...prev, ...patch };
  captures.value = next.sort(byTimeDesc);
}

export function removeCapture(uid: string): void {
  captures.value = captures.value.filter((c) => c.uid !== uid);
}

export function mergeCaptures(items: CaptureOut[]): void {
  const map = new Map(captures.value.map((c) => [c.uid, c]));
  for (const c of items) map.set(c.uid, { ...map.get(c.uid), ...c });
  captures.value = [...map.values()].sort(byTimeDesc).slice(0, 200);
}

const byUpdatedDesc = (a: MemoryOut, b: MemoryOut): number =>
  a.updated_at < b.updated_at ? 1 : a.updated_at > b.updated_at ? -1 : 0;

export function upsertMemory(mem: MemoryOut): void {
  const list = memories.value;
  const idx = list.findIndex((m) => m.uid === mem.uid);
  const next = idx === -1 ? [mem, ...list] : list.map((m, i) => (i === idx ? { ...m, ...mem } : m));
  memories.value = next.sort(byUpdatedDesc);
}

export function patchMemory(uid: string, patch: Partial<MemoryOut>): boolean {
  const list = memories.value;
  const idx = list.findIndex((m) => m.uid === uid);
  if (idx === -1) return false;
  memories.value = list.map((m, i) => (i === idx ? { ...m, ...patch } : m)).sort(byUpdatedDesc);
  return true;
}

export function removeMemory(uid: string): void {
  memories.value = memories.value.filter((m) => m.uid !== uid);
}

export function mergeMemories(items: MemoryOut[]): void {
  const map = new Map(memories.value.map((m) => [m.uid, m]));
  for (const m of items) map.set(m.uid, { ...map.get(m.uid), ...m });
  memories.value = [...map.values()].sort(byUpdatedDesc);
}

export function memoryByUid(uid: string): MemoryOut | undefined {
  return memories.value.find((m) => m.uid === uid);
}

if (typeof window !== 'undefined') {
  window.addEventListener('online', () => (online.value = true));
  window.addEventListener('offline', () => (online.value = false));
}
