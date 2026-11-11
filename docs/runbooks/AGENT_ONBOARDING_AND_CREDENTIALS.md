# Runbook — Agent Onboarding and Credential Setup

Owner: agent:human. Never paste credential values into issues, PRs, docs, chat transcripts or logs. Record only *where* a credential lives and *which* permission it has.

## Identities (target state, ADR 0009)
| Agent | Identity | GitHub permission | Notes |
|---|---|---|---|
| Orchestrator / Claude worker | GitHub App installation (bot) | Contents RW, Pull requests RW, Issues RW, Checks R, Metadata R; product repos and sandbox only | Cannot approve its own PRs; no admin |
| Codex reviewer | separate GitHub App or bot account | Pull requests RW (review), Contents R | Reviews only |
| ChatGPT (web) | ChatGPT GitHub connector authorised by the human | Read-only where possible | Interactive only; cannot be woken by comments (ADR 0004) |
| Human | owner account | Admin | Approves production, merges, settings |

Until bot identities exist, all agents act as the owner account and "no self-merge / independent review" is policy only (see ADR 0006 when merged).

## Onboarding checklist per agent
1. Read `AGENTS.md`, `CLAUDE.md` (Claude), `docs/OPERATING_MODEL.md`, `docs/AGENT_HANDOFF_PROTOCOL.md`.
2. Confirm which owner label it acts for (`agent:claude`, `agent:chatgpt`, `agent:codex`).
3. Credentials: created by the human, least privilege, stored in the OS keychain / CLI auth store / GitHub Environment secret. Never in the repository or `.env` files committed to git.
4. Verify access without printing tokens: `gh auth status` (shows account and scopes only), `gh api repos/<o>/<r> --jq .permissions`.
5. Run one read-only task (e.g. summarise an issue) and one low-risk task through the full handoff.
6. Record in the onboarding log (private location): agent, identity, scopes, date, expiry, who approved.

## Provider spend limits (required before unattended runs)
| Provider | Where to set | Value | Verified on |
|---|---|---|---|
| Anthropic API (if used) | Console → organisation limits | monthly hard limit | |
| OpenAI API (if used) | Platform → limits | monthly budget | |
| Cloud host | Billing budget + alert at 50/90/100% | | |
Subscriptions (ChatGPT, Claude) are not API credits; unattended API workers need separate keys and limits.

## Figma
- Figma file owned by the human's team; agents get view (Claude) or edit (ChatGPT, if connected) access only to the experiment file.
- Claude Code reads frames through a configured Figma MCP server; record the server name and scope here, not tokens.

## Rotation and offboarding
- Rotate API keys every 90 days or on any incident (see `INCIDENT_AND_KILL_SWITCH.md`).
- Offboard: revoke key/app installation, remove connector, remove from environment reviewers, note date.

## GitHub token scopes in use
Record required scopes (e.g. `repo`, `workflow`, `read:project`/`project` for Projects). Request new scopes only for a ticketed need.
