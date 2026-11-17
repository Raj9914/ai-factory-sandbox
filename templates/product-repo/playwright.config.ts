// Playwright config for the core-journey end-to-end test (e2e/journey.spec.ts).
//
//   E2E_BASE_URL set   -> test that URL (the preview workflow passes the per-PR preview URL)
//   E2E_BASE_URL unset -> start e2e/serve.mjs and test the local build (dist/, or the example
//                         page in e2e/site/ until the product builds one)
//
// Run: npx playwright test     (install first: see e2e/README.md)
// Each test has 30 s, failures retry once, and the whole run stops at 2 minutes.
// A failing test keeps its trace in test-results/ (uploaded by the preview workflow).
import { defineConfig, devices } from '@playwright/test';

const baseURL = (process.env.E2E_BASE_URL || '').trim().replace(/\/+$/, '');
const port = Number(process.env.E2E_PORT || 4173);
const localURL = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  retries: 1,
  globalTimeout: 120_000,
  forbidOnly: !!process.env.CI,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: baseURL || localURL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: baseURL
    ? undefined
    : {
        command: 'node e2e/serve.mjs',
        url: `${localURL}/`,
        env: { E2E_PORT: String(port) },
        reuseExistingServer: !process.env.CI,
        timeout: 30_000,
      },
});
