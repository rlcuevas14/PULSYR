"""Búsqueda full-text única sobre items (ts_rank / websearch_to_tsquery 'spanish').

Una sola implementación del FTS, consumida por REST (/items/search), el MCP
(pulsyr_search) y la resolución por texto del grafo (relationships.resolve_query).

Usa `websearch_to_tsquery` (no `plainto_tsquery`): acepta la sintaxis de buscador
web — comillas para frases exactas, `OR` entre términos, y `-término` para
excluir. `plainto_tsquery` hacía AND estricto de todos los términos (una consulta
de 6 palabras no encontraba nada si faltaba una). Ambas toleran texto arbitrario
del usuario sin lanzar excepción; el cambio solo relaja el matching.
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_MAX_SEARCH_RESULTS = 200


async def search_items(
    db: AsyncSession,
    q: str,
    *,
    limit: int = 50,
    with_scope: bool = False,
    project_id: Any = None,
) -> list[dict[str, Any]]:
    """Busca ítems por full-text en español, ordenados por rank descendente.

    Devuelve dicts con: id, title, summary_md, type, status, scope_id, effort_ai,
    impact_ai, stale_risk y rank. Si `with_scope=True`, incluye además `scope`
    (nombre del scope, vía JOIN). Los ids vienen como str.

    Dedup: la query agrupa por ítem (un ítem aparece una vez) y ordena por rank, id.
    """
    # Account isolation: when a project is given, restrict the FTS to that project.
    pclause_j = "AND i.project_id = :pid" if project_id is not None else ""
    pclause = "AND project_id = :pid" if project_id is not None else ""
    if with_scope:
        sql = f"""
            SELECT i.id, i.title, i.summary_md, i.type, i.status, i.scope_id,
                   s.name AS scope, i.effort_ai, i.impact_ai, i.stale_risk,
                   ts_rank(i.search_vector, websearch_to_tsquery('spanish', :q)) AS rank
            FROM items i JOIN scopes s ON s.id = i.scope_id
            WHERE i.search_vector @@ websearch_to_tsquery('spanish', :q) {pclause_j}
            ORDER BY rank DESC, i.id
            LIMIT :limit
        """
    else:
        sql = f"""
            SELECT id, title, summary_md, type, status, scope_id,
                   effort_ai, impact_ai, stale_risk,
                   ts_rank(search_vector, websearch_to_tsquery('spanish', :q)) AS rank
            FROM items
            WHERE search_vector @@ websearch_to_tsquery('spanish', :q) {pclause}
            ORDER BY rank DESC, id
            LIMIT :limit
        """
    params: dict[str, Any] = {"q": q, "limit": min(max(int(limit), 1), _MAX_SEARCH_RESULTS)}
    if project_id is not None:
        params["pid"] = project_id
    rows = (await db.execute(text(sql), params)).mappings().all()
    out: list[dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        row["id"] = str(row["id"])
        row["scope_id"] = str(row["scope_id"]) if row.get("scope_id") else None
        row["rank"] = float(row["rank"]) if row.get("rank") is not None else 0.0
        out.append(row)
    return out
