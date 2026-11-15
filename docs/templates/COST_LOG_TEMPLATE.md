# <Experiment ID> — Cost Log

Manual log until automated ingestion exists. One row per run, deploy or purchase. Record only actual figures from a provider bill, CLI output or orchestrator run record. If unknown, write `NOT AVAILABLE`; never estimate in the Actual column.

Pilot caps (internal hypotheses, see docs/COST_CONTROLS.md): USD 5 per task, USD 25 per experiment for model/API spend, max 3 repair attempts, 30-minute task timeout. Provider hard limits must be configured separately (see runbook `AGENT_ONBOARDING_AND_CREDENTIALS.md`).

| Date (UTC) | Issue/PR | Actor (agent:*) | Provider/service | What | Duration (min) | Tokens (if shown) | Actual USD | Source of figure |
|---|---|---|---|---|---|---|---|---|

## Running totals
| Category | Cap | Actual to date | % of cap |
|---|---|---|---|
| Model/API | 25.00 | | |
| Cloud/hosting | | | |
| Acquisition | | | |

## Stop rules
Stop and escalate to agent:human when: a cap is reached; an action's cost is unknown and could be material; the same failure repeats twice; any spend cannot be attributed to an issue.
