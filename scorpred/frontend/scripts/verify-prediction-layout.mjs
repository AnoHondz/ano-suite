import { chromium, expect } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const output = resolve(process.argv[2] || '../.tmp/prediction-layout');
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const report = { mocked: false, errors: [], views: [] };
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.on('pageerror', error => report.errors.push(error.message));
  // A previously saved graphics preference must not restore the oversized scene.
  await page.addInitScript(() => localStorage.setItem('scorpred-graphics', '3d'));
  for (const [sport, id, date] of [
    ['nba', '401909095', '2026-10-23'], ['soccer', '401879253', '2026-10-23'], ['nfl', '401873017', '2026-10-25'],
  ]) {
    const started = performance.now();
    await page.goto(`http://127.0.0.1:5173/match-analysis?sport=${sport}&match_id=${id}&date=${date}`);
    const field = page.locator('.prediction-field');
    await expect(field).toBeVisible({ timeout: 55_000 });
    await expect(page.locator('main h1')).toHaveCount(1);
    await expect(page.locator('.analysis-masthead')).toHaveCount(0);
    await expect(page.locator('canvas')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Enable 3D' })).toHaveCount(0);
    await expect(page.getByRole('heading', { name: /Why (this prediction|unavailable)/ })).toBeVisible();
    const props = page.getByRole('region', { name: 'Player props', exact: true });
    await expect(props).toBeVisible();
    await expect(props.getByText(/Finding the best picks/)).toHaveCount(0, { timeout: 45_000 });
    await expect(props.getByRole('spinbutton')).toHaveCount(0);
    const propCount = await props.locator('.pick-row').count();
    if (propCount) {
      await expect(props.locator('.pick-estimate-label').first()).toHaveText('Model estimate');
      await props.screenshot({ path: resolve(output, `${sport}-props.png`) });
      await page.evaluate(() => scrollTo(0, 0));
    }
    if (sport === 'nba') await expect(page.locator('.compact-comparison')).toBeVisible({ timeout: 45_000 });
    for (const width of [1440, 390, 320]) {
      await page.setViewportSize({ width, height: width === 1440 ? 900 : 844 });
      await page.evaluate(() => scrollTo(0, 0));
      const bounds = await page.getByLabel('Match prediction', { exact: true }).boundingBox();
      const pitch = await page.locator('.prediction-pitch').boundingBox();
      expect(bounds.y).toBeGreaterThanOrEqual(pitch.y);
      expect(bounds.y + bounds.height).toBeLessThanOrEqual(pitch.y + pitch.height + 1);
      expect(bounds.y + bounds.height).toBeLessThan(width === 1440 ? 650 : 710);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
      expect(overflow).toBe(false);
      await page.screenshot({ path: resolve(output, `${sport}-${width}.png`), fullPage: false });
      report.views.push({ sport, width, prediction: await page.locator('.field-result').innerText(), headingCount: await page.locator('h1').count(), overflow, bounds,
        propCount, manualInputs: await props.getByRole('spinbutton').count(), sportsbookStatus: await props.locator('.prop-market-status').allTextContents() });
    }
    const resources = await page.evaluate(() => performance.getEntriesByType('resource').map(entry => entry.name));
    expect(resources.some(name => /ArenaScene|TacticalPlayers|@react-three|three\.module/.test(name))).toBe(false);
    await page.getByText('Score forecast & analysis', { exact: true }).click();
    await expect(page.getByLabel('Scoring predictor', { exact: true })).toBeVisible();
    console.log(JSON.stringify({ sport, readyMs: Math.round(performance.now() - started), verified: 'single heading, prediction in field, supporting information below, 1440 / 390 / 320, lite only' }));
  }
  report.ok = report.errors.length === 0;
  await writeFile(resolve(output, 'verification.json'), JSON.stringify(report, null, 2));
  expect(report.errors).toEqual([]);
} finally {
  await browser.close();
}
