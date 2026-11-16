import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "product-repo"
CI = ".github/workflows/ci.yml"


def load_validator():
    spec = importlib.util.spec_from_file_location("validate_template", ROOT / "scripts" / "validate_template.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def copy_template(tmp_path):
    dest = tmp_path / "product-repo"
    shutil.copytree(TEMPLATE, dest)
    return dest


def run(*args):
    return subprocess.run([sys.executable, "scripts/validate_template.py", *args],
                          cwd=ROOT, capture_output=True, text=True, timeout=60)


def test_cli_passes_on_template():
    result = run("templates/product-repo")
    version = (TEMPLATE / "TEMPLATE_VERSION").read_text(encoding="utf-8").strip()
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == f"Template valid (version {version})"


def test_cli_fails_on_broken_copy(tmp_path):
    root = copy_template(tmp_path)
    (root / "Makefile").unlink()
    result = run(str(root))
    assert result.returncode == 1
    assert "missing Makefile" in result.stdout


@pytest.mark.parametrize("rel", sorted(load_validator().REQUIRED))
def test_fails_when_required_file_missing(tmp_path, rel):
    root = copy_template(tmp_path)
    (root / rel).unlink()
    assert f"missing {rel}" in load_validator().validate(root)


@pytest.mark.parametrize("ref", ["actions/checkout@v4", "actions/checkout@main",
                                 "actions/checkout@3d3c42e", "actions/checkout"])
def test_fails_on_unpinned_action(tmp_path, ref):
    root = copy_template(tmp_path)
    path = root / CI
    text = path.read_text(encoding="utf-8")
    pinned = re.search(r"uses: (\S+)", text).group(1)
    path.write_text(text.replace(pinned, ref), encoding="utf-8")
    errors = load_validator().validate(root)
    assert any(e.startswith(f"action not pinned to a 40-char commit SHA: {CI}:") and e.endswith(ref) for e in errors)


def test_fails_when_permissions_missing(tmp_path):
    root = copy_template(tmp_path)
    path = root / CI
    text = path.read_text(encoding="utf-8")
    assert "permissions:\n  contents: read\n" in text
    path.write_text(text.replace("permissions:\n  contents: read\n", ""), encoding="utf-8")
    assert f"workflow missing top-level permissions: {CI}" in load_validator().validate(root)


def test_fails_on_pull_request_target(tmp_path):
    root = copy_template(tmp_path)
    path = root / CI
    path.write_text(path.read_text(encoding="utf-8").replace("  pull_request:\n", "  pull_request_target:\n"),
                    encoding="utf-8")
    assert f"workflow must not use pull_request_target: {CI}" in load_validator().validate(root)


def test_checks_extra_workflows(tmp_path):
    root = copy_template(tmp_path)
    (root / ".github/workflows/extra.yaml").write_text(
        "on: push\njobs:\n  x:\n    steps:\n      - uses: actions/setup-node@v4\n", encoding="utf-8")
    errors = load_validator().validate(root)
    assert "workflow missing top-level permissions: .github/workflows/extra.yaml" in errors
    assert any(e.startswith("action not pinned") and "extra.yaml" in e for e in errors)


@pytest.mark.parametrize("ref,ok", [
    ("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1", True),
    ("./.github/actions/local", True),
    ("docker://alpine@sha256:" + "a" * 64, True),
    ("docker://alpine:3", False),
    ("actions/checkout@3D3C42E5AAC5BA805825DA76410C181273BA90B1", False),
    ("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1x", False),
])
def test_check_uses(ref, ok):
    assert load_validator().check_uses(ref) is ok


def test_fails_when_make_target_missing(tmp_path):
    root = copy_template(tmp_path)
    path = root / "Makefile"
    path.write_text(path.read_text(encoding="utf-8").replace("\nsmoke:", "\nsmoke-test:"), encoding="utf-8")
    assert "Makefile missing target 'smoke'" in load_validator().validate(root)


def test_fails_when_verify_command_changed(tmp_path):
    root = copy_template(tmp_path)
    path = root / ".ai-factory.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("make install && make test", "true"), encoding="utf-8")
    assert ".ai-factory.yaml verify must run 'make install && make test'" in load_validator().validate(root)


def test_fails_on_bad_version(tmp_path):
    root = copy_template(tmp_path)
    (root / "TEMPLATE_VERSION").write_text("latest\n", encoding="utf-8")
    assert "TEMPLATE_VERSION must be MAJOR.MINOR.PATCH, got 'latest'" in load_validator().validate(root)


def test_fails_on_secret(tmp_path):
    root = copy_template(tmp_path)
    (root / "docs/research/notes.md").write_text("token: ghp_" + "A" * 36 + "\n", encoding="utf-8")
    assert "possible secret in docs/research/notes.md" in load_validator().validate(root)


def test_template_contains_no_product_or_repo_specific_names():
    forbidden = re.compile(r"ai-factory-sandbox|Raj9914|github\.com/", re.I)
    hits = [p.relative_to(TEMPLATE).as_posix() for p in TEMPLATE.rglob("*")
            if p.is_file() and forbidden.search(p.read_text(encoding="utf-8"))]
    assert hits == []


def test_template_ci_runs_make_test_with_read_only_contents():
    text = (TEMPLATE / CI).read_text(encoding="utf-8")
    assert "run: make test" in text
    assert "permissions:\n  contents: read\n" in text
    assert "write" not in text


def test_template_copies_factory_prd_template():
    prd = (ROOT / "docs/templates/PRD_TEMPLATE.md").read_text(encoding="utf-8")
    assert (TEMPLATE / "docs/PRD.md").read_text(encoding="utf-8") == prd
    for name in ["USER_JOURNEY_TEMPLATE.md", "ANALYTICS_SPEC_TEMPLATE.md", "COST_LOG_TEMPLATE.md",
                 "HOUR_24_DECISION_TEMPLATE.md", "HOUR_48_DECISION_TEMPLATE.md", "TASK_BREAKDOWN_TEMPLATE.md"]:
        assert (TEMPLATE / "docs/templates" / name).read_text(encoding="utf-8") == \
            (ROOT / "docs/templates" / name).read_text(encoding="utf-8"), name
