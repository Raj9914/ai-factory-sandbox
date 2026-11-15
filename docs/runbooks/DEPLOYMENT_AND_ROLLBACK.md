# Runbook — Deployment and Rollback

Applies to product repositories (ADR 0008). Hosting is chosen per product (ADR 0011). In this factory sandbox `deploy.yml` only echoes and deploys nothing.

## Environments
| Environment | Trigger | Gate | Who approves |
|---|---|---|---|
| preview | each PR after CI passes | CI `test` green | none (no production data or secrets) |
| staging/beta | `workflow_dispatch` from `main` | GitHub Environment `staging` restricted to protected branches | none, or human per product |
| production | `workflow_dispatch` from `main` | GitHub Environment `production` with required reviewer | agent:human only |

Agents may trigger preview and staging. Agents never approve production.

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
