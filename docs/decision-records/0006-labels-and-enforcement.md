# ADR 0006 — Labels and What Is Actually Enforced
- Status: Accepted
- Date: 2026-11-11

Labels: Ship48 uses agent:*, status:*, type:* and area:* (see AGENT_HANDOFF_PROTOCOL.md). The ai:* and ai-factory labels come from ai-work-orchestrator, which used this repo for live tests and no longer targets it. They are kept so old issues and PRs stay readable. Ship48 workflows ignore them.

Enforced by GitHub:
- main requires a pull request and the `test` check, for admins too. Direct pushes and force pushes are rejected.
- Deploys to the `production` environment wait for the owner's approval. `staging` is ungated.
- Workflows have read-only default permissions, and actions are pinned to commit SHAs.

Policy only, not enforced:
- "Do not merge your own PR" and "the reviewer is independent". Agents act under the owner's GitHub identity, so GitHub cannot tell an agent from the owner. Required approvals would block the owner too, because GitHub does not let you approve your own PR.
- To enforce these rules, give agents their own identity (a GitHub App or bot account) with write access, then require 1 approving review and turn on prevent_self_review for production.
