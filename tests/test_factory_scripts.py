import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    return subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True, timeout=60)


def test_validate_factory_passes_on_repo():
    result = run("scripts/validate_factory.py")
    assert result.returncode == 0, result.stdout + result.stderr


def test_skill_mirrors_are_in_sync():
    result = run("scripts/sync_skills.py", "--check")
    assert result.returncode == 0, result.stdout + result.stderr


def load_validator():
    spec = importlib.util.spec_from_file_location("validate_factory", ROOT / "scripts" / "validate_factory.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def copy_repo(tmp_path):
    dest = tmp_path / "repo"
    shutil.copytree(ROOT, dest, ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", "*.egg-info"))
    return dest


def test_task_form_default_labels():
    labels, _ = load_validator().parse_form((ROOT / ".github/ISSUE_TEMPLATE/task.yml").read_text(encoding="utf-8"))
    assert "status:proposed" in labels
    assert "status:ready" not in labels


def test_decision_form_default_labels():
    labels, _ = load_validator().parse_form((ROOT / ".github/ISSUE_TEMPLATE/decision.yml").read_text(encoding="utf-8"))
    assert {"type:decision", "agent:human", "status:proposed"} <= set(labels)
    assert "status:ready" not in labels


def test_task_form_covers_all_16_sections():
    validator = load_validator()
    _, fields = validator.parse_form((ROOT / ".github/ISSUE_TEMPLATE/task.yml").read_text(encoding="utf-8"))
    assert len(validator.REQUIRED_FORM_FIELDS["task.yml"]) == 16
    assert all(fields.get(f) is True for f in validator.REQUIRED_FORM_FIELDS["task.yml"])


@pytest.mark.parametrize("form,field", [("task.yml", "rollback"), ("task.yml", "owner"), ("decision.yml", "tradeoffs")])
def test_validator_fails_when_required_field_removed(tmp_path, form, field):
    validator = load_validator()
    root = copy_repo(tmp_path)
    path = root / ".github/ISSUE_TEMPLATE" / form
    text = path.read_text(encoding="utf-8")
    assert f"id: {field}\n" in text
    path.write_text(text.replace(f"id: {field}\n", f"id: {field}_renamed\n"), encoding="utf-8")
    errors = validator.validate(root)
    assert f"form missing required field {field!r}: .github/ISSUE_TEMPLATE/{form}" in errors


def test_validator_fails_when_field_made_optional(tmp_path):
    validator = load_validator()
    root = copy_repo(tmp_path)
    path = root / ".github/ISSUE_TEMPLATE/task.yml"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    start = lines.index("    id: priority\n")
    offset = next(i for i in range(start, len(lines)) if lines[i].strip() == "required: true")
    lines[offset] = lines[offset].replace("true", "false")
    path.write_text("".join(lines), encoding="utf-8")
    assert "form field 'priority' must be required: .github/ISSUE_TEMPLATE/task.yml" in validator.validate(root)


def test_validator_fails_on_status_ready_or_missing_proposed(tmp_path):
    validator = load_validator()
    root = copy_repo(tmp_path)
    path = root / ".github/ISSUE_TEMPLATE/task.yml"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace('"status:proposed"', '"status:ready"'), encoding="utf-8")
    errors = validator.validate(root)
    assert "form must not apply status:ready: .github/ISSUE_TEMPLATE/task.yml" in errors
    assert "form missing default label status:proposed: .github/ISSUE_TEMPLATE/task.yml" in errors


def test_validator_fails_when_form_file_missing(tmp_path):
    validator = load_validator()
    root = copy_repo(tmp_path)
    (root / ".github/ISSUE_TEMPLATE/decision.yml").unlink()
    assert "missing .github/ISSUE_TEMPLATE/decision.yml" in validator.validate(root)
