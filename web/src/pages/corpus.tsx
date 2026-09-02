import type { JSX } from 'preact';
import { useState } from 'preact/hooks';
import { api, isApiError } from '../api';
import { hubOrigin } from '../auth';
import { MonthBars } from '../components/charts';
import { Icon } from '../components/icon';
import { Card, CopyButton, ErrorNote, Spinner, useAsync } from '../components/ui';
import { fmtInt, relTime } from '../format';
import { corpusTick } from '../store';
import { S } from '../strings';
import type { ImportReport, SourceOut, StatsOut } from '../types';

const UPLOAD_KINDS = ['auto', 'whatsapp_export', 'discord', 'mbox'] as const;

export function CorpusPage(): JSX.Element {
  const tick = corpusTick.value;
  const stats = useAsync(api.stats, [tick]);
  const sources = useAsync(api.sources, [tick]);
  return (
    <div class="page">
      <header class="page-head">
        <h1 class="page-title">{S.corpus.title}</h1>
        <p class="page-sub">{S.corpus.sub}</p>
      </header>
      {stats.error ? <ErrorNote error={stats.error} onRetry={stats.reload} /> : null}
      {stats.data ? <StatsView stats={stats.data} /> : stats.loading ? <Spinner /> : null}
      <Card title={S.corpus.sources}>
        {sources.error ? <ErrorNote error={sources.error} onRetry={sources.reload} /> : null}
        {sources.data && sources.data.length === 0 && <p class="muted">{S.corpus.noSources}</p>}
        {sources.data && sources.data.length > 0 && (
          <div class="list">
            {sources.data.map((s) => (
              <SourceItem key={s.id} source={s} onDeleted={() => { sources.reload(); stats.reload(); }} />
            ))}
          </div>
        )}
      </Card>
      <Card title={S.corpus.upload} lead={S.corpus.uploadHint}>
        <UploadZone onDone={() => { sources.reload(); stats.reload(); }} />
      </Card>
      <Card title={S.corpus.importCli} lead={S.corpus.importCliLead}>
        <CliInstructions />
      </Card>
    </div>
  );
}

function StatsView({ stats }: { stats: StatsOut }): JSX.Element {
  const first = stats.first_train_threshold;
  const comfy = stats.comfortable_threshold;
  const pct = Math.min(1, stats.me_words / comfy);
  const firstPos = Math.min(1, first / comfy);
  const langs = Object.entries(stats.by_lang).sort((a, b) => b[1] - a[1]);
  return (
    <>
      <Card>
        <div class="hero">
          <div>
            <div class="hero-number">{fmtInt(stats.me_words)}</div>
            <div class="hero-label">{S.corpus.wordsOfYou}</div>
          </div>
          <div class="progress" role="progressbar" aria-valuenow={stats.me_words} aria-valuemin={0} aria-valuemax={comfy}>
            <div class="progress-fill" style={`width:${pct * 100}%`} />
            <div class={`progress-mark ${stats.me_words >= first ? 'reached' : ''}`} style={`left:${firstPos * 100}%`}>
              <span>
                {S.corpus.firstTrain} · {fmtInt(first)}
              </span>
            </div>
            <div class={`progress-mark ${stats.me_words >= comfy ? 'reached' : ''}`} style="left:100%">
              <span style="transform:translateX(-100%)">
                {S.corpus.comfortable} · {fmtInt(comfy)}
              </span>
            </div>
          </div>
          <p class={stats.ready_for_first_train ? 'small' : 'small dim'} style={stats.ready_for_first_train ? 'color:var(--ok)' : ''}>
            {stats.ready_for_first_train ? S.corpus.readyToTrain : S.corpus.needMore(first - stats.me_words)}
          </p>
          <div class="stat-grid">
            <Stat value={fmtInt(stats.me_messages)} label={S.corpus.messages} />
            <Stat value={fmtInt(stats.other_messages)} label={S.corpus.contextMessages} />
            <Stat value={fmtInt(stats.conversations)} label={S.corpus.conversations} />
            <Stat value={fmtInt(stats.memories)} label={S.corpus.memories} />
            <Stat value={fmtInt(stats.captures_pending)} label={S.corpus.pendingCaptures} />
            <Stat value={stats.latest_version ?? S.common.none} label={S.corpus.latestVersion} />
          </div>
        </div>
      </Card>
      <Card title={S.corpus.byMonth}>
        <MonthBars months={stats.by_month} />
      </Card>
      <div class="grid-2">
        <Card title={S.corpus.byRegister}>
          <div class="table-wrap">
            <table class="table">
              <thead>
                <tr>
                  <th>{S.corpus.register}</th>
                  <th class="num">{S.corpus.messages}</th>
                  <th class="num">{S.corpus.words}</th>
                </tr>
              </thead>
              <tbody>
                {stats.by_register.map((r) => (
                  <tr key={r.register}>
                    <td>{r.register}</td>
                    <td class="num">{fmtInt(r.messages)}</td>
                    <td class="num">{fmtInt(r.words)}</td>
                  </tr>
                ))}
                {stats.by_register.length === 0 && (
                  <tr>
                    <td colSpan={3} class="muted">
                      {S.common.none}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </Card>
        <Card title={S.corpus.bySource}>
          <div class="table-wrap">
            <table class="table">
              <thead>
                <tr>
                  <th>{S.corpus.source}</th>
                  <th class="num">{S.corpus.messages}</th>
                  <th class="num">{S.corpus.words}</th>
                </tr>
              </thead>
              <tbody>
                {stats.by_source.map((r) => (
                  <tr key={`${r.kind}:${r.label}`}>
                    <td>
                      <span class="muted">{r.kind}</span> {r.label}
                    </td>
                    <td class="num">{fmtInt(r.messages)}</td>
                    <td class="num">{fmtInt(r.words)}</td>
                  </tr>
                ))}
                {stats.by_source.length === 0 && (
                  <tr>
                    <td colSpan={3} class="muted">
                      {S.common.none}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </Card>
      </div>
      {langs.length > 0 && (
        <Card title={S.corpus.languages}>
          <div class="row">
            {langs.map(([lang, n]) => (
              <span key={lang} class="chip chip-lang">
                {lang} <span class="muted">{fmtInt(n)}</span>
              </span>
            ))}
          </div>
        </Card>
      )}
    </>
  );
}

function Stat({ value, label }: { value: string; label: string }): JSX.Element {
  return (
    <div class="stat">
      <span class="stat-value">{value}</span>
      <span class="stat-label">{label}</span>
    </div>
  );
}

function SourceItem({ source, onDeleted }: { source: SourceOut; onDeleted: () => void }): JSX.Element {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const del = async (): Promise<void> => {
    if (!confirm(S.corpus.confirmDeleteSource(source.label))) return;
    setBusy(true);
    try {
      await api.deleteSource(source.id);
      onDeleted();
    } catch (e) {
      setError(e);
      setBusy(false);
    }
  };
  return (
    <div class="source-item">
      <div class="capture-icon">
        <Icon name="corpus" size={18} />
      </div>
      <div style="flex:1;min-width:0" class="stack-sm">
        <div>
          <strong>{source.label}</strong> <span class="muted small">{source.kind}</span>
        </div>
        <div class="small dim">
          {fmtInt(source.me_words)} {S.corpus.wordsOfYou} · {fmtInt(source.messages)} {S.corpus.messages}
          {source.last_import_at && ` · ${S.corpus.lastImport} ${relTime(source.last_import_at)}`}
        </div>
        {source.locator && <div class="tiny muted mono">{source.locator}</div>}
        {error ? <ErrorNote error={error} /> : null}
      </div>
      <button class="btn btn-danger btn-sm" disabled={busy} onClick={() => void del()}>
        <Icon name="trash" size={15} />
        {S.corpus.deleteSource}
      </button>
    </div>
  );
}

function UploadZone({ onDone }: { onDone: () => void }): JSX.Element {
  const [over, setOver] = useState(false);
  const [kind, setKind] = useState<string>('auto');
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState<ImportReport | null>(null);
  const [error, setError] = useState<unknown>(null);
  const handle = async (file: File | undefined): Promise<void> => {
    if (!file || busy) return;
    setBusy(true);
    setError(null);
    setReport(null);
    try {
      setReport(await api.importUpload(file, kind));
      onDone();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div class="stack">
      <div
        class={`dropzone ${over ? 'over' : ''}`}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          void handle(e.dataTransfer?.files?.[0]);
        }}
      >
        <Icon name="upload" size={28} />
        <div class="row" style="justify-content:center">
          <select class="select" style="width:auto" value={kind} aria-label={S.corpus.uploadKind} onChange={(e) => setKind(e.currentTarget.value)}>
            {UPLOAD_KINDS.map((k) => (
              <option key={k} value={k}>
                {S.corpus.uploadKinds[k] ?? k}
              </option>
            ))}
          </select>
          <label class="btn btn-primary">
            <input type="file" hidden accept=".txt,.zip,.mbox,text/plain,application/zip" disabled={busy} onChange={(e) => { void handle(e.currentTarget.files?.[0]); e.currentTarget.value = ''; }} />
            {busy ? <Spinner /> : <Icon name="upload" size={16} />}
            {busy ? S.corpus.uploading : S.corpus.uploadPick}
          </label>
        </div>
      </div>
      {error ? (
        isApiError(error, 404) ? (
          <div class="note">{S.corpus.uploadUnavailable}</div>
        ) : (
          <ErrorNote error={error} />
        )
      ) : null}
      {report && <ReportView report={report} />}
    </div>
  );
}

function ReportView({ report }: { report: ImportReport }): JSX.Element {
  const skipped = Object.entries(report.skipped_reasons);
  return (
    <div class="stack" style="gap:8px">
      <div class="section-title">
        {S.corpus.report} · {report.source.label}
      </div>
      <div class="report-grid">
        <Stat value={fmtInt(report.inserted)} label={S.corpus.reportInserted} />
        <Stat value={fmtInt(report.duplicates)} label={S.corpus.reportDuplicates} />
        <Stat value={fmtInt(report.skipped)} label={S.corpus.reportSkipped} />
        <Stat value={fmtInt(report.me_words)} label={S.corpus.reportMeWords} />
        <Stat value={fmtInt(report.conversations)} label={S.corpus.reportConversations} />
      </div>
      {skipped.length > 0 && (
        <p class="small dim">
          {S.corpus.reportSkipped}: {skipped.map(([k, v]) => `${k} ${v}`).join(', ')}
        </p>
      )}
      {report.notes.length > 0 && (
        <ul class="small dim" style="margin:0;padding-left:18px">
          {report.notes.map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function CliInstructions(): JSX.Element {
  const [code, setCode] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const origin = hubOrigin();
  const lines = [`soulsaka hub login --url ${origin} --code ${code ?? '<code>'}`, 'soulsaka import auto'];
  const mint = async (): Promise<void> => {
    setBusy(true);
    setError(null);
    try {
      setCode((await api.pairingCode()).code);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div class="stack">
      <pre class="code-block">{lines.join('\n')}</pre>
      <div class="row">
        <button class="btn btn-sm" disabled={busy} onClick={() => void mint()}>
          <Icon name="key" size={15} />
          {S.corpus.getCode}
        </button>
        {code && <span class="small muted">{S.corpus.codeValid}</span>}
        <CopyButton text={lines.join('\n')} />
      </div>
      {error ? <ErrorNote error={error} /> : null}
    </div>
  );
}
