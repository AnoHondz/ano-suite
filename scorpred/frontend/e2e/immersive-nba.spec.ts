import { test, expect, type Page } from '@playwright/test';

const decision = { sport: 'nba', matchId: 'context-test', teamA: 'Home test team', teamB: 'Away test team',
  homeId: 20, awayId: 5, matchup: 'Home test team vs Away test team', side: 'Unavailable', action: 'SKIP', confidence: 0,
  reason: 'Insufficient current-season samples.', data: 'Limited Data', matchDate: '2026-10-23T23:00:00Z', eventStatus: 'pre' };
const summary = { games: 2, wins: 1, losses: 1, points_for: 102.5, points_against: 100.5, scope: 'Observed archive sample', recent_results: [] };
const team = { team: { id: '20', name: 'Home test team' }, previous: summary,
  current: { ...summary, games: 0, wins: 0, losses: 0, points_for: null, points_against: null },
  current_season: 2027, previous_season: 2026, current_status: 'unavailable', current_source: {},
  historical_source: { files: ['local-test.json'], historical_pregame_availability: 'unverified' } };

async function setup(page: Page) {
  await page.clock.install({ time: new Date('2026-10-22T12:00:00Z') });
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/session') return route.fulfill({ json: { user: null } });
    if (path === '/api/matches/context-test') return route.fulfill({ json: { decision } });
    if (path === '/api/dashboard/nba') return route.fulfill({ json: { slate: [decision], topOpportunities: [], selectedDate: '2026-10-23', availableDates: ['2026-10-23'] } });
    if (path.includes('/context')) return route.fulfill({ json: { result: { version: 'nba-descriptive-context-v1', role: 'descriptive_only',
      teams: { home: team, away: { ...team, team: { id: '5', name: 'Away test team' } } }, game: {},
      statistics_cutoff: '2026-10-21T12:00:00Z', notice: 'Historical observations only; no prediction fallback.', refresh_pending: false } } });
    if (path === '/api/nba/games') return route.fulfill({ json: { slate: [decision], topOpportunities: [], selectedDate: '2026-10-23', availableDates: ['2026-10-23'] } });
    return route.fulfill({ status: 422, json: { message: 'Scoring unavailable for this controlled sample.' } });
  });
}

test('historical comparison is useful without inventing a current record or probability', async ({ page }) => {
  await setup(page);
  await page.goto('/match-analysis?sport=nba&match_id=context-test');
  const comparison = page.getByLabel('NBA team comparisons');
  await expect(comparison).toContainText('102.5');
  await expect(comparison).toContainText('Descriptive');
  expect(await comparison.evaluate(element => Boolean(element.compareDocumentPosition(document.querySelector('.prediction-field')!) & Node.DOCUMENT_POSITION_PRECEDING))).toBe(true);
  await page.getByRole('button', { name: 'Current season', exact: true }).click();
  await expect(comparison).toContainText('do not establish a 0–0 season record');
  await expect(comparison).toContainText('Unavailable');
  await expect(page.getByLabel('Match prediction', { exact: true })).toContainText('Prediction unavailable');
});

test('NBA overview shows only the slate; comparisons open from the game card', async ({ page }) => {
  await setup(page);
  await page.goto('/nba');
  const schedule = page.getByLabel('NBA slate');
  await expect(schedule.locator('.decision-card')).toHaveCount(1);
  await expect(schedule).toContainText('Insufficient current-season samples.');
  await expect(page.getByLabel('NBA plays')).toHaveCount(0);
  await expect(page.getByLabel('NBA team comparisons')).toHaveCount(0);
  await expect(page.locator('.matchup-stage')).toHaveCount(0);
  await schedule.getByRole('button', { name: /Analyze Matchup/ }).click();
  await expect(page).toHaveURL(/match_id=context-test/);
  await expect(page.getByLabel('NBA team comparisons')).toContainText('102.5');
});

test('priced NBA plays lead the page with win probability and the sportsbook line', async ({ page }) => {
  await setup(page);
  const play = { ...decision, matchId: 'priced-game', side: 'Home test team', action: 'BET', confidence: 64, winProbability: 64.2,
    data: 'Strong Data', reason: 'Projected Home test team 116–110 Away test team. Fair line Home test team -6, Home test team -179.',
    market: { bookmaker: 'DraftKings', updated_at: '2026-10-22T11:59:00Z', home_decimal: 1.8, away_decimal: 2.1, home_spread: -4.5 } };
  await page.route('**/api/dashboard/nba*', route => route.fulfill({ json: { slate: [play, decision], topOpportunities: [play],
    selectedDate: '2026-10-23', availableDates: ['2026-10-23'] } }));
  await page.goto('/nba');
  const plays = page.getByLabel('NBA plays');
  await expect(plays.locator('.decision-card')).toHaveCount(1);
  await expect(plays).toContainText('64%');
  await expect(plays).toContainText('Win probability');
  await expect(plays).toContainText('Home test team -4.5 · Moneyline 1.80 / 2.10');
  await expect(plays).not.toContainText('Rule confidence');
  await expect(page.getByLabel('NBA slate').locator('.decision-card')).toHaveCount(1);
});

test('early-season evaluation is labeled research and never requests a disabled preview', async ({ page }) => {
  await setup(page);
  const previews: string[] = [];
  page.on('request', request => { if (request.url().includes('/research/early-season/matches/')) previews.push(request.url()); });
  await page.route('**/api/nba/research/early-season', route => route.fulfill({ json: { available: true, research_enabled: false,
    report: { version: 'controlled-study', evaluated_games: 10, conclusion: 'Prospective validation is required.',
      coverage: { '2024': { early_games: 10, winner_rule_gate: 6, research_available: 10 } },
      metrics: { blend: { games: 10, log_loss: .65, brier: .23, accuracy: .6 } } } } }));
  await page.goto('/match-analysis?sport=nba&match_id=context-test');
  const study = page.getByLabel('NBA early-season research study');
  await study.locator('summary').click();
  await expect(study).toContainText('10 games tested');
  await expect(study).toContainText('production predictions unchanged');
  await expect(study).toContainText('Experimental previews are disabled');
  await expect(study.getByRole('button')).toHaveCount(0);
  expect(previews).toHaveLength(0);
  await expect(page.locator('.matchup-stage')).toContainText('Prediction unavailable');
});

test('a failed schedule refresh keeps descriptive comparisons and an explicit warning', async ({ page }) => {
  await setup(page);
  await page.route('**/api/matches/context-test*', route => route.fulfill({ json: { decision,
    warnings: ['Schedule refresh failed; showing last observed matchup details without a prediction.'] } }));
  await page.goto('/match-analysis?sport=nba&match_id=context-test');
  await expect(page.getByRole('status').filter({ hasText: 'Schedule refresh failed' })).toBeVisible();
  await expect(page.getByLabel('NBA team comparisons')).toContainText('102.5');
  await expect(page.locator('.matchup-stage')).toContainText('Prediction unavailable');
});

test('archived NBA selection opens the existing analysis route', async ({ page }) => {
  await setup(page);
  await page.goto('/nba?view=archive');
  await page.getByRole('button', { name: /Analyze Matchup/ }).click();
  await expect(page).toHaveURL(/match_id=context-test/);
  await expect(page.getByLabel('NBA team comparisons')).toContainText('102.5');
});

test('lite field displays the prediction without graphics controls or a WebGL renderer', async ({ page }) => {
  await setup(page);
  await page.goto('/match-analysis?sport=nba&match_id=context-test');
  await expect(page.locator('.prediction-surface')).toBeVisible();
  await expect(page.getByRole('button', { name: /Away view|Enable 3D|Replay/ })).toHaveCount(0);
  await expect(page.getByLabel('Match prediction', { exact: true })).toBeVisible();
  await expect(page.locator('canvas')).toHaveCount(0);
  const resources = await page.evaluate(() => performance.getEntriesByType('resource').map(resource => resource.name));
  expect(resources.some(name => name.includes('ArenaScene'))).toBe(false);
});

test('reduced motion keeps a static lightweight venue and all data controls', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await setup(page);
  await page.goto('/match-analysis?sport=nba&match_id=context-test');
  await expect(page.locator('.prediction-surface')).toBeVisible();
  await expect(page.locator('.prediction-surface svg')).toHaveCSS('transform', 'none');
  await expect(page.locator('canvas')).toHaveCount(0);
  await expect(page.getByLabel('NBA team comparisons')).toContainText('102.5');
  await expect(page.getByLabel('Main navigation')).toBeVisible();
});
