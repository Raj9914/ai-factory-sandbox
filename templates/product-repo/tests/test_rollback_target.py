"""Tests for scripts/rollback_target.py (run: python -m pytest -q tests/test_rollback_target.py).

Deployment histories are in-memory fakes ordered newest first, like the Deployments API;
nothing touches the network.
"""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

TEMPLATE = Path(__file__).resolve().parents[1]
SCRIPT = TEMPLATE / "scripts" / "rollback_target.py"


def load():
    spec = importlib.util.spec_from_file_location("rollback_target", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look the module up while classes are built
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True  # keep scripts/__pycache__ out of the template tree
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


rt = load()

A, B, C, D = ("a" * 40, "b" * 40, "c" * 40, "d" * 40)


def history(*entries):
    """entries: (sha, [states oldest..newest], rollback_from or None), newest first."""
    deployments, statuses = [], {}
    for number, (sha, states, rollback_from) in enumerate(entries, 1):
        deployment = {"id": 1000 - number, "sha": sha,
                      "payload": {"rollback_from": rollback_from} if rollback_from else {}}
        deployments.append(deployment)
        statuses[deployment["id"]] = [{"state": s} for s in reversed(states)]  # API: newest first
    return deployments, statuses


def find(*entries):
    deployments, statuses = history(*entries)
    calls = []

    def get_statuses(deployment_id):
        calls.append(deployment_id)
        return statuses[deployment_id]

    return rt.find_rollback_target(deployments, get_statuses), calls


OK = ["in_progress", "success"]
SUPERSEDED = ["in_progress", "success", "inactive"]


def test_drill_deploy_a_then_b_rolls_back_to_a():
    target, _ = find((B, OK, None), (A, SUPERSEDED, None))
    assert (target.current_sha, target.target_sha) == (B, A)
    assert target.current_deployment_id == 999 and target.target_deployment_id == 998


def test_failed_and_unfinished_deployments_are_ignored():
    target, _ = find((C, ["in_progress", "failure"], None), (D, ["in_progress"], None),
                     (B, OK, None), (A, ["in_progress", "error"], None), (A, SUPERSEDED, None))
    assert (target.current_sha, target.target_sha) == (B, A)


def test_redeploys_of_the_current_sha_are_skipped():
    target, _ = find((B, OK, None), (B, SUPERSEDED, None), (A, SUPERSEDED, None))
    assert target.target_sha == A


def test_second_rollback_goes_further_back_not_to_the_rolled_back_commit():
    # deploy A, B, C; rollback C -> B; rollback again must go to A, never back to C.
    target, _ = find((B, OK, C), (C, SUPERSEDED, None), (B, SUPERSEDED, None), (A, SUPERSEDED, None))
    assert (target.current_sha, target.target_sha) == (B, A)


def test_payload_as_json_string_is_understood():
    deployments, statuses = history((B, OK, None), (C, SUPERSEDED, None), (A, SUPERSEDED, None))
    deployments[0]["payload"] = json.dumps({"rollback_from": C})
    target = rt.find_rollback_target(deployments, statuses.__getitem__)
    assert target.target_sha == A


def test_stops_reading_statuses_once_the_target_is_found():
    _, calls = find((B, OK, None), (A, SUPERSEDED, None), (D, SUPERSEDED, None), (C, SUPERSEDED, None))
    assert calls == [999, 998]


def test_no_successful_deployment_is_an_error():
    with pytest.raises(rt.RollbackError, match="no successful deployment"):
        find((A, ["in_progress", "failure"], None))
    with pytest.raises(rt.RollbackError, match="no successful deployment"):
        find()


def test_only_one_successful_commit_is_an_error():
    with pytest.raises(rt.RollbackError, match="nothing to roll back to"):
        find((A, OK, None), (A, SUPERSEDED, None), (B, ["failure"], None))


def test_invalid_shas_are_ignored():
    target, _ = find((B, OK, None), ("main", SUPERSEDED, None), (A, SUPERSEDED, None))
    assert target.target_sha == A


def test_outputs_are_github_output_lines():
    target, _ = find((B, OK, None), (A, SUPERSEDED, None))
    assert target.outputs() == (f"current_sha={B}\ncurrent_deployment_id=999\n"
                                f"target_sha={A}\ntarget_deployment_id=998\n")


def test_cli_rejects_bad_repo_and_environment(capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert rt.main(["--environment", "staging"]) == 2
    assert "owner/name" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        rt.main(["--environment", "preview", "--repo", "o/r"])


def test_cli_prints_target_from_gh(monkeypatch, capsys):
    deployments, statuses = history((B, OK, None), (A, SUPERSEDED, None))
    paths = []

    def fake_get(self, path):
        paths.append(path)
        if path.startswith("repos/o/r/deployments?"):
            return deployments if "page=1" in path else []
        return statuses[int(path.split("/")[4])]

    monkeypatch.setattr(rt.GhClient, "_get", fake_get)
    assert rt.main(["--environment", "production", "--repo", "o/r"]) == 0
    assert f"target_sha={A}" in capsys.readouterr().out
    assert paths[0] == "repos/o/r/deployments?environment=production&per_page=100&page=1"


def test_cli_reports_missing_history(monkeypatch, capsys):
    monkeypatch.setattr(rt.GhClient, "_get", lambda self, path: [])
    assert rt.main(["--environment", "staging", "--repo", "o/r"]) == 1
    assert "nothing to roll back" in capsys.readouterr().err
