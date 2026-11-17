"""Issue #41: preview screenshots at design/screens.yml viewports, artifact + PR comment table.

The Node script runs through subprocesses. Capture runs use a fake `playwright` module
(resolved from the working directory, like the real one) against a real local HTTP server;
the root CI job `template-screenshots` runs the same script with real Playwright Chromium.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "product-repo"
SCRIPT = TEMPLATE / "scripts" / "screenshots.mjs"
SPEC = TEMPLATE / "design" / "screens.yml"
WORKFLOW = TEMPLATE / ".github" / "workflows" / "preview.yml"
ROOT_CI = ROOT / ".github" / "workflows" / "ci.yml"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

sys.path.insert(0, str(ROOT / "scripts"))
import validate_template  # noqa: E402

FAKE_PLAYWRIGHT = r"""
const fs = require('fs');
const mode = process.env.FAKE_PW_MODE || 'fetch';
const log = (entry) => { if (process.env.FAKE_PW_LOG) fs.appendFileSync(process.env.FAKE_PW_LOG, JSON.stringify(entry) + '\n'); };
const SIG = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
exports.chromium = {
  async launch(options) {
    log({ launch: options });
    return {
      async newContext(options) {
        log({ context: options });
        return {
          async newPage() {
            return {
              setDefaultTimeout() {},
              async goto(url, options) {
                log({ goto: url, timeout: options.timeout });
                if (mode === 'hang') return new Promise(() => {});
                const response = await fetch(url, { redirect: 'follow' });
                return { status: () => response.status };
              },
              async screenshot(options) {
                log({ screenshot: options.path, fullPage: options.fullPage });
                fs.writeFileSync(options.path, SIG);
              },
            };
          },
          async close() {},
        };
      },
      async close() {},
    };
  },
};
"""


def load_design_validator():
    # Template CI runs templates/product-repo/scripts/validate_design_records.py. Load it without
    # writing __pycache__ into the template (the template validator rejects non-text files).
    name = "template_validate_design_records"
    spec = importlib.util.spec_from_file_location(name, TEMPLATE / "scripts" / "validate_design_records.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    previous, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def node_eval(js: str):
    """Run an ES module snippet with the script's exports in scope as `m`; return its JSON output."""
    code = f"const m = await import({json.dumps(SCRIPT.as_uri())});\n{js}"
    result = subprocess.run(["node", "--input-type=module", "-e", code], capture_output=True,
                            text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def spec_result(yaml_text: str):
    return node_eval(f"console.log(JSON.stringify(m.loadSpec(m.parseYaml({json.dumps(yaml_text)}))));")


class Handler(BaseHTTPRequestHandler):
    PAGES = {"/": b"<h1>Home</h1>", "/signup/": b"<h1>Sign up</h1>", "/app/": b"<h1>App</h1>",
             "/app/signup/": b"<h1>App sign up</h1>"}

    def do_GET(self):  # noqa: N802
        body = self.PAGES.get(self.path)
        self.send_response(200 if body else 404)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body or b"not found")

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def workdir(tmp_path):
    package = tmp_path / "node_modules" / "playwright"
    package.mkdir(parents=True)
    (package / "package.json").write_text('{"name": "playwright", "main": "index.js"}', encoding="utf-8")
    (package / "index.js").write_text(FAKE_PLAYWRIGHT, encoding="utf-8")
    return tmp_path


def run_cli(cwd, *args, env=None, timeout=60):
    full_env = {**os.environ, **(env or {})}
    return subprocess.run(["node", str(SCRIPT), *map(str, args)], cwd=cwd, capture_output=True,
                          text=True, timeout=timeout, env=full_env)


# --- example spec -------------------------------------------------------------------------------

def test_example_spec_passes_design_record_validator():
    validator = load_design_validator()
    assert validator.validate(SPEC) == []


def test_js_parser_matches_python_parser_on_example():
    validator = load_design_validator()
    expected = validator.parse(SPEC.read_text(encoding="utf-8"))
    actual = node_eval(f"console.log(JSON.stringify(m.parseYaml({json.dumps(SPEC.read_text(encoding='utf-8'))})));")
    assert actual == expected


def test_example_has_at_least_two_screens_at_two_viewports():
    result = spec_result(SPEC.read_text(encoding="utf-8"))
    assert result["errors"] == []
    screens = result["screens"]
    assert len(screens) >= 4
    assert {s["viewport"] for s in screens} >= {"mobile", "desktop"}
    assert len({s["route"] for s in screens}) >= 2
    assert len({s["file"] for s in screens}) == len(screens)
    for screen in screens:
        assert screen["figma_url"].startswith("https://www.figma.com/design/")
        assert re.fullmatch(r"\d+:\d+", screen["node_id"])
        assert screen["width"] > 0 and screen["height"] > 0


# --- spec validation ----------------------------------------------------------------------------

BASE_SPEC = """viewports:
  mobile: 390x844
screens:
  - id: S1
    name: Home
    route: {route}
    viewport: {viewport}
"""


@pytest.mark.parametrize("route", ["//evil.example/", "https://evil.example/", "home", '"/a b"', "/a\\\\b"])
def test_rejects_routes_that_are_not_same_origin_paths(route):
    errors = spec_result(BASE_SPEC.format(route=route, viewport="mobile"))["errors"]
    assert any("route must be a same-origin path" in e for e in errors), errors


def test_rejects_unknown_viewport_and_bad_sizes():
    errors = spec_result(BASE_SPEC.format(route="/", viewport="tablet"))["errors"]
    assert errors == ['screen S1: viewport must be one of [mobile], got "tablet"']
    bad = BASE_SPEC.replace("390x844", "390by844").format(route="/", viewport="mobile")
    errors = spec_result(bad)["errors"]
    assert any(e.startswith("viewport mobile: must be") for e in errors), errors
    huge = BASE_SPEC.replace("390x844", "99999x10").format(route="/", viewport="mobile")
    assert spec_result(huge)["errors"]


def test_rejects_duplicates_bad_ids_and_bad_figma_links():
    dup = BASE_SPEC.format(route="/", viewport="mobile") + "  - id: S1\n    route: /x\n    viewport: mobile\n"
    assert spec_result(dup)["errors"] == ["screen S1: duplicate screenshot S1-mobile.png (ids must be unique)"]
    bad_id = BASE_SPEC.format(route="/", viewport="mobile").replace("id: S1", "id: ../x")
    assert "id must use letters" in spec_result(bad_id)["errors"][0]
    bad_figma = BASE_SPEC.format(route="/", viewport="mobile") + "    figma_url: https://evil.example/x\n"
    assert "figma_url is not a Figma" in spec_result(bad_figma)["errors"][0]


def test_screens_without_route_are_skipped_but_at_least_one_is_required():
    text = BASE_SPEC.format(route="/", viewport="mobile") + "  - id: M1\n    name: Modal\n"
    result = spec_result(text)
    assert result["errors"] == [] and result["skipped"] == [{"id": "M1", "name": "Modal"}]
    only_skipped = "viewports:\n  mobile: 390x844\nscreens:\n  - id: M1\n"
    assert spec_result(only_skipped)["errors"] == ["no screen has a route; nothing to capture"]


def test_resolve_url_keeps_base_path_and_origin():
    urls = node_eval("""console.log(JSON.stringify([
      m.resolveUrl('https://pr-1.p.pages.dev', '/'),
      m.resolveUrl('https://pr-1.p.pages.dev/app?x=1', '/signup/'),
      m.resolveUrl('http://localhost:4173/', '/a/b?c=d'),
    ]));""")
    assert urls == ["https://pr-1.p.pages.dev/", "https://pr-1.p.pages.dev/app/signup/",
                    "http://localhost:4173/a/b?c=d"]
    errors = node_eval("""const out = [];
      for (const base of ['ftp://x', 'not a url', 'https://user:pw@x.dev']) {
        try { m.resolveUrl(base, '/'); out.push('accepted ' + base); } catch (e) { out.push(e.name + ': ' + e.message); }
      }
      console.log(JSON.stringify(out));""")
    assert all(not e.startswith("accepted") for e in errors), errors


# --- capture (CLI) ------------------------------------------------------------------------------

def test_cli_captures_example_screens_at_their_viewports(server, workdir):
    log = workdir / "pw.log"
    result = run_cli(workdir, server, SPEC, workdir / "out", env={"FAKE_PW_LOG": str(log)})
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr
    pngs = sorted(p.name for p in (workdir / "out").glob("*.png"))
    assert pngs == ["S1-mobile.png", "S2-desktop.png", "S3-mobile.png", "S4-desktop.png"]
    for png in (workdir / "out").glob("*.png"):
        assert png.read_bytes().startswith(PNG_SIGNATURE)
    assert "screenshots: 4 captured, 0 failed" in result.stdout

    entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    contexts = [e["context"]["viewport"] for e in entries if "context" in e]
    assert contexts == [{"width": 390, "height": 844}, {"width": 1440, "height": 900},
                        {"width": 390, "height": 844}, {"width": 1440, "height": 900}]
    assert [e["goto"] for e in entries if "goto" in e] == [
        f"{server}/", f"{server}/", f"{server}/signup/", f"{server}/signup/"]
    assert all(e["fullPage"] for e in entries if "screenshot" in e)
    # fresh context per screen, no stored auth state or extra headers
    assert all(set(e["context"]) == {"viewport", "deviceScaleFactor"} for e in entries if "context" in e)

    manifest = json.loads((workdir / "out" / "screens.json").read_text(encoding="utf-8"))
    assert manifest["base_url"] == server
    assert [s["status"] for s in manifest["screens"]] == ["ok"] * 4
    assert manifest["screens"][0]["node_id"] == "1:2"
    assert manifest["screens"][0]["figma_url"].endswith("node-id=1-2")


def test_cli_fails_on_http_error_route(server, workdir):
    spec = workdir / "screens.yml"
    spec.write_text(BASE_SPEC.format(route="/", viewport="mobile")
                    + "  - id: S2\n    route: /missing\n    viewport: mobile\n", encoding="utf-8")
    result = run_cli(workdir, server, spec, workdir / "out")
    assert result.returncode == 1, result.stdout + result.stderr
    manifest = json.loads((workdir / "out" / "screens.json").read_text(encoding="utf-8"))
    assert [s["status"] for s in manifest["screens"]] == ["ok", "failed"]
    assert "HTTP 404" in manifest["screens"][1]["error"]
    assert not (workdir / "out" / "S2-mobile.png").exists()


def test_cli_preserves_base_path(server, workdir):
    result = run_cli(workdir, f"{server}/app", SPEC, workdir / "out")
    assert result.returncode == 0, result.stdout + result.stderr


def test_hanging_preview_is_bounded_by_timeouts(workdir):
    spec = workdir / "screens.yml"
    spec.write_text(BASE_SPEC.format(route="/", viewport="mobile")
                    + "".join(f"  - id: S{i}\n    route: /\n    viewport: mobile\n" for i in range(2, 9)),
                    encoding="utf-8")
    started = time.monotonic()
    result = run_cli(workdir, "http://127.0.0.1:9", spec, workdir / "out",
                     env={"FAKE_PW_MODE": "hang", "SCREENSHOT_TIMEOUT_MS": "400", "SCREENSHOT_BUDGET_MS": "1000"})
    elapsed = time.monotonic() - started
    assert result.returncode == 1, result.stdout + result.stderr
    assert elapsed < 10, elapsed
    manifest = json.loads((workdir / "out" / "screens.json").read_text(encoding="utf-8"))
    errors = [s["error"] for s in manifest["screens"]]
    assert len(errors) == 8
    assert "timed out after 400 ms" in errors[0]
    assert any("time budget of 1000 ms exhausted" in e for e in errors), errors


def test_cli_usage_spec_and_setup_errors_exit_2(server, workdir, tmp_path_factory):
    assert run_cli(workdir).returncode == 2
    result = run_cli(workdir, "ftp://example.com", SPEC, workdir / "out")
    assert result.returncode == 2 and "http:// or https://" in result.stderr
    result = run_cli(workdir, server, workdir / "missing.yml", workdir / "out")
    assert result.returncode == 2
    bad = workdir / "bad.yml"
    bad.write_text(BASE_SPEC.format(route="//evil.example", viewport="mobile"), encoding="utf-8")
    result = run_cli(workdir, server, bad, workdir / "out")
    assert result.returncode == 2 and "same-origin path" in result.stderr
    result = run_cli(workdir, server, SPEC, workdir / "out", env={"SCREENSHOT_TIMEOUT_MS": "soon"})
    assert result.returncode == 2 and "SCREENSHOT_TIMEOUT_MS" in result.stderr
    empty = tmp_path_factory.mktemp("no-playwright")  # outside workdir, so no fake module
    result = run_cli(empty, server, SPEC, empty / "out")
    assert result.returncode == 2 and "Playwright is not installed" in result.stderr


# --- PR comment ---------------------------------------------------------------------------------

def test_comment_table_links_each_screenshot_and_figma_frame(server, workdir):
    run_cli(workdir, server, SPEC, workdir / "out")
    manifest_path = workdir / "out" / "screens.json"
    body = node_eval(f"""
      const fs = await import('node:fs');
      const manifest = JSON.parse(fs.readFileSync({json.dumps(str(manifest_path))}, 'utf8'));
      console.log(JSON.stringify(m.buildComment(manifest, {{
        artifactUrl: 'https://github.com/o/r/actions/runs/1/artifacts/2',
        runUrl: 'https://github.com/o/r/actions/runs/1', sha: 'abc123' }})));""")
    print(body)
    assert body.startswith("<!-- ship48-screenshots -->\n### Preview screenshots")
    assert "| Screen | Route | Viewport | Screenshot | Figma frame |" in body
    assert "4 of 4 screens captured" in body
    rows = [line for line in body.splitlines() if re.match(r"\| S\d", line)]
    assert len(rows) == 4
    assert rows[0] == ("| S1 Home (mobile) | `/` | mobile (390×844) | "
                       "[S1-mobile.png](https://github.com/o/r/actions/runs/1/artifacts/2) | "
                       "[1:2](https://www.figma.com/design/FILEKEY/Product?node-id=1-2) |")
    assert "(1440×900)" in rows[1] and "[2:3](" in rows[3]
    assert "SHA `abc123`" in body


def test_comment_reports_failures_escapes_cells_and_handles_missing_manifest():
    out = node_eval("""console.log(JSON.stringify([
      m.buildComment({ base_url: 'http://x', skipped: [{ id: 'M1', name: 'Modal' }], screens: [
        { id: 'S1', name: 'A | <b>', route: '/', viewport: 'm', width: 1, height: 2, status: 'failed',
          error: 'GET / returned HTTP 500', file: 'S1-m.png', figma_url: 'javascript:alert(1)', node_id: '1:2' }] }),
      m.buildComment(null, {}),
    ]));""")
    failed, missing = out
    assert "failed: GET / returned HTTP 500" in failed
    assert "A \\| \\<b\\>" in failed
    assert "javascript:" not in failed
    assert "Not captured (no route): M1" in failed
    assert "0 of 1 screens captured" in failed
    assert missing.startswith("<!-- ship48-screenshots -->")
    assert "No screenshots were captured" in missing


# --- workflows ----------------------------------------------------------------------------------

def preview_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def screenshots_job() -> str:
    text = preview_text()
    return text[text.index("\n  screenshots:\n"):]


def test_preview_workflow_is_valid_and_pinned():
    assert validate_template.validate(TEMPLATE) == []
    assert validate_template.validate_workflow(preview_text(), "preview.yml") == []
    for ref in re.findall(r"uses:\s*(\S+)", preview_text()):
        assert validate_template.check_uses(ref), ref
    assert re.search(r'PLAYWRIGHT_VERSION: "\d+\.\d+\.\d+"', screenshots_job())
    assert '"playwright@$PLAYWRIGHT_VERSION"' in screenshots_job()
    assert "npx --no-install playwright install --with-deps chromium" in screenshots_job()


def test_screenshots_job_runs_after_preview_without_secrets():
    job = screenshots_job()
    assert "    needs: preview\n" in job
    assert "deployment_url: ${{ steps.deploy.outputs.deployment_url }}" in preview_text()
    assert "PREVIEW_URL: ${{ needs.preview.outputs.deployment_url }}" in job
    assert "secrets." not in job and "environment:" not in job
    assert "permissions:" not in job
    assert "persist-credentials: false" in job
    assert "timeout-minutes:" in job and "SCREENSHOT_TIMEOUT_MS" in job and "SCREENSHOT_BUDGET_MS" in job


def test_screenshots_job_captures_uploads_and_comments():
    job = screenshots_job()
    order = [job.index('node scripts/screenshots.mjs "$PREVIEW_URL" design/screens.yml screenshots/'),
             job.index("uses: actions/upload-artifact@"), job.index("uses: actions/github-script@")]
    assert order == sorted(order)
    upload = job[order[1]:order[2]]
    assert "path: screenshots/" in upload
    comment = job[order[2]:]
    assert "ARTIFACT_URL: ${{ steps.upload.outputs.artifact-url }}" in comment
    assert "buildComment" in comment and "MARKER" in comment
    assert "updateComment" in comment and "createComment" in comment
    assert "${{" not in comment.split("script: |", 1)[1]  # no expressions inside the script
    for step in ("id: upload", "Upsert PR comment (screenshot table)"):
        head = job[job.index(step):]
        assert "if: always() && steps.spec.outputs.present == 'true'" in head.split("uses:", 1)[0]


def test_root_ci_runs_verification_command_with_real_playwright():
    text = ROOT_CI.read_text(encoding="utf-8")
    job = text[text.index("  template-screenshots:"):]
    job = job[:job.index("\n  test-integrity:")]
    for ref in re.findall(r"uses:\s*(\S+)", job):
        assert validate_template.check_uses(ref), ref
    assert "working-directory: templates/product-repo" in job
    assert "node scripts/screenshots.mjs http://localhost:4173 design/screens.yml out/" in job
    assert '[ "$count" -ge 4 ]' in job
    version = re.search(r'PLAYWRIGHT_VERSION: "([\d.]+)"', job).group(1)
    assert f'PLAYWRIGHT_VERSION: "{version}"' in screenshots_job()
