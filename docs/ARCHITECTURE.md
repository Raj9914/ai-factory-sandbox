# Architecture

Control plane: GitHub repo, issues, PRs, Actions, labels and reports.
Design plane: Figma editable screens/components/variables; GitHub stores PRD, screen inventory, links and review exports.
Execution plane: GitHub Actions plus isolated workers for Claude/Codex after explicit setup.
ChatGPT plane: interactive product/design/review via connected apps and supported plugins/Work.

Flow: Human → GitHub task ledger → ChatGPT product/design or Claude engineering → Figma/code → CI/security/review → beta → user evidence → experiment decision.

A normal ChatGPT Web conversation is not a persistent worker and cannot be awakened by an issue comment. Unattended OpenAI work needs an eligible event/Work/plugin capability or separately authenticated API worker. Subscription and API billing are separate.

The queue heartbeat (every 30 minutes) only reports ready queue state; it does not invoke agents. Scheduled workflows can be delayed or skipped.

Isolation: one task/run/branch; per-issue concurrency lock; no shared writable workspace. Use ephemeral hosted runners for untrusted code. No production secrets in PR jobs.

Pilot stack: GitHub Actions, Claude Code, ChatGPT Web, Figma and hosting selected per product. No Kubernetes or distributed queue for the first product.
