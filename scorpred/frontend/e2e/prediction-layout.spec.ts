import { test, expect, type Page } from '@playwright/test';

async function setup(page: Page, sport: string, extra: Record<string, unknown> = {}) {
  await page.clock.install({ time: new Date('2026-10-23T12:00:00Z') });
  await page.addInitScript(() => localStorage.setItem('scorpred-graphics', '3d'));
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/session') return route.fulfill({ json: { user: null } });
    if (path === '/api/matches/layout-check') return route.fulfill({ json: { decision: {
      matchId: 'layout-check', sport, teamA: 'Home team', teamB: 'Away team', matchup: 'Home team vs Away team',
      matchDate: '2026-10-25T17:00:00Z', eventStatus: 'pre', action: 'CONSIDER', side: 'Home team',
      confidence: 79, winProbability: sport === 'nfl' ? null : 61.2, scenarioShare: sport === 'nfl' ? 63.4 : null,
      reason: 'Observed scoring favors the home side. Sample coverage is limited.', data: 'Partial Data', ...extra,
    } } });
    return route.fulfill({ status: 422, json: { error: 'Unavailable in this controlled layout test.' } });
  });
}

for (const sport of ['nba', 'soccer', 'nfl']) test(`${sport} leads with one heading and the actual estimate inside the lite field`, async ({ page }) => {
  await setup(page, sport);
  await page.goto('/match-analysis?sport=' + sport + '&match_id=layout-check');
  const field = page.locator('.prediction-field');
  await expect(page.locator('h1')).toHaveCount(1);
  await expect(page.getByRole('heading', { level: 1 })).toHaveAccessibleName('Home team vs Away team');
  await expect(field.locator('.field-measure strong')).toHaveText(sport === 'nfl' ? '63.4%' : '61.2%');
  await expect(field.locator('.field-measure')).not.toContainText('79');
  await expect(field).toContainText(sport === 'nfl' ? 'Scenario share' : 'Win probability');
  await expect(page.locator('canvas')).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Enable 3D|Replay|Home view|Away view/ })).toHaveCount(0);
  await expect(page.getByText('Score forecast & analysis', { exact: true })).toBeVisible();
  await expect(page.getByLabel('Scoring predictor', { exact: true })).toBeHidden();
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    const box = await field.locator('.field-result').boundingBox();
    const pitch = await field.locator('.prediction-pitch').boundingBox();
    expect(box!.y).toBeGreaterThanOrEqual(pitch!.y);
    expect(box!.y + box!.height).toBeLessThanOrEqual(pitch!.y + pitch!.height + 1);
    expect(box!.y + box!.height).toBeLessThan(710);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
  }
  const resources = await page.evaluate(() => performance.getEntriesByType('resource').map(entry => entry.name));
  expect(resources.some(name => /ArenaScene|TacticalPlayers|@react-three/.test(name))).toBe(false);
});

test('missing or invalid win probability is not relabeled from rule confidence', async ({ page }) => {
  await setup(page, 'nba', { winProbability: 105 });
  await page.goto('/match-analysis?sport=nba&match_id=layout-check');
  const field = page.locator('.prediction-field');
  await expect(field).toContainText('Rule confidence');
  await expect(field).toContainText('Win probability unavailable');
  await expect(field.locator('.field-measure strong')).toHaveText('79.0%');
  await expect(field).not.toContainText('105');
});

test('a soccer draw selection is labeled as draw probability', async ({ page }) => {
  await setup(page, 'soccer', { side: 'Draw', winProbability: 29.2, drawProbability: 29.2 });
  await page.goto('/match-analysis?sport=soccer&match_id=layout-check');
  await expect(page.locator('.field-pick')).toHaveText('Draw');
  await expect(page.locator('.field-measure')).toHaveText('29.2%Draw probability');
});

test('a draw without a probability keeps rule confidence explicitly labeled', async ({ page }) => {
  await setup(page, 'soccer', { side: 'Draw', winProbability: null, drawProbability: null });
  await page.goto('/match-analysis?sport=soccer&match_id=layout-check');
  const field = page.locator('.prediction-field');
  await expect(field.locator('.field-measure')).toHaveText('79.0%Rule confidence');
  await expect(field).toContainText('Draw probability unavailable');
});
