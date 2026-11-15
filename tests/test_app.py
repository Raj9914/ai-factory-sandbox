import http.client
import json
import threading

import pytest

from app import handle, serve


def test_root():
    assert handle("/") == (200, {"service": "ai-factory-sandbox"})


def test_unknown_path():
    assert handle("/nope")[0] == 404


def test_health():
    assert handle("/health") == (200, {"status": "ok"})


@pytest.mark.parametrize("path", ["/health?probe=1", "/health#x", "/health?a=1&b=2"])
def test_health_ignores_query_and_fragment(path):
    assert handle(path) == (200, {"status": "ok"})


@pytest.fixture
def server():
    srv = serve("127.0.0.1", 0)  # port 0: the OS picks a free port
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv.server_address
    srv.shutdown()
    srv.server_close()


def request(addr, method, path):
    conn = http.client.HTTPConnection(*addr, timeout=5)
    conn.request(method, path)
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp, body


def test_http_get_health(server):
    resp, body = request(server, "GET", "/health")
    assert resp.status == 200
    assert resp.getheader("Content-Type") == "application/json"
    assert int(resp.getheader("Content-Length")) == len(body)
    assert json.loads(body) == {"status": "ok"}


def test_http_get_unknown_is_json_404(server):
    resp, body = request(server, "GET", "/nope")
    assert resp.status == 404
    assert json.loads(body) == {"error": "not found"}


def test_http_head_has_headers_but_no_body(server):
    resp, body = request(server, "HEAD", "/health")
    assert resp.status == 200
    assert body == b""
    assert int(resp.getheader("Content-Length")) == len(json.dumps({"status": "ok"}).encode())


def test_http_post_is_json_405(server):
    resp, body = request(server, "POST", "/health")
    assert resp.status == 405
    assert resp.getheader("Allow") == "GET, HEAD"
    assert json.loads(body) == {"error": "method not allowed"}


def test_serve_reads_host_and_port_from_env(monkeypatch):
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("PORT", "0")
    srv = serve()
    try:
        assert srv.server_address[0] == "127.0.0.1"
        assert srv.server_address[1] > 0
    finally:
        srv.server_close()
