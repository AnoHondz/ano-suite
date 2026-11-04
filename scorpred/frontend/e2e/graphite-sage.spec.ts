import { test, expect, type Page } from '@playwright/test';

// Controlled fixtures exercise presentation contracts; they are never shipped as app data.
const origin = '2026-10-19T12:00:00Z';
const kickoff = '2026-10-20T23:00:00Z';
const fixture = { matchId: 'design-check', sport: 'nfl', teamA: 'Home test team', teamB: 'Away test team',
  matchup: 'Home test team vs Away test team', side: 'Home test team', confidence: 60,
  scenarioShare: 60, tiedScenarioShare: 5, action: 'CONSIDER', data: 'Limited Data',
  reason: 'Controlled test fixture for the existing scenario contract.', matchDate: kickoff, eventStatus: 'pre' };

async function setup(page: Page) {
  await page.clock.install({ time: new Date(origin) });
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/session') return route.fulfill({ json: { user: null } });
    if (path === '/api/leagues') return route.fulfill({ json: { leagues: [{ id: 39, name: 'Premier League', flag: '' }] } });
    if (path === '/api/matches/design-check') return route.fulfill({ json: { decision: fixture } });
    if (path === '/api/nfl/research/winner/models') return route.fulfill({ status: 404, json: { error: 'NFL winner research is disabled.' } });
    if (path === '/api/nfl/matches/design-check/analysis') return route.fulfill({ json: { result: {
      projected_score: { home: 20, away: 18 }, probabilities: { home: 60, draw: 5, away: 35 },
      samples: { home: 4, away: 5 }, data_source: 'nflverse', notice: 'Descriptive test scenarios.' } } });
    if (path.startsWith('/api/dashboard/')) {
      const sport = path.split('/').pop();
      const card = { ...fixture, sport, matchId: `${sport}-fixture` };
      return route.fulfill({ json: { slate: sport === 'insights' ? [] : [card], topOpportunities: [],
        radarCards: [], radarGroups: {}, volatilityRows: [], selectedDate: '2026-10-20', availableDates: ['2026-10-20'] } });
    }
    return route.fulfill({ status: 503, json: { error: 'Unexpected test request.' } });
  });
}

test('historical metrics and calibration are the saved Phase 2.1 results, separate from slate decisions', async ({ page }) => {
  await setup(page);
  const predictionRequests: string[] = [];
  page.on('request', request => { if (/\/research\/winner\//.test(request.url())) predictionRequests.push(request.url()); });
  await page.goto('/insights');
  await page.getByRole('button', { name: 'Historical performance', exact: true }).click();
  const history = page.getByLabel('NFL historical model performance');
  await expect(history).toContainText('139');
  await expect(history).toContainText('Class B');
  await expect(history).toContainText('not live production performance');
  await expect(history).toContainText('No statistically significant advantage established');
  const rows = history.locator('.evaluation-table-card tbody tr');
  await expect(rows).toHaveCount(5);
  await expect(rows.nth(0)).toContainText('0.684146');
  await expect(rows.nth(0)).toContainText('0.245510');
  await expect(rows.nth(0)).toContainText('56.83%');
  await expect(rows.nth(1)).toContainText('55.40%');
  await expect(rows.nth(2)).toContainText('61.15%');
  await expect(rows.nth(3)).toContainText('0.708323');
  await expect(rows.nth(4)).toContainText('0.230126');
  await page.getByLabel('Calibration model').selectOption('nonlinear_forest:selected');
  await expect(page.getByRole('img', { name: /^Calibration for Random forest/ })).toBeVisible();
  await expect(history.locator('.calibration-dot')).toHaveCount(7);
  expect(predictionRequests).toEqual([]);
  await expect(page.getByLabel('Slate overview')).toBeHidden();
  await page.getByRole('button', { name: 'Current slate', exact: true }).click();
  await expect(page.getByLabel('Slate overview')).toBeVisible();
});

test('NFL disabled research never requests a game forecast and scenario outcomes retain their identities', async ({ page }) => {
  await setup(page);
  const requests: string[] = [];
  page.on('request', request => { if (request.url().includes('/research/winner/')) requests.push(new URL(request.url()).pathname); });
  await page.goto('/match-analysis?sport=nfl&match_id=design-check');
  await page.getByText('Score forecast & analysis', { exact: true }).click();
  const scenarios = page.getByLabel('Recent scoring scenario shares', { exact: true });
  await expect(scenarios).toContainText('60.0%');
  await expect(scenarios).toContainText('5.0%');
  await expect(scenarios).toContainText('35.0%');
  const research = page.getByLabel('NFL winner model research');
  await expect(research).toContainText('Research disabled or unavailable');
  await expect(research).toContainText('NFL winner research is disabled.');
  await expect(page.getByLabel('Match prediction', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Team comparison', exact: true })).toBeVisible();
  expect([...new Set(requests)]).toEqual(['/api/nfl/research/winner/models']);
});

test('home date controls preserve the requested date for all sports and keep SKIP fixtures', async ({ page }) => {
  await setup(page);
  await page.route('**/api/dashboard/nfl*', route => route.fulfill({ json: { slate: [{ ...fixture, action: 'SKIP' }], selectedDate: '2026-10-20' } }));
  await page.goto('/?date=2026-10-20');
  await expect(page.getByLabel('NFL shortlist').locator('.action-badge')).toHaveText('SKIP');
  const next = page.waitForRequest(request => request.url().includes('/api/dashboard/nfl?date=2026-10-21'));
  await page.getByRole('button', { name: 'Next day', exact: true }).click();
  await next;
  await expect(page).toHaveURL('/?date=2026-10-21');
  await expect(page.getByLabel('Slate date', { exact: true })).toHaveValue('2026-10-21');
  await page.getByRole('button', { name: 'NFL', exact: true }).last().click();
  await expect(page.getByLabel('Soccer shortlist')).toHaveCount(0);
  await expect(page.getByLabel('NFL shortlist')).toBeVisible();
});

test('an individual provider failure leaves the other sports readable without inventing complete slate counts', async ({ page }) => {
  await setup(page);
  await page.route('**/api/dashboard/soccer*', route => route.fulfill({ status: 503, json: { error: 'Soccer test provider unavailable.' } }));
  await page.goto('/');
  await expect(page.getByRole('alert')).toContainText('Soccer test provider unavailable.');
  await expect(page.getByLabel('NFL shortlist').locator('.decision-card')).toBeVisible();
  await expect(page.getByLabel('Slate overview').locator('.plan-stat-num').first()).toContainText('—');
});

for (const width of [320, 390, 768, 1440]) test(`all dashboard layouts and NFL analysis fit ${width}px without page errors`, async ({ page }) => {
  await setup(page);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
    await page.setViewportSize({ width, height: 1000 });
    for (const path of ['/', '/soccer', '/nba', '/nfl', '/match-analysis?sport=nfl&match_id=design-check', '/insights']) {
      await page.goto(path);
      if (path === '/insights') await page.getByRole('button', { name: 'Historical performance', exact: true }).click();
      await expect(page.locator('.skeleton-line')).toHaveCount(0);
      expect(await page.evaluate(() => document.documentElement.scrollWidth), `${path} at ${width}px`).toBe(width);
    }
  expect(errors).toEqual([]);
});
