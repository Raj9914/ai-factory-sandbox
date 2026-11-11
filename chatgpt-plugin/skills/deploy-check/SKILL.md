---
name: deploy-check
description: Check Ship48 release readiness, secrets, monitoring, rollback and smoke evidence.
---
# Deployment Readiness
Preview may deploy after CI. Production requires explicit human approval via protected GitHub Environment. Never bypass protection.

Check required CI/security; no secrets/PII committed; runtime config; safe migrations/rollback; auth; HTTPS/session settings; safe logs; error monitoring; core analytics; health/readiness; smoke tests; budget caps; rollback owner/steps.

Report environment, commit SHA, PASS/FAIL/NOT RUN, preview URL/smoke evidence, rollback and approval requirement. Claim deployment only if provider/workflow confirms success.
