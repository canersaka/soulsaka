import type { JSX } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import { ApiError, api, errorDetail, errorMessage, isApiError, requestRaw } from '../api';
import { readLocal, writeLocal } from '../auth';
import { Icon } from '../components/icon';
import { Chip, ErrorNote, Segmented, Spinner, autoGrow, useAsync } from '../components/ui';
import { fmtTime, relTime, truncate } from '../format';
import { newUid } from '../queue';
import { navigate, onLinkClick } from '../router';
import { parseJSON, readSSE } from '../sse';
import { S } from '../strings';
import { REGISTERS, type ChatMode, type ChatTurn, type LLMProfile, type Register } from '../types';

const KEY_MODE = 'soulsaka.chatMode';
const KEY_REGISTER = 'soulsaka.chatRegister';
const KEY_PROFILE = 'soulsaka.chatProfile';

const isMode = (v: string | null): v is ChatMode => v === 'assistant' || v === 'twin';
const isRegister = (v: string | null): v is Register =>
  v !== null && (REGISTERS as readonly string[]).includes(v);

export function ChatPage({ chatUid }: { chatUid: string | null }): JSX.Element {
  const chats = useAsync(api.chats, []);
  const profiles = useAsync(api.profiles, []);
  const [listOpen, setListOpen] = useState(false);
  const [mode, setMode] = useState<ChatMode>(() => {
    const v = readLocal(KEY_MODE);
    return isMode(v) ? v : 'assistant';
  });
  const [register, setRegister] = useState<Register>(() => {
    const v = readLocal(KEY_REGISTER);
    return isRegister(v) ? v : 'text';
  });
  const [profileName, setProfileName] = useState<string | null>(readLocal(KEY_PROFILE));

  const list = profiles.data ?? [];
  const selected =
    list.find((p) => p.name === profileName && p.enabled) ??
    list.find((p) => p.default && p.enabled) ??
    list.find((p) => p.enabled) ??
    null;
  const cloudOff = list.some((p) => p.cloud && !p.enabled);

  return (
    <div class="page">
      <header class="page-head">
        <div class="row row-between">
          <h1 class="page-title">{S.chat.title}</h1>
          <button
            class="btn btn-sm"
            type="button"
            onClick={() => {
              setListOpen(false);
              navigate('/chat');
            }}
          >
            <Icon name="plus" size={16} />
            {S.chat.newChat}
          </button>
        </div>
      </header>
      <div class="chat-layout">
        <aside class={`chat-list ${listOpen ? '' : 'collapsed'}`}>
          <ChatListToggle open={listOpen} onToggle={() => setListOpen((o) => !o)} count={chats.data?.length ?? 0} />
          {chats.error ? <ErrorNote error={chats.error} onRetry={chats.reload} /> : null}
          {chats.data && chats.data.length === 0 && <p class="muted small" style="padding:8px 12px">{S.chat.noChats}</p>}
          {chats.data?.map((c) => (
            <a
              key={c.uid}
              class={`chat-list-item ${c.uid === chatUid ? 'active' : ''}`}
              href={`/chat/${c.uid}`}
              onClick={(e) => {
                setListOpen(false);
                onLinkClick(e);
              }}
            >
              <span class="title">{c.title || (c.first_text ? truncate(c.first_text, 48) : S.chat.untitled)}</span>
              <span class="tiny muted">{relTime(c.updated_at)}</span>
            </a>
          ))}
        </aside>
        <div class="chat-thread">
          <div class="chat-controls">
            <button class="btn btn-sm chat-list-btn" type="button" onClick={() => setListOpen((o) => !o)}>
              <Icon name="menu" size={16} />
              {S.chat.chats}
            </button>
            <Segmented<ChatMode>
              ariaLabel="mode"
              value={mode}
              onChange={(m) => {
                setMode(m);
                writeLocal(KEY_MODE, m);
              }}
              options={[
                { value: 'assistant', label: S.chat.modeAssistant, title: S.chat.modeHintAssistant },
                { value: 'twin', label: S.chat.modeTwin, title: S.chat.modeHintTwin },
              ]}
            />
            {mode === 'twin' && (
              <Segmented<Register>
                ariaLabel={S.chat.register}
                value={register}
                onChange={(r) => {
                  setRegister(r);
                  writeLocal(KEY_REGISTER, r);
                }}
                options={REGISTERS.map((r) => ({ value: r, label: S.chat.registers[r] ?? r }))}
              />
            )}
            <ProfilePicker
              profiles={list}
              value={selected?.name ?? ''}
              onChange={(name) => {
                setProfileName(name);
                writeLocal(KEY_PROFILE, name);
              }}
            />
          </div>
          {cloudOff && (
            <p class="hint">
              <Icon name="cloud" size={13} /> {S.chat.cloudDisabled}
            </p>
          )}
          <Thread
            chatUid={chatUid}
            mode={mode}
            register={register}
            profile={selected}
            onSent={chats.reload}
          />
        </div>
      </div>
    </div>
  );
}

function ChatListToggle({ open, onToggle, count }: { open: boolean; onToggle: () => void; count: number }): JSX.Element {
  return (
    <div class="row row-between" style="padding:4px 12px 8px">
      <span class="section-title">
        {S.chat.chats} {count > 0 && <span class="muted">({count})</span>}
      </span>
      {open && (
        <button class="btn btn-ghost btn-sm btn-icon" onClick={onToggle} aria-label={S.common.close}>
          <Icon name="x" size={16} />
        </button>
      )}
    </div>
  );
}

function ProfilePicker({
  profiles,
  value,
  onChange,
}: {
  profiles: LLMProfile[];
  value: string;
  onChange: (name: string) => void;
}): JSX.Element {
  const current = profiles.find((p) => p.name === value);
  return (
    <label class="row" style="gap:6px">
      <span class="label">{S.chat.profile}</span>
      <select
        class="select"
        value={value}
        onChange={(e) => onChange(e.currentTarget.value)}
        aria-label={S.chat.profile}
      >
        {profiles.map((p) => (
          <option
            key={p.name}
            value={p.name}
            disabled={!p.enabled}
            title={p.cloud && !p.enabled ? S.chat.cloudDisabled : `${p.backend} · ${p.model}`}
          >
            {p.name}
            {p.cloud ? ` · ${S.chat.cloud}` : ''}
            {p.default ? ' (default)' : ''}
          </option>
        ))}
      </select>
      {current?.cloud && <Chip class="chip-cloud" label={S.chat.cloud} />}
      {current && !current.cloud && current.personal && (
        <span class="tiny muted">{S.chat.personal}</span>
      )}
    </label>
  );
}

function Thread({
  chatUid,
  mode,
  register,
  profile,
  onSent,
}: {
  chatUid: string | null;
  mode: ChatMode;
  register: Register;
  profile: LLMProfile | null;
  onSent: () => void;
}): JSX.Element {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [loading, setLoading] = useState(chatUid !== null);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [draft, setDraft] = useState('');
  const [streaming, setStreaming] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const ownedUid = useRef<string | null>(null);
  const threadUid = useRef<string | null>(chatUid);
  const abortRef = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const textRef = useRef<HTMLTextAreaElement>(null);
  threadUid.current = chatUid;

  useEffect(() => {
    if (ownedUid.current === chatUid) return; // the chat this thread just created
    abortRef.current?.abort();
    setStreaming(null);
    setSending(false);
    setError(null);
    if (!chatUid) {
      setTurns([]);
      setLoading(false);
      return;
    }
    let alive = true;
    setLoading(true);
    setLoadError(null);
    api.chatTurns(chatUid).then(
      (t) => {
        if (!alive) return;
        setTurns(t);
        setLoading(false);
      },
      (e: unknown) => {
        if (!alive) return;
        setLoadError(e);
        setLoading(false);
      },
    );
    return () => {
      alive = false;
    };
  }, [chatUid]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end' });
  }, [turns, streaming]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const send = async (): Promise<void> => {
    const text = draft.trim();
    if (!text || sending) return;
    const uid = chatUid ?? newUid();
    if (!chatUid) {
      ownedUid.current = uid;
      navigate(`/chat/${uid}`, true);
    }
    setDraft('');
    if (textRef.current) {
      textRef.current.value = '';
      autoGrow(textRef.current);
    }
    setError(null);
    setSending(true);
    setStreaming('');
    setTurns((t) => [...t, { role: 'user', text, profile: null, created_at: new Date().toISOString() }]);
    const controller = new AbortController();
    abortRef.current = controller;
    let acc = '';
    try {
      const res = await requestRaw('/api/chat', {
        json: {
          text,
          chat_uid: uid,
          profile: profile?.name ?? null,
          mode,
          register,
          stream: true,
        },
        signal: controller.signal,
      });
      if (!res.ok) throw new ApiError(res.status, await errorDetail(res));
      if (!res.body) throw new Error(S.errors.generic);
      let streamError: string | null = null;
      await readSSE(
        res.body,
        (name, data) => {
          if (name === 'token') {
            const p = parseJSON<{ t?: string }>(data);
            if (p?.t) {
              acc += p.t;
              setStreaming(acc);
            }
          } else if (name === 'error') {
            const p = parseJSON<{ error?: string }>(data);
            streamError = p?.error ?? S.errors.generic;
          }
        },
        controller.signal,
      );
      if (streamError) setError(new ApiError(502, streamError));
    } catch (e) {
      if (!controller.signal.aborted) setError(e);
    } finally {
      if (acc && threadUid.current === uid) {
        setTurns((t) => [
          ...t,
          { role: 'assistant', text: acc, profile: profile?.name ?? null, created_at: new Date().toISOString() },
        ]);
      }
      if (threadUid.current === uid) {
        setStreaming(null);
        setSending(false);
      }
      abortRef.current = null;
      onSent();
    }
  };

  const errorText = (e: unknown): string => {
    if (isApiError(e, 403)) return S.chat.errCloudRefused;
    if (isApiError(e, 502)) return S.chat.errModelDown(profile?.name ?? 'local');
    return errorMessage(e);
  };

  return (
    <>
      <div class="messages">
        {loading && <Spinner />}
        {loadError ? <ErrorNote error={loadError} /> : null}
        {!loading && turns.length === 0 && streaming === null && (
          <div class="empty">{chatUid ? S.chat.selectChat : S.chat.emptyThread}</div>
        )}
        {turns.map((t, i) => (
          <Message key={i} turn={t} cloud={t.profile !== null && isCloud(t.profile, profile)} />
        ))}
        {streaming !== null && (
          <div class="msg-wrap assistant">
            <div class="msg assistant" aria-live="polite">
              {streaming === '' ? (
                <span class="row" style="gap:8px">
                  <Spinner /> <span class="muted">{S.chat.thinking}</span>
                </span>
              ) : (
                <>
                  {streaming}
                  <span class="caret" />
                </>
              )}
            </div>
          </div>
        )}
        {error ? (
          <div class="error" role="alert">
            <Icon name="alert" size={18} />
            <span>{errorText(error)}</span>
          </div>
        ) : null}
        <div ref={endRef} />
      </div>
      <div class="chat-composer">
        <div class="composer">
          <textarea
            ref={textRef}
            class="textarea"
            rows={1}
            placeholder={mode === 'twin' ? S.chat.placeholderTwin : S.chat.placeholder}
            value={draft}
            aria-label={S.chat.placeholder}
            onInput={(e) => {
              setDraft(e.currentTarget.value);
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
          {sending ? (
            <button class="btn" type="button" onClick={() => abortRef.current?.abort()} aria-label={S.chat.stop}>
              <Icon name="stop" size={18} />
              <span>{S.chat.stop}</span>
            </button>
          ) : (
            <button
              class="btn btn-primary"
              type="button"
              disabled={!draft.trim() || !profile}
              onClick={() => void send()}
              aria-label={S.chat.send}
            >
              <Icon name="send" size={18} />
              <span>{S.chat.send}</span>
            </button>
          )}
        </div>
      </div>
    </>
  );
}

const cloudNames = new Set<string>();
function isCloud(name: string, current: LLMProfile | null): boolean {
  if (current && current.name === name) {
    if (current.cloud) cloudNames.add(name);
    return current.cloud;
  }
  return cloudNames.has(name);
}

function Message({ turn, cloud }: { turn: ChatTurn; cloud: boolean }): JSX.Element {
  const role = turn.role === 'user' ? 'user' : 'assistant';
  return (
    <div class={`msg-wrap ${role}`}>
      <div class={`msg ${role}`}>{turn.text}</div>
      <div class="msg-meta">
        {role === 'assistant' && turn.profile && <span>{turn.profile}</span>}
        {cloud && <Chip class="chip-cloud" label={S.chat.cloud} />}
        <span>{fmtTime(turn.created_at)}</span>
      </div>
    </div>
  );
}
