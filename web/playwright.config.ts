import { defineConfig } from '@playwright/test';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HOST = '127.0.0.1';
const PORT = Number(process.env.SOULSAKA_E2E_PORT ?? 8766);
/** A second hub that does not trust loopback, so the pairing flow can be exercised. */
const PAIR_PORT = PORT + 2;
/** The fake OpenAI-compatible server the chat test starts (see tests/smoke.spec.ts). */
const LLM_PORT = PORT + 3;

const webDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(webDir, '..');
const dataDir = process.env.SOULSAKA_E2E_DATA_DIR ?? mkdtempSync(path.join(tmpdir(), 'soulsaka-e2e-'));
const pairDataDir = mkdtempSync(path.join(tmpdir(), 'soulsaka-e2e-pair-'));
process.env.SOULSAKA_E2E_PAIR_DATA_DIR = pairDataDir;
process.env.SOULSAKA_E2E_PAIR_URL = `http://${HOST}:${PAIR_PORT}`;
process.env.SOULSAKA_E2E_LLM_PORT = String(LLM_PORT);

// Set PW_CHROMIUM_PATH (e.g. /opt/pw-browsers/chromium) if the bundled browser cannot be resolved.
const executablePath = process.env.PW_CHROMIUM_PATH;

const hubEnv = {
  SOULSAKA_ASR__BACKEND: 'fake',
  SOULSAKA_SPEAKER__BACKEND: 'fake',
  SOULSAKA_EMBED__BACKEND: 'hash',
  SOULSAKA_TTS__BACKEND: 'fake',
  SOULSAKA_LLM__PROFILES__LOCAL__BASE_URL: `http://${HOST}:${LLM_PORT}/v1`,
  SOULSAKA_ME__DISPLAY_NAME: 'Test Person',
};

export default defineConfig({
  testDir: './tests',
  timeout: 90_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: `http://${HOST}:${PORT}`,
    trace: 'retain-on-failure',
    permissions: ['microphone'],
    launchOptions: {
      ...(executablePath ? { executablePath } : {}),
      // A fake microphone so push-to-talk can be exercised headlessly.
      args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'],
    },
  },
  projects: [
    {
      name: 'desktop',
      use: { browserName: 'chromium', viewport: { width: 1280, height: 800 } },
      grepInvert: /@phone/,
    },
    {
      name: 'phone',
      use: {
        browserName: 'chromium',
        viewport: { width: 390, height: 844 },
        isMobile: true,
        hasTouch: true,
        deviceScaleFactor: 2,
      },
      grep: /@phone/,
    },
  ],
  webServer: [
    {
      // A hub with fake ML backends, serving the built web/dist from the repo checkout.
      command: `uv run soulsaka serve --host ${HOST} --port ${PORT}`,
      cwd: repoRoot,
      url: `http://${HOST}:${PORT}/api/health`,
      reuseExistingServer: false,
      timeout: 120_000,
      stdout: 'ignore',
      stderr: 'pipe',
      env: { ...hubEnv, SOULSAKA_DATA_DIR: dataDir },
    },
    {
      command: `uv run soulsaka serve --host ${HOST} --port ${PAIR_PORT}`,
      cwd: repoRoot,
      url: `http://${HOST}:${PAIR_PORT}/api/health`,
      reuseExistingServer: false,
      timeout: 120_000,
      stdout: 'ignore',
      stderr: 'pipe',
      env: { ...hubEnv, SOULSAKA_DATA_DIR: pairDataDir, SOULSAKA_HUB__TRUST_LOOPBACK: 'false' },
    },
  ],
});
