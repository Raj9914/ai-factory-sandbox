# Ship48 AI Factory

> Build. Ship. Prove. Or Kill.

This is a public sandbox pilot. It already contains a tiny Python app, tests and example CI/deploy workflows. Do not commit secrets, customer/personal data or confidential product plans. Move the factory control plane to a private repository before confidential use.

## Start here
- Product: docs/SHIP48_PRD.md
- Architecture: docs/ARCHITECTURE.md
- Operating model: docs/OPERATING_MODEL.md
- Design: docs/DESIGN_AND_SCREEN_PIPELINE.md
- Experiment: docs/48_HOUR_EXPERIMENT.md
- Security: docs/SECURITY_AND_APPROVALS.md
- Handoffs: docs/AGENT_HANDOFF_PROTOCOL.md

Claude Code reads CLAUDE.md and .claude/skills/. Codex reads AGENTS.md and .agents/skills/. Canonical skills are in skills/. ChatGPT playbook/plugin sources are in docs/CHATGPT_WEB_PLAYBOOK.md and chatgpt-plugin/skills/.

Run python scripts/sync_skills.py after editing canonical skills; use --check to verify mirrors. Run python scripts/validate_factory.py to validate docs.

The queue heartbeat (every 30 minutes) is queue-only: it reports ready issues. It does not call an LLM, start Claude, wake ChatGPT, create tickets, merge PRs or deploy. Agent dispatch needs separate credentials, isolated workers, idempotent claims, budgets and human gates.

Validate: python -m pip install -e '.[dev]' && python -m pytest -q && python scripts/validate_factory.py && python scripts/sync_skills.py --check

No tickets are created by this bootstrap; issue forms are templates only.
