from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.main import create_app


async def test_app_origin_marks_html_and_errors_noindex():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        private_html = await client.get("/docs", follow_redirects=False)
        missing = await client.get("/definitely-missing-indexing-test")

    assert private_html.status_code == 200
    assert private_html.headers["content-type"].startswith("text/html")
    assert private_html.headers["x-robots-tag"] == "noindex, nofollow"
    assert missing.status_code == 404
    assert missing.headers["x-robots-tag"] == "noindex, nofollow"


async def test_app_origin_marks_api_responses_noindex():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/items")

    assert response.status_code == 401
    assert response.headers["x-robots-tag"] == "noindex, nofollow"


def test_production_disables_interactive_api_schema(monkeypatch):
    monkeypatch.setattr(settings, "debug", False)
    app = create_app()
    paths = {route.path for route in app.routes if hasattr(route, "path")}

    assert "/docs" not in paths
    assert "/redoc" not in paths
    assert "/openapi.json" not in paths


def test_debug_keeps_interactive_api_schema(monkeypatch):
    monkeypatch.setattr(settings, "debug", True)
    app = create_app()
    paths = {route.path for route in app.routes if hasattr(route, "path")}

    assert {"/docs", "/redoc", "/openapi.json"} <= paths
