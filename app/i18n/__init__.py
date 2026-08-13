"""UI internationalization — JSON catalogs + request-scoped resolution.

Hand-rolled on purpose (same ethos as the MCP server): flat dot-namespaced keys in
``locales/{code}.json``, English as source of truth and fallback. No gettext/.po
toolchain — contributors edit JSON directly and CI verifies catalog completeness.

Python side: ``t(key, lang, **kwargs)`` / ``tn(key, n, lang)``.
Jinja side: globals ``t``/``tn``/``current_lang``/``LANGS`` (registered in
``templates_config.py`` via ``pass_context`` — the language comes from the request
session, default English).
"""

import json
from pathlib import Path
from typing import Any

from fastapi import Request

DEFAULT_LANG = "en"
# (code, native label) — order defines the selector menu.
LANGS: list[tuple[str, str]] = [("en", "English"), ("es", "Español")]
SUPPORTED = frozenset(code for code, _ in LANGS)

_SPANISH_COUNTRIES = frozenset(
    {
        "AR", "BO", "CL", "CO", "CR", "CU", "DO", "EC", "ES", "GQ",
        "GT", "HN", "MX", "NI", "PA", "PE", "PR", "PY", "SV", "UY", "VE",
    }
)

_DIR = Path(__file__).parent / "locales"
_catalogs: dict[str, dict[str, str]] = {
    code: json.loads((_DIR / f"{code}.json").read_text(encoding="utf-8"))
    for code in SUPPORTED
}


def resolve_lang(request: Request) -> str:
    """Session choice, then country at the edge, then browser preference."""
    lang = request.session.get("lang")
    if lang in SUPPORTED:
        return lang
    country = request.headers.get("cf-ipcountry", "").upper()
    if country:
        return "es" if country in _SPANISH_COUNTRIES else DEFAULT_LANG
    accepted = request.headers.get("accept-language", "").lower()
    return "es" if accepted.startswith("es") else DEFAULT_LANG


def t(key: str, lang: str = DEFAULT_LANG, **kwargs: Any) -> str:
    """Translate ``key``; falls back to English, then to the key itself (never raises)."""
    text = _catalogs.get(lang, {}).get(key) or _catalogs[DEFAULT_LANG].get(key) or key
    return text.format(**kwargs) if kwargs else text


def tn(key: str, n: int, lang: str = DEFAULT_LANG, **kwargs: Any) -> str:
    """Pluralized translate: picks ``{key}.one`` or ``{key}.other`` (en/es/fr rule)."""
    return t(f"{key}.{'one' if n == 1 else 'other'}", lang, n=n, **kwargs)
