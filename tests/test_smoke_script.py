"""Tests for templates/product-repo/scripts/smoke.sh against real local HTTP servers (issue #53)."""
import http.server
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates" / "product-repo"
SCRIPT = TEMPLATE / "scripts" / "smoke.sh"
ROUTES = TEMPLATE / "smoke-routes.txt"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def run_smoke(url, routes_file=None, timeout=90, **env):
    environ = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(ROOT)}
    if routes_file is not None:
        environ["SMOKE_ROUTES_FILE"] = str(routes_file)
    environ.update({k: str(v) for k, v in env.items()})
    return subprocess.run(["bash", str(SCRIPT), url], capture_output=True, text=True,
                          env=environ, timeout=timeout)


def summary_rows(stdout):
    """Map route -> (status, result) from the printed summary table."""
    lines = stdout.splitlines()
    start = next(i for i, line in enumerate(lines) if line.split()[:3] == ["ROUTE", "STATUS", "RESULT"])
    rows = {}
    for line in lines[start + 1:]:
        if line.startswith("smoke:"):
            break
        route, status, result = line.split(None, 2)
        rows[route] = (status, result)
    return rows


@pytest.fixture
def static_server(tmp_path):
    """`python -m http.server` serving a static site (no /health), as in the issue's step 2."""
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<h1>ok</h1>", encoding="utf-8")
    (site / "about.html").write_text("<h1>about</h1>", encoding="utf-8")
    port = free_port()
    proc = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1",
                             "--directory", str(site)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 15
    while True:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            break
        except OSError:
            if time.monotonic() > deadline:
                proc.kill()
                pytest.fail("http.server did not start")
            time.sleep(0.1)
    yield f"http://127.0.0.1:{port}"
    proc.terminate()
    proc.wait(timeout=10)


class AppServer:
    """In-process app with /health; can answer 503 for the first N requests to a path."""

    def __init__(self, failures=None):
        self.failures = dict(failures or {})
        self.headers_seen = []
        app = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                app.headers_seen.append({k.lower(): v for k, v in self.headers.items()})
                if app.failures.get(self.path, 0) > 0:
                    app.failures[self.path] -= 1
                    code = 503
                elif self.path in ("/health", "/", "/api/status"):
                    code = 200
                else:
                    code = 404
                self.send_response(code)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


def write_routes(tmp_path, *routes):
    path = tmp_path / "routes.txt"
    path.write_text("# comment\n\n" + "\n".join(routes) + "\n", encoding="utf-8")
    return path


# --- acceptance criteria -----------------------------------------------------

def test_healthy_static_server_exits_zero_and_lists_routes(static_server, tmp_path):
    result = run_smoke(static_server, write_routes(tmp_path, "/about.html  # inline comment"))
    assert result.returncode == 0, result.stdout + result.stderr
    rows = summary_rows(result.stdout)
    assert rows == {"/health": ("404", "SKIP (static)"), "/": ("200", "PASS"),
                    "/about.html": ("200", "PASS")}
    assert result.stdout.rstrip().endswith("smoke: PASS")


def test_template_routes_file_passes_against_static_server(static_server):
    result = run_smoke(static_server)  # default routes file: templates/product-repo/smoke-routes.txt
    assert result.returncode == 0, result.stdout + result.stderr
    assert summary_rows(result.stdout)["/"] == ("200", "PASS")


def test_issue_verification_dead_port_fails_within_60_seconds():
    # Issue section 11: bash templates/product-repo/scripts/smoke.sh http://127.0.0.1:9; echo $?
    started = time.monotonic()
    result = subprocess.run(["bash", str(SCRIPT), "http://127.0.0.1:9"], capture_output=True,
                            text=True, timeout=90)
    elapsed = time.monotonic() - started
    assert result.returncode != 0
    assert elapsed < 60, f"took {elapsed:.1f}s"
    rows = summary_rows(result.stdout)
    assert rows["/health"] == ("000", "FAIL")
    assert rows["/"] == ("-", "SKIP (unhealthy)")
    # 5 retries when the port refuses at once; fewer when it hangs and the budget runs out.
    assert 1 <= result.stderr.count("retry ") <= 5
    assert result.stdout.rstrip().endswith("smoke: FAIL")


def test_hanging_server_stops_within_budget(tmp_path):
    """A host that accepts connections but never answers fails once the per-check budget is spent."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(16)
        url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        started = time.monotonic()
        result = run_smoke(url, write_routes(tmp_path, "/"), SMOKE_TIMEOUT=2, SMOKE_BUDGET=5,
                           SMOKE_BACKOFF=1)
        elapsed = time.monotonic() - started
    assert result.returncode == 1
    assert summary_rows(result.stdout)["/health"] == ("000", "FAIL")
    assert elapsed < 9, f"took {elapsed:.1f}s"
    assert result.stderr.count("retry ") < 5


def test_app_with_health_endpoint_passes(tmp_path):
    with AppServer() as app:
        result = run_smoke(app.url + "/", write_routes(tmp_path, "/", "api/status"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert summary_rows(result.stdout) == {"/health": ("200", "PASS"), "/": ("200", "PASS"),
                                           "/api/status": ("200", "PASS")}


def test_missing_route_fails_with_status_in_summary(tmp_path):
    with AppServer() as app:
        result = run_smoke(app.url, write_routes(tmp_path, "/", "/missing"), SMOKE_BACKOFF=0)
    assert result.returncode == 1
    rows = summary_rows(result.stdout)
    assert rows["/"] == ("200", "PASS")
    assert rows["/missing"] == ("404", "FAIL")
    assert "retry" not in result.stderr  # 4xx is not retried


def test_5xx_is_retried_then_passes(tmp_path):
    with AppServer(failures={"/health": 3}) as app:
        result = run_smoke(app.url, write_routes(tmp_path), SMOKE_BACKOFF=0)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr.count("retry ") == 3


def test_persistent_5xx_fails_after_five_retries(tmp_path):
    with AppServer(failures={"/health": 100}) as app:
        result = run_smoke(app.url, write_routes(tmp_path, "/"), SMOKE_BACKOFF=0)
    assert result.returncode == 1
    assert summary_rows(result.stdout)["/health"] == ("503", "FAIL")
    assert result.stderr.count("retry ") == 5


def test_requests_carry_no_auth_headers_or_cookies(tmp_path):
    with AppServer() as app:
        assert run_smoke(app.url, write_routes(tmp_path, "/")).returncode == 0
    assert app.headers_seen
    for headers in app.headers_seen:
        assert "authorization" not in headers and "cookie" not in headers
    text = SCRIPT.read_text(encoding="utf-8")
    assert "Authorization" not in text and "--header" not in text and " -H " not in text
    assert "curl -q " in text  # ignore ~/.curlrc, which could inject headers


def test_each_request_has_a_10_second_time_limit():
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'TIMEOUT="${SMOKE_TIMEOUT:-10}"' in text
    assert 'RETRIES="${SMOKE_RETRIES:-5}"' in text
    assert 'BUDGET="${SMOKE_BUDGET:-50}"' in text
    assert '--max-time "$2"' in text and "limit=$(( left < TIMEOUT ? left : TIMEOUT ))" in text


@pytest.mark.parametrize("args", [[], ["a", "b"], ["ftp://example.com"], ["example.com"]])
def test_bad_usage_exits_2(args):
    result = subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, timeout=30)
    assert result.returncode == 2
    assert "usage:" in result.stderr


def test_template_routes_file_lists_root():
    routes = [line.split("#")[0].strip() for line in ROUTES.read_text(encoding="utf-8").splitlines()]
    assert [r for r in routes if r] == ["/"]


# --- make smoke URL=... wraps the script ----------------------------------------

def test_make_smoke_wraps_script(static_server):
    result = subprocess.run(["make", "-s", "-C", str(TEMPLATE), "smoke", f"URL={static_server}"],
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "smoke summary for" in result.stdout
    assert summary_rows(result.stdout)["/"] == ("200", "PASS")


def test_make_smoke_fails_on_dead_url():
    result = subprocess.run(["make", "-s", "-C", str(TEMPLATE), "smoke", "URL=http://127.0.0.1:9"],
                            capture_output=True, text=True, timeout=90,
                            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "SMOKE_BACKOFF": "0", "SMOKE_BUDGET": "5"})
    assert result.returncode != 0
    assert "smoke: FAIL" in result.stdout


def test_make_smoke_without_url_prints_usage():
    result = subprocess.run(["make", "-s", "-C", str(TEMPLATE), "smoke"],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert "usage: make smoke URL=<base-url>" in result.stdout
