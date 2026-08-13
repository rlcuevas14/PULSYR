import json
import struct
from pathlib import Path

from scripts.check_public_site import (
    EXPECTED_ROUTES,
    DocumentContract,
    inspect_site,
    localized_route,
    route_file,
)


def _document(route: str, title: str, description: str, *, robots: str = "index,follow") -> str:
    canonical = f"https://pulsyr.dev{route}"
    locale = "es" if route == "/es/" or route.startswith("/es/") else "en"
    english = f"https://pulsyr.dev{localized_route(route, 'en')}"
    spanish = f"https://pulsyr.dev{localized_route(route, 'es')}"
    social_alt = (
        "Pulsyr — El backlog que tu agente mantiene"
        if locale == "es"
        else "Pulsyr — The backlog your agent maintains"
    )
    schema_types = (
        ["Organization", "WebSite"]
        if route in ("/", "/es/")
        else ["SoftwareApplication", "BreadcrumbList"]
        if route in ("/producto/", "/es/producto/")
        else ["BreadcrumbList"]
    )
    schema = json.dumps(
        {"@context": "https://schema.org", "@graph": [{"@type": kind} for kind in schema_types]}
    )
    return f"""<!doctype html><html lang="{locale}"><head>
    <title>{title}</title><meta name="description" content="{description}">
    <meta name="robots" content="{robots}"><link rel="canonical" href="{canonical}">
    <link rel="alternate" hreflang="en" href="{english}">
    <link rel="alternate" hreflang="es" href="{spanish}">
    <link rel="alternate" hreflang="x-default" href="{english}">
    <meta property="og:title" content="{title}"><meta property="og:description" content="{description}">
    <meta property="og:type" content="website"><meta property="og:url" content="{canonical}">
    <meta property="og:image" content="https://pulsyr.dev/og/pulsyr-social.png">
    <meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">
    <meta property="og:image:alt" content="{social_alt}">
    <meta property="og:locale" content="{'es_ES' if locale == 'es' else 'en_US'}">
    <meta property="og:locale:alternate" content="{'en_US' if locale == 'es' else 'es_ES'}">
    <meta name="twitter:card" content="summary_large_image"><meta name="twitter:title" content="{title}">
    <meta name="twitter:description" content="{description}">
    <meta name="twitter:image" content="https://pulsyr.dev/og/pulsyr-social.png">
    <meta name="twitter:image:alt" content="{social_alt}">
    <script type="application/ld+json">{schema}</script></head><body>
    <nav><a href="/producto/">Product</a><a href="https://app.pulsyr.dev/login">App</a></nav>
    <main><h1>Heading</h1></main></body></html>"""


def _write_support_files(dist: Path) -> None:
    sitemap_urls = "".join(
        f"<url><loc>https://pulsyr.dev{route}</loc></url>" for route in EXPECTED_ROUTES
    )
    (dist / "sitemap.xml").write_text(
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{sitemap_urls}</urlset>',
        encoding="utf-8",
    )
    (dist / "robots.txt").write_text(
        "User-agent: *\nAllow: /\nSitemap: https://pulsyr.dev/sitemap.xml\n", encoding="utf-8"
    )
    (dist / "llms.txt").write_text(
        "The linked pages and repository are authoritative.", encoding="utf-8"
    )
    og = dist / "og" / "pulsyr-social.png"
    og.parent.mkdir()
    og.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + struct.pack(">II", 1200, 630))
    for route in ("/404/", "/500/"):
        path = route_file(dist, route)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '<html lang="en"><head><meta name="robots" content="noindex,follow"></head>'
            "<body><h1>Error</h1></body></html>",
            encoding="utf-8",
        )


def test_document_contract_extracts_rendered_fields():
    parser = DocumentContract()
    parser.feed(
        _document(
            "/",
            "Pulsyr — The backlog your agent maintains",
            "A sufficiently long and page-specific description for the public document.",
        )
    )

    assert parser.lang == "en"
    assert parser.h1_count == 1
    assert parser.canonical == "https://pulsyr.dev/"
    assert parser.hreflang == {
        "en": "https://pulsyr.dev/",
        "es": "https://pulsyr.dev/es/",
        "x-default": "https://pulsyr.dev/",
    }
    assert parser.executable_scripts == []
    assert len(parser.json_ld_sources) == 1


def test_site_contract_reports_missing_routes(tmp_path: Path):
    errors = inspect_site(tmp_path)
    missing_routes = [error for error in errors if "missing generated file" in error]
    assert len(missing_routes) == len(EXPECTED_ROUTES)


def test_site_contract_accepts_complete_static_fixture(tmp_path: Path):
    for index, route in enumerate(EXPECTED_ROUTES):
        path = route_file(tmp_path, route)
        path.parent.mkdir(parents=True, exist_ok=True)
        description = (
            f"This is a distinct public description number {index} with enough detail for validation."
        )
        path.write_text(_document(route, f"Page {index}", description), encoding="utf-8")
    _write_support_files(tmp_path)

    assert inspect_site(tmp_path) == []


def test_site_contract_rejects_undeclared_html(tmp_path: Path):
    for index, route in enumerate(EXPECTED_ROUTES):
        path = route_file(tmp_path, route)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _document(
                route,
                f"Page {index}",
                f"A unique and sufficiently detailed description for declared page number {index}.",
            ),
            encoding="utf-8",
        )
    _write_support_files(tmp_path)
    extra = tmp_path / "forgotten" / "index.html"
    extra.parent.mkdir()
    extra.write_text("<html><body><h1>Forgotten</h1></body></html>", encoding="utf-8")

    errors = inspect_site(tmp_path)
    assert any("undeclared HTML documents" in error for error in errors)
