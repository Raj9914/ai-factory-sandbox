#!/usr/bin/env python3
"""Validate minimum Ship48 docs and canonical skill frontmatter."""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ["README.md", "CLAUDE.md", "AGENTS.md", "docs/SHIP48_PRD.md", "docs/ARCHITECTURE.md",
            "docs/OPERATING_MODEL.md", "docs/DESIGN_AND_SCREEN_PIPELINE.md", "docs/48_HOUR_EXPERIMENT.md",
            "docs/SECURITY_AND_APPROVALS.md", "docs/CHATGPT_WEB_PLAYBOOK.md", "docs/AGENT_HANDOFF_PROTOCOL.md"]
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def validate(root: Path = ROOT) -> list[str]:
    errors = [f"missing {p}" for p in REQUIRED if not (root / p).is_file()]
    skills = sorted((root / "skills").glob("*/SKILL.md"))
    if not skills:
        errors.append("no canonical skills found in skills/")
    for path in skills:
        rel = path.relative_to(root)
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        m = FRONTMATTER.match(text)
        if not m:
            errors.append(f"invalid skill (no frontmatter): {rel}")
            continue
        fields = dict(line.split(":", 1) for line in m.group(1).splitlines() if ":" in line)
        name = fields.get("name", "").strip()
        if not re.fullmatch(r"[a-z0-9-]+", name):
            errors.append(f"invalid skill name {name!r}: {rel}")
        elif name != path.parent.name:
            errors.append(f"skill name {name!r} does not match folder {path.parent.name!r}: {rel}")
        if not fields.get("description", "").strip():
            errors.append(f"skill missing description: {rel}")
    return errors


def main() -> int:
    errors = validate()
    if errors:
        print("\n".join(errors))
        return 1
    count = len(list((ROOT / "skills").glob("*/SKILL.md")))
    print(f"Factory docs valid; {count} skills found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
