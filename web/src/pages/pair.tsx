import type { JSX } from 'preact';
import { useState } from 'preact/hooks';
import { api, authState, isApiError, errorMessage } from '../api';
import { getHubUrl, setCredentials, setHubUrl } from '../auth';
import { Icon } from '../components/icon';
import { Field } from '../components/ui';
import { navigate } from '../router';
import { S } from '../strings';

function guessDeviceName(): string {
  const ua = navigator.userAgent;
  if (/iPhone/.test(ua)) return 'iPhone';
  if (/iPad/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1)) return 'iPad';
  if (/Android/.test(ua)) return 'Android';
  if (/Macintosh/.test(ua)) return 'Mac';
  if (/Windows/.test(ua)) return 'Windows';
  if (/Linux/.test(ua)) return 'Linux';
  return 'Browser';
}

export function PairPage({
  initialCode,
  onPaired,
}: {
  initialCode: string;
  onPaired: () => void;
}): JSX.Element {
  const [code, setCode] = useState(initialCode);
  const [name, setName] = useState(guessDeviceName());
  const [hub, setHub] = useState(getHubUrl());
  const [showHub, setShowHub] = useState(getHubUrl() !== '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: Event): Promise<void> => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    setHubUrl(hub);
    try {
      const res = await api.pair(code, name.trim() || guessDeviceName());
      setCredentials(res.token, res.device_uid);
      authState.value = 'ok';
      navigate('/', true);
      onPaired();
    } catch (err) {
      setError(isApiError(err, 400) ? S.pair.invalid : errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div class="pair-wrap">
      <form class="card pair-card stack" onSubmit={(e) => void submit(e)}>
        <div class="brand" style="padding:0">
          <span class="brand-mark">
            <Icon name="sparkle" size={16} />
          </span>
          <div>
            <div class="brand-name">{S.app.name}</div>
            <div class="brand-tag">{S.app.tagline}</div>
          </div>
        </div>
        <h1 class="page-title" style="font-size:22px">
          {S.pair.title}
        </h1>
        <p class="dim small">{S.pair.lead}</p>
        <Field label={S.pair.codeLabel}>
          <input
            class="input code-input"
            value={code}
            maxLength={8}
            autoComplete="one-time-code"
            autoCapitalize="characters"
            autoCorrect="off"
            spellcheck={false}
            inputMode="text"
            onInput={(e) => setCode(e.currentTarget.value.toUpperCase().replace(/[^A-Z0-9]/g, ''))}
            required
          />
        </Field>
        <Field label={S.pair.nameLabel}>
          <input
            class="input"
            value={name}
            placeholder={S.pair.namePlaceholder}
            onInput={(e) => setName(e.currentTarget.value)}
          />
        </Field>
        {showHub ? (
          <Field label={S.pair.hubLabel} hint={S.pair.hubHint}>
            <input
              class="input"
              value={hub}
              placeholder={S.pair.hubPlaceholder}
              inputMode="url"
              autoCapitalize="off"
              onInput={(e) => setHub(e.currentTarget.value)}
            />
          </Field>
        ) : (
          <button type="button" class="btn btn-ghost btn-sm" onClick={() => setShowHub(true)}>
            {S.pair.hubLabel}
          </button>
        )}
        {error && (
          <div class="error" role="alert">
            <Icon name="alert" size={18} />
            <span>{error}</span>
          </div>
        )}
        <button class="btn btn-primary btn-block" type="submit" disabled={busy || code.length < 8}>
          {busy ? S.pair.pairing : S.pair.submit}
        </button>
        <p class="hint">{S.pair.localHint}</p>
      </form>
    </div>
  );
}
