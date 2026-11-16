# Runbook — Deployment and Rollback

Applies to product repositories (ADR 0008). Hosting is chosen per product (ADR 0011). In this factory sandbox `deploy.yml` only echoes and deploys nothing.

## Environments
| Environment | Trigger | Gate | Who approves |
|---|---|---|---|
| preview | each PR after CI passes | CI `test` green | none (no production data or secrets) |
| staging/beta | `workflow_dispatch` from `main` | GitHub Environment `staging` restricted to protected branches | none, or human per product |
| production | `workflow_dispatch` from `main` | GitHub Environment `production` with required reviewer | agent:human only |

Agents may trigger preview and staging. Agents never approve production.

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
1. Confirm the commit is on `main` and required checks passed: `gh api repos/<o>/<r>/commits/<sha>/check-runs --jq '.check_runs[]|"\(.name) \(.conclusion)"'` → all `success`.
2. Record the current known-good SHA: `gh api repos/<o>/<r>/deployments -f environment=<env> --method GET --jq '.[0].sha'`.
3. Dispatch: `gh workflow run deploy.yml -R <o>/<r> -f environment=<env>`.
4. Production only: human approves in Actions → run → Review deployments.
5. Watch: `gh run watch <run-id> -R <o>/<r> --exit-status` → exit 0.
6. Smoke test (the deploy job runs it; repeat manually if needed): `curl -fsS <url>/health` → `{"status":"ok"}` and the core route returns 200.
7. Record environment, SHA, run ID, URL and smoke result in the PR or experiment log.

## Rollback
Trigger: smoke test fails, error rate spike, P0 defect, or human decision.
1. Find the previous successful deployment SHA for the environment (step 2 above, or the deployment record).
2. Redeploy that SHA: `gh workflow run deploy.yml -R <o>/<r> --ref <known-good-sha-or-tag> -f environment=<env>` (production still needs human approval; a protected-branch policy may require a tag or revert PR instead — then use step 3).
3. Or revert: branch from `main`, `git revert <bad-sha>` (`-m 1` for merge commits), open PR, merge after CI, deploy normally.
4. Verify smoke test passes on the rolled-back version.
5. Data: if the bad version ran a migration, follow the migration's documented down-step. Migrations without a documented rollback are not allowed in experiments.
6. Open a `type:bug` issue with timeline and evidence.

## Never
- Deploy a commit that has not passed required checks.
- Put provider credentials in frontend bundles or PR jobs.
- Approve your own production deployment as an agent.
