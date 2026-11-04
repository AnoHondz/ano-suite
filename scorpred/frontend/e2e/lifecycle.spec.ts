import { expect, test, type Page } from '@playwright/test';

test.use({ timezoneId: 'Pacific/Auckland' });

const origin = '2026-10-18T01:00:00Z';
const primary = { line: 2.5, over_prob: 55, under_prob: 45, lean: 'OVER' };
const top = { home: 2, away: 1, score: '2-1', prob: 9.5, result: 'H' };
const soccer = { sport: 'soccer', team_a: 'Home team', team_b: 'Away team', model: 'test',
  projected_score: { home: 1.8, away: 1.1, total: 2.9 }, lambda_a: 1.8, lambda_b: 1.1,
  most_likely_score: top, top_scorelines: [top], matrix_grid: [top], win_probabilities: { home: 55, draw: 25, away: 20 },
  over_under: [primary], primary_ou_line: primary, btts: { yes: 51, no: 49 } };
const nba = { ...soccer, sport: 'nba', proj_margin: 8, spread_favorite: 'Home team', sigma_total: 18,
  projected_score: { home: 114, away: 106, total: 220 }, most_likely_range: { ...top, home: 114, away: 106, prob: .2 },
  top_scorelines: [{ ...top, home: 114, away: 106, prob: .2 }], win_probabilities: { home: 65, away: 35 } };

async function setup(page: Page, sport: 'soccer' | 'nba' | 'nfl', kickoff: string, freshness?: Record<string, string>) {
  await page.clock.install({ time: new Date(origin) });
  const decision = { matchId: 'lifecycle-1', teamA: 'Home team', teamB: 'Away team', matchup: 'Home team vs Away team',
    side: 'Home team', confidence: 68, action: 'CONSIDER', data: 'Partial Data', reason: 'Pregame comparison.',
    sport, homeId: 1, awayId: 2, leagueId: 39, matchDate: kickoff, eventStatus: 'pre', dataFreshness: freshness };
  await page.route('**/api/**', route => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path === '/api/session') return route.fulfill({ json: { user: null } });
    if (path === '/api/matches/lifecycle-1') return route.fulfill({ json: { decision } });
    if (path.startsWith('/api/dashboard/')) return route.fulfill({ json: { slate: [decision], topOpportunities: [decision],
      plan: { bet: 0, consider: 1, skip: 0 }, selectedDate: '2026-10-17', availableDates: ['2026-10-17', '2026-10-18'] } });
    if (path === '/api/nfl/matches/lifecycle-1/analysis') {
      expect(url.searchParams.get('date')).toBe('2026-10-17');
      return route.fulfill({ json: { result: { projected_score: { home: 24, away: 20 }, probabilities: { home: 60, draw: 5, away: 35 },
        samples: { home: 5, away: 5 }, notice: 'Scenario shares are descriptive.' } } });
    }
    if (path.startsWith('/api/scoreline/')) {
      expect(url.searchParams.get('event_id')).toBe('lifecycle-1');
      return route.fulfill({ json: { status: 'ok', result: sport === 'soccer' ? soccer : nba } });
    }
    return route.fulfill({ status: 503, json: { error: 'Unexpected request.' } });
  });
}

test('Eastern date tabs, kickoff labels and analysis links agree in another browser timezone', async ({ page }) => {
  await setup(page, 'nfl', '2026-10-18T02:00:00Z');
  await page.goto('/nfl');
  await expect(page.getByRole('button', { name: 'Today', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Tomorrow', exact: true })).toBeVisible();
  await expect(page.locator('.match-card-footer')).toContainText('Oct 17');
  await expect(page.locator('.match-card-footer')).toContainText('10:00 PM ET');
  await expect(page.locator('.live-chip')).toContainText('09:00 PM ET');
  await page.getByRole('button', { name: /Analyze Match/ }).click();
  await expect(page).toHaveURL(/date=2026-10-17/);
  await page.getByText('Score forecast & analysis', { exact: true }).click();
  await expect(page.getByLabel('Projected score').first()).toBeVisible();
});

for (const sport of ['soccer', 'nba', 'nfl'] as const) {
  test(`${sport} withdraws an already loaded prediction at kickoff without a reload`, async ({ page }) => {
    await setup(page, sport, '2026-10-18T01:00:03Z');
    await page.goto(`/match-analysis?sport=${sport}&match_id=lifecycle-1`);
    await page.getByText('Score forecast & analysis', { exact: true }).click();
    await expect(page.getByLabel('Projected score').first()).toBeVisible();
    await expect(page.locator('.action-badge')).toHaveAttribute('aria-label', 'Betting decision: CONSIDER');
    await page.clock.fastForward(4000);
    await expect(page.locator('.action-badge')).toHaveAttribute('aria-label', 'Betting decision: SKIP');
    await expect(page.getByLabel('Projected score')).toHaveCount(0);
    await expect(page.getByLabel('Scoring predictor', { exact: true })).toContainText('unavailable after kickoff');
    await expect(page.getByLabel('Match prediction', { exact: true })).toContainText('Prediction unavailable');
    await expect(page.locator('.field-measure')).toHaveCount(0);
    await expect(page.locator('.field-pick')).not.toContainText('Home team');
  });
}

test('source expiry removes a loaded score forecast and its recommendation', async ({ page }) => {
  await setup(page, 'nba', '2026-10-18T02:00:00Z', { state: 'fresh', observed_at: origin,
    expires_at: '2026-10-18T01:00:03Z', max_stale_at: '2026-10-18T01:05:00Z' });
  await page.goto('/match-analysis?sport=nba&match_id=lifecycle-1');
  await page.getByText('Score forecast & analysis', { exact: true }).click();
  await expect(page.getByLabel('Projected score').first()).toBeVisible();
  await page.clock.fastForward(4000);
  await expect(page.locator('.action-badge')).toHaveAttribute('aria-label', 'Betting decision: SKIP');
  await expect(page.locator('.field-measure')).toHaveCount(0);
  await expect(page.getByLabel('Projected score')).toHaveCount(0);
  await expect(page.getByLabel('Scoring predictor', { exact: true })).toContainText('source data needs a fresh update');
});

test('home separates NFL scenario shares and recalculates counts at kickoff', async ({ page }) => {
  await setup(page, 'nfl', '2026-10-18T01:00:15Z');
  await page.route('**/api/dashboard/*', route => {
    const sport = new URL(route.request().url()).pathname.split('/').pop();
    const decision = { matchId: sport, sport, teamA: `${sport} Home`, teamB: `${sport} Away`, matchup: `${sport} Home vs ${sport} Away`,
      side: `${sport} Home`, action: 'CONSIDER', confidence: sport === 'nfl' ? 100 : 67, data: 'Partial Data',
      reason: 'Pregame comparison.', matchDate: '2026-10-18T01:00:15Z', eventStatus: 'pre' };
    return route.fulfill({ json: { slate: [decision], topOpportunities: [decision], plan: { bet: 99, consider: 99, skip: 99 } } });
  });
  await page.goto('/');
  const shortlists = page.locator('section[aria-label$="shortlist"]');
  await expect(shortlists).toHaveCount(3);
  expect(await shortlists.evaluateAll(elements => elements.map(element => element.getAttribute('aria-label')))).toEqual(['Soccer shortlist', 'NBA shortlist', 'NFL shortlist']);
  await expect(page.locator('.plan-line .plan-consider')).toContainText('3');
  await expect(page.getByLabel('NFL shortlist')).toContainText('Scoring scenario share');
  await page.clock.fastForward(16000);
  await expect(page.getByRole('button', { name: /Analyze Match/ })).toHaveCount(0);
  await expect(page.getByLabel('Slate overview')).toContainText('3 skip');
});

test('a score response expires even if its parent card is still fresh', async ({ page }) => {
  await setup(page, 'nba', '2026-10-18T02:00:00Z');
  await page.route('**/api/scoreline/nba?*', route => route.fulfill({ json: { status: 'ok', result: { ...nba,
    data_freshness: { state: 'fresh', observed_at: origin, expires_at: '2026-10-18T01:00:15Z', max_stale_at: '2026-10-18T01:05:00Z' } } } }));
  await page.goto('/match-analysis?sport=nba&match_id=lifecycle-1');
  await page.getByText('Score forecast & analysis', { exact: true }).click();
  await expect(page.getByLabel('Projected score').first()).toBeVisible();
  await page.clock.fastForward(16000);
  await expect(page.getByLabel('Projected score')).toHaveCount(0);
  await expect(page.getByLabel('Scoring predictor', { exact: true })).toContainText('source data needs a fresh update');
});
