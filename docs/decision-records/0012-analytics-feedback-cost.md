# ADR 0012 — Analytics, User Feedback and Cost Tracking
- Status: Proposed (needs owner approval)
- Date: 2026-11-11

## Context
No analytics provider, event schema, feedback channel or cost reporting exists. The orchestrator records `cost_usd` per model run in its database but does not report it per ticket. Hour-48 verdicts need actual numerators/denominators and spend.

## Decision proposal
- **Analytics**: PostHog (cloud, EU or US region chosen per product) with anonymous IDs and autocapture off; events defined first in `docs/templates/ANALYTICS_SPEC_TEMPLATE.md`. Alternative: Plausible custom events for web-only experiments needing no user-level funnels.
- **Feedback**: an in-product link to a short form whose submissions land as issues in the private product repo via a `feedback.yml` issue form, or provider survey; no PII fields.
- **Cost**: manual `COST_LOG_TEMPLATE.md` now; orchestrator posts per-ticket model cost/duration on the issue (from its run records); provider hard limits set by the human before unattended runs; automated provider-billing ingestion is P3.
- **Decisions**: hour-24 KEEP_SCOPE/CUT_SCOPE and hour-48 BUILD/PIVOT/KILL/INCONCLUSIVE records live in the product repo under `experiments/<experiment-id>/`.

## Trade-offs
- A third-party analytics processor receives event data → no PII, documented retention.
- Manual cost logging is error-prone; acceptable for the first experiment, replaced by the orchestrator report.
