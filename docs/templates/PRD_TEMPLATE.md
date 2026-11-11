# <Product> — PRD

| Field | Value |
|---|---|
| Status | Draft / Approved (approver, date) |
| Experiment ID | `<slug>-<YYYYMMDD>` |
| Product repo | `<owner>/<repo>` (private) |
| Owner | agent:human (approver) / agent:chatgpt (author) |
| Clock start (hour 0) | `<ISO time>` |
| Linked | Experiment brief, market research, journeys, screen spec, analytics spec |

## 1. Problem
Who has which problem, in what situation. Label every statement **Fact** (source, date) / **Assumption** / **Open question**.

## 2. Target user and context
One persona only. Where they are reached (acquisition source).

## 3. Current alternatives and evidence
| Alternative | What users do today | Weakness | Source |
|---|---|---|---|

## 4. Value proposition and hypothesis
We believe `<user>` will `<action>` because `<reason>`. We will know when `<activation event>` reaches `<threshold>` of `<denominator>` within `<window>`.

## 5. Primary journey / game loop
Reference `USER_JOURNEY_TEMPLATE.md`. Number each step; each step maps to a screen ID.

## 6. Scope
**Must have** (each item maps to ≥1 acceptance criterion):
1.

**Out of scope** (explicit): authentication, payments, social, admin UI unless listed as must-have.

## 7. Screens and states
Screen IDs from the screen spec. Each lists loading / empty / error / success / disabled / validation.

## 8. Data, integrations and storage
| Data | Source | Stored where | Retention | PII? |
|---|---|---|---|---|

## 9. Privacy, security and accessibility
PII collected (prefer none), consent, secrets needed (names only, never values), WCAG target, threat notes.

## 10. Analytics
Event list from `ANALYTICS_SPEC_TEMPLATE.md`. Activation event definition repeated here verbatim.

## 11. Non-functional requirements
Performance budget, supported devices/browsers, availability expectation, cost ceiling per day.

## 12. Acceptance criteria (testable)
| ID | Given / When / Then | Verification (command or manual step) | Expected result |
|---|---|---|---|
| AC-1 | | | |

## 13. Launch and rollback
Preview → beta environment, smoke test, rollback target (known-good SHA), rollback owner. See `docs/runbooks/DEPLOYMENT_AND_ROLLBACK.md`.

## 14. Hour-24 and hour-48 gates
- Hour 24 CUT_SCOPE trigger:
- Hour 48 BUILD threshold / PIVOT signal / KILL condition / INCONCLUSIVE condition:

## 15. Risks and open questions
| Risk | Likelihood | Impact | Mitigation | Owner |
|---|---|---|---|---|
