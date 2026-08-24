#!/usr/bin/env python3
"""Verify the generated public site's indexing and technical SEO contract."""

from __future__ import annotations

import argparse
import json
import struct
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

SITE_ORIGIN = "https://pulsyr.dev"
ENGLISH_ROUTES = (
    "/",
    "/producto/",
    "/integraciones/mcp/",
    "/precios/",
    "/open-source/",
    "/docs/primeros-pasos/",
    "/seguridad/",
    "/privacidad/",
    "/terminos/",
    "/reembolsos/",
    "/contacto/",
)
EXPECTED_ROUTES = ENGLISH_ROUTES + tuple(
    "/es/" if route == "/" else f"/es{route}" for route in ENGLISH_ROUTES
)
ERROR_ROUTES = ("/404/", "/500/")


class DocumentContract(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.h1_count = 0
        self.title = ""
        self.description = ""
        self.robots = ""
        self.lang = ""
        self.canonical = ""
        self.hreflang: dict[str, str] = {}
        self.meta: dict[str, str] = {}
        self.links: list[str] = []
        self.executable_scripts: list[str] = []
        self.json_ld_sources: list[str] = []
        self._in_title = False
        self._in_json_ld = False
        self._json_ld_source = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "html":
            self.lang = values.get("lang") or ""
        elif tag == "h1":
            self.h1_count += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (values.get("name") or values.get("property") or "").lower()
            content = values.get("content") or ""
            self.meta[key] = content
            if key == "description":
                self.description = content
            elif key == "robots":
                self.robots = content
        elif tag == "link":
            rel = (values.get("rel") or "").lower().split()
            href = values.get("href") or ""
            if "canonical" in rel:
                self.canonical = href
            if "alternate" in rel and values.get("hreflang"):
                self.hreflang[(values["hreflang"] or "").lower()] = href
        elif tag == "a" and values.get("href"):
            self.links.append(values["href"] or "")
        elif tag == "script":
            if (values.get("type") or "").lower() == "application/ld+json":
                self._in_json_ld = True
                self._json_ld_source = ""
            else:
                self.executable_scripts.append(values.get("src") or "inline")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "script" and self._in_json_ld:
            self.json_ld_sources.append(self._json_ld_source)
            self._in_json_ld = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if self._in_json_ld:
            self._json_ld_source += data


def route_file(dist: Path, route: str) -> Path:
    if route == "/":
        return dist / "index.html"
    if route in ERROR_ROUTES:
        return dist / f"{route.strip('/')}.html"
    return dist.joinpath(*route.strip("/").split("/"), "index.html")


def localized_route(route: str, locale: str) -> str:
    base = route[3:] if route.startswith("/es/") else "/" if route == "/es/" else route
    if locale == "en":
        return base
    return "/es/" if base == "/" else f"/es{base}"


def _schema_types(parser: DocumentContract, route: str, errors: list[str]) -> set[str]:
    types: set[str] = set()
    for source in parser.json_ld_sources:
        try:
            payload = json.loads(source)
        except json.JSONDecodeError as exc:
            errors.append(f"{route}: invalid JSON-LD: {exc}")
            continue
        nodes = payload.get("@graph", [payload]) if isinstance(payload, dict) else []
        for node in nodes:
            if isinstance(node, dict) and isinstance(node.get("@type"), str):
                types.add(node["@type"])
    return types


def _inspect_social_image(dist: Path, errors: list[str]) -> None:
    path = dist / "og" / "pulsyr-social.png"
    if not path.is_file():
        errors.append("social image: missing /og/pulsyr-social.png")
        return
    header = path.read_bytes()[:24]
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        errors.append("social image: expected a valid PNG")
        return
    width, height = struct.unpack(">II", header[16:24])
    if (width, height) != (1200, 630):
        errors.append(f"social image: expected 1200x630, found {width}x{height}")


def _inspect_root_files(dist: Path, errors: list[str]) -> None:
    sitemap = dist / "sitemap.xml"
    if not sitemap.is_file():
        errors.append("sitemap: missing /sitemap.xml")
    else:
        try:
            root = ET.fromstring(sitemap.read_text(encoding="utf-8"))
            namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            urls = {element.text or "" for element in root.findall("s:url/s:loc", namespace)}
            expected = {f"{SITE_ORIGIN}{route}" for route in EXPECTED_ROUTES}
            if urls != expected:
                errors.append(f"sitemap: expected only {sorted(expected)}, found {sorted(urls)}")
        except ET.ParseError as exc:
            errors.append(f"sitemap: invalid XML: {exc}")

    robots = dist / "robots.txt"
    if not robots.is_file():
        errors.append("robots: missing /robots.txt")
    else:
        body = robots.read_text(encoding="utf-8")
        if "User-agent: *" not in body or "Allow: /" not in body:
            errors.append("robots: missing explicit public crawling policy")
        if f"Sitemap: {SITE_ORIGIN}/sitemap.xml" not in body:
            errors.append("robots: missing canonical sitemap reference")

    llms = dist / "llms.txt"
    if not llms.is_file() or "linked pages and repository are authoritative" not in llms.read_text(
        encoding="utf-8"
    ):
        errors.append("llms: missing or does not identify authoritative sources")


def inspect_site(dist: Path) -> list[str]:
    errors: list[str] = []
    documents: dict[str, DocumentContract] = {}
    titles: dict[str, str] = {}
    descriptions: dict[str, str] = {}
    declared_documents = {
        route_file(dist, route).resolve() for route in (*EXPECTED_ROUTES, *ERROR_ROUTES)
    }
    unexpected_documents = sorted(
        str(path.relative_to(dist))
        for path in dist.rglob("*.html")
        if path.resolve() not in declared_documents
    )
    if unexpected_documents:
        errors.append(
            "metadata contract: undeclared HTML documents found: "
            f"{unexpected_documents}"
        )

    for route in EXPECTED_ROUTES:
        path = route_file(dist, route)
        if not path.is_file():
            errors.append(f"{route}: missing generated file {path}")
            continue
        parser = DocumentContract()
        parser.feed(path.read_text(encoding="utf-8"))
        documents[route] = parser
        canonical = f"{SITE_ORIGIN}{route}"
        locale = "es" if route == "/es/" or route.startswith("/es/") else "en"
        if parser.lang != locale:
            errors.append(f"{route}: expected html lang={locale}")
        if parser.h1_count != 1:
            errors.append(f"{route}: expected exactly one h1, found {parser.h1_count}")
        if not parser.title.strip():
            errors.append(f"{route}: missing title")
        if len(parser.description.strip()) < 60:
            errors.append(f"{route}: description is missing or too short")
        if parser.robots.lower().replace(" ", "") != "index,follow":
            errors.append(f"{route}: expected robots index,follow")
        if parser.canonical != canonical:
            errors.append(f"{route}: expected canonical {canonical}, found {parser.canonical!r}")
        english_url = f"{SITE_ORIGIN}{localized_route(route, 'en')}"
        spanish_url = f"{SITE_ORIGIN}{localized_route(route, 'es')}"
        expected_hreflang = {"en": english_url, "es": spanish_url, "x-default": english_url}
        if parser.hreflang != expected_hreflang:
            errors.append(f"{route}: incorrect hreflang alternates: {parser.hreflang}")
        social_alt = (
            "Pulsyr — El backlog que tu agente mantiene"
            if locale == "es"
            else "Pulsyr — The backlog your agent maintains"
        )
        required_meta = {
            "og:title": parser.title,
            "og:description": parser.description,
            "og:type": "website",
            "og:url": canonical,
            "og:image": f"{SITE_ORIGIN}/og/pulsyr-social.png",
            "og:image:width": "1200",
            "og:image:height": "630",
            "og:image:alt": social_alt,
            "og:locale": "es_ES" if locale == "es" else "en_US",
            "og:locale:alternate": "en_US" if locale == "es" else "es_ES",
            "twitter:card": "summary_large_image",
            "twitter:title": parser.title,
            "twitter:description": parser.description,
            "twitter:image": f"{SITE_ORIGIN}/og/pulsyr-social.png",
            "twitter:image:alt": social_alt,
        }
        for key, expected in required_meta.items():
            if parser.meta.get(key) != expected:
                errors.append(f"{route}: expected {key}={expected!r}")
        if not any(href.startswith("https://app.pulsyr.dev") for href in parser.links):
            errors.append(f"{route}: missing app CTA")
        if parser.executable_scripts:
            errors.append(f"{route}: unexpected executable JavaScript: {parser.executable_scripts}")
        schema_types = _schema_types(parser, route, errors)
        expected_types = (
            {"Organization", "WebSite"}
            if route in ("/", "/es/")
            else {"SoftwareApplication", "BreadcrumbList"}
            if route in ("/producto/", "/es/producto/")
            else {"BreadcrumbList"}
        )
        if not expected_types <= schema_types:
            errors.append(
                f"{route}: expected schema types {sorted(expected_types)}, "
                f"found {sorted(schema_types)}"
            )
        if parser.title in titles:
            errors.append(f"{route}: duplicate title with {titles[parser.title]}")
        titles[parser.title] = route
        if parser.description in descriptions:
            errors.append(f"{route}: duplicate description with {descriptions[parser.description]}")
        descriptions[parser.description] = route

    for route, parser in documents.items():
        for href in parser.links:
            parsed = urlparse(href)
            if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
                continue
            target = parsed.path
            if target.startswith(("/favicon", "/og/")) or "." in Path(target).name:
                continue
            if target.startswith("/__language/"):
                continue
            normalized = target if target.endswith("/") else f"{target}/"
            if normalized not in EXPECTED_ROUTES and not route_file(dist, normalized).is_file():
                errors.append(f"{route}: internal link has no generated target: {href}")

    for route in ERROR_ROUTES:
        path = route_file(dist, route)
        if not path.is_file():
            errors.append(f"{route}: missing branded error document")
            continue
        parser = DocumentContract()
        parser.feed(path.read_text(encoding="utf-8"))
        if parser.h1_count != 1 or parser.robots.lower().replace(" ", "") != "noindex,follow":
            errors.append(f"{route}: error document must have one h1 and noindex,follow")

    _inspect_root_files(dist, errors)
    _inspect_social_image(dist, errors)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist", type=Path)
    parser.add_argument("--report", type=Path, help="Write a machine-readable gate report")
    args = parser.parse_args()
    errors = inspect_site(args.dist)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(
                {
                    "gate": "seo-crawl",
                    "routes": list(EXPECTED_ROUTES),
                    "thresholds": {
                        "metadata_coverage_percent": 100,
                        "private_urls_in_sitemap": 0,
                        "broken_internal_links": 0,
                    },
                    "passed": not errors,
                    "errors": errors,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    if errors:
        print("Public site contract failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Public site contract passed for {len(EXPECTED_ROUTES)} indexable routes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
