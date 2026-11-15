# Agent Handoff Protocol

A task states objective, one owner, status, PRD/design links, constraints, acceptance criteria, verification commands, deliverables and next owner.

Labels (created in the repo; see ADR 0006):
- Owner, exactly one: agent:chatgpt, agent:claude, agent:codex, agent:human. The task form's Owner field is turned into this label automatically (.github/workflows/issue-owner-label.yml) for owner/member/collaborator authors.
- Status, exactly one: status:proposed → status:approved → status:ready → status:claimed → status:in_progress → status:review → status:accepted → status:done; also status:blocked, status:cancelled, status:killed. Only a human moves a task to status:ready.
- Type: type:idea, type:bug, type:task. Area: area:design, area:frontend, area:backend, area:testing.
- Legacy ai:* and ai-factory labels belong to ai-work-orchestrator; Ship48 does not read them.

Before claim: verify owner, ready status, no active run, branch and criteria. Record worker/run ID, branch, planned scope and start time. A stale claim is flagged, not stolen.

Completion report: READY_FOR_REVIEW/BLOCKED/DONE; summary; files; tests actually run/results; Figma/preview/PR evidence; known gaps; actual cost/time if known; next action and owner.

Use per-issue concurrency locks. Reviews are read-only. No two workers write the same branch.
