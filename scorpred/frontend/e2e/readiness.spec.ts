import { test, expect, type Page } from '@playwright/test';

const card = { sport: 'nba', matchId: 'ready-game', teamA: 'Home test team', teamB: 'Away test team',
  matchup: 'Home test team vs Away test team', homeId: 1, awayId: 2, side: 'Unavailable', action: 'SKIP',
  confidence: 0, data: 'Limited Data', reason: 'Not enough completed current-season games.',
  matchDate: '2026-10-26T23:00:00Z', eventStatus: 'pre', dataFreshness: {
    state: 'fresh', observed_at: '2026-10-22T12:00:00Z', expires_at: '2026-10-22T12:10:00Z', max_stale_at: '2026-10-22T12:20:00Z' } };
const dates = ['2026-10-22', '2026-10-23', '2026-10-24', '2026-10-25', '2026-10-26'];
const payload = { slate: [card], topOpportunities: [], availableDates: dates, selectedDate: dates[4],
  radarCards: [], radarGroups: {}, volatilityRows: [] };

async function setup(page: Page) {
  await page.clock.install({ time: new Date('2026-10-22T12:00:00Z') });
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/session') return route.fulfill({ json: { user: null } });
    if (path === '/api/leagues') return route.fulfill({ json: { leagues: [{ id: 39, name: 'Premier League' }] } });
    if (path.startsWith('/api/dashboard/')) return route.fulfill({ json: payload });
    if (path === '/api/matches/ready-game') return route.fulfill({ json: { decision: card } });
    return route.fulfill({ status: 422, json: { message: 'At least three completed games are needed for scoring.' } });
  });
}

test('five primary destinations, deduplicated requests, cache reuse, and explicit refresh', async ({ page }) => {
  await setup(page);
  const requests: string[] = [];
  page.on('request', request => { if (request.url().includes('/api/dashboard/nba')) requests.push(request.url()); });
  await page.goto('/');
  await expect(page.getByLabel('NBA shortlist').locator('.decision-card')).toHaveCount(1);
  await expect(page.getByLabel('Main navigation').getByRole('button')).toHaveCount(5);
  expect(requests).toHaveLength(1);
  await page.getByLabel('Main navigation').getByRole('button', { name: 'NBA', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'NBA Game Plan' })).toBeVisible();
  expect(requests).toHaveLength(1);
  await page.getByLabel('Main navigation').getByRole('button', { name: 'Dashboard', exact: true }).click();
  await expect(page.getByLabel('NBA shortlist').locator('.decision-card')).toHaveCount(1);
  expect(requests).toHaveLength(1);
  await page.getByRole('button', { name: 'Refresh data', exact: true }).click();
  await expect.poll(() => requests.length).toBe(2);
  await expect(page.getByRole('button', { name: 'Refresh data', exact: true })).toBeEnabled();
});

test('client cache expires after fifteen seconds across navigation', async ({ page }) => {
  await setup(page);
  let requests = 0;
  page.on('request', request => { if (request.url().includes('/api/dashboard/nba')) requests++; });
  await page.goto('/nba');
  await expect(page.locator('.decision-card')).toHaveCount(1);
  await page.getByLabel('Main navigation').getByRole('button', { name: 'NFL', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'NFL Game Plan' })).toBeVisible();
  await page.clock.fastForward(16000);
  await page.getByLabel('Main navigation').getByRole('button', { name: 'NBA', exact: true }).click();
  await expect.poll(() => requests).toBe(2);
});

test('later schedule dates remain reachable and selected after reload', async ({ page }) => {
  await setup(page);
  await page.goto('/nba?date=2026-10-26');
  await expect(page.locator('.league-scroll button')).toHaveCount(5);
  await expect(page.locator('.league-scroll [aria-pressed=true]')).toContainText('Oct 26');
  await page.reload();
  await expect(page.locator('.league-scroll [aria-pressed=true]')).toContainText('Oct 26');
});

test('unsupported scoring is an availability notice and does not invent a forecast', async ({ page }) => {
  await setup(page);
  await page.goto('/match-analysis?sport=nba&match_id=ready-game');
  await expect(page.getByLabel('Match prediction', { exact: true })).toBeVisible();
  await page.getByText('Score forecast & analysis', { exact: true }).click();
  await expect(page.getByLabel('Scoring predictor', { exact: true }).getByRole('status').filter({ hasText: 'At least three completed games' })).toBeVisible();
  await expect(page.getByRole('alert')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Try again' })).toHaveCount(0);
  await expect(page.getByLabel('Match prediction', { exact: true })).toContainText('Prediction unavailable');
  await expect(page.locator('.field-measure')).toHaveCount(0);
});

test('provider warnings stay visible and failures preserve navigation and date controls', async ({ page }) => {
  await setup(page);
  await page.route('**/api/dashboard/nba*', route => route.fulfill({ json: { ...payload, warnings: ['Showing a delayed provider schedule.'] } }));
  await page.goto('/nba');
  await expect(page.getByLabel('Data availability')).toContainText('Showing a delayed provider schedule.');
  await page.route('**/api/dashboard/nba*', route => route.fulfill({ status: 503, json: { error: 'Provider unavailable.' } }));
  await page.getByRole('button', { name: 'Refresh data', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Provider unavailable.');
  await expect(page.getByRole('heading', { name: 'NBA Game Plan' })).toBeVisible();
  await expect(page.getByLabel('Main navigation')).toBeVisible();
  await expect(page.getByText('No games found', { exact: true })).toHaveCount(0);
});

test('invalid match identifiers show an actionable error without loading scoring', async ({ page }) => {
  await setup(page);
  await page.route('**/api/matches/unknown*', route => route.fulfill({ status: 404, json: { error: 'Choose a match from the current slate.' } }));
  await page.goto('/match-analysis?sport=nba&match_id=unknown');
  await expect(page.getByRole('alert')).toContainText('Choose a match from the current slate.');
  await page.getByLabel('Main navigation').getByRole('button', { name: 'Dashboard', exact: true }).click();
  await expect(page.getByLabel('NBA shortlist')).toBeVisible();
});

test('an open NBA matchup renews its expired source and retains comparisons without inventing a prediction', async ({ page }) => {
  await setup(page);
  let requests = 0;
  await page.route('**/api/matches/ready-game*', route => {
    requests++;
    return route.fulfill({ json: { decision: { ...card, dataFreshness: { ...card.dataFreshness,
      expires_at: requests === 1 ? '2026-10-22T12:00:02Z' : '2026-10-22T12:10:00Z' } } } });
  });
  await page.goto('/match-analysis?sport=nba&match_id=ready-game');
  await expect(page.getByLabel('Match prediction', { exact: true })).toBeVisible();
  expect(requests).toBe(1);
  await page.clock.fastForward(2300);
  await expect.poll(() => requests).toBe(2);
  await expect(page.getByLabel('Match prediction', { exact: true })).toBeVisible();
  await expect(page.locator('.field-pick')).toContainText('Prediction unavailable');
  await page.clock.fastForward(1000);
  expect(requests).toBe(2);
});
