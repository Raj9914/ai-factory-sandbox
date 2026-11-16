import json

import pytest

from scripts import claim_logic
from scripts.claim_logic import (
    CLAIMED,
    IN_PROGRESS,
    READY,
    claim_record,
    decide,
    expected_branch,
    parse_command,
    slugify,
)

OWNER = "agent:claude"


# --- decide(): the eight required cases -------------------------------------


def test_claim_ready_becomes_claimed():
    labels, message = decide(["type:task", OWNER, READY], "/claim")
    assert labels == sorted(["type:task", OWNER, CLAIMED])
    assert message.startswith("Claimed")


def test_claim_rejected_when_already_claimed():
    current = ["type:task", OWNER, CLAIMED]
    labels, message = decide(current, "/claim")
    assert labels == sorted(current)
    assert "rejected" in message and CLAIMED in message


def test_claim_rejected_when_in_progress():
    current = ["type:task", OWNER, READY, IN_PROGRESS]
    labels, message = decide(current, "/claim")
    assert labels == sorted(current)
    assert "rejected" in message and IN_PROGRESS in message


def test_claim_rejected_without_owner():
    current = ["type:task", READY]
    labels, message = decide(current, "/claim")
    assert labels == sorted(current)
    assert "exactly one `agent:*`" in message and "found none" in message


def test_claim_rejected_with_two_owners():
    current = ["type:task", READY, "agent:claude", "agent:codex"]
    labels, message = decide(current, "/claim")
    assert labels == sorted(current)
    assert "`agent:claude`, `agent:codex`" in message


def test_release_from_claimed_returns_to_ready():
    labels, message = decide(["type:task", OWNER, CLAIMED], "/release")
    assert labels == sorted(["type:task", OWNER, READY])
    assert message.startswith("Released")


def test_release_from_ready_is_noop():
    current = ["type:task", OWNER, READY]
    labels, message = decide(current, "/release")
    assert labels == sorted(current)
    assert message.startswith("Nothing to release")


def test_unknown_command_changes_nothing():
    current = ["type:task", OWNER, READY]
    labels, message = decide(current, "/steal")
    assert labels == sorted(current)
    assert message.startswith("Unknown command")


# --- decide(): further edge cases -------------------------------------------


def test_claim_rejected_when_not_ready():
    current = ["type:task", OWNER, "status:proposed"]
    labels, message = decide(current, "/claim")
    assert labels == sorted(current)
    assert "not `status:ready`" in message


def test_release_from_in_progress_returns_to_ready():
    labels, _ = decide([OWNER, IN_PROGRESS], "/release")
    assert labels == sorted([OWNER, READY])


def test_release_rejected_from_other_status():
    current = [OWNER, "status:review"]
    labels, message = decide(current, "/release")
    assert labels == sorted(current)
    assert "Release rejected" in message and "`status:review`" in message


def test_second_claim_after_first_is_rejected():
    """Serialized racing claims: the second sees the first's labels and is rejected."""
    after_first, first = decide([OWNER, READY], "/claim")
    after_second, second = decide(after_first, "/claim")
    assert first.startswith("Claimed")
    assert "rejected" in second
    assert after_second == after_first


# --- parse_command() ---------------------------------------------------------


@pytest.mark.parametrize(
    "body,expected",
    [
        ("/claim", "/claim"),
        ("/release", "/release"),
        ("  /claim  \r\nsome notes", "/claim"),
        ("/release\n$(rm -rf /)", "/release"),
        ("/claim now", None),
        ("/claimx", None),
        ("/CLAIM", None),
        ("please /claim", None),
        ("\n/claim", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_command_matches_first_line_literals_only(body, expected):
    assert parse_command(body) == expected


# --- claim record ------------------------------------------------------------


def test_slugify_is_safe_and_bounded():
    assert slugify("[P1] Claim workflow: /claim & /release!") == "p1-claim-workflow-claim-release"
    assert slugify("`; rm -rf / #`") == "rm-rf"
    assert slugify("") == "task"
    assert slugify(None) == "task"
    assert len(slugify("a" * 100)) == claim_logic.SLUG_MAX
    assert not slugify("a" * 39 + " b").endswith("-")


def test_claim_record_fields():
    record = claim_record("octocat", "123", "2026-11-16T00:00:00Z", 34, "Claim workflow")
    assert "Actor: @octocat" in record
    assert "Run ID: 123" in record
    assert "Timestamp: 2026-11-16T00:00:00Z" in record
    assert "`task/34-claim-workflow`" in record
    assert expected_branch(34, "Claim workflow") == "task/34-claim-workflow"


# --- main(): I/O wrapper with gh stubbed --------------------------------------


def _run_main(tmp_path, monkeypatch, *, body, association="COLLABORATOR", live_labels, payload_labels=None, pr=False):
    issue = {"number": 7, "title": "Do the thing", "labels": [{"name": n} for n in (payload_labels or [])]}
    if pr:
        issue["pull_request"] = {}
    event = {"issue": issue, "comment": {"body": body, "author_association": association, "user": {"login": "alice"}}}
    path = tmp_path / "event.json"
    path.write_text(json.dumps(event), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(path))
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_RUN_ID", "999")
    calls = []

    def fake_gh(*args, stdin=None):
        calls.append((args, json.loads(stdin) if stdin else None))
        if args[-1].endswith("/labels") and "--method" not in args:
            return json.dumps([[{"name": n} for n in live_labels]])
        return "{}"

    monkeypatch.setattr(claim_logic, "_gh_api", fake_gh)
    assert claim_logic.main() == 0
    return calls


def test_main_uses_live_labels_not_payload_snapshot(tmp_path, monkeypatch):
    # The payload still says status:ready, but a racing claim already won.
    calls = _run_main(
        tmp_path, monkeypatch, body="/claim", payload_labels=[OWNER, READY], live_labels=[OWNER, CLAIMED]
    )
    writes = [c for c in calls if "--method" in c[0]]
    assert len(writes) == 1  # comment only, no label change
    assert "PUT" not in writes[0][0]
    assert "rejected" in writes[0][1]["body"]


def test_main_successful_claim_sets_labels_and_posts_record(tmp_path, monkeypatch):
    calls = _run_main(tmp_path, monkeypatch, body="/claim\nextra", live_labels=[OWNER, READY])
    put = [c for c in calls if "PUT" in c[0]]
    post = [c for c in calls if "POST" in c[0]]
    assert put[0][1] == {"labels": sorted([OWNER, CLAIMED])}
    body = post[0][1]["body"]
    assert "Actor: @alice" in body and "Run ID: 999" in body and "`task/7-do-the-thing`" in body
    assert "extra" not in body


@pytest.mark.parametrize(
    "kwargs",
    [
        {"body": "/claim", "association": "NONE"},
        {"body": "/claim", "association": "CONTRIBUTOR"},
        {"body": "hello /claim"},
        {"body": "/claim", "pr": True},
    ],
)
def test_main_ignores_untrusted_non_command_and_pr_comments(tmp_path, monkeypatch, kwargs):
    assert _run_main(tmp_path, monkeypatch, live_labels=[OWNER, READY], **kwargs) == []
