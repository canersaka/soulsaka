/** Theme override on top of prefers-color-scheme. */

import { signal } from '@preact/signals';
import { readLocal, writeLocal } from './auth';

export type Theme = 'system' | 'light' | 'dark';
const KEY = 'soulsaka.theme';

function readTheme(): Theme {
  const v = readLocal(KEY);
  return v === 'light' || v === 'dark' ? v : 'system';
}

export const theme = signal<Theme>(readTheme());

export function applyTheme(t: Theme): void {
  theme.value = t;
  writeLocal(KEY, t === 'system' ? null : t);
  const root = document.documentElement;
  if (t === 'system') root.removeAttribute('data-theme');
  else root.setAttribute('data-theme', t);
}
