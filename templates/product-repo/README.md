# &lt;Product&gt;

> Ship48 product repository. Created from the factory `templates/product-repo` template (version in `TEMPLATE_VERSION`).

Replace `<Product>` and the placeholder text below when the repository is provisioned. Keep this repository **private**: it holds the PRD, research, experiment logs and code for one product.

## Where everything lives
| Path | Contents |
|---|---|
| `docs/PRD.md` | Product requirements (start from the template already in the file) |
| `docs/research/` | Market, competitor and user research |
| `docs/templates/` | Journey, analytics, task, cost-log and hour-24/48 templates |
| `experiments/<id>/` | One folder per 48-hour experiment (see `experiments/README.md`) |
| `Makefile` | Stack-agnostic command contract used by CI, agents and humans |
| `dist/` | Build output (`make build`); not committed |
| `.github/` | CI, issue forms and PR template |
| `.ai-factory.yaml` | Verification commands the factory runs before review |
| `AGENTS.md`, `CLAUDE.md` | Agent rules |

## Command contract
```bash
make install              # install dependencies from the lockfile
make test                 # run all tests; CI runs this
make build                # build into dist/
make smoke URL=<base-url> # check a deployed preview or beta URL responds
```
Every stack (web, mobile, game, API) implements these four targets. Nothing else may be assumed by CI or agents.

## Preview deployments
`.github/workflows/preview.yml` gives every pull request its own Cloudflare Pages preview (ADR 0011). On each push to a PR branch it:
1. checks out the PR head commit and runs `make install`, `make test` and `make build`;
2. deploys `dist/` with Wrangler (version pinned in `WRANGLER_VERSION`) to the Pages branch `pr-<number>`, i.e. `https://pr-<number>.<project>.pages.dev`;
3. runs `make smoke URL=<preview-url>` against that exact deployment;
4. records a GitHub Deployment (environment `preview`, PR head SHA) with the result;
5. creates or updates one PR comment with the URL, SHA and `smoke: pass` / `smoke: fail`.

Setup (once per product, by a human):
- Create the Cloudflare Pages project (direct upload) and set the repository **variable** `CLOUDFLARE_PAGES_PROJECT` to its name. While the variable is unset the job is skipped, so API-only products are unaffected.
- Create the GitHub Environment `preview` and add the **environment secrets** `CLOUDFLARE_API_TOKEN` (scoped to *Cloudflare Pages: Edit* for this account only) and `CLOUDFLARE_ACCOUNT_ID`. Never use production data or production credentials in previews.

Security: the workflow uses `pull_request` (never `pull_request_target`) and runs only when the PR branch is in this repository, so fork PRs skip the job and never reach the `preview` secrets. Permissions are limited to `contents: read`, `deployments: write` and `pull-requests: write`; the Cloudflare secrets are passed only to the deploy step.

Rollback/clean-up: delete or revert the workflow to stop previews; Pages preview deployments can be deleted in the Cloudflare dashboard.

## Rules
- No secrets, tokens or personal data in the repository, issues or PRs.
- Tasks start as `status:proposed`; only a human applies `status:ready`, merges PRs and approves production.
- One task = one owner = one branch = one PR.
