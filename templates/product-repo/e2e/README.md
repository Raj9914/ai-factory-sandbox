# End-to-end test of the core journey (Playwright)

`journey.spec.ts` walks the primary journey (see `docs/templates/USER_JOURNEY_TEMPLATE.md`)
in Chromium. It is an example: replace its steps with the product's activation path.

| File | Purpose |
|---|---|
| `../playwright.config.ts` | 30 s per test, 1 retry, 2 min for the whole run, trace kept on failure |
| `journey.spec.ts` | the core-journey example spec |
| `serve.mjs` | zero-dependency static server for local runs (serves `dist/`, else `site/`) |
| `site/index.html` | example template page, used until `make build` writes `dist/index.html` |

## Run locally

```bash
npm install --no-save --no-package-lock "@playwright/test@1.55.0"   # same pin as preview.yml
npx playwright install chromium
make build
npx playwright test                                   # local build via e2e/serve.mjs
E2E_BASE_URL=https://pr-12.<project>.pages.dev npx playwright test   # a deployed preview
```

Once the product has a Node lockfile, add `@playwright/test` (exact version) to its
devDependencies and drop the `npm install` line from the `e2e` job in
`.github/workflows/preview.yml`.

## In CI

The `e2e` job in `preview.yml` runs after the `preview` job and tests the preview URL of this
commit. When the preview job is skipped (fork PRs, or no Pages project yet) it tests the local
build. On failure it uploads `test-results/` (Playwright traces) and `playwright-report/` as
an artifact; open a trace with `npx playwright show-trace <trace.zip>`.
The job is not a required check until a human adds `e2e` to branch protection. Once it is
required, a failing journey blocks merge.

No credentials, tokens or real personal data in tests. Previews hold no production data.
Visual diffs are out of scope.
