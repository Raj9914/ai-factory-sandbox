"""Tests for scripts/validate_decisions.py (run: python -m pytest -q tests/test_validate_decisions.py)."""
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

TEMPLATE = Path(__file__).resolve().parents[1]
SCRIPT = TEMPLATE / "scripts" / "validate_decisions.py"
CI = TEMPLATE / ".github" / "workflows" / "ci.yml"


def load():
    spec = importlib.util.spec_from_file_location("validate_decisions", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look the module up while classes are built
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True  # keep scripts/__pycache__ out of the template tree
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


vd = load()

EVIDENCE = """
## Evidence
- Metrics: [metrics](metrics.md)
- Cost: [cost log](cost.md#totals)
"""


def h24(decision="**KEEP_SCOPE**", decided_by="agent:human", recorded="2026-11-12T09:00:00Z",
        evidence=EVIDENCE):
    return f"""# demo-20261111 — Hour-24 Decision

| Field | Value |
|---|---|
| Hour 0 (ISO) | 2026-11-11T09:00:00Z |
| Recorded at (ISO) | {recorded} |
| Drafted by | agent:chatgpt |
| Decided by | {decided_by} |
| Decision | {decision} |
{evidence}"""


def h48(verdict="**KILL**", decided_by="agent:human", recorded="2026-11-11 / 2026-11-13T09:00:00Z",
        evidence=EVIDENCE):
    return f"""# demo-20261111 — Hour-48 Decision

| Field | Value |
|---|---|
| Hour 0 / recorded at (ISO) | {recorded} |
| Drafted by | agent:chatgpt (skill `48h-decision`) |
| Decided by | {decided_by} |
| **Final verdict** | {verdict} |
| Confidence | medium, exposure reached 40 of 50 target users |
{evidence}"""


def experiment(tmp_path, files: dict, evidence=("metrics.md", "cost.md")):
    exp = tmp_path / "experiments" / "demo-20261111"
    exp.mkdir(parents=True)
    for name in evidence:
        (exp / name).write_text("# evidence\n", encoding="utf-8")
    for name, text in files.items():
        (exp / name).write_text(text, encoding="utf-8")
    return tmp_path / "experiments"


def run(experiments):
    return subprocess.run([sys.executable, str(SCRIPT), str(experiments)],
                          capture_output=True, text=True, timeout=30)


def test_valid_records_pass(tmp_path):
    exps = experiment(tmp_path, {"decision-24h.md": h24(), "decision-48h.md": h48()})
    result = run(exps)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Decision records valid (2 files)" in result.stdout


@pytest.mark.parametrize("verdict", ["BUILD", "PIVOT", "KILL", "INCONCLUSIVE", "`PIVOT`"])
def test_every_allowed_final_verdict_passes(tmp_path, verdict):
    exps = experiment(tmp_path, {"decision-48h.md": h48(verdict=verdict)})
    assert vd.validate_file(exps / "demo-20261111" / "decision-48h.md") == []


def test_cut_scope_passes(tmp_path):
    exps = experiment(tmp_path, {"decision-24h.md": h24(decision="CUT_SCOPE")})
    assert vd.validate_file(exps / "demo-20261111" / "decision-24h.md") == []


def test_two_verdicts_fail(tmp_path):
    exps = experiment(tmp_path, {"decision-48h.md": h48(verdict="**BUILD** / **PIVOT**")})
    result = run(exps)
    assert result.returncode == 1
    assert "exactly one verdict required, found BUILD, PIVOT" in result.stdout


def test_unfilled_template_fails(tmp_path):
    template = (TEMPLATE / "docs" / "templates" / "HOUR_24_DECISION_TEMPLATE.md").read_text(encoding="utf-8")
    exps = experiment(tmp_path, {"decision-24h.md": template + EVIDENCE})
    errors = vd.validate_file(exps / "demo-20261111" / "decision-24h.md")
    assert any("exactly one verdict required" in e for e in errors)
    assert any("missing date" in e for e in errors)


@pytest.mark.parametrize("template,name,filled", [
    ("HOUR_24_DECISION_TEMPLATE.md", "decision-24h.md", {
        "| Recorded at (ISO) | |": "| Recorded at (ISO) | 2026-11-12T09:00:00Z |",
        "| Decision | **KEEP_SCOPE** or **CUT_SCOPE** (hour 24 never issues a final verdict) |":
            "| Decision | **CUT_SCOPE** |",
    }),
    ("HOUR_48_DECISION_TEMPLATE.md", "decision-48h.md", {
        "| Hour 0 / recorded at (ISO) | |": "| Hour 0 / recorded at (ISO) | 2026-11-11 / 2026-11-13 |",
        "| **Final verdict** | **BUILD / PIVOT / KILL / INCONCLUSIVE** (exactly one) |":
            "| **Final verdict** | **INCONCLUSIVE** |",
    }),
])
def test_filled_in_shipped_templates_pass(tmp_path, template, name, filled):
    text = (TEMPLATE / "docs" / "templates" / template).read_text(encoding="utf-8")
    for old, new in filled.items():
        assert old in text, f"template row changed: {old}"
        text = text.replace(old, new)
    exps = experiment(tmp_path, {name: text + EVIDENCE})
    assert vd.validate_file(exps / "demo-20261111" / name) == []


@pytest.mark.parametrize("name,text", [
    ("decision-24h.md", h24(decision="CONTINUE")),
    ("decision-48h.md", h48(verdict="**CONTINUE**")),
])
def test_legacy_continue_fails(tmp_path, name, text):
    exps = experiment(tmp_path, {name: text})
    result = run(exps)
    assert result.returncode == 1
    assert "verdict 'CONTINUE' is not allowed" in result.stdout


@pytest.mark.parametrize("name,text,bad", [
    ("decision-24h.md", h24(decision="BUILD"), "BUILD"),        # final verdict at hour 24
    ("decision-48h.md", h48(verdict="KEEP_SCOPE"), "KEEP_SCOPE"),  # scope call at hour 48
    ("decision-48h.md", h48(verdict="SHIP"), "SHIP"),
])
def test_verdict_outside_allowed_set_fails(tmp_path, name, text, bad):
    exps = experiment(tmp_path, {name: text})
    errors = vd.validate_file(exps / "demo-20261111" / name)
    assert any(f"verdict {bad!r} is not allowed" in e for e in errors), errors


def test_missing_verdict_fails(tmp_path):
    exps = experiment(tmp_path, {"decision-48h.md": h48(verdict="")})
    errors = vd.validate_file(exps / "demo-20261111" / "decision-48h.md")
    assert any("missing verdict" in e for e in errors)


def test_missing_evidence_links_fail(tmp_path):
    exps = experiment(tmp_path, {"decision-48h.md": h48(evidence="\nNo links here.\n")})
    result = run(exps)
    assert result.returncode == 1
    assert "missing evidence link to metrics.md" in result.stdout
    assert "missing evidence link to cost.md" in result.stdout


def test_evidence_link_to_missing_file_fails(tmp_path):
    exps = experiment(tmp_path, {"decision-24h.md": h24()}, evidence=("metrics.md",))
    errors = vd.validate_file(exps / "demo-20261111" / "decision-24h.md")
    assert errors == [f"{exps / 'demo-20261111' / 'decision-24h.md'}: "
                      "evidence link to cost.md points to a missing file: cost.md"]


@pytest.mark.parametrize("decider", ["agent:chatgpt", "agent:claude", ""])
def test_non_human_decider_fails(tmp_path, decider):
    exps = experiment(tmp_path, {"decision-24h.md": h24(decided_by=decider)})
    errors = vd.validate_file(exps / "demo-20261111" / "decision-24h.md")
    assert any("human" in e for e in errors), errors


@pytest.mark.parametrize("recorded,message", [
    ("", "missing date"),
    ("tomorrow", "ISO date"),
    ("2026-13-40", "invalid date"),
])
def test_bad_date_fails(tmp_path, recorded, message):
    exps = experiment(tmp_path, {"decision-24h.md": h24(recorded=recorded)})
    errors = vd.validate_file(exps / "demo-20261111" / "decision-24h.md")
    assert any(message in e for e in errors), errors


def test_duplicate_verdict_field_fails(tmp_path):
    text = h48() + "\n| Final verdict | BUILD |\n"
    exps = experiment(tmp_path, {"decision-48h.md": text})
    errors = vd.validate_file(exps / "demo-20261111" / "decision-48h.md")
    assert any("verdict is given more than once" in e for e in errors)


def test_front_matter_record_passes(tmp_path):
    text = "---\nverdict: INCONCLUSIVE\ndecided_by: human\ndate: 2026-11-13\n---\n# Hour 48\n" + EVIDENCE
    exps = experiment(tmp_path, {"decision-48h.md": text})
    assert vd.validate_file(exps / "demo-20261111" / "decision-48h.md") == []


def test_readme_record_names_are_checked(tmp_path):
    exps = experiment(tmp_path, {"HOUR_24.md": h24(decision="CONTINUE"), "HOUR_48.md": h48()})
    result = run(exps)
    assert result.returncode == 1
    assert "HOUR_24.md: verdict 'CONTINUE' is not allowed" in result.stdout
    assert "HOUR_48.md" not in result.stdout


def test_no_records_passes(tmp_path):
    result = run(tmp_path / "experiments")
    assert result.returncode == 0
    assert "nothing to validate" in result.stdout


def test_template_ci_runs_validator():
    assert "run: python3 scripts/validate_decisions.py experiments" in CI.read_text(encoding="utf-8")
