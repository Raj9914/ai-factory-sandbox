---
name: implement-task
description: Implement one explicitly assigned Ship48 engineering task on an isolated branch and hand off a reviewable PR.
---
# Implement One Task
Work only on a task explicitly assigned to Claude/current worker. Never take another agent's or human's task. Stop if owner/status is unclear or another run claimed it.

Before: read CLAUDE.md, AGENTS.md, PRD/design and issue comments; confirm ready/unblocked; inspect code; state plan; use isolated branch.

During: stay in scope; preserve behaviour; add tests; never commit secrets/PII, weaken checks, perform destructive work, change approved Figma design without ownership, merge your PR or deploy production.

Before handoff: run relevant format/lint/type/test/build/security commands; inspect diff for unrelated changes and secrets.

Report issue, branch, summary, files, exact test results, gaps, PR, actual cost/time if available, next owner/action. Update issue and stop for review.
