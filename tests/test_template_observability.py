"""Run the product template's observability test suite from the factory's pytest/CI.

The template tests themselves live in templates/product-repo/src/observability/ so they
ship with every product repo; this wrapper makes the factory CI run them too.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "product-repo"


def test_template_python_observability_suite_passes():
    # -B: never write __pycache__ into the template (the template validator rejects binary files).
    result = subprocess.run(
        [sys.executable, "-B", "-m", "unittest", "discover", "-s", "src/observability", "-t", "src", "-v"],
        cwd=TEMPLATE, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "test_no_network_calls_when_dsn_unset" in result.stderr
    assert result.stderr.rstrip().endswith("OK")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_template_browser_observability_suite_passes():
    result = subprocess.run(["node", "--test", "src/observability/"],
                            cwd=TEMPLATE, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "no network calls and no listeners when DSN unset" in result.stdout
    assert "# fail 0" in result.stdout


def test_template_has_no_bytecode():
    assert not list((TEMPLATE / "src").rglob("__pycache__"))
