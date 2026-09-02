// Renders the PWA icons (PNG) from one SVG mark with the bundled Chromium.
// Usage: node scripts/make-icons.mjs   (writes into web/public)
import { writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const here = path.dirname(fileURLToPath(import.meta.url));
const out = path.resolve(here, '..', 'public');

const mark = (size) => `<!doctype html><html><head><style>html,body{margin:0;background:transparent}</style></head>
<body><svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 64 64">
  <rect width="64" height="64" fill="#b5533c"/>
  <circle cx="25" cy="32" r="13" fill="#f6f4ee"/>
  <circle cx="39" cy="32" r="13" fill="none" stroke="#f6f4ee" stroke-width="3.2"/>
</svg></body></html>`;

const targets = [
  ['icon-192.png', 192],
  ['icon-512.png', 512],
  ['icon-maskable-512.png', 512],
  ['apple-touch-icon.png', 180],
];

const browser = await chromium.launch({
  executablePath: process.env.PW_CHROMIUM_PATH || undefined,
});
try {
  const page = await browser.newPage();
  for (const [name, size] of targets) {
    await page.setViewportSize({ width: size, height: size });
    await page.setContent(mark(size));
    const png = await page.screenshot({ clip: { x: 0, y: 0, width: size, height: size }, omitBackground: true });
    writeFileSync(path.join(out, name), png);
    console.log(`wrote ${name} (${png.length} bytes)`);
  }
} finally {
  await browser.close();
}
