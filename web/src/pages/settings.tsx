import type { JSX } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import { api, isApiError } from '../api';
import { clearCredentials, getDeviceUid, getHubUrl, getToken, hubOrigin, setHubUrl } from '../auth';
import { Icon } from '../components/icon';
import { Card, Chip, CopyButton, ErrorNote, Field, Segmented, Spinner, useAsync, useNow } from '../components/ui';
import { fmtDateTime, fmtInt, relTime, truncate } from '../format';
import { deviceTick, speakerTick, voiceTick } from '../store';
import { S } from '../strings';
import { applyTheme, theme, type Theme } from '../theme';

export function SettingsPage(): JSX.Element {
  return (
    <div class="page">
      <header class="page-head">
        <h1 class="page-title">{S.settings.title}</h1>
      </header>
      <DeviceCard />
      <PairCard />
      <DevicesCard />
      <SpeakerCard />
      <VoiceCard />
      <ConfigCards />
      <JobsCard />
      <AppearanceCard />
      <HubCard />
    </div>
  );
}

function DeviceCard(): JSX.Element {
  const me = useAsync(api.me, []);
  const hasToken = getToken() !== null;
  return (
    <Card title={S.settings.device}>
      {me.error ? <ErrorNote error={me.error} onRetry={me.reload} /> : null}
      {me.data && (
        <dl class="kv">
          <dt>{S.settings.deviceName}</dt>
          <dd>{me.data.name}</dd>
          <dt>{S.settings.deviceKind}</dt>
          <dd>{me.data.kind}</dd>
          <dt>{S.settings.deviceUid}</dt>
          <dd class="mono">{me.data.uid}</dd>
          <dt>{S.settings.paired}</dt>
          <dd>{fmtDateTime(me.data.created_at) || '–'}</dd>
        </dl>
      )}
      {me.data?.uid === 'local' && <p class="hint" style="margin-top:10px">{S.settings.localDevice}</p>}
      {hasToken && (
        <div style="margin-top:12px">
          <button
            class="btn btn-danger btn-sm"
            onClick={() => {
              clearCredentials();
              location.reload();
            }}
          >
            {S.settings.forget}
          </button>
        </div>
      )}
    </Card>
  );
}

function PairCard(): JSX.Element {
  const [code, setCode] = useState<{ code: string; expires: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const now = useNow(15_000);
  const mint = async (): Promise<void> => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.pairingCode();
      setCode({ code: r.code, expires: Date.now() + r.ttl_s * 1000 });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  const url = code ? `${hubOrigin()}/?pair=${code.code}` : '';
  const minutes = code ? Math.max(0, Math.round((code.expires - now) / 60_000)) : 0;
  return (
    <Card title={S.settings.pairAnother} lead={S.settings.pairLead}>
      <div class="stack">
        {code && (
          <>
            <div class="big-code" aria-live="polite">
              {code.code}
            </div>
            <p class="small muted" style="text-align:center">
              {S.settings.codeExpires(minutes)}
            </p>
            <div class="stack-sm">
              <span class="label">{S.settings.openOn}</span>
              <div class="row">
                <code class="code-block" style="flex:1;padding:8px 12px">
                  {url}
                </code>
                <CopyButton text={url} />
              </div>
            </div>
          </>
        )}
        <div class="row">
          <button class="btn btn-primary btn-sm" disabled={busy} onClick={() => void mint()}>
            {busy ? <Spinner /> : <Icon name="key" size={15} />}
            {busy ? S.settings.minting : S.settings.mint}
          </button>
          {code && <CopyButton text={code.code} label={S.common.copy} />}
        </div>
        {error ? <ErrorNote error={error} /> : null}
      </div>
    </Card>
  );
}

function DevicesCard(): JSX.Element {
  const devices = useAsync(api.devices, [deviceTick.value]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const mine = getDeviceUid();
  const revoke = async (uid: string, name: string): Promise<void> => {
    if (!confirm(S.settings.confirmRevoke(name))) return;
    setBusy(uid);
    setError(null);
    try {
      await api.revokeDevice(uid);
      if (uid === mine) {
        clearCredentials();
        location.reload();
        return;
      }
      devices.reload();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  };
  return (
    <Card title={S.settings.devices}>
      {devices.error ? <ErrorNote error={devices.error} onRetry={devices.reload} /> : null}
      {devices.data && devices.data.length === 0 && <p class="muted">{S.settings.noDevices}</p>}
      {devices.data?.map((d) => (
        <div key={d.uid} class="device-item">
          <Icon name="device" size={18} />
          <div class="body">
            <div>
              <strong>{d.name}</strong> <span class="muted small">{d.kind}</span>{' '}
              {d.uid === mine && <Chip class="chip-me" label={S.settings.device.toLowerCase()} />}
            </div>
            <div class="tiny muted">
              {S.settings.paired} {fmtDateTime(d.created_at)}
              {d.last_seen_at && ` · ${S.settings.lastSeen} ${relTime(d.last_seen_at)}`}
            </div>
          </div>
          <button class="btn btn-danger btn-sm" disabled={busy === d.uid} onClick={() => void revoke(d.uid, d.name)}>
            {S.settings.revoke}
          </button>
        </div>
      ))}
      {error ? <ErrorNote error={error} /> : null}
    </Card>
  );
}

function SpeakerCard(): JSX.Element {
  const sp = useAsync(api.speaker, [speakerTick.value]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const reset = async (): Promise<void> => {
    if (!confirm(S.settings.confirmResetSpeaker)) return;
    setBusy(true);
    try {
      await api.resetSpeaker();
      sp.reload();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  const s = sp.data;
  return (
    <Card title={S.settings.speaker} lead={S.settings.speakerLead}>
      {sp.error ? <ErrorNote error={sp.error} onRetry={sp.reload} /> : null}
      {s && (
        <div class="stack">
          <div class="row">
            <Chip
              class={s.ready ? 'chip-me' : s.enrolled ? '' : 'chip-unknown'}
              label={s.ready ? S.settings.ready : s.enrolled ? S.settings.enrolled : S.settings.notEnrolled}
            />
            {!s.ready && typeof s.min_samples === 'number' && (
              <span class="small dim">{S.settings.notReady(s.n_samples ?? 0, s.min_samples)}</span>
            )}
          </div>
          <dl class="kv">
            <dt>{S.settings.samples}</dt>
            <dd>{fmtInt(s.n_samples ?? 0)}</dd>
            <dt>{S.settings.threshold}</dt>
            <dd>{typeof s.threshold === 'number' ? s.threshold.toFixed(2) : '–'}</dd>
            <dt>{S.settings.backend}</dt>
            <dd>{s.backend ?? '–'}</dd>
          </dl>
          {s.error && <div class="error small">{s.error}</div>}
          <div>
            <button class="btn btn-danger btn-sm" disabled={busy} onClick={() => void reset()}>
              {S.settings.resetSpeaker}
            </button>
          </div>
        </div>
      )}
      {error ? <ErrorNote error={error} /> : null}
    </Card>
  );
}

function useObjectUrl(): [string | null, (blob: Blob | null) => void] {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => () => { if (url) URL.revokeObjectURL(url); }, [url]);
  return [url, (blob) => setUrl(blob ? URL.createObjectURL(blob) : null)];
}

function VoiceCard(): JSX.Element {
  const ref = useAsync(api.voiceReference, [voiceTick.value]);
  const [building, setBuilding] = useState(false);
  const [built, setBuilt] = useState<number | null>(null);
  const [buildError, setBuildError] = useState<unknown>(null);
  const [text, setText] = useState('');
  const [speaking, setSpeaking] = useState(false);
  const [speakError, setSpeakError] = useState<unknown>(null);
  const [audioUrl, setAudio] = useState<Blob | null>(null);
  const [objectUrl, setObjectUrl] = useObjectUrl();
  useEffect(() => setObjectUrl(audioUrl), [audioUrl]); // eslint-disable-line react-hooks/exhaustive-deps

  const build = async (): Promise<void> => {
    setBuilding(true);
    setBuildError(null);
    setBuilt(null);
    try {
      const out = await api.buildVoiceReference();
      setBuilt(out.seconds);
      ref.reload();
    } catch (e) {
      setBuildError(e);
    } finally {
      setBuilding(false);
    }
  };
  const play = async (): Promise<void> => {
    setSpeakError(null);
    try {
      setAudio(await api.voiceReferenceAudio());
    } catch (e) {
      setSpeakError(e);
    }
  };
  const speak = async (): Promise<void> => {
    const t = text.trim();
    if (!t || speaking) return;
    setSpeaking(true);
    setSpeakError(null);
    try {
      setAudio(await api.speak(t));
    } catch (e) {
      setSpeakError(e);
    } finally {
      setSpeaking(false);
    }
  };
  const r = ref.data;
  const unavailable = isApiError(ref.error, 404);
  return (
    <Card title={S.settings.voice} lead={S.settings.voiceLead}>
      {unavailable && <p class="muted small">{S.errors.notImplemented}</p>}
      {ref.error && !unavailable ? <ErrorNote error={ref.error} onRetry={ref.reload} /> : null}
      {r && (
        <div class="stack">
          <dl class="kv">
            <dt>{S.settings.voiceReference}</dt>
            <dd>{r.reference_clip ? S.settings.voiceReady : S.settings.voiceNone}</dd>
            <dt>{S.settings.voiceCandidates}</dt>
            <dd>{fmtInt(r.candidates)}</dd>
            {r.reference_text && (
              <>
                <dt>{S.settings.voiceText}</dt>
                <dd class="small dim">{truncate(r.reference_text, 160)}</dd>
              </>
            )}
          </dl>
          <div class="row">
            <button class="btn btn-sm" disabled={building} onClick={() => void build()}>
              {building ? <Spinner /> : <Icon name="mic" size={15} />}
              {S.settings.voiceBuild}
            </button>
            {r.reference_clip && (
              <button class="btn btn-sm" onClick={() => void play()}>
                <Icon name="radio" size={15} />
                {S.settings.voicePlay}
              </button>
            )}
            {built !== null && <span class="small" style="color:var(--ok)">{S.settings.voiceBuilt(built)}</span>}
          </div>
          {buildError ? <ErrorNote error={buildError} /> : null}
          <form
            class="composer"
            onSubmit={(e) => {
              e.preventDefault();
              void speak();
            }}
          >
            <input
              class="input"
              value={text}
              placeholder={S.settings.voiceSayPlaceholder}
              aria-label={S.settings.voiceSpeak}
              onInput={(e) => setText(e.currentTarget.value)}
            />
            <button class="btn btn-primary" type="submit" disabled={speaking || !text.trim()}>
              {speaking ? <Spinner /> : <Icon name="radio" size={18} />}
              <span>{S.settings.voiceSpeak}</span>
            </button>
          </form>
          {speakError ? (
            isApiError(speakError, 503) ? (
              <div class="note">{S.settings.voiceUnavailable}</div>
            ) : (
              <ErrorNote error={speakError} />
            )
          ) : null}
          {objectUrl && <audio controls autoplay src={objectUrl} style="width:100%" />}
        </div>
      )}
    </Card>
  );
}

function ConfigCards(): JSX.Element {
  const cfg = useAsync(api.config, []);
  const health = useAsync(api.health, []);
  const c = cfg.data;
  return (
    <>
      <Card title={S.settings.privacy} lead={S.settings.privacyEdit}>
        {cfg.error ? <ErrorNote error={cfg.error} onRetry={cfg.reload} /> : null}
        {c && (
          <dl class="kv">
            <dt>{S.settings.privacyOther}</dt>
            <dd>{S.settings.privacyOtherValues[c.privacy.other_speakers] ?? c.privacy.other_speakers}</dd>
            <dt>{S.settings.privacyAudio}</dt>
            <dd>{c.privacy.keep_audio ? S.common.yes : S.common.no}</dd>
            <dt>{S.settings.privacyCloud}</dt>
            <dd>
              {c.privacy.allow_cloud_llm ? S.settings.privacyCloudOn : S.settings.privacyCloudOff}{' '}
              <span class="muted tiny mono">privacy.allow_cloud_llm</span>
            </dd>
            <dt>{S.settings.privacyNames}</dt>
            <dd>{c.privacy.keep_contact_names ? S.settings.privacyNamesOn : S.settings.privacyNamesOff}</dd>
          </dl>
        )}
      </Card>
      <Card title={S.settings.llm}>
        {c && (
          <div class="table-wrap">
            <table class="table">
              <thead>
                <tr>
                  <th>{S.settings.llmProfile}</th>
                  <th>{S.settings.backend}</th>
                  <th>{S.settings.llmModel}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {c.llm.profiles.map((p) => (
                  <tr key={p.name} style={p.enabled ? '' : 'opacity:.55'}>
                    <td>
                      <strong>{p.name}</strong>
                      {p.default && <span class="muted tiny"> · {S.settings.llmDefault}</span>}
                    </td>
                    <td>{p.backend}</td>
                    <td class="mono small">{p.model}</td>
                    <td>
                      <span class="row" style="gap:6px">
                        {p.cloud && <Chip class="chip-cloud" label={S.chat.cloud} />}
                        {p.personal && !p.cloud && <Chip class="chip-me" label={S.chat.personal} />}
                        {!p.enabled && <Chip label={S.common.off} />}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <Card title={S.settings.about}>
        <dl class="kv">
          <dt>{S.app.name}</dt>
          <dd>{health.data ? S.settings.version(health.data.version) : '–'}</dd>
          {c && (
            <>
              <dt>{S.settings.me}</dt>
              <dd>
                {c.me.display_name || '–'}
                {c.me.names.length > 0 && <span class="muted"> · {c.me.names.join(', ')}</span>}
              </dd>
              <dt>{S.settings.asr}</dt>
              <dd>
                {c.asr.backend} <span class="muted">{c.asr.model}</span>
              </dd>
              <dt>{S.settings.trainBase}</dt>
              <dd>
                {c.train.base_model} <span class="muted">{c.train.backend}</span>
              </dd>
            </>
          )}
        </dl>
      </Card>
    </>
  );
}

function JobsCard(): JSX.Element {
  const jobs = useAsync(api.jobs, []);
  const counts = Object.entries(jobs.data?.counts ?? {});
  return (
    <Card
      title={S.settings.jobs}
      actions={
        <button class="btn btn-ghost btn-sm btn-icon" onClick={jobs.reload} aria-label={S.common.refresh} title={S.common.refresh}>
          <Icon name="refresh" size={16} />
        </button>
      }
    >
      {jobs.error ? <ErrorNote error={jobs.error} onRetry={jobs.reload} /> : null}
      {jobs.data && counts.length === 0 && <p class="muted">{S.settings.jobsNone}</p>}
      {counts.length > 0 && (
        <div class="row" style="margin-bottom:12px">
          {counts.map(([status, n]) => (
            <Chip key={status} status={status === 'queued' ? 'queued-job' : status} label={`${status} ${n}`} />
          ))}
        </div>
      )}
      {jobs.data && jobs.data.recent.length > 0 && (
        <div class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th>#</th>
                <th>kind</th>
                <th>status</th>
                <th>when</th>
                <th>{S.train.error}</th>
              </tr>
            </thead>
            <tbody>
              {jobs.data.recent.slice(0, 15).map((j) => (
                <tr key={j.id}>
                  <td class="mono">{j.id}</td>
                  <td>{j.kind}</td>
                  <td>
                    <Chip status={j.status === 'queued' ? 'queued-job' : j.status} label={j.status} />
                  </td>
                  <td class="small">{relTime(j.finished_at ?? j.started_at ?? j.created_at)}</td>
                  <td class="small muted">{j.error ? truncate(j.error, 80) : ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function AppearanceCard(): JSX.Element {
  return (
    <Card title={S.settings.theme}>
      <Segmented<Theme>
        value={theme.value}
        onChange={applyTheme}
        options={[
          { value: 'system', label: S.settings.themeSystem },
          { value: 'light', label: S.settings.themeLight },
          { value: 'dark', label: S.settings.themeDark },
        ]}
      />
    </Card>
  );
}

function HubCard(): JSX.Element {
  const [url, setUrl] = useState(getHubUrl());
  const [saved, setSaved] = useState(false);
  return (
    <Card title={S.settings.hub}>
      <form
        class="stack"
        onSubmit={(e) => {
          e.preventDefault();
          setHubUrl(url);
          setSaved(true);
          window.setTimeout(() => location.reload(), 300);
        }}
      >
        <Field label={S.settings.hubUrl} hint={S.settings.hubHint}>
          <input
            class="input"
            value={url}
            placeholder={location.origin}
            inputMode="url"
            autoCapitalize="off"
            onInput={(e) => setUrl(e.currentTarget.value)}
          />
        </Field>
        <div class="row">
          <button class="btn btn-sm" type="submit">
            {S.common.save}
          </button>
          {saved && <span class="small muted">{S.settings.hubSaved}</span>}
        </div>
      </form>
    </Card>
  );
}
