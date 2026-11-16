# Experiments

One folder per 48-hour experiment. The experiment ID is `<slug>-<YYYYMMDD>` (the same ID used in `docs/PRD.md`, analytics UTM campaigns and cost logs).

```text
experiments/
  <id>/
    README.md        # brief: hypothesis, hour 0 (ISO), owner, links to PRD sections and issues
    COST_LOG.md      # from docs/templates/COST_LOG_TEMPLATE.md; actual figures only
    HOUR_24.md       # from docs/templates/HOUR_24_DECISION_TEMPLATE.md (KEEP_SCOPE / CUT_SCOPE)
    HOUR_48.md       # from docs/templates/HOUR_48_DECISION_TEMPLATE.md (BUILD / PIVOT / KILL / INCONCLUSIVE)
    evidence/        # smoke output, exported metric counts, redacted screenshots
```

Rules:
- Record actual figures only; write `NOT MEASURED` or `NOT AVAILABLE` instead of estimating.
- No personal data: store counts and anonymised summaries, never raw user records.
- Decisions are drafted by agents and made by a human. Do not edit a decision file after the human records the verdict; add a dated note instead.
