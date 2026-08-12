#!/usr/bin/env python3
"""Verify the minimum P1 contract against Astro's generated HTML."""

from __future__ import annotations

import argparse
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

EXPECTED_ROUTES = (
    "/",
    "/producto/",
    "/integraciones/mcp/",
    "/open-source/",
    "/docs/primeros-pasos/",
    "/seguridad/",
    "/privacidad/",
    "/terminos/",
    "/contacto/",
)


class DocumentContract(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.h1_count = 0
        self.title = ""
        self.description = ""
        self.robots = ""
        self.lang = ""
        self.links: list[str] = []
        self.scripts: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "html":
            self.lang = values.get("lang") or ""
        elif tag == "h1":
            self.h1_count += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            name = (values.get("name") or "").lower()
            if name == "description":
                self.description = values.get("content") or ""
            elif name == "robots":
                self.robots = values.get("content") or ""
        elif tag == "a" and values.get("href"):
            self.links.append(values["href"] or "")
        elif tag == "script":
            self.scripts.append(values.get("src") or "inline")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data


def route_file(dist: Path, route: str) -> Path:
    if route == "/":
        return dist / "index.html"
    return dist.joinpath(*route.strip("/").split("/"), "index.html")


def inspect_site(dist: Path) -> list[str]:
    errors: list[str] = []
    documents: dict[str, DocumentContract] = {}
    titles: dict[str, str] = {}
    descriptions: dict[str, str] = {}

    for route in EXPECTED_ROUTES:
        path = route_file(dist, route)
        if not path.is_file():
            errors.append(f"{route}: missing generated file {path}")
            continue
        parser = DocumentContract()
        parser.feed(path.read_text(encoding="utf-8"))
        documents[route] = parser
        if parser.lang != "en":
            errors.append(f"{route}: expected html lang=en")
        if parser.h1_count != 1:
            errors.append(f"{route}: expected exactly one h1, found {parser.h1_count}")
        if not parser.title.strip():
            errors.append(f"{route}: missing title")
        if len(parser.description.strip()) < 60:
            errors.append(f"{route}: description is missing or too short")
        if parser.robots.lower().replace(" ", "") != "index,follow":
            errors.append(f"{route}: expected robots index,follow")
        if not any(href.startswith("https://app.pulsyr.dev") for href in parser.links):
            errors.append(f"{route}: missing app CTA")
        if parser.scripts:
            errors.append(f"{route}: P1 pages should need no JavaScript, found {parser.scripts}")
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
            if target.startswith("/favicon") or "." in Path(target).name:
                continue
            normalized = target if target.endswith("/") else f"{target}/"
            if normalized not in EXPECTED_ROUTES and not route_file(dist, normalized).is_file():
                errors.append(f"{route}: internal link has no generated target: {href}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist", type=Path)
    args = parser.parse_args()
    errors = inspect_site(args.dist)
    if errors:
        print("Public site contract failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Public site contract passed for {len(EXPECTED_ROUTES)} routes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
