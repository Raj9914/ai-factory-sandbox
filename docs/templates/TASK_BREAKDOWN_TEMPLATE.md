# <Product> — Task Breakdown

Turns an approved PRD into GitHub task issues that one worker can finish alone. Author: agent:chatgpt or agent:human. Tasks are created as `status:proposed`; only a human moves a task to `status:ready`.

## Rules
- One task = one owner label = one branch = one PR. No two tasks edit the same file in the same wave.
- Each task ≤ 30 min of agent time (pilot cap) and ≤ ~300 changed lines.
- Each acceptance criterion is observable with a command or a manual step and an expected result.
- Each task names a file allowlist. Files outside it are out of scope.
- Protected paths (workflows, deploy, auth, secrets, infra) make the task high-risk and need a human reviewer.

## Waves
| Wave | Task key | Title | Owner | Files (allowlist) | Blocked by | Risk |
|---|---|---|---|---|---|---|
| 0 | T0 | Baseline: record current test/build output on default branch | agent:claude | none (read-only) | — | low |
| 1 | T1 | | | | T0 | |

## Per-task issue body (maps to `.github/ISSUE_TEMPLATE/task.yml`)
1. Title and priority (P0–P3)
2. Objective and business reason (link PRD section)
3. Background and existing implementation (files, current behaviour, baseline from T0)
4. Expected outcome
5. Scope and exclusions; file allowlist
6. Single owner label
7. Dependencies / blocked-by issues
8. Affected repositories, files and services
9. Implementation steps
10. Acceptance criteria (measurable)
11. Exact verification commands and expected results
12. Security and operational considerations
13. Timebox and budget estimate (marked as estimates)
14. Rollback or recovery
15. Definition of Done
16. Deliverables and next handoff

## Anti-cheat checks for reviewers
- Tests were not deleted, skipped or weakened.
- Expected outputs were not edited to match wrong behaviour.
- No unrelated files changed (compare to allowlist).
- Claimed command output is reproducible by the reviewer.
