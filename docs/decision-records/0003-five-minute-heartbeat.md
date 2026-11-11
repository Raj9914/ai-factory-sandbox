# ADR 0003 — Five-Minute Queue Heartbeat
- Status: Accepted for pilot
- Date: 2026-11-11

Five-minute Actions schedule reports ready queue only. It does not invoke LLMs, dispatch Claude, wake ChatGPT, create issues, merge PRs or deploy. Blind coding every five minutes risks duplicate work and cost. Add dispatch only after idempotent claims, isolated workers, credentials, budgets, timeouts and cancellation. Scheduled Actions may be delayed or skipped.

Update 2026-11-11: cadence changed to every 30 minutes. The report is only a job summary, and 288 runs/day added noise without value. GitHub also disables scheduled workflows after 60 days without repository activity.
