# ADR 0008 — One Private Repository per Product
- Status: Proposed (needs owner approval)
- Date: 2026-11-11

## Context
`skills/product-prd` writes "docs/PRD.md in the product repository", but no product-repository model exists. This factory repo is public (ADR 0005) and must not hold confidential plans or user data.

## Options
1. **Separate private repository per product**, created from a factory template by a provisioning script.
2. `products/<slug>/` inside the factory repository.

## Recommendation: option 1
- Product plans, analytics and feedback can be confidential; the factory repo is public.
- Per-repo branch protection, environments, secrets and deploy targets; a KILL verdict archives one repo cleanly.
- The orchestrator already models one manifest per repository and refuses two manifests on one repo.

## Trade-offs
- Template drift between products → mitigate with a versioned template and a provisioning script that applies labels/protection/environments idempotently.
- Cross-product reporting needs aggregation (acceptable at one active product).
- Private repos on a free plan have limits on environment protection rules; verify plan features before provisioning (required reviewers on private repos may require a paid plan).

## Consequences
Factory repo holds templates, skills, scripts and generic docs only. Product repos hold PRD, design records, experiment logs and code.
