/** Fetch wrapper: hub address, client header, bearer token, typed errors. */

import { signal } from '@preact/signals';
import { getHubUrl, getToken } from './auth';
import { S } from './strings';
import type {
  CaptureOut,
  ChatSummary,
  ChatTurn,
  ConfigOut,
  DatasetPreview,
  DeviceOut,
  EvalSummary,
  HealthOut,
  ImportReport,
  JobsOut,
  LLMProfile,
  MemoryKind,
  MemoryOut,
  PairResponse,
  SourceOut,
  SpeakerStatus,
  StatsOut,
  SyncOut,
  TrainingRun,
  VoiceReference,
  VoiceReferenceBuild,
} from './types';

export const CLIENT_HEADER = 'X-Soulsaka-Client';
export const CLIENT_NAME = 'web';

export type AuthState = 'unknown' | 'ok' | 'unauthorized';
export const authState = signal<AuthState>('unknown');
/** False after the last request failed at the network level. */
export const hubReachable = signal(true);

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  constructor(status: number, detail: string) {
    super(detail);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

export function isApiError(e: unknown, status?: number): e is ApiError {
  return e instanceof ApiError && (status === undefined || e.status === status);
}

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.detail || S.errors.generic;
  if (e instanceof Error && e.message) return e.message;
  return S.errors.generic;
}

export function apiUrl(path: string): string {
  const base = getHubUrl();
  return base ? `${base}${path}` : path;
}

export interface RequestOptions {
  method?: string;
  json?: unknown;
  form?: FormData;
  signal?: AbortSignal;
  /** Send the device token (default true). The blind-test page runs without one. */
  auth?: boolean;
}

function buildHeaders(withAuth: boolean): Headers {
  const h = new Headers();
  h.set(CLIENT_HEADER, CLIENT_NAME);
  if (withAuth) {
    const token = getToken();
    if (token) h.set('Authorization', `Bearer ${token}`);
  }
  return h;
}

export async function errorDetail(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    if (typeof body.detail === 'string') return body.detail;
    if (body.detail !== undefined) return JSON.stringify(body.detail);
  } catch {
    /* not JSON */
  }
  return `${res.status} ${res.statusText}`.trim();
}

/** Low-level request; returns the Response so callers can stream it. */
export async function requestRaw(path: string, opts: RequestOptions = {}): Promise<Response> {
  const withAuth = opts.auth !== false;
  const headers = buildHeaders(withAuth);
  let body: BodyInit | undefined;
  if (opts.json !== undefined) {
    headers.set('Content-Type', 'application/json');
    body = JSON.stringify(opts.json);
  } else if (opts.form) {
    body = opts.form;
  }
  let res: Response;
  try {
    res = await fetch(apiUrl(path), {
      method: opts.method ?? (body ? 'POST' : 'GET'),
      headers,
      body,
      signal: opts.signal,
      cache: 'no-store',
    });
  } catch (e) {
    if (opts.signal?.aborted) throw e;
    hubReachable.value = false;
    throw new ApiError(0, S.errors.network);
  }
  hubReachable.value = true;
  if (res.status === 401 && withAuth) authState.value = 'unauthorized';
  return res;
}

/** Like request(), but hands back the body as a Blob (audio, files). */
export async function requestBlob(path: string, opts: RequestOptions = {}): Promise<Blob> {
  const res = await requestRaw(path, opts);
  if (!res.ok) throw new ApiError(res.status, await errorDetail(res));
  return res.blob();
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const res = await requestRaw(path, opts);
  if (!res.ok) throw new ApiError(res.status, await errorDetail(res));
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const q = (params: Record<string, string | number | boolean | null | undefined>): string => {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : '';
};

/** Typed endpoints. Everything here sends the device token. */
export const api = {
  health: () => request<HealthOut>('/api/health', { auth: false }),
  me: () => request<DeviceOut>('/api/me'),
  pair: (code: string, name: string) =>
    request<PairResponse>('/api/pair', {
      json: { code: code.trim().toUpperCase(), name, kind: 'browser' },
      auth: false,
    }),
  config: () => request<ConfigOut>('/api/config'),

  devices: () => request<DeviceOut[]>('/api/devices'),
  revokeDevice: (uid: string) =>
    request<{ ok: boolean }>(`/api/devices/${encodeURIComponent(uid)}`, { method: 'DELETE' }),
  pairingCode: () =>
    request<{ code: string; ttl_s: number }>('/api/devices/pairing-code', {
      method: 'POST',
      json: {},
    }),

  captures: (limit = 50) => request<CaptureOut[]>(`/api/captures${q({ limit })}`),
  capture: (uid: string) => request<CaptureOut>(`/api/captures/${encodeURIComponent(uid)}`),
  retryCapture: (uid: string) =>
    request<{ ok: boolean }>(`/api/captures/${encodeURIComponent(uid)}/retry`, {
      method: 'POST',
      json: {},
    }),
  deleteCapture: (uid: string) =>
    request<{ ok: boolean }>(`/api/captures/${encodeURIComponent(uid)}`, { method: 'DELETE' }),

  memories: (params: { q?: string; include_archived?: boolean; limit?: number } = {}) =>
    request<MemoryOut[]>(`/api/memories${q({ limit: 500, ...params })}`),
  memory: (uid: string) => request<MemoryOut>(`/api/memories/${encodeURIComponent(uid)}`),
  createMemory: (text: string, kind: MemoryKind) =>
    request<MemoryOut>('/api/memories', { json: { text, kind } }),
  updateMemory: (uid: string, patch: { text?: string; kind?: MemoryKind; archived?: boolean }) =>
    request<MemoryOut>(`/api/memories/${encodeURIComponent(uid)}`, {
      method: 'PATCH',
      json: patch,
    }),
  deleteMemory: (uid: string) =>
    request<{ ok: boolean }>(`/api/memories/${encodeURIComponent(uid)}`, { method: 'DELETE' }),

  sync: (since: string | null) => request<SyncOut>(`/api/sync${q({ since })}`),

  stats: () => request<StatsOut>('/api/stats'),
  sources: () => request<SourceOut[]>('/api/sources'),
  deleteSource: (id: number) =>
    request<{ ok: boolean }>(`/api/sources/${id}`, { method: 'DELETE' }),
  importKinds: () => request<string[]>('/api/import/kinds'),
  importUpload: (file: File, kind: string) => {
    const form = new FormData();
    form.append('file', file, file.name);
    form.append('kind', kind);
    return request<ImportReport>('/api/import/upload', { form });
  },

  chats: () => request<ChatSummary[]>('/api/chats'),
  chatTurns: (uid: string) => request<ChatTurn[]>(`/api/chats/${encodeURIComponent(uid)}`),
  profiles: () => request<LLMProfile[]>('/api/llm/profiles'),

  speaker: () => request<SpeakerStatus>('/api/speaker'),
  voiceReference: () => request<VoiceReference>('/api/voice/reference'),
  buildVoiceReference: () =>
    request<VoiceReferenceBuild>('/api/voice/reference', { method: 'POST', json: {} }),
  voiceReferenceAudio: () => requestBlob('/api/voice/reference/audio'),
  speak: (text: string) => requestBlob('/api/voice/speak', { method: 'POST', json: { text } }),
  resetSpeaker: () => request<{ ok: boolean }>('/api/speaker', { method: 'DELETE' }),
  jobs: () => request<JobsOut>('/api/jobs'),

  trainingRuns: () => request<TrainingRun[]>('/api/training/runs'),
  datasetPreview: (n = 5) => request<DatasetPreview>(`/api/training/dataset/preview${q({ n })}`),
  startTraining: () => request<TrainingRun>('/api/training/runs', { method: 'POST', json: {} }),
  evalSummary: () => request<EvalSummary>('/api/eval/summary'),
};
