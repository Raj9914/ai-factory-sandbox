#!/usr/bin/env python3
"""Pull funnel numerators/denominators for an experiment into experiments/<id>/metrics.md.

Usage:
    python3 scripts/experiment_metrics.py --experiment <id> --since <iso> [--until <iso>]
        [--fixture COUNTS.json] [--experiments-dir experiments] [--events analytics/events.json]

Stdlib only. Reads the event spec (`analytics/events.json`) and the experiment's pre-registered
thresholds (`experiments/<id>/experiment.yml`), gets the number of unique users per event since
`--since`, and writes `experiments/<id>/metrics.md` and `experiments/<id>/metrics.json`
(counts, rates, sample size, threshold met?). It does not write a verdict: the hour-24/48
decision is drafted with the `48h-decision` skill and made by a human.

Data source:
- `--fixture COUNTS.json`: offline mode for tests and dry runs. The file is
  `{"counts": {"<event>": <unique users>, ...}}`. A spec event missing from the file counts as 0.
- otherwise PostHog's query API (HogQL), read-only. Configuration comes from the environment and
  is never written to the output or committed:
    POSTHOG_PERSONAL_API_KEY  personal API key with read-only `query:read` scope (required)
    POSTHOG_PROJECT_ID        numeric project ID (required)
    POSTHOG_HOST              API host, default https://us.posthog.com
  Events with `internal = true` (team/test traffic, see analytics/events.json) are excluded.

experiment.yml schema (a small YAML subset: `key: value` scalars, one nested `metrics:` list of
mappings, `#` comments; no anchors, flow collections or multi-line strings):

    id: demo-20261111                 # must equal the folder name / --experiment
    hour_0: 2026-11-11T09:00:00Z      # experiment start (ISO 8601, with timezone)
    metrics:
      - name: activation_rate         # snake_case, unique
        label: Activation rate        # optional, shown in metrics.md
        numerator: activation         # event from analytics/events.json (unique users)
        denominator: app_opened       # event from analytics/events.json (unique users)
        threshold: 0.25               # pre-registered rate, 0..1
        direction: at_least           # optional: at_least (default) or at_most
        min_sample: 30                # optional: denominator below this is INSUFFICIENT DATA

Rules: a zero denominator (or one below `min_sample`) yields `INSUFFICIENT DATA` and never a
rate or a threshold result. Small samples are directional only; no significance is claimed.

Exit codes: 0 written, 1 invalid input or query failure (nothing written), 2 usage error.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request

INSUFFICIENT = "INSUFFICIENT DATA"
DIRECTIONS = ("at_least", "at_most")
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
DEFAULT_HOST = "https://us.posthog.com"
QUERY_TIMEOUT_S = 30


class MetricsError(Exception):
    """Invalid input or a failed query; reported as one line, exit code 1."""


# --------------------------------------------------------------------------- experiment.yml

def _scalar(raw: str):
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    if value == "":
        return None
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"-?(\d+\.\d*|\.\d+)", value):
        return float(value)
    return value


def _strip_comment(line: str) -> str:
    quote = None
    for index, char in enumerate(line):
        if quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "#" and (index == 0 or line[index - 1] in " \t"):
            return line[:index]
    return line


def parse_experiment_yaml(text: str) -> dict:
    """Parse the experiment.yml subset documented in the module docstring."""
    data: dict = {}
    current_list = None   # list being filled under a top-level key
    current_item = None   # mapping being filled inside that list
    item_indent = None
    for number, raw in enumerate(text.replace("\r\n", "\n").split("\n"), 1):
        line = _strip_comment(raw).rstrip()
        if not line.strip():
            continue
        if "\t" in line[: len(line) - len(line.lstrip())]:
            raise MetricsError(f"experiment.yml:{number}: use spaces, not tabs, for indentation")
        indent = len(line) - len(line.lstrip(" "))
        body = line.strip()
        if indent == 0:
            key, sep, value = body.partition(":")
            if not sep or not NAME_RE.match(key.strip()):
                raise MetricsError(f"experiment.yml:{number}: expected 'key: value', got {body!r}")
            key = key.strip()
            if key in data:
                raise MetricsError(f"experiment.yml:{number}: duplicate key {key!r}")
            if value.strip():
                data[key] = _scalar(value)
                current_list = current_item = None
            else:
                current_list = data[key] = []
                current_item = item_indent = None
            continue
        if current_list is None:
            raise MetricsError(f"experiment.yml:{number}: unexpected indentation")
        if body.startswith("- "):
            current_item = {}
            current_list.append(current_item)
            item_indent = indent + 2
            body = body[2:].strip()
        elif current_item is None or indent != item_indent:
            raise MetricsError(f"experiment.yml:{number}: expected a list item ('- key: value')")
        key, sep, value = body.partition(":")
        if not sep or not NAME_RE.match(key.strip()):
            raise MetricsError(f"experiment.yml:{number}: expected 'key: value', got {body!r}")
        key = key.strip()
        if key in current_item:
            raise MetricsError(f"experiment.yml:{number}: duplicate key {key!r}")
        current_item[key] = _scalar(value)
    return data


def parse_iso(value, what: str) -> datetime:
    if not isinstance(value, str):
        raise MetricsError(f"{what} must be an ISO 8601 timestamp, got {value!r}")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise MetricsError(f"{what} is not an ISO 8601 timestamp: {value!r}") from None
    if moment.tzinfo is None:
        raise MetricsError(f"{what} must include a timezone (e.g. Z): {value!r}")
    return moment.astimezone(timezone.utc)


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_events(path: Path) -> set:
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MetricsError(f"cannot read event spec {path}: {exc}") from None
    events = spec.get("events") if isinstance(spec, dict) else None
    if not isinstance(events, dict) or not events:
        raise MetricsError(f"event spec {path} has no 'events' object")
    return set(events)


def load_experiment(path: Path, experiment_id: str, events: set) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MetricsError(f"cannot read {path}: {exc}") from None
    data = parse_experiment_yaml(text)
    errors = []
    if data.get("id") != experiment_id:
        errors.append(f"id must equal the experiment folder {experiment_id!r}, got {data.get('id')!r}")
    try:
        data["hour_0"] = parse_iso(data.get("hour_0"), "hour_0")
    except MetricsError as exc:
        errors.append(str(exc))
    metrics = data.get("metrics")
    if not isinstance(metrics, list) or not metrics:
        errors.append("metrics must be a non-empty list")
        metrics = []
    seen = set()
    for index, metric in enumerate(metrics, 1):
        where = f"metrics[{index}]"
        name = metric.get("name")
        if not isinstance(name, str) or not NAME_RE.match(name):
            errors.append(f"{where}.name must be snake_case, got {name!r}")
        elif name in seen:
            errors.append(f"{where}.name {name!r} is duplicated")
        seen.add(name)
        unknown = set(metric) - {"name", "label", "numerator", "denominator", "threshold",
                                 "direction", "min_sample"}
        if unknown:
            errors.append(f"{where} has unknown keys: {', '.join(sorted(unknown))}")
        for part in ("numerator", "denominator"):
            event = metric.get(part)
            if event not in events:
                errors.append(f"{where}.{part} {event!r} is not an event in analytics/events.json")
        threshold = metric.get("threshold")
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) \
                or not 0 <= threshold <= 1:
            errors.append(f"{where}.threshold must be a rate between 0 and 1, got {threshold!r}")
        metric.setdefault("direction", "at_least")
        if metric["direction"] not in DIRECTIONS:
            errors.append(f"{where}.direction must be one of {', '.join(DIRECTIONS)}")
        min_sample = metric.setdefault("min_sample", 1)
        if isinstance(min_sample, bool) or not isinstance(min_sample, int) or min_sample < 1:
            errors.append(f"{where}.min_sample must be a positive integer, got {min_sample!r}")
        if metric.get("label") is None:
            metric["label"] = name
    if errors:
        raise MetricsError(f"{path}: " + "; ".join(errors))
    return data


# --------------------------------------------------------------------------- data sources

def fixture_counts(path: Path, events: set) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MetricsError(f"cannot read fixture {path}: {exc}") from None
    counts = data.get("counts") if isinstance(data, dict) else None
    if not isinstance(counts, dict):
        raise MetricsError(f"fixture {path} must be {{\"counts\": {{\"<event>\": <int>}}}}")
    result = {event: 0 for event in events}
    for event, count in counts.items():
        if event not in events:
            raise MetricsError(f"fixture {path}: {event!r} is not an event in the spec")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise MetricsError(f"fixture {path}: count for {event!r} must be a non-negative integer")
        result[event] = count
    return result


def build_hogql(events: list, since: datetime, until: datetime | None) -> str:
    """Unique users per event in the window, excluding internal traffic."""
    for event in events:
        if not NAME_RE.match(event):  # spec names are snake_case; keeps the query injection-free
            raise MetricsError(f"event name {event!r} is not snake_case")
    names = ", ".join(f"'{event}'" for event in sorted(events))
    window = f"timestamp >= toDateTime('{since.strftime('%Y-%m-%d %H:%M:%S')}', 'UTC')"
    if until is not None:
        window += f" AND timestamp < toDateTime('{until.strftime('%Y-%m-%d %H:%M:%S')}', 'UTC')"
    return (
        "SELECT event, count(DISTINCT person_id) AS users FROM events "
        f"WHERE event IN ({names}) AND {window} "
        "AND coalesce(toString(properties.internal), '') NOT IN ('true', '1') "
        "GROUP BY event"
    )


def posthog_counts(events: set, since: datetime, until: datetime | None,
                   env=None, urlopen=urllib.request.urlopen) -> dict:
    env = os.environ if env is None else env
    key = env.get("POSTHOG_PERSONAL_API_KEY", "").strip()
    project = env.get("POSTHOG_PROJECT_ID", "").strip()
    host = (env.get("POSTHOG_HOST", "").strip() or DEFAULT_HOST).rstrip("/")
    if not key or not project:
        raise MetricsError("set POSTHOG_PERSONAL_API_KEY and POSTHOG_PROJECT_ID (or use --fixture)")
    if not project.isdigit():
        raise MetricsError("POSTHOG_PROJECT_ID must be the numeric project ID")
    if not host.startswith("https://") and not host.startswith("http://127.0.0.1") \
            and not host.startswith("http://localhost"):
        raise MetricsError("POSTHOG_HOST must use https://")
    body = json.dumps({"query": {"kind": "HogQLQuery",
                                 "query": build_hogql(sorted(events), since, until)}}).encode()
    request = urllib.request.Request(
        f"{host}/api/projects/{project}/query/", data=body, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=QUERY_TIMEOUT_S) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise MetricsError(f"PostHog query failed: HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise MetricsError(f"PostHog query failed: {type(exc).__name__}") from None
    rows = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise MetricsError("PostHog query returned no 'results' list")
    result = {event: 0 for event in events}
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) != 2 or row[0] not in result \
                or isinstance(row[1], bool) or not isinstance(row[1], int) or row[1] < 0:
            raise MetricsError(f"PostHog query returned an unexpected row: {row!r}")
        result[row[0]] = row[1]
    return result


# --------------------------------------------------------------------------- report

def compute(experiment: dict, counts: dict) -> list:
    results = []
    for metric in experiment["metrics"]:
        numerator = counts[metric["numerator"]]
        denominator = counts[metric["denominator"]]
        entry = {
            "name": metric["name"],
            "label": metric["label"],
            "numerator_event": metric["numerator"],
            "denominator_event": metric["denominator"],
            "numerator": numerator,
            "denominator": denominator,
            "sample_size": denominator,
            "min_sample": metric["min_sample"],
            "threshold": metric["threshold"],
            "direction": metric["direction"],
        }
        if denominator == 0 or denominator < metric["min_sample"]:
            entry.update(rate=None, threshold_met=None, status=INSUFFICIENT)
        else:
            rate = numerator / denominator
            if metric["direction"] == "at_least":
                met = rate >= metric["threshold"]
            else:
                met = rate <= metric["threshold"]
            entry.update(rate=round(rate, 4), threshold_met=met, status="MET" if met else "NOT MET")
        results.append(entry)
    return results


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def render_markdown(report: dict) -> str:
    window_end = report["until"] or "query time (open window)"
    lines = [
        f"# {report['experiment']} — Metrics",
        "",
        "Generated by `scripts/experiment_metrics.py`. Do not edit by hand; re-run the script.",
        "Counts are unique users per event; internal traffic (`internal=true`) is excluded.",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Experiment | {report['experiment']} |",
        f"| Hour 0 (ISO) | {report['hour_0']} |",
        f"| Window | {report['since']} → {window_end} |",
        f"| Source | {report['source']} |",
        "",
        "## Funnel metrics",
        "",
        "| Metric | Numerator | Denominator | Rate | Sample size | Pre-registered threshold | Met? |",
        "|---|---|---|---|---|---|---|",
    ]
    for m in report["metrics"]:
        sign = "≥" if m["direction"] == "at_least" else "≤"
        rate = INSUFFICIENT if m["rate"] is None else _pct(m["rate"])
        sample = str(m["sample_size"])
        if m["min_sample"] > 1:
            sample += f" (min {m['min_sample']})"
        lines.append(
            f"| {m['label']} | {m['numerator']} (`{m['numerator_event']}`) "
            f"| {m['denominator']} (`{m['denominator_event']}`) | {rate} | {sample} "
            f"| {sign} {_pct(m['threshold'])} | {m['status']} |"
        )
    lines += [
        "",
        f"`{INSUFFICIENT}`: the denominator is zero or below the pre-registered minimum sample;",
        "no rate is reported. Small samples are directional only; no statistical significance",
        "is claimed. The verdict is made by a human (see the hour-24/48 decision records).",
        "",
    ]
    return "\n".join(lines)


def build_report(experiment: dict, counts: dict, since: datetime, until: datetime | None,
                 source: str) -> dict:
    return {
        "experiment": experiment["id"],
        "hour_0": iso(experiment["hour_0"]),
        "since": iso(since),
        "until": iso(until) if until else None,
        "source": source,
        "counts": {event: counts[event] for event in sorted(counts)},
        "metrics": compute(experiment, counts),
    }


def main(argv=None, env=None, urlopen=urllib.request.urlopen) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--experiment", required=True, help="experiment ID (folder name)")
    parser.add_argument("--since", required=True, help="window start, ISO 8601 with timezone")
    parser.add_argument("--until", help="window end, ISO 8601 with timezone (default: open)")
    parser.add_argument("--fixture", type=Path, help="offline counts JSON instead of PostHog")
    parser.add_argument("--experiments-dir", type=Path, default=Path("experiments"))
    parser.add_argument("--events", type=Path, default=Path("analytics/events.json"))
    args = parser.parse_args(argv)
    try:
        if not NAME_RE.match(args.experiment.replace("-", "_")):
            raise MetricsError(f"invalid experiment ID {args.experiment!r}")
        since = parse_iso(args.since, "--since")
        until = parse_iso(args.until, "--until") if args.until else None
        if until is not None and until <= since:
            raise MetricsError("--until must be after --since")
        folder = args.experiments_dir / args.experiment
        events = load_events(args.events)
        experiment = load_experiment(folder / "experiment.yml", args.experiment, events)
        needed = {e for m in experiment["metrics"] for e in (m["numerator"], m["denominator"])}
        if args.fixture:
            counts = fixture_counts(args.fixture, events)
            source = f"fixture `{args.fixture.name}`"
        else:
            counts = posthog_counts(needed, since, until, env=env, urlopen=urlopen)
            source = "PostHog query API (HogQL, unique users)"
        counts = {event: counts.get(event, 0) for event in needed}
        report = build_report(experiment, counts, since, until, source)
    except MetricsError as exc:
        print(f"experiment_metrics: {exc}", file=sys.stderr)
        return 1
    (folder / "metrics.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                                         encoding="utf-8")
    (folder / "metrics.md").write_text(render_markdown(report), encoding="utf-8")
    for m in report["metrics"]:
        print(f"{m['name']}: {m['numerator']}/{m['denominator']} -> {m['status']}")
    print(f"wrote {folder / 'metrics.md'} and {folder / 'metrics.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
