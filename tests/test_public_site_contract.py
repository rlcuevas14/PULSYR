from pathlib import Path

from scripts.check_public_site import EXPECTED_ROUTES, DocumentContract, inspect_site, route_file


def _document(title: str, description: str, *, h1: str = "Heading") -> str:
    return f"""<!doctype html><html lang="en"><head>
    <title>{title}</title><meta name="description" content="{description}">
    <meta name="robots" content="index,follow"></head><body>
    <nav><a href="/producto/">Product</a><a href="https://app.pulsyr.dev/login">App</a></nav>
    <main><h1>{h1}</h1></main></body></html>"""


def test_document_contract_extracts_rendered_fields():
    parser = DocumentContract()
    parser.feed(
        _document("Title", "A sufficiently long and page-specific description for the public document.")
    )

    assert parser.lang == "en"
    assert parser.h1_count == 1
    assert parser.title == "Title"
    assert parser.robots == "index,follow"
    assert parser.scripts == []


def test_site_contract_reports_missing_routes(tmp_path: Path):
    errors = inspect_site(tmp_path)
    assert len(errors) == len(EXPECTED_ROUTES)
    assert all("missing generated file" in error for error in errors)


def test_site_contract_accepts_complete_static_fixture(tmp_path: Path):
    for index, route in enumerate(EXPECTED_ROUTES):
        path = route_file(tmp_path, route)
        path.parent.mkdir(parents=True, exist_ok=True)
        description = (
            f"This is a distinct public description number {index} with enough detail for validation."
        )
        path.write_text(_document(f"Page {index}", description), encoding="utf-8")

    assert inspect_site(tmp_path) == []
