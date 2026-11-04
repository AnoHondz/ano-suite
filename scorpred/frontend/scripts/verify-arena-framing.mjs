import { chromium, expect } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const output = resolve(process.argv[2] || '../.tmp/arena-framing');
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: 'msedge', headless: true,
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
const report = { mocked: false, animation: 'Generic coaching illustrations; real API matchup labels', renderer: 'Edge software WebGL', errors: [], views: [], motion: [] };
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  const frameDifference = async (first, second) => page.evaluate(async encoded => {
    const pixels = await Promise.all(encoded.map(async value => {
      const bitmap = await createImageBitmap(await (await fetch(`data:image/png;base64,${value}`)).blob());
      const surface = new OffscreenCanvas(bitmap.width, bitmap.height);
      const context = surface.getContext('2d');
      context.drawImage(bitmap, 0, 0);
      const data = context.getImageData(0, 0, bitmap.width, bitmap.height).data;
      bitmap.close();
      return data;
    }));
    let changed = 0;
    for (let i = 0; i < pixels[0].length; i += 4) {
      // Ignore a few quantization levels from software WebGL antialiasing.
      if ([0, 1, 2].some(channel => Math.abs(pixels[0][i + channel] - pixels[1][i + channel]) > 3)) changed++;
    }
    return changed / (pixels[0].length / 4);
  }, [first.toString('base64'), second.toString('base64')]);
  page.on('pageerror', error => report.errors.push(error.message));
  await page.addInitScript(() => localStorage.setItem('scorpred-graphics', '3d'));
  for (const [sport, id, date, title] of [
    ['nfl', '401873017', '2026-10-25', 'NFL'],
    ['nba', '401909095', '2026-10-23', 'NBA'],
    ['soccer', '401879253', '2026-10-23', 'Soccer'],
  ]) {
    await page.setViewportSize({ width: 1440, height: 1100 });
    const start = performance.now();
    await page.goto(`http://127.0.0.1:5173/match-analysis?sport=${sport}&match_id=${id}&date=${date}`);
    const arena = page.getByRole('region', { name: `${title} matchup arena` });
    const sportsArena = arena.locator('.sports-arena');
    await sportsArena.scrollIntoViewIfNeeded();
    await page.waitForTimeout(1500);
    await sportsArena.scrollIntoViewIfNeeded();
    await expect(arena.locator('canvas[data-renderer="webgl"]')).toBeVisible({ timeout: 45_000 });
    const sceneReadyMs = Math.round(performance.now() - start);
    await page.waitForTimeout(1000);
    await arena.getByRole('button', { name: 'Replay', exact: true }).click();
    await sportsArena.scrollIntoViewIfNeeded();
    await page.waitForTimeout(1700);
    await arena.getByRole('button', { name: 'Pause play', exact: true }).click();
    await expect(sportsArena).toHaveAttribute('data-playing', 'false');
    await page.waitForTimeout(1000);
    const movingCanvas = arena.locator('canvas[data-renderer="webgl"]');
    const pausedFirst = await movingCanvas.screenshot();
    await page.waitForTimeout(400);
    const pausedSecond = await movingCanvas.screenshot();
    await writeFile(resolve(output, `${sport}-paused-first.png`), pausedFirst);
    await writeFile(resolve(output, `${sport}-paused-second.png`), pausedSecond);
    const pausedDifference = await frameDifference(pausedFirst, pausedSecond);
    expect(pausedDifference, `${sport} pauses its rendered players and ball`).toBeLessThan(.00001);
    await arena.getByRole('button', { name: 'Resume play', exact: true }).click();
    await sportsArena.scrollIntoViewIfNeeded();
    await page.waitForTimeout(2200);
    await arena.getByRole('button', { name: 'Pause play', exact: true }).click();
    const moved = await movingCanvas.screenshot();
    const movingDifference = await frameDifference(pausedFirst, moved);
    expect(movingDifference, `${sport} actually moves its rendered players and ball`).toBeGreaterThan(.0001);
    report.motion.push({ sport, pauseFrozen: true, resumeChangesFrame: true, replay: true, pausedDifference, movingDifference });
    for (const width of [1440, 390, 320]) {
      await page.setViewportSize({ width, height: width === 1440 ? 1100 : 844 });
      // An actual source refresh can replace the match route while measuring it.
      // Reacquire and scroll the replacement arena rather than a detached canvas.
      for (let attempt = 0; attempt < 3; attempt++) {
        try {
          await sportsArena.scrollIntoViewIfNeeded();
          const canvas = arena.locator('canvas[data-renderer="webgl"]');
          await expect(canvas).toBeVisible({ timeout: 15_000 });
          await page.waitForTimeout(350);
          const dpr = await canvas.evaluate(element => element.width / element.getBoundingClientRect().width);
          const labels = await arena.locator('.floating-matchup').boundingBox();
          const bounds = await sportsArena.boundingBox();
          expect(labels.y + labels.height).toBeLessThanOrEqual(bounds.y + 4);
          await arena.screenshot({ path: resolve(output, `${sport}-${width === 1440 ? 'desktop' : `mobile-${width}`}.png`) });
          await expect(canvas).toBeVisible();
          report.views.push({ sport, width, sceneReadyMs, arena: bounds, dpr,
            overflow: await page.evaluate(() => document.documentElement.scrollWidth > innerWidth) });
          break;
        } catch (error) {
          if (attempt === 2) throw error;
        }
      }
      await arena.getByRole('button', { name: 'Away view', exact: true }).click();
      await expect(sportsArena).toHaveAttribute('data-side', 'away');
      await arena.getByRole('button', { name: 'Home view', exact: true }).click();
    }
    await arena.getByRole('button', { name: 'Use lite view', exact: true }).click();
    await expect(arena.locator('canvas')).toHaveCount(0);
    await expect(arena.locator('.arena-flat')).toBeVisible();
    await arena.getByRole('button', { name: 'Enable 3D', exact: true }).click();
    await expect(arena.locator('canvas[data-renderer="webgl"]')).toBeVisible({ timeout: 45_000 });
    await page.evaluate(() => scrollTo(0, document.body.scrollHeight));
    await expect(sportsArena).toHaveAttribute('data-active', 'false');
    console.log(JSON.stringify({ sport, sceneReadyMs, verified: '1440 / 390 / 320, movement / pause / replay, home / away, lite / 3D, offscreen pause' }));
  }
  const arena = page.getByRole('region', { name: 'Soccer matchup arena' });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await expect(arena.locator('.arena-flat')).toBeVisible();
  await expect(arena.locator('canvas')).toHaveCount(0);
  await arena.screenshot({ path: resolve(output, 'soccer-reduced-motion.png') });
  await expect(arena.getByRole('button', { name: 'Reduced motion' })).toBeDisabled();
  report.reducedMotion = true;
  report.ok = report.errors.length === 0 && report.views.every(view => !view.overflow);
  await writeFile(resolve(output, 'verification.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
  if (!report.ok) process.exitCode = 1;
} finally {
  await browser.close();
}
