import { test, expect, type Page } from '@playwright/test';

async function setup(page: Page, enabled: boolean) {
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/session') return route.fulfill({ json: { user: null } });
    if (path === '/api/leagues') return route.fulfill({ json: { leagues: [] } });
    if (path.startsWith('/api/dashboard/')) return route.fulfill({ json: { radarCards: [], radarGroups: {}, volatilityRows: [], slate: [] } });
    if (path === '/api/nfl/research/winner/forecasts/status') {
      if (!enabled) return route.fulfill({ status: 404, json: { error: 'NFL winner research is disabled.' } });
      return route.fulfill({ json: { research_only: true, model_enabled_in_production: false, result: {
        research_only: true, enabled: false, production_active: false, registered: true,
        models: [{ model_id: 'fixture-elo-v1', model_version: 'fixture-elo-v1', training_games: 10,
          training_cutoff: '2026-02-01T00:00:00Z', artifact_sha256: 'a'.repeat(64), training_evidence_class: 'B' }],
        upcoming: [{ game_id: 'fixture', home_team: 'BUF', away_team: 'NE', scheduled_kickoff: '2026-11-26T01:00:00Z',
          deadline: '2026-11-26T00:00:00Z', status: 'scheduled', reserved_holdout: true }],
        archived_forecasts: 2, archived_games: 1, missed_games: 0, rejected_attempts: 1,
        evaluation_locked: true, holdout_games: 109, earliest_review_at: '2027-03-01T00:00:00Z',
        evidence_quality: 'Class A observed inputs; Class B historical model training; not production validated',
      } } });
    }
    return route.fulfill({ status: 503, json: { error: 'Unexpected test request.' } });
  });
}

test('disabled research preserves flags and requests no forecasts or outcomes', async ({ page }) => {
  await setup(page, false);
  const researchRequests: string[] = [];
  page.on('request', r => { if (r.url().includes('/research/')) researchRequests.push(`${r.method()} ${new URL(r.url()).pathname}`); });
  await page.goto('/insights');
  await page.getByRole('button', { name: 'Pregame research', exact: true }).click();
  const panel = page.getByLabel('Experimental NFL Winner Research', { exact: true });
  await expect(panel).toContainText('Not Used for Production Recommendations');
  await expect(panel).toContainText('Research disabled or unavailable');
  expect(researchRequests.length).toBeGreaterThan(0);
  expect([...new Set(researchRequests)]).toEqual(['GET /api/nfl/research/winner/forecasts/status']);
});

test('authorized status shows provenance and lock without production controls', async ({ page }) => {
  await setup(page, true);
  const calls: string[] = [];
  page.on('request', r => { if (r.url().includes('/research/')) calls.push(`${r.method()} ${new URL(r.url()).pathname}`); });
  await page.goto('/insights');
  await page.getByRole('button', { name: 'Pregame research', exact: true }).click();
  const panel = page.getByLabel('Experimental NFL Winner Research', { exact: true });
  await expect(panel).toContainText('fixture-elo-v1');
  await expect(panel).toContainText('109 games');
  await expect(panel).toContainText('Sealed');
  await expect(panel).toContainText('NE at BUF');
  await expect(panel).toContainText('2026-11-26 00:00:00 UTC');
  await expect(panel).toContainText('Class A observed inputs; Class B historical model training');
  expect(calls.length).toBeGreaterThan(0);
  expect([...new Set(calls)]).toEqual(['GET /api/nfl/research/winner/forecasts/status']);
  await expect(panel.getByRole('button', { name: /enable|evaluate|unlock|generate/i })).toHaveCount(0);
});
