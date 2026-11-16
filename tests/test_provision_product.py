import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

import pytest

from scripts import provision_product as pp

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "provision_product.py"
OWNER = "octo"
OWNER_ID = 4242


class FakeGitHub:
    """In-memory GitHub REST API standing in for ``gh api``. Records every call."""

    def __init__(self, login=OWNER, user_id=OWNER_ID):
        self.login = login
        self.user_id = user_id
        self.repos = {}
        self.calls = []
        self._next = 0

    # -- helpers ---------------------------------------------------------
    def _sha(self):
        self._next += 1
        return f"{self._next:040x}"

    @property
    def writes(self):
        return [call for call in self.calls if call[0] != "GET"]

    def provisioned(self, slug="demo"):
        """Return a repo provisioned by a real apply run."""
        assert pp.main(["--slug", slug, "--owner", OWNER, "--apply", "--confirm", slug],
                       runner=self, out=lambda _: None, sleep=lambda _: None) == 0
        return self.repos[f"{OWNER}/{slug}"]

    def _new_repo(self, name, body):
        main_sha, readme = self._sha(), self._sha()
        self.repos[f"{OWNER}/{name}"] = {
            "meta": {"name": name, "private": body.get("private", False),
                     "visibility": "private" if body.get("private") else "public",
                     "allow_auto_merge": body.get("allow_auto_merge", False),
                     "delete_branch_on_merge": body.get("delete_branch_on_merge", False)},
            "labels": {},
            "protection": None,
            "environments": {},
            "ref": main_sha if body.get("auto_init") else None,
            "trees": {main_sha: [{"path": "README.md", "type": "blob", "sha": readme}]},
            "commits": {main_sha: main_sha},  # commit sha -> tree sha
            "pending_trees": {},
        }

    # -- the runner ------------------------------------------------------
    def __call__(self, method, path, body=None, paginate=False):
        self.calls.append((method, path, body))
        parts = path.split("?")[0].split("/")
        if path == "user" and method == "GET":
            return pp.Response(200, {"login": self.login, "id": self.user_id, "type": "User"})
        if parts[0] == "users" and method == "GET":
            if parts[1] == self.login:
                return pp.Response(200, {"login": self.login, "id": self.user_id, "type": "User"})
            return pp.Response(404, {"message": "Not Found"})
        if path == "user/repos" and method == "POST":
            self._new_repo(body["name"], body)
            return pp.Response(201, {"name": body["name"]})
        assert parts[0] == "repos", path
        repo = self.repos.get(f"{parts[1]}/{parts[2]}")
        if repo is None:
            return pp.Response(404, {"message": "Not Found"})
        rest = parts[3:]
        if not rest:
            if method == "GET":
                return pp.Response(200, dict(repo["meta"]))
            if method == "PATCH":
                if "visibility" in body:
                    repo["meta"]["private"] = body["visibility"] == "private"
                    repo["meta"]["visibility"] = body["visibility"]
                repo["meta"].update({k: v for k, v in body.items() if k != "visibility"})
                return pp.Response(200, dict(repo["meta"]))
        if rest[0] == "labels":
            if method == "GET":
                return pp.Response(200, [dict(v) for v in repo["labels"].values()])
            if method == "POST":
                if body["name"] in repo["labels"]:
                    return pp.Response(422, {"message": "already_exists"})
                repo["labels"][body["name"]] = dict(body)
                return pp.Response(201, dict(body))
            if method == "PATCH":
                repo["labels"][unquote(rest[1])].update(body)
                return pp.Response(200, {})
        if rest[:3] == ["branches", "main", "protection"]:
            if method == "GET":
                return pp.Response(200, repo["protection"]) if repo["protection"] else pp.Response(404, {})
            if method == "PUT":
                if repo["ref"] is None:
                    return pp.Response(404, {"message": "Branch not found"})
                reviews = body["required_pull_request_reviews"]
                repo["protection"] = {
                    "required_status_checks": {"strict": body["required_status_checks"]["strict"],
                                               "contexts": list(body["required_status_checks"]["contexts"])},
                    "enforce_admins": {"enabled": body["enforce_admins"]},
                    "required_pull_request_reviews": None if reviews is None else {
                        "required_approving_review_count": reviews["required_approving_review_count"]},
                    "allow_force_pushes": {"enabled": body["allow_force_pushes"]},
                    "allow_deletions": {"enabled": body["allow_deletions"]},
                }
                return pp.Response(200, repo["protection"])
        if rest[0] == "environments":
            name = rest[1]
            if method == "GET":
                return pp.Response(200, repo["environments"][name]) if name in repo["environments"] \
                    else pp.Response(404, {})
            if method == "PUT":
                rules = []
                if body.get("reviewers"):
                    rules.append({"type": "required_reviewers", "reviewers": [
                        {"type": r["type"], "reviewer": {"id": r["id"]}} for r in body["reviewers"]]})
                if body.get("deployment_branch_policy"):
                    rules.append({"type": "branch_policy"})
                repo["environments"][name] = {"name": name, "protection_rules": rules,
                                              "deployment_branch_policy": body.get("deployment_branch_policy")}
                return pp.Response(200, repo["environments"][name])
        if rest[0] == "git":
            if rest[1:4] == ["ref", "heads", "main"] and method == "GET":
                if repo["ref"] is None:
                    return pp.Response(409, {"message": "Git Repository is empty."})
                return pp.Response(200, {"object": {"sha": repo["ref"]}})
            if rest[1] == "trees" and method == "GET":
                tree = repo["trees"][repo["commits"][rest[2]]]
                return pp.Response(200, {"tree": tree})
            if rest[1] == "trees" and method == "POST":
                sha = self._sha()
                repo["trees"][sha] = [
                    {"path": e["path"], "type": "blob", "mode": e["mode"],
                     "sha": pp.git_blob_sha(e["content"].encode("utf-8"))} for e in body["tree"]]
                return pp.Response(201, {"sha": sha})
            if rest[1] == "commits" and method == "POST":
                assert body["parents"] == [repo["ref"]]
                sha = self._sha()
                repo["commits"][sha] = body["tree"]
                return pp.Response(201, {"sha": sha})
            if rest[1:4] == ["refs", "heads", "main"] and method == "PATCH":
                if repo["protection"]:
                    return pp.Response(422, {"message": "protected branch"})
                repo["ref"] = body["sha"]
                return pp.Response(200, {})
        raise AssertionError(f"unexpected call {method} {path}")


def run(fake, *args):
    lines = []
    code = pp.main(["--slug", "demo", "--owner", OWNER, *args], runner=fake, out=lines.append,
                   sleep=lambda _: None)
    return code, "\n".join(lines)


def apply_args(slug="demo"):
    return ["--apply", "--confirm", slug]


# -- desired state --------------------------------------------------------

def test_label_set_has_at_least_40_unique_labels_with_valid_colors():
    names = [name for name, _, _ in pp.LABELS]
    assert len(names) >= 40
    assert len(set(names)) == len(names)
    for name, color, _ in pp.LABELS:
        assert len(color) == 6 and int(color, 16) >= 0, name
    for required in ("agent:claude", "agent:human", "status:ready", "status:review", "type:task", "area:testing"):
        assert required in names


def test_slug_validation():
    assert pp.SLUG_RE.match("demo")
    assert pp.SLUG_RE.match("my-app-2")
    for bad in ("Demo", "-demo", "demo-", "de mo", "a" * 41, "../x", ""):
        assert not pp.SLUG_RE.match(bad), bad


# -- dry run -----------------------------------------------------------------

def test_dry_run_on_fresh_repo_makes_zero_write_calls_and_plans_everything():
    fake = FakeGitHub()
    code, output = run(fake)
    assert code == 0
    assert fake.writes == []
    assert "DRY RUN: 0 changes applied" in output
    assert '"private": true' in output and "create private repository octo/demo" in output
    assert '"allow_auto_merge": false' in output
    assert '"delete_branch_on_merge": true' in output
    assert output.count("POST repos/octo/demo/labels ") >= 40
    assert "PUT repos/octo/demo/branches/main/protection" in output
    assert '"enforce_admins": true' in output and '"contexts": ["test"]' in output
    for env in pp.ENVIRONMENTS:
        assert f"PUT repos/octo/demo/environments/{env}" in output
    assert '"reviewers": [{"id": 4242, "type": "User"}]' in output
    assert "POST repos/octo/demo/git/trees" in output
    assert "delivery: pr_only" in output and "auto_merge: false" in output


def test_plan_covers_acceptance_resources():
    fake = FakeGitHub()
    ctx = pp.Context(owner=OWNER, slug="demo", reviewer_id=OWNER_ID)
    planned, applied = pp.provision(pp.read_only(fake), ctx, apply=False, out=lambda _: None)
    assert applied == 0
    by_resource = {}
    for change in planned:
        by_resource.setdefault(change.resource, []).append(change)
    create = by_resource["repository"][0].calls[0]
    assert create.body["private"] is True and create.body["allow_auto_merge"] is False
    assert len(by_resource["labels"]) >= 40
    protection = by_resource["protection"][0].calls[0].body
    assert protection["required_status_checks"]["contexts"] == ["test"]
    assert protection["enforce_admins"] is True
    assert protection["required_pull_request_reviews"] is not None
    envs = {c.calls[0].path.rsplit("/", 1)[1]: c.calls[0].body for c in by_resource["environments"]}
    assert set(envs) == {"preview", "staging", "production"}
    assert envs["production"]["reviewers"] and envs["production"]["deployment_branch_policy"]["protected_branches"]
    assert envs["staging"]["deployment_branch_policy"]["protected_branches"]
    assert fake.writes == []


def test_read_only_wrapper_blocks_writes():
    with pytest.raises(RuntimeError, match="dry run attempted a write"):
        pp.read_only(FakeGitHub())("POST", "user/repos", {"name": "x"})


def test_dry_run_on_provisioned_repo_plans_nothing():
    fake = FakeGitHub()
    fake.provisioned()
    fake.calls.clear()
    code, output = run(fake)
    assert code == 0 and fake.writes == []
    assert "DRY RUN: 0 changes applied (0 planned)" in output
    assert "drift: none" in output


# -- apply -------------------------------------------------------------------

def test_apply_requires_matching_confirm():
    fake = FakeGitHub()
    assert run(fake, "--apply")[0] == 2
    assert run(fake, "--apply", "--confirm", "other")[0] == 2
    assert fake.calls == []


def test_public_visibility_refused_without_allow_public():
    fake = FakeGitHub()
    assert run(fake, "--visibility", "public")[0] == 2
    assert fake.calls == []
    code, output = run(fake, "--visibility", "public", "--allow-public")
    assert code == 0 and "create public repository" in output and fake.writes == []


def test_apply_creates_fully_configured_repo():
    fake = FakeGitHub()
    code, output = run(fake, *apply_args())
    assert code == 0
    repo = fake.repos["octo/demo"]
    assert repo["meta"]["private"] is True
    assert repo["meta"]["allow_auto_merge"] is False and repo["meta"]["delete_branch_on_merge"] is True
    assert set(repo["labels"]) == {name for name, _, _ in pp.LABELS}
    assert pp.normalize_protection(repo["protection"]) == pp.desired_protection(0)
    assert set(repo["environments"]) == set(pp.ENVIRONMENTS)
    files = {e["path"] for e in repo["trees"][repo["commits"][repo["ref"]]]}
    assert {"TEMPLATE_VERSION", "Makefile", ".github/workflows/ci.yml", "AGENTS.md"} <= files
    assert f"APPLIED: {len(fake.writes) - 2} changes applied" in output  # seed = 3 calls, one change


def test_second_apply_reports_zero_changes():
    fake = FakeGitHub()
    fake.provisioned()
    fake.calls.clear()
    code, output = run(fake, *apply_args())
    assert code == 0
    assert fake.writes == []
    assert "APPLIED: 0 changes applied" in output
    assert "drift: none" in output


def test_drift_is_detected_reported_and_repaired():
    fake = FakeGitHub()
    repo = fake.provisioned()
    repo["meta"]["allow_auto_merge"] = True
    repo["labels"]["status:ready"]["color"] = "ffffff"
    del repo["labels"]["type:bug"]
    repo["labels"]["custom"] = {"name": "custom", "color": "000000", "description": ""}
    repo["protection"]["enforce_admins"]["enabled"] = False
    del repo["environments"]["production"]
    fake.calls.clear()

    code, output = run(fake)  # dry run reports drift without writing
    assert code == 0 and fake.writes == []
    assert "drift: 6 item(s)" in output
    for text in ("settings: set allow_auto_merge=False", "labels: update label status:ready",
                 "labels: create label type:bug", "extra label 'custom' (not deleted)",
                 "protection: update protection of main", "environments: create environment production"):
        assert text in output
    assert "DRY RUN: 0 changes applied (5 planned" in output

    code, output = run(fake, *apply_args())
    assert code == 0 and "APPLIED: 5 changes applied" in output
    assert "custom" in repo["labels"]  # never deleted
    fake.calls.clear()
    code, output = run(fake, *apply_args())
    assert "APPLIED: 0 changes applied" in output and fake.writes == []


def test_public_repo_drifts_back_to_private():
    fake = FakeGitHub()
    repo = fake.provisioned()
    repo["meta"]["private"] = False
    code, output = run(fake, *apply_args())
    assert code == 0 and repo["meta"]["private"] is True
    assert '"visibility": "private"' in output


def test_template_drift_is_reported_not_overwritten():
    fake = FakeGitHub()
    repo = fake.provisioned()
    tree = repo["trees"][repo["commits"][repo["ref"]]]
    for entry in tree:
        if entry["path"] == "README.md":
            entry["sha"] = "0" * 40
    fake.calls.clear()
    code, output = run(fake, *apply_args())
    assert code == 0 and fake.writes == []
    assert "template: README.md differs from the template (not changed)" in output


def test_existing_repo_without_template_is_not_seeded():
    fake = FakeGitHub()
    fake._new_repo("demo", {"private": True, "auto_init": True})
    repo = fake.repos["octo/demo"]
    repo["trees"][repo["ref"]].append({"path": "src/app.py", "type": "blob", "sha": "1" * 40})
    code, output = run(fake, *apply_args())
    assert code == 0
    assert "not seeding" in output
    assert not any("git/" in path for method, path, _ in fake.writes)


def test_required_approvals_flag():
    fake = FakeGitHub()
    code, output = run(fake, "--required-approvals", "1")
    assert code == 0 and '"required_approving_review_count": 1' in output
    assert run(fake, "--required-approvals", "-1")[0] == 2


def test_api_error_exits_1():
    fake = FakeGitHub()
    def failing(method, path, body=None, paginate=False):
        if method == "PUT":
            return pp.Response(422, {"message": "Upgrade to GitHub Pro"})
        return fake(method, path, body, paginate)
    code = pp.main(["--slug", "demo", "--owner", OWNER, *apply_args()], runner=failing,
                   out=lambda _: None, sleep=lambda _: None)
    assert code == 1


def test_manifest_stub_written(tmp_path):
    target = tmp_path / "manifest.yaml"
    code, _ = run(FakeGitHub(), "--manifest-out", str(target))
    text = target.read_text(encoding="utf-8")
    assert code == 0 and "repository: octo/demo" in text and "delivery: pr_only" in text


def test_offline_dry_run_plans_as_new_repo():
    def unavailable(method, path, body=None, paginate=False):
        raise pp.GhUnavailable("gh CLI not found")
    lines = []
    assert pp.main(["--slug", "demo"], runner=unavailable, out=lines.append) == 0
    output = "\n".join(lines)
    assert "NOTE: cannot read GitHub" in output
    assert "create private repository <owner>/demo" in output
    assert "DRY RUN: 0 changes applied" in output
    assert pp.main(["--slug", "demo", *apply_args()], runner=unavailable, out=lines.append) == 1


def test_concatenated_pages_are_merged():
    assert pp._decode_concatenated_json('[{"a": 1}]\n[{"b": 2}]') == [{"a": 1}, {"b": 2}]
    assert pp._decode_concatenated_json('{"x": 1}') == {"x": 1}


def test_cli_verification_command_without_gh(tmp_path):
    """Issue verification: ``python scripts/provision_product.py --slug demo``."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GH_")
           and key != "GITHUB_TOKEN"}
    env["PATH"] = str(tmp_path)  # no gh on PATH: never touches the network from tests
    proc = subprocess.run([sys.executable, str(SCRIPT), "--slug", "demo"], cwd=ROOT, env=env,
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    assert "[repository] create private repository <owner>/demo" in proc.stdout
    assert proc.stdout.strip().splitlines()[-1].startswith("DRY RUN: 0 changes applied")
