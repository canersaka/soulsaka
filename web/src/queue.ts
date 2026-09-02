/** Offline capture queue in IndexedDB, flushed to the hub whenever it is reachable. */

import { signal } from '@preact/signals';
import { ApiError, authState, request } from './api';
import { upsertCapture } from './store';
import type { CaptureOrigin, CaptureOut } from './types';

const DB_NAME = 'soulsaka';
const DB_VERSION = 1;
const STORE = 'queue';
const FLUSH_INTERVAL_MS = 30_000;

export interface QueuedCapture {
  uid: string;
  kind: 'text' | 'audio';
  origin: CaptureOrigin;
  client_ts: string;
  text?: string;
  blob?: Blob;
  mime?: string;
  created: number;
  attempts: number;
  error?: string;
}

export const queued = signal<QueuedCapture[]>([]);
export const flushing = signal(false);

export function newUid(): string {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID().replace(/-/g, '');
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

let dbPromise: Promise<IDBDatabase> | null = null;

function openDb(): Promise<IDBDatabase> {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve, reject) => {
    if (typeof indexedDB === 'undefined') {
      reject(new Error('IndexedDB unavailable'));
      return;
    }
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) db.createObjectStore(STORE, { keyPath: 'uid' });
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error ?? new Error('IndexedDB open failed'));
  });
  return dbPromise;
}

function tx<T>(mode: IDBTransactionMode, fn: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  return openDb().then(
    (db) =>
      new Promise<T>((resolve, reject) => {
        const t = db.transaction(STORE, mode);
        const req = fn(t.objectStore(STORE));
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error ?? new Error('IndexedDB request failed'));
      }),
  );
}

/** In-memory fallback when IndexedDB is not available (still works for the session). */
const memoryQueue = new Map<string, QueuedCapture>();
let useMemory = false;

async function putItem(item: QueuedCapture): Promise<void> {
  if (useMemory) {
    memoryQueue.set(item.uid, item);
    return;
  }
  try {
    await tx('readwrite', (s) => s.put(item));
  } catch {
    useMemory = true;
    memoryQueue.set(item.uid, item);
  }
}

async function deleteItem(uid: string): Promise<void> {
  if (useMemory) {
    memoryQueue.delete(uid);
    return;
  }
  try {
    await tx('readwrite', (s) => s.delete(uid));
  } catch {
    memoryQueue.delete(uid);
  }
}

async function allItems(): Promise<QueuedCapture[]> {
  if (useMemory) return [...memoryQueue.values()].sort((a, b) => a.created - b.created);
  try {
    const items = await tx<QueuedCapture[]>('readonly', (s) => s.getAll());
    return items.sort((a, b) => a.created - b.created);
  } catch {
    useMemory = true;
    return [...memoryQueue.values()].sort((a, b) => a.created - b.created);
  }
}

async function refresh(): Promise<void> {
  queued.value = await allItems();
}

export async function enqueueText(text: string, origin: CaptureOrigin = 'manual'): Promise<QueuedCapture> {
  const item: QueuedCapture = {
    uid: newUid(),
    kind: 'text',
    origin,
    client_ts: new Date().toISOString(),
    text,
    created: Date.now(),
    attempts: 0,
  };
  await putItem(item);
  await refresh();
  void flushQueue();
  return item;
}

export async function enqueueAudio(
  blob: Blob,
  mime: string,
  origin: CaptureOrigin,
  clientTs = new Date().toISOString(),
): Promise<QueuedCapture> {
  const item: QueuedCapture = {
    uid: newUid(),
    kind: 'audio',
    origin,
    client_ts: clientTs,
    blob,
    mime,
    created: Date.now(),
    attempts: 0,
  };
  await putItem(item);
  await refresh();
  void flushQueue();
  return item;
}

export async function dropQueued(uid: string): Promise<void> {
  await deleteItem(uid);
  await refresh();
}

export function extensionFor(mime: string | undefined): string {
  const m = (mime ?? '').toLowerCase();
  if (m.includes('webm')) return 'webm';
  if (m.includes('mp4') || m.includes('m4a') || m.includes('aac')) return 'mp4';
  if (m.includes('ogg') || m.includes('opus')) return 'ogg';
  if (m.includes('wav')) return 'wav';
  return 'bin';
}

async function upload(item: QueuedCapture): Promise<CaptureOut> {
  if (item.kind === 'text') {
    return request<CaptureOut>('/api/captures', {
      json: {
        uid: item.uid,
        kind: 'text',
        origin: item.origin,
        client_ts: item.client_ts,
        text: item.text ?? '',
      },
    });
  }
  const form = new FormData();
  const ext = extensionFor(item.mime);
  form.append('file', item.blob ?? new Blob(), `${item.uid}.${ext}`);
  form.append('uid', item.uid);
  form.append('client_ts', item.client_ts);
  form.append('origin', item.origin);
  form.append('meta', JSON.stringify({ mime: item.mime ?? '', client: 'web' }));
  return request<CaptureOut>('/api/captures/audio', { form });
}

const isPermanent = (e: ApiError): boolean =>
  e.status >= 400 && e.status < 500 && e.status !== 401 && e.status !== 408 && e.status !== 429;

let flushPromise: Promise<void> | null = null;

/** Upload everything in order. Single-flight; stops at the first transient failure. */
export function flushQueue(): Promise<void> {
  if (flushPromise) return flushPromise;
  flushPromise = (async () => {
    flushing.value = true;
    try {
      const items = await allItems();
      for (const item of items) {
        if (authState.value === 'unauthorized') break;
        try {
          const out = await upload(item);
          await deleteItem(item.uid);
          upsertCapture(out);
        } catch (e) {
          if (e instanceof ApiError && isPermanent(e)) {
            await deleteItem(item.uid);
            upsertCapture({
              uid: item.uid,
              kind: item.kind,
              origin: item.origin,
              client_ts: item.client_ts,
              received_at: item.client_ts,
              status: 'failed',
              text: item.text ?? null,
              error: e.detail,
              memory_uids: [],
              device_uid: '',
            });
            continue;
          }
          const msg = e instanceof Error ? e.message : String(e);
          await putItem({ ...item, attempts: item.attempts + 1, error: msg });
          break;
        }
      }
    } finally {
      await refresh();
      flushing.value = false;
      flushPromise = null;
    }
  })();
  return flushPromise;
}

let queueStarted = false;

export function startQueue(): void {
  if (queueStarted) return;
  queueStarted = true;
  void refresh().then(() => flushQueue());
  window.addEventListener('online', () => void flushQueue());
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') void flushQueue();
  });
  setInterval(() => void flushQueue(), FLUSH_INTERVAL_MS);
}
