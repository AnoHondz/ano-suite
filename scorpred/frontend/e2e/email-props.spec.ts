import { test, expect, type Page } from '@playwright/test';

async function setup(page: Page, ready = true) {
  await page.clock.install({ time: new Date('2026-10-25T12:00:00Z') });
  const posts: unknown[] = [];
  await page.route('**/api/**', route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/session') return route.fulfill({ json: { user: { email: 'owner@example.com' }, csrf_token: 'csrf-test' } });
    if (url.pathname === '/api/props/email') {
      if (route.request().method() === 'POST') {
        posts.push(route.request().postDataJSON());
        expect(route.request().headers()['x-csrf-token']).toBe('csrf-test');
        return route.fulfill({ status: 202, json: { job: { id: 'a'.repeat(64), state: 'collecting', message: 'Collecting props.' } } });
      }
      return route.fulfill({ json: { ready, recipient: 'owner@example.com', job: url.searchParams.has('id')
        ? { id: 'a'.repeat(64), state: 'sent', pick_count: 8, message: 'Accepted by mail server.' } : null } });
    }
    if (url.pathname === '/api/props/best') return route.fulfill({ json: { result: { date: '2026-10-25', overall: [],
      by_sport: { soccer: [], nba: [], nfl: [] }, notes: [], slates: Object.fromEntries(['soccer', 'nba', 'nfl'].map(s =>
        [s, { date: '2026-10-25', games: 0, with_props: 0, unavailable: [] }])) } } });
    return route.fulfill({ json: { slate: [], topOpportunities: [], plan: { bet: 0, consider: 0, skip: 0 },
      availableDates: ['2026-10-25'], selectedDate: '2026-10-25' } });
  });
  return posts;
}

test('emails selected sport asynchronously and disables duplicate send after confirmation', async ({ page }) => {
  const posts = await setup(page);
  await page.goto('/?date=2026-10-25');
  const region = page.getByRole('region', { name: 'Best player props' });
  await region.getByRole('tab', { name: 'NFL', exact: true }).click();
  await region.getByRole('button', { name: 'Email these props' }).click();
  await expect(region.getByRole('status')).toContainText('Collecting props.');
  await expect(region.getByRole('button', { name: 'Collecting props…' })).toBeDisabled();
  await page.clock.fastForward(6000);
  await expect(region.getByRole('status')).toContainText('Sent 8 props to your mail server.');
  await expect(region.getByRole('button', { name: 'Email requested' })).toBeDisabled();
  expect(posts).toEqual([{ date: '2026-10-25', sports: ['nfl'] }]);
});

test('missing mail configuration shows setup status and prevents send', async ({ page }) => {
  const posts = await setup(page, false);
  await page.goto('/?date=2026-10-25');
  await expect(page.getByText('Email setup required:', { exact: false })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Email these props' })).toBeDisabled();
  expect(posts).toHaveLength(0);
});
