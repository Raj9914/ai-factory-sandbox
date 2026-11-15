#!/usr/bin/env python3
"""Status-label guard.

Warns (by comment) when an open ``type:task`` issue carries zero or more than
one ``status:*`` label. It only reads labels; it never adds, removes or edits
labels and never parses the issue body.

The pure functions (``status_labels``, ``marker``, ``evaluate``,
``should_comment``) have no I/O. ``main`` is a thin wrapper that reads the
GitHub event payload and talks to the API through the ``gh`` CLI.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

STATUS_PREFIX = "status:"
TASK_LABEL = "type:task"
MARKER_PREFIX = "<!-- status-guard:state="
MARKER_SUFFIX = " -->"


def status_labels(labels):
    """Return the sorted, de-duplicated ``status:*`` label names."""
    return sorted({name for name in labels if name.startswith(STATUS_PREFIX)})


def marker(status):
    """Hidden HTML marker identifying a status-label state."""
    state = ",".join(sorted(set(status))) if status else "none"
    return f"{MARKER_PREFIX}{state}{MARKER_SUFFIX}"


def evaluate(labels, state, is_task):
    """Return the warning comment body, or ``None`` when no warning is needed."""
    if state != "open" or not is_task:
        return None
    status = status_labels(labels)
    if len(status) == 1:
        return None
    if status:
        found = ", ".join(f"`{name}`" for name in status)
        problem = f"carries {len(status)} `status:*` labels ({found})"
    else:
        problem = "carries no `status:*` label"
    return (
        f"{marker(status)}\n"
        f"**Status-label guard:** this task {problem}. "
        "A `type:task` issue should have exactly one `status:*` label. "
        "Labels were not changed; please fix them manually."
    )


def should_comment(existing_comment_bodies, comment_marker):
    """True unless an existing comment already contains ``comment_marker``."""
    return not any(comment_marker in (body or "") for body in existing_comment_bodies)


def _label_names(issue):
    names = []
    for label in issue.get("labels") or []:
        name = label.get("name") if isinstance(label, dict) else label
        if isinstance(name, str):
            names.append(name)
    return names


def _gh_api(*args, stdin=None):
    result = subprocess.run(
        ["gh", "api", *args],
        input=stdin,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def main():
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not event_path or not repo:
        print("GITHUB_EVENT_PATH and GITHUB_REPOSITORY are required", file=sys.stderr)
        return 2
    with open(event_path, encoding="utf-8") as fh:
        event = json.load(fh)

    issue = event.get("issue") or {}
    number = issue.get("number")
    if number is None or "pull_request" in issue:
        print("Not an issue event; nothing to do.")
        return 0

    labels = _label_names(issue)
    body = evaluate(labels, issue.get("state"), TASK_LABEL in labels)
    if body is None:
        print(f"Issue #{number}: no warning needed.")
        return 0

    comment_marker = marker(status_labels(labels))
    pages = json.loads(
        _gh_api("--paginate", "--slurp", f"repos/{repo}/issues/{number}/comments")
    )
    existing = [c.get("body") or "" for page in pages for c in page]
    if not should_comment(existing, comment_marker):
        print(f"Issue #{number}: warning for this state already posted.")
        return 0

    _gh_api(
        "--method",
        "POST",
        f"repos/{repo}/issues/{number}/comments",
        "--input",
        "-",
        stdin=json.dumps({"body": body}),
    )
    print(f"Issue #{number}: posted status-label warning.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
