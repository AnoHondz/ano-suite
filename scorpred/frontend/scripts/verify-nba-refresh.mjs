// Real schedule → analysis → source-expiry refresh, without altering cache files.
import { chromium, expect } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const output = resolve(process.argv[2] || '../.tmp/nba_refresh/browser');
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const report = { mocked: false, errors: [], requests: [] };
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await context.addInitScript(() => localStorage.setItem('scorpred-graphics', 'lite'));
  const page = await context.newPage();
  page.on('pageerror', error => report.errors.push(error.message));
  page.on('response', response => {
    if (response.url().includes('/api/')) report.requests.push({ path: new URL(response.url()).pathname, status: response.status(), ms: Math.round(response.request().timing().responseStart) });
  });
  const response = await context.request.get('http://127.0.0.1:5173/api/dashboard/nba', { timeout: 60000 });
  expect(response.status()).toBe(200);
  let schedule = await response.json();
  const future = data => data.slate.find(game => Date.parse(game.matchDate) > Date.now() + 120000);
  let selected = future(schedule);
  if (!selected) {
    for (const day of schedule.availableDates.slice(1, 3)) {
      schedule = await (await context.request.get(`http://127.0.0.1:5173/api/dashboard/nba?date=${day}`, { timeout: 60000 })).json();
      selected = future(schedule);
      if (selected) break;
    }
  }
  expect(selected, 'A genuinely future scheduled NBA fixture must be available').toBeTruthy();
  report.fixture = { id: selected.matchId, matchup: selected.matchup, kickoff: selected.matchDate };
  console.log(JSON.stringify({ stage: 'selected', ...report.fixture }));
  await page.goto(`http://127.0.0.1:5173/nba?date=${schedule.selectedDate}`);
  const card = page.locator('.decision-card').filter({ hasText: selected.teamA }).filter({ hasText: selected.teamB }).first();
  await expect(card).toBeVisible({ timeout: 60000 });
  await card.getByRole('button', { name: /Analyze Matchup/ }).click();
  await expect(page).toHaveURL(new RegExp(`match_id=${selected.matchId}`));
  const comparisons = page.getByLabel('NBA team comparisons');
  await expect(comparisons.locator('.context-metrics')).toHaveCount(2, { timeout: 45000 });
  await comparisons.getByRole('button', { name: 'Previous season', exact: true }).click();
  await expect(comparisons).toContainText('Existing local archive');
  report.comparisons = await comparisons.locator('.context-metrics').allTextContents();
  const matchURL = `http://127.0.0.1:5173/api/matches/${selected.matchId}?sport=nba&date=${schedule.selectedDate}`;
  const first = (await (await context.request.get(matchURL)).json()).decision;
  report.before = { source: first.dataFreshness, reason: first.reason, winProbability: first.winProbability, modelSource: first.modelSource };
  const expires = Date.parse(first.dataFreshness.expires_at);
  expect(Number.isFinite(expires)).toBe(true);
  const refreshedResponse = page.waitForResponse(response => response.url().includes(`/api/matches/${selected.matchId}?`) && response.status() === 200, { timeout: 75000 });
  // Let the real one-minute provider receipt expire; leave all cache files intact.
  while (Date.now() <= expires + 1200) await page.waitForTimeout(Math.min(30000, expires + 1201 - Date.now()));
  const fresh = (await (await refreshedResponse).json()).decision;
  report.after = { source: fresh.dataFreshness, reason: fresh.reason, winProbability: fresh.winProbability, modelSource: fresh.modelSource };
  // Composite provenance retains the oldest input receipt; a fresh scoreboard
  // can advance the limiting expiry while an hourly team receipt stays unchanged.
  expect(Date.parse(fresh.dataFreshness.observed_at)).toBeGreaterThanOrEqual(Date.parse(first.dataFreshness.observed_at));
  expect(Date.parse(fresh.dataFreshness.expires_at)).toBeGreaterThan(expires);
  expect(Date.parse(fresh.dataFreshness.expires_at)).toBeGreaterThan(Date.now());
  expect(fresh.dataFreshness.state).toBe('fresh');
  await expect(comparisons.locator('.context-metrics')).toHaveCount(2, { timeout: 45000 });
  await page.screenshot({ path: resolve(output, 'nba-analysis-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
  await page.screenshot({ path: resolve(output, 'nba-analysis-mobile.png'), fullPage: true });
  report.mobileNoOverflow = true;
  report.automaticRefresh = true;
  report.ok = report.errors.length === 0;
  console.log(JSON.stringify({ stage: 'fresh_after_expiry', source: fresh.dataFreshness, ok: report.ok }));
} finally {
  await writeFile(resolve(output, 'report.json'), JSON.stringify(report, null, 2));
  await browser.close();
}
if (!report.ok) process.exitCode = 1;
