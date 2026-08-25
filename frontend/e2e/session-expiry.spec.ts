import { expect, test } from '@playwright/test';

test('an expired token returns the user to sign-in with an explanation', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'TradeVoice Operations' })).toBeVisible();
  await page.getByRole('button', { name: 'Open demo workspace' }).click();
  await expect(page.getByRole('heading', { name: 'Voice control plane' })).toBeVisible();

  // Simulate the access token expiring while the app is open.
  await page.route('**/api/**', (route) =>
    route.fulfill({ status: 401, contentType: 'application/json', body: '{"detail":"Access token is no longer valid"}' }),
  );

  await page.getByRole('button', { name: 'Simulate call' }).click();

  await expect(page.getByRole('heading', { name: 'TradeVoice Operations' })).toBeVisible();
  await expect(page.getByText('Your session expired. Please sign in again.')).toBeVisible();
});
