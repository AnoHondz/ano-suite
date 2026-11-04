// Unmocked local application check. Never evaluates sealed research outcomes.
import { chromium, expect } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const output = resolve(process.argv[2] || '../.tmp/app_ready/browser-after');
const baseURL = process.env.SCORPRED_URL || 'http://127.0.0.1:5173';
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true });
const report = { baseURL, observedAt: new Date().toISOString(), mocked: false, journeys: [] };
try {
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
    const context = await browser.newContext({ viewport });
    const page = await context.newPage();
    const journey = { viewport, steps: [], requests: [], errors: [] };
    page.on('pageerror', error => journey.errors.push(error.message));
    page.on('response', async response => {
      if (!response.url().includes('/api/')) return;
      const request = response.request();
      const timing = request.timing();
      journey.requests.push({ path: new URL(response.url()).pathname + new URL(response.url()).search,
        status: response.status(), ms: Math.round(timing.responseStart) });
    });
    async function step(name, action, verify) {
      const start = performance.now();
      const record = { name };
      try {
        await action();
        if (verify) await verify();
        await expect(page.locator('.skeleton-line')).toHaveCount(0, { timeout: 55000 });
        await expect(page.getByText('Loading opportunities…', { exact: true })).toHaveCount(0, { timeout: 55000 });
        record.ok = true;
      } catch (error) { record.ok = false; record.error = error.message; }
      record.ms = Math.round(performance.now() - start);
      record.url = page.url();
      record.alerts = await page.getByRole('alert').allTextContents();
      record.overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
      record.cards = await page.locator('.decision-card').count();
      journey.steps.push(record);
      await page.screenshot({ path: resolve(output, `${viewport.width}-${name}.png`), fullPage: true });
      console.log(JSON.stringify({ viewport: viewport.width, ...record }));
    }
    const nav = () => page.locator(viewport.width < 768 ? '.mobile-tab-bar' : '.sidebar-nav');
    const dashboard = () => nav().getByRole('button', { name: /^(Dashboard|Home)$/ }).click();
    await step('dashboard', () => page.goto(baseURL), () => expect(page.getByLabel('NFL shortlist')).toBeVisible());
    for (const sport of ['NFL', 'NBA', 'Soccer']) {
      await step(sport.toLowerCase(), () => nav().getByRole('button', { name: sport, exact: true }).click(),
        () => expect(page.locator('.decision-card').first()).toBeVisible({ timeout: 55000 }));
      await step(`${sport.toLowerCase()}-analysis`, () => page.getByRole('button', { name: /Analyze Match/ }).first().click(), async () => {
        await expect(page.getByLabel('Match prediction', { exact: true })).toBeVisible({ timeout: 55000 });
        await page.getByText('Score forecast & analysis', { exact: true }).click();
        await expect(page.getByRole('status').filter({ hasText: /Loading recent scoring/ })).toHaveCount(0, { timeout: 55000 });
        await expect(page.getByRole('status').filter({ hasText: /Loading scoring/ })).toHaveCount(0, { timeout: 55000 });
        await expect(page.getByText('Computing scoreline distribution…', { exact: true })).toHaveCount(0, { timeout: 55000 });
      });
      if (sport === 'NFL') await step('dashboard-return', dashboard);
    }
    await step('insights', () => nav().getByRole('button', { name: 'Insights', exact: true }).click(),
      () => expect(page.getByRole('heading', { name: 'Insights & model performance.' })).toBeVisible());
    await step('refresh-control', () => page.getByRole('button', { name: 'Refresh data', exact: true }).click(),
      () => expect(page.getByRole('button', { name: 'Refresh data', exact: true })).toBeEnabled({ timeout: 55000 }));
    await step('refresh', () => page.reload(), () => expect(page.locator('.topbar-title')).toHaveText('Insights'));
    report.journeys.push(journey);
    await context.close();
  }
} finally {
  await browser.close();
  report.ok = report.journeys.length === 2 && report.journeys.every(journey =>
    journey.errors.length === 0 && journey.steps.every(step => step.ok && !step.overflow && step.alerts.length === 0));
  await writeFile(resolve(output, 'report.json'), JSON.stringify(report, null, 2));
}
if (!report.ok) process.exitCode = 1;
