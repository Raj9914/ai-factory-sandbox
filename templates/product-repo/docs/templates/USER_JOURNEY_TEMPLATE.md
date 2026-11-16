# <Product> — User Journeys

| Field | Value |
|---|---|
| PRD | link |
| Persona | one persona from PRD §2 |
| Author / approver | agent:chatgpt / agent:human |

## Primary journey (the activation path)
| Step | User intent | Screen ID | User action | System response | Analytics event | Failure path |
|---|---|---|---|---|---|---|
| 1 | | S-01 | | | | |

Activation is reached at step: `<n>` (must match PRD §4 and analytics spec).

## Return / replay journey
Same table. Defines the return or replay signal for hour-48.

## Edge and failure journeys
| Case | Trigger | Expected behaviour | Screen/state |
|---|---|---|---|
| First run, no data | | empty state | |
| Network failure | | retry + error state | |
| Invalid input | | validation state | |

## Screen inventory cross-check
Every Screen ID above must exist in `docs/design/SCREEN_SPEC_TEMPLATE.md`; every screen there must appear in a journey or be removed.
