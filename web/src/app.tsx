import type { JSX } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import { api, authState, hubReachable } from './api';
import { Icon, type IconName } from './components/icon';
import { pokeEvents, startEvents } from './events';
import { CapturePage } from './pages/capture';
import { ChatPage } from './pages/chat';
import { CorpusPage } from './pages/corpus';
import { MemoriesPage } from './pages/memories';
import { PairPage } from './pages/pair';
import { RatePage } from './pages/rate';
import { SettingsPage } from './pages/settings';
import { TrainPage } from './pages/train';
import { flushQueue, queued, startQueue } from './queue';
import { currentPath, matchRoute, onLinkClick, type Route } from './router';
import { connection, online } from './store';
import { S } from './strings';

interface NavItem {
  page: Route['page'];
  path: string;
  label: string;
  icon: IconName;
  tab: boolean;
}

const NAV: readonly NavItem[] = [
  { page: 'capture', path: '/', label: S.nav.capture, icon: 'mic', tab: true },
  { page: 'chat', path: '/chat', label: S.nav.chat, icon: 'chat', tab: true },
  { page: 'memories', path: '/memories', label: S.nav.memories, icon: 'memory', tab: true },
  { page: 'corpus', path: '/corpus', label: S.nav.corpus, icon: 'corpus', tab: true },
  { page: 'train', path: '/train', label: S.nav.train, icon: 'train', tab: true },
  { page: 'settings', path: '/settings', label: S.nav.settings, icon: 'settings', tab: false },
];

let booted = false;

async function boot(): Promise<void> {
  if (booted) return;
  booted = true;
  try {
    await api.me();
    authState.value = 'ok';
  } catch {
    // A 401 already flipped the state; anything else (hub down, offline) still gets the
    // app so captures can queue. A later 401 shows the pairing screen.
    if (authState.value !== 'unauthorized') authState.value = 'ok';
  }
  if (authState.value === 'ok') {
    startQueue();
    startEvents();
  }
}

export function App(): JSX.Element {
  const route = matchRoute(currentPath.value);
  if (route.page === 'rate') return <RatePage version={route.version} />;
  return <AuthedApp route={route} />;
}

function AuthedApp({ route }: { route: Route }): JSX.Element {
  const [pairCode] = useState(() => {
    const code = new URLSearchParams(location.search).get('pair');
    if (code) history.replaceState(null, '', location.pathname);
    return (code ?? '').toUpperCase();
  });
  useEffect(() => {
    void boot();
  }, []);
  if (authState.value === 'unauthorized') {
    return (
      <PairPage
        initialCode={pairCode}
        onPaired={() => {
          startQueue();
          startEvents();
          void flushQueue();
        }}
      />
    );
  }
  return <Shell route={route} />;
}

function Shell({ route }: { route: Route }): JSX.Element {
  const pending = queued.value.length;
  const current = NAV.find((n) => n.page === route.page);
  return (
    <div class="shell">
      <aside class="sidebar">
        <div class="brand">
          <span class="brand-mark">
            <Icon name="sparkle" size={16} />
          </span>
          <div>
            <div class="brand-name">{S.app.name}</div>
            <div class="brand-tag">{S.app.tagline}</div>
          </div>
        </div>
        <nav class="nav" aria-label="Main">
          {NAV.map((n) => (
            <a key={n.path} class={`nav-item ${n.page === route.page ? 'active' : ''}`} href={n.path} onClick={onLinkClick}>
              <Icon name={n.icon} size={19} />
              <span>{n.label}</span>
              {n.page === 'capture' && pending > 0 && <span class="badge-count">{pending}</span>}
            </a>
          ))}
        </nav>
        <div class="sidebar-foot">
          <ConnStatus />
        </div>
      </aside>
      <div class="content">
        <header class="topbar">
          <span class="topbar-title">{current?.label ?? S.app.name}</span>
          <ConnStatus compact />
          <a class="btn btn-ghost btn-icon" href="/settings" onClick={onLinkClick} aria-label={S.nav.settings}>
            <Icon name="settings" size={20} />
          </a>
        </header>
        <Banners />
        <main class="main">
          <Page route={route} />
        </main>
      </div>
      <nav class="tabbar" aria-label="Main">
        {NAV.filter((n) => n.tab).map((n) => (
          <a key={n.path} class={`tab ${n.page === route.page ? 'active' : ''}`} href={n.path} onClick={onLinkClick}>
            <Icon name={n.icon} size={22} />
            <span>{n.label}</span>
            {n.page === 'capture' && pending > 0 && <span class="badge-count">{pending}</span>}
          </a>
        ))}
      </nav>
    </div>
  );
}

function Page({ route }: { route: Route }): JSX.Element {
  switch (route.page) {
    case 'capture':
      return <CapturePage />;
    case 'chat':
      return <ChatPage chatUid={route.chatUid} />;
    case 'memories':
      return <MemoriesPage />;
    case 'corpus':
      return <CorpusPage />;
    case 'train':
      return <TrainPage />;
    case 'settings':
      return <SettingsPage />;
    case 'rate':
      return <RatePage version={route.version} />;
    default:
      return (
        <div class="empty">
          404 · <a href="/" onClick={onLinkClick}>{S.nav.capture}</a>
        </div>
      );
  }
}

function connState(): { state: string; label: string } {
  if (!online.value) return { state: 'offline', label: S.conn.offline };
  if (authState.value === 'unauthorized') return { state: 'down', label: S.conn.unauthorized };
  const c = connection.value;
  if (c === 'live') return { state: 'live', label: S.conn.live };
  if (c === 'down') return { state: 'down', label: S.conn.down };
  return { state: 'connecting', label: S.conn.connecting };
}

function ConnStatus({ compact }: { compact?: boolean }): JSX.Element {
  const { state, label } = connState();
  return (
    <span class="conn" title={label}>
      <span class="conn-dot" data-state={state} />
      {!compact && <span>{label}</span>}
    </span>
  );
}

function Banners(): JSX.Element | null {
  const n = queued.value.length;
  const retry = (): void => {
    pokeEvents();
    void flushQueue();
  };
  if (!online.value) {
    return (
      <div class="banner" data-tone="bad" role="status">
        <Icon name="wifi-off" size={18} />
        <span>
          {S.banners.offline}
          {n > 0 && ` · ${S.banners.pending(n)}`}
        </span>
      </div>
    );
  }
  if (connection.value === 'down' || !hubReachable.value) {
    return (
      <div class="banner" role="status">
        <Icon name="alert" size={18} />
        <span>
          {S.banners.hubDown}
          {n > 0 && ` · ${S.banners.pending(n)}`}
        </span>
        <button class="btn btn-sm" onClick={retry}>
          {S.banners.retryNow}
        </button>
      </div>
    );
  }
  if (n > 0) {
    return (
      <div class="banner" data-tone="info" role="status">
        <Icon name="upload" size={18} />
        <span>{S.banners.pending(n)}</span>
        <button class="btn btn-sm" onClick={retry}>
          {S.banners.retryNow}
        </button>
      </div>
    );
  }
  return null;
}
