// Actual local APIs and software WebGL in Edge; no example sports values.
import { chromium, expect } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const output = resolve(process.argv[2] || '../.tmp/nba_3d/immersive');
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: 'msedge', headless: true, args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
const report = { mocked: false, browser: 'Edge with software WebGL', pages: [], errors: [], performance: {}, fallback: {} };
try {
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  const page = await context.newPage();
  page.on('pageerror', error => report.errors.push(error.message));
  for (const [name, path] of [['dashboard', '/'], ['nfl', '/nfl'], ['nba', '/nba'], ['soccer', '/soccer'],
    ['match-analysis', '/match-analysis?sport=nba&match_id=401909093&date=2026-10-22'], ['insights', '/insights']]) {
    const start = performance.now();
    await page.goto(`http://127.0.0.1:5173${path}`);
    await expect(page.locator('.topbar-title')).toBeVisible();
    const shellMs = Math.round(performance.now() - start);
    await expect(page.locator('.matchup-stage')).toBeVisible();
    await expect(page.locator('.skeleton-line')).toHaveCount(0, { timeout: 55000 });
    const contentMs = Math.round(performance.now() - start);
    await expect(page.locator('canvas[data-renderer=webgl]')).toHaveCount(1, { timeout: 45000 });
    const sceneMs = Math.round(performance.now() - start);
    await page.getByRole('button', { name: 'Away view', exact: true }).click();
    await expect(page.locator('.sports-arena')).toHaveAttribute('data-side', 'away');
    await page.getByRole('button', { name: 'Home view', exact: true }).click();
    const canvas = page.locator('canvas');
    const bounds = await canvas.boundingBox();
    await page.mouse.move(bounds.x + bounds.width * .7, bounds.y + bounds.height * .4);
    await page.waitForTimeout(350);
    const dimensions = await canvas.evaluate(element => ({ width: element.width, cssWidth: element.getBoundingClientRect().width, dpr: element.width / element.getBoundingClientRect().width }));
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
    const alerts = await page.getByRole('alert').allTextContents();
    report.pages.push({ name, path, shellMs, contentMs, sceneMs, dimensions, overflow, alerts, webglCanvases: await canvas.count() });
    await page.screenshot({ path: resolve(output, `${name}-viewport.png`), fullPage: false });
    await page.screenshot({ path: resolve(output, `${name}-desktop.png`), fullPage: true });
    console.log(JSON.stringify(report.pages.at(-1)));
  }
  report.performance.animation = await page.evaluate(() => new Promise(resolve => {
    const samples = []; let previous = performance.now(); const start = previous;
    function tick(now) { samples.push(now - previous); previous = now;
      if (now - start < 2000) requestAnimationFrame(tick);
      else { const sorted = samples.toSorted((a, b) => a - b); resolve({ frames: samples.length, durationMs: now - start, meanFrameMs: samples.reduce((a, b) => a + b, 0) / samples.length, p95FrameMs: sorted[Math.floor(sorted.length * .95)] }); }
    } requestAnimationFrame(tick);
  }));
  await page.locator('.workspace-footer').scrollIntoViewIfNeeded();
  await expect(page.locator('.sports-arena')).toHaveAttribute('data-active', 'false');
  report.pausesOffscreen = true;
  await context.close();

  const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
  const phone = await mobile.newPage();
  phone.on('pageerror', error => report.errors.push(error.message));
  for (const path of ['/', '/nba', '/soccer', '/nfl']) {
    await phone.goto(`http://127.0.0.1:5173${path}`);
    await expect(phone.locator('canvas[data-renderer=webgl]')).toHaveCount(1, { timeout: 45000 });
    expect(await phone.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
    await phone.screenshot({ path: resolve(output, `${path.replace('/', '') || 'dashboard'}-mobile.png`), fullPage: true });
  }
  report.mobile = { width: 390, noOverflow: true, maxCanvases: await phone.locator('canvas').count(), dpr: await phone.locator('canvas').evaluate(e => e.width / e.getBoundingClientRect().width) };
  await mobile.close();

  const reduced = await browser.newContext({ viewport: { width: 390, height: 844 }, reducedMotion: 'reduce' });
  const quiet = await reduced.newPage();
  await quiet.goto('http://127.0.0.1:5173/nba');
  await expect(quiet.getByRole('button', { name: 'Reduced motion', exact: true })).toBeVisible();
  await expect(quiet.locator('canvas')).toHaveCount(0);
  report.fallback.reducedMotion = true;
  await quiet.screenshot({ path: resolve(output, 'nba-reduced-motion.png'), fullPage: true });
  await reduced.close();

  const unavailable = await browser.newContext();
  await unavailable.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function(type, ...args) {
      if (String(type).includes('webgl')) return null;
      return original.call(this, type, ...args);
    };
  });
  const fallback = await unavailable.newPage();
  await fallback.goto('http://127.0.0.1:5173/nfl');
  await expect(fallback.getByRole('button', { name: 'Lite view · WebGL unavailable', exact: true })).toBeVisible({ timeout: 45000 });
  await expect(fallback.locator('.arena-flat')).toBeVisible();
  await expect(fallback.getByRole('heading', { name: 'NFL Game Plan' })).toBeVisible();
  report.fallback.webglUnavailable = true;
  await unavailable.close();
  report.ok = report.errors.length === 0 && report.pages.every(page => !page.overflow && page.webglCanvases === 1 && page.alerts.length === 0);
} finally {
  await writeFile(resolve(output, 'report.json'), JSON.stringify(report, null, 2));
  await browser.close();
}
if (!report.ok) process.exitCode = 1;
