import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from scripts.web_baseline import inspect_http, markdown_summary, normalize_base_url


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/target")
            self.end_headers()
            return
        if self.path == "/missing":
            self.send_response(404)
        else:
            self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Cache-Control", "public, max-age=60")
        # This value must never be copied into a report.
        self.send_header("Set-Cookie", "session=do-not-record")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format, *args):  # noqa: A002, ANN001
        return


@pytest.fixture
def local_origin():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://Example.com/", "https://example.com"),
        ("http://localhost:8000", "http://localhost:8000"),
    ],
)
def test_normalize_base_url(raw, expected):
    assert normalize_base_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["example.com", "ftp://example.com", "https://example.com/path", "https://example.com/?x=1"],
)
def test_normalize_base_url_rejects_non_origins(raw):
    with pytest.raises(ValueError):
        normalize_base_url(raw)


def test_http_probe_keeps_status_and_only_safe_headers(local_origin):
    ok = inspect_http(local_origin, "/")
    redirect = inspect_http(local_origin, "/redirect")
    missing = inspect_http(local_origin, "/missing")

    assert ok["status"] == 200
    assert ok["headers"]["cache-control"] == "public, max-age=60"
    assert "set-cookie" not in ok["headers"]
    assert redirect["status"] == 302
    assert redirect["headers"]["location"] == "/target"
    assert missing["status"] == 404


def test_markdown_summary_is_stable_json_input():
    report = {
        "captured_at": "2026-08-12T00:00:00+00:00",
        "base_url": "https://example.com",
        "location": "test",
        "label": "fixture",
        "dns": {"ok": True},
        "tls": {"ok": True},
        "routes": [
            {
                "url": "https://example.com/",
                "status": 200,
                "headers": {"content-encoding": "gzip", "x-robots-tag": "noindex"},
            }
        ],
    }

    # Ensure the report remains serializable before presenting it in a workflow summary.
    rendered = markdown_summary(json.loads(json.dumps(report)))
    assert "https://example.com/" in rendered
    assert "gzip" in rendered
    assert "noindex" in rendered
