import type { JSX } from 'preact';
import { useState } from 'preact/hooks';
import { api, isApiError } from '../api';
import { hubOrigin } from '../auth';
import { FidelityChart } from '../components/charts';
import { Icon } from '../components/icon';
import { Card, Chip, CopyButton, ErrorNote, Modal, Spinner, useAsync } from '../components/ui';
import { fmtDate, fmtInt, relTime } from '../format';
import { trainingTick } from '../store';
import { S } from '../strings';
import type { DatasetPreview, TrainingRun } from '../types';

const versionNum = (v: string): number => Number(v.replace(/^v/, '')) || 0;

export function TrainPage(): JSX.Element {
  const tick = trainingTick.value;
  const runs = useAsync(api.trainingRuns, [tick]);
  const evals = useAsync(api.evalSummary, [tick]);
  const [preview, setPreview] = useState<DatasetPreview | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [previewError, setPreviewError] = useState<unknown>(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<unknown>(null);
  const [started, setStarted] = useState<string | null>(null);

  const unavailable = isApiError(runs.error, 404);
  const running = (runs.data ?? []).some((r) => r.status === 'running');
  const sorted = [...(runs.data ?? [])].sort((a, b) => versionNum(b.version) - versionNum(a.version));

  const doPreview = async (): Promise<void> => {
    setPreviewBusy(true);
    setPreviewError(null);
    try {
      setPreview(await api.datasetPreview(5));
    } catch (e) {
      setPreviewError(e);
    } finally {
      setPreviewBusy(false);
    }
  };
  const doTrain = async (): Promise<void> => {
    setStarting(true);
    setStartError(null);
    setStarted(null);
    try {
      const run = await api.startTraining();
      setStarted(run.version);
      runs.reload();
    } catch (e) {
      setStartError(e);
    } finally {
      setStarting(false);
    }
  };

  return (
    <div class="page">
      <header class="page-head">
        <h1 class="page-title">{S.train.title}</h1>
        <p class="page-sub">{S.train.sub}</p>
      </header>
      <Card
        title={S.train.versions}
        actions={
          <>
            <button class="btn btn-sm" disabled={previewBusy || unavailable} onClick={() => void doPreview()}>
              {previewBusy ? <Spinner /> : <Icon name="corpus" size={15} />}
              {S.train.preview}
            </button>
            <button class="btn btn-primary btn-sm" disabled={starting || running || unavailable} onClick={() => void doTrain()}>
              {starting ? <Spinner /> : <Icon name="train" size={15} />}
              {running ? S.train.training : S.train.trainNext}
            </button>
          </>
        }
      >
        {unavailable ? (
          <div class="empty">{S.train.unavailable}</div>
        ) : runs.error ? (
          <ErrorNote error={runs.error} onRetry={runs.reload} />
        ) : runs.loading ? (
          <Spinner />
        ) : sorted.length === 0 ? (
          <div class="empty">{S.train.noRuns}</div>
        ) : (
          <div class="list">
            {sorted.map((r) => (
              <RunItem key={r.version} run={r} />
            ))}
          </div>
        )}
        {started && <p class="small" style="color:var(--ok);margin-top:10px">{S.train.started(started)}</p>}
        {startError ? <div style="margin-top:10px"><ErrorNote error={startError} /></div> : null}
        {previewError ? (
          <div style="margin-top:10px">
            {isApiError(previewError, 404) ? <div class="note">{S.train.unavailable}</div> : <ErrorNote error={previewError} />}
          </div>
        ) : null}
      </Card>
      <Card title={S.train.fidelity} lead={S.train.fidelitySub}>
        {evals.data && evals.data.versions.length > 0 ? (
          <FidelityChart versions={evals.data.versions} />
        ) : evals.loading ? (
          <Spinner />
        ) : evals.error && !isApiError(evals.error, 404) ? (
          <ErrorNote error={evals.error} onRetry={evals.reload} />
        ) : (
          <div class="empty">{S.train.noEval}</div>
        )}
      </Card>
      {preview && (
        <Modal title={S.train.previewTitle} onClose={() => setPreview(null)}>
          <div class="stack">
            <p class="dim small">
              {S.train.previewSummary(preview.n_examples, preview.n_words, preview.n_holdout)}
            </p>
            {preview.samples.length === 0 && <div class="empty">{S.train.noRuns}</div>}
            {preview.samples.map((s, i) => (
              <div key={i} class="sample">
                <details>
                  <summary class="tiny muted" style="cursor:pointer">
                    {S.train.previewSystem}
                  </summary>
                  <p class="tiny dim pre" style="margin-top:6px">
                    {s.system}
                  </p>
                </details>
                {s.context.map((c, j) => (
                  <div key={j} class="turn">
                    <span class="role">{c.role}</span>
                    <span class="pre">{c.text}</span>
                  </div>
                ))}
                <div class="turn">
                  <span class="role">{S.train.previewTarget}</span>
                  <span class="target pre">{s.target}</span>
                </div>
              </div>
            ))}
          </div>
        </Modal>
      )}
    </div>
  );
}

function metricPairs(metrics: Record<string, unknown> | null): [string, string][] {
  if (!metrics) return [];
  return Object.entries(metrics)
    .filter(([, v]) => typeof v === 'number' || typeof v === 'string')
    .slice(0, 6)
    .map(([k, v]) => [k, typeof v === 'number' ? (Number.isInteger(v) ? String(v) : v.toFixed(3)) : String(v)]);
}

function RunItem({ run }: { run: TrainingRun }): JSX.Element {
  const rateUrl = `${hubOrigin()}/rate/${run.version}`;
  const pairs = metricPairs(run.metrics);
  return (
    <div class="run-item">
      <span class="run-version">{run.version}</span>
      <div class="stack-sm" style="min-width:0">
        <div class="row" style="gap:8px">
          <Chip status={run.status} label={S.train.status[run.status] ?? run.status} />
          <span class="small dim">
            {run.backend} · {S.train.base} {run.base_model}
          </span>
        </div>
        <div class="small dim">
          {typeof run.n_examples === 'number' && `${fmtInt(run.n_examples)} ${S.train.examples} · `}
          {typeof run.n_words === 'number' && `${fmtInt(run.n_words)} ${S.train.words} · `}
          {run.data_cutoff && `${S.train.cutoff} ${fmtDate(run.data_cutoff)} · `}
          {run.finished_at ? relTime(run.finished_at) : run.started_at ? relTime(run.started_at) : ''}
        </div>
        {pairs.length > 0 && (
          <div class="row tiny muted" style="gap:10px">
            {pairs.map(([k, v]) => (
              <span key={k}>
                {k} <strong style="color:var(--ink-2)">{v}</strong>
              </span>
            ))}
          </div>
        )}
        {run.error && <div class="error small">{run.error}</div>}
        {run.status === 'done' && (
          <div class="row" style="gap:8px">
            <a class="tiny mono" href={`/rate/${run.version}`} target="_blank" rel="noreferrer">
              {rateUrl}
            </a>
            <CopyButton text={rateUrl} label={S.train.rateLink} />
          </div>
        )}
      </div>
      <span />
    </div>
  );
}
