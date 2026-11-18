"""Tests for scripts/experiment_cost.py (run: python -m pytest -q tests/test_experiment_cost.py)."""
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

TEMPLATE = Path(__file__).resolve().parents[1]
SCRIPT = TEMPLATE / "scripts" / "experiment_cost.py"
COST_LOG_TEMPLATE = TEMPLATE / "docs" / "templates" / "COST_LOG_TEMPLATE.md"
EXPERIMENT = "demo-20261111"


def load():
    spec = importlib.util.spec_from_file_location("experiment_cost", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True  # keep scripts/__pycache__ out of the template tree
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


ec = load()


def run(run_id, cost="absent", issue=12, **tokens):
    record = {"run_id": run_id, "issue": issue,
              "tokens": {"input": 100, "output": 20, "cache_read": 1000, "cache_creation": 5}}
    record["tokens"].update(tokens)
    if cost != "absent":
        record["cost_usd"] = cost
    return json.dumps(record)


def cost_log(*rows):
    """The real COST_LOG_TEMPLATE with data rows inserted under the log table."""
    text = COST_LOG_TEMPLATE.read_text(encoding="utf-8")
    separator = "|---|---|---|---|---|---|---|---|---|\n"
    assert separator in text
    body = "".join(
        f"| 2026-11-11 | #12 | agent:human | {service} | {what} | | | {actual} | invoice |\n"
        for service, what, actual in rows)
    return text.replace(separator, separator + body, 1)


@pytest.fixture
def folder(tmp_path):
    path = tmp_path / "experiments" / EXPERIMENT
    path.mkdir(parents=True)
    return path


def write(folder, runs=None, log=None, log_name="cost-log.md"):
    if runs is not None:
        (folder / "runs.jsonl").write_text("\n".join(runs) + "\n", encoding="utf-8")
    if log is not None:
        (folder / log_name).write_text(log, encoding="utf-8")


def report(folder):
    return (folder / "cost.md").read_text(encoding="utf-8")


def field(md, name):
    for line in md.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 2 and cells[0] == name:
            return cells[1]
    raise AssertionError(f"field {name!r} not in report")


# --------------------------------------------------------------------------- totals

def test_known_costs_roll_up_runs_and_manual_log(folder, capsys):
    write(folder, runs=[run("zs-1", 1.25), run("zs-2", 0.5, input=900)],
          log=cost_log(("Cloudflare", "domain", "10.00"), ("Ads", "launch", "$3.5")))
    assert ec.main([str(folder)]) == 0
    md = report(folder)
    assert field(md, "Dollar total") == "USD 15.25"
    assert "lower bound" not in md
    assert field(md, "Agent runs (known cost)") == "USD 1.75"
    assert field(md, "Manual log (known cost)") == "USD 13.50"
    assert field(md, "Agent runs with unknown cost") == "0"
    assert field(md, "Manual entries with unknown cost") == "0"
    assert "UNDER CAP: USD 15.25 of USD 25.00 (USD 9.75 remaining)." in md
    assert "OVER CAP" not in md
    # token totals: input 100 + 900, output 2*20, cache_read 2*1000, cache_creation 2*5
    assert "| 1,000 | 40 | 2,000 | 10 | 3,050 |" in md
    out = capsys.readouterr().out
    assert "total: USD 15.25" in out and f"wrote {folder / 'cost.md'}" in out


def test_template_header_rows_and_running_totals_are_not_counted(folder):
    # The unfilled template has no data rows; the "Running totals" table has no
    # "Actual USD" column and its "25.00" cap must not be read as spend.
    write(folder, log=COST_LOG_TEMPLATE.read_text(encoding="utf-8"))
    assert ec.main([str(folder)]) == 0
    md = report(folder)
    assert field(md, "Dollar total") == "USD 0.00"
    assert field(md, "Manual entries") == "0"
    assert "runs.jsonl` missing" in md


def test_output_is_deterministic(folder):
    write(folder, runs=[run("zs-1", 2)], log=cost_log(("Hosting", "pages", "1")))
    assert ec.main([str(folder)]) == 0
    first = report(folder)
    assert ec.main([str(folder)]) == 0
    assert report(folder) == first


# --------------------------------------------------------------------------- unknown cost

def test_unknown_costs_are_counted_and_total_is_a_lower_bound(folder, capsys):
    write(folder,
          runs=[run("zs-1", 2.0), run("zs-sub"), run("zs-null", None)],
          log=cost_log(("Claude", "subscription", "NOT AVAILABLE"), ("Hosting", "pages", ""),
                       ("Ads", "launch", "4")))
    assert ec.main([str(folder)]) == 0
    md = report(folder)
    assert field(md, "Dollar total") == "USD 6.00 (lower bound)"
    assert field(md, "Agent runs") == "3"
    assert field(md, "Agent runs with unknown cost") == "2"
    assert field(md, "Manual entries") == "3"
    assert field(md, "Manual entries with unknown cost") == "2"
    assert "UNDER CAP (unconfirmed)" in md and "4 cost(s) unknown" in md
    assert "total: USD 6.00 (lower bound)" in capsys.readouterr().out


def test_unknown_cost_is_never_counted_as_zero_in_summary():
    runs, problems = ec.parse_runs(run("a") + "\n" + run("b", 0) + "\n")
    assert problems == []
    assert runs[0]["cost_usd"] is None and runs[1]["cost_usd"] == Decimal("0")
    summary = ec.summarise(runs, [], Decimal("25"))
    assert summary["unknown_runs"] == 1 and summary["lower_bound"] is True


def test_legacy_cost_log_name_is_read(folder):
    write(folder, log=cost_log(("Hosting", "pages", "7.10")), log_name="COST_LOG.md")
    assert ec.main([str(folder)]) == 0
    md = report(folder)
    assert field(md, "Dollar total") == "USD 7.10"
    assert "`COST_LOG.md` (1 valid, 0 skipped)" in md


# --------------------------------------------------------------------------- malformed input

def test_malformed_lines_are_skipped_and_reported(folder, capsys):
    write(folder, runs=[
        run("zs-1", 1.5),
        "{not json",
        "[1, 2]",
        json.dumps({"issue": 1, "tokens": {}}),                      # missing run_id
        run("zs-2", -1),                                             # negative cost
        run("zs-3", "1.00"),                                         # cost as string
        run("zs-4", 1, input=-5),                                    # negative tokens
        json.dumps({"run_id": "zs-5", "issue": 1}),                  # missing tokens
        run("zs-1", 9),                                              # duplicate run_id
        "",
        run("zs-6", 0.25),
    ], log=cost_log(("Ads", "launch", "about 3"), ("Hosting", "pages", "2")))
    assert ec.main([str(folder)]) == 0
    md = report(folder)
    assert field(md, "Dollar total") == "USD 3.75"
    assert field(md, "Agent runs") == "2"
    skipped = md.split("## Skipped input", 1)[1]
    for expected in ["runs.jsonl:2: invalid JSON", "runs.jsonl:3: not a JSON object",
                     "runs.jsonl:4: run_id must be a non-empty string",
                     "runs.jsonl:5: cost_usd must be a non-negative number",
                     "runs.jsonl:6: cost_usd must be a number or null",
                     "runs.jsonl:7: tokens.input must be a non-negative integer",
                     "runs.jsonl:8: tokens must be an object",
                     "runs.jsonl:9: duplicate run_id 'zs-1'",
                     "cost-log.md:", "is not a number or NOT AVAILABLE"]:
        assert expected in skipped
    assert "`runs.jsonl` (2 valid, 8 skipped)" in md
    err = capsys.readouterr().err
    assert "experiment_cost: skipped runs.jsonl:2: invalid JSON" in err


def test_cost_log_row_with_wrong_column_count_is_reported():
    text = "| What | Actual USD |\n|---|---|\n| ads | 5 |\n| broken |\n"
    entries, problems = ec.parse_cost_log(text, "cost-log.md")
    assert [e["cost_usd"] for e in entries] == [Decimal("5")]
    assert problems == ["cost-log.md:4: expected 2 columns, got 1"]


@pytest.mark.parametrize("cell, expected", [
    ("12", Decimal("12")), ("12.50", Decimal("12.50")), ("$1,200.00", Decimal("1200.00")),
    ("USD 3.20", Decimal("3.20")), ("3.20 USD", Decimal("3.20")), ("**4**", Decimal("4")),
    ("NOT AVAILABLE", None), ("not measured", None), ("", None), ("Unknown", None),
])
def test_parse_amount(cell, expected):
    assert ec.parse_amount(cell) == expected


@pytest.mark.parametrize("cell", ["-3", "~5", "5-10", "free"])
def test_parse_amount_rejects_non_amounts(cell):
    with pytest.raises(ValueError):
        ec.parse_amount(cell)


# --------------------------------------------------------------------------- cap

def test_over_cap_prints_explicit_line(folder, capsys):
    write(folder, runs=[run("zs-1", 20)], log=cost_log(("Ads", "launch", "6.50")))
    assert ec.main([str(folder)]) == 0
    md = report(folder)
    over = [line for line in md.splitlines() if line.startswith("OVER CAP")]
    assert over == ["OVER CAP: USD 26.50 spent vs USD 25.00 cap (USD 1.50 over). "
                    "Stop and escalate to agent:human."]
    assert "UNDER CAP" not in md
    assert "OVER CAP" in capsys.readouterr().out


def test_over_cap_with_unknown_costs_is_still_over_cap(folder):
    write(folder, runs=[run("zs-1", 30), run("zs-2")])
    assert ec.main([str(folder)]) == 0
    md = report(folder)
    assert "OVER CAP: USD 30.00 (at least) spent" in md
    assert field(md, "Dollar total") == "USD 30.00 (lower bound)"


def test_exactly_at_cap_is_not_over(folder):
    write(folder, runs=[run("zs-1", 25)])
    assert ec.main([str(folder)]) == 0
    md = report(folder)
    assert "OVER CAP" not in md
    assert "UNDER CAP: USD 25.00 of USD 25.00 (USD 0.00 remaining)." in md


def test_custom_cap(folder):
    write(folder, runs=[run("zs-1", 6)])
    assert ec.main([str(folder), "--cap", "5"]) == 0
    assert "OVER CAP: USD 6.00 spent vs USD 5.00 cap" in report(folder)


@pytest.mark.parametrize("cap", ["0", "-1", "abc", "nan"])
def test_invalid_cap_is_usage_error(folder, cap):
    write(folder, runs=[run("zs-1", 1)])
    with pytest.raises(SystemExit) as exc:
        ec.main([str(folder), "--cap", cap])
    assert exc.value.code == 2
    assert not (folder / "cost.md").exists()


# --------------------------------------------------------------------------- missing input

def test_missing_folder_or_inputs_fail_without_writing(tmp_path, folder, capsys):
    assert ec.main([str(tmp_path / "nope")]) == 1
    assert "does not exist" in capsys.readouterr().err
    assert ec.main([str(folder)]) == 1
    assert "has neither runs.jsonl nor" in capsys.readouterr().err
    assert not (folder / "cost.md").exists()


def test_cli_offline_run(folder):
    write(folder, runs=[run("zs-1", 26)])
    result = subprocess.run([sys.executable, "-B", str(SCRIPT), str(folder)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert "OVER CAP" in result.stdout
    assert (folder / "cost.md").is_file()


def test_script_makes_no_network_or_gh_calls():
    source = SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("urllib", "http.client", "socket", "subprocess", "requests"):
        assert f"import {forbidden}" not in source
