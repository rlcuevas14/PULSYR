"""i18n invariants: catalog completeness, placeholder parity, template key coverage.

Deterministic gate (no rendering): parses every template for t()/tn() usage and
verifies the three catalogs stay in lockstep. This is what lets contributors add
languages by editing JSON only.
"""
import json
import re
from pathlib import Path

from jinja2 import Environment

from app.enums import ITEM_STATUSES, ITEM_TYPES, ORIGENES
from app.i18n import DEFAULT_LANG, LANGS, SUPPORTED, t, tn

LOCALES = Path("app/i18n/locales")
TEMPLATES = Path("app/templates")

CATALOGS = {code: json.loads((LOCALES / f"{code}.json").read_text(encoding="utf-8")) for code, _ in LANGS}

# Prefijos usados con clave dinámica en templates/router: t("status." ~ x), etc.
DYNAMIC_PREFIXES = {
    "status.": ITEM_STATUSES,
    "type.": ITEM_TYPES,
    "origin.": ORIGENES,
    "stage.": ["idea", "research", "stories", "spec",
               "in-development", "review", "done", "discarded"],
    "triage.": ["real-bug", "bad-input", "3rd-party", "noise"],
    "kind.": ["decision"],  # solo 'decision' se pinta con pill en item_detail
    "month.": [str(n) for n in range(1, 13)],
    "relation.": ["blocks", "requires", "conflicts", "related", "part_of"],
    "relation.in.": ["blocks", "requires", "conflicts", "related", "part_of"],
    "role.": ["owner", "member", "viewer", "editor"],
    "management.subtab.": ["documentos", "plan", "pendientes"],
    "management.pending.bucket.": ["overdue", "today", "upcoming", "none"],
    "management.pending.groupby.": ["status", "owner", "due", "none"],
    "pstatus.": ["open", "doing", "blocked", "done"],
    "dstatus.": ["draft", "review", "final", "archived"],
    "event.": ["created", "status_changed", "closed", "reopened",
               "priority_changed", "unblocked_by", "edited"],
}

_T_CALL = re.compile(r"""\bt(?:n)?\(\s*['"]([a-z0-9_.\-]+)['"]""")
_PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


def _template_keys() -> set[str]:
    keys: set[str] = set()
    for path in TEMPLATES.rglob("*.html"):
        for m in _T_CALL.finditer(path.read_text(encoding="utf-8")):
            keys.add(m.group(1))
    return keys


def _python_keys() -> set[str]:
    keys: set[str] = set()
    for path in Path("app").rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        for m in re.finditer(r"""_t\(\s*f?['"]([a-z0-9_.\-{}]+)['"]""", src):
            key = m.group(1)
            if "{" not in key:  # f-strings dinámicos se cubren por DYNAMIC_PREFIXES
                keys.add(key)
    return keys


def test_catalogs_have_identical_key_sets():
    base = set(CATALOGS[DEFAULT_LANG])
    for code in SUPPORTED:
        missing = base - set(CATALOGS[code])
        extra = set(CATALOGS[code]) - base
        assert not missing, f"{code}.json missing keys: {sorted(missing)[:10]}"
        assert not extra, f"{code}.json extra keys: {sorted(extra)[:10]}"


def test_every_template_key_exists_in_catalogs():
    used = _template_keys() | _python_keys()
    # tn() usa sufijos .one/.other — expandir
    catalog = set(CATALOGS[DEFAULT_LANG])
    missing = set()
    for key in used:
        if key.endswith("."):  # prefijo dinámico (t("status." ~ x)) — cubierto aparte
            continue
        if key in catalog or f"{key}.other" in catalog:
            continue
        missing.add(key)
    assert not missing, f"keys used but not in catalogs: {sorted(missing)}"


def test_dynamic_enum_labels_complete():
    catalog = set(CATALOGS[DEFAULT_LANG])
    for prefix, values in DYNAMIC_PREFIXES.items():
        for v in values:
            assert f"{prefix}{v}" in catalog, f"missing dynamic key {prefix}{v}"


def test_placeholders_match_across_languages():
    for key, en_val in CATALOGS[DEFAULT_LANG].items():
        expected = set(_PLACEHOLDER.findall(en_val))
        for code in SUPPORTED:
            got = set(_PLACEHOLDER.findall(CATALOGS[code][key]))
            assert got == expected, f"{code}:{key} placeholders {got} != en {expected}"


def test_all_templates_parse():
    env = Environment()  # noqa: S701 — solo parseo sintáctico, sin render
    for path in TEMPLATES.rglob("*.html"):
        env.parse(path.read_text(encoding="utf-8"), name=str(path))


def test_t_fallback_chain():
    assert t("nav.backlog", "fr") == "Backlog"
    assert t("nonexistent.key", "fr") == "nonexistent.key"  # never raises
    assert tn("common.items", 1, "es") == "1 ítem"
    assert tn("common.items", 3, "es") == "3 ítems"
    assert t("flash.project_active", "es", name="X") == "Proyecto activo: X"
