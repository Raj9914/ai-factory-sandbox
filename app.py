"""Tiny HTTP service used to exercise the AI factory end to end. Standard library only."""
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit


def handle(path: str) -> tuple[int, dict]:
    """Route a GET path to (status, json_body). Pure function so it is trivial to test.

    Query strings and fragments are ignored, so "/health?probe=1" routes like "/health".
    """
    route = urlsplit(path).path
    if route == "/":
        return 200, {"service": "ai-factory-sandbox"}
    if route == "/health":
        return 200, {"status": "ok"}
    return 404, {"error": "not found"}


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: dict, include_body: bool = True, extra_headers: dict | None = None) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if include_body:
            self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        self._send(*handle(self.path))

    def do_HEAD(self):  # noqa: N802
        self._send(*handle(self.path), include_body=False)

    def _method_not_allowed(self):
        self._send(405, {"error": "method not allowed"}, extra_headers={"Allow": "GET, HEAD"})

    do_POST = do_PUT = do_PATCH = do_DELETE = _method_not_allowed  # noqa: N815


def serve(host: str | None = None, port: int | None = None) -> HTTPServer:
    """Build the server. HOST/PORT env vars override the local-only defaults (use HOST=0.0.0.0 in a container)."""
    host = host if host is not None else os.environ.get("HOST", "127.0.0.1")
    port = port if port is not None else int(os.environ.get("PORT", "8000"))
    return HTTPServer((host, port), Handler)


if __name__ == "__main__":
    serve().serve_forever()
