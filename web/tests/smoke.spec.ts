import { expect, test, type Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HEADERS = { 'X-Soulsaka-Client': 'e2e' };
const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');

const PAGES: [string, string][] = [
  ['/', 'Capture'],
  ['/chat', 'Chat'],
  ['/memories', 'Memories'],
  ['/corpus', 'Corpus'],
  ['/train', 'Train'],
  ['/settings', 'Settings'],
];

async function expectHeading(page: Page, name: string): Promise<void> {
  await expect(page.getByRole('heading', { name, exact: true }).first()).toBeVisible();
}

/** An OpenAI-compatible stand-in for llama.cpp: streams the prompt back as tokens. */
function startFakeLLM(port: number): http.Server {
  const server = http.createServer((req, res) => {
    let body = '';
    req.on('data', (c: Buffer) => (body += c.toString()));
    req.on('end', () => {
      if (req.method === 'GET') {
        res.writeHead(200, { 'content-type': 'application/json' });
        res.end('{"data":[]}');
        return;
      }
      let parsed: { stream?: boolean; messages?: { role: string; content: string }[] } = {};
      try {
        parsed = JSON.parse(body) as typeof parsed;
      } catch {
        /* ignore */
      }
      const last = parsed.messages?.at(-1)?.content ?? '';
      if (parsed.stream) {
        res.writeHead(200, { 'content-type': 'text/event-stream' });
        const words = `You said: ${last}. Noted.`.split(' ');
        let i = 0;
        const t = setInterval(() => {
          const w = words[i];
          if (w !== undefined) {
            const chunk = { choices: [{ delta: { content: (i ? ' ' : '') + w } }] };
            res.write(`data: ${JSON.stringify(chunk)}\n\n`);
            i++;
          } else {
            clearInterval(t);
            res.write('data: [DONE]\n\n');
            res.end();
          }
        }, 20);
        return;
      }
      // Non-streaming calls come from the background memory extractor: answer with nothing.
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ choices: [{ message: { content: '{"memories": []}' } }] }));
    });
  });
  server.on('error', () => undefined);
  server.listen(port, '127.0.0.1');
  return server;
}

let llm: http.Server | null = null;
test.beforeAll(() => {
  llm = startFakeLLM(Number(process.env.SOULSAKA_E2E_LLM_PORT ?? 8769));
});
test.afterAll(() => {
  llm?.close();
});

test.describe('smoke', () => {
  test('every page renders and the event stream connects', async ({ page }) => {
    for (const [path, heading] of PAGES) {
      await page.goto(path);
      await expectHeading(page, heading);
    }
    await page.goto('/rate/v1');
    await expectHeading(page, 'Which one is real?');
    await page.goto('/');
    await expect(page.locator('.sidebar .conn-dot[data-state="live"]')).toBeVisible();
  });

  test('a typed capture becomes a memory and counts toward the corpus', async ({ page }) => {
    await page.goto('/');
    await expectHeading(page, 'Capture');
    await page.getByRole('textbox', { name: 'Capture' }).fill('remember the door code is 4521');
    await page.getByRole('button', { name: 'Send' }).click();

    const item = page.locator('.capture-item', { hasText: 'remember the door code is 4521' });
    await expect(item).toBeVisible();
    await expect(item.locator('.chip[data-status="done"]')).toBeVisible();
    await expect(item.locator('.memory-link')).toContainText('door code is 4521');

    await page.goto('/memories');
    await expectHeading(page, 'Memories');
    await expect(page.locator('.memory-text', { hasText: 'door code is 4521' })).toBeVisible();

    await page.goto('/corpus');
    await expectHeading(page, 'Corpus');
    const hero = page.locator('.hero-number');
    await expect(hero).toBeVisible();
    await expect(hero).not.toHaveText('0');
    const words = Number((await hero.textContent())?.replace(/[^\d]/g, ''));
    expect(words).toBeGreaterThan(0);
  });

  test('a memory spoken elsewhere appears live on the Memories page', async ({ page }) => {
    await page.goto('/memories');
    await expectHeading(page, 'Memories');
    await expect(page.locator('.sidebar .conn-dot[data-state="live"]')).toBeVisible();
    const uid = 'e2e' + Date.now().toString(16).padStart(29, '0');
    const res = await page.request.post('/api/captures', {
      headers: HEADERS,
      data: {
        uid,
        kind: 'text',
        origin: 'manual',
        client_ts: new Date().toISOString(),
        text: 'remember the wifi password is hunter2',
      },
    });
    expect(res.status()).toBe(201);
    // No navigation: the memory must arrive through /api/events.
    await expect(page.locator('.memory-text', { hasText: 'wifi password is hunter2' })).toBeVisible();
  });

  test('memories can be added, edited and archived', async ({ page }) => {
    await page.goto('/memories');
    await page.getByRole('textbox', { name: 'Add a memory' }).fill('Parking spot is B12 this week');
    await page.getByRole('button', { name: 'Save' }).click();
    const item = page.locator('.memory-item', { hasText: 'Parking spot is B12' });
    await expect(item).toBeVisible();
    await item.locator('.memory-text').click();
    // Once editing, the text lives in the textarea's value, so re-locate by structure.
    const editor = page.locator('.memory-item', { has: page.locator('textarea') });
    await editor.locator('textarea').fill('Parking spot is C3 this week');
    await editor.getByRole('button', { name: 'Save' }).click();
    const edited = page.locator('.memory-item', { hasText: 'Parking spot is C3' });
    await expect(edited).toBeVisible();
    await edited.getByRole('button', { name: 'Archive' }).click();
    await expect(edited).toBeHidden();
    await page.getByLabel('Show archived').check();
    await expect(page.locator('.memory-item.archived', { hasText: 'Parking spot is C3' })).toBeVisible();
  });

  test('captures queue while offline and upload when the network is back', async ({ page, context }) => {
    await page.goto('/');
    await expectHeading(page, 'Capture');
    await expect(page.locator('.sidebar .conn-dot[data-state="live"]')).toBeVisible();
    await context.setOffline(true);
    await page.getByRole('textbox', { name: 'Capture' }).fill('offline note about the ferry timetable');
    await page.getByRole('button', { name: 'Send' }).click();
    const item = page.locator('.capture-item', { hasText: 'ferry timetable' });
    await expect(item.locator('.chip[data-status="queued"]')).toBeVisible();
    await expect(page.locator('.banner')).toContainText('offline');
    await expect(page.locator('.sidebar .badge-count')).toHaveText('1');
    await context.setOffline(false);
    await expect(item.locator('.chip[data-status="done"]')).toBeVisible();
    await expect(page.locator('.sidebar .badge-count')).toHaveCount(0);
  });

  test('push-to-talk records with the microphone and uploads the clip', async ({ page }) => {
    await page.goto('/');
    const ptt = page.getByRole('button', { name: 'Hold to talk' });
    const box = await ptt.boundingBox();
    if (!box) throw new Error('push-to-talk button not laid out');
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await expect(page.locator('.ptt.recording')).toBeVisible();
    await page.waitForTimeout(1500);
    await page.mouse.up();
    await expect(page.locator('.ptt.recording')).toHaveCount(0);
    // The fake microphone carries no speech, so the hub keeps the upload but discards it.
    const item = page.locator('.capture-item', { hasText: 'No speech found' });
    await expect(item).toBeVisible({ timeout: 45_000 });
    await expect(item.locator('.chip[data-status="discarded"]')).toBeVisible();
  });

  test('chat streams a reply from the local model and keeps the thread', async ({ page }) => {
    await page.goto('/chat');
    await expectHeading(page, 'Chat');
    await page.getByRole('textbox', { name: 'Message' }).fill('hello there');
    await page.getByRole('button', { name: 'Send' }).click();
    await expect(page.locator('.msg.assistant').last()).toContainText('You said: hello there. Noted.');
    await expect(page).toHaveURL(/\/chat\/[0-9a-f]{32}$/);
    await expect(page.locator('.chat-list-item.active')).toContainText('hello there');
    await page.reload();
    await expect(page.locator('.msg.user').first()).toContainText('hello there');
    await expect(page.locator('.msg.assistant').first()).toContainText('You said: hello there');
  });

  test('a device that is not trusted is asked to pair and can use a ?pair= link', async ({ browser }) => {
    const pairDir = process.env.SOULSAKA_E2E_PAIR_DATA_DIR;
    const pairUrl = process.env.SOULSAKA_E2E_PAIR_URL;
    test.skip(!pairDir || !pairUrl, 'pairing hub not configured');
    const out = execFileSync('uv', ['run', 'soulsaka', 'pair'], {
      cwd: repoRoot,
      env: { ...process.env, SOULSAKA_DATA_DIR: pairDir, NO_COLOR: '1', TERM: 'dumb' },
      encoding: 'utf8',
    });
    // rich may still emit escape codes under FORCE_COLOR; strip them before matching.
    const plain = out.replace(/\x1b\[[0-9;]*m/g, '');
    const code = /pairing code:\s+([A-Z0-9]{8})/.exec(plain)?.[1];
    expect(code, out).toBeTruthy();
    const context = await browser.newContext({ baseURL: pairUrl });
    const page = await context.newPage();
    await page.goto('/');
    await expectHeading(page, 'Pair this device');
    await page.goto(`/?pair=${code}`);
    await expect(page.getByLabel('Pairing code')).toHaveValue(code ?? '');
    await page.getByLabel('Name this device').fill('e2e phone');
    await page.getByRole('button', { name: 'Pair' }).click();
    await expectHeading(page, 'Capture');
    await expect(page.locator('.sidebar .conn-dot[data-state="live"]')).toBeVisible();
    await page.goto('/settings');
    await expect(page.locator('.kv', { hasText: 'e2e phone' }).first()).toBeVisible();
    await expect(page.locator('.device-item', { hasText: 'e2e phone' })).toBeVisible();
    await context.close();
  });

  test('phone layout shows the tab bar and the capture composer @phone', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.tabbar')).toBeVisible();
    await expect(page.locator('.sidebar')).toBeHidden();
    await expect(page.getByRole('button', { name: 'Hold to talk' })).toBeVisible();
    await page.locator('.tabbar').getByRole('link', { name: 'Memories' }).click();
    await expectHeading(page, 'Memories');
  });
});
