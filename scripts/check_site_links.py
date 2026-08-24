#!/usr/bin/env python3
"""Check generated internal links/fragments and a small critical external allowlist."""

from __future__ import annotations

import argparse
import json
import time
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urljoin, urlparse
from urllib.request import Request, urlopen

SITE_ORIGIN = "https://pulsyr.dev"
# www.paddle.com is here because the Privacy Notice must link to Paddle's own
# policy: Paddle is an independent controller of billing data, not our
# subprocessor, so naming it without linking its terms would misstate who
# answers for that data.
ALLOWED_EXTERNAL_HOSTS = {"github.com", "app.pulsyr.dev", "www.paddle.com"}
LANGUAGE_ENDPOINTS = {"/__language/en", "/__language/es"}


class Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []
        self.ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "a" and values.get("href"):
            self.hrefs.append(values["href"] or "")
        if values.get("id"):
            self.ids.add(values["id"] or "")
        if tag == "a" and values.get("name"):
            self.ids.add(values["name"] or "")


def route_for(path: Path, dist: Path) -> str:
    relative = path.relative_to(dist).as_posix()
    if relative == "index.html":
        return "/"
    if relative.endswith("/index.html"):
        return f"/{relative.removesuffix('index.html')}"
    return f"/{relative}"


def target_file(dist: Path, url_path: str) -> Path:
    clean = unquote(url_path).lstrip("/")
    candidate = dist / PurePosixPath(clean)
    if url_path.endswith("/") or not candidate.suffix:
        candidate = candidate / "index.html"
    return candidate


def check_internal(dist: Path) -> tuple[list[str], set[str], int]:
    errors: list[str] = []
    external: set[str] = set()
    documents: dict[Path, Links] = {}
    routes: dict[Path, str] = {}
    for path in dist.rglob("*.html"):
        parser = Links()
        parser.feed(path.read_text(encoding="utf-8"))
        resolved = path.resolve()
        documents[resolved] = parser
        routes[resolved] = route_for(path, dist)

    checked = 0
    for source, parser in documents.items():
        source_url = f"{SITE_ORIGIN}{routes[source]}"
        for href in parser.hrefs:
            parsed = urlparse(urljoin(source_url, href))
            if parsed.scheme not in {"http", "https"}:
                continue
            if parsed.netloc and parsed.netloc != "pulsyr.dev":
                external.add(f"{parsed.scheme}://{parsed.netloc}{parsed.path}")
                if parsed.netloc not in ALLOWED_EXTERNAL_HOSTS:
                    errors.append(f"{routes[source]}: external host is not approved: {parsed.netloc}")
                continue
            checked += 1
            if parsed.path.rstrip("/") in LANGUAGE_ENDPOINTS:
                next_values = parse_qs(parsed.query).get("next", [])
                if len(next_values) != 1:
                    errors.append(f"{routes[source]}: language link requires one next path: {href}")
                    continue
                next_path = urlparse(next_values[0]).path
                if not target_file(dist, next_path).resolve().is_file():
                    errors.append(
                        f"{routes[source]}: language link points from missing page: {next_path}"
                    )
                continue
            target = target_file(dist, parsed.path).resolve()
            if not target.is_file():
                errors.append(f"{routes[source]}: missing internal target {href}")
                continue
            if parsed.fragment and parsed.fragment not in documents.get(target, Links()).ids:
                errors.append(f"{routes[source]}: missing fragment #{parsed.fragment} in {parsed.path}")
    return errors, external, checked


def probe(url: str, attempts: int = 3) -> tuple[int | None, str | None]:
    last_error: str | None = None
    for attempt in range(attempts):
        try:
            request = Request(url, method="HEAD", headers={"User-Agent": "Pulsyr-CI-Link-Checker/1.0"})
            with urlopen(request, timeout=20) as response:  # noqa: S310
                return response.status, None
        except HTTPError as exc:
            if exc.code not in {405, 429}:
                return exc.code, str(exc)
            last_error = str(exc)
        except (ConnectionError, TimeoutError, URLError) as exc:
            last_error = str(exc)
        if attempt + 1 < attempts:
            time.sleep(2**attempt)
    return None, last_error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist", type=Path)
    parser.add_argument("--critical", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--skip-external", action="store_true")
    args = parser.parse_args()

    errors, discovered, internal_count = check_internal(args.dist)
    critical = {
        line.strip()
        for line in args.critical.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    missing = sorted(critical - discovered)
    errors.extend(f"critical external URL is not linked by the site: {url}" for url in missing)
    external_results: dict[str, dict[str, int | str | None]] = {}
    if not args.skip_external:
        for url in sorted(critical):
            status, error = probe(url)
            external_results[url] = {"status": status, "error": error}
            if status is None or not 200 <= status < 400:
                errors.append(f"critical external URL failed: {url} ({status or error})")

    report = {
        "gate": "links",
        "thresholds": {"broken_internal": 0, "broken_critical_external": 0},
        "internal_links_checked": internal_count,
        "external_hosts": sorted({urlparse(url).netloc for url in discovered}),
        "critical_external": external_results,
        "passed": not errors,
        "errors": errors,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if errors:
        print("Link gate failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Link gate passed ({internal_count} internal, {len(critical)} critical external).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
