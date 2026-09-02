import type { JSX } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import { api, errorMessage } from '../api';
import { Icon, type IconName } from '../components/icon';
import { Chip, ErrorNote, Spinner, autoGrow, useDebounced, useNow } from '../components/ui';
import { parseIso, relTime } from '../format';
import { memories, mergeMemories, removeMemory, upsertMemory } from '../store';
import { S } from '../strings';
import { MEMORY_KINDS, type MemoryKind, type MemoryOut } from '../types';

const KIND_ICON: Record<string, IconName> = {
  note: 'note',
  fact: 'fact',
  preference: 'preference',
  todo: 'todo',
  number: 'number',
  event: 'event',
  person: 'person',
};

const isKind = (v: string): v is MemoryKind => (MEMORY_KINDS as readonly string[]).includes(v);

export function MemoriesPage(): JSX.Element {
  const [query, setQuery] = useState('');
  const dq = useDebounced(query.trim(), 250);
  const [results, setResults] = useState<MemoryOut[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<unknown>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [loadError, setLoadError] = useState<unknown>(null);
  const mountedAt = useRef(Date.now());
  const all = memories.value;

  useEffect(() => {
    api.memories({ include_archived: true }).then(mergeMemories, setLoadError);
  }, []);

  useEffect(() => {
    if (!dq) {
      setResults(null);
      setSearchError(null);
      return;
    }
    let alive = true;
    setSearching(true);
    api.memories({ q: dq }).then(
      (r) => {
        if (!alive) return;
        setResults(r);
        setSearchError(null);
        setSearching(false);
      },
      (e: unknown) => {
        if (!alive) return;
        setSearchError(e);
        setSearching(false);
      },
    );
    return () => {
      alive = false;
    };
  }, [dq, all]);

  const visible = (results ?? all).filter((m) => showArchived || !m.archived);
  const groups = [
    ...MEMORY_KINDS.map((kind) => ({ kind, items: visible.filter((m) => m.kind === kind) })),
    { kind: 'other', items: visible.filter((m) => !isKind(m.kind)) },
  ].filter((g) => g.items.length > 0);

  return (
    <div class="page">
      <header class="page-head">
        <h1 class="page-title">{S.memories.title}</h1>
        <p class="page-sub">{S.memories.sub}</p>
      </header>
      <AddMemory />
      <div class="row">
        <div class="search" style="flex:1;min-width:200px">
          <span class="search-icon">
            <Icon name="search" size={18} />
          </span>
          <input
            class="input"
            type="search"
            placeholder={S.memories.search}
            value={query}
            aria-label={S.memories.search}
            onInput={(e) => setQuery(e.currentTarget.value)}
          />
        </div>
        <label class="row small" style="gap:6px;cursor:pointer;min-height:44px">
          <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.currentTarget.checked)} />
          {S.memories.showArchived}
        </label>
        {searching && <Spinner />}
      </div>
      {loadError ? <ErrorNote error={loadError} /> : null}
      {searchError ? <ErrorNote error={searchError} /> : null}
      {groups.length === 0 ? (
        <div class="empty">{dq ? S.memories.noResults : S.memories.empty}</div>
      ) : (
        <div class="card" style="padding:8px 18px">
          {groups.map((g) => (
            <section key={g.kind}>
              <div class="kind-head">
                <Icon name={KIND_ICON[g.kind] ?? 'note'} size={16} />
                <span>{S.memories.kinds[g.kind] ?? g.kind}</span>
                <span class="count">{g.items.length}</span>
              </div>
              {g.items.map((m) => (
                <MemoryItem key={m.uid} mem={m} mountedAt={mountedAt.current} />
              ))}
            </section>
          ))}
        </div>
      )}
    </div>
  );
}

function AddMemory(): JSX.Element {
  const [text, setText] = useState('');
  const [kind, setKind] = useState<MemoryKind>('note');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async (e: Event): Promise<void> => {
    e.preventDefault();
    const t = text.trim();
    if (!t || busy) return;
    setBusy(true);
    setError(null);
    try {
      const out = await api.createMemory(t, kind);
      upsertMemory(out);
      setText('');
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form class="card stack" onSubmit={(e) => void submit(e)} style="gap:10px">
      <div class="composer">
        <input
          class="input"
          placeholder={S.memories.addPlaceholder}
          value={text}
          aria-label={S.memories.add}
          onInput={(e) => setText(e.currentTarget.value)}
        />
        <select
          class="select"
          style="width:auto"
          value={kind}
          aria-label={S.memories.kind}
          onChange={(e) => {
            const v = e.currentTarget.value;
            if (isKind(v)) setKind(v);
          }}
        >
          {MEMORY_KINDS.map((k) => (
            <option key={k} value={k}>
              {S.memories.kindSingular[k] ?? k}
            </option>
          ))}
        </select>
        <button class="btn btn-primary" type="submit" disabled={busy || !text.trim()}>
          <Icon name="plus" size={18} />
          <span>{S.memories.save}</span>
        </button>
      </div>
      {error && (
        <div class="error" role="alert">
          <Icon name="alert" size={18} />
          <span>{error}</span>
        </div>
      )}
    </form>
  );
}

function MemoryItem({ mem, mountedAt }: { mem: MemoryOut; mountedAt: number }): JSX.Element {
  const now = useNow();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(mem.text);
  const [kind, setKind] = useState(mem.kind);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const created = parseIso(mem.created_at)?.getTime() ?? 0;
  const fresh = created > mountedAt - 10_000 && Date.now() - created < 60_000;

  const run = async (fn: () => Promise<void>): Promise<void> => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  const save = (): Promise<void> =>
    run(async () => {
      const patch: { text?: string; kind?: MemoryKind } = {};
      if (text.trim() && text.trim() !== mem.text) patch.text = text.trim();
      if (kind !== mem.kind && isKind(kind)) patch.kind = kind;
      if (Object.keys(patch).length > 0) upsertMemory(await api.updateMemory(mem.uid, patch));
      setEditing(false);
    });
  const archive = (archived: boolean): Promise<void> =>
    run(async () => upsertMemory(await api.updateMemory(mem.uid, { archived })));
  const del = (): Promise<void> =>
    run(async () => {
      await api.deleteMemory(mem.uid);
      removeMemory(mem.uid);
    });

  return (
    <div class={`memory-item ${mem.archived ? 'archived' : ''} ${fresh ? 'highlight' : ''}`}>
      <div class="memory-body">
        {editing ? (
          <div class="stack" style="gap:8px">
            <textarea
              class="textarea"
              rows={2}
              value={text}
              ref={autoGrow}
              onInput={(e) => {
                setText(e.currentTarget.value);
                autoGrow(e.currentTarget);
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) void save();
                if (e.key === 'Escape') setEditing(false);
              }}
            />
            <div class="row">
              <select class="select" style="width:auto" value={kind} onChange={(e) => setKind(e.currentTarget.value)}>
                {MEMORY_KINDS.map((k) => (
                  <option key={k} value={k}>
                    {S.memories.kindSingular[k] ?? k}
                  </option>
                ))}
              </select>
              <button class="btn btn-primary btn-sm" disabled={busy} onClick={() => void save()}>
                {S.common.save}
              </button>
              <button class="btn btn-ghost btn-sm" disabled={busy} onClick={() => setEditing(false)}>
                {S.common.cancel}
              </button>
            </div>
          </div>
        ) : (
          <div
            class="memory-text"
            title={S.common.edit}
            onClick={() => {
              setText(mem.text);
              setKind(mem.kind);
              setEditing(true);
            }}
          >
            {mem.text}
          </div>
        )}
        <div class="memory-meta">
          <span>{S.memories.source[mem.source_kind] ?? mem.source_kind}</span>
          <span>· {relTime(mem.updated_at, now)}</span>
          {mem.archived && <Chip label={S.memories.archived} />}
          {typeof mem.score === 'number' && mem.score > 0 && (
            <span class="muted">· {mem.score.toFixed(2)}</span>
          )}
        </div>
        {error && (
          <div class="error" role="alert">
            <Icon name="alert" size={18} />
            <span>{error}</span>
          </div>
        )}
      </div>
      {!editing && (
        <div class="actions">
          <button
            class="btn btn-ghost btn-sm btn-icon"
            title={S.common.edit}
            aria-label={S.common.edit}
            disabled={busy}
            onClick={() => {
              setText(mem.text);
              setKind(mem.kind);
              setEditing(true);
            }}
          >
            <Icon name="edit" size={16} />
          </button>
          <button
            class="btn btn-ghost btn-sm btn-icon"
            title={mem.archived ? S.common.unarchive : S.common.archive}
            aria-label={mem.archived ? S.common.unarchive : S.common.archive}
            disabled={busy}
            onClick={() => void archive(!mem.archived)}
          >
            <Icon name={mem.archived ? 'refresh' : 'archive'} size={16} />
          </button>
          <button
            class="btn btn-ghost btn-sm btn-icon"
            title={S.common.delete}
            aria-label={S.common.delete}
            disabled={busy}
            onClick={() => {
              if (confirm(S.common.confirmDelete)) void del();
            }}
          >
            <Icon name="trash" size={16} />
          </button>
        </div>
      )}
    </div>
  );
}
