# Runbook — Deployment and Rollback

Applies to product repositories (ADR 0008). Hosting is chosen per product (ADR 0011). Product repositories get `preview.yml`, `deploy.yml` and `rollback.yml` from `templates/product-repo/.github/workflows/`. The factory sandbox's own `.github/workflows/deploy.yml` only echoes and deploys nothing.

## Environments
| Environment | Trigger | Gate | Who approves |
|---|---|---|---|
| preview | each PR (`preview.yml`) | `make test` in the job | none (no production data or secrets) |
| staging/beta | push to `main` (`deploy.yml`); rollback via `rollback.yml` | GitHub Environment `staging` restricted to protected branches | none, or human per product |
| production | `workflow_dispatch` of `deploy.yml` or `rollback.yml` from `main` | GitHub Environment `production` with required reviewer | agent:human only |

Agents may trigger preview, staging and rollbacks. Agents never approve production deploys or production rollbacks.

## Provisioning a product repository
`scripts/provision_product.py` creates a private product repository from `templates/product-repo` and applies every per-product setting. It changes nothing unless told to.

1. Dry run (default, read calls only): `python scripts/provision_product.py --slug <slug> [--owner <user-or-org>]` prints every write call it would make and ends with `DRY RUN: 0 changes applied (N planned ...)`. Without a working `gh` login it plans as if the repository does not exist yet.
2. Apply (human only, with their own `gh` login; the script never reads or stores a token): `python scripts/provision_product.py --slug <slug> --apply --confirm <slug>`. It ends with `APPLIED: N changes applied` and prints an orchestrator manifest stub (PR-only delivery); `--manifest-out <file>` also writes it to a file.
3. Re-run the apply on the same slug: it must report `APPLIED: 0 changes applied` and `drift: none`. On an existing repository every planned change is listed as drift and repaired by `--apply`.

What it applies, in this order:
- Repository: private (`--visibility public` is refused unless `--allow-public`), auto-merge off, delete branch on merge on.
- Template: one initial commit with all template files, only into a repository that holds nothing but the auto-init README. Template differences in a provisioned repository are reported as drift and never overwritten.
- Labels: the factory set (`LABELS` in the script, 40 labels). Missing labels are created and changed ones updated; extra labels are reported, never deleted.
- `main` protection: pull request required, `test` check required, enforce_admins, no force pushes or deletions, `--required-approvals` approvals (default 0, because GitHub does not let the owner approve their own PR; see ADR 0006).
- Environments: `preview` (no gate), `staging` (protected branches only), `production` (required reviewer, `--reviewer` or the owner, plus protected branches only).

Notes: seeding `.github/workflows/` needs the `workflow` token scope. Branch protection and required reviewers on private repositories may need a paid plan (ADR 0008); the script then stops with the API error, and re-running after the fix continues where it stopped. Rollback: a human archives the created repository.

## Deploy (staging or production)
Product repositories use `.github/workflows/deploy.yml` from the template (Cloudflare Pages, ADR 0011):
- **Staging:** every push to `main` deploys that commit to the `staging` environment (Pages branch `staging`; URL `https://staging.<project>.pages.dev` or the `STAGING_URL` variable).
- **Production:** `gh workflow run deploy.yml -R <o>/<r> --ref main -f environment=production` deploys the current `main` commit to the `production` environment (Pages production branch `main` or the `CLOUDFLARE_PRODUCTION_BRANCH` variable; URL `https://<project>.pages.dev` or `PRODUCTION_URL`). GitHub holds the job until a human approves it.

Every run first checks that it was started from `main`. It then checks out exactly the target SHA and runs `make install`, `make test` and `make build`. It creates a GitHub Deployment (environment, SHA, `in_progress`), deploys `dist/` with the pinned Wrangler and runs `make smoke` against the new deployment's unique URL. Finally it sets the Deployment status: `success` if smoke passed, `failure` if it deployed but smoke failed, `error` if it did not deploy. The run summary lists environment, SHA, URL, status and smoke result. Deploys to one environment queue (`concurrency: deploy-<env>`) and are never cancelled half-way. While a deploy runs, GitHub keeps only the newest waiting run.

Setup (once per product, by a human):
- Set the repository variable `CLOUDFLARE_PAGES_PROJECT`. Until it is set, pushes skip the deploy and manual runs fail.
- Add the environment secrets `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` to both `staging` and `production`.
- The environments themselves come from `scripts/provision_product.py`; `production` has a required reviewer.
- Optional variables: `STAGING_URL`, `PRODUCTION_URL`, `CLOUDFLARE_PRODUCTION_BRANCH`.

Steps:
1. Confirm the commit is on `main` and required checks passed: `gh api repos/<o>/<r>/commits/<sha>/check-runs --jq '.check_runs[]|"\(.name) \(.conclusion)"'` → all `success`.
2. Record the current known-good SHA: `gh api repos/<o>/<r>/deployments -f environment=<env> --method GET --jq '.[0].sha'`.
3. Staging deploys on merge. Production: `gh workflow run deploy.yml -R <o>/<r> --ref main -f environment=production`.
4. Production only: a human approves in Actions → run → Review deployments. Agents never approve.
5. Watch: `gh run watch <run-id> -R <o>/<r> --exit-status` → exit 0 (the job fails if smoke fails).
6. Check the record: `gh api repos/<o>/<r>/deployments --jq '[.[]|select(.environment=="<env>")][0].sha'` → the deployed SHA, with latest status `success`. Repeat smoke manually if needed: `make smoke URL=<url>`.
7. Record environment, SHA, run ID, URL and smoke result in the PR or experiment log.

## Rollback
Trigger: smoke test fails, error rate spike, P0 defect, or human decision.

One step: `gh workflow run rollback.yml -R <o>/<r> --ref main -f environment=<staging|production> [-f incident=<issue number>]`.

`.github/workflows/rollback.yml` then runs three jobs:
1. **plan**: `scripts/rollback_target.py` reads the environment's GitHub Deployments, newest first. The newest deployment that ever reached `success` is the current one. The target is the newest earlier successful deployment with a different SHA. Failed, errored and unfinished deployments are ignored. A SHA that was already rolled back from is never chosen again, so a second rollback goes further back. If there is no earlier successful deployment, the run fails and changes nothing.
2. **deploy**: calls `deploy.yml` with the target SHA. The rollback is rebuilt, recorded (Deployment payload `rollback_from=<current SHA>`), deployed and smoke-tested exactly like a normal deploy, in the same environment. A production rollback waits for human approval like any production deploy.
3. **report**: if `incident` is given, comments on that issue with the result (`rolled back, smoke: pass` or the failure), the from/to SHAs, the URL and the run link.

After the run:
1. Watch: `gh run watch <run-id> -R <o>/<r> --exit-status` → exit 0. `gh run list -R <o>/<r> -w rollback.yml -L 1` → `success`.
2. Verify the live SHA: `gh api repos/<o>/<r>/deployments --jq '[.[]|select(.environment=="<env>")][0].sha'` → the known-good SHA.
3. Data: if the bad version ran a migration, follow the migration's documented down-step. These workflows do not handle database migrations; products must declare them. Migrations without a documented rollback are not allowed in experiments.
4. Open (or update) a `type:bug` issue with timeline and evidence, then fix forward with a normal PR.

If the workflow cannot run (Actions or the GitHub API is down), a human rolls back manually in the Cloudflare Pages dashboard: project → Deployments → the last good deployment → Rollback to this deployment. Then record the SHA and time on the incident issue. Alternatively, revert: branch from `main`, `git revert <bad-sha>` (`-m 1` for merge commits), open a PR and merge after CI; the push to `main` redeploys staging.

### Rollback drill (once per product, before the first beta)
1. Merge commit A to `main`; the staging deploy succeeds. Note SHA A.
2. Merge commit B; the staging deploy succeeds and the latest staging deployment is B.
3. Run `gh workflow run rollback.yml -R <o>/<r> --ref main -f environment=staging -f incident=<drill issue>`.
4. Expect all of these, and paste the outputs into the drill issue or PR:
   - `gh run list -R <o>/<r> -w rollback.yml -L 1` → `success`
   - `gh api repos/<o>/<r>/deployments --jq '[.[]|select(.environment=="staging")][0].sha'` → SHA A
   - smoke passed in the run
   - the drill issue has the rollback comment

## Never
- Deploy a commit that has not passed required checks.
- Put provider credentials in frontend bundles or PR jobs.
- Approve your own production deployment or production rollback as an agent.
