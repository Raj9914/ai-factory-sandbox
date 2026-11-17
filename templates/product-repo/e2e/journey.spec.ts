// Example Playwright end-to-end test of the core journey (primary journey in
// docs/templates/USER_JOURNEY_TEMPLATE.md). Runs against the per-PR preview URL in CI
// (E2E_BASE_URL) or against the local build (playwright.config.ts webServer).
//
// Replace the steps with the product's activation path: one test per journey, located by
// role and accessible name. No credentials and no real personal data in tests; previews hold
// no production data.
import { test, expect, type Page } from '@playwright/test';

// Collects uncaught page errors so a journey that "works" while throwing still fails.
function trackPageErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  return errors;
}

test.describe('core journey', () => {
  test('a visitor reaches activation', async ({ page }) => {
    const errors = trackPageErrors(page);
    await page.goto('/');
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();

    await page.getByRole('button', { name: 'Get started' }).click();
    const goal = page.getByLabel('What do you want to try?');
    await expect(goal).toBeVisible();

    await goal.fill('Plan a weekend trip');
    await page.getByRole('button', { name: 'Continue' }).click();

    await expect(page.getByRole('status')).toHaveText("You're all set.");
    expect(errors, 'uncaught page errors').toEqual([]);
  });

  test('an empty goal shows a validation message and stays on the step', async ({ page }) => {
    const errors = trackPageErrors(page);
    await page.goto('/');
    await page.getByRole('button', { name: 'Get started' }).click();
    await page.getByRole('button', { name: 'Continue' }).click();

    await expect(page.getByRole('alert')).toHaveText('Enter a goal to continue.');
    await expect(page.getByLabel('What do you want to try?')).toHaveAttribute('aria-invalid', 'true');
    await expect(page.getByRole('status')).toBeHidden();
    expect(errors, 'uncaught page errors').toEqual([]);
  });
});
