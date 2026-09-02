import type { ComponentChildren, JSX } from 'preact';
import { useCallback, useEffect, useRef, useState } from 'preact/hooks';
import { errorMessage } from '../api';
import { S } from '../strings';
import { Icon } from './icon';

export function Chip({
  status,
  label,
  class: cls,
}: {
  status?: string;
  label?: string;
  class?: string;
}): JSX.Element {
  return (
    <span class={`chip ${cls ?? ''}`} data-status={status}>
      {label ?? (status ? (S.capture.status[status] ?? status) : '')}
    </span>
  );
}

export function Spinner(): JSX.Element {
  return <span class="spinner" role="status" aria-label={S.common.loading} />;
}

export function Empty({ children }: { children: ComponentChildren }): JSX.Element {
  return <div class="empty">{children}</div>;
}

export function ErrorNote({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry?: () => void;
}): JSX.Element {
  return (
    <div class="error" role="alert">
      <Icon name="alert" size={18} />
      <span>{errorMessage(error)}</span>
      {onRetry && (
        <button class="btn btn-sm" onClick={onRetry}>
          {S.common.retry}
        </button>
      )}
    </div>
  );
}

export function Card({
  title,
  lead,
  actions,
  children,
  class: cls,
}: {
  title?: string;
  lead?: string;
  actions?: ComponentChildren;
  children: ComponentChildren;
  class?: string;
}): JSX.Element {
  return (
    <section class={`card ${cls ?? ''}`}>
      {(title || actions) && (
        <div class="card-head">
          {title && <h2 class="card-title">{title}</h2>}
          {actions && <div class="row">{actions}</div>}
        </div>
      )}
      {lead && <p class="card-lead">{lead}</p>}
      {children}
    </section>
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ComponentChildren;
}): JSX.Element {
  return (
    <label class="field">
      <span class="label">{label}</span>
      {children}
      {hint && <span class="hint">{hint}</span>}
    </label>
  );
}

export interface SegmentOption<T extends string> {
  value: T;
  label: string;
  disabled?: boolean;
  title?: string;
}

export function Segmented<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
}: {
  options: readonly SegmentOption<T>[];
  value: T;
  onChange: (v: T) => void;
  ariaLabel?: string;
}): JSX.Element {
  return (
    <div class="segmented" role="group" aria-label={ariaLabel}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          class={o.value === value ? 'active' : ''}
          disabled={o.disabled}
          title={o.title}
          aria-pressed={o.value === value}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Switch({
  checked,
  onChange,
  label,
  accent,
  disabled,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  accent?: boolean;
  disabled?: boolean;
}): JSX.Element {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      class={`switch btn-ghost ${checked ? 'on' : ''} ${accent ? 'accent' : ''}`}
      style="background:transparent;border:0;padding:0;min-height:44px"
      disabled={disabled}
      onClick={() => onChange(!checked)}
    >
      <span class="switch-track" />
      <span style="font-weight:600">{label}</span>
    </button>
  );
}

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    try {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.setAttribute('readonly', '');
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand('copy');
      ta.remove();
      return ok;
    } catch {
      return false;
    }
  }
}

export function CopyButton({ text, label }: { text: string; label?: string }): JSX.Element {
  const [done, setDone] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  return (
    <button
      type="button"
      class="btn btn-sm"
      onClick={async () => {
        if (await copyText(text)) {
          setDone(true);
          window.clearTimeout(timer.current);
          timer.current = window.setTimeout(() => setDone(false), 1500);
        }
      }}
    >
      <Icon name={done ? 'check' : 'copy'} size={15} />
      {done ? S.common.copied : (label ?? S.common.copy)}
    </button>
  );
}

export function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ComponentChildren;
}): JSX.Element {
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    <div
      class="modal-backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div class="modal" role="dialog" aria-modal="true" aria-label={title}>
        <div class="card-head">
          <h2 class="card-title">{title}</h2>
          <button class="btn btn-ghost btn-icon" onClick={onClose} aria-label={S.common.close}>
            <Icon name="x" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export interface AsyncState<T> {
  data: T | null;
  error: unknown;
  loading: boolean;
  reload: () => void;
  setData: (updater: T | ((prev: T | null) => T | null)) => void;
}

/** Run an async loader when deps change; ignores stale results. */
export function useAsync<T>(fn: () => Promise<T>, deps: readonly unknown[]): AsyncState<T> {
  const [data, setDataState] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const seq = useRef(0);
  useEffect(() => {
    const id = ++seq.current;
    setLoading(true);
    fn().then(
      (d) => {
        if (seq.current !== id) return;
        setDataState(() => d);
        setError(null);
        setLoading(false);
      },
      (e: unknown) => {
        if (seq.current !== id) return;
        setError(e);
        setLoading(false);
      },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  const setData = useCallback((updater: T | ((prev: T | null) => T | null)) => {
    setDataState((prev) =>
      typeof updater === 'function' ? (updater as (p: T | null) => T | null)(prev) : updater,
    );
  }, []);
  return { data, error, loading, reload, setData };
}

/** A clock that ticks every 30 s so relative times stay fresh. */
export function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(t);
  }, [intervalMs]);
  return now;
}

export function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(t);
  }, [value, ms]);
  return v;
}

/** Auto-growing textarea. */
export function autoGrow(el: HTMLTextAreaElement | null): void {
  if (!el) return;
  el.style.height = 'auto';
  el.style.height = `${Math.min(el.scrollHeight, 220)}px`;
}

export function Toast({ text }: { text: string | null }): JSX.Element | null {
  if (!text) return null;
  return (
    <div class="toast" role="status">
      {text}
    </div>
  );
}

export function useToast(): [string | null, (t: string) => void] {
  const [text, setText] = useState<string | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const show = useCallback((t: string) => {
    setText(t);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setText(null), 2200);
  }, []);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  return [text, show];
}
