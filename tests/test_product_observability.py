"""Runs the product template's error-tracking tests (issue #58):
``cd templates/product-repo && npm test -- observability``."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates" / "product-repo"


def counts(stdout):
    found = {}
    for key in ("tests", "pass", "fail"):
        # TAP reporter prints "# tests 6"; the spec reporter (newer Node) prints "ℹ tests 6".
        match = re.search(rf"^(?:#|ℹ) {key} (\d+)$", stdout, re.MULTILINE)
        assert match, f"no '{key}' summary line in output:\n{stdout}"
        found[key] = int(match.group(1))
    return found


def test_npm_test_observability_all_pass():
    result = subprocess.run(["npm", "test", "--", "observability"], cwd=TEMPLATE,
                            capture_output=True, text=True, timeout=120)
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr
    found = counts(result.stdout)
    assert found["fail"] == 0
    assert found["pass"] == found["tests"] >= 6
    assert "no network calls when DSN is unset" in result.stdout
