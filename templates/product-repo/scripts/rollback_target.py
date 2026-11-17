#!/usr/bin/env python3
"""Find the commit a rollback should redeploy, from GitHub Deployment records (stdlib only).

Usage: python3 scripts/rollback_target.py --environment <staging|production> [--repo owner/name]

Run by .github/workflows/rollback.yml before it redeploys. It reads the Deployments API
(newest first) for one environment and prints, as `key=value` lines for $GITHUB_OUTPUT:

  current_sha=<sha>            commit of the newest deployment that ever reached `success`
  current_deployment_id=<id>
  target_sha=<sha>             newest earlier successful deployment with a different commit
  target_deployment_id=<id>

"Successful" means the deployment has a `success` status at any point (GitHub marks older
successful deployments `inactive` once a newer one succeeds, so the latest status alone is
not enough). Failed, errored and never-finished deployments are ignored.

Rollbacks are recorded by deploy.yml with payload `{"rollback_from": "<sha>"}`. Commits that
were rolled back from are never chosen as a target, so a second rollback goes further back
instead of re-deploying the commit that was just rolled back.

Exits 1 with a message on stderr when there is no current or no earlier successful deployment.

Environment: GH_TOKEN and GITHUB_REPOSITORY (owner/name; or pass --repo). The pure function
`find_rollback_target` is unit-tested; `GhClient` is a thin wrapper around the `gh` CLI.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
import re
import subprocess
import sys
from typing import Callable, Iterable

ENVIRONMENTS = ("staging", "production")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
PER_PAGE = 100
MAX_PAGES = 10  # 1,000 deployments is far more history than a 48-hour experiment produces


class RollbackError(RuntimeError):
    """No usable deployment history for a rollback."""


@dataclass(frozen=True)
class Target:
    current_sha: str
    current_deployment_id: int
    target_sha: str
    target_deployment_id: int

    def outputs(self) -> str:
        return (f"current_sha={self.current_sha}\n"
                f"current_deployment_id={self.current_deployment_id}\n"
                f"target_sha={self.target_sha}\n"
                f"target_deployment_id={self.target_deployment_id}\n")


def rolled_back_from(deployment: dict) -> str:
    """The commit a rollback deployment replaced ("" for a normal deployment)."""
    payload = deployment.get("payload") or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return ""
    if not isinstance(payload, dict):
        return ""
    sha = payload.get("rollback_from") or ""
    return sha if isinstance(sha, str) and SHA_RE.fullmatch(sha) else ""


def ever_succeeded(statuses: Iterable[dict]) -> bool:
    return any(status.get("state") == "success" for status in statuses)


def find_rollback_target(deployments: Iterable[dict],
                         get_statuses: Callable[[int], list[dict]]) -> Target:
    """Pick the rollback target from deployments ordered newest first.

    The first successful deployment is the current one; the target is the next successful
    deployment whose commit differs from the current commit and was never rolled back from.
    """
    current = None
    excluded: set[str] = set()
    for deployment in deployments:
        sha = deployment.get("sha") or ""
        if not SHA_RE.fullmatch(sha):
            continue
        if not ever_succeeded(get_statuses(deployment["id"])):
            continue
        replaced = rolled_back_from(deployment)
        if replaced:
            excluded.add(replaced)
        if current is None:
            current = deployment
            excluded.add(sha)
            continue
        if sha not in excluded:
            return Target(current["sha"], current["id"], sha, deployment["id"])
    if current is None:
        raise RollbackError("no successful deployment found; nothing to roll back")
    raise RollbackError(f"no earlier successful deployment with a different commit than "
                        f"{current['sha']}; nothing to roll back to")


class GhClient:
    """Reads deployments through the `gh` CLI (uses GH_TOKEN from the environment)."""

    def __init__(self, repo: str):
        self.repo = repo

    def _get(self, path: str) -> list[dict]:
        result = subprocess.run(["gh", "api", path], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RollbackError(f"gh api {path} failed: {result.stderr.strip()}")
        return json.loads(result.stdout or "[]")

    def deployments(self, environment: str) -> Iterable[dict]:
        for page in range(1, MAX_PAGES + 1):
            batch = self._get(f"repos/{self.repo}/deployments?environment={environment}"
                              f"&per_page={PER_PAGE}&page={page}")
            yield from batch
            if len(batch) < PER_PAGE:
                return

    def statuses(self, deployment_id: int) -> list[dict]:
        return self._get(f"repos/{self.repo}/deployments/{int(deployment_id)}/statuses?per_page={PER_PAGE}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--environment", required=True, choices=ENVIRONMENTS)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    args = parser.parse_args(argv)
    if not REPO_RE.fullmatch(args.repo):
        print("rollback_target: --repo or GITHUB_REPOSITORY must be owner/name", file=sys.stderr)
        return 2
    client = GhClient(args.repo)
    try:
        target = find_rollback_target(client.deployments(args.environment), client.statuses)
    except RollbackError as exc:
        print(f"rollback_target: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(target.outputs())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
