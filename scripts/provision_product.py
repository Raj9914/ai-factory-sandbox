#!/usr/bin/env python3
"""Provision a private product repository from ``templates/product-repo``.

See ADR 0008 (one private repository per product) and the "Provisioning a
product repository" section of ``docs/runbooks/DEPLOYMENT_AND_ROLLBACK.md``.

Usage::

    python scripts/provision_product.py --slug <slug>                          # dry run
    python scripts/provision_product.py --slug <slug> --apply --confirm <slug>

The desired state is data (``LABELS``, ``desired_*`` functions). Each resource
is handled as read -> compare -> apply: the script reads the live state, plans
only the write calls needed to reach the desired state, and (with ``--apply``)
performs them. A dry run (the default) performs read calls only and prints every
write call it would make. Re-running on a provisioned repository plans 0 changes;
any planned change on an existing repository is reported as drift.

Resources, in apply order:

1. repository   private (``--visibility public`` needs ``--allow-public``)
2. settings     auto-merge off, delete branch on merge on
3. template     one initial commit with every file of ``templates/product-repo``
                (only into a repository that holds nothing but the auto-init
                README; never overwrites product content)
4. labels       the factory label set (created/updated, never deleted)
5. protection   ``main``: PR required, ``test`` check required, enforce_admins,
                ``--required-approvals`` approvals (default 0, see ADR 0006), no
                force pushes or deletions
6. environments preview, staging (protected branches only), production
                (required reviewer + protected branches only)

All API calls go through the ``gh`` CLI, so the human's existing ``gh`` login is
used at apply time. The script never reads, prints or stores a token. When
``gh`` cannot reach GitHub during a dry run, the plan assumes the repository
does not exist yet.

The script finally prints an orchestrator manifest stub (PR-only delivery).

Exit codes: 0 OK, 1 GitHub API error, 2 usage error or refused option.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = ROOT / "templates" / "product-repo"
DEFAULT_BRANCH = "main"
REQUIRED_CHECK = "test"
ENVIRONMENTS = ("preview", "staging", "production")
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
OWNER_PLACEHOLDER = "<owner>"
REVIEWER_ID_PLACEHOLDER = "<reviewer-id>"
SEED_MESSAGE = "Initial product repository from templates/product-repo"

# The factory label set (docs/AGENT_HANDOFF_PROTOCOL.md, ADR 0006, issue forms,
# GitHub defaults and Dependabot). Legacy ai:* / ai-factory labels belong to
# ai-work-orchestrator and are not copied to product repositories.
LABELS = (
    ("agent:chatgpt", "1d76db", "Owner: ChatGPT"),
    ("agent:claude", "1d76db", "Owner: Claude"),
    ("agent:codex", "1d76db", "Owner: Codex"),
    ("agent:human", "1d76db", "Owner: human"),
    ("status:proposed", "ededed", "Proposed; not ready for work"),
    ("status:approved", "c2e0c6", "Approved; not yet ready"),
    ("status:ready", "0e8a16", "Ready for work (set by a human only)"),
    ("status:claimed", "fbca04", "Claimed by its owner"),
    ("status:in_progress", "fbca04", "Work in progress"),
    ("status:review", "5319e7", "In review"),
    ("status:accepted", "0e8a16", "Accepted by review"),
    ("status:done", "0e8a16", "Done (set by a human only)"),
    ("status:blocked", "b60205", "Blocked"),
    ("status:cancelled", "cfd3d7", "Cancelled"),
    ("status:killed", "000000", "Experiment killed"),
    ("type:idea", "c5def5", "Product or game idea"),
    ("type:bug", "d73a4a", "Defect"),
    ("type:task", "c5def5", "Engineering task"),
    ("type:decision", "c5def5", "Decision record"),
    ("type:feedback", "c5def5", "User feedback"),
    ("area:design", "bfdadc", "Design and screens"),
    ("area:frontend", "bfdadc", "Frontend"),
    ("area:backend", "bfdadc", "Backend"),
    ("area:testing", "bfdadc", "Testing"),
    ("priority:P0", "b60205", "Priority P0"),
    ("priority:P1", "d93f0b", "Priority P1"),
    ("priority:P2", "fbca04", "Priority P2"),
    ("priority:P3", "0e8a16", "Priority P3"),
    ("bug", "d73a4a", "Something isn't working"),
    ("documentation", "0075ca", "Improvements or additions to documentation"),
    ("duplicate", "cfd3d7", "This issue or pull request already exists"),
    ("enhancement", "a2eeef", "New feature or request"),
    ("good first issue", "7057ff", "Good for newcomers"),
    ("help wanted", "008672", "Extra attention is needed"),
    ("invalid", "e4e669", "This doesn't seem right"),
    ("question", "d876e3", "Further information is requested"),
    ("wontfix", "ffffff", "This will not be worked on"),
    ("dependencies", "0366d6", "Pull requests that update a dependency file"),
    ("github_actions", "000000", "Pull requests that update GitHub Actions code"),
    ("python", "2b67c6", "Pull requests that update Python code"),
)


class GhUnavailable(Exception):
    """``gh`` is missing, not logged in or cannot reach GitHub."""


class ApiError(Exception):
    """A GitHub API call returned an unexpected status."""


@dataclass
class Response:
    status: int
    data: object = None


@dataclass
class Call:
    """One planned write call. ``${name}`` strings in ``body`` are filled from
    ``save`` values of earlier calls; ``summary`` replaces a long body in output."""

    method: str
    path: str
    body: object = None
    summary: str = ""
    save: tuple = ()  # (name, response key)

    def render(self):
        if self.summary:
            detail = self.summary
        elif self.body is None:
            detail = ""
        else:
            detail = json.dumps(self.body, sort_keys=True)
        return f"{self.method} {self.path}" + (f" {detail}" if detail else "")


@dataclass
class Change:
    resource: str
    description: str
    calls: list


@dataclass
class Context:
    owner: str
    slug: str
    private: bool = True
    required_approvals: int = 0
    reviewer: str = ""
    reviewer_id: object = REVIEWER_ID_PLACEHOLDER
    owner_is_org: bool = False
    repo_exists: bool = False
    offline: bool = False
    template_dir: Path = TEMPLATE_DIR
    sleep: object = time.sleep
    drift: list = field(default_factory=list)

    @property
    def repo(self):
        return f"repos/{self.owner}/{self.slug}"

    @property
    def reads_possible(self):
        """False when reading would only return 404 (dry run of a new repo, or offline)."""
        return self.repo_exists and not self.offline


# --------------------------------------------------------------------------
# gh runner
# --------------------------------------------------------------------------

def _decode_concatenated_json(text):
    """``gh api --paginate`` prints one JSON array per page back to back."""
    decoder = json.JSONDecoder()
    values, index, text = [], 0, text.strip()
    while index < len(text):
        value, index = decoder.raw_decode(text, index)
        values.append(value)
        while index < len(text) and text[index].isspace():
            index += 1
    if len(values) == 1:
        return values[0]
    merged = []
    for value in values:
        merged.extend(value if isinstance(value, list) else [value])
    return merged


def gh_runner(method, path, body=None, paginate=False):
    """Call the GitHub REST API through ``gh api`` (uses the human's gh login)."""
    cmd = ["gh", "api", "--method", method, path, "-H", "Accept: application/vnd.github+json"]
    if paginate:
        cmd.append("--paginate")
    if body is not None:
        cmd += ["--input", "-"]
    try:
        proc = subprocess.run(cmd, input=None if body is None else json.dumps(body),
                              capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise GhUnavailable("gh CLI not found") from exc
    data = None
    if proc.stdout.strip():
        try:
            data = _decode_concatenated_json(proc.stdout)
        except ValueError:
            data = proc.stdout
    if proc.returncode == 0:
        return Response(200, data)
    match = re.search(r"HTTP (\d{3})", proc.stderr)
    if not match:
        raise GhUnavailable(proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "gh api failed")
    status = int(match.group(1))
    if status == 401:
        raise GhUnavailable("gh is not authenticated (HTTP 401)")
    return Response(status, data)


def read_only(runner):
    """Wrap a runner so that any non-GET call raises (dry-run safety net)."""
    def guarded(method, path, body=None, paginate=False):
        if method != "GET":
            raise RuntimeError(f"dry run attempted a write call: {method} {path}")
        return runner(method, path, body, paginate)
    return guarded


def _get(runner, path, paginate=False, allow=(404,)):
    response = runner("GET", path, None, paginate)
    if response.status == 200 or response.status in allow:
        return response
    raise ApiError(f"GET {path} returned HTTP {response.status}: {response.data}")


# --------------------------------------------------------------------------
# Desired state
# --------------------------------------------------------------------------

def desired_settings():
    return {"allow_auto_merge": False, "delete_branch_on_merge": True}


def desired_protection(required_approvals):
    """Comparable form of the desired ``main`` protection."""
    return {
        "required_checks": [REQUIRED_CHECK],
        "strict": False,
        "enforce_admins": True,
        "pull_request_required": True,
        "required_approvals": required_approvals,
        "allow_force_pushes": False,
        "allow_deletions": False,
    }


def protection_body(required_approvals):
    """``PUT .../branches/main/protection`` request body."""
    return {
        "required_status_checks": {"strict": False, "contexts": [REQUIRED_CHECK]},
        "enforce_admins": True,
        "required_pull_request_reviews": {
            "required_approving_review_count": required_approvals,
            "dismiss_stale_reviews": False,
            "require_code_owner_reviews": False,
        },
        "restrictions": None,
        "allow_force_pushes": False,
        "allow_deletions": False,
    }


PROTECTED_BRANCHES_ONLY = {"protected_branches": True, "custom_branch_policies": False}


def desired_environments(reviewer_id):
    """Comparable form: name -> {reviewers, branch_policy}."""
    return {
        "preview": {"reviewers": [], "branch_policy": None},
        "staging": {"reviewers": [], "branch_policy": dict(PROTECTED_BRANCHES_ONLY)},
        "production": {"reviewers": [["User", reviewer_id]], "branch_policy": dict(PROTECTED_BRANCHES_ONLY)},
    }


def environment_body(desired):
    body = {"deployment_branch_policy": desired["branch_policy"]}
    if desired["reviewers"]:
        body["reviewers"] = [{"type": kind, "id": rid} for kind, rid in desired["reviewers"]]
    return body


def git_blob_sha(content):
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()


def template_files(template_dir):
    """Return sorted ``[(path, mode, bytes)]`` for every file in the template."""
    files = []
    for path in sorted(template_dir.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        mode = "100755" if path.stat().st_mode & 0o111 else "100644"
        files.append((path.relative_to(template_dir).as_posix(), mode, path.read_bytes()))
    return files


# --------------------------------------------------------------------------
# Normalisers (live API shape -> comparable form)
# --------------------------------------------------------------------------

def normalize_protection(data):
    if not data:
        return None
    checks = data.get("required_status_checks") or {}
    reviews = data.get("required_pull_request_reviews")
    return {
        "required_checks": sorted(checks.get("contexts") or []),
        "strict": bool(checks.get("strict")),
        "enforce_admins": bool((data.get("enforce_admins") or {}).get("enabled")),
        "pull_request_required": reviews is not None,
        "required_approvals": (reviews or {}).get("required_approving_review_count", 0),
        "allow_force_pushes": bool((data.get("allow_force_pushes") or {}).get("enabled")),
        "allow_deletions": bool((data.get("allow_deletions") or {}).get("enabled")),
    }


def normalize_environment(data):
    reviewers = []
    for rule in data.get("protection_rules") or []:
        if rule.get("type") == "required_reviewers":
            for item in rule.get("reviewers") or []:
                reviewers.append([item.get("type"), (item.get("reviewer") or {}).get("id")])
    policy = data.get("deployment_branch_policy")
    if policy is not None:
        policy = {"protected_branches": bool(policy.get("protected_branches")),
                  "custom_branch_policies": bool(policy.get("custom_branch_policies"))}
    return {"reviewers": sorted(reviewers, key=str), "branch_policy": policy}


# --------------------------------------------------------------------------
# Planners: read live state, return the changes needed
# --------------------------------------------------------------------------

def plan_repository(runner, ctx):
    if not ctx.offline:
        ctx.repo_exists = _get(runner, ctx.repo).status == 200
    if ctx.repo_exists:
        return []
    body = {"name": ctx.slug, "private": ctx.private, "auto_init": True, **desired_settings()}
    path = f"orgs/{ctx.owner}/repos" if ctx.owner_is_org else "user/repos"
    visibility = "private" if ctx.private else "public"
    return [Change("repository", f"create {visibility} repository {ctx.owner}/{ctx.slug}",
                   [Call("POST", path, body)])]


def plan_settings(runner, ctx):
    if not ctx.reads_possible:
        return []  # a new repository is created with the desired settings
    current = _get(runner, ctx.repo, allow=()).data or {}
    patch = {key: value for key, value in desired_settings().items() if current.get(key) != value}
    if ctx.private and current.get("private") is not True:
        patch["visibility"] = "private"
    if not patch:
        return []
    return [Change("settings", "set " + ", ".join(f"{k}={v}" for k, v in sorted(patch.items())),
                   [Call("PATCH", ctx.repo, patch)])]


def _seed_calls(ctx, files, parent_sha):
    tree = []
    for path, mode, content in files:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ApiError(f"template file is not UTF-8 text: {path}") from exc
        tree.append({"path": path, "mode": mode, "type": "blob", "content": text})
    paths = ", ".join(path for path, _, _ in files)
    return [
        Call("POST", f"{ctx.repo}/git/trees", {"tree": tree},
             summary=f"({len(files)} files from templates/product-repo: {paths})", save=("tree_sha", "sha")),
        Call("POST", f"{ctx.repo}/git/commits",
             {"message": SEED_MESSAGE, "tree": "${tree_sha}", "parents": [parent_sha]},
             save=("commit_sha", "sha")),
        Call("PATCH", f"{ctx.repo}/git/refs/heads/{DEFAULT_BRANCH}", {"sha": "${commit_sha}", "force": False}),
    ]


def _read_main_ref(runner, ctx, attempts=5):
    """A just-created repository may need a moment before ``main`` exists."""
    for attempt in range(attempts):
        response = _get(runner, f"{ctx.repo}/git/ref/heads/{DEFAULT_BRANCH}", allow=(404, 409))
        if response.status == 200:
            return response.data["object"]["sha"]
        if attempt < attempts - 1:
            ctx.sleep(2)
    return None


def plan_template(runner, ctx):
    files = template_files(ctx.template_dir)
    if not ctx.reads_possible:
        return [Change("template", f"seed {len(files)} template files in one commit on {DEFAULT_BRANCH}",
                       _seed_calls(ctx, files, "<main-sha>"))]
    parent = _read_main_ref(runner, ctx)
    if parent is None:
        ctx.drift.append(f"template: {DEFAULT_BRANCH} has no commits; not seeding (seed it manually)")
        return []
    tree = _get(runner, f"{ctx.repo}/git/trees/{parent}?recursive=1", allow=()).data or {}
    live = {item["path"]: item["sha"] for item in tree.get("tree", []) if item.get("type") == "blob"}
    if "TEMPLATE_VERSION" not in live:
        if set(live) - {"README.md"}:
            ctx.drift.append("template: repository has content but no TEMPLATE_VERSION; not seeding")
            return []
        return [Change("template", f"seed {len(files)} template files in one commit on {DEFAULT_BRANCH}",
                       _seed_calls(ctx, files, parent))]
    for path, _, content in files:
        if path not in live:
            ctx.drift.append(f"template: {path} missing (not changed)")
        elif live[path] != git_blob_sha(content):
            ctx.drift.append(f"template: {path} differs from the template (not changed)")
    return []


def plan_labels(runner, ctx):
    live = {}
    if ctx.reads_possible:
        data = _get(runner, f"{ctx.repo}/labels?per_page=100", paginate=True, allow=()).data or []
        live = {item["name"]: item for item in data}
    changes = []
    for name, color, description in LABELS:
        body = {"name": name, "color": color, "description": description}
        current = live.get(name)
        if current is None:
            changes.append(Change("labels", f"create label {name}", [Call("POST", f"{ctx.repo}/labels", body)]))
        elif (current.get("color") or "").lower() != color or (current.get("description") or "") != description:
            changes.append(Change("labels", f"update label {name}",
                                  [Call("PATCH", f"{ctx.repo}/labels/{quote(name, safe='')}",
                                        {"color": color, "description": description})]))
    wanted = {name for name, _, _ in LABELS}
    for name in sorted(set(live) - wanted):
        ctx.drift.append(f"labels: extra label {name!r} (not deleted)")
    return changes


def plan_protection(runner, ctx):
    current = None
    if ctx.reads_possible:
        response = _get(runner, f"{ctx.repo}/branches/{DEFAULT_BRANCH}/protection")
        current = normalize_protection(response.data) if response.status == 200 else None
    if current == desired_protection(ctx.required_approvals):
        return []
    verb = "protect" if current is None else "update protection of"
    return [Change("protection",
                   f"{verb} {DEFAULT_BRANCH}: PR required, '{REQUIRED_CHECK}' required, enforce_admins, "
                   f"{ctx.required_approvals} approvals, no force pushes or deletions",
                   [Call("PUT", f"{ctx.repo}/branches/{DEFAULT_BRANCH}/protection",
                         protection_body(ctx.required_approvals))])]


def plan_environments(runner, ctx):
    changes = []
    for name, desired in desired_environments(ctx.reviewer_id).items():
        current = None
        if ctx.reads_possible:
            response = _get(runner, f"{ctx.repo}/environments/{name}")
            current = normalize_environment(response.data) if response.status == 200 else None
        if current == desired:
            continue
        verb = "create" if current is None else "update"
        rules = []
        if desired["reviewers"]:
            rules.append(f"required reviewer {ctx.reviewer or ctx.owner}")
        if desired["branch_policy"]:
            rules.append("protected branches only")
        detail = f" ({', '.join(rules)})" if rules else ""
        changes.append(Change("environments", f"{verb} environment {name}{detail}",
                              [Call("PUT", f"{ctx.repo}/environments/{name}", environment_body(desired))]))
    return changes


PLANNERS = (plan_repository, plan_settings, plan_template, plan_labels, plan_protection, plan_environments)


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------

def _fill(value, saved):
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        return saved[value[2:-1]]
    if isinstance(value, list):
        return [_fill(item, saved) for item in value]
    if isinstance(value, dict):
        return {key: _fill(item, saved) for key, item in value.items()}
    return value


def execute(runner, change):
    saved = {}
    for call in change.calls:
        response = runner(call.method, call.path, _fill(call.body, saved), False)
        if response.status >= 300:
            raise ApiError(f"{call.method} {call.path} returned HTTP {response.status}: {response.data}")
        if call.save:
            name, key = call.save
            saved[name] = (response.data or {})[key]


def resolve_identity(runner, ctx, owner_given):
    """Fill owner, org flag and production reviewer id from the API."""
    if not owner_given:
        ctx.owner = _get(runner, "user", allow=()).data["login"]
    account = _get(runner, f"users/{ctx.owner}").data or {}
    ctx.owner_is_org = account.get("type") == "Organization"
    if not ctx.reviewer:
        ctx.reviewer = ctx.owner if not ctx.owner_is_org else _get(runner, "user", allow=()).data["login"]
    reviewer = account if ctx.reviewer == ctx.owner else _get(runner, f"users/{ctx.reviewer}").data or {}
    if not reviewer.get("id"):
        raise ApiError(f"cannot resolve production reviewer {ctx.reviewer!r}")
    ctx.reviewer_id = reviewer["id"]


def provision(runner, ctx, apply=False, out=print):
    """Plan (and with ``apply`` perform) every change. Returns ``(planned, applied)``."""
    planned, applied, pre_existing = [], 0, False
    for planner in PLANNERS:
        changes = planner(runner, ctx)
        if planner is plan_repository:
            pre_existing = ctx.repo_exists
        elif pre_existing:
            for change in changes:
                ctx.drift.append(f"{change.resource}: {change.description}")
        for change in changes:
            planned.append(change)
            out(f"[{change.resource}] {change.description}")
            for call in change.calls:
                out(f"    {call.render()}")
            if apply:
                execute(runner, change)
                applied += 1
                if planner is plan_repository:
                    ctx.repo_exists = True
    return planned, applied


def manifest_stub(owner, slug):
    return "\n".join([
        f"# Orchestrator manifest stub for {owner}/{slug} (generated by scripts/provision_product.py).",
        "# Review it against the orchestrator's manifest schema before use (ADR 0007).",
        f"repository: {owner}/{slug}",
        f"default_branch: {DEFAULT_BRANCH}",
        "delivery: pr_only          # agents open pull requests; a human merges",
        "auto_merge: false",
        "label_adapter: ship48",
        f"required_check: {REQUIRED_CHECK}",
        "verify:",
        "  - make install",
        "  - make test",
        "",
    ])


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--slug", required=True, help="product slug = repository name")
    parser.add_argument("--owner", help="user or organisation (default: the gh-authenticated user)")
    parser.add_argument("--apply", action="store_true", help="perform the changes (default: dry run)")
    parser.add_argument("--confirm", metavar="SLUG", help="required with --apply; must equal --slug")
    parser.add_argument("--visibility", choices=("private", "public"), default="private")
    parser.add_argument("--allow-public", action="store_true", help="required with --visibility public")
    parser.add_argument("--required-approvals", type=int, default=0,
                        help="approving reviews required on main (default 0, see ADR 0006)")
    parser.add_argument("--reviewer", help="production environment reviewer login (default: owner)")
    parser.add_argument("--template-dir", type=Path, default=TEMPLATE_DIR, help=argparse.SUPPRESS)
    parser.add_argument("--manifest-out", type=Path, help="also write the manifest stub to this file")
    return parser


def main(argv=None, runner=gh_runner, out=print, sleep=time.sleep):
    args = build_parser().parse_args(argv)
    err = lambda message: print(message, file=sys.stderr)  # noqa: E731
    if not SLUG_RE.match(args.slug):
        err(f"error: invalid slug {args.slug!r} (lowercase letters, digits and inner hyphens, max 40)")
        return 2
    if args.visibility == "public" and not args.allow_public:
        err("error: refusing --visibility public without --allow-public (product repos are private, ADR 0008)")
        return 2
    if args.apply and args.confirm != args.slug:
        err(f"error: --apply requires --confirm {args.slug}")
        return 2
    if args.required_approvals < 0 or args.required_approvals > 6:
        err("error: --required-approvals must be between 0 and 6")
        return 2

    ctx = Context(owner=args.owner or OWNER_PLACEHOLDER, slug=args.slug, private=args.visibility == "private",
                  required_approvals=args.required_approvals, reviewer=args.reviewer or "",
                  template_dir=args.template_dir, sleep=sleep)
    api = runner if args.apply else read_only(runner)
    mode = "APPLY" if args.apply else "DRY RUN"
    try:
        try:
            resolve_identity(api, ctx, owner_given=bool(args.owner))
        except GhUnavailable as exc:
            if args.apply:
                raise
            ctx.offline = True
            out(f"NOTE: cannot read GitHub ({exc}); the plan assumes {ctx.owner}/{ctx.slug} does not exist yet.")
        out(f"{mode}: provisioning {ctx.owner}/{ctx.slug}")
        planned, applied = provision(api, ctx, apply=args.apply, out=out)
    except (GhUnavailable, ApiError) as exc:
        err(f"error: {exc}")
        return 1

    if ctx.repo_exists and not ctx.offline:
        out(f"drift: {len(ctx.drift)} item(s)" if ctx.drift else "drift: none")
        for item in ctx.drift:
            out(f"  - {item}")
    out("")
    stub = manifest_stub(ctx.owner, ctx.slug)
    out(stub)
    if args.manifest_out:
        args.manifest_out.write_text(stub, encoding="utf-8")
    if args.apply:
        out(f"APPLIED: {applied} changes applied")
    else:
        hint = f"; re-run with --apply --confirm {ctx.slug} to apply" if planned else ""
        out(f"DRY RUN: 0 changes applied ({len(planned)} planned{hint})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
