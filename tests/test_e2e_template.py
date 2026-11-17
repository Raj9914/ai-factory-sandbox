"""Checks for the product template's Playwright core-journey test (issue #51).

Browsers are not installed in this repository's CI, so the spec itself runs in the product's
preview workflow. Here: the config, spec and workflow job are checked as text (PyYAML is not
a dependency), and the local static server e2e/serve.mjs is exercised with Node.
"""
from contextlib import contextmanager
from pathlib import Path
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "product-repo"
E2E = TEMPLATE / "e2e"
CONFIG = TEMPLATE / "playwright.config.ts"
SPEC = E2E / "journey.spec.ts"
SERVER = E2E / "serve.mjs"
PAGE = E2E / "site" / "index.html"
WORKFLOW = TEMPLATE / ".github" / "workflows" / "preview.yml"

sys.path.insert(0, str(ROOT / "scripts"))
import validate_template  # noqa: E402


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def e2e_job() -> str:
    body = read(WORKFLOW)
    start = body.index("\n  e2e:\n")
    rest = body[start + 1:]
    end = re.search(r"\n  \S", rest[1:])
    return rest if end is None else rest[: end.start() + 1]


def test_files_exist_and_template_still_valid():
    for path in (CONFIG, SPEC, SERVER, PAGE, E2E / "README.md"):
        assert path.is_file(), path
    assert validate_template.validate(TEMPLATE) == []


def test_config_retries_timeouts_and_trace():
    config = read(CONFIG)
    assert "testDir: './e2e'" in config
    assert re.search(r"^\s*retries: 1,$", config, re.M)
    assert re.search(r"^\s*timeout: 30_000,$", config, re.M)       # 30 s per test
    assert re.search(r"^\s*globalTimeout: 120_000,$", config, re.M)  # whole run <= 2 min
    assert "trace: 'retain-on-failure'" in config
    assert "process.env.E2E_BASE_URL" in config
    assert "command: 'node e2e/serve.mjs'" in config


def test_spec_walks_core_journey_without_credentials():
    spec = read(SPEC)
    assert "from '@playwright/test'" in spec
    assert "test.describe('core journey'" in spec
    assert "page.goto('/')" in spec  # relative to baseURL: preview or local build
    assert not re.search(r"\.(only|skip|fixme)\(", spec)
    assert not re.search(r"password|token|secret|cookie|authorization", spec, re.I)
    page = read(PAGE)
    # Every locator the spec uses exists on the example page.
    for needle in ("Get started", "What do you want to try?", "Continue",
                   "Enter a goal to continue.", "You're all set."):
        assert needle in spec and needle in page, needle
    assert 'role="status"' in page and 'role="alert"' in page
    assert not re.search(r"""(src|href)=["']https?://""", page)  # no external requests


def test_workflow_e2e_job_runs_after_preview_and_uploads_trace():
    body = read(WORKFLOW)
    assert validate_template.validate_workflow(body, "preview.yml") == []
    job = e2e_job()
    assert "needs: preview" in job
    assert "if: ${{ !cancelled() && needs.preview.result != 'failure' }}" in job
    assert "deployment_url: ${{ steps.deploy.outputs.deployment_url }}" in body  # preview output
    assert "E2E_BASE_URL: ${{ needs.preview.outputs.deployment_url }}" in job
    assert re.search(r'PLAYWRIGHT_VERSION: "\d+\.\d+\.\d+"', job)
    assert '"@playwright/test@$PLAYWRIGHT_VERSION"' in job
    order = [job.index(s) for s in ("run: make build", "npx playwright install --with-deps chromium",
                                    "run: npx playwright test", "uses: actions/upload-artifact@")]
    assert order == sorted(order)
    test_step = job[job.index("name: Core journey"):job.index("run: npx playwright test")]
    assert "timeout-minutes: 2" in test_step
    upload = job[job.index("name: Upload Playwright trace on failure"):]
    assert "if: failure()" in upload and "test-results/" in upload
    for ref in re.findall(r"uses:\s*(\S+)", job):
        assert validate_template.check_uses(ref), ref
    assert "secrets." not in job and "environment:" not in job
    assert "persist-credentials: false" in job


@contextmanager
def server(env_root: Path | None = None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("E2E_")}
    env["E2E_PORT"] = "0"  # free port
    if env_root is not None:
        env["E2E_ROOT"] = str(env_root)
    proc = subprocess.Popen(["node", str(SERVER)], cwd=TEMPLATE, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        line = proc.stdout.readline()
        match = re.search(r"at (http://127\.0\.0\.1:\d+)/", line)
        assert match, f"server did not start: {line!r}"
        yield match.group(1)
    finally:
        proc.kill()
        proc.wait(timeout=10)


def fetch(url: str, method: str = "GET") -> tuple[int, str, str]:
    request = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.headers.get("content-type", ""), response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, "", ""


def test_server_serves_example_page_when_no_build():
    with server(E2E / "site") as base:
        status, ctype, body = fetch(base + "/")
        assert status == 200 and ctype.startswith("text/html")
        assert "Get started" in body
        assert fetch(base + "/index.html?x=1")[0] == 200
        assert fetch(base + "/missing.js")[0] == 404
        assert fetch(base + "/", "POST")[0] == 405


def test_server_refuses_paths_outside_root(tmp_path):
    site = tmp_path / "dist"
    site.mkdir()
    (site / "index.html").write_text("<h1>built</h1>", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("private", encoding="utf-8")
    with server(site) as base:
        assert fetch(base + "/")[2] == "<h1>built</h1>"
        for path in ("/../outside.txt", "/%2e%2e/outside.txt", "/..%2foutside.txt", "/%00", "/%E0%A4%A"):
            status, _, body = fetch(base + path)
            assert status == 404 and "private" not in body, path
