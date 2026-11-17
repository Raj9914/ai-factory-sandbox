"""Static checks for the product template's per-PR preview workflow (issue #54, ADR 0011).

PyYAML is not a dependency, so these tests read the workflow as text.
"""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "product-repo"
WORKFLOW = TEMPLATE / ".github" / "workflows" / "preview.yml"
README = TEMPLATE / "README.md"

sys.path.insert(0, str(ROOT / "scripts"))
import validate_template  # noqa: E402


def text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def top_level_block(name: str) -> list[str]:
    lines = text().splitlines()
    start = lines.index(f"{name}:")
    block = []
    for line in lines[start + 1:]:
        if line and not line.startswith(" "):
            break
        block.append(line.strip())
    return [line for line in block if line]


def step_index(marker: str) -> int:
    index = text().find(marker)
    assert index >= 0, marker
    return index


def test_template_validator_passes():
    assert validate_template.validate(TEMPLATE) == []
    assert validate_template.validate_workflow(text(), "preview.yml") == []


def test_validator_cli_reports_valid():
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "validate_template.py"), str(TEMPLATE)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Template valid" in result.stdout


def test_triggers_on_pull_request_only():
    assert "pull_request_target" not in text()
    assert top_level_block("on") == ["pull_request:", "types: [opened, synchronize, reopened]"]


def test_permissions_are_exactly_the_three_required():
    assert sorted(top_level_block("permissions")) == [
        "contents: read", "deployments: write", "pull-requests: write"]
    assert "permissions:" not in text().split("jobs:", 1)[1]  # no job-level escalation


def test_fork_prs_are_skipped():
    assert "if: github.event.pull_request.head.repo.full_name == github.repository &&" in text()


def test_uses_preview_environment_and_secrets_only_in_deploy_step():
    body = text()
    assert re.search(r"environment:\n\s+name: preview\n", body)
    deploy = body[step_index("id: deploy"):step_index("id: smoke")]
    assert body.count("${{ secrets.") == 2
    assert "secrets.CLOUDFLARE_API_TOKEN" in deploy and "secrets.CLOUDFLARE_ACCOUNT_ID" in deploy


def test_actions_and_wrangler_are_pinned():
    for ref in re.findall(r"uses:\s*(\S+)", text()):
        assert validate_template.check_uses(ref), ref
    assert re.search(r'WRANGLER_VERSION: "\d+\.\d+\.\d+"', text())
    assert '"wrangler@$WRANGLER_VERSION"' in text()
    assert "persist-credentials: false" in text()


def test_order_test_build_deploy_smoke():
    order = [step_index("run: make test"), step_index("run: make build"),
             step_index("pages deploy dist"), step_index('make smoke URL="$SMOKE_URL"')]
    assert order == sorted(order)
    assert step_index("run: make install") < order[0]


def test_deploys_to_pr_branch_and_checks_out_head_sha():
    body = text()
    assert 'branch="pr-$PR_NUMBER"' in body and '--branch "$branch"' in body
    assert "ref: ${{ github.event.pull_request.head.sha }}" in body


def test_records_github_deployment_for_head_sha():
    body = text()
    assert 'gh api "repos/$REPO/deployments"' in body
    assert '{ref: $ref, environment: "preview"' in body
    assert "HEAD_SHA: ${{ github.event.pull_request.head.sha }}" in body
    status = body[step_index("name: Update GitHub Deployment status"):]
    assert "if: always()" in status.split("run:", 1)[0]


def test_comment_upsert_by_marker_with_url_sha_and_smoke():
    comment = text()[step_index("name: Upsert PR comment"):]
    assert "if: always()" in comment.split("run:", 1)[0]
    assert 'marker="<!-- ship48-preview -->"' in comment
    assert "-X PATCH" in comment and "issues/comments/$existing" in comment
    for part in ("${PREVIEW_URL:-", "$HEAD_SHA", "smoke: $smoke", 'smoke="pass"', 'smoke="fail"'):
        assert part in comment


def test_no_untrusted_expressions_inside_run_scripts():
    # Expressions in run: scripts are a script-injection risk; values go through env instead.
    in_run = False
    for line in text().splitlines():
        stripped = line.strip()
        if stripped.startswith("run:") or stripped.startswith("- run:"):
            in_run = stripped.endswith("|")
            assert "${{" not in stripped, line
            continue
        if in_run and re.match(r"\s{0,8}\S", line):
            in_run = False
        if in_run:
            assert "${{" not in line, line


def test_readme_documents_preview_setup():
    readme = README.read_text(encoding="utf-8")
    for needle in ("## Preview deployments", "preview.yml", "CLOUDFLARE_PAGES_PROJECT",
                   "CLOUDFLARE_API_TOKEN", "pr-<number>", "make smoke URL=<preview-url>",
                   "pull_request_target", "fork"):
        assert needle in readme, needle
