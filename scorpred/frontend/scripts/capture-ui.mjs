// Capture the implemented app against its real API; no fixture routes or model activation.
// Start Flask first, build the frontend, then run from frontend: node scripts/capture-ui.mjs
import { chromium } from 'playwright';
import { mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';

const baseURL = process.env.SCORPRED_PREVIEW_URL || 'http://127.0.0.1:5001';
const output = fileURLToPath(new URL('../../docs/assets/graphite-sage/', import.meta.url));
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome' });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, timezoneId: 'America/New_York' });
const page = await context.newPage();
const audit = { captured_at: new Date().toISOString(), source: 'Actual running application and APIs; no mocked data', responses: [], page_errors: [], screenshots: [] };
page.on('pageerror', error => audit.page_errors.push(error.message));
page.on('response', response => {
  const url = new URL(response.url());
  if (url.origin === new URL(baseURL).origin && url.pathname.startsWith('/api/')) audit.responses.push({ path: url.pathname, status: response.status() });
});

async function open(path) {
  await page.goto(new URL(path, baseURL).href, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => !document.querySelector('.skeleton-line') && ![...document.querySelectorAll('[role="status"]')].some(item => /loading/i.test(item.textContent || '')), { timeout: 55000 });
  await page.evaluate(() => document.fonts.ready);
}
async function capture(name) {
  const width = await page.evaluate(() => document.documentElement.scrollWidth);
  const viewport = page.viewportSize().width;
  if (width > viewport) throw new Error(`Horizontal overflow in ${name}: ${width} > ${viewport}`);
  await page.screenshot({ path: resolve(output, name), fullPage: true });
  audit.screenshots.push(name);
  console.log(`Captured ${name}`);
}

try {
  await open('/');
  await capture('home-desktop.png');
  await page.setViewportSize({ width: 390, height: 844 });
  await capture('home-mobile.png');
  await page.setViewportSize({ width: 1440, height: 1000 });
  for (const sport of ['nfl', 'nba', 'soccer']) {
    await open(`/${sport}`);
    await capture(`${sport}-desktop.png`);
    await page.setViewportSize({ width: 390, height: 844 });
    await capture(`${sport}-mobile.png`);
    await page.setViewportSize({ width: 1440, height: 1000 });
  }
  const response = await context.request.get(new URL('/api/dashboard/nfl', baseURL).href);
  const nfl = response.ok() ? await response.json() : null;
  const game = nfl?.slate?.find(item => item.matchId);
  if (game) {
    const params = new URLSearchParams({ sport: 'nfl', match_id: game.matchId });
    if (game.slateDate || nfl.selectedDate) params.set('date', game.slateDate || nfl.selectedDate);
    await open(`/match-analysis?${params}`);
    await capture('nfl-analysis-desktop.png');
    await page.setViewportSize({ width: 390, height: 844 });
    await capture('nfl-analysis-mobile.png');
    await page.setViewportSize({ width: 1440, height: 1000 });
  }
  await open('/insights');
  await capture('insights-slate-desktop.png');
  await page.getByRole('button', { name: 'Historical performance', exact: true }).click();
  await page.getByLabel('Calibration model').selectOption('nonlinear_forest:selected');
  await capture('insights-history-desktop.png');
  await page.setViewportSize({ width: 390, height: 844 });
  await capture('insights-history-mobile.png');
  if (audit.page_errors.length) throw new Error(`Browser errors: ${audit.page_errors.join('; ')}`);
} finally {
  await writeFile(resolve(output, 'capture-audit.json'), `${JSON.stringify(audit, null, 2)}\n`);
  await browser.close();
}
