import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    return subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True, timeout=60)


def test_validate_factory_passes_on_repo():
    result = run("scripts/validate_factory.py")
    assert result.returncode == 0, result.stdout + result.stderr


def test_skill_mirrors_are_in_sync():
    result = run("scripts/sync_skills.py", "--check")
    assert result.returncode == 0, result.stdout + result.stderr
