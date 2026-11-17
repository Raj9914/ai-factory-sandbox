#!/usr/bin/env python3
"""Validate hour-24 and hour-48 experiment decision records (stdlib only, offline).

Usage: python3 scripts/validate_decisions.py [EXPERIMENTS_DIR]   (default: experiments)

Checks every `experiments/*/decision-24h.md` and `experiments/*/decision-48h.md` (and the
`HOUR_24.md` / `HOUR_48.md` names listed in experiments/README.md). Each record must have:

- exactly one verdict from the allowed set:
    hour 24: KEEP_SCOPE or CUT_SCOPE
    hour 48: BUILD, PIVOT, KILL or INCONCLUSIVE
  Any other verdict (for example the legacy CONTINUE) fails, as do two verdicts.
- a human decider (`Decided by` = `agent:human`, `human` or `human:<name>`);
- a date (`Recorded at (ISO)`, `Hour 0 / recorded at (ISO)` or `Date`) containing a valid
  ISO date, YYYY-MM-DD;
- evidence: Markdown links to `metrics.md` and `cost.md`. Relative links must point to files
  that exist.

Fields are read from the `| Field | Value |` table used by docs/templates/HOUR_24_DECISION_TEMPLATE.md
and HOUR_48_DECISION_TEMPLATE.md, or from simple `key: value` front matter between `---` lines
(keys `verdict`, `decided_by`, `date`). A field given more than once fails.

If no decision records exist yet the validator reports that and passes.
"""
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
import re
import sys

DEFAULT_EXPERIMENTS = Path("experiments")
ALLOWED = {
    24: ("KEEP_SCOPE", "CUT_SCOPE"),
    48: ("BUILD", "PIVOT", "KILL", "INCONCLUSIVE"),
}
FILENAMES = {
    "decision-24h.md": 24,
    "HOUR_24.md": 24,
    "decision-48h.md": 48,
    "HOUR_48.md": 48,
}
REQUIRED_EVIDENCE = ("metrics.md", "cost.md")

# Normalised field label -> canonical field name.
LABELS = {
    "decision": "verdict",
    "final verdict": "verdict",
    "verdict": "verdict",
    "decided by": "decided_by",
    "decided_by": "decided_by",
    "decider": "decided_by",
    "recorded at (iso)": "date",
    "hour 0 / recorded at (iso)": "date",
    "recorded at": "date",
    "recorded_at": "date",
    "date": "date",
}

VERDICT_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Z][A-Z_]{2,}(?![A-Za-z0-9_])")
ISO_DATE_RE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
HUMAN_RE = re.compile(r"^(?:agent:)?human(?::\S+)?$")
LINK_RE = re.compile(r"\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")


@dataclass
class Record:
    fields: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)


def _clean(text: str) -> str:
    return re.sub(r"[*`]", "", text).strip()


def _add(record: Record, label: str, value: str) -> None:
    name = LABELS.get(_clean(label).lower())
    if name is None:
        return
    if name in record.fields:
        record.errors.append(f"{name} is given more than once")
        return
    record.fields[name] = _clean(value)


def parse_record(text: str) -> Record:
    record = Record()
    lines = text.replace("\r\n", "\n").split("\n")
    start = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                for line in lines[1:i]:
                    if ":" in line and not line.lstrip().startswith("#"):
                        key, _, value = line.partition(":")
                        _add(record, key, value.strip().strip("\"'"))
                start = i + 1
                break
    for line in lines[start:]:
        m = TABLE_ROW_RE.match(line)
        if not m:
            continue
        cells = [c.strip() for c in m.group(1).split("|")]
        if len(cells) >= 2:
            _add(record, cells[0], cells[1])
    return record


def _check_verdict(value, hour: int) -> list[str]:
    allowed = ALLOWED[hour]
    choices = " / ".join(allowed)
    if not value:
        return [f"missing verdict (hour {hour} allows exactly one of {choices})"]
    tokens = VERDICT_TOKEN_RE.findall(value)
    if not tokens:
        return [f"no verdict in {value!r} (hour {hour} allows exactly one of {choices})"]
    errors = [f"verdict {t!r} is not allowed at hour {hour} (allowed: {choices})"
              for t in dict.fromkeys(tokens) if t not in allowed]
    if errors:
        return errors
    if len(set(tokens)) > 1:
        return [f"exactly one verdict required, found {', '.join(dict.fromkeys(tokens))}"]
    return []


def _check_decider(value) -> list[str]:
    if not value:
        return ["missing 'Decided by' (must be a human)"]
    if not HUMAN_RE.fullmatch(value.strip().lower()):
        return [f"decider must be a human (agent:human), got {value!r}"]
    return []


def _check_date(value) -> list[str]:
    if not value:
        return ["missing date (Recorded at, ISO YYYY-MM-DD)"]
    m = ISO_DATE_RE.search(value)
    if not m:
        return [f"date must contain an ISO date YYYY-MM-DD, got {value!r}"]
    try:
        date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return [f"invalid date {m.group(0)!r}"]
    return []


def _check_evidence(text: str, base: Path) -> list[str]:
    errors = []
    targets = [t for t in LINK_RE.findall(text)]
    for name in REQUIRED_EVIDENCE:
        matches = []
        for target in targets:
            path_part = target.split("#", 1)[0].split("?", 1)[0]
            if path_part.rstrip("/").rsplit("/", 1)[-1].lower() == name:
                matches.append(path_part)
        if not matches:
            errors.append(f"missing evidence link to {name}")
            continue
        local = [p for p in matches if not re.match(r"^[a-z][a-z0-9+.-]*:", p, re.I)]
        if local and len(local) == len(matches) and not any((base / p).is_file() for p in local):
            errors.append(f"evidence link to {name} points to a missing file: {local[0]}")
    return errors


def validate_text(text: str, hour: int, base: Path) -> list[str]:
    record = parse_record(text)
    errors = list(record.errors)
    errors += _check_verdict(record.fields.get("verdict"), hour)
    errors += _check_decider(record.fields.get("decided_by"))
    errors += _check_date(record.fields.get("date"))
    errors += _check_evidence(text, base)
    return errors


def validate_file(path: Path) -> list[str]:
    path = Path(path)
    hour = FILENAMES.get(path.name)
    if hour is None:
        return [f"{path}: not a decision record name ({', '.join(FILENAMES)})"]
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return [f"{path}: not UTF-8 text"]
    return [f"{path}: {e}" for e in validate_text(text, hour, path.parent)]


def find_records(experiments: Path) -> list[Path]:
    experiments = Path(experiments)
    if not experiments.is_dir():
        return []
    return sorted(p for name in FILENAMES for p in experiments.glob(f"*/{name}") if p.is_file())


def main(argv: list[str]) -> int:
    experiments = Path(argv[1]) if len(argv) > 1 else DEFAULT_EXPERIMENTS
    records = find_records(experiments)
    if not records:
        print(f"No decision records under {experiments}; nothing to validate")
        return 0
    errors = [e for path in records for e in validate_file(path)]
    if errors:
        print("\n".join(errors))
        return 1
    print(f"Decision records valid ({len(records)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
