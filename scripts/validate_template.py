#!/usr/bin/env python3
"""Validate the Ship48 product repository template (stdlib only).

Usage: python scripts/validate_template.py [TEMPLATE_DIR]   (default: templates/product-repo)

Checks: required files present, TEMPLATE_VERSION is semver, Makefile declares the
install/test/build/smoke contract, .ai-factory.yaml verifies with `make install && make test`,
every workflow declares top-level `permissions`, never uses `pull_request_target`, and pins
every action to a 40-character commit SHA, and no file contains an obvious secret.
"""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEMPLATE = ROOT / "templates" / "product-repo"

REQUIRED = [
    "README.md", "CLAUDE.md", "AGENTS.md", "Makefile", "TEMPLATE_VERSION", ".ai-factory.yaml",
    ".github/workflows/ci.yml", ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/ISSUE_TEMPLATE/task.yml", ".github/ISSUE_TEMPLATE/bug.yml", ".github/ISSUE_TEMPLATE/feedback.yml",
    "docs/PRD.md", "docs/research/README.md", "experiments/README.md",
]
MAKE_TARGETS = ["install", "test", "build", "smoke"]
VERIFY_COMMAND = "make install && make test"
VERSION_RE = re.compile(r"\d+\.\d+\.\d+")
USES_RE = re.compile(r"""^\s*(?:-\s+)?uses:\s*["']?([^\s"'#]+)""")
SHA_REF_RE = re.compile(r"@[0-9a-f]{40}$")
SECRET_PATTERNS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),           # GitHub tokens
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),                      # AWS access key id
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),                 # OpenAI/Anthropic-style API keys
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]


def check_uses(ref: str) -> bool:
    """True if a `uses:` reference is pinned (or local)."""
    if ref.startswith("./"):
        return True
    if ref.startswith("docker://"):
        return "@sha256:" in ref
    return SHA_REF_RE.search(ref) is not None


def validate_workflow(text: str, rel: str) -> list[str]:
    errors = []
    lines = text.replace("\r\n", "\n").splitlines()
    if "pull_request_target" in text:
        errors.append(f"workflow must not use pull_request_target: {rel}")
    if not any(re.match(r"permissions:", line) for line in lines):
        errors.append(f"workflow missing top-level permissions: {rel}")
    for number, line in enumerate(lines, 1):
        m = USES_RE.match(line)
        if m and not check_uses(m.group(1)):
            errors.append(f"action not pinned to a 40-char commit SHA: {rel}:{number}: {m.group(1)}")
    return errors


def read_version(root: Path) -> str:
    path = root / "TEMPLATE_VERSION"
    return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


def validate(root: Path = DEFAULT_TEMPLATE) -> list[str]:
    root = Path(root)
    if not root.is_dir():
        return [f"template directory not found: {root}"]
    errors = [f"missing {p}" for p in REQUIRED if not (root / p).is_file()]

    version = read_version(root)
    if (root / "TEMPLATE_VERSION").is_file() and not VERSION_RE.fullmatch(version):
        errors.append(f"TEMPLATE_VERSION must be MAJOR.MINOR.PATCH, got {version!r}")

    makefile = root / "Makefile"
    if makefile.is_file():
        text = makefile.read_text(encoding="utf-8")
        errors += [f"Makefile missing target {t!r}" for t in MAKE_TARGETS
                   if not re.search(rf"^{t}:", text, re.M)]
        if "dist" not in text:
            errors.append("Makefile must build into dist/")

    factory = root / ".ai-factory.yaml"
    if factory.is_file() and VERIFY_COMMAND not in factory.read_text(encoding="utf-8"):
        errors.append(f".ai-factory.yaml verify must run {VERIFY_COMMAND!r}")

    workflows_dir = root / ".github" / "workflows"
    workflows = sorted([*workflows_dir.glob("*.yml"), *workflows_dir.glob("*.yaml")])
    for path in workflows:
        errors += validate_workflow(path.read_text(encoding="utf-8"), path.relative_to(root).as_posix())

    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            errors.append(f"non-text file in template: {path.relative_to(root).as_posix()}")
            continue
        if any(p.search(text) for p in SECRET_PATTERNS):
            errors.append(f"possible secret in {path.relative_to(root).as_posix()}")
    return errors


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else DEFAULT_TEMPLATE
    errors = validate(root)
    if errors:
        print("\n".join(errors))
        return 1
    print(f"Template valid (version {read_version(root)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
