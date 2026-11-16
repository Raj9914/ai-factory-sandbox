#!/usr/bin/env python3
"""Stale-claim detector: flag claimed work with no recent activity.

See ADR 0010 and ``docs/AGENT_HANDOFF_PROTOCOL.md``: "A stale claim is flagged,
not stolen."

An open issue labelled ``status:claimed`` or ``status:in_progress`` is *stale*
when its latest issue or linked-PR activity is older than the threshold
(default 4 h). A stale issue gets ``status:blocked`` added (the claim label is
kept) and one comment per stale episode. This script never removes labels,
never releases a claim and never reassigns an owner.

A stale episode ends when any new activity happens after the flag comment
(a comment, a label change, linked-PR activity, ...). If the issue then goes
stale again, a new episode starts and one new comment is posted.

The pure functions (``parse_time``, ``activity_from_timeline``, ``evaluate``,
``flag_comment``, ``parse_threshold``) have no I/O and are unit-tested. ``main``
is a thin wrapper that talks to the API through the ``gh`` CLI.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

CLAIMED = "status:claimed"
IN_PROGRESS = "status:in_progress"
CLAIM_LABELS = (CLAIMED, IN_PROGRESS)
BLOCKED = "status:blocked"
MARKER = "<!-- stale-claim-detector -->"
DEFAULT_THRESHOLD_MINUTES = 240

NOT_CLAIMED = "not_claimed"
FRESH = "fresh"
ALREADY_FLAGGED = "already_flagged"
FLAG = "flag"


def parse_time(value):
    """Parse a GitHub ISO-8601 timestamp (``2026-11-16T10:00:00Z``) into an aware UTC datetime."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_threshold(value):
    """Return the threshold in minutes from a string/int; empty means the 4 h default."""
    if value is None or str(value).strip() == "":
        return DEFAULT_THRESHOLD_MINUTES
    minutes = int(str(value).strip())
    if minutes < 1:
        raise ValueError("threshold must be at least 1 minute")
    return minutes


def _event_time(event):
    """Best timestamp of a timeline event (events differ in which field they carry)."""
    for key in ("created_at", "submitted_at", "updated_at"):
        if event.get(key):
            return parse_time(event[key])
    for key in ("committer", "author"):
        person = event.get(key)
        if isinstance(person, dict) and person.get("date"):
            return parse_time(person["date"])
    return None


def activity_from_timeline(created_at, events):
    """Return ``(activity_times, flag_times)`` from an issue's timeline events.

    ``activity_times`` always includes the issue's ``created_at``. The
    detector's own footprint (comments carrying ``MARKER`` and ``labeled``
    events for ``status:blocked``) is not activity; marker comments are
    returned separately as ``flag_times``. A cross-referencing pull request
    contributes its own ``updated_at``, so linked-PR activity resets staleness.
    """
    activity = []
    flags = []
    start = parse_time(created_at)
    if start is not None:
        activity.append(start)
    for event in events or []:
        if not isinstance(event, dict):
            continue
        kind = event.get("event")
        when = _event_time(event)
        if kind == "commented" and MARKER in (event.get("body") or ""):
            if when is not None:
                flags.append(when)
            continue
        if kind == "labeled" and (event.get("label") or {}).get("name") == BLOCKED:
            continue
        if when is not None:
            activity.append(when)
        if kind == "cross-referenced":
            source = (event.get("source") or {}).get("issue") or {}
            if "pull_request" in source:
                pr_time = parse_time(source.get("updated_at"))
                if pr_time is not None:
                    activity.append(pr_time)
    return activity, flags


def evaluate(labels, activity_times, flag_times, now, threshold_minutes=DEFAULT_THRESHOLD_MINUTES):
    """Decide what to do with one issue.

    Returns one of ``NOT_CLAIMED``, ``FRESH``, ``ALREADY_FLAGGED`` or ``FLAG``.
    """
    if not any(name in CLAIM_LABELS for name in labels):
        return NOT_CLAIMED
    if not activity_times:
        return FLAG if not flag_times else ALREADY_FLAGGED
    last_activity = max(activity_times)
    if now - last_activity <= timedelta(minutes=threshold_minutes):
        return FRESH
    if flag_times and max(flag_times) >= last_activity:
        return ALREADY_FLAGGED
    return FLAG


def _format_duration(minutes):
    hours, mins = divmod(int(minutes), 60)
    if hours and mins:
        return f"{hours} h {mins} min"
    if hours:
        return f"{hours} h"
    return f"{mins} min"


def flag_comment(labels, last_activity, now, threshold_minutes=DEFAULT_THRESHOLD_MINUTES):
    """Markdown body of the one-per-episode stale-claim comment."""
    claim = next((name for name in CLAIM_LABELS if name in labels), CLAIMED)
    idle_minutes = int((now - last_activity).total_seconds() // 60)
    return (
        f"{MARKER}\n"
        f"**Stale claim flagged:** this issue is `{claim}` but has had no issue or linked-PR "
        f"activity for {_format_duration(idle_minutes)} (threshold {_format_duration(threshold_minutes)}; "
        f"last activity {last_activity.strftime('%Y-%m-%dT%H:%M:%SZ')}).\n\n"
        f"Added `{BLOCKED}`; the claim label was kept. A stale claim is flagged, not stolen: "
        "nothing was released or reassigned. The owner should post an update, or a human "
        "can release the claim with `/release`."
    )


def _gh_api(*args, stdin=None):
    result = subprocess.run(
        ["gh", "api", *args],
        input=stdin,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _paginated(path):
    pages = json.loads(_gh_api("--paginate", "--slurp", path))
    return [item for page in pages for item in page]


def _claimed_issues(repo):
    """Open issues (not PRs) carrying any claim label, de-duplicated by number."""
    found = {}
    for label in CLAIM_LABELS:
        for issue in _paginated(f"repos/{repo}/issues?state=open&labels={label}&per_page=100"):
            if "pull_request" in issue or issue.get("number") is None:
                continue
            found[issue["number"]] = issue
    return [found[number] for number in sorted(found)]


def _label_names(issue):
    names = []
    for label in issue.get("labels") or []:
        name = label.get("name") if isinstance(label, dict) else label
        if isinstance(name, str):
            names.append(name)
    return names


def process_issue(repo, issue, now, threshold_minutes):
    """Evaluate one issue and apply the flag if needed; returns the decision."""
    number = issue["number"]
    labels = _label_names(issue)
    events = _paginated(f"repos/{repo}/issues/{number}/timeline?per_page=100")
    activity, flags = activity_from_timeline(issue.get("created_at"), events)
    decision = evaluate(labels, activity, flags, now, threshold_minutes)
    if decision == FLAG:
        last_activity = max(activity) if activity else now
        # POST adds labels; it never removes the claim label.
        _gh_api(
            "--method",
            "POST",
            f"repos/{repo}/issues/{number}/labels",
            "--input",
            "-",
            stdin=json.dumps({"labels": [BLOCKED]}),
        )
        _gh_api(
            "--method",
            "POST",
            f"repos/{repo}/issues/{number}/comments",
            "--input",
            "-",
            stdin=json.dumps({"body": flag_comment(labels, last_activity, now, threshold_minutes)}),
        )
    return decision


def main():
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        print("GITHUB_REPOSITORY is required", file=sys.stderr)
        return 2
    try:
        threshold = parse_threshold(os.environ.get("STALE_THRESHOLD_MINUTES"))
    except ValueError as exc:
        print(f"Invalid STALE_THRESHOLD_MINUTES: {exc}", file=sys.stderr)
        return 2
    now = datetime.now(timezone.utc)
    print(f"Stale-claim threshold: {threshold} min")

    failures = 0
    results = []
    for issue in _claimed_issues(repo):
        number = issue["number"]
        try:
            decision = process_issue(repo, issue, now, threshold)
        except subprocess.CalledProcessError as exc:
            failures += 1
            print(f"::warning::Issue #{number}: GitHub API call failed ({exc.returncode})")
            continue
        results.append((number, decision))
        print(f"Issue #{number}: {decision}")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write("## Stale-claim detector\n\n")
            fh.write(f"Threshold: {threshold} min. Claims are flagged, never released or reassigned.\n\n")
            if results:
                fh.write("| Issue | Decision |\n|---|---|\n")
                for number, decision in results:
                    fh.write(f"| #{number} | {decision} |\n")
            else:
                fh.write("No open claimed or in-progress issues.\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
