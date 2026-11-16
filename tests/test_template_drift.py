import base64
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import provision_product as pp
from scripts import template_drift as td

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "template_drift.py"
REPO = "octo/demo"

CI_V1 = b"name: CI\non: [pull_request]\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
CI_V2 = CI_V1 + b"  secrets:\n    runs-on: ubuntu-latest\n"
TEMPLATE_V1 = {
    "TEMPLATE_VERSION": b"0.1.0\n",
    ".github/workflows/ci.yml": CI_V1,
    ".github/ISSUE_TEMPLATE/task.yml": b"name: Task\n",
    "AGENTS.md": b"# Agents\n",
    "README.md": b"# Product\n",
}
TEMPLATE_V2 = {**TEMPLATE_V1, "TEMPLATE_VERSION": b"0.2.0\n", ".github/workflows/ci.yml": CI_V2}


def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid",
                    "-c", "commit.gpgsign=false", *args], cwd=cwd, check=True, capture_output=True)


def _write_template(template_dir, files):
    for rel, content in files.items():
        path = template_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


@pytest.fixture
def factory(tmp_path):
    """A factory git repository with template 0.1.0 then 0.2.0 committed."""
    root = tmp_path / "factory"
    template_dir = root / td.TEMPLATE_REL
    root.mkdir()
    _git(root, "init", "-q")
    _write_template(template_dir, TEMPLATE_V1)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "template 0.1.0")
    _write_template(template_dir, TEMPLATE_V2)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "template 0.2.0")
    return root


class FakeGitHub:
    """Read-only stand-in for ``gh api`` serving one repository tree."""

    def __init__(self, files, repo=REPO):
        self.repo = repo
        self.blobs = {pp.git_blob_sha(content): content for content in files.values()}
        self.tree = [{"path": path, "type": "blob", "mode": "100644", "sha": pp.git_blob_sha(content)}
                     for path, content in files.items()]
        self.calls = []

    def __call__(self, method, path, body=None, paginate=False):
        self.calls.append((method, path))
        if path == f"repos/{self.repo}/git/trees/HEAD?recursive=1":
            return pp.Response(200, {"sha": "0" * 40, "tree": self.tree, "truncated": False})
        prefix = f"repos/{self.repo}/git/blobs/"
        if path.startswith(prefix) and path[len(prefix):] in self.blobs:
            content = base64.b64encode(self.blobs[path[len(prefix):]]).decode()
            return pp.Response(200, {"content": content, "encoding": "base64"})
        return pp.Response(404, {"message": "Not Found"})


def run(factory, files, *extra):
    lines = []
    github = FakeGitHub(files)
    code = td.main([REPO, *extra], runner=github, out=lines.append, root=factory)
    assert all(method == "GET" for method, _ in github.calls)
    return code, lines


def test_identical_copy_reports_zero_drift(factory):
    code, lines = run(factory, dict(TEMPLATE_V2))
    assert code == 0
    assert "drift vs claimed 0.2.0: 0 files" in lines
    assert "drift vs latest 0.2.0: 0 files" in lines
    assert not any(line.startswith("  ") for line in lines)


def test_modified_workflow_is_detected(factory):
    files = {**TEMPLATE_V2, ".github/workflows/ci.yml": CI_V2 + b"  extra: true\n"}
    code, lines = run(factory, files)
    assert code == 1
    assert lines.count("drift vs claimed 0.2.0: 1 file") == 1
    assert lines.count("drift vs latest 0.2.0: 1 file") == 1
    assert lines.count("  modified  .github/workflows/ci.yml") == 2


def test_older_claimed_version_compared_against_its_own_template(factory):
    code, lines = run(factory, dict(TEMPLATE_V1))
    assert code == 1
    claimed_at = lines.index("drift vs claimed 0.1.0: 0 files")
    latest_at = lines.index("drift vs latest 0.2.0: 1 file")
    assert claimed_at < latest_at
    assert lines[latest_at + 1] == "  modified  .github/workflows/ci.yml"
    assert any(line.startswith("claimed template version: 0.1.0 (factory commit ") for line in lines)


def test_missing_tracked_file_is_reported(factory):
    files = {k: v for k, v in TEMPLATE_V2.items() if k != ".github/ISSUE_TEMPLATE/task.yml"}
    code, lines = run(factory, files)
    assert code == 1
    assert "  missing   .github/ISSUE_TEMPLATE/task.yml" in lines


def test_untracked_paths_ignored_unless_all(factory):
    files = {**TEMPLATE_V2, "README.md": b"# My product\n", "src/app.py": b"print()\n"}
    code, lines = run(factory, files)
    assert code == 0
    assert "drift vs latest 0.2.0: 0 files" in lines
    code, lines = run(factory, files, "--all")
    assert code == 1
    assert "  modified  README.md" in lines
    assert not any("src/app.py" in line for line in lines)


def test_unknown_or_missing_claimed_version_needs_attention(factory):
    code, lines = run(factory, {**TEMPLATE_V2, "TEMPLATE_VERSION": b"9.9.9\n"})
    assert code == 1
    assert "claimed template version: 9.9.9 (unknown to this factory repository)" in lines
    assert "drift vs latest 0.2.0: 0 files" in lines
    code, lines = run(factory, {k: v for k, v in TEMPLATE_V2.items() if k != "TEMPLATE_VERSION"})
    assert code == 1
    assert "claimed template version: none (TEMPLATE_VERSION missing)" in lines


def test_api_error_and_bad_repo_argument(factory):
    lines = []
    assert td.main(["octo/absent"], runner=FakeGitHub({}), out=lines.append, root=factory) == 2
    assert lines[-1].startswith("error: GET repos/octo/absent/git/trees/HEAD")
    assert td.main(["not-a-repo"], runner=FakeGitHub({}), out=lines.append, root=factory) == 2


def test_runner_is_read_only(factory):
    def writer(method, path, body=None, paginate=False):
        return pp.Response(200, {})
    guarded = pp.read_only(writer)
    with pytest.raises(RuntimeError):
        guarded("POST", "repos/octo/demo/git/blobs", {})


def test_tracked_patterns_cover_shipped_safety_files():
    tracked = [path for path, _mode, _content in pp.template_files(ROOT / td.TEMPLATE_REL)
               if td.is_tracked(path)]
    for expected in (".github/workflows/ci.yml", ".github/ISSUE_TEMPLATE/task.yml",
                     ".github/PULL_REQUEST_TEMPLATE.md", "AGENTS.md", "CLAUDE.md"):
        assert expected in tracked
    assert "TEMPLATE_VERSION" not in tracked


def test_cli_runs_as_script():
    proc = subprocess.run([sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True)
    assert proc.returncode == 0
    assert "<owner>/<name>" in proc.stdout
