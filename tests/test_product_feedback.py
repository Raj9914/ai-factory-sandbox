"""Issue #61: no-PII feedback form, in-product Feedback link and the type:feedback label."""
import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates" / "product-repo"
FORM = TEMPLATE / ".github" / "ISSUE_TEMPLATE" / "feedback.yml"


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses in provision_product resolve their module here
    spec.loader.exec_module(module)
    return module


def counts(stdout):
    found = {}
    for key in ("tests", "pass", "fail"):
        # TAP reporter prints "# tests 5"; the spec reporter (newer Node) prints "ℹ tests 5".
        match = re.search(rf"^(?:#|ℹ) {key} (\d+)$", stdout, re.MULTILINE)
        assert match, f"no '{key}' summary line in output:\n{stdout}"
        found[key] = int(match.group(1))
    return found


def test_npm_test_feedback_all_pass():
    result = subprocess.run(["npm", "test", "--", "feedback"], cwd=TEMPLATE,
                            capture_output=True, text=True, timeout=120)
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr
    found = counts(result.stdout)
    assert found["fail"] == 0
    assert found["pass"] == found["tests"] >= 5


def test_form_has_rating_goal_outcome_and_label():
    text = FORM.read_text(encoding="utf-8")
    assert 'labels: ["type:feedback"]' in text
    assert re.findall(r"^\s+id: (\S+)$", text, re.MULTILINE) == ["rating", "goal", "outcome"]
    assert "type: dropdown" in text
    assert re.findall(r'^\s+- "(\d)"$', text, re.MULTILINE) == ["1", "2", "3", "4", "5"]
    assert "label: What were you trying to do?" in text
    assert "label: What happened?" in text


def test_issue_grep_matches_only_the_form_name_key():
    # Issue verification: grep -Eic "email|phone|name:" feedback.yml  -> 1
    lines = FORM.read_text(encoding="utf-8").splitlines()
    hits = [line for line in lines if re.search(r"email|phone|name:", line, re.IGNORECASE)]
    assert hits == ["name: Feedback"]


def test_grep_command_output_is_1():
    result = subprocess.run(["grep", "-Eic", "email|phone|name:", str(FORM)],
                            capture_output=True, text=True, timeout=30)
    assert result.stdout.strip() == "1"


def test_validator_accepts_form():
    assert load("validate_template").validate_feedback_form(FORM.read_text(encoding="utf-8")) == []


def test_validator_rejects_pii_fields():
    validator = load("validate_template")
    text = FORM.read_text(encoding="utf-8")
    for field in ["id: email", "label: Your email address", "label: Phone number", "label: Full name",
                  "label: Name", "description: Your name (optional)", "placeholder: you@e-mail",
                  "id: user_name", "label: Contact details"]:
        bad = text + f"  - type: input\n    attributes:\n      {field}\n"
        errors = validator.validate_feedback_form(bad)
        assert any("field asks for personal data" in e for e in errors), field


def test_validator_requires_feedback_label():
    validator = load("validate_template")
    text = FORM.read_text(encoding="utf-8").replace('labels: ["type:feedback"]', 'labels: ["type:bug"]')
    assert validator.validate_feedback_form(text) == \
        [".github/ISSUE_TEMPLATE/feedback.yml must apply the 'type:feedback' label"]


def test_validate_reports_pii_form_in_template(tmp_path):
    root = tmp_path / "product-repo"
    shutil.copytree(TEMPLATE, root)
    path = root / ".github" / "ISSUE_TEMPLATE" / "feedback.yml"
    path.write_text(path.read_text(encoding="utf-8") + "  - type: input\n    id: email\n", encoding="utf-8")
    errors = load("validate_template").validate(root)
    assert any(e.startswith(".github/ISSUE_TEMPLATE/feedback.yml:") and "id: email" in e for e in errors)


def test_provisioning_label_set_has_type_feedback():
    labels = {name: (color, description) for name, color, description in load("provision_product").LABELS}
    assert "type:feedback" in labels
