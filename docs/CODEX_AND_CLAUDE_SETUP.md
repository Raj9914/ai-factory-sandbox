# Codex and Claude Code Setup

Codex reads root AGENTS.md and skills under .agents/skills/<name>/SKILL.md. Run Codex at repository root.

Claude Code reads root CLAUDE.md and skills under .claude/skills/<name>/SKILL.md. Run Claude at repository root; confirm owner before work.

Edit canonical skills/<name>/SKILL.md only. Run python scripts/sync_skills.py and python scripts/sync_skills.py --check. Review generated diffs.

Use supported CLI/app auth and least privilege. CI uses secrets/OIDC as applicable. Never copy browser cookies/session tokens into CI.
