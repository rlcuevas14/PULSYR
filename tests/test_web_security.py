import json
from pathlib import Path

from fastapi import File, Form, Request, UploadFile
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.main import create_app
from app.templates_config import templates
from app.web_security import CSRF_COOKIE


async def test_security_headers_and_private_cache_cover_html_and_errors():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        html = await client.get("/docs", headers={"Accept": "text/html"})
        missing = await client.get("/missing-security-test", headers={"Accept": "text/html"})

    for response in (html, missing):
        assert response.headers["content-security-policy"].startswith("default-src 'self'")
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
        assert "camera=()" in response.headers["permissions-policy"]
        assert response.headers["cache-control"] == "private, no-store"


async def test_production_adds_conservative_hsts(monkeypatch):
    monkeypatch.setattr(settings, "debug", False)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        response = await client.get("/missing-hsts-test")

    assert response.headers["strict-transport-security"] == "max-age=31536000"
    assert "includeSubDomains" not in response.headers["strict-transport-security"]


async def test_hashed_assets_are_immutable_and_compressed():
    app = create_app()
    manifest = json.loads(Path("app/static/asset-manifest.json").read_text(encoding="utf-8"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        asset = await client.get(manifest["app.css"], headers={"Accept-Encoding": "gzip"})

    assert asset.status_code == 200
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert asset.headers["content-encoding"] == "gzip"
    assert asset.headers["x-content-type-options"] == "nosniff"


async def test_cookie_authenticated_post_without_csrf_is_rejected():
    app = create_app()

    @app.post("/__test__/csrf-form")
    async def csrf_form(value: str = Form(...)):
        return {"value": value}

    @app.post("/__test__/csrf-upload")
    async def csrf_upload(upload: UploadFile = File(...)):
        return {"filename": upload.filename}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.get("/docs")
        assert CSRF_COOKIE in first.cookies
        client.cookies.set("pulsyr_session", "browser-session")
        rejected = await client.post("/logout")
        token = client.cookies.get(CSRF_COOKIE)
        accepted = await client.post(
            "/__test__/csrf-form", data={"csrf_token": token, "value": "preserved"}
        )
        upload = await client.post(
            "/__test__/csrf-upload",
            data={"csrf_token": token},
            files={"upload": ("evidence.txt", b"ok", "text/plain")},
        )

    assert rejected.status_code == 403
    assert rejected.text == "CSRF validation failed"
    assert accepted.status_code == 200
    assert accepted.json() == {"value": "preserved"}
    assert upload.status_code == 200
    assert upload.json() == {"filename": "evidence.txt"}


async def test_csrf_token_is_available_to_forms_and_htmx():
    app = create_app()

    @app.get("/__test__/login-template")
    async def login_template(request: Request):
        return templates.TemplateResponse(request, "login.html", {"error": None})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/__test__/login-template")

    token = response.cookies[CSRF_COOKIE]
    assert f'<meta name="csrf-token" content="{token}">' in response.text
    assert "cdn.tailwindcss.com" not in response.text
    assert "fonts.googleapis.com" not in response.text
    assert "unpkg.com" not in response.text
    assert "/static/assets/htmx." in response.text
