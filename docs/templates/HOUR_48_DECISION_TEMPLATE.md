# <Experiment ID> — Hour-48 Decision

| Field | Value |
|---|---|
| Hour 0 / recorded at (ISO) | |
| Drafted by | agent:chatgpt (skill `48h-decision`) |
| Decided by | agent:human |
| **Final verdict** | **BUILD / PIVOT / KILL / INCONCLUSIVE** (exactly one) |
| Confidence | low / medium / high, with reason |

## Verdict rules
- **BUILD**: credible activation and return/replay signal against the pre-registered threshold; clear next test.
- **PIVOT**: some signal, but the proposition, onboarding or loop misses; name what changes.
- **KILL**: no credible signal with adequate exposure, an unsafe blocker, or unacceptable unit cost.
- **INCONCLUSIVE**: exposure or measurement was inadequate. Do not present as demand or as failure.

## Evidence (actual counts only)
| Metric | Numerator | Denominator | Window | Source | Pre-registered threshold | Met? |
|---|---|---|---|---|---|---|
| Exposure (target users reached) | | | | | | |
| Activation | | | | | | |
| Return / replay | | | | | | |
| Crash-free sessions | | | | | | |

Small samples are directional only. Do not claim statistical significance.

## Qualitative feedback
| Quote or summary | Source (feedback issue link) | Count |
|---|---|---|

## Defects and safety
Open critical security / data-loss defects (must be zero for BUILD).

## Cost
| Category | Planned cap | Actual | Source |
|---|---|---|---|
| Model/API | USD 25 | | cost log |
| Cloud/hosting | | | |
| Acquisition | | | |
| Total | | | |

## Learning
Largest uncertainty resolved; largest uncertainty remaining.

## Next actions
| Action | Owner | Due |
|---|---|---|
For KILL: archive repo, shut down beta, delete collected user data per PRD retention.
