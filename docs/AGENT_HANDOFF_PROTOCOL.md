# Agent Handoff Protocol

A task states objective, one owner, status, PRD/design links, constraints, acceptance criteria, verification commands, deliverables and next owner.

Labels (created in the repo; see ADR 0006):
- Owner, exactly one: agent:chatgpt, agent:claude, agent:codex, agent:human. The task form's Owner field is turned into this label automatically (.github/workflows/issue-owner-label.yml) for owner/member/collaborator authors.
- Status, exactly one: status:proposed → status:approved → status:ready → status:claimed → status:in_progress → status:review → status:accepted → status:done; also status:blocked, status:cancelled, status:killed. Only a human moves a task to status:ready.
- Type: type:idea, type:bug, type:task. Area: area:design, area:frontend, area:backend, area:testing.
- Legacy ai:* and ai-factory labels belong to ai-work-orchestrator; Ship48 does not read them.

Before claim: verify owner, ready status, no active run, branch and criteria. Record worker/run ID, branch, planned scope and start time. A stale claim is flagged, not stolen.

Interactive claims (ChatGPT, Codex, human, manual Claude; ADR 0010) use issue-comment commands handled by `.github/workflows/claim.yml` (logic in `scripts/claim_logic.py`). The orchestrator does not use them; its lease mirrors the labels itself.
- Post a comment whose first line is exactly `/claim` or `/release` (anything after the first line is ignored). Only OWNER/MEMBER/COLLABORATOR comments on issues (not PRs) are acted on; others are silently ignored.
- `/claim` succeeds only if the issue has `status:ready`, no `status:claimed` or `status:in_progress`, and exactly one `agent:*` label. It swaps `status:ready` → `status:claimed` and posts a claim record: actor, run ID, UTC timestamp and expected branch `task/<issue>-<slug>`.
- `/release` moves a `status:claimed` or `status:in_progress` issue back to `status:ready` and comments. On a `status:ready` issue it is a no-op.
- A rejected command changes no labels and posts the reason.
- Runs are serialized per issue (`concurrency: claim-<issue>`, never cancelled) and re-read live labels, so of two racing `/claim` comments exactly one wins. GitHub keeps only one pending run per group, so a third command sent while one is running and one is queued replaces the queued one; re-post it if it got no reply.
- Labels are advisory until enforcement lands (#11). Disable with `gh workflow disable claim.yml`.

Completion report: READY_FOR_REVIEW/BLOCKED/DONE; summary; files; tests actually run/results; Figma/preview/PR evidence; known gaps; actual cost/time if known; next action and owner.

Use per-issue concurrency locks. Reviews are read-only. No two workers write the same branch.
