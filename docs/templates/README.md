# Ship48 Templates

Copy a template into the product repository (see ADR 0008). Do not fill templates in this public factory repo with confidential product data.

| Stage | Template | Produced by | Consumed by |
|---|---|---|---|
| Idea screening | `MARKET_RESEARCH_TEMPLATE.md` + `skills/idea-score` | agent:chatgpt | agent:human (approve idea) |
| Experiment brief | `../PRODUCT_EXPERIMENT_TEMPLATE.md` | agent:chatgpt | agent:human |
| PRD | `PRD_TEMPLATE.md` | agent:chatgpt | all |
| Journeys | `USER_JOURNEY_TEMPLATE.md` | agent:chatgpt | design, engineering |
| Screens | `../design/SCREEN_SPEC_TEMPLATE.md` | agent:chatgpt | agent:claude |
| Analytics | `ANALYTICS_SPEC_TEMPLATE.md` | agent:chatgpt | agent:claude |
| Task breakdown | `TASK_BREAKDOWN_TEMPLATE.md` | agent:chatgpt or agent:human | GitHub task issues |
| Cost log | `COST_LOG_TEMPLATE.md` | every run owner | hour-24/48 decisions |
| Hour 24 | `HOUR_24_DECISION_TEMPLATE.md` | agent:chatgpt drafts | agent:human decides |
| Hour 48 | `HOUR_48_DECISION_TEMPLATE.md` | agent:chatgpt drafts | agent:human decides |

Runbooks live in `../runbooks/`.

## Verdict vocabulary (single source)
- Idea screening (`idea-score`): GO, VALIDATE_FIRST, REJECT, STOP.
- Hour 24: KEEP_SCOPE or CUT_SCOPE.
- Hour 48 final verdict: **BUILD, PIVOT, KILL or INCONCLUSIVE**. No other final verdict is valid.

## Fact labelling
Every template separates **Fact** (with source link and date), **Assumption** and **Open question**. Never invent numbers. Write `NOT MEASURED` instead of estimating a metric.
