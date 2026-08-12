from pathlib import Path

from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.templates_config import _render_md


async def test_browser_404_is_branded_noindex_html_with_request_id():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/missing-browser-page", headers={"Accept": "text/html"})

    request_id = response.headers["x-request-id"]
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    assert len(request_id) == 32
    assert "<h1>Page not found</h1>" in response.text
    assert f"Request ID: {request_id}" in response.text
    assert '<meta name="robots" content="noindex,nofollow">' in response.text


async def test_api_404_keeps_json_even_when_html_is_accepted():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/missing", headers={"Accept": "text/html"})

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"detail": "Not Found"}
    assert len(response.headers["x-request-id"]) == 32


async def test_browser_500_is_branded_and_does_not_leak_exception():
    app = create_app()

    @app.get("/__test__/unhandled-error")
    async def unhandled_error():
        raise RuntimeError("private database detail")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/__test__/unhandled-error", headers={"Accept": "text/html"})

    request_id = response.headers["x-request-id"]
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    assert "<h1>Temporary error</h1>" in response.text
    assert f"Request ID: {request_id}" in response.text
    assert "private database detail" not in response.text


def test_nested_markdown_demotes_h1_to_h2():
    rendered = str(_render_md("# Agent heading\n\n## Existing section"))

    assert "<h1>" not in rendered
    assert rendered.count("<h2>") == 2


def test_project_settings_has_a_page_heading():
    source = Path("app/templates/projects_settings.html").read_text(encoding="utf-8")

    assert source.count("<h1") == 1
