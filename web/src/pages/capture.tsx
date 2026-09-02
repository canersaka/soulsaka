import { signal } from '@preact/signals';
import type { JSX } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import { api, errorMessage } from '../api';
import { readLocal, writeLocal } from '../auth';
import { Listener } from '../audio/listener';
import { Recorder, micErrorMessage, recordingSupported } from '../audio/recorder';
import { Icon } from '../components/icon';
import { Chip, ErrorNote, Switch, autoGrow, useNow } from '../components/ui';
import { fmtDuration, relTime, truncate } from '../format';
import { dropQueued, enqueueAudio, enqueueText, queued, type QueuedCapture } from '../queue';
import { onLinkClick } from '../router';
import { captures, memories, mergeCaptures, mergeMemories, removeCapture, upsertCapture } from '../store';
import { S } from '../strings';
import type { CaptureOut } from '../types';

const KEY_THRESHOLD = 'soulsaka.vadThreshold';
const DEFAULT_THRESHOLD_DB = -42;
const TAP_MAX_MS = 350;

// Listener state lives at module level so it survives navigation while the mic stays open.
const listenerOn = signal(false);
const levelDb = signal(-100);
const speaking = signal(false);
const segmentsSent = signal(0);
const listenerError = signal<string | null>(null);
let listener: Listener | null = null;

function readThreshold(): number {
  const v = Number(readLocal(KEY_THRESHOLD));
  return Number.isFinite(v) && v !== 0 ? v : DEFAULT_THRESHOLD_DB;
}

async function toggleListener(on: boolean): Promise<void> {
  listenerError.value = null;
  if (!on) {
    listener?.stop();
    listener = null;
    listenerOn.value = false;
    speaking.value = false;
    levelDb.value = -100;
    return;
  }
  const l = new Listener(readThreshold(), {
    onLevel: (db, active) => {
      levelDb.value = db;
      speaking.value = active;
    },
    onSegment: (blob, info) => {
      segmentsSent.value++;
      void enqueueAudio(blob, 'audio/wav', 'listener', info.startedAt);
    },
    onError: (e) => {
      listenerError.value = errorMessage(e);
    },
  });
  try {
    await l.start();
    listener = l;
    listenerOn.value = true;
  } catch (e) {
    listenerError.value = micErrorMessage(e);
    listenerOn.value = false;
  }
}

export function CapturePage(): JSX.Element {
  const [loadError, setLoadError] = useState<unknown>(null);
  useEffect(() => {
    api.captures(50).then(mergeCaptures, setLoadError);
    if (memories.value.length === 0) api.memories().then(mergeMemories, () => undefined);
  }, []);
  return (
    <div class="page">
      <header class="page-head">
        <h1 class="page-title">{S.capture.title}</h1>
        <p class="page-sub">{S.capture.sub}</p>
      </header>
      <section class="card stack">
        <TextComposer />
        <div class="grid-2" style="align-items:start">
          <PushToTalk />
          <AlwaysListening />
        </div>
      </section>
      <section class="stack-sm">
        <h2 class="section-title">{S.capture.recent}</h2>
        {loadError ? <ErrorNote error={loadError} /> : null}
        <CaptureList />
      </section>
    </div>
  );
}

function TextComposer(): JSX.Element {
  const [text, setText] = useState('');
  const ref = useRef<HTMLTextAreaElement>(null);
  const send = async (): Promise<void> => {
    const t = text.trim();
    if (!t) return;
    setText('');
    if (ref.current) {
      ref.current.value = '';
      autoGrow(ref.current);
    }
    await enqueueText(t, 'manual');
  };
  return (
    <div class="composer">
      <textarea
        ref={ref}
        class="textarea"
        rows={1}
        placeholder={S.capture.textPlaceholder}
        value={text}
        aria-label={S.capture.title}
        onInput={(e) => {
          setText(e.currentTarget.value);
          autoGrow(e.currentTarget);
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
            const coarse = window.matchMedia('(pointer: coarse)').matches;
            if (!coarse || e.metaKey || e.ctrlKey) {
              e.preventDefault();
              void send();
            }
          }
        }}
      />
      <button
        class="btn btn-primary"
        type="button"
        disabled={!text.trim()}
        onClick={() => void send()}
        aria-label={S.capture.send}
      >
        <Icon name="send" size={18} />
        <span>{S.capture.send}</span>
      </button>
    </div>
  );
}

function PushToTalk(): JSX.Element {
  const [recording, setRecording] = useState(false);
  const [toggleMode, setToggleMode] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const rec = useRef<Recorder | null>(null);
  const downAt = useRef(0);
  const timer = useRef(0);
  const supported = recordingSupported();

  const start = async (): Promise<void> => {
    if (rec.current) return;
    setError(null);
    const r = new Recorder();
    rec.current = r;
    try {
      await r.start();
      setRecording(true);
      setElapsed(0);
      const t0 = performance.now();
      timer.current = window.setInterval(() => setElapsed((performance.now() - t0) / 1000), 200);
    } catch (e) {
      rec.current = null;
      setError(micErrorMessage(e));
    }
  };

  const stop = async (): Promise<void> => {
    const r = rec.current;
    if (!r) return;
    rec.current = null;
    window.clearInterval(timer.current);
    setRecording(false);
    setToggleMode(false);
    try {
      const out = await r.stop();
      if (out.blob.size > 0 && out.durationS >= 0.3) {
        await enqueueAudio(out.blob, out.mime, 'manual');
      }
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  useEffect(
    () => () => {
      window.clearInterval(timer.current);
      rec.current?.cancel();
    },
    [],
  );

  const onDown = (e: PointerEvent): void => {
    e.preventDefault();
    if (recording && toggleMode) {
      void stop();
      return;
    }
    downAt.current = performance.now();
    (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
    void start();
  };
  const onUp = (): void => {
    if (!rec.current) return;
    if (performance.now() - downAt.current < TAP_MAX_MS) {
      setToggleMode(true); // a quick tap keeps recording until the next tap
      return;
    }
    void stop();
  };

  return (
    <div class="ptt-wrap">
      <button
        type="button"
        class={`ptt ${recording ? 'recording' : ''}`}
        disabled={!supported}
        aria-pressed={recording}
        aria-label={recording ? S.capture.pttTap : S.capture.ptt}
        onPointerDown={onDown}
        onPointerUp={onUp}
        onPointerCancel={onUp}
        onContextMenu={(e) => e.preventDefault()}
      >
        <Icon name={recording ? 'stop' : 'mic'} size={40} stroke={1.6} />
      </button>
      <div class="ptt-label">
        {recording ? (
          <span class="row" style="gap:8px">
            <span class="pulse" />
            <span class="rec-timer">{fmtDuration(elapsed)}</span>
            <span>{toggleMode ? S.capture.pttTap : S.capture.recording}</span>
          </span>
        ) : supported ? (
          S.capture.pttHint
        ) : (
          S.errors.micUnsupported
        )}
      </div>
      {error && (
        <div class="error" role="alert">
          <Icon name="alert" size={18} />
          <span>{error}</span>
        </div>
      )}
    </div>
  );
}

function AlwaysListening(): JSX.Element {
  const [threshold, setThreshold] = useState(readThreshold());
  const on = listenerOn.value;
  const db = levelDb.value;
  const level = Math.max(0, Math.min(1, (db + 70) / 70));
  const thresholdPos = Math.max(0, Math.min(1, (threshold + 70) / 70));
  const supported = recordingSupported() && typeof AudioContext !== 'undefined';
  return (
    <div class="stack" style="gap:10px">
      <div class="listen-head">
        <Switch
          checked={on}
          disabled={!supported}
          onChange={(v) => void toggleListener(v)}
          label={S.capture.alwaysListening}
        />
        {on && (
          <span class="listen-state" aria-live="polite">
            <span class="pulse" />
            {speaking.value ? S.capture.recording : S.capture.listening}
          </span>
        )}
      </div>
      <p class="hint">{S.capture.alwaysListeningHint}</p>
      {on && (
        <>
          <div class="meter" aria-label={S.capture.level}>
            <div class={`meter-fill ${speaking.value ? 'hot' : ''}`} style={`width:${level * 100}%`} />
            <div class="meter-threshold" style={`left:${thresholdPos * 100}%`} />
          </div>
          <label class="field">
            <span class="label">
              {S.capture.sensitivity} <span class="muted">({threshold} dB)</span>
            </span>
            <input
              class="range"
              type="range"
              min={-65}
              max={-15}
              step={1}
              value={threshold}
              onInput={(e) => {
                const v = Number(e.currentTarget.value);
                setThreshold(v);
                writeLocal(KEY_THRESHOLD, String(v));
                if (listener) listener.thresholdDb = v;
              }}
            />
          </label>
          <p class="hint">
            {segmentsSent.value > 0 ? `${segmentsSent.value} ${S.capture.segmentSent} · ` : ''}
            {S.capture.keepScreenOn}
          </p>
        </>
      )}
      {listenerError.value && (
        <div class="error" role="alert">
          <Icon name="alert" size={18} />
          <span>{listenerError.value}</span>
        </div>
      )}
    </div>
  );
}

type Row = { kind: 'queued'; item: QueuedCapture } | { kind: 'server'; item: CaptureOut };

function CaptureList(): JSX.Element {
  const now = useNow();
  const local = queued.value;
  const server = captures.value;
  const localUids = new Set(local.map((q) => q.uid));
  const rows: Row[] = [
    ...local.map((item): Row => ({ kind: 'queued', item })).reverse(),
    ...server.filter((c) => !localUids.has(c.uid)).map((item): Row => ({ kind: 'server', item })),
  ];
  if (rows.length === 0) return <div class="empty">{S.capture.empty}</div>;
  return (
    <div class="card" style="padding:4px 18px">
      {rows.map((r) =>
        r.kind === 'queued' ? (
          <QueuedItem key={r.item.uid} item={r.item} now={now} />
        ) : (
          <CaptureItem key={r.item.uid} cap={r.item} now={now} />
        ),
      )}
    </div>
  );
}

function QueuedItem({ item, now }: { item: QueuedCapture; now: number }): JSX.Element {
  return (
    <div class="capture-item">
      <div class="capture-icon">
        <Icon name={item.kind === 'audio' ? 'mic' : 'text'} size={18} />
      </div>
      <div class="capture-body">
        <div class="capture-meta">
          <Chip status="queued" />
          <span>{S.capture.origin[item.origin] ?? item.origin}</span>
          <span>·</span>
          <span>{relTime(item.client_ts, now)}</span>
          {item.error && <span class="muted">· {truncate(item.error, 60)}</span>}
        </div>
        <div class={`capture-text ${item.kind === 'audio' ? 'placeholder' : ''}`}>
          {item.kind === 'audio' ? S.capture.queued : item.text}
        </div>
      </div>
      <div class="actions">
        <button
          class="btn btn-ghost btn-sm btn-icon"
          aria-label={S.capture.remove}
          title={S.capture.remove}
          onClick={() => void dropQueued(item.uid)}
        >
          <Icon name="x" size={16} />
        </button>
      </div>
    </div>
  );
}

function SpeakerBadge({ cap }: { cap: CaptureOut }): JSX.Element | null {
  if (cap.kind !== 'audio' || cap.status === 'pending' || cap.status === 'processing') return null;
  if (cap.speaker_is_me === true) return <Chip class="chip-me" label={S.capture.speakerMe} />;
  if (cap.speaker_is_me === false) return <Chip class="chip-notme" label={S.capture.speakerNotMe} />;
  return <Chip class="chip-unknown" label={S.capture.speakerUnknown} />;
}

function CaptureItem({ cap, now }: { cap: CaptureOut; now: number }): JSX.Element {
  const [busy, setBusy] = useState(false);
  const mems = memories.value;
  const linked = cap.memory_uids.map((uid) => ({ uid, mem: mems.find((m) => m.uid === uid) }));
  const isLocalFailure = cap.status === 'failed' && cap.device_uid === '';
  let body: JSX.Element;
  if (cap.text) body = <div class="capture-text">{cap.text}</div>;
  else if (cap.status === 'discarded') {
    body = (
      <div class="capture-text placeholder">
        {cap.speaker_is_me === false ? S.capture.discardedOther : S.capture.discardedSilence}
      </div>
    );
  } else if (cap.status === 'failed') {
    body = <div class="capture-text placeholder">{cap.error ?? S.errors.generic}</div>;
  } else {
    body = (
      <div class="capture-text placeholder">
        {cap.status === 'processing' ? S.capture.transcriptProcessing : S.capture.transcriptPending}
      </div>
    );
  }
  const retry = async (): Promise<void> => {
    setBusy(true);
    try {
      if (isLocalFailure) {
        removeCapture(cap.uid);
        if (cap.text) await enqueueText(cap.text, cap.origin);
      } else {
        await api.retryCapture(cap.uid);
        upsertCapture({ uid: cap.uid, status: 'pending', error: null });
      }
    } catch {
      /* the list keeps the old state */
    } finally {
      setBusy(false);
    }
  };
  const remove = async (): Promise<void> => {
    setBusy(true);
    try {
      if (!isLocalFailure) await api.deleteCapture(cap.uid);
      removeCapture(cap.uid);
    } catch {
      setBusy(false);
    }
  };
  return (
    <div class="capture-item">
      <div class="capture-icon">
        <Icon name={cap.kind === 'audio' ? 'mic' : 'text'} size={18} />
      </div>
      <div class="capture-body">
        <div class="capture-meta">
          <Chip status={cap.status} />
          <SpeakerBadge cap={cap} />
          <span>{S.capture.origin[cap.origin] ?? cap.origin}</span>
          {typeof cap.duration_s === 'number' && cap.duration_s > 0 && (
            <span>· {fmtDuration(cap.duration_s)}</span>
          )}
          <span>· {relTime(cap.client_ts, now)}</span>
          {cap.error && cap.status !== 'discarded' && cap.status !== 'failed' && (
            <span class="muted">· {truncate(cap.error, 60)}</span>
          )}
        </div>
        {body}
        {linked.length > 0 && (
          <div class="memory-links">
            {linked.map(({ uid, mem }) => (
              <a key={uid} class="memory-link" href="/memories" onClick={onLinkClick}>
                <Icon name="memory" size={13} />
                <span>{mem ? truncate(mem.text, 60) : S.capture.memoriesLinked(1)}</span>
              </a>
            ))}
          </div>
        )}
      </div>
      <div class="actions">
        {(cap.status === 'failed' || (cap.status === 'pending' && cap.error)) && (
          <button
            class="btn btn-ghost btn-sm btn-icon"
            aria-label={S.capture.retry}
            title={S.capture.retry}
            disabled={busy}
            onClick={() => void retry()}
          >
            <Icon name="refresh" size={16} />
          </button>
        )}
        <button
          class="btn btn-ghost btn-sm btn-icon"
          aria-label={S.capture.remove}
          title={S.capture.remove}
          disabled={busy}
          onClick={() => {
            if (confirm(S.common.confirmDelete)) void remove();
          }}
        >
          <Icon name="trash" size={16} />
        </button>
      </div>
    </div>
  );
}
