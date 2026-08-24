import { expect, test } from '@playwright/test';

test('authenticated voice-platform vertical slice', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'TradeVoice Operations' })).toBeVisible();
  await page.getByRole('button', { name: 'Open demo workspace' }).click();
  await expect(page.getByRole('heading', { name: 'Voice control plane' })).toBeVisible();
  await expect(page.getByText('Trade Support Agent')).toBeVisible();

  await page.getByRole('button', { name: 'Simulate call' }).click();
  await expect(page.getByText(/Buyer requested a .*basmati rice supplier/)).toBeVisible();

  await page.getByRole('button', { name: /Eastern Grain Trading/ }).click();
  await page.getByRole('button', { name: 'Talk to Support' }).click();
  await page.getByRole('button', { name: 'Start Conversation' }).click();
  await expect(page.getByText('Ready', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'I need 50 tonnes of basmati rice for Dubai.' }).click();
  await expect(page.getByText(/Please confirm: create an enquiry/)).toBeVisible();
  await page.getByLabel('Fallback message').fill('confirm');
  await page.getByRole('button', { name: 'Send' }).click();
  await expect(page.getByText(/enquiry ID is ENQ-1004/)).toBeVisible();
  await page.getByRole('button', { name: 'Close support panel' }).click();
  await expect(page.getByText('ENQ-1004')).toBeVisible();
});
