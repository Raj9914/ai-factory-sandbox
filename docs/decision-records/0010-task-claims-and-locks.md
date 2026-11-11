# ADR 0010 — Task Claim, Lock, Release and Recovery
- Status: Proposed (needs owner approval)
- Date: 2026-11-11

## Context
Docs require per-issue locks and claim records (`docs/AGENT_HANDOFF_PROTOCOL.md`), but nothing implements them. `status:claimed` exists as a label only. The orchestrator implements atomic leases in its own database for tasks it runs.

## Decision proposal
Two claim paths, one visible state on the issue:
1. **Automated (orchestrator)**: the orchestrator lease is authoritative; it mirrors `status:claimed` → `status:in_progress` → `status:review` and posts a claim comment (worker, run ID, branch, start, lease expiry).
2. **Interactive (ChatGPT, Codex, human, manual Claude)**: claim via a GitHub workflow triggered by a `/claim` comment from a trusted author, serialized per issue with `concurrency: claim-<issue>`; succeeds only if the issue is `status:ready`, the commenter's agent label matches, and no `status:claimed`/`status:in_progress` label exists. `/release` returns it to `status:ready`.
- Lock = the label + claim comment; the concurrency group prevents two claims racing.
- Stale claim: no issue/PR activity for 4 h (pilot) → heartbeat flags it and adds `status:blocked` + comment. **Never auto-reassign**; a human releases.
- Recovery: orchestrator releases leases of dead processes on restart (already implemented); interactive claims are released by `/release` or a human.

## Trade-offs
- Label-based locks are advisory for anyone with write access; real enforcement needs separate identities (ADR 0009).
- Comment commands add a small workflow surface; the body is never executed and authors are restricted to OWNER/MEMBER/COLLABORATOR.
