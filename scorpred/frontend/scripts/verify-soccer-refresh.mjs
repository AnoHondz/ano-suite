import { chromium, expect } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const output = resolve('../.tmp/soccer-refresh-repair');
await mkdir(output, { recursive: true });
const base = 'http://127.0.0.1:5173';
const day = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(new Date());
const report = { day, mocked: false, errors: [], leagues: [], views: [] };
const browser = await chromium.launch({ channel: 'msedge', headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.on('pageerror', error => report.errors.push(error.message));
  await page.addInitScript(() => localStorage.setItem('scorpred-graphics', 'lite'));
  let selected;
  for (const league of [39, 78, 140]) {
    const started = performance.now();
    const response = await page.request.get(`${base}/api/dashboard/soccer?league_id=${league}&date=${day}`, { timeout: 30_000 });
    const body = await response.json();
    const available = (body.slate || []).filter(d => d.pregameAvailable && Number.isFinite(d.winProbability) && d.side !== 'Unavailable');
    report.leagues.push({ league, status: response.status(), ms: Math.round(performance.now() - started),
      count: body.slate?.length || 0, forecasts: available.length, refreshPending: body.refresh_pending,
      games: available.map(d => ({ id: d.matchId, matchup: d.matchup, action: d.action, probability: d.winProbability, source: d.dataFreshness })) });
    expect(response.status()).toBe(200);
    expect(available.length).toBeGreaterThan(0);
    await page.goto(`${base}/soccer?league_id=${league}&date=${day}`);
    await expect(page.locator('.decision-card').first()).toBeVisible({ timeout: 45_000 });
    if (league === 39) selected = available.sort((a, b) => Date.parse(b.matchDate) - Date.parse(a.matchDate))[0];
  }
  await page.goto(`${base}/soccer?league_id=39&date=${day}`);
  const card = page.locator('.decision-card').filter({ hasText: selected.teamA }).filter({ hasText: selected.teamB }).first();
  await expect(card).toBeVisible({ timeout: 45_000 });
  await page.screenshot({ path: resolve(output, 'soccer-slate-desktop.png'), fullPage: true });
  await card.getByRole('button', { name: /Analyze Match/ }).click();
  await expect(page.locator('.field-measure strong')).toHaveText(`${selected.winProbability.toFixed(1)}%`, { timeout: 45_000 });
  await expect(page.locator('main h1')).toHaveAccessibleName(`${selected.teamA} vs ${selected.teamB}`);
  const props = page.getByRole('region', { name: 'Player props', exact: true });
  await expect(props).toBeVisible();
  await expect(props.getByText(/Finding the best picks/)).toHaveCount(0, { timeout: 45_000 });
  await page.getByText('Score forecast & analysis', { exact: true }).click();
  await expect(page.getByLabel('Projected score').first()).toBeVisible({ timeout: 45_000 });
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
    expect(overflow).toBe(false);
    report.views.push({ width, match: selected.matchup, probability: await page.locator('.field-measure strong').innerText(),
      propCount: await props.locator('.pick-row').count(), overflow, url: page.url() });
    await page.screenshot({ path: resolve(output, `soccer-analysis-${width}.png`), fullPage: true });
  }
  expect(report.errors).toEqual([]);
  report.ok = true;
} catch (error) {
  report.ok = false;
  report.error = error.message;
  process.exitCode = 1;
} finally {
  await browser.close();
  await writeFile(resolve(output, 'verification.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
}
