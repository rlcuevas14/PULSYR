import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi.templating import Jinja2Templates
from jinja2 import pass_context
from jinja2.runtime import Context
from markdown_it import MarkdownIt
from markupsafe import Markup

from app import i18n
from app.accounts.plans import FREE, limits_for
from app.config import settings
from app.items.lifecycle import allowed_targets, non_terminal_targets
from app.ui.navigation import navigation_context

templates = Jinja2Templates(directory="app/templates")

# Helpers disponibles en todas las plantillas.
templates.env.globals["non_terminal_targets"] = non_terminal_targets
templates.env.globals["allowed_targets"] = allowed_targets
templates.env.globals["base_url"] = settings.base_url
templates.env.globals["FREE_LIMITS"] = limits_for(FREE)
templates.env.globals["navigation_context"] = navigation_context


def billing_enabled() -> bool:
    """Read at render time, not at import time: a value frozen when the module
    loaded cannot follow a settings change, and every test that monkeypatches
    the key would be asserting against the value the process started with."""
    return bool(settings.paddle_api_key)


templates.env.globals["billing_enabled"] = billing_enabled


@lru_cache(maxsize=1)
def _asset_manifest() -> dict[str, str]:
    path = Path("app/static/asset-manifest.json")
    if not path.is_file():
        raise RuntimeError("Frontend assets are missing. Run: cd site && npm run build:app")
    return json.loads(path.read_text(encoding="utf-8"))


def asset_url(logical_name: str) -> str:
    try:
        return _asset_manifest()[logical_name]
    except KeyError as exc:
        raise RuntimeError(f"Unknown frontend asset: {logical_name}") from exc


templates.env.globals["asset_url"] = asset_url


# i18n: la lengua sale de la sesión del request (pass_context), default inglés.
# ctx.get: dentro de un macro importado SIN `with context` no hay request — degrada
# a inglés en vez de reventar el render (los imports de macros deben llevar
# `with context` para heredar el idioma).
def _lang_of(ctx: Context) -> str:
    request = ctx.get("request")
    return i18n.resolve_lang(request) if request is not None else i18n.DEFAULT_LANG


@pass_context
def _t(ctx: Context, key: str, **kwargs: Any) -> str:
    return i18n.t(key, _lang_of(ctx), **kwargs)


@pass_context
def _tn(ctx: Context, key: str, n: int, **kwargs: Any) -> str:
    return i18n.tn(key, n, _lang_of(ctx), **kwargs)


@pass_context
def _current_lang(ctx: Context) -> str:
    return _lang_of(ctx)


templates.env.globals["t"] = _t
templates.env.globals["tn"] = _tn
templates.env.globals["current_lang"] = _current_lang
templates.env.globals["LANGS"] = i18n.LANGS


def _fecha(value: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Formatea una fecha/hora. Tolera None y valores no-datetime."""
    if value is None:
        return "—"
    if isinstance(value, datetime):
        return value.strftime(fmt)
    return str(value)


# Filtro Jinja `fecha`: {{ item.created_at | fecha }} → '2026-06-15 14:30'.
templates.env.filters["fecha"] = _fecha


# html=False is the XSS boundary: raw HTML in user/agent markdown is escaped, never
# emitted. breaks=True keeps the single-newline behavior people expect from the old
# whitespace-pre-wrap rendering.
_md = MarkdownIt("commonmark", {"html": False, "breaks": True})


def _render_md(text: str | None) -> Markup:
    tokens = _md.parse(text or "")
    # A document already has its page-level h1. User/agent Markdown is nested
    # content, so top-level Markdown headings start at h2 instead of creating a
    # second document title.
    for token in tokens:
        if token.tag == "h1":
            token.tag = "h2"
    return Markup(_md.renderer.render(tokens, _md.options, {}))


# Jinja filter `md`: {{ item.summary_md | md }} → sanitized HTML (pair with .p-md styles).
templates.env.filters["md"] = _render_md


# Paleta de presets para el color de proyecto (indigo default + paleta de marca).
BRAND_PRESETS = ["#6366f1", "#ff4d8b", "#1a3a3a", "#b8a4ed", "#ffb084", "#e8b94a", "#ff6b5a", "#a4d4c5"]


def accent_fg(color: str | None) -> str:
    """Foreground (ink/white) legible sobre el color de acento elegido libremente.

    Umbral perceptual 0.35 sobre luminancia relativa WCAG: reproduce las elecciones
    de texto por tarjeta del design template (blanco sobre teal/coral/pink/indigo,
    tinta sobre ochre/peach/lavender/mint). Valores no parseables → blanco (default indigo).
    """
    c = (color or "#6366f1").lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    try:
        r, g, b = (int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except (ValueError, IndexError):
        return "#ffffff"

    def _lin(x: float) -> float:
        return x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4

    lum = 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)
    return "#0a0a0a" if lum > 0.35 else "#ffffff"


templates.env.globals["accent_fg"] = accent_fg
templates.env.globals["BRAND_PRESETS"] = BRAND_PRESETS
