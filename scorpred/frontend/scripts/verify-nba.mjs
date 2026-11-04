import { chromium, expect } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const output = resolve(process.argv[2] || '../.tmp/nba_3d/nba-verification');
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await context.newPage();
const report = { api: [], pages: [], errors: [] };
page.on('pageerror', error => report.errors.push(error.message));
try {
  await page.goto('http://127.0.0.1:5173/nba');
  const overview = page.getByLabel('NBA team comparisons');
  await expect(overview.locator('.context-metrics')).toHaveCount(2);
  await expect(overview).toContainText('Existing local archive');
  await expect(overview).toContainText('82');
  report.overviewComparisons = true;
  await page.screenshot({ path: resolve(output, 'nba-overview.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(overview.locator('.context-metrics').first()).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
  report.mobileComparisons = true;
  await page.screenshot({ path: resolve(output, 'nba-overview-mobile.png'), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  for (const [name, path] of [
    ['76ers-cavaliers-early-season', '/match-analysis?sport=nba&match_id=401909093&date=2026-10-22'],
    ['thunder-nuggets', '/match-analysis?sport=nba&match_id=401909094&date=2026-10-22'],
    ['completed-game', '/match-analysis?sport=nba&match_id=401909834&date=2026-10-21'],
    ['historical-game', '/match-analysis?sport=nba&match_id=401859967&date=2026-06-14'],
  ]) {
    const start = performance.now();
    await page.goto(`http://127.0.0.1:5173${path}`);
    const panel = page.getByLabel('NBA team comparisons');
    await expect(panel.getByRole('heading', { name: 'The evidence beyond a prediction.' })).toBeVisible({ timeout: 55000 });
    await expect(panel.locator('.context-metrics')).toHaveCount(2);
    await expect(panel).toContainText('Existing local archive');
    await expect(panel).toContainText('Descriptive');
    await expect(page.getByRole('alert')).toHaveCount(0);
    report.pages.push({ name, path, ms: Math.round(performance.now() - start), comparisonText: await panel.innerText() });
    await page.screenshot({ path: resolve(output, `${name}.png`), fullPage: true });
    console.log(JSON.stringify({ name, ms: report.pages.at(-1).ms }));
  }
  await page.goto('http://127.0.0.1:5173/nba');
  await page.getByRole('button', { name: 'Local history', exact: true }).click();
  await expect(page.locator('.decision-card').first()).toBeVisible();
  await page.getByRole('button', { name: /Analyze Matchup/ }).first().click();
  await expect(page.getByLabel('NBA team comparisons').getByRole('heading', { name: 'The evidence beyond a prediction.' })).toBeVisible();
  report.historyNavigation = true;
  await page.route('**/api/nba/matches/*/context*', route => route.fulfill({ status: 503, json: { error: 'Controlled provider outage for browser verification.' } }));
  await page.goto('http://127.0.0.1:5173/match-analysis?sport=nba&match_id=401909093&date=2026-10-22');
  await expect(page.getByLabel('NBA team comparisons')).toContainText('Controlled provider outage');
  await expect(page.getByRole('heading', { name: 'Game outlook', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Retry comparisons', exact: true })).toBeVisible();
  report.controlledOutage = true;
  report.ok = report.errors.length === 0 && report.pages.length === 4;
} finally {
  await writeFile(resolve(output, 'report.json'), JSON.stringify(report, null, 2));
  await browser.close();
}
