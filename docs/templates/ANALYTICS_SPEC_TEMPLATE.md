# <Product> — Analytics Event and Metrics Specification

| Field | Value |
|---|---|
| PRD | link |
| Analytics provider | per ADR 0012 (record actual choice) |
| Owner | agent:chatgpt (spec) / agent:claude (instrumentation) |

## Principles
- Collect no PII. Use a random anonymous ID; no emails, names, precise location or free-text in event properties.
- Every event is defined here before it is shipped. Unknown events are a defect.
- Exclude internal/test traffic with the `internal=true` property and the documented filter.

## Event catalogue
| Event name (snake_case) | Fired when (exact trigger) | Properties (name: type, allowed values) | Screen ID | Journey step | Verification step |
|---|---|---|---|---|---|
| `app_opened` | first render of entry screen | `source: string (utm_source or "direct")` | S-01 | 1 | open preview URL, see event in provider live view |
| `core_action_completed` | | | | | |

## Metric definitions
| Metric | Numerator | Denominator | Window | Exclusions |
|---|---|---|---|---|
| Activation rate | users firing `<activation event>` | users firing `app_opened` from target channels | hour 18–48 | internal=true |
| Return rate | users with ≥2 sessions ≥30 min apart | activated users | hour 18–48 | |
| Crash-free sessions | sessions without `error_unhandled` | all sessions | | |

## Acquisition tagging
UTM convention: `utm_source=<channel>&utm_campaign=<experiment-id>`.

## Feedback capture
Feedback route (form/link), what is stored, who can read it, retention.

## Verification checklist
- [ ] Each event seen once in provider debug view from the preview URL.
- [ ] Internal traffic filter verified.
- [ ] No PII in any captured payload (inspect raw event JSON).
