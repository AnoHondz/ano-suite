import { test, expect, type Page } from '@playwright/test';
import { ballPose, PLAYERS, playerPose, playOpacity, PLAY_DURATION, type PlaySport } from '../src/components/immersive/arenaPlay';

for (const sport of ['nfl', 'nba', 'soccer'] as const) {
  test(`${sport} uses complete teams, bounded movement and a continuous visible play`, () => {
    const actors = PLAYERS[sport];
    const count = sport === 'nba' ? 5 : 11;
    expect(actors.filter(actor => actor.team === 'home')).toHaveLength(count);
    expect(actors.filter(actor => actor.team === 'away')).toHaveLength(count);
    expect(new Set(actors.map(actor => actor.id)).size).toBe(count * 2);
    if (sport === 'nfl') expect(actors.filter(actor => actor.team === 'home' && Math.abs(playerPose(actor, 0).x + .7) < .001)).toHaveLength(7);
    expect(actors.some(actor => JSON.stringify(playerPose(actor, 0)) !== JSON.stringify(playerPose(actor, 5)))).toBe(true);
    let playersInBounds = true, ballInBounds = true, grounded = true;
    let nearestPlayers = Infinity, largestBallStep = 0;
    for (let time = 0; time < PLAY_DURATION; time += .05) {
      const positions = actors.map(actor => playerPose(actor, time));
      for (const pose of positions) {
        playersInBounds &&= Math.abs(pose.x) <= 4.7 && Math.abs(pose.z) <= (sport === 'nfl' ? 2.1 : 2.5);
      }
      if (playOpacity(time) > .5) for (let i = 0; i < positions.length; i++) for (let j = i + 1; j < positions.length; j++) {
        nearestPlayers = Math.min(nearestPlayers, Math.hypot(positions[i].x - positions[j].x, positions[i].z - positions[j].z));
      }
      const ball = ballPose(sport, time);
      ballInBounds &&= Math.abs(ball.x) < 5 && Math.abs(ball.z) < 2.5 && ball.y >= .08 && ball.y < 1.3;
      if (sport === 'soccer') grounded &&= Math.abs(ball.y - .095) < .001;
      if (time < 7.5) {
        const next = ballPose(sport, time + .005);
        largestBallStep = Math.max(largestBallStep, Math.hypot(next.x - ball.x, next.z - ball.z, next.y - ball.y));
      }
    }
    expect(playersInBounds).toBe(true);
    expect(ballInBounds).toBe(true);
    expect(grounded).toBe(true);
    expect(nearestPlayers).toBeGreaterThan(.15);
    expect(largestBallStep).toBeLessThan(.05);
    for (const actor of actors) expect(playerPose(actor, PLAY_DURATION)).toEqual(playerPose(actor, 0));
  });

  test(`${sport} possession stays with a player between passes`, () => {
    const carrierRole = sport === 'nfl' ? 'WR' : sport === 'nba' ? 'C' : 'FWD';
    const carrier = PLAYERS[sport].find(actor => actor.team === 'home' && actor.role === carrierRole)!;
    const pose = playerPose(carrier, 6);
    const ball = ballPose(sport, 6);
    expect(Math.hypot(ball.x - pose.x, ball.z - pose.z)).toBeLessThan(.25);
    expect(playOpacity(8.5)).toBe(0);
  });

  test(`${sport} lite prediction sits before supporting details with one matchup heading`, async ({ page }) => {
    await setup(page, sport);
    await page.goto('/match-analysis?sport=' + sport + '&match_id=tactical-test');
    const arena = page.getByRole('region', { name: `${sport === 'soccer' ? 'Soccer' : sport.toUpperCase()} matchup prediction` });
    await expect(page.locator('h1')).toHaveCount(1);
    await expect(arena.getByLabel('Match prediction', { exact: true })).toContainText('Prediction unavailable');
    await expect(arena.locator('[data-renderer="lite"]')).toHaveCount(1);
    expect(await arena.evaluate(element => Boolean(element.compareDocumentPosition(document.querySelector('.prediction-explanation')!) & Node.DOCUMENT_POSITION_FOLLOWING))).toBe(true);
    await expect(arena.getByRole('button', { name: 'Replay', exact: true })).toHaveCount(0);
    await expect(arena.locator('canvas')).toHaveCount(0);
    await page.setViewportSize({ width: 320, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(320);
  });
}

async function setup(page: Page, sport: PlaySport) {
  await page.clock.install({ time: new Date('2026-10-23T12:00:00Z') });
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/session') return route.fulfill({ json: { user: null } });
    if (path === '/api/matches/tactical-test') return route.fulfill({ json: { decision: {
      sport, matchId: 'tactical-test', teamA: 'Home team', teamB: 'Away team', matchup: 'Home team vs Away team',
      action: 'SKIP', side: 'Unavailable', confidence: 0, data: 'Limited Data', reason: 'Forecast unavailable.',
      matchDate: '2026-10-25T17:00:00Z', eventStatus: 'pre',
    } } });
    return route.fulfill({ status: 422, json: { error: 'Unavailable in this controlled illustration test.' } });
  });
}

test('no selected matchup produces no unrelated tactical scene', async ({ page }) => {
  await setup(page, 'soccer');
  await page.goto('/match-analysis');
  await expect(page.getByText('No match selected', { exact: true })).toBeVisible();
  await expect(page.locator('.matchup-stage')).toHaveCount(0);
});
