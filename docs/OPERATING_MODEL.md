# Operating Model

| Role | Responsibility |
|---|---|
| Human | Scope, budgets, secrets, production approval, final decision |
| ChatGPT | Research, idea score, PRD, screen/Figma design where connected, independent review |
| Claude | Assigned implementation, tests, fixes, PR and preview |
| Codex | Optional separately assigned worker/reviewer |
| Actions | CI, queue heartbeat, later gated dispatch |

Lifecycle: proposed → approved → ready → claimed → in_progress → review → accepted → done. Other states: blocked/cancelled/killed.

Each task has one owner. No agents edit the same task/branch concurrently. Reviewer is read-only; findings go to task owner.

ChatGPT→Claude handoff contains approved PRD, testable criteria, Figma URLs/frame IDs, constraints and owner label. Claude→ChatGPT contains PR, test evidence, preview/screenshots and explicit review request. Issue comments do not wake this chat.

Each run reports completed work, changed files/links, tests/results, risks, actual duration/cost if known, next action and owner.
