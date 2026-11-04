// Real local report/UI verification; never enables predictions or reads outcomes.
import { chromium, expect } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const output = resolve(process.argv[2] || '../.tmp/nba_early_season/browser');
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const report = { mocked: false, errors: [], previewRequests: [] };
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await context.addInitScript(() => localStorage.setItem('scorpred-graphics', 'lite'));
  const page = await context.newPage();
  page.on('pageerror', error => report.errors.push(error.message));
  page.on('request', request => { if (request.url().includes('/research/early-season/matches/')) report.previewRequests.push(request.url()); });
  await page.goto('http://127.0.0.1:5173/match-analysis?sport=nba&match_id=401909094&date=2026-10-22');
  const study = page.getByLabel('NBA early-season research study');
  await expect(study.locator('summary')).toBeVisible({ timeout: 45000 });
  await study.locator('summary').click();
  await expect(study).toContainText('Experimental previews are disabled');
  await expect(study.locator('tbody tr')).toHaveCount(3);
  await expect(page.getByLabel('NBA team comparisons')).toContainText('Observed record');
  await page.screenshot({ path: resolve(output, 'nba-study-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
  await page.screenshot({ path: resolve(output, 'nba-study-mobile.png'), fullPage: true });
  report.text = await study.innerText();
  report.mobileNoOverflow = true;
  report.ok = report.errors.length === 0 && report.previewRequests.length === 0;
} finally {
  await writeFile(resolve(output, 'report.json'), JSON.stringify(report, null, 2));
  await browser.close();
}
if (!report.ok) process.exitCode = 1;
