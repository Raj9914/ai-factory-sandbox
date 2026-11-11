---
name: independent-pr-review
description: Review a Ship48 PR for correctness, regressions, security, testing and design without editing code.
---
# Independent PR Review
Act read-only and independently. Read issue, PRD, criteria, full diff and relevant context.

Review correctness/edge cases/regressions; auth/input validation/secrets/privacy/dependencies; concurrency/data integrity/retries; test quality; responsive/accessibility; approved Figma comparison; migration/deploy/rollback.

Each finding: severity P0–P3, file/line, concrete failure, impact and minimal fix. Do not invent findings or call subjective preferences blockers.

Verdict: APPROVE only with no material blocker and criteria satisfied; REQUEST_CHANGES for material correctness/security/acceptance gaps; COMMENT for non-blocking suggestions or missing evidence. State test evidence and uncertainty. Do not edit files.
