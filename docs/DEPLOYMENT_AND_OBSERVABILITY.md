# Deployment and Observability

Environments: local, preview, beta/staging, production. Preview may deploy after CI. Production requires protected Environment and explicit human approval.

Deploy only a commit passing required checks. Record SHA/environment; run smoke test; keep known-good release; document rollback. Never place provider credentials in frontend bundle.

Choose per product: Cloudflare Pages/Firebase Hosting for static UI; Cloud Run for container API; managed PostgreSQL/Supabase only when needed. Tiny experiments do not need Kubernetes by default.

Observe health/readiness, crashes/errors, safe logs, activation/completion and spend. Record rollback version/steps/data constraints/owner.
