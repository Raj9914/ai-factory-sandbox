# Ship48 AI Factory — PRD

- Status: Draft v1
- Product: Internal AI-assisted app/game experiment factory
- Users: Human owner, ChatGPT, Claude Code, optional Codex
- Promise: Controlled beta and evidence-based decision within 48 hours

## Problem
Ideas stall between research, product definition, design, implementation, QA and launch. Multiple agents create duplicated work, unclear ownership, weak verification and uncontrolled spend.

## Vision
An auditable factory taking one approved idea through discovery, editable design, isolated implementation, independent review, beta exposure and BUILD/PIVOT/KILL/INCONCLUSIVE decision.

## Jobs
Human: direction, scope/budget/production approvals, final decision.
ChatGPT: research, scoring, PRD/screens, Figma where connected, independent review, evidence analysis.
Claude: assigned implementation, tests, fixes, PR and preview.
Codex: optional explicitly assigned worker/reviewer.

## Workflow
Idea → score → PRD → flow/screens → editable Figma → owner-assigned GitHub tasks → isolated build → CI/security → review → preview/beta → real-user evidence → hour-24/hour-48 decision.

## MVP must-have
GitHub control plane; CLAUDE.md/AGENTS.md; canonical skills and mirrors; task ownership/handoff; Figma-first design; queue heartbeat; run reports; CI/security/review; human production gates; experiment metrics/cost control.

## Out of scope
Multi-product autonomy; autonomous production release; custom orchestration SaaS; Jira sync; automatic ChatGPT wake-up from comments; Kubernetes/Kafka/Redis; payments/auth/social unless essential.

## Requirements
One persona and core journey; usually 3–5 app screens or one playable game loop; one owner/branch per task; tests and reviewable PR; preview after CI; human production approval; every run reports evidence; define activation before launch; record 24h/48h decisions.

## Metrics
Factory: task-to-PR time, first-pass CI, PR acceptance, collision/recovery rate, cost/run.
Product: time to beta, target-user exposure, activation/denominator, repeat/replay, crash-free sessions, feedback, unit cost.

## Pilot defaults
One active build/product; max 3 repair attempts; 30-min task timeout; suggested USD 5/task and USD 25/experiment planning caps. Internal hypotheses only; owner must configure provider/cloud limits.

## Dependencies
GitHub; supported Claude auth/API for unattended workers; Figma MCP; ChatGPT skills/plugin eligibility or manual playbook; hosting/analytics; protected environment and human approver.
