"""Tiny HTTP service used to exercise the AI factory end to end. Standard library only."""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer


def handle(path: str) -> tuple[int, dict]:
    """Route a GET path to (status, json_body). Pure function so it is trivial to test."""
    if path == "/":
        return 200, {"service": "ai-factory-sandbox"}
    return 404, {"error": "not found"}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        status, body = handle(self.path)
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 8000), Handler).serve_forever()
