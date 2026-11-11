---
name: test-and-verify
description: Verify Ship48 acceptance criteria with repeatable tests and evidence.
---
# Test and Verify
Test acceptance criteria, not only implementation. Never delete/weaken failing tests. Mark PASS, FAIL or NOT RUN honestly.

Layers: format/lint/type/security/dependency; unit; integration; E2E core journey/game loop; responsive/accessibility/loading/empty/error states; runtime startup/health/console/crash; release smoke/rollback.

Apps: first run, primary action, validation, promised persistence, network failure/retry. Games: controls, rules, scoring, pause/resume, win/loss, restart/replay and supported devices.

Output a table of check, method/command, result, evidence and severity. List blockers first. End READY_FOR_REVIEW or BLOCKED. Never claim a test ran if it did not.
