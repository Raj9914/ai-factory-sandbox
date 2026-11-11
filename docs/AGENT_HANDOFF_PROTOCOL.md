# Agent Handoff Protocol

A task states objective, one owner, status, PRD/design links, constraints, acceptance criteria, verification commands, deliverables and next owner.

Labels: agent:chatgpt, agent:claude, agent:codex, agent:human; status:ready, status:blocked; area:design, area:frontend, area:backend, area:testing.

Before claim: verify owner, ready status, no active run, branch and criteria. Record worker/run ID, branch, planned scope and start time. A stale claim is flagged, not stolen.

Completion report: READY_FOR_REVIEW/BLOCKED/DONE; summary; files; tests actually run/results; Figma/preview/PR evidence; known gaps; actual cost/time if known; next action and owner.

Use per-issue concurrency locks. Reviews are read-only. No two workers write the same branch.
