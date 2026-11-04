import { test, expect, type Page } from '@playwright/test';

const decision = { matchId: '42', matchup: 'Arsenal vs Chelsea', teamA: 'Arsenal', teamB: 'Chelsea',
  side: 'Arsenal', confidence: 68, reason: 'Recent form favors Arsenal.', action: 'CONSIDER',
  data: 'Strong Data', sport: 'soccer', homeId: 42, awayId: 49, leagueId: 39, matchDate: '2026-10-17T16:30:00Z' };
const sportData = { slate: [decision], topOpportunities: [decision], plan: { bet: 0, consider: 1, skip: 0 }, availableDates: ['2026-10-17'], selectedDate: '2026-10-17' };

async function mockApi(page: Page) {
  await page.clock.install({ time: new Date('2026-10-16T12:00:00Z') });
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/session') return route.fulfill({ json: { user: null } });
    if (path === '/api/nfl/research/winner/models') return route.fulfill({ status: 404, json: { error: 'Research disabled.' } });
    if (/^\/api\/nfl\/matches\/[^/]+\/props$/.test(path)) return route.fulfill({ status: 422, json: { error: 'Player props unavailable for this controlled navigation fixture.' } });
    if (path === '/api/leagues') return route.fulfill({ json: { leagues: [{ id: 39, name: 'Premier League', flag: '', country: 'England' }] } });
    if (path === '/api/dashboard/soccer') return route.fulfill({ json: sportData });
    if (path === '/api/dashboard/nba') return route.fulfill({ json: { ...sportData, slate: [], topOpportunities: [] } });
    if (path === '/api/dashboard/nfl') return route.fulfill({ json: { ...sportData, slate: [], topOpportunities: [], plan: { bet: 0, consider: 0, skip: 0 } } });
    if (path === '/api/matches/42') return route.fulfill({ json: { decision } });
    return route.fulfill({ status: 503, json: { error: 'Data is temporarily unavailable.' } });
  });
}

test('navigation, filters, refresh, match links, and browser back preserve the URL', async ({ page }) => {
  await mockApi(page);
  await page.goto('/');
  await page.locator('.sidebar-nav').getByRole('button', { name: 'Soccer', exact: true }).click();
  await expect(page).toHaveURL(/\/soccer$/);
  await page.reload();
  await expect(page.locator('.topbar-title')).toHaveText('Soccer');
  await page.getByRole('button', { name: /Analyze Match/ }).first().click();
  await expect(page).toHaveURL(/\/match-analysis\?.*match_id=42/);
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Arsenal vs Chelsea', exact: true })).toBeVisible();
  await page.getByText('Score forecast & analysis', { exact: true }).click();
  await expect(page.getByLabel('Scoring predictor').getByRole('alert')).toContainText('Data is temporarily unavailable.');
  await page.goBack();
  await expect(page.locator('.topbar-title')).toHaveText('Soccer');
});

test('NFL has reloadable navigation and event-bound scoring analysis', async ({ page }) => {
  await mockApi(page);
  const nflDecision = { ...decision, matchId: '123', sport: 'nfl', teamA: 'Kansas City Chiefs', teamB: 'Buffalo Bills',
    matchup: 'Kansas City Chiefs vs Buffalo Bills', side: 'Kansas City Chiefs', data: 'Limited Data',
    modelSource: 'nfl_recent_scoring_v1', matchDate: '2026-10-19T00:20:00Z', slateDate: '2026-10-18', reason: 'Experimental recent-scoring scenarios.' };
  await page.route('**/api/dashboard/nfl*', route => route.fulfill({ json: {
    ...sportData, slate: [nflDecision], topOpportunities: [nflDecision], availableDates: ['2026-10-18', '2026-10-19'], selectedDate: '2026-10-18', notice: 'Experimental recent-scoring scenarios.' } }));
  await page.route('**/api/matches/123?*', route => route.fulfill({ json: { decision: nflDecision } }));
  await page.route('**/api/nfl/matches/123/analysis?*', route => route.fulfill({ json: { result: {
    projected_score: { home: 24.5, away: 20.5 }, probabilities: { home: 60, draw: 4, away: 36 },
    samples: { home: 5, away: 5 }, notice: 'Scenario shares are descriptive estimates, not calibrated probabilities.' } } }));
  await page.goto('/');
  await page.locator('.sidebar-nav').getByRole('button', { name: 'NFL', exact: true }).click();
  await expect(page).toHaveURL(/\/nfl$/);
  await expect(page.getByRole('heading', { name: 'NFL Game Plan' })).toBeVisible();
  await page.getByRole('button', { name: /Analyze Match/ }).click();
  await expect(page).toHaveURL(/sport=nfl&match_id=123/);
  await expect(page).toHaveURL(/date=2026-10-18/);
  await page.reload();
  await page.getByText('Score forecast & analysis', { exact: true }).click();
  await expect(page.getByText('Projected points:', { exact: false })).toContainText('24.5');
  await expect(page.getByText('Scenario shares are descriptive estimates, not calibrated probabilities.')).toBeVisible();
  await expect(page.getByLabel('Scoring predictor').getByRole('alert')).toHaveCount(0);
  await page.goBack();
  await expect(page).toHaveURL(/\/nfl$/);
});

test('timeout clears loading and exposes a retry action', async ({ page }) => {
  await mockApi(page);
  await page.route('**/api/dashboard/soccer', () => {});
  await page.goto('/');
  await expect(page.locator('.skeleton-line').first()).toBeVisible();
  await page.clock.fastForward(46_000);
  await expect(page.getByRole('alert')).toContainText('timed out');
  await expect(page.locator('.skeleton-line')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
});

test('provider failures are explicit and retry can recover', async ({ page }) => {
  await mockApi(page);
  let failed = true;
  await page.route('**/api/dashboard/soccer', route => failed
    ? route.fulfill({ status: 503, json: { error: 'Soccer provider unavailable.' } })
    : route.fulfill({ json: sportData }));
  await page.goto('/');
  await expect(page.getByRole('alert')).toContainText('Soccer provider unavailable.');
  await expect(page.getByText('Slate still forming', { exact: true })).toHaveCount(0);
  failed = false;
  await page.getByRole('button', { name: 'Try again' }).click();
  await expect(page.getByRole('button', { name: /Analyze Match/ })).toBeVisible();
  await expect(page.getByRole('alert')).toHaveCount(0);
});

test('desktop hides mobile tabs and mobile fits the viewport', async ({ page }) => {
  await mockApi(page);
  await page.goto('/');
  await expect(page.locator('.mobile-tab-bar')).toBeHidden();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator('.sidebar')).toBeHidden();
  await expect(page.locator('.mobile-tab-bar')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
});

test('matchday redesign displays data coverage and fits narrow screens', async ({ page }, testInfo) => {
  await mockApi(page);
  const nflDecision = { ...decision, sport: 'nfl', matchId: 'nfl-1', teamA: 'Kansas City Chiefs', teamB: 'Buffalo Bills',
    matchup: 'Kansas City Chiefs vs Buffalo Bills', side: 'Kansas City Chiefs', data: 'Limited Data',
    reason: 'Experimental recent-scoring scenarios. Review availability and weather before relying on this estimate.' };
  await page.route('**/api/dashboard/nfl*', route => route.fulfill({ json: { ...sportData, slate: [nflDecision], topOpportunities: [nflDecision],
    dataSources: { form: 'available', odds: 'not_configured', injuries: 'not_connected', weather: 'not_connected' } } }));
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Today’s slate.' })).toBeVisible();
  await expect(page.getByLabel('Slate overview')).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('home-desktop.png'), fullPage: true });
  await page.getByRole('button', { name: 'Explore NFL' }).click();
  await page.getByText('Data sources', { exact: true }).click();
  await expect(page.getByLabel('NFL data coverage')).toContainText('Not connected');
  await expect(page.getByRole('link', { name: 'The Odds API · prices' })).toHaveAttribute('href', 'https://the-odds-api.com/liveapi/guides/v4/');
  await page.screenshot({ path: testInfo.outputPath('nfl-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
  await page.screenshot({ path: testInfo.outputPath('nfl-mobile.png'), fullPage: true });
  await page.setViewportSize({ width: 320, height: 720 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(320);
});

test('free NFL coverage shows snapshot limits and expires on an open page', async ({ page }) => {
  await mockApi(page);
  await page.route('**/api/dashboard/nfl*', route => route.fulfill({ json: {
    ...sportData, slate: [], topOpportunities: [],
    dataSources: { form: 'available', provider: 'nflverse', odds: 'not_configured', injuries: 'not_connected', weather: 'not_connected',
      researchDatasets: [
        { name: 'ngs_passing', status: 'available', rows: 200, season: 2026, latestWeek: 6,
          observedAt: '2026-10-16T11:00:00Z', expiresAt: '2026-10-16T12:00:10Z' },
        { name: 'injuries', status: 'not_synced', rows: 0, season: 2026 },
      ] },
  } }));
  await page.goto('/nfl');
  await page.getByText('Data sources', { exact: true }).click();
  const coverage = page.getByLabel('Free NFL research datasets');
  await expect(coverage).toContainText('200 rows · 2026 · through week 6');
  await expect(coverage).toContainText('Imported snapshot');
  await expect(coverage).toContainText('Injury research snapshots');
  await expect(page.getByText(/Injury rows lack report timestamps/)).toBeVisible();
  await page.clock.fastForward(11_000);
  await expect(coverage).toContainText('Snapshot needs refresh');
  await expect(coverage.getByText('Imported snapshot', { exact: true })).toHaveCount(0);
  await page.setViewportSize({ width: 320, height: 720 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(320);
});
