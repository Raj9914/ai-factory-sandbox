#!/usr/bin/env python3
"""Interactive task claims: ``/claim`` and ``/release`` issue-comment commands.

See ADR 0010 and ``docs/AGENT_HANDOFF_PROTOCOL.md``.

The pure functions (``parse_command``, ``decide``, ``slugify``,
``expected_branch``, ``claim_record``) have no I/O and are unit-tested.
``main`` is a thin wrapper that reads the GitHub event payload, re-reads the
issue's *current* labels through the ``gh`` CLI (the payload is a snapshot taken
when the comment was posted, so two racing claims would both see
``status:ready``), applies the decision and posts the result.

The comment body is untrusted: only its first line is compared against two
literals, and it is never echoed, executed or passed to a shell.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

CLAIM = "/claim"
RELEASE = "/release"
COMMANDS = (CLAIM, RELEASE)

READY = "status:ready"
CLAIMED = "status:claimed"
IN_PROGRESS = "status:in_progress"
STATUS_PREFIX = "status:"
OWNER_PREFIX = "agent:"
TRUSTED_ASSOCIATIONS = ("OWNER", "MEMBER", "COLLABORATOR")
SLUG_MAX = 40


def parse_command(body):
    """Return ``/claim`` or ``/release`` if the body's first line is exactly one of them, else ``None``."""
    lines = (body or "").splitlines()
    first_line = lines[0].strip() if lines else ""
    return first_line if first_line in COMMANDS else None


def decide(labels, command):
    """Return ``(new_labels, message)`` for ``command`` applied to an issue with ``labels``.

    ``new_labels`` is the full, sorted label set to apply. When the command is
    rejected or is a no-op it equals the current labels, so callers can detect
    "no change" by comparing sets.
    """
    current = sorted(set(labels))
    if command == CLAIM:
        if CLAIMED in current or IN_PROGRESS in current:
            active = CLAIMED if CLAIMED in current else IN_PROGRESS
            return current, f"Claim rejected: the issue is already `{active}`. A stale claim is flagged, not stolen."
        if READY not in current:
            return current, f"Claim rejected: the issue is not `{READY}`. Only a human moves a task to `{READY}`."
        owners = [name for name in current if name.startswith(OWNER_PREFIX)]
        if len(owners) != 1:
            found = ", ".join(f"`{name}`" for name in owners) or "none"
            return current, (
                f"Claim rejected: the issue needs exactly one `{OWNER_PREFIX}*` owner label (found {found})."
            )
        new = sorted((set(current) - {READY}) | {CLAIMED})
        return new, f"Claimed: `{READY}` → `{CLAIMED}` (owner `{owners[0]}`)."
    if command == RELEASE:
        if CLAIMED in current or IN_PROGRESS in current:
            new = sorted((set(current) - {CLAIMED, IN_PROGRESS}) | {READY})
            return new, f"Released: the issue is back to `{READY}`."
        if READY in current:
            return current, f"Nothing to release: the issue is already `{READY}`."
        status = ", ".join(f"`{name}`" for name in current if name.startswith(STATUS_PREFIX)) or "none"
        return current, (
            f"Release rejected: the issue is not `{CLAIMED}` or `{IN_PROGRESS}` (status: {status})."
        )
    return current, f"Unknown command. Supported commands: `{CLAIM}`, `{RELEASE}`."


def slugify(title):
    """Lower-case ``[a-z0-9-]`` slug of ``title``, at most ``SLUG_MAX`` characters."""
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    slug = slug[:SLUG_MAX].rstrip("-")
    return slug or "task"


def expected_branch(number, title):
    return f"task/{number}-{slugify(title)}"


def claim_record(actor, run_id, timestamp, number, title):
    """Markdown claim record posted after a successful ``/claim``."""
    return (
        "**Claim record**\n"
        f"- Actor: @{actor}\n"
        f"- Run ID: {run_id}\n"
        f"- Timestamp: {timestamp}\n"
        f"- Expected branch: `{expected_branch(number, title)}`\n"
        f"\nRelease with a `{RELEASE}` comment."
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


def _current_labels(repo, number):
    pages = json.loads(_gh_api("--paginate", "--slurp", f"repos/{repo}/issues/{number}/labels"))
    return [label["name"] for page in pages for label in page if isinstance(label.get("name"), str)]


def main():
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not event_path or not repo:
        print("GITHUB_EVENT_PATH and GITHUB_REPOSITORY are required", file=sys.stderr)
        return 2
    with open(event_path, encoding="utf-8") as fh:
        event = json.load(fh)

    issue = event.get("issue") or {}
    comment = event.get("comment") or {}
    number = issue.get("number")
    if number is None or "pull_request" in issue:
        print("Not an issue comment; nothing to do.")
        return 0
    # Defence in depth: the workflow `if:` already filters on this.
    if comment.get("author_association") not in TRUSTED_ASSOCIATIONS:
        print(f"Issue #{number}: comment author is not trusted; ignored.")
        return 0
    command = parse_command(comment.get("body"))
    if command is None:
        print(f"Issue #{number}: no command on the first line; ignored.")
        return 0

    labels = _current_labels(repo, number)
    new_labels, message = decide(labels, command)
    if set(new_labels) != set(labels):
        _gh_api(
            "--method",
            "PUT",
            f"repos/{repo}/issues/{number}/labels",
            "--input",
            "-",
            stdin=json.dumps({"labels": new_labels}),
        )
        if command == CLAIM:
            actor = (comment.get("user") or {}).get("login") or os.environ.get("GITHUB_ACTOR", "unknown")
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            run_id = os.environ.get("GITHUB_RUN_ID", "unknown")
            message = f"{message}\n\n{claim_record(actor, run_id, timestamp, number, issue.get('title'))}"

    _gh_api(
        "--method",
        "POST",
        f"repos/{repo}/issues/{number}/comments",
        "--input",
        "-",
        stdin=json.dumps({"body": message}),
    )
    print(f"Issue #{number}: {command} -> {message.splitlines()[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
