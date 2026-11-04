import { expect, test } from '@playwright/test';

for (const view of ['slate', 'analysis']) {
  test(`soccer ${view} obtains a new source observation at expiry`, async ({ page }) => {
    await page.clock.install({ time: new Date('2026-10-24T13:00:00Z') });
    let reads = 0;
    await page.route('**/api/**', route => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/api/session') return route.fulfill({ json: { user: null } });
      if (path === '/api/leagues') return route.fulfill({ json: { leagues: [{ id: 39, name: 'Premier League', country: 'England', flag: '' }] } });
      if (path === (view === 'slate' ? '/api/dashboard/soccer' : '/api/matches/refresh-soccer')) {
        reads++;
        const renewed = reads > 1;
        const decision = { matchId: 'refresh-soccer', sport: 'soccer', teamA: 'Home team', teamB: 'Away team',
          matchup: 'Home team vs Away team', matchDate: '2026-10-24T16:00:00Z', eventStatus: 'pre',
          side: 'Home team', action: 'CONSIDER', confidence: 70, winProbability: renewed ? 67 : 63,
          homeProbability: renewed ? 67 : 63, drawProbability: 20, data: 'Partial Data', homeId: 1, awayId: 2, leagueId: 39,
          reason: renewed ? 'Refreshed source observation' : 'Initial source observation',
          dataFreshness: { source: 'Controlled source', state: 'fresh', observed_at: renewed ? '2026-10-24T13:00:31Z' : '2026-10-24T13:00:00Z',
            expires_at: renewed ? '2026-10-24T13:02:00Z' : '2026-10-24T13:00:30Z', max_stale_at: '2026-10-24T13:05:00Z' } };
        return route.fulfill({ json: view === 'analysis' ? { decision } : { slate: [decision], topOpportunities: [],
          availableDates: ['2026-10-24'], selectedDate: '2026-10-24', plan: { bet: 0, consider: 1, skip: 0 } } });
      }
      return route.fulfill({ status: 422, json: { error: 'Unavailable in this controlled source-refresh test.' } });
    });
    await page.goto(view === 'slate' ? '/soccer' : '/match-analysis?sport=soccer&match_id=refresh-soccer&date=2026-10-24&league_id=39');
    const estimate = view === 'slate' ? page.locator('.match-market').first() : page.locator('.field-measure strong');
    await expect(estimate).toContainText(view === 'slate' ? '63%' : '63.0%');
    await page.clock.fastForward(31_000);
    await expect(estimate).toContainText(view === 'slate' ? '67%' : '67.0%');
    expect(reads).toBe(2);
    await page.clock.fastForward(10_000);
    expect(reads).toBe(2); // One refresh, not a request loop.
    if (view === 'analysis') await expect(page.locator('.field-measure strong')).toHaveText('67.0%');
  });
}

test('soccer refresh failure cannot keep showing an expired prediction', async ({ page }) => {
  await page.clock.install({ time: new Date('2026-10-24T13:00:00Z') });
  let reads = 0;
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/session') return route.fulfill({ json: { user: null } });
    if (path === '/api/matches/refresh-soccer') {
      if (++reads > 1) return route.fulfill({ status: 503, json: { error: 'Soccer source refresh unavailable' } });
      return route.fulfill({ json: { decision: { matchId: 'refresh-soccer', sport: 'soccer', teamA: 'Home team', teamB: 'Away team',
        matchup: 'Home team vs Away team', matchDate: '2026-10-24T16:00:00Z', eventStatus: 'pre', side: 'Home team',
        action: 'CONSIDER', confidence: 70, winProbability: 63, data: 'Partial Data', reason: 'Initial source observation',
        dataFreshness: { state: 'fresh', observed_at: '2026-10-24T13:00:00Z', expires_at: '2026-10-24T13:00:30Z', max_stale_at: '2026-10-24T13:05:00Z' } } } });
    }
    return route.fulfill({ status: 422, json: { error: 'Unavailable in this controlled test.' } });
  });
  await page.goto('/match-analysis?sport=soccer&match_id=refresh-soccer');
  await expect(page.locator('.field-measure strong')).toHaveText('63.0%');
  await page.clock.fastForward(31_000);
  await expect(page.getByRole('alert')).toContainText('Soccer source refresh unavailable');
  await expect(page.locator('.field-measure')).toHaveCount(0);
  expect(reads).toBe(2);
});
