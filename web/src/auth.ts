/** Device credentials and the hub address, kept in localStorage. */

const KEY_TOKEN = 'soulsaka.token';
const KEY_DEVICE = 'soulsaka.device';
const KEY_HUB = 'soulsaka.hub';

export function readLocal(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function writeLocal(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    /* private mode or storage disabled: the app still works for this session */
  }
}

export function getToken(): string | null {
  return readLocal(KEY_TOKEN);
}

export function getDeviceUid(): string | null {
  return readLocal(KEY_DEVICE);
}

export function setCredentials(token: string, deviceUid: string): void {
  writeLocal(KEY_TOKEN, token);
  writeLocal(KEY_DEVICE, deviceUid);
}

export function clearCredentials(): void {
  writeLocal(KEY_TOKEN, null);
  writeLocal(KEY_DEVICE, null);
}

/** '' means "same origin as this page". */
export function getHubUrl(): string {
  return (readLocal(KEY_HUB) ?? '').trim().replace(/\/+$/, '');
}

export function setHubUrl(url: string): void {
  const clean = url.trim().replace(/\/+$/, '');
  writeLocal(KEY_HUB, clean || null);
}

/** The origin other devices should open, e.g. for pairing links. */
export function hubOrigin(): string {
  return getHubUrl() || location.origin;
}
