# ADR 0009 — Separate Agent Identities with Least Privilege
- Status: Proposed (needs owner approval)
- Date: 2026-11-11

## Context (verified 2026-11-11)
The only collaborator is the owner account (admin). Agents act as the owner, so GitHub cannot distinguish an agent from the human. `main` requires a PR and the `test` check with admins enforced, but `required_approving_review_count` is 0 and `allow_auto_merge` is true, so an agent can merge its own PR. Production environment requires the owner's approval.

## Options
1. **GitHub App per automated role** (orchestrator/implementer, reviewer), installed only on target repos, short-lived installation tokens.
2. Machine-user bot accounts with fine-grained PATs.
3. Keep the owner identity; rely on policy.

## Recommendation: option 1 (option 2 as fallback if Apps are impractical for a CLI worker)
- Installation tokens expire in 1 hour; permissions are per-repo and auditable; Apps cannot be admins.
- Then require 1 approving review on `main`, keep `enforce_admins`, and enable `prevent_self_review` on production.
- ChatGPT keeps the human-authorised connector, read-only where possible.
- Human remains the only merger and production approver.

## Trade-offs
- Setup and key custody effort; private key must live outside the repo (OS keychain or secret store).
- Requiring approvals also blocks the owner from merging their own PRs unless another identity approves → the reviewer App's approval or a ruleset bypass for the owner must be designed explicitly.

## Interim (until done)
Disable repository auto-merge or document that only the human enables it; keep the `deploy-approved`/production gates human-only.
