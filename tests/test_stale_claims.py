import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts import stale_claims
from scripts.stale_claims import (
    ALREADY_FLAGGED,
    BLOCKED,
    CLAIMED,
    DEFAULT_THRESHOLD_MINUTES,
    FLAG,
    FRESH,
    IN_PROGRESS,
    MARKER,
    NOT_CLAIMED,
    activity_from_timeline,
    evaluate,
    flag_comment,
    parse_threshold,
    parse_time,
)

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
OWNER = "agent:claude"


def ago(**kwargs):
    return NOW - timedelta(**kwargs)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def comment(when, body="update"):
    return {"event": "commented", "created_at": iso(when), "body": body}


def labeled(when, name):
    return {"event": "labeled", "created_at": iso(when), "label": {"name": name}}


def pr_reference(when, pr_updated):
    return {
        "event": "cross-referenced",
        "created_at": iso(when),
        "source": {"type": "issue", "issue": {"number": 99, "pull_request": {}, "updated_at": iso(pr_updated)}},
    }


# --- evaluate(): required cases ------------------------------------------------


def test_fresh_claim_is_not_flagged():
    assert evaluate([OWNER, CLAIMED], [ago(hours=1)], [], NOW) == FRESH


def test_stale_claim_is_flagged():
    assert evaluate([OWNER, CLAIMED], [ago(hours=5)], [], NOW) == FLAG


def test_stale_in_progress_is_flagged():
    assert evaluate([OWNER, IN_PROGRESS], [ago(hours=4, minutes=1)], [], NOW) == FLAG


def test_already_flagged_does_not_post_duplicate_comment():
    events = [labeled(ago(hours=6), CLAIMED), comment(ago(hours=1), MARKER + "\nflagged"), labeled(ago(hours=1), BLOCKED)]
    activity, flags = activity_from_timeline(iso(ago(days=1)), events)
    assert evaluate([OWNER, CLAIMED, BLOCKED], activity, flags, NOW) == ALREADY_FLAGGED


def test_pr_activity_resets_staleness():
    # The issue itself is quiet for 6 h, but the linked PR was updated 30 min ago.
    events = [labeled(ago(hours=6), CLAIMED), pr_reference(ago(hours=6), ago(minutes=30))]
    activity, flags = activity_from_timeline(iso(ago(days=1)), events)
    assert evaluate([OWNER, CLAIMED], activity, flags, NOW) == FRESH
    # Without the PR update the same issue is stale.
    activity, flags = activity_from_timeline(iso(ago(days=1)), events[:1])
    assert evaluate([OWNER, CLAIMED], activity, flags, NOW) == FLAG


# --- further behaviour ---------------------------------------------------------


def test_unclaimed_issue_is_ignored():
    assert evaluate([OWNER, "status:ready"], [ago(days=3)], [], NOW) == NOT_CLAIMED


def test_activity_after_flag_starts_new_episode():
    events = [comment(ago(hours=10), MARKER), comment(ago(hours=5), "still working")]
    activity, flags = activity_from_timeline(iso(ago(days=1)), events)
    assert evaluate([OWNER, CLAIMED, BLOCKED], activity, flags, NOW) == FLAG


def test_detector_footprint_is_not_activity():
    events = [comment(ago(minutes=5), MARKER + " text"), labeled(ago(minutes=5), BLOCKED)]
    activity, flags = activity_from_timeline(iso(ago(hours=5)), events)
    assert activity == [ago(hours=5)]
    assert flags == [ago(minutes=5)]


def test_other_label_changes_count_as_activity():
    events = [{"event": "unlabeled", "created_at": iso(ago(minutes=10)), "label": {"name": BLOCKED}}]
    activity, _ = activity_from_timeline(iso(ago(days=2)), events)
    assert evaluate([OWNER, CLAIMED], activity, [], NOW) == FRESH


def test_commit_event_timestamp_is_used():
    events = [{"event": "committed", "committer": {"date": iso(ago(minutes=20))}}]
    activity, _ = activity_from_timeline(iso(ago(days=2)), events)
    assert max(activity) == ago(minutes=20)


def test_threshold_is_configurable():
    activity = [ago(minutes=2)]
    assert evaluate([CLAIMED], activity, [], NOW, threshold_minutes=1) == FLAG
    assert evaluate([CLAIMED], activity, [], NOW, threshold_minutes=DEFAULT_THRESHOLD_MINUTES) == FRESH


def test_parse_threshold():
    assert parse_threshold(None) == 240
    assert parse_threshold("") == 240
    assert parse_threshold("1") == 1
    with pytest.raises(ValueError):
        parse_threshold("0")
    with pytest.raises(ValueError):
        parse_threshold("abc")


def test_parse_time_handles_z_suffix():
    assert parse_time("2026-11-16T12:00:00Z") == NOW


def test_flag_comment_keeps_claim_and_has_marker():
    body = flag_comment([OWNER, IN_PROGRESS], ago(hours=5), NOW)
    assert body.startswith(MARKER)
    assert IN_PROGRESS in body and BLOCKED in body
    assert "5 h" in body and "threshold 4 h" in body
    assert "not stolen" in body


# --- main(): thin I/O wrapper with a fake gh -----------------------------------


class FakeGh:
    def __init__(self, issues, timelines):
        self.issues = issues
        self.timelines = timelines
        self.writes = []

    def __call__(self, *args, stdin=None):
        args = list(args)
        if args[:2] == ["--method", "POST"]:
            self.writes.append((args[2], json.loads(stdin)))
            return "{}"
        path = args[-1]
        if "/timeline" in path:
            number = int(path.split("/issues/")[1].split("/")[0])
            return json.dumps([self.timelines.get(number, [])])
        if "/issues?" in path:
            label = path.split("labels=")[1].split("&")[0]
            return json.dumps([[i for i in self.issues if label in [x["name"] for x in i["labels"]]]])
        raise AssertionError(f"unexpected gh call: {args}")


def run_main(monkeypatch, tmp_path, fake, threshold=None):
    monkeypatch.setattr(stale_claims, "_gh_api", fake)
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    if threshold is None:
        monkeypatch.delenv("STALE_THRESHOLD_MINUTES", raising=False)
    else:
        monkeypatch.setenv("STALE_THRESHOLD_MINUTES", threshold)
    return stale_claims.main()


def issue(number, labels, created):
    return {"number": number, "created_at": iso(created), "labels": [{"name": n} for n in labels]}


def test_main_flags_once_and_never_removes_labels(monkeypatch, tmp_path):
    old = datetime.now(timezone.utc) - timedelta(hours=6)
    fake = FakeGh(
        issues=[issue(1, [OWNER, CLAIMED], old), issue(2, ["status:ready"], old), {"number": 3, "pull_request": {}, "labels": [{"name": CLAIMED}]}],
        timelines={1: [labeled(old, CLAIMED)]},
    )
    assert run_main(monkeypatch, tmp_path, fake) == 0
    assert fake.writes[0] == ("repos/o/r/issues/1/labels", {"labels": [BLOCKED]})
    path, payload = fake.writes[1]
    assert path == "repos/o/r/issues/1/comments" and payload["body"].startswith(MARKER)
    assert len(fake.writes) == 2

    # Second run: the detector's own comment and label are now on the timeline.
    flagged_at = datetime.now(timezone.utc)
    fake.timelines[1] += [comment(flagged_at, payload["body"]), labeled(flagged_at, BLOCKED)]
    fake.writes.clear()
    assert run_main(monkeypatch, tmp_path, fake) == 0
    assert fake.writes == []
    assert "already_flagged" in Path(tmp_path / "summary.md").read_text()


def test_main_one_minute_threshold(monkeypatch, tmp_path):
    recent = datetime.now(timezone.utc) - timedelta(minutes=3)
    fake = FakeGh(issues=[issue(7, [OWNER, IN_PROGRESS], recent)], timelines={})
    assert run_main(monkeypatch, tmp_path, fake, threshold="240") == 0
    assert fake.writes == []
    assert run_main(monkeypatch, tmp_path, fake, threshold="1") == 0
    assert [w[0] for w in fake.writes] == ["repos/o/r/issues/7/labels", "repos/o/r/issues/7/comments"]


def test_main_rejects_invalid_threshold(monkeypatch, tmp_path):
    fake = FakeGh(issues=[], timelines={})
    assert run_main(monkeypatch, tmp_path, fake, threshold="-5") == 2


def test_workflow_is_pinned_and_least_privilege():
    text = (Path(__file__).resolve().parents[1] / ".github/workflows/stale-claims.yml").read_text()
    uses = [line.split("uses:")[1].strip() for line in text.splitlines() if "uses:" in line]
    assert uses, "workflow should use actions"
    for ref in uses:
        sha = ref.split("@")[1].split()[0]
        assert len(sha) == 40 and all(c in "0123456789abcdef" for c in sha), ref
    assert 'cron: "*/30 * * * *"' in text
    assert "issues: write" in text and "pull-requests: read" in text
    assert "threshold_minutes" in text
