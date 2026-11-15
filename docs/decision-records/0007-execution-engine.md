# ADR 0007 — Execution Engine: Reuse ai-work-orchestrator
- Status: Proposed (needs owner approval; see the linked decision issue)
- Date: 2026-11-11

## Context (verified 2026-11-11)
Ship48 has no execution plane: the heartbeat only reports the queue (`.github/workflows/factory-heartbeat.yml`, read-only permissions, no model call). The owner's existing ai-work-orchestrator (private repository) already ran this sandbox end to end on 2026-10-02 (issues #1/#3, PRs #2/#4): GitHub issue → spec → Claude in Docker → verify → PR → Codex review → merge → staging. Its test suite passes (235 tests on 2026-11-11). Capabilities confirmed in code: SQLite ticket leases with expiry and crash recovery, Docker sandbox (non-root, read-only rootfs, dropped caps), controller-owned verify commands, independent reviewer (implementer never reviews itself), protected paths → high risk + 2 reviews, attempt and review-cycle caps, PR-only mode, production never automatic, per-run `cost_usd` recording.
Gaps: it reads `ai-factory` + `ai:*` labels, not Ship48 `status:*`/`agent:*`; acts as the owner's GitHub identity; no ChatGPT or Figma integration; sandbox network egress unrestricted; GitHub Projects sync and webhook wake-up are open orchestrator issues.

## Options
1. **Reuse the orchestrator** and add a Ship48 label adapter and a Ship48 project manifest.
2. Build a new Ship48 engine in GitHub Actions (self-hosted or hosted runners invoking Claude/Codex APIs).
3. Stay manual (human drives every agent).

## Recommendation: option 1
- Proven safety controls already exist and are tested; rebuilding them is weeks of work with new risk.
- Ship48 keeps GitHub as the ledger (ADR 0001); the orchestrator already uses GitHub issues/PRs as its interface.
- ChatGPT stays interactive (ADR 0004); only Claude/Codex stages are automated.

## Trade-offs
- Runs on one owner machine (single point of failure, no HA). Acceptable for one active product (PRD pilot default).
- Two label vocabularies until the adapter lands; the adapter must make Ship48 labels authoritative for Ship48 repos.
- Orchestrator repo is private; Ship48 public docs reference capabilities only.

## Consequences
New work: label adapter, Ship48 manifests (PR-only for product repos), bot identity, egress allowlist. Option 2 is reconsidered only if the factory must run without the owner machine.
