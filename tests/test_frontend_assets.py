import json
import re
from pathlib import Path


def test_private_templates_have_no_runtime_cdn_dependencies():
    templates = Path("app/templates")
    # partials/_paddle_js.html is the one deliberate exception, checked on its
    # own below: everything else must still self-host its executable assets.
    paddle_js = templates / "partials" / "_paddle_js.html"
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in templates.rglob("*.html")
        if path != paddle_js
    )

    assert not re.search(r'<script[^>]+src=["\']https?://', source, re.IGNORECASE)
    assert not re.search(r'<link[^>]+href=["\']https?://', source, re.IGNORECASE)
    for forbidden in ("cdn.tailwindcss.com", "unpkg.com", "cdn.jsdelivr.net", "fonts.googleapis.com"):
        assert forbidden not in source


def test_paddle_js_partial_references_only_paddles_own_cdn():
    """partials/_paddle_js.html is exempt from the no-CDN rule above because
    Paddle's checkout script must be served from Paddle's own CDN: self-hosting
    a payment provider's script would pin it at a version Paddle updates for
    fraud handling and compliance, and Paddle's checkout only works when loaded
    from an origin Paddle itself serves. The CSP was widened for billing paths
    only (see app/web_security.py, _billing_csp/_PADDLE_SCRIPT) to match this
    one exception, not to permit CDNs generally. This test keeps the exception
    to exactly one external origin, so a future edit cannot quietly add a
    second CDN under cover of it.
    """
    paddle_js = Path("app/templates/partials/_paddle_js.html").read_text(encoding="utf-8")
    urls = re.findall(r'(?:src|href)=["\'](https?://[^"\']+)', paddle_js, re.IGNORECASE)

    assert urls
    assert all(url.startswith("https://cdn.paddle.com/") for url in urls)


def test_paddle_js_partial_is_gated_on_a_configured_token():
    """The one CDN script tag in the private app must not exist on an install
    with no Paddle token. The routes guard their own pages, but the partial
    carries the guard too, so including it from a future template cannot quietly
    reopen the hole."""
    paddle_js = Path("app/templates/partials/_paddle_js.html").read_text(encoding="utf-8")

    assert paddle_js.startswith("{% if paddle_token %}")
    assert paddle_js.rstrip().endswith("{% endif %}")


def test_private_templates_have_no_inline_executable_code():
    templates = Path("app/templates")
    source = "\n".join(path.read_text(encoding="utf-8") for path in templates.rglob("*.html"))

    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", source, re.IGNORECASE)
    assert not re.search(r"\son(?:click|change|input|submit|keydown)\s*=", source, re.IGNORECASE)


def test_asset_manifest_points_only_to_existing_content_hashed_files():
    static_root = Path("app/static")
    manifest = json.loads((static_root / "asset-manifest.json").read_text(encoding="utf-8"))

    assert set(manifest) == {"app.css", "app.js", "htmx.js", "sortable.js"}
    for url in manifest.values():
        assert re.fullmatch(r"/static/assets/[^/]+\.[a-f0-9]{12}\.(css|js)", url)
        assert (Path("app") / url.lstrip("/")).is_file()
    assert not list(static_root.rglob("*.map"))
    assert not (static_root / "src").exists()


def test_sortable_is_loaded_only_by_the_backlog_template():
    head = Path("app/templates/partials/_head.html").read_text(encoding="utf-8")
    backlog = Path("app/templates/backlog.html").read_text(encoding="utf-8")

    assert "sortable.js" not in head
    assert "asset_url('sortable.js')" in backlog


def test_stored_secrets_are_never_bound_to_password_values():
    integrations = Path("app/templates/account_integrations.html").read_text(encoding="utf-8")
    project_settings = Path("app/templates/projects_settings.html").read_text(encoding="utf-8")

    assert 'value="{{ conn.client_secret' not in integrations
    assert 'value="{{ conn.api_token' not in integrations
    assert 'value="{{ project.github_webhook_secret' not in project_settings
    assert "common.secret_configured" in integrations
    assert "common.secret_configured" in project_settings
