#!/usr/bin/env python3
"""Validate minimum Ship48 docs and canonical skill frontmatter."""
from pathlib import Path
import re, sys
ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ["README.md","CLAUDE.md","AGENTS.md","docs/SHIP48_PRD.md","docs/ARCHITECTURE.md","docs/OPERATING_MODEL.md","docs/DESIGN_AND_SCREEN_PIPELINE.md","docs/48_HOUR_EXPERIMENT.md","docs/SECURITY_AND_APPROVALS.md","docs/CHATGPT_WEB_PLAYBOOK.md"]
errors = [f"missing {p}" for p in REQUIRED if not (ROOT/p).is_file()]
skills = sorted((ROOT/"skills").glob("*/SKILL.md"))
if len(skills) != 8:
    errors.append(f"expected 8 canonical skills, found {len(skills)}")
pattern = re.compile(r"^---\nname: [a-z0-9-]+\ndescription: .+\n---\n", re.S)
for path in skills:
    if not pattern.match(path.read_text(encoding="utf-8")):
        errors.append(f"invalid skill: {path.relative_to(ROOT)}")
if errors:
    print("\n".join(errors)); sys.exit(1)
print(f"Factory docs valid; {len(skills)} skills found.")
