import { test, expect, type Page } from '@playwright/test';

const decision = { matchId: 'score-1', teamA: 'Home team', teamB: 'Away team', matchup: 'Home team vs Away team',
  side: 'Home team', confidence: 55, action: 'CONSIDER', data: 'Partial Data', reason: 'Compare scoring evidence.',
  sport: 'soccer', homeId: 1, awayId: 2, leagueId: 39, matchDate: '2027-01-01T15:00:00Z' };
const research = { available: true, model: 'test_research', projected_score: { home: 1.8, away: 1.1, total: 2.9 },
  intervals: { home: { low: 0, high: 3 }, away: { low: 0, high: 2 } }, notice: 'Retrospective evaluation only.',
  validation: { status: 'retrospective_holdout', test_games: 400, points_mae: .9, baseline_mae: 1.0, interval_coverage: { home: .81, away: .79 }, test_end: '2026-06-01' },
  references: [{ title: 'Research paper', url: 'https://doi.org/10.1111/1467-9876.00065' }] };
const primary = { line: 2.5, over_prob: 55, under_prob: 45, lean: 'OVER' };
const top = { home: 2, away: 1, score: '2-1', prob: 9.5, result: 'H' };
const soccer = { sport: 'soccer', team_a: 'Home team', team_b: 'Away team', model: 'test_research', research,
  projected_score: research.projected_score, lambda_a: 1.8, lambda_b: 1.1,
  most_likely_score: top, top_scorelines: [top], matrix_grid: [top], win_probabilities: { home: 55, draw: 25, away: 20 },
  over_under: [primary], primary_ou_line: primary, btts: { yes: 51, no: 49 } };

async function setup(page: Page, sport = 'soccer', extra: Record<string, unknown> = {}) {
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/session') return route.fulfill({ json: { user: null } });
    if (path === '/api/matches/score-1') return route.fulfill({ json: { decision: { ...decision, sport, ...extra } } });
    return route.fulfill({ status: 503, json: { error: 'Not part of this scoring check.' } });
  });
}

test('soccer separates expected goals from exact scores and shows research and uncertainty', async ({ page }) => {
  await setup(page);
  await page.route('**/api/scoreline/soccer?*', route => {
    expect(new URL(route.request().url()).searchParams.get('before')).toBe(decision.matchDate);
    return route.fulfill({ json: { status: 'ok', result: soccer } });
  });
  await page.goto('/match-analysis?sport=soccer&match_id=score-1');
  await page.getByText('Score forecast & analysis', { exact: true }).click();
  const predictor = page.getByLabel('Scoring predictor', { exact: true });
  await expect(predictor.getByLabel('Projected score')).toContainText('1.8');
  await expect(predictor).toContainText('Most likely scoreline');
  await expect(predictor.getByLabel('Score prediction ranges')).toContainText('80% target range');
  await expect(predictor).toContainText('Later-game test: 400 games');
  await expect(predictor.getByRole('link', { name: /Research paper/ })).toHaveAttribute('href', 'https://doi.org/10.1111/1467-9876.00065');
  await page.setViewportSize({ width: 320, height: 720 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(320);
  await page.reload();
  await page.getByText('Score forecast & analysis', { exact: true }).click();
  await expect(predictor.getByLabel('Projected score')).toContainText('1.8');
});

test('malformed scoring response displays an error and retry recovers without a page crash', async ({ page }) => {
  await setup(page);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  let recover = false;
  await page.route('**/api/scoreline/soccer?*', route => route.fulfill({ json: { status: 'ok', result: recover ? soccer : { sport: 'soccer', projected_score: {} } } }));
  await page.goto('/match-analysis?sport=soccer&match_id=score-1');
  await page.getByText('Score forecast & analysis', { exact: true }).click();
  await expect(page.getByLabel('Scoring predictor').getByRole('alert')).toContainText('incomplete data');
  recover = true;
  await page.getByLabel('Scoring predictor').getByRole('button', { name: 'Try again' }).click();
  await expect(page.getByLabel('Projected score')).toContainText('1.8');
  expect(errors).toEqual([]);
});

test('NBA labels projected margin correctly and does not advertise tied final scores', async ({ page }) => {
  await setup(page, 'nba');
  const nba = { ...soccer, sport: 'nba', proj_margin: 8, spread_favorite: 'Home team', sigma_total: 18,
    projected_score: { home: 114, away: 106, total: 220 }, most_likely_range: { ...top, home: 114, away: 106, score: '114-106', prob: .2 },
    top_scorelines: [{ ...top, home: 114, away: 106, score: '114-106', prob: .2 }], win_probabilities: { home: 65, away: 35 } };
  await page.route('**/api/scoreline/nba?*', route => route.fulfill({ json: { status: 'ok', result: nba } }));
  await page.goto('/match-analysis?sport=nba&match_id=score-1');
  await page.getByText('Score forecast & analysis', { exact: true }).click();
  const predictor = page.getByLabel('Scoring predictor', { exact: true });
  await expect(predictor).toContainText('Projected margin: 8.0 points');
  await expect(predictor.getByText('Draw', { exact: true })).toHaveCount(0);
  await expect(predictor.getByLabel('Projected score')).toContainText('114.0');
});

test('NBA shows quarters, starting matchups and projected box lines', async ({ page }) => {
  await setup(page, 'nba');
  const player = (id: string, name: string, position: string, pts: number) => ({ id, name, position, status: '', availability: 1, value: 3,
    minutes: 34, pts, reb: 6, ast: 5, fg3m: 2, stl: 1, blk: .5 });
  const box = (prefix: string) => ({ players: ['PG', 'SG', 'SF', 'PF', 'C'].map((pos, i) => player(`${prefix}${i}`, `${prefix} ${pos}`, pos, 20 - i)),
    unavailable: prefix === 'Home' ? [{ id: 'x', name: 'Injured Star', status: 'Out', pts: 27 }] : [], unassigned_minutes: 0 });
  const home = box('Home'), away = box('Away');
  const nba = { ...soccer, sport: 'nba', proj_margin: 4, spread_favorite: 'Home team', sigma_total: 19,
    projected_score: { home: 114, away: 110, total: 224 }, most_likely_range: { ...top, home: 114, away: 110, score: '114-110', prob: .3 },
    top_scorelines: [{ ...top, home: 114, away: 110, score: '114-110', prob: .3 }], win_probabilities: { home: 62, away: 38 },
    intervals: { home: { low: 98, high: 130 }, away: { low: 94, high: 126 } }, total_interval: { low: 199, high: 249 }, overtime_prob: 4.9,
    quarters: [{ period: 'Q1', home: 28.6, away: 27.6 }, { period: 'Q2', home: 28.3, away: 27.3 }, { period: 'Q3', home: 28.7, away: 27.7 },
      { period: 'Q4', home: 27.2, away: 26.2 }, { period: '1st half', home: 56.9, away: 54.9 }],
    players: { home, away },
    matchups: { starters: home.players.map((p, i) => ({ position: p.position, home: p, away: away.players[i], edge: 'even', value_gap: 0 })),
      bench: { home: { players: 4, pts: 30, minutes: 70, value: 2 }, away: { players: 4, pts: 28, minutes: 70, value: 1.5 } } } };
  await page.route('**/api/scoreline/nba?*', route => route.fulfill({ json: { status: 'ok', result: nba } }));
  await page.goto('/match-analysis?sport=nba&match_id=score-1');
  await page.getByText('Score forecast & analysis', { exact: true }).click();
  const predictor = page.getByLabel('Scoring predictor', { exact: true });
  await expect(predictor).toContainText('80% ranges');
  await expect(predictor).toContainText('overtime 4.9%');
  await predictor.getByText('Full breakdown', { exact: true }).click();
  await expect(predictor.getByText('Projected Points by Quarter')).toBeVisible();
  await expect(predictor.getByText('Starting Matchups')).toBeVisible();
  await expect(predictor).toContainText('Home PG');
  await expect(predictor).toContainText('Out: Injured Star (Out, 27.0 ppg)');
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(await page.evaluate(() => window.innerWidth));
});

test('NFL shows the trained projection separately from descriptive scenario shares', async ({ page }) => {
  await setup(page, 'nfl');
  await page.route('**/api/nfl/matches/score-1/analysis?*', route => route.fulfill({ json: { result: {
    projected_score: { home: 20, away: 17 }, probabilities: { home: 60, draw: 5, away: 35 }, samples: { home: 5, away: 5 },
    data_source: 'nflverse', notice: 'Scenario shares are descriptive.',
    research: { ...research, projected_score: { home: 24.5, away: 20.1, total: 44.6 } },
  } } }));
  await page.goto('/match-analysis?sport=nfl&match_id=score-1');
  await page.getByText('Score forecast & analysis', { exact: true }).click();
  await expect(page.getByLabel('Experimental NFL scoring research')).toContainText('24.5');
  await expect(page.getByText('Projected points:', { exact: false })).toContainText('20.0');
  await expect(page.getByLabel('Score prediction ranges')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Recent scoring scenarios', exact: true })).toBeVisible();
});
