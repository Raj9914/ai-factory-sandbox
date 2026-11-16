import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_design_records.py"
TEMPLATE = ROOT / "templates" / "product-repo"
TEMPLATE_SCRIPT = TEMPLATE / "scripts" / "validate_design_records.py"
TEMPLATE_CI = TEMPLATE / ".github" / "workflows" / "ci.yml"
CI_STEP = "run: python3 scripts/validate_design_records.py design/screens.yml"

VALID = """\
# Screen inventory
required_states: [default, loading, empty, error]
screens:
  - id: S1
    name: Sign in
    figma_url: https://www.figma.com/design/AbC123xyz/Sample-Product?node-id=12-34
    node_id: "12:34"
    version: "2026-11-11 v3"
    states: [default, loading, error]
    not_applicable: [empty]
  - id: S2
    name: Home
    figma_url: https://www.figma.com/file/AbC123xyz/Sample-Product
    node_id: '56-78'
    version: 1234567890
    states:
      - default
      - loading
      - empty
      - error
"""


def load():
    spec = importlib.util.spec_from_file_location("validate_design_records", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write(tmp_path, text, name="screens.yml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def run(script, *args, cwd=ROOT):
    return subprocess.run([sys.executable, str(script), *map(str, args)],
                          cwd=cwd, capture_output=True, text=True, check=False)


def test_valid_inventory_passes(tmp_path):
    path = write(tmp_path, VALID)
    assert load().validate(path) == []
    result = run(SCRIPT, path)
    assert result.returncode == 0, result.stdout
    assert result.stdout.strip() == "Design records valid (2 screens)"


def test_parses_screens_subset():
    data = load().parse(VALID)
    assert data["required_states"] == ["default", "loading", "empty", "error"]
    assert data["screens"][0]["node_id"] == "12:34"
    assert data["screens"][1]["states"] == ["default", "loading", "empty", "error"]


def test_fails_on_missing_node_id(tmp_path):
    path = write(tmp_path, VALID.replace('    node_id: "12:34"\n', ""))
    errors = load().validate(path)
    assert errors == [f"{path}: screen S1: missing node_id"]
    result = run(SCRIPT, path)
    assert result.returncode == 1
    assert result.stdout.strip() == f"{path}: screen S1: missing node_id"


def test_fails_on_missing_state(tmp_path):
    path = write(tmp_path, VALID.replace("    not_applicable: [empty]\n", ""))
    assert load().validate(path) == [
        f"{path}: screen S1: missing state 'empty' (add to states or not_applicable)"]


def test_fails_on_missing_states_list(tmp_path):
    path = write(tmp_path, VALID.replace("    states: [default, loading, error]\n", ""))
    assert load().validate(path) == [f"{path}: screen S1: missing states"]


@pytest.mark.parametrize("field", ["figma_url", "version", "name"])
def test_fails_on_missing_required_field(tmp_path, field):
    lines = [l for l in VALID.splitlines(keepends=True) if not l.startswith(f"    {field}:")]
    errors = load().validate(write(tmp_path, "".join(lines)))
    assert any(e.endswith(f"screen S1: missing {field}") for e in errors)
    assert any(e.endswith(f"screen S2: missing {field}") for e in errors)


def test_fails_on_non_figma_url_and_bad_node_id(tmp_path):
    text = VALID.replace("https://www.figma.com/file/AbC123xyz/Sample-Product", "https://example.com/x")
    text = text.replace("node_id: '56-78'", "node_id: frame-a")
    errors = load().validate(write(tmp_path, text))
    assert any("screen S2: figma_url is not a Figma file/design URL" in e for e in errors)
    assert any("screen S2: node_id must look like '12:34'" in e for e in errors)


def test_fails_on_duplicate_id(tmp_path):
    errors = load().validate(write(tmp_path, VALID.replace("id: S2", "id: S1")))
    assert any(e.endswith("screen S1: duplicate id") for e in errors)


def test_custom_required_states(tmp_path):
    text = VALID.replace("required_states: [default, loading, empty, error]",
                         "required_states: [default, disabled]")
    errors = load().validate(write(tmp_path, text))
    assert any(e.endswith("screen S1: missing state 'disabled' (add to states or not_applicable)") for e in errors)
    assert any(e.endswith("screen S2: missing state 'disabled' (add to states or not_applicable)") for e in errors)


def test_fails_on_empty_or_unparseable_inventory(tmp_path):
    assert load().validate(write(tmp_path, "screens: []\n"))[0].endswith("screens must be a non-empty list")
    errors = load().validate(write(tmp_path, "screens:\n  - id: S1\n   name: bad indent\n"))
    assert len(errors) == 1 and "line 3" in errors[0]


def test_missing_inventory_passes_with_message(tmp_path):
    result = run(SCRIPT, tmp_path / "design" / "screens.yml")
    assert result.returncode == 0
    assert result.stdout.startswith("No design inventory at ")


def test_template_carries_identical_copy():
    assert TEMPLATE_SCRIPT.read_text(encoding="utf-8") == SCRIPT.read_text(encoding="utf-8")


def test_template_ci_runs_validator_before_tests():
    text = TEMPLATE_CI.read_text(encoding="utf-8")
    assert CI_STEP in text
    assert text.index(CI_STEP) < text.index("run: make test")


def test_template_ci_step_fails_on_record_missing_node_id(tmp_path):
    """Run the template CI step exactly as written, from a product repo root."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "validate_design_records.py").write_text(
        TEMPLATE_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "design").mkdir()
    write(tmp_path / "design", VALID.replace("    node_id: '56-78'\n", ""))
    command = CI_STEP.removeprefix("run: ").split()
    result = subprocess.run([sys.executable, *command[1:]], cwd=tmp_path,
                            capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert result.stdout.strip() == "design/screens.yml: screen S2: missing node_id"
