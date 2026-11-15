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

FORMS_DIR = ".github/ISSUE_TEMPLATE"
# Required issue-form fields by id. Each must exist and be marked `required: true`.
# Task fields map to the 16 sections of docs/templates/TASK_BREAKDOWN_TEMPLATE.md (section 1's title is the issue title).
REQUIRED_FORM_FIELDS = {
    "task.yml": ["priority", "objective", "background", "outcome", "scope", "owner", "dependencies", "affected",
                 "steps", "acceptance", "verify", "security", "timebox", "rollback", "dod", "deliverables"],
    "decision.yml": ["question", "options", "recommendation", "tradeoffs"],
}
REQUIRED_FORM_LABELS = {
    "task.yml": ["status:proposed", "type:task"],
    "decision.yml": ["status:proposed", "type:decision", "agent:human"],
}
FORBIDDEN_FORM_LABELS = ["status:ready"]  # only a human applies status:ready


def parse_form(text: str) -> tuple[list[str], dict[str, bool]]:
    """Return (labels, {field id: required}) from an issue form.

    Deliberately small line-based reader (stdlib only) for the flat layout used in this repo's forms.
    """
    labels: list[str] = []
    fields: dict[str, bool] = {}
    current = None
    for line in text.replace("\r\n", "\n").splitlines():
        if line.startswith("labels:"):
            labels = re.findall(r"""["']([^"']+)["']""", line)
        elif re.match(r"\s*- type:", line):
            current = None
        elif m := re.match(r"\s+id:\s*(\S+)\s*$", line):
            current = m.group(1).strip("\"'")
            fields[current] = False
        elif current and re.match(r"\s+required:\s*true\s*$", line):
            fields[current] = True
    return labels, fields


def validate_forms(root: Path) -> list[str]:
    errors = []
    forms_dir = root / FORMS_DIR
    for path in sorted(forms_dir.glob("*.yml")):
        labels, _ = parse_form(path.read_text(encoding="utf-8"))
        errors += [f"form must not apply {label}: {FORMS_DIR}/{path.name}"
                   for label in FORBIDDEN_FORM_LABELS if label in labels]
    for name, ids in REQUIRED_FORM_FIELDS.items():
        rel = f"{FORMS_DIR}/{name}"
        path = root / rel
        if not path.is_file():
            errors.append(f"missing {rel}")
            continue
        labels, fields = parse_form(path.read_text(encoding="utf-8"))
        errors += [f"form missing default label {label}: {rel}"
                   for label in REQUIRED_FORM_LABELS.get(name, []) if label not in labels]
        for field_id in ids:
            if field_id not in fields:
                errors.append(f"form missing required field {field_id!r}: {rel}")
            elif not fields[field_id]:
                errors.append(f"form field {field_id!r} must be required: {rel}")
    return errors


def validate(root: Path = ROOT) -> list[str]:
    errors = [f"missing {p}" for p in REQUIRED if not (root / p).is_file()]
    errors += validate_forms(root)
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
