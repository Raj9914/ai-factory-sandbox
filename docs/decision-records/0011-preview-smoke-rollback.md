# ADR 0011 — Preview Deployment, Smoke Tests and Rollback
- Status: Proposed (needs owner approval)
- Date: 2026-11-11

## Context
`deploy.yml` only echoes; `staging` has no protection rules or branch policy; production requires the owner's approval. No preview, smoke test or rollback exists.

## Decision proposal
- **Hosting default per product type**: static/web UI → Cloudflare Pages (per-PR preview URLs built in); container API → Google Cloud Run (revision per deploy, traffic rollback to previous revision). Firebase Hosting preview channels are an alternative for Firebase-based products. Chosen per product in its PRD; credentials via OIDC/short-lived tokens where the provider supports it, else an Environment secret.
- **Preview**: every PR after CI; no production data or secrets; URL posted to the PR.
- **Smoke test**: a script in the product repo (`scripts/smoke.sh <base-url>`) run as the final deploy step; checks `/health` and the core route; deploy job fails if smoke fails.
- **Rollback**: record each deployment (GitHub Deployments API); `rollback` workflow redeploys the last successful SHA (or shifts Cloud Run traffic); production rollback also needs human approval.
- **Staging**: restrict to protected branches.

## Trade-offs
- Two hosting stacks to template; acceptable because experiments are either web UI or API, and both have free tiers.
- Preview deploys from fork PRs are disabled (public repos) to avoid secret exposure.
