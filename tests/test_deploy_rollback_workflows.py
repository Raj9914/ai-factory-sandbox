"""Static checks for the product template's deploy.yml and rollback.yml (issue #55), plus a run
of the template's rollback_target.py unit tests (template tests are outside `testpaths`).

PyYAML is not a dependency, so these tests read the workflows as text.
"""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "product-repo"
WORKFLOWS = TEMPLATE / ".github" / "workflows"
DEPLOY = WORKFLOWS / "deploy.yml"
ROLLBACK = WORKFLOWS / "rollback.yml"
RUNBOOK = ROOT / "docs" / "runbooks" / "DEPLOYMENT_AND_ROLLBACK.md"

sys.path.insert(0, str(ROOT / "scripts"))
import validate_template  # noqa: E402


def deploy() -> str:
    return DEPLOY.read_text(encoding="utf-8")


def rollback() -> str:
    return ROLLBACK.read_text(encoding="utf-8")


def top_level_block(body: str, name: str) -> list[str]:
    lines = body.splitlines()
    start = lines.index(f"{name}:")
    block = []
    for line in lines[start + 1:]:
        if line and not line.startswith(" "):
            break
        block.append(line)
    return [line for line in block if line.strip()]


def index(body: str, marker: str) -> int:
    found = body.find(marker)
    assert found >= 0, marker
    return found


def assert_no_expressions_in_run_scripts(body: str):
    in_run = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("run:") or stripped.startswith("- run:"):
            in_run = stripped.endswith("|")
            assert "${{" not in stripped, line
            continue
        if in_run and re.match(r"\s{0,8}\S", line):
            in_run = False
        if in_run:
            assert "${{" not in line, line


def test_template_and_workflows_validate():
    assert validate_template.validate(TEMPLATE) == []
    assert validate_template.validate_workflow(deploy(), "deploy.yml") == []
    assert validate_template.validate_workflow(rollback(), "rollback.yml") == []


def test_actions_pinned_and_no_script_injection():
    for body in (deploy(), rollback()):
        for ref in re.findall(r"uses:\s*(\S+)", body):
            assert validate_template.check_uses(ref), ref
        assert "persist-credentials: false" in body
        assert "pull_request" not in body
        assert_no_expressions_in_run_scripts(body)


def test_deploy_triggers_push_main_to_staging_and_dispatch_to_production():
    on = "\n".join(top_level_block(deploy(), "on"))
    assert "  push:\n    branches: [main]" in on
    assert "  workflow_dispatch:" in on and "default: production" in on
    assert "options: [production, staging]" in on
    assert "  workflow_call:" in on
    for name in ("environment:", "sha:", "rollback_from:"):
        assert name in on.split("workflow_call:", 1)[1]
    # push has no inputs, so the environment falls back to staging
    assert "name: ${{ inputs.environment || 'staging' }}" in deploy()
    assert "DEPLOY_SHA: ${{ inputs.sha || github.sha }}" in deploy()


def test_deploy_uses_environment_gate_and_main_only():
    body = deploy()
    assert re.search(r"\n    environment:\n      name: \$\{\{ inputs\.environment \|\| 'staging' \}\}\n", body)
    assert '[ "$RUN_REF" = refs/heads/main ]' in body
    assert "^[0-9a-f]{40}$" in body
    assert "cancel-in-progress: false" in body


def test_deploy_permissions_and_secrets_scope():
    body = deploy()
    assert [line.strip() for line in top_level_block(body, "permissions")] == [
        "contents: read", "deployments: write"]
    assert "permissions:" not in body.split("jobs:", 1)[1]
    assert body.count("${{ secrets.") == 2
    step = body[index(body, "id: deploy\n"):index(body, "id: smoke")]
    assert "secrets.CLOUDFLARE_API_TOKEN" in step and "secrets.CLOUDFLARE_ACCOUNT_ID" in step


def test_deploy_order_test_build_record_deploy_smoke_status():
    body = deploy()
    order = [index(body, "run: make install"), index(body, "run: make test"),
             index(body, "run: make build"), index(body, "name: Record GitHub Deployment"),
             index(body, "pages deploy dist"), index(body, 'make smoke URL="$SMOKE_URL"'),
             index(body, "name: Update GitHub Deployment status")]
    assert order == sorted(order)
    assert "ref: ${{ steps.target.outputs.sha }}" in body


def test_deploy_records_deployment_with_status():
    body = deploy()
    assert 'gh api "repos/$REPO/deployments" --input -' in body
    assert "environment: $env" in body and 'production_environment: ($env == "production")' in body
    assert "{rollback_from: $from}" in body
    status = body[index(body, "name: Update GitHub Deployment status"):]
    assert "if: always()" in status.split("run:", 1)[0]
    assert "state=success" in status and "state=failure" in status and "state=error" in status
    assert 'deployments/$DEPLOYMENT_ID/statuses' in status


def test_deploy_exposes_outputs_for_rollback():
    body = deploy()
    for name in ("url", "sha", "smoke"):
        assert f"value: ${{{{ jobs.deploy.outputs.{name} }}}}" in body


def test_rollback_dispatch_inputs():
    on = "\n".join(top_level_block(rollback(), "on"))
    assert "workflow_dispatch:" in on and "push:" not in on
    assert "environment:" in on and "options: [staging, production]" in on
    assert "incident:" in on and "required: false" in on.split("incident:", 1)[1]


def test_rollback_permissions_least_privilege():
    body = rollback()
    assert [line.strip() for line in top_level_block(body, "permissions")] == ["contents: read"]
    plan = body[index(body, "  plan:"):index(body, "  deploy:")]
    assert "deployments: read" in plan and "write" not in plan
    report = body[index(body, "  report:"):]
    assert "issues: write" in report and "deployments: write" not in report
    assert "secrets" not in body


def test_rollback_finds_target_then_redeploys_through_deploy_yml():
    body = rollback()
    assert 'python3 scripts/rollback_target.py --environment "$ENVIRONMENT"' in body
    deploy_job = body[index(body, "  deploy:"):index(body, "  report:")]
    assert "needs: plan" in deploy_job
    assert "uses: ./.github/workflows/deploy.yml" in deploy_job
    assert "sha: ${{ needs.plan.outputs.target_sha }}" in deploy_job
    assert "rollback_from: ${{ needs.plan.outputs.current_sha }}" in deploy_job
    assert "environment: ${{ inputs.environment }}" in deploy_job
    assert (TEMPLATE / "scripts" / "rollback_target.py").is_file()


def test_rollback_comments_on_incident_when_given():
    report = rollback()[index(rollback(), "  report:"):]
    assert "if: always() && inputs.incident != ''" in report
    assert '[[ "$INCIDENT" =~ ^[1-9][0-9]{0,9}$ ]]' in report
    assert 'gh api "repos/$REPO/issues/$INCIDENT/comments"' in report
    for part in ("${FROM_SHA:-", "${TO_SHA:-", "smoke: pass", "$GITHUB_RUN_ID"):
        assert part in report


def test_runbook_documents_deploy_and_rollback_workflows():
    runbook = RUNBOOK.read_text(encoding="utf-8")
    for needle in ("deploy.yml", "rollback.yml", "rollback_target.py", "incident",
                   "Review deployments", "Rollback drill", "Pages dashboard",
                   "CLOUDFLARE_PAGES_PROJECT", "migration"):
        assert needle in runbook, needle


def test_template_rollback_target_unit_tests_pass():
    result = subprocess.run(
        [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider",
         str(TEMPLATE / "tests" / "test_rollback_target.py")],
        cwd=TEMPLATE, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert re.search(r"\b\d+ passed\b", result.stdout), result.stdout
