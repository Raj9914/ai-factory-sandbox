# Runbook — Incident Response and Kill Switch

Owner: agent:human. Any agent that detects a trigger stops its own work and reports; only the human executes steps marked **(human)**.

## Triggers
- Suspected secret, token or PII in a commit, issue, PR, log or artifact.
- An agent pushed to a default branch, merged a PR, deployed production, or touched a repository outside its task.
- Unexpected spend, or a cap in the cost log reached.
- Repeated identical failure loop (same error twice).
- Prompt-injection evidence (agent followed instructions found in issue/web/file content).
- Production outage or data loss.

## Kill switch (stop all automated work), in order
1. **(human)** Stop the orchestrator service, if running for this repo: `systemctl --user stop ai-controller.service`. Verify: `systemctl --user is-active ai-controller.service` prints `inactive`.
2. **(human)** Disable automation workflows: `gh workflow disable <name> -R <owner/repo>` for each of `ship48-queue-heartbeat`, `issue-owner-label` (when present), any dispatch workflow. Verify: `gh workflow list -R <owner/repo> --all` shows `disabled_manually`.
3. **(human)** Remove `status:ready` from open issues so nothing is picked up: `gh issue list -R <owner/repo> --label status:ready --json number --jq '.[].number' | xargs -I{} gh issue edit {} -R <owner/repo> --remove-label status:ready --add-label status:blocked`.
4. **(human)** Revoke or suspend agent credentials: GitHub App installation suspend, rotate provider API keys, revoke personal tokens used by agents.
5. Interactive agents (ChatGPT, Claude Code, Codex): end the session; do not start new ones until the incident is closed.

## Secret exposure
1. **(human)** Revoke/rotate the secret at the provider first (assume it is compromised; public repos are scraped within minutes).
2. Remove it from the current tree in a PR. History rewrite only with explicit human decision; rotation is mandatory regardless.
3. Check GitHub secret-scanning alerts: `gh api repos/<owner/repo>/secret-scanning/alerts`.
4. Record the incident (below).

## Bad merge or deploy
Follow `DEPLOYMENT_AND_ROLLBACK.md` → Rollback. Revert merges with a revert PR (`git revert -m 1 <sha>` on a branch), never by force-push.

## Evidence to preserve
Workflow run IDs and logs, orchestrator run records and logs, issue/PR timeline, the exact agent prompt/session if available. Do not delete issues or runs.

## Incident record (add as an issue labelled `type:bug`, `status:blocked`; keep confidential detail out of public repos)
- Detected at (UTC), by whom, trigger
- Impact (repos, environments, users, spend)
- Containment steps and times
- Root cause (verified, or "unknown")
- Follow-up issues
- Re-enable decision (human, time)

## Re-enable checklist
- [ ] Root cause understood or contained.
- [ ] Credentials rotated where relevant.
- [ ] Workflows re-enabled one by one; heartbeat run manually and summary checked.
- [ ] One low-risk task run end-to-end under observation before normal operation.
