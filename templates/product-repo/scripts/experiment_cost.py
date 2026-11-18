#!/usr/bin/env python3
"""Roll up an experiment's agent run records and manual cost log into experiments/<id>/cost.md.

Usage:
    python3 scripts/experiment_cost.py experiments/<id> [--cap 25]

Stdlib only and offline: no network, `gh` or provider billing calls. Inputs, both inside the
experiment folder:

- `runs.jsonl`: one JSON object per agent (Zeroshot) run, for example
      {"run_id": "zs-123", "issue": 42, "tokens": {"input": 1200, "output": 300,
       "cache_read": 5000, "cache_creation": 0}, "cost_usd": 0.42}
  `run_id` (non-empty string) and `tokens` (object; each of `input`, `output`, `cache_read`,
  `cache_creation` a non-negative integer, a missing key counts as 0) are required. `issue` is
  an issue number or reference. `cost_usd` is optional: a non-negative number when the provider
  reported a dollar cost; absent or `null` means unknown (e.g. subscription runs). Blank lines
  are ignored. Malformed lines and duplicate `run_id`s are skipped and reported.
- `cost-log.md` (or the older `COST_LOG.md`): the manual log from
  `docs/templates/COST_LOG_TEMPLATE.md`. Every Markdown table with an `Actual USD` column is
  read. An empty cell or `NOT AVAILABLE` / `NOT MEASURED` / `UNKNOWN` is an unknown cost; a
  number (optionally prefixed by `$` or `USD`) is a known cost; anything else is skipped and
  reported.

Rules: unknown costs are counted separately and never treated as 0. If any cost is unknown,
the dollar total is marked a lower bound. A known total above the cap gives an explicit
`OVER CAP` line. Missing inputs are reported in cost.md, not guessed.

Exit codes: 0 written, 1 invalid input (nothing written), 2 usage error.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import sys

DEFAULT_CAP = Decimal("25")
TOKEN_KEYS = ("input", "output", "cache_read", "cache_creation")
RUNS_FILE = "runs.jsonl"
COST_LOG_FILES = ("cost-log.md", "COST_LOG.md")
OUTPUT_FILE = "cost.md"
ACTUAL_COLUMN = "actual usd"
UNKNOWN_MARKERS = {"", "not available", "not measured", "unknown", "n/a", "-", "—"}
AMOUNT_RE = re.compile(r"^(?:usd\s*|\$\s*)?(\d+(?:\.\d+)?)(?:\s*usd)?$", re.IGNORECASE)


class CostError(Exception):
    """Invalid input; reported as one line, exit code 1."""


# --------------------------------------------------------------------------- runs.jsonl

def _is_count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def parse_cost(value):
    """Return a non-negative Decimal, None for unknown, or raise ValueError."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"cost_usd must be a number or null, got {value!r}")
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"cost_usd must be a non-negative number, got {value!r}")
    return amount


def parse_run(line: str) -> dict:
    try:
        record = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON ({exc.msg})") from None
    if not isinstance(record, dict):
        raise ValueError("not a JSON object")
    run_id = record.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id must be a non-empty string")
    issue = record.get("issue")
    if isinstance(issue, bool) or not isinstance(issue, (int, str)) or issue == "":
        raise ValueError("issue must be an issue number or reference")
    tokens = record.get("tokens")
    if not isinstance(tokens, dict):
        raise ValueError("tokens must be an object")
    counts = {}
    for key in TOKEN_KEYS:
        value = tokens.get(key, 0)
        if not _is_count(value):
            raise ValueError(f"tokens.{key} must be a non-negative integer, got {value!r}")
        counts[key] = value
    return {"run_id": run_id.strip(), "issue": issue, "tokens": counts,
            "cost_usd": parse_cost(record.get("cost_usd"))}


def parse_runs(text: str) -> tuple[list, list]:
    """Return (runs, problems); problems are 'runs.jsonl:<line>: <reason>' strings."""
    runs, problems, seen = [], [], set()
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            run = parse_run(line)
        except ValueError as exc:
            problems.append(f"{RUNS_FILE}:{number}: {exc}")
            continue
        if run["run_id"] in seen:
            problems.append(f"{RUNS_FILE}:{number}: duplicate run_id {run['run_id']!r}")
            continue
        seen.add(run["run_id"])
        runs.append(run)
    return runs, problems


# --------------------------------------------------------------------------- manual cost log

def _cells(line: str) -> list:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [cell.strip() for cell in body.split("|")]


def _is_separator(cells: list) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-+:?", cell) for cell in cells)


def parse_amount(cell: str):
    """Return a Decimal, None for unknown, or raise ValueError."""
    text = cell.strip().strip("*_`").strip()
    if text.lower() in UNKNOWN_MARKERS:
        return None
    match = AMOUNT_RE.match(text.replace(",", ""))
    if not match:
        raise ValueError(f"Actual USD {cell!r} is not a number or NOT AVAILABLE")
    return Decimal(match.group(1))


def parse_cost_log(text: str, name: str) -> tuple[list, list]:
    """Return (entries, problems) from every table that has an 'Actual USD' column."""
    entries, problems = [], []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip().startswith("|") or index + 1 >= len(lines):
            index += 1
            continue
        header = _cells(line)
        if not _is_separator(_cells(lines[index + 1])):
            index += 1
            continue
        lowered = [cell.lower() for cell in header]
        index += 2
        if ACTUAL_COLUMN not in lowered:
            continue
        column = lowered.index(ACTUAL_COLUMN)
        while index < len(lines) and lines[index].strip().startswith("|"):
            number = index + 1
            cells = _cells(lines[index])
            index += 1
            if not any(cells):
                continue
            if len(cells) != len(header):
                problems.append(f"{name}:{number}: expected {len(header)} columns, "
                                f"got {len(cells)}")
                continue
            try:
                amount = parse_amount(cells[column])
            except ValueError as exc:
                problems.append(f"{name}:{number}: {exc}")
                continue
            entries.append({"line": number, "row": dict(zip(header, cells)), "cost_usd": amount})
    return entries, problems


# --------------------------------------------------------------------------- report

def summarise(runs: list, manual: list, cap: Decimal) -> dict:
    known_runs = [r["cost_usd"] for r in runs if r["cost_usd"] is not None]
    known_manual = [e["cost_usd"] for e in manual if e["cost_usd"] is not None]
    runs_total = sum(known_runs, Decimal("0"))
    manual_total = sum(known_manual, Decimal("0"))
    total = runs_total + manual_total
    unknown_runs = len(runs) - len(known_runs)
    unknown_manual = len(manual) - len(known_manual)
    tokens = {key: sum(r["tokens"][key] for r in runs) for key in TOKEN_KEYS}
    return {
        "runs": len(runs),
        "manual_entries": len(manual),
        "runs_total": runs_total,
        "manual_total": manual_total,
        "total": total,
        "unknown_runs": unknown_runs,
        "unknown_manual": unknown_manual,
        "lower_bound": unknown_runs + unknown_manual > 0,
        "tokens": tokens,
        "cap": cap,
        "over_cap": total > cap,
    }


def usd(amount: Decimal) -> str:
    return f"USD {amount.quantize(Decimal('0.01')):,}"


def cap_status(summary: dict) -> str:
    total, cap = summary["total"], summary["cap"]
    if summary["over_cap"]:
        qualifier = " (at least)" if summary["lower_bound"] else ""
        return (f"OVER CAP: {usd(total)}{qualifier} spent vs {usd(cap)} cap "
                f"({usd(total - cap)} over). Stop and escalate to agent:human.")
    unknown = summary["unknown_runs"] + summary["unknown_manual"]
    if summary["lower_bound"]:
        return (f"UNDER CAP (unconfirmed): known spend {usd(total)} of {usd(cap)}; "
                f"{unknown} cost(s) unknown, so actual spend may be higher.")
    return f"UNDER CAP: {usd(total)} of {usd(cap)} ({usd(cap - total)} remaining)."


def render_markdown(experiment: str, summary: dict, sources: dict, problems: list) -> str:
    total_label = usd(summary["total"])
    if summary["lower_bound"]:
        total_label += " (lower bound)"
    tokens = summary["tokens"]
    lines = [
        f"# {experiment} — Cost",
        "",
        "Generated by `scripts/experiment_cost.py`. Do not edit by hand; re-run the script.",
        "Figures are actual costs from run records and the manual cost log; unknown costs are",
        "counted separately and never treated as 0.",
        "",
        "## Cap status",
        "",
        cap_status(summary),
        "",
        "## Spend",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Dollar total | {total_label} |",
        f"| Agent runs (known cost) | {usd(summary['runs_total'])} |",
        f"| Manual log (known cost) | {usd(summary['manual_total'])} |",
        f"| Agent runs | {summary['runs']} |",
        f"| Agent runs with unknown cost | {summary['unknown_runs']} |",
        f"| Manual entries | {summary['manual_entries']} |",
        f"| Manual entries with unknown cost | {summary['unknown_manual']} |",
        f"| Planning cap | {usd(summary['cap'])} per experiment |",
        "",
        "## Tokens (agent runs)",
        "",
        "| Input | Output | Cache read | Cache creation | Total |",
        "|---|---|---|---|---|",
        f"| {tokens['input']:,} | {tokens['output']:,} | {tokens['cache_read']:,} "
        f"| {tokens['cache_creation']:,} | {sum(tokens.values()):,} |",
        "",
        "## Sources",
        "",
        f"- Agent runs: {sources['runs']}",
        f"- Manual cost log: {sources['manual']}",
        "",
    ]
    if problems:
        lines += ["## Skipped input", "",
                  "These lines were malformed and are not included in any total:", ""]
        lines += [f"- {problem}" for problem in problems]
        lines.append("")
    return "\n".join(lines)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CostError(f"cannot read {path}: {exc}") from None


def build(folder: Path, cap: Decimal) -> tuple[str, dict, list]:
    if not folder.is_dir():
        raise CostError(f"experiment folder {folder} does not exist")
    runs_path = folder / RUNS_FILE
    log_path = next((folder / name for name in COST_LOG_FILES if (folder / name).is_file()), None)
    if not runs_path.is_file() and log_path is None:
        raise CostError(f"{folder} has neither {RUNS_FILE} nor {' / '.join(COST_LOG_FILES)}")
    problems = []
    runs, manual = [], []
    if runs_path.is_file():
        runs, run_problems = parse_runs(_read(runs_path))
        problems += run_problems
        runs_source = f"`{RUNS_FILE}` ({len(runs)} valid, {len(run_problems)} skipped)"
    else:
        runs_source = f"NOT AVAILABLE (`{RUNS_FILE}` missing)"
    if log_path is not None:
        manual, log_problems = parse_cost_log(_read(log_path), log_path.name)
        problems += log_problems
        manual_source = f"`{log_path.name}` ({len(manual)} valid, {len(log_problems)} skipped)"
    else:
        manual_source = f"NOT AVAILABLE (`{COST_LOG_FILES[0]}` missing)"
    summary = summarise(runs, manual, cap)
    text = render_markdown(folder.name, summary, {"runs": runs_source, "manual": manual_source},
                           problems)
    return text, summary, problems


def _cap(value: str) -> Decimal:
    try:
        cap = Decimal(value)
    except InvalidOperation:
        raise argparse.ArgumentTypeError(f"invalid cap {value!r}") from None
    if not cap.is_finite() or cap <= 0:
        raise argparse.ArgumentTypeError(f"cap must be a positive number, got {value!r}")
    return cap


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("experiment", type=Path, help="experiment folder, e.g. experiments/<id>")
    parser.add_argument("--cap", type=_cap, default=DEFAULT_CAP,
                        help="planning cap in USD per experiment (default: 25)")
    args = parser.parse_args(argv)
    try:
        text, summary, problems = build(args.experiment, args.cap)
    except CostError as exc:
        print(f"experiment_cost: {exc}", file=sys.stderr)
        return 1
    output = args.experiment / OUTPUT_FILE
    output.write_text(text, encoding="utf-8")
    for problem in problems:
        print(f"experiment_cost: skipped {problem}", file=sys.stderr)
    total = usd(summary["total"]) + (" (lower bound)" if summary["lower_bound"] else "")
    print(f"total: {total}")
    print(cap_status(summary))
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
