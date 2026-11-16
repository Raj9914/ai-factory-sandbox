#!/usr/bin/env python3
"""Report template drift of a product repository (read-only).

Usage::

    python scripts/template_drift.py <owner>/<repo> [--ref <branch>] [--all]

A product repository provisioned from ``templates/product-repo`` (see
``scripts/provision_product.py``) carries the ``TEMPLATE_VERSION`` it was
created from. This script fetches the repository tree through ``gh api`` and
compares the git blob hashes of the tracked template paths with

1. the template as it was at the version the repository claims (the newest
   commit of this factory repository whose ``templates/product-repo/TEMPLATE_VERSION``
   holds that version), and
2. the latest template (``templates/product-repo`` in this checkout).

By default only safety-relevant paths are tracked (``TRACKED_PATTERNS``:
workflows, issue forms, the PR template, agent files, secret-scan config and
the design-record validator); ``--all`` compares every template file. A file is
``modified`` when its hash differs and ``missing`` when the product repository
does not have it. Product-only files are never reported.

The script only performs ``GET`` calls; it never writes to GitHub or to this
repository. Automatic sync pull requests are out of scope.

Exit codes: 0 no drift, 1 drift found or claimed version unresolved,
2 usage, ``gh`` or GitHub API error.
"""

from __future__ import annotations

import argparse
import base64
import fnmatch
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

try:  # imported as part of the ``scripts`` package (tests)
    from scripts import provision_product as pp
except ImportError:  # run as ``python scripts/template_drift.py``
    import provision_product as pp

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_REL = "templates/product-repo"
VERSION_FILE = "TEMPLATE_VERSION"

TRACKED_PATTERNS = (
    ".github/workflows/*",
    ".github/ISSUE_TEMPLATE/*",
    ".github/PULL_REQUEST_TEMPLATE.md",
    "AGENTS.md",
    "CLAUDE.md",
    ".gitleaks.toml",
    "scripts/validate_design_records.py",
)


class DriftError(Exception):
    """The drift report cannot be produced (bad input, gh or API failure)."""


@dataclass
class Snapshot:
    """Template file hashes (``path -> git blob sha``) at one version."""

    version: str
    source: str
    files: dict


def is_tracked(path, all_files=False):
    if path == VERSION_FILE:
        return False
    return all_files or any(fnmatch.fnmatchcase(path, pattern) for pattern in TRACKED_PATTERNS)


# --------------------------------------------------------------------------
# Factory side: template snapshots
# --------------------------------------------------------------------------

def latest_snapshot(template_dir):
    """Snapshot of the template directory as checked out."""
    version_path = template_dir / VERSION_FILE
    if not version_path.is_file():
        raise DriftError(f"{template_dir} has no {VERSION_FILE}")
    files = {path: pp.git_blob_sha(content) for path, _mode, content in pp.template_files(template_dir)}
    return Snapshot(version_path.read_text().strip(), "working tree", files)


def _git(root, *args):
    proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)
    return proc.returncode, proc.stdout


def snapshot_at_version(root, version, template_rel=TEMPLATE_REL):
    """Newest committed template whose ``TEMPLATE_VERSION`` equals ``version``.

    Returns ``None`` when no commit of the factory repository has that version.
    """
    code, out = _git(root, "log", "--format=%H", "--", template_rel)
    if code != 0:
        raise DriftError(f"cannot read git history of {root}")
    for commit in out.split():
        code, content = _git(root, "show", f"{commit}:{template_rel}/{VERSION_FILE}")
        if code != 0 or content.strip() != version:
            continue
        code, listing = _git(root, "ls-tree", "-r", commit, "--", f"{template_rel}/")
        if code != 0:
            raise DriftError(f"cannot list {template_rel} at {commit}")
        files = {}
        for line in listing.splitlines():
            meta, _, path = line.partition("\t")
            _mode, kind, sha = meta.split()
            if kind == "blob":
                files[path[len(template_rel) + 1:]] = sha
        return Snapshot(version, f"factory commit {commit[:12]}", files)
    return None


# --------------------------------------------------------------------------
# Product side: repository tree through gh api
# --------------------------------------------------------------------------

def fetch_repo_tree(runner, repo, ref):
    """Return ``path -> blob sha`` for every file of ``repo`` at ``ref``."""
    path = f"repos/{repo}/git/trees/{quote(ref, safe='')}?recursive=1"
    response = runner("GET", path, None, False)
    if response.status != 200 or not isinstance(response.data, dict):
        raise DriftError(f"GET {path} returned HTTP {response.status}")
    if response.data.get("truncated"):
        raise DriftError(f"tree of {repo} at {ref} is truncated by the API; cannot compare reliably")
    return {entry["path"]: entry["sha"] for entry in response.data.get("tree", [])
            if entry.get("type") == "blob"}


def fetch_blob_text(runner, repo, sha):
    path = f"repos/{repo}/git/blobs/{sha}"
    response = runner("GET", path, None, False)
    if response.status != 200 or not isinstance(response.data, dict):
        raise DriftError(f"GET {path} returned HTTP {response.status}")
    content = response.data.get("content", "")
    if response.data.get("encoding") == "base64":
        return base64.b64decode(content).decode("utf-8", errors="replace")
    return content


# --------------------------------------------------------------------------
# Comparison and report
# --------------------------------------------------------------------------

def compare(template_files, repo_files, all_files=False):
    """Return sorted ``[(status, path)]`` for tracked template paths that differ."""
    drift = []
    for path in sorted(template_files):
        if not is_tracked(path, all_files):
            continue
        if path not in repo_files:
            drift.append(("missing", path))
        elif repo_files[path] != template_files[path]:
            drift.append(("modified", path))
    return drift


def _section(label, drift, out):
    count = len(drift)
    out(f"drift vs {label}: {count} file{'' if count == 1 else 's'}")
    for status, path in drift:
        out(f"  {status:<8}  {path}")


def report(runner, repo, ref="HEAD", all_files=False, root=ROOT, template_dir=None, out=print):
    """Print the drift report. Returns the exit code."""
    template_dir = template_dir or (root / TEMPLATE_REL)
    latest = latest_snapshot(template_dir)
    repo_files = fetch_repo_tree(runner, repo, ref)

    out(f"repo: {repo} (ref {ref})")
    out(f"tracked: {'all template files' if all_files else 'safety-relevant paths'}")
    attention = False
    if VERSION_FILE not in repo_files:
        out(f"claimed template version: none ({VERSION_FILE} missing)")
        attention = True
    else:
        claimed_version = fetch_blob_text(runner, repo, repo_files[VERSION_FILE]).strip()
        claimed = snapshot_at_version(root, claimed_version)
        if claimed is None:
            out(f"claimed template version: {claimed_version} (unknown to this factory repository)")
            attention = True
        else:
            out(f"claimed template version: {claimed.version} ({claimed.source})")
            drift = compare(claimed.files, repo_files, all_files)
            _section(f"claimed {claimed.version}", drift, out)
            attention = attention or bool(drift)

    out(f"latest template version: {latest.version} ({latest.source})")
    drift = compare(latest.files, repo_files, all_files)
    _section(f"latest {latest.version}", drift, out)
    attention = attention or bool(drift)
    return 1 if attention else 0


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("repo", help="product repository as <owner>/<name>")
    parser.add_argument("--ref", default="HEAD", help="branch, tag or commit to inspect (default: HEAD)")
    parser.add_argument("--all", action="store_true", dest="all_files",
                        help="compare every template file, not only safety-relevant paths")
    return parser


def main(argv=None, runner=None, out=print, root=ROOT, template_dir=None):
    args = build_parser().parse_args(argv)
    if args.repo.count("/") != 1 or not all(args.repo.split("/")):
        out("error: repo must be <owner>/<name>")
        return 2
    guarded = pp.read_only(runner or pp.gh_runner)
    try:
        return report(guarded, args.repo, args.ref, args.all_files, root, template_dir, out)
    except (DriftError, pp.GhUnavailable) as exc:
        out(f"error: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
