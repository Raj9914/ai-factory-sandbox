#!/usr/bin/env python3
"""Hour-24 and hour-48 decision reminders (stdlib only).

Usage: python3 scripts/decision_timers.py [EXPERIMENTS_DIR] [--now ISO] [--dry-run]
       (default EXPERIMENTS_DIR: experiments)

Run hourly by .github/workflows/decision-timers.yml. For every `experiments/<id>/experiment.yml`
it reads the experiment start, the top-level `hour_0:` key (ISO 8601 with a timezone, the same
key scripts/experiment_metrics.py uses), and opens one `type:decision` issue owned by
`agent:human` when hour 0 + 24 h and hour 0 + 48 h have passed. Each issue body is prefilled from
docs/templates/HOUR_24_DECISION_TEMPLATE.md / HOUR_48_DECISION_TEMPLATE.md and links the latest
metrics and cost reports (experiments/<id>/metrics.md and cost.md on the default branch).

Idempotent: every issue carries a hidden marker
`<!-- ship48-decision-timer experiment=<id> checkpoint=<24|48> -->`. Before opening an issue the
script searches all issues (open and closed) for that marker (or the exact reminder title) and
skips the checkpoint if one exists. A checkpoint whose decision record already exists
(experiments/<id>/decision-24h.md or HOUR_24.md, decision-48h.md or HOUR_48.md) is also skipped.

It never decides, edits or closes anything: a human records the verdict.

Environment (set by the workflow): GH_TOKEN, GITHUB_REPOSITORY (owner/name), GITHUB_SERVER_URL,
DEFAULT_BRANCH. The pure functions are unit-tested with a frozen clock; `GhClient` is a thin
wrapper around the `gh` CLI.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys

DEFAULT_EXPERIMENTS = Path("experiments")
CHECKPOINTS = (24, 48)
LABELS = ["type:decision", "agent:human", "status:proposed"]
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
HOUR_0_RE = re.compile(r"^hour_0:[ \t]*(.*)$")
RECORD_FILES = {24: ("decision-24h.md", "HOUR_24.md"), 48: ("decision-48h.md", "HOUR_48.md")}
TEMPLATE_FILES = {
    24: Path("docs/templates/HOUR_24_DECISION_TEMPLATE.md"),
    48: Path("docs/templates/HOUR_48_DECISION_TEMPLATE.md"),
}
VERDICTS = {24: "KEEP_SCOPE or CUT_SCOPE", 48: "BUILD, PIVOT, KILL or INCONCLUSIVE"}


class TimerError(ValueError):
    """An experiment folder whose start time cannot be read."""


@dataclass(frozen=True)
class Experiment:
    id: str
    hour_0: datetime
    folder: Path


# --------------------------------------------------------------------------- pure helpers

def parse_iso(value: str, what: str = "time") -> datetime:
    """Parse ISO 8601 with a timezone (`Z` or an offset) into an aware UTC datetime."""
    text = str(value).strip()
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise TimerError(f"{what} is not ISO 8601: {value!r}") from None
    if parsed.tzinfo is None:
        raise TimerError(f"{what} must include a timezone, e.g. 2026-11-11T09:00:00Z: {value!r}")
    return parsed.astimezone(timezone.utc)


def parse_hour_0(text: str) -> datetime:
    """Return the top-level `hour_0:` value of an experiment.yml (a small YAML subset)."""
    found = []
    for line in text.replace("\r\n", "\n").split("\n"):
        m = HOUR_0_RE.match(line)  # top level only: no indentation
        if not m:
            continue
        value = re.sub(r"\s+#.*$", "", m.group(1)).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1].strip()
        found.append(value)
    if not found or not found[0]:
        raise TimerError("experiment.yml has no top-level hour_0 (experiment start, ISO 8601)")
    if len(found) > 1:
        raise TimerError("experiment.yml has more than one hour_0")
    return parse_iso(found[0], "hour_0")


def load_experiments(root: Path) -> tuple[list[Experiment], list[str]]:
    """Read every experiments/<id>/experiment.yml. Returns (experiments, errors)."""
    experiments, errors = [], []
    if not root.is_dir():
        return experiments, errors
    for path in sorted(root.glob("*/experiment.yml")):
        folder = path.parent
        if not ID_RE.match(folder.name):
            errors.append(f"{path}: experiment folder name {folder.name!r} is not a valid id")
            continue
        try:
            hour_0 = parse_hour_0(path.read_text(encoding="utf-8"))
        except (TimerError, UnicodeDecodeError) as exc:
            errors.append(f"{path}: {exc}")
            continue
        experiments.append(Experiment(folder.name, hour_0, folder))
    return experiments, errors


def due_checkpoints(hour_0: datetime, now: datetime) -> list[int]:
    """Checkpoints (hours after hour 0) whose time has come."""
    return [h for h in CHECKPOINTS if now >= hour_0 + timedelta(hours=h)]


def marker(experiment_id: str, checkpoint: int) -> str:
    return f"<!-- ship48-decision-timer experiment={experiment_id} checkpoint={checkpoint} -->"


def issue_title(experiment_id: str, checkpoint: int) -> str:
    return f"[DECISION] {experiment_id}: hour-{checkpoint} decision checkpoint"


def find_existing(issues, experiment_id: str, checkpoint: int):
    """The issue (open or closed) already opened for this checkpoint, or None."""
    tag, title = marker(experiment_id, checkpoint), issue_title(experiment_id, checkpoint)
    for issue in issues:
        if "pull_request" in issue:
            continue
        if tag in (issue.get("body") or "") or (issue.get("title") or "").strip() == title:
            return issue
    return None


def record_exists(experiment: Experiment, checkpoint: int) -> bool:
    return any((experiment.folder / name).is_file() for name in RECORD_FILES[checkpoint])


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def report_link(experiment: Experiment, names, repo_url: str, branch: str) -> str:
    """Markdown link to the first existing report in `names` (or the first name, flagged missing)."""
    name = next((n for n in names if (experiment.folder / n).is_file()), names[0])
    rel = f"experiments/{experiment.id}/{name}"
    target = f"{repo_url}/blob/{branch}/{rel}" if repo_url else rel
    note = "" if (experiment.folder / name).is_file() else " (not generated yet: NOT AVAILABLE)"
    return f"[`{rel}`]({target}){note}"


def issue_body(experiment: Experiment, checkpoint: int, template_text: str | None,
               repo_url: str = "", branch: str = "main") -> str:
    due = experiment.hour_0 + timedelta(hours=checkpoint)
    metrics = report_link(experiment, ("metrics.md",), repo_url, branch)
    cost = report_link(experiment, ("cost.md", "COST_LOG.md"), repo_url, branch)
    record = f"experiments/{experiment.id}/{RECORD_FILES[checkpoint][0]}"
    lines = [
        marker(experiment.id, checkpoint),
        f"## Hour-{checkpoint} decision checkpoint: `{experiment.id}`",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Experiment | `{experiment.id}` |",
        f"| Hour 0 (ISO) | {_iso(experiment.hour_0)} |",
        f"| Checkpoint due (ISO) | {_iso(due)} |",
        f"| Allowed verdicts | {VERDICTS[checkpoint]} |",
        "| Decided by | agent:human |",
        "",
        "### Evidence (latest reports)",
        f"- Metrics: {metrics}",
        f"- Cost: {cost}",
        "",
        "### What to do",
        f"1. Draft `{record}` from the prefilled template below (actual figures only; write",
        "   `NOT MEASURED` or `NOT AVAILABLE` instead of estimating).",
        "2. A human records the verdict, then `python3 scripts/validate_decisions.py experiments`",
        "   must pass in CI.",
        "",
        "This issue was opened automatically by `decision-timers`. It never decides; close it once",
        "the decision record is merged.",
        "",
        "---",
        "",
    ]
    if template_text:
        prefilled = template_text.replace("<Experiment ID>", experiment.id)
        prefilled = re.sub(r"^\| Hour 0 \(ISO\) \|\s*\|", f"| Hour 0 (ISO) | {_iso(experiment.hour_0)} |",
                           prefilled, flags=re.M)
        prefilled = re.sub(r"^\| Hour 0 / recorded at \(ISO\) \|\s*\|",
                           f"| Hour 0 / recorded at (ISO) | {_iso(experiment.hour_0)} / |",
                           prefilled, flags=re.M)
        lines.append(prefilled.rstrip())
    else:
        lines.append(f"_Decision template {TEMPLATE_FILES[checkpoint].as_posix()} not found._")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- orchestration

def run(root: Path, client, now: datetime, template_root: Path | None = None,
        repo_url: str = "", branch: str = "main", dry_run: bool = False, out=print) -> int:
    """Open every due, not-yet-opened checkpoint issue. Returns the process exit code."""
    template_root = template_root if template_root is not None else root.resolve().parent
    experiments, errors = load_experiments(root)
    for error in errors:
        out(f"ERROR {error}")
    if not experiments:
        out(f"no experiments with experiment.yml under {root}; nothing to do")
        return 1 if errors else 0
    issues = None
    for experiment in experiments:
        for checkpoint in due_checkpoints(experiment.hour_0, now):
            label = f"{experiment.id} hour-{checkpoint}"
            if record_exists(experiment, checkpoint):
                out(f"skip {label}: decision record already exists")
                continue
            if issues is None:
                issues = list(client.list_issues())
            existing = find_existing(issues, experiment.id, checkpoint)
            if existing is not None:
                out(f"skip {label}: issue #{existing.get('number')} already exists")
                continue
            template_path = template_root / TEMPLATE_FILES[checkpoint]
            template_text = template_path.read_text(encoding="utf-8") if template_path.is_file() else None
            title = issue_title(experiment.id, checkpoint)
            body = issue_body(experiment, checkpoint, template_text, repo_url, branch)
            if dry_run:
                out(f"would open {label}: {title}")
                continue
            created = client.create_issue(title, body, list(LABELS))
            issues.append(created)
            out(f"opened {label}: #{created.get('number')} {created.get('html_url', '')}".rstrip())
    return 1 if errors else 0


class GhClient:
    """Minimal GitHub REST client over the `gh` CLI (authenticated by GH_TOKEN)."""

    def __init__(self, repo: str):
        self.repo = repo

    def _api(self, *args: str, stdin: str | None = None) -> str:
        result = subprocess.run(["gh", "api", *args], input=stdin, capture_output=True,
                                text=True, check=False, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f"gh api {args[-1]} failed: {result.stderr.strip()}")
        return result.stdout

    def list_issues(self):
        pages = json.loads(self._api("--paginate", "--slurp",
                                     f"repos/{self.repo}/issues?state=all&per_page=100") or "[]")
        return [issue for page in pages for issue in page]

    def create_issue(self, title: str, body: str, labels: list[str]):
        payload = json.dumps({"title": title, "body": body, "labels": labels})
        return json.loads(self._api("-X", "POST", f"repos/{self.repo}/issues", "--input", "-",
                                    stdin=payload))


def main(argv: list[str] | None = None, client=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("experiments", nargs="?", default=str(DEFAULT_EXPERIMENTS))
    parser.add_argument("--now", help="override the clock (ISO 8601 with timezone), for tests")
    parser.add_argument("--dry-run", action="store_true", help="print what would be opened")
    args = parser.parse_args(argv)
    try:
        now = parse_iso(args.now, "--now") if args.now else datetime.now(timezone.utc)
    except TimerError as exc:
        print(f"ERROR {exc}")
        return 2
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    branch = os.environ.get("DEFAULT_BRANCH") or "main"
    if client is None and not args.dry_run:
        if not repo:
            print("ERROR GITHUB_REPOSITORY (owner/name) is not set")
            return 2
        client = GhClient(repo)
    if client is None:  # dry run without a repository: assume no issues exist yet
        client = _NoIssues()
    repo_url = f"{server}/{repo}" if repo else ""
    return run(Path(args.experiments), client, now, repo_url=repo_url, branch=branch,
               dry_run=args.dry_run)


class _NoIssues:
    def list_issues(self):
        return []

    def create_issue(self, title, body, labels):  # pragma: no cover - dry run never creates
        raise RuntimeError("dry run must not create issues")


if __name__ == "__main__":
    sys.exit(main())
