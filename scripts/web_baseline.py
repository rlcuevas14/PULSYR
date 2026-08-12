#!/usr/bin/env python3
"""Capture a small, reproducible DNS/TLS/HTTP baseline for a web origin.

The probe is intentionally read-only and uses only Python's standard library so it
can run locally or on GitHub Actions without installing the application. It records
only an allowlist of response headers and never persists cookies or response bodies.
Network failures are evidence too: the command still writes a report and exits zero.
Use the separate test suite to validate probe behavior without touching production.
"""

from __future__ import annotations

import argparse
import json
import platform
import socket
import ssl
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

DEFAULT_ROUTES = (
    "/",
    "/login",
    "/robots.txt",
    "/sitemap.xml",
    "/llms.txt",
    "/definitely-missing-web-baseline",
)

OBSERVED_HEADERS = (
    "content-type",
    "content-encoding",
    "cache-control",
    "strict-transport-security",
    "content-security-policy",
    "x-frame-options",
    "x-content-type-options",
    "referrer-policy",
    "permissions-policy",
    "x-robots-tag",
    "location",
    "server",
    "vary",
    "etag",
    "last-modified",
)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_base_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("base URL must be an absolute http(s) origin")
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        raise ValueError("base URL must not contain a path, query or fragment")
    host = parsed.hostname.lower()
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{host}{port}"


def resolve_dns(hostname: str) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        addresses = sorted(
            {
                row[4][0]
                for row in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
            }
        )
        return {
            "ok": True,
            "addresses": addresses,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        }
    except OSError as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        }


def _certificate_common_name(certificate: dict[str, Any], field: str) -> str | None:
    for relative_name in certificate.get(field, ()):
        for attribute, value in relative_name:
            if attribute == "commonName":
                return str(value)
    return None


def inspect_tls(hostname: str, port: int = 443, timeout: float = 10.0) -> dict[str, Any]:
    started = time.perf_counter()
    context = ssl.create_default_context()
    try:
        with socket.create_connection((hostname, port), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=hostname) as wrapped:
                certificate: dict[str, Any] = dict(wrapped.getpeercert() or {})
                cipher = wrapped.cipher()
                return {
                    "ok": True,
                    "protocol": wrapped.version(),
                    "cipher": cipher[0] if cipher else None,
                    "subject_common_name": _certificate_common_name(certificate, "subject"),
                    "issuer_common_name": _certificate_common_name(certificate, "issuer"),
                    "not_before": certificate.get("notBefore"),
                    "not_after": certificate.get("notAfter"),
                    "elapsed_ms": round((time.perf_counter() - started) * 1000),
                }
    except (OSError, ssl.SSLError) as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        }


def _selected_headers(headers) -> dict[str, str]:  # noqa: ANN001
    return {
        name: value
        for name in OBSERVED_HEADERS
        if (value := headers.get(name)) is not None
    }


def inspect_http(base_url: str, route: str, timeout: float = 15.0) -> dict[str, Any]:
    url = urljoin(f"{base_url}/", route.lstrip("/"))
    request = Request(
        url,
        method="GET",
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip, br, zstd",
            "User-Agent": "PulsyrWebBaseline/1.0 (+https://pulsyr.dev)",
        },
    )
    started = time.perf_counter()
    opener = build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            response.read(1)
            return {
                "ok": True,
                "url": url,
                "status": response.status,
                "headers": _selected_headers(response.headers),
                "elapsed_ms": round((time.perf_counter() - started) * 1000),
            }
    except HTTPError as exc:
        # urllib represents non-2xx and blocked redirects as HTTPError, but their
        # status and headers are valid observations rather than network failures.
        return {
            "ok": True,
            "url": url,
            "status": exc.code,
            "headers": _selected_headers(exc.headers),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        }
    except (URLError, OSError, TimeoutError) as exc:
        return {
            "ok": False,
            "url": url,
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        }


def capture(base_url: str, routes: list[str], location: str, label: str) -> dict[str, Any]:
    parsed = urlparse(base_url)
    tls = (
        inspect_tls(parsed.hostname or "", parsed.port or 443)
        if parsed.scheme == "https"
        else {"ok": False, "skipped": "origin does not use HTTPS"}
    )
    return {
        "schema_version": 1,
        "captured_at": _utc_now(),
        "label": label,
        "location": location,
        "runner": {
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        "base_url": base_url,
        "dns": resolve_dns(parsed.hostname or ""),
        "tls": tls,
        "routes": [inspect_http(base_url, route) for route in routes],
    }


def markdown_summary(report: dict[str, Any]) -> str:
    lines = [
        "## Web baseline",
        "",
        f"- Captured: `{report.get('captured_at', 'unknown')}`",
        f"- Origin: `{report.get('base_url', 'unknown')}`",
        f"- Location: `{report.get('location', 'unknown')}`",
        f"- Label: `{report.get('label', 'unknown')}`",
        f"- DNS: `{'ok' if report.get('dns', {}).get('ok') else 'failed'}`",
        f"- TLS: `{'ok' if report.get('tls', {}).get('ok') else 'failed'}`",
        "",
        "| URL | Status | Encoding | Cache | CSP | Robots |",
        "|---|---:|---|---|---|---|",
    ]
    for route in report.get("routes", []):
        headers = route.get("headers", {})
        status = route.get("status", "network-error")
        lines.append(
            "| {url} | {status} | {encoding} | {cache} | {csp} | {robots} |".format(
                url=route.get("url", "unknown"),
                status=status,
                encoding=headers.get("content-encoding", "—"),
                cache=headers.get("cache-control", "—"),
                csp="yes" if headers.get("content-security-policy") else "—",
                robots=headers.get("x-robots-tag", "—"),
            )
        )
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--base-url", help="Absolute origin, without a path")
    mode.add_argument("--summarize", type=Path, help="Render an existing JSON report as Markdown")
    parser.add_argument("--route", action="append", dest="routes", help="Route to observe; repeatable")
    parser.add_argument("--location", default="local/unspecified")
    parser.add_argument("--label", default="manual-baseline")
    parser.add_argument("--output", type=Path, help="Write JSON to this path instead of stdout")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.summarize:
        report = json.loads(args.summarize.read_text(encoding="utf-8"))
        print(markdown_summary(report))
        return 0

    try:
        base_url = normalize_base_url(args.base_url)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    report = capture(base_url, args.routes or list(DEFAULT_ROUTES), args.location, args.label)
    payload = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
