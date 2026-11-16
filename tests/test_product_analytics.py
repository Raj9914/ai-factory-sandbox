"""Runs the product template's analytics tests (issue #60) the way the issue verifies them:
``cd templates/product-repo && npm test -- analytics``."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates" / "product-repo"


def run_npm_test(*args):
    return subprocess.run(["npm", "test", "--", *args], cwd=TEMPLATE, capture_output=True,
                          text=True, timeout=120)


def counts(stdout):
    found = {}
    for key in ("tests", "pass", "fail"):
        # TAP reporter prints "# tests 12"; the spec reporter (newer Node) prints "ℹ tests 12".
        match = re.search(rf"^(?:#|ℹ) {key} (\d+)$", stdout, re.MULTILINE)
        assert match, f"no '{key}' summary line in output:\n{stdout}"
        found[key] = int(match.group(1))
    return found


def test_npm_test_analytics_all_pass():
    result = run_npm_test("analytics")
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr
    found = counts(result.stdout)
    assert found["fail"] == 0
    assert found["pass"] == found["tests"] >= 5


def test_npm_test_fails_when_a_test_fails(tmp_path):
    # Guard against a test script that always exits 0: point it at a failing file.
    failing = TEMPLATE / "tests" / "zz_tmp_failing_probe.test.js"
    failing.write_text("import { test } from 'node:test';\ntest('probe', () => { throw new Error('x'); });\n")
    try:
        result = run_npm_test("zz_tmp_failing_probe")
    finally:
        failing.unlink()
    assert result.returncode != 0
    assert counts(result.stdout)["fail"] == 1
