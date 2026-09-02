/** Types mirroring src/soulsaka/models.py and the route return shapes. */

export type Register = 'text' | 'email' | 'speech' | 'doc';
export type CaptureKind = 'text' | 'audio';
export type CaptureOrigin = 'manual' | 'listener' | 'chat';
export type MemoryKind = 'note' | 'fact' | 'preference' | 'todo' | 'number' | 'event' | 'person';
export type ChatMode = 'assistant' | 'twin';

export const MEMORY_KINDS: readonly MemoryKind[] = [
  'note',
  'fact',
  'preference',
  'todo',
  'number',
  'event',
  'person',
];
export const REGISTERS: readonly Register[] = ['text', 'email', 'speech', 'doc'];

export interface CaptureOut {
  uid: string;
  device_uid: string;
  kind: CaptureKind;
  origin: CaptureOrigin;
  status: string;
  client_ts: string;
  received_at: string;
  processed_at?: string | null;
  text?: string | null;
  lang?: string | null;
  duration_s?: number | null;
  speaker_is_me?: boolean | null;
  speaker_score?: number | null;
  error?: string | null;
  memory_uids: string[];
}

export interface MemoryOut {
  uid: string;
  kind: string;
  text: string;
  source_kind: string;
  source_ref: string | null;
  confidence: number;
  created_at: string;
  updated_at: string;
  expires_at: string | null;
  archived: boolean;
  score?: number | null;
}

export interface SyncOut {
  server_time: string;
  memories: MemoryOut[];
  captures: CaptureOut[];
}

export interface RegisterStats {
  register: string;
  messages: number;
  words: number;
}
export interface SourceStats {
  kind: string;
  label: string;
  messages: number;
  words: number;
}
export interface MonthStats {
  month: string;
  words: number;
}
export interface StatsOut {
  me_words: number;
  me_messages: number;
  other_messages: number;
  conversations: number;
  memories: number;
  captures_pending: number;
  by_register: RegisterStats[];
  by_source: SourceStats[];
  by_lang: Record<string, number>;
  by_month: MonthStats[];
  first_train_threshold: number;
  comfortable_threshold: number;
  ready_for_first_train: boolean;
  latest_version: string | null;
}

export interface SourceOut {
  id: number;
  kind: string;
  label: string;
  locator: string;
  device_uid: string;
  created_at: string;
  last_import_at: string | null;
  messages: number;
  me_messages: number;
  me_words: number;
}

export interface ImportReport {
  source: { kind: string; label: string; locator: string };
  received: number;
  inserted: number;
  duplicates: number;
  skipped: number;
  skipped_reasons: Record<string, number>;
  me_words: number;
  conversations: number;
  notes: string[];
}

export interface DeviceOut {
  uid: string;
  name: string;
  kind: string;
  created_at: string;
  last_seen_at: string | null;
}

export interface PairResponse {
  device_uid: string;
  token: string;
}

export interface LLMProfile {
  name: string;
  backend: string;
  model: string;
  cloud: boolean;
  personal: boolean;
  enabled: boolean;
  default: boolean;
}

export interface ChatSummary {
  uid: string;
  title: string | null;
  created_at: string;
  updated_at: string;
  first_text: string | null;
}

export interface ChatTurn {
  role: string;
  text: string;
  profile: string | null;
  created_at: string;
}

export interface SpeakerStatus {
  enrolled: boolean;
  ready: boolean;
  n_samples?: number;
  min_samples?: number;
  threshold?: number;
  backend?: string;
  error?: string;
}

export interface JobRow {
  id: number;
  kind: string;
  status: string;
  attempts: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
}
export interface JobsOut {
  counts: Record<string, number>;
  recent: JobRow[];
}

export interface ConfigOut {
  me: { display_name: string; names: string[] };
  privacy: {
    other_speakers: string;
    keep_audio: boolean;
    allow_cloud_llm: boolean;
    allow_hosts: string[];
    keep_contact_names: boolean;
  };
  llm: { default: string; profiles: LLMProfile[] };
  speaker: { threshold: number; min_enroll_samples: number };
  asr: { backend: string; model: string };
  train: { base_model: string; backend: string };
}

export interface HealthOut {
  ok: boolean;
  version: string;
  devices: number;
  name: string | null;
}

export interface TrainingRun {
  version: string;
  backend: string;
  base_model: string;
  status: string;
  n_examples: number | null;
  n_words: number | null;
  data_cutoff: string | null;
  started_at: string | null;
  finished_at: string | null;
  metrics: Record<string, unknown> | null;
  error: string | null;
}

export interface DatasetSample {
  system: string;
  context: { role: string; text: string }[];
  target: string;
}
export interface DatasetPreview {
  n_examples: number;
  n_words: number;
  n_holdout: number;
  samples: DatasetSample[];
}

export interface EvalVersion {
  version: string;
  blind_accuracy: number | null;
  blind_n: number | null;
  discriminator_accuracy: number | null;
  voice_cosine: number | null;
  trained_at: string | null;
}
export interface EvalSummary {
  versions: EvalVersion[];
}

export interface EvalPair {
  uid: string;
  context: string | { role: string; text: string }[];
  first: string;
  second: string;
}
