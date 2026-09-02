/** The blind test friends take. Runs without a device token; keep it independent of the shell. */

import type { JSX } from 'preact';
import { useState } from 'preact/hooks';
import { isApiError, request } from '../api';
import { readLocal, writeLocal } from '../auth';
import { Icon } from '../components/icon';
import { ErrorNote, Field, Spinner, useAsync } from '../components/ui';
import { S } from '../strings';
import type { EvalPair, HealthOut } from '../types';

const KEY_RATER = 'soulsaka.rater';

const publicGet = <T,>(path: string): Promise<T> => request<T>(path, { auth: false });
const publicPost = <T,>(path: string, json: unknown): Promise<T> =>
  request<T>(path, { json, auth: false });

type Turn = { role: string; text: string };

function contextTurns(ctx: EvalPair['context']): Turn[] | null {
  if (Array.isArray(ctx)) return ctx;
  const t = ctx.trim();
  if (t.startsWith('[')) {
    try {
      const parsed = JSON.parse(t) as unknown;
      if (Array.isArray(parsed) && parsed.every((x) => x && typeof x === 'object' && 'text' in x)) {
        return parsed as Turn[];
      }
    } catch {
      /* plain text */
    }
  }
  return null;
}

export function RatePage({ version }: { version: string }): JSX.Element {
  const [rater, setRater] = useState(readLocal(KEY_RATER) ?? '');
  const [started, setStarted] = useState(rater.trim().length > 0);
  const [round, setRound] = useState(0);
  const health = useAsync(() => publicGet<HealthOut>('/api/health'), []);
  const pairs = useAsync(
    () =>
      started
        ? publicGet<EvalPair[]>(
            `/api/eval/pairs?version=${encodeURIComponent(version)}&rater=${encodeURIComponent(rater.trim())}`,
          )
        : Promise.resolve(null),
    [started, version, round],
  );
  const name = health.data?.name ?? null;

  return (
    <div class="rate-wrap">
      <div class="brand" style="padding:0">
        <span class="brand-mark">
          <Icon name="sparkle" size={16} />
        </span>
        <div>
          <div class="brand-name">{S.app.name}</div>
          <div class="brand-tag">
            {S.rate.version} {version}
          </div>
        </div>
      </div>
      <h1 class="page-title">{S.rate.title}</h1>
      <p class="dim">{name ? S.rate.lead(name) : S.rate.leadNoName}</p>
      {!started ? (
        <form
          class="card stack"
          onSubmit={(e) => {
            e.preventDefault();
            if (!rater.trim()) return;
            writeLocal(KEY_RATER, rater.trim());
            setStarted(true);
          }}
        >
          <Field label={S.rate.yourName}>
            <input
              class="input"
              value={rater}
              placeholder={S.rate.namePlaceholder}
              autoComplete="given-name"
              onInput={(e) => setRater(e.currentTarget.value)}
              required
            />
          </Field>
          <button class="btn btn-primary btn-block" type="submit" disabled={!rater.trim()}>
            {S.rate.start}
          </button>
        </form>
      ) : pairs.loading ? (
        <Spinner />
      ) : pairs.error ? (
        isApiError(pairs.error, 404) ? (
          <div class="empty">{S.rate.unavailable}</div>
        ) : (
          <ErrorNote error={pairs.error} onRetry={pairs.reload} />
        )
      ) : !pairs.data || pairs.data.length === 0 ? (
        <div class="empty">{S.rate.noPairs}</div>
      ) : (
        <Quiz key={round} pairs={pairs.data} rater={rater.trim()} onAgain={() => setRound((r) => r + 1)} />
      )}
    </div>
  );
}

function Quiz({ pairs, rater, onAgain }: { pairs: EvalPair[]; rater: string; onAgain: () => void }): JSX.Element {
  const [idx, setIdx] = useState(0);
  const [picked, setPicked] = useState<'first' | 'second' | null>(null);
  const [correct, setCorrect] = useState<boolean | null>(null);
  const [score, setScore] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const pair = pairs[idx];

  if (!pair) {
    const pct = pairs.length ? Math.round((score / pairs.length) * 100) : 0;
    return (
      <div class="card stack" style="text-align:center">
        <h2 class="card-title">{S.rate.done}</h2>
        <div class="score-big">{S.rate.score(score, pairs.length)}</div>
        <p class="dim">{S.rate.interpret(pct)}</p>
        <button class="btn btn-primary" onClick={onAgain}>
          {S.rate.again}
        </button>
      </div>
    );
  }

  const guess = async (which: 'first' | 'second'): Promise<void> => {
    if (picked || busy) return;
    setBusy(true);
    setError(null);
    try {
      const r = await publicPost<{ correct: boolean }>(`/api/eval/pairs/${encodeURIComponent(pair.uid)}/guess`, {
        rater,
        guessed_first: which === 'first',
      });
      setPicked(which);
      setCorrect(r.correct);
      if (r.correct) setScore((s) => s + 1);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  const next = (): void => {
    setIdx((i) => i + 1);
    setPicked(null);
    setCorrect(null);
  };
  const turns = contextTurns(pair.context);
  const cls = (which: 'first' | 'second'): string =>
    `candidate ${picked === which ? `picked ${correct ? 'right' : 'wrong'}` : ''}`;

  return (
    <div class="stack" style="gap:16px">
      <div class="row row-between small muted">
        <span>{S.rate.progress(idx + 1, pairs.length)}</span>
        <span>{S.rate.score(score, idx)}</span>
      </div>
      <div class="card stack">
        <span class="section-title">{S.rate.context}</span>
        {turns ? (
          <div class="rate-context">
            {turns.map((t, i) => (
              <div key={i} class="rate-turn">
                <span class="role">{t.role}</span>
                <span class="pre">{t.text}</span>
              </div>
            ))}
          </div>
        ) : (
          <div class="rate-context">{String(pair.context)}</div>
        )}
      </div>
      <div class="candidates">
        <button class={cls('first')} disabled={picked !== null || busy} onClick={() => void guess('first')}>
          <span class="tag">{S.rate.first}</span>
          <span>{pair.first}</span>
        </button>
        <button class={cls('second')} disabled={picked !== null || busy} onClick={() => void guess('second')}>
          <span class="tag">{S.rate.second}</span>
          <span>{pair.second}</span>
        </button>
      </div>
      {error ? <ErrorNote error={error} /> : null}
      {picked !== null && (
        <div class="row row-between">
          <span class={`verdict ${correct ? 'right' : 'wrong'}`}>{correct ? S.rate.correct : S.rate.wrong}</span>
          <button class="btn btn-primary" onClick={next}>
            {S.rate.next}
            <Icon name="chevron-right" size={16} />
          </button>
        </div>
      )}
    </div>
  );
}
