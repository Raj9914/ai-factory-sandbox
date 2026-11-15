#!/usr/bin/env python3
"""Test-integrity guard.

Fails when the diff ``<base>...<head>`` weakens the test suite:

* a test file is deleted (renames are allowed);
* a test function is removed (Python ``def test_*``, JS/TS ``it(...)`` /
  ``test(...)``) and is neither re-added elsewhere nor replaced by a renamed
  test in the same file;
* a skip/focus marker is added (``@pytest.mark.skip``, ``skipif``, ``xfail``,
  ``pytest.skip()``, ``unittest.skip*``, ``SkipTest``, ``it.skip``,
  ``describe.only``, ``xit``, ``fit`` ...).

Violations are waived only when the pull request carries the
``test-change-approved`` label and the most recent add/remove of that label
in the PR timeline was an *add by the repository owner*. A label added by
any other collaborator does not count.

Usage::

    python scripts/check_test_integrity.py <base> <head> [--repo OWNER/NAME --pr N]

Without ``--repo``/``--pr`` (and without ``GITHUB_REPOSITORY``/``PR_NUMBER``
in the environment) the label check is skipped and any violation fails.

Exit codes: 0 OK or approved, 1 violations (or approval lookup failed),
2 usage/git error.

The pure functions (``parse_diff``, ``find_violations``, ``is_approved``) have
no I/O; ``main`` wraps ``git`` and the ``gh`` CLI.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field

APPROVAL_LABEL = "test-change-approved"

PY_EXT = (".py",)
JS_EXT = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts")

PY_TEST_DEF = re.compile(r"^\s*(?:async\s+)?def\s+(test\w*)\s*\(")
JS_TEST_DEF = re.compile(
    r"""^\s*[xf]?(?:it|test)(?:\.(?:only|skip|concurrent|each\s*\(.*?\)))*\s*\(\s*(['"`])(.+?)\1"""
)

# Anchored to the start of the (stripped) source line so that marker text
# inside strings, comments or regexes elsewhere on a line is not reported.
PY_SKIP_MARKERS = [
    re.compile(r"^\s*@pytest\.mark\.(?:skip|skipif|xfail)\b"),
    re.compile(r"^\s*pytestmark\s*=.*\bpytest\.mark\.(?:skip|skipif|xfail)\b"),
    re.compile(r"^\s*(?:if\b.*:\s*)?pytest\.(?:skip|xfail|importorskip)\s*\("),
    re.compile(r"^\s*@(?:unittest\.)?(?:skip|skipIf|skipUnless|expectedFailure)\b"),
    re.compile(r"^\s*(?:if\b.*:\s*)?raise\s+(?:unittest\.)?SkipTest\b"),
    re.compile(r"^\s*(?:self\.)?skipTest\s*\("),
]
JS_SKIP_MARKERS = [
    re.compile(r"^\s*(?:it|test|describe|suite|context)\.(?:skip|only|todo)\b"),
    re.compile(r"^\s*(?:xit|xtest|xdescribe|xcontext|fit|fdescribe|ftest)\s*\("),
]


def is_test_file(path: str) -> bool:
    """Heuristic: Python/JS/TS file that holds tests."""
    if not path:
        return False
    lower = path.lower()
    parts = lower.split("/")
    name = parts[-1]
    in_test_dir = any(p in ("tests", "test", "__tests__", "spec") for p in parts[:-1])
    if lower.endswith(PY_EXT):
        return name.startswith("test_") or name.endswith("_test.py") or (
            in_test_dir and name != "__init__.py"
        )
    if lower.endswith(JS_EXT):
        return ".test." in name or ".spec." in name or in_test_dir
    return False


@dataclass
class FileDiff:
    old_path: str | None
    new_path: str | None
    deleted: bool = False
    renamed: bool = False
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


def _strip_prefix(path: str) -> str | None:
    path = path.split("\t", 1)[0]
    if path == "/dev/null":
        return None
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path


def parse_diff(text: str) -> list[FileDiff]:
    """Parse ``git diff`` unified output into per-file added/removed lines."""
    files: list[FileDiff] = []
    current: FileDiff | None = None
    in_hunk = False
    for line in text.splitlines():
        if line.startswith("diff --git "):
            match = re.match(r"diff --git a/(.*) b/(.*)$", line)
            old, new = (match.group(1), match.group(2)) if match else (None, None)
            current = FileDiff(old_path=old, new_path=new)
            files.append(current)
            in_hunk = False
            continue
        if current is None:
            continue
        if not in_hunk:
            if line.startswith("deleted file mode"):
                current.deleted = True
            elif line.startswith("rename from "):
                current.renamed = True
                current.old_path = line[len("rename from "):]
            elif line.startswith("rename to "):
                current.renamed = True
                current.new_path = line[len("rename to "):]
            elif line.startswith("--- "):
                current.old_path = _strip_prefix(line[4:])
            elif line.startswith("+++ "):
                current.new_path = _strip_prefix(line[4:])
            elif line.startswith("@@"):
                in_hunk = True
            continue
        if line.startswith("@@"):
            continue
        if line.startswith("+"):
            current.added.append(line[1:])
        elif line.startswith("-"):
            current.removed.append(line[1:])
    return files


def collect_test_names(path: str | None, lines: list[str]) -> list[str]:
    """Test function names defined on ``lines`` of file ``path``."""
    if not path or not is_test_file(path):
        return []
    lower = path.lower()
    names = []
    for line in lines:
        if lower.endswith(PY_EXT):
            match = PY_TEST_DEF.match(line)
            if match:
                names.append(match.group(1))
        elif lower.endswith(JS_EXT):
            match = JS_TEST_DEF.match(line)
            if match:
                names.append(match.group(2))
    return names


def skip_markers(path: str | None, lines: list[str]) -> list[str]:
    """Lines on ``lines`` of file ``path`` that add a skip/xfail/only marker."""
    if not path:
        return []
    lower = path.lower()
    if lower.endswith(PY_EXT):
        patterns = PY_SKIP_MARKERS
    elif lower.endswith(JS_EXT):
        patterns = JS_SKIP_MARKERS
    else:
        return []
    return [ln.strip() for ln in lines if any(p.match(ln) for p in patterns)]


def find_violations(files: list[FileDiff]) -> list[str]:
    """Return human-readable violations for the parsed diff."""
    violations: list[str] = []

    removed_by_file: dict[str, list[str]] = {}
    added_by_file: dict[str, list[str]] = {}
    for f in files:
        if f.deleted and f.old_path and is_test_file(f.old_path):
            violations.append(f"deleted test file: {f.old_path}")
        if f.old_path:
            removed_by_file.setdefault(f.old_path, []).extend(
                collect_test_names(f.old_path, f.removed)
            )
        if f.new_path:
            added_by_file.setdefault(f.new_path, []).extend(
                collect_test_names(f.new_path, f.added)
            )
        for marker in skip_markers(f.new_path, f.added):
            violations.append(f"added skip marker in {f.new_path}: {marker}")

    # 1) A removed name that is re-added anywhere (moved, signature edited,
    #    file renamed) is not a removal.
    added_pool = Counter(n for names in added_by_file.values() for n in names)
    unmatched_removed: dict[str, list[str]] = {}
    for path, names in removed_by_file.items():
        for name in names:
            if added_pool[name] > 0:
                added_pool[name] -= 1
            else:
                unmatched_removed.setdefault(path, []).append(name)

    # 2) Remaining removals in a file are allowed one-for-one against new
    #    test names in the same file (a renamed test).
    for path, names in unmatched_removed.items():
        spare = 0
        for name in added_by_file.get(path, []):
            if added_pool[name] > 0:
                added_pool[name] -= 1
                spare += 1
        for name in names[spare:]:
            violations.append(f"removed test: {path}::{name}")
    return violations


def is_approved(timeline: list[dict], owner: str, label: str = APPROVAL_LABEL) -> bool:
    """True when the latest add/remove of ``label`` is an add by ``owner``."""
    last = None
    for event in timeline:
        if event.get("event") not in ("labeled", "unlabeled"):
            continue
        if ((event.get("label") or {}).get("name")) != label:
            continue
        last = event
    if last is None or last.get("event") != "labeled":
        return False
    actor = ((last.get("actor") or {}).get("login") or "").lower()
    return bool(owner) and actor == owner.lower()


def _git_diff(base: str, head: str) -> str:
    result = subprocess.run(
        [
            "git",
            "-c",
            "core.quotePath=false",
            "diff",
            "--no-color",
            "--no-ext-diff",
            "--unified=0",
            "-M",
            f"{base}...{head}",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git diff failed")
    return result.stdout


def _fetch_timeline(repo: str, pr: str) -> list[dict]:
    result = subprocess.run(
        ["gh", "api", "--paginate", "--slurp", f"repos/{repo}/issues/{pr}/timeline"],
        capture_output=True,
        text=True,
        check=True,
    )
    pages = json.loads(result.stdout)
    return [event for page in pages for event in page]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("base")
    parser.add_argument("head")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--pr", default=os.environ.get("PR_NUMBER"))
    args = parser.parse_args(argv)

    try:
        diff = _git_diff(args.base, args.head)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    violations = find_violations(parse_diff(diff))
    if not violations:
        print(f"OK: no test-integrity violations in {args.base}...{args.head}")
        return 0

    print("Test-integrity violations:")
    for item in violations:
        print(f"  - {item}")

    if not (args.repo and args.pr):
        print("FAIL: no pull request context; approval label not checked.")
        return 1

    owner = args.repo.split("/", 1)[0]
    try:
        timeline = _fetch_timeline(args.repo, args.pr)
    except (subprocess.CalledProcessError, OSError, ValueError) as exc:
        print(f"FAIL: could not read PR timeline: {exc}")
        return 1
    if is_approved(timeline, owner):
        print(f"APPROVED: `{APPROVAL_LABEL}` added by repository owner {owner}.")
        return 0
    print(
        f"FAIL: add label `{APPROVAL_LABEL}` (by the repository owner {owner}) "
        "to approve these test changes."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
