/** A hand-rolled router on location.pathname. The hub serves index.html for every path. */

import { signal } from '@preact/signals';

export type Route =
  | { page: 'capture' }
  | { page: 'chat'; chatUid: string | null }
  | { page: 'memories' }
  | { page: 'corpus' }
  | { page: 'train' }
  | { page: 'rate'; version: string }
  | { page: 'settings' }
  | { page: 'notfound' };

export const currentPath = signal(location.pathname);

export function navigate(to: string, replace = false): void {
  if (to === location.pathname + location.search) return;
  if (replace) history.replaceState(null, '', to);
  else history.pushState(null, '', to);
  currentPath.value = location.pathname;
  window.scrollTo(0, 0);
}

window.addEventListener('popstate', () => {
  currentPath.value = location.pathname;
});

export function matchRoute(path: string): Route {
  const parts = path.split('/').filter(Boolean).map(decodeURIComponent);
  const [head, second] = parts;
  if (!head) return { page: 'capture' };
  switch (head) {
    case 'chat':
      return { page: 'chat', chatUid: second ?? null };
    case 'memories':
      return { page: 'memories' };
    case 'corpus':
      return { page: 'corpus' };
    case 'train':
      return { page: 'train' };
    case 'rate':
      return { page: 'rate', version: second ?? 'latest' };
    case 'settings':
      return { page: 'settings' };
    default:
      return { page: 'notfound' };
  }
}

/** Intercept plain left-clicks on in-app links. */
export function onLinkClick(e: MouseEvent): void {
  if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) {
    return;
  }
  const a = e.currentTarget as HTMLAnchorElement | null;
  const href = a?.getAttribute('href');
  if (!href || !href.startsWith('/')) return;
  e.preventDefault();
  navigate(href);
}
