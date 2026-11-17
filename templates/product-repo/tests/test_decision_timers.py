"""Tests for scripts/decision_timers.py (run: python -m pytest -q tests/test_decision_timers.py).

All tests use a frozen clock and an in-memory fake GitHub client; nothing touches the network.
"""
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import re
import shutil
import sys

import pytest

TEMPLATE = Path(__file__).resolve().parents[1]
SCRIPT = TEMPLATE / "scripts" / "decision_timers.py"
WORKFLOW = TEMPLATE / ".github" / "workflows" / "decision-timers.yml"


def load():
    spec = importlib.util.spec_from_file_location("decision_timers", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look the module up while classes are built
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True  # keep scripts/__pycache__ out of the template tree
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


dt = load()

HOUR_0 = datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc)
EXP = "demo-20261111"


class FakeGitHub:
    """In-memory issues API: stores created issues, returns them from list_issues."""

    def __init__(self, issues=None):
        self.issues = list(issues or [])
        self.created = []

    def list_issues(self):
        return [dict(issue) for issue in self.issues]

    def create_issue(self, title, body, labels):
        issue = {"number": 100 + len(self.issues), "title": title, "body": body,
                 "labels": [{"name": n} for n in labels], "state": "open",
                 "html_url": f"https://github.example/issues/{100 + len(self.issues)}"}
        self.issues.append(issue)
        self.created.append(issue)
        return issue


@pytest.fixture
def repo(tmp_path):
    """A product-repo-like tree: experiments/<id>/experiment.yml plus the decision templates."""
    (tmp_path / "docs" / "templates").mkdir(parents=True)
    for name in ("HOUR_24_DECISION_TEMPLATE.md", "HOUR_48_DECISION_TEMPLATE.md"):
        shutil.copy(TEMPLATE / "docs" / "templates" / name, tmp_path / "docs" / "templates" / name)
    folder = tmp_path / "experiments" / EXP
    folder.mkdir(parents=True)
    (folder / "experiment.yml").write_text(
        f"# experiment\nid: {EXP}\nhour_0: 2026-11-11T09:00:00Z  # start\nmetrics:\n"
        "  - name: activation_rate\n    numerator: activation\n    denominator: app_opened\n",
        encoding="utf-8")
    return tmp_path


def run(repo, client, now, **kwargs):
    lines = []
    code = dt.run(repo / "experiments", client, now, repo_url="https://git.example/acme/demo",
                  branch="main", out=lines.append, **kwargs)
    return code, lines


def by_checkpoint(client):
    counts = {}
    for issue in client.issues:
        m = re.search(r"experiment=(\S+) checkpoint=(\d+)", issue["body"])
        counts[(m.group(1), int(m.group(2)))] = counts.get((m.group(1), int(m.group(2))), 0) + 1
    return counts


# --------------------------------------------------------------------------- acceptance criterion

def test_exactly_one_issue_per_checkpoint_after_three_runs(repo):
    client = FakeGitHub()
    for now in (HOUR_0 + timedelta(hours=49), HOUR_0 + timedelta(hours=50),
                HOUR_0 + timedelta(hours=51)):
        code, _ = run(repo, client, now)
        assert code == 0
    assert by_checkpoint(client) == {(EXP, 24): 1, (EXP, 48): 1}


def test_hourly_runs_across_the_window_open_each_checkpoint_once_on_time(repo):
    client = FakeGitHub()
    opened_at = {}
    for hour in range(0, 60):
        now = HOUR_0 + timedelta(hours=hour, minutes=17)
        before = len(client.issues)
        run(repo, client, now)
        for issue in client.issues[before:]:
            opened_at[int(re.search(r"checkpoint=(\d+)", issue["body"]).group(1))] = hour
    assert opened_at == {24: 24, 48: 48}
    assert by_checkpoint(client) == {(EXP, 24): 1, (EXP, 48): 1}


def test_three_runs_before_hour_24_open_nothing(repo):
    client = FakeGitHub()
    for hours in (0, 12, 23.99):
        assert run(repo, client, HOUR_0 + timedelta(hours=hours))[0] == 0
    assert client.issues == []


def test_between_checkpoints_only_hour_24_is_open(repo):
    client = FakeGitHub()
    for hours in (24, 30, 47):
        run(repo, client, HOUR_0 + timedelta(hours=hours))
    assert by_checkpoint(client) == {(EXP, 24): 1}


def test_existing_closed_issue_with_marker_is_not_duplicated(repo):
    closed = {"number": 7, "state": "closed", "title": "renamed by a human",
              "body": "notes\n" + dt.marker(EXP, 24)}
    client = FakeGitHub([closed])
    code, lines = run(repo, client, HOUR_0 + timedelta(hours=25))
    assert code == 0
    assert client.created == []
    assert any("issue #7 already exists" in line for line in lines)


def test_existing_issue_with_same_title_but_no_marker_is_not_duplicated(repo):
    client = FakeGitHub([{"number": 8, "title": dt.issue_title(EXP, 24), "body": "edited"}])
    run(repo, client, HOUR_0 + timedelta(hours=25))
    assert client.created == []


def test_pull_requests_with_the_marker_are_ignored(repo):
    pr = {"number": 9, "title": "x", "body": dt.marker(EXP, 24), "pull_request": {}}
    client = FakeGitHub([pr])
    run(repo, client, HOUR_0 + timedelta(hours=25))
    assert len(client.created) == 1


def test_marker_is_per_experiment_and_checkpoint(repo):
    other = repo / "experiments" / "other-20261112"
    other.mkdir()
    (other / "experiment.yml").write_text("hour_0: 2026-11-12T09:00:00+00:00\n", encoding="utf-8")
    client = FakeGitHub()
    for _ in range(3):
        run(repo, client, HOUR_0 + timedelta(hours=49))
    assert by_checkpoint(client) == {(EXP, 24): 1, (EXP, 48): 1, ("other-20261112", 24): 1}


def test_existing_decision_record_skips_the_checkpoint(repo):
    (repo / "experiments" / EXP / "decision-24h.md").write_text("recorded\n", encoding="utf-8")
    (repo / "experiments" / EXP / "HOUR_48.md").write_text("recorded\n", encoding="utf-8")
    client = FakeGitHub()
    code, lines = run(repo, client, HOUR_0 + timedelta(hours=49))
    assert code == 0 and client.created == []
    assert sum("decision record already exists" in line for line in lines) == 2


def test_dry_run_opens_nothing(repo):
    client = FakeGitHub()
    code, lines = run(repo, client, HOUR_0 + timedelta(hours=49), dry_run=True)
    assert code == 0 and client.created == []
    assert sum(line.startswith("would open") for line in lines) == 2


# --------------------------------------------------------------------------- issue content

def test_issue_is_owned_by_human_and_prefilled_from_templates(repo):
    client = FakeGitHub()
    run(repo, client, HOUR_0 + timedelta(hours=49))
    h24, h48 = sorted(client.created, key=lambda i: i["title"])
    for issue in (h24, h48):
        names = [label["name"] for label in issue["labels"]]
        assert "type:decision" in names and "agent:human" in names
        assert not any(n.startswith("agent:") and n != "agent:human" for n in names)
        assert "<Experiment ID>" not in issue["body"]
        assert "| Hour 0 (ISO) | 2026-11-11T09:00:00Z |" in issue["body"]
    assert h24["title"] == f"[DECISION] {EXP}: hour-24 decision checkpoint"
    assert f"# {EXP} — Hour-24 Decision" in h24["body"]
    assert "**KEEP_SCOPE** or **CUT_SCOPE**" in h24["body"]
    assert "| Checkpoint due (ISO) | 2026-11-12T09:00:00Z |" in h24["body"]
    assert f"# {EXP} — Hour-48 Decision" in h48["body"]
    assert "| Hour 0 / recorded at (ISO) | 2026-11-11T09:00:00Z / |" in h48["body"]
    assert "| Checkpoint due (ISO) | 2026-11-13T09:00:00Z |" in h48["body"]
    assert h48["body"].startswith(dt.marker(EXP, 48))


def test_issue_links_latest_metrics_and_cost_reports(repo):
    folder = repo / "experiments" / EXP
    (folder / "metrics.md").write_text("# metrics\n", encoding="utf-8")
    client = FakeGitHub()
    run(repo, client, HOUR_0 + timedelta(hours=25))
    body = client.created[0]["body"]
    base = f"https://git.example/acme/demo/blob/main/experiments/{EXP}"
    assert f"- Metrics: [`experiments/{EXP}/metrics.md`]({base}/metrics.md)\n" in body
    assert f"- Cost: [`experiments/{EXP}/cost.md`]({base}/cost.md) (not generated yet: NOT AVAILABLE)" in body


def test_cost_link_falls_back_to_cost_log(repo):
    (repo / "experiments" / EXP / "COST_LOG.md").write_text("# cost\n", encoding="utf-8")
    client = FakeGitHub()
    run(repo, client, HOUR_0 + timedelta(hours=25))
    assert f"/experiments/{EXP}/COST_LOG.md)\n" in client.created[0]["body"]


def test_missing_template_still_opens_issue_with_note(repo):
    (repo / "docs" / "templates" / "HOUR_24_DECISION_TEMPLATE.md").unlink()
    client = FakeGitHub()
    run(repo, client, HOUR_0 + timedelta(hours=25))
    assert "HOUR_24_DECISION_TEMPLATE.md not found" in client.created[0]["body"]


# --------------------------------------------------------------------------- experiment.yml

@pytest.mark.parametrize("text, expected", [
    ("hour_0: 2026-11-11T09:00:00Z\n", HOUR_0),
    ('hour_0: "2026-11-11T10:00:00+01:00"  # quoted\n', HOUR_0),
    ("id: x\nhour_0: '2026-11-11T09:00:00Z'\n", HOUR_0),
    ("metrics:\n  - hour_0: 2020-01-01T00:00:00Z\nhour_0: 2026-11-11T09:00:00Z\n", HOUR_0),
])
def test_parse_hour_0(text, expected):
    assert dt.parse_hour_0(text) == expected


@pytest.mark.parametrize("text, message", [
    ("id: x\n", "no top-level hour_0"),
    ("hour_0:\n", "no top-level hour_0"),
    ("hour_0: 2026-11-11T09:00:00\n", "must include a timezone"),
    ("hour_0: tomorrow\n", "not ISO 8601"),
    ("hour_0: 2026-11-11T09:00:00Z\nhour_0: 2026-11-12T09:00:00Z\n", "more than one hour_0"),
])
def test_parse_hour_0_rejects(text, message):
    with pytest.raises(dt.TimerError, match=message):
        dt.parse_hour_0(text)


def test_invalid_experiment_reports_error_but_others_still_run(repo):
    bad = repo / "experiments" / "bad-20261111"
    bad.mkdir()
    (bad / "experiment.yml").write_text("hour_0: soon\n", encoding="utf-8")
    client = FakeGitHub()
    code, lines = run(repo, client, HOUR_0 + timedelta(hours=25))
    assert code == 1
    assert any(line.startswith("ERROR") and "bad-20261111" in line for line in lines)
    assert by_checkpoint(client) == {(EXP, 24): 1}


def test_no_experiments_is_a_no_op(tmp_path):
    client = FakeGitHub()
    lines = []
    code = dt.run(tmp_path / "experiments", client, HOUR_0 + timedelta(hours=49), out=lines.append)
    assert code == 0 and client.issues == []
    assert lines == [f"no experiments with experiment.yml under {tmp_path / 'experiments'}; nothing to do"]


def test_folders_without_experiment_yml_are_ignored(repo):
    (repo / "experiments" / "notes").mkdir()
    client = FakeGitHub()
    assert run(repo, client, HOUR_0 + timedelta(hours=25))[0] == 0
    assert len(client.created) == 1


def test_due_checkpoints_boundaries():
    assert dt.due_checkpoints(HOUR_0, HOUR_0 + timedelta(hours=24) - timedelta(seconds=1)) == []
    assert dt.due_checkpoints(HOUR_0, HOUR_0 + timedelta(hours=24)) == [24]
    assert dt.due_checkpoints(HOUR_0, HOUR_0 + timedelta(hours=48)) == [24, 48]


# --------------------------------------------------------------------------- CLI

def test_main_with_frozen_clock_and_injected_client(repo, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/demo")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://git.example")
    monkeypatch.setenv("DEFAULT_BRANCH", "trunk")
    client = FakeGitHub()
    for _ in range(3):
        assert dt.main([str(repo / "experiments"), "--now", "2026-11-13T10:17:00Z"], client=client) == 0
    assert by_checkpoint(client) == {(EXP, 24): 1, (EXP, 48): 1}
    assert "https://git.example/acme/demo/blob/trunk/experiments/" in client.created[0]["body"]
    assert "opened" in capsys.readouterr().out


def test_main_dry_run_needs_no_repository(repo, monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert dt.main([str(repo / "experiments"), "--now", "2026-11-13T10:17:00Z", "--dry-run"]) == 0
    assert capsys.readouterr().out.count("would open") == 2


def test_main_requires_repository_when_not_dry_run(repo, monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert dt.main([str(repo / "experiments"), "--now", "2026-11-13T10:17:00Z"]) == 2


def test_main_rejects_naive_now(repo):
    assert dt.main([str(repo / "experiments"), "--now", "2026-11-13T10:17:00", "--dry-run"]) == 2


def test_gh_client_paginates_and_posts(monkeypatch):
    calls = []

    class Result:
        def __init__(self, stdout):
            self.returncode, self.stdout, self.stderr = 0, stdout, ""

    def fake_run(cmd, input=None, **kwargs):
        calls.append((cmd, input))
        if "POST" in cmd:
            return Result('{"number": 5, "html_url": "u"}')
        return Result('[[{"number": 1}], [{"number": 2, "pull_request": {}}]]')

    monkeypatch.setattr(dt.subprocess, "run", fake_run)
    client = dt.GhClient("acme/demo")
    assert [i["number"] for i in client.list_issues()] == [1, 2]
    assert calls[0][0][:4] == ["gh", "api", "--paginate", "--slurp"]
    assert "repos/acme/demo/issues?state=all&per_page=100" in calls[0][0]
    assert client.create_issue("t", "b", ["type:decision"])["number"] == 5
    assert calls[1][0][-4:] == ["-X", "POST", "repos/acme/demo/issues", "--input", "-"][-4:]
    assert '"labels": ["type:decision"]' in calls[1][1]


# --------------------------------------------------------------------------- workflow

def test_workflow_is_hourly_least_privilege_and_pinned():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r'cron:\s*"\d{1,2} \* \* \* \*"', text), "must run hourly"
    assert "permissions: {}" in text
    assert re.findall(r"^\s+(\w[\w-]*):\s*write", text, re.M) == ["issues"]
    assert "pull_request_target" not in text
    uses = re.findall(r"uses:\s*([^\s#]+)", text)
    assert uses and all(re.search(r"@[0-9a-f]{40}$", ref) for ref in uses)
    assert "persist-credentials: false" in text
    assert "cancel-in-progress: false" in text
    assert "scripts/decision_timers.py experiments" in text
    assert "${{ github.event" not in text.split("run:")[-1], "no event data interpolated into run"
