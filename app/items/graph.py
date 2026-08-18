"""Queries over the item relationship graph (item_relationships).

The graph is traversed in pure SQL (no recursion: depth is fixed = 2).
Blocking is DERIVED from the graph, not a materialized state.
"""

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import TERMINAL, sql_list

# Relations that count as hard dependencies for the topological order.
_PRECEDENCE = ("blocks", "requires")
# Reusable SQL fragment: terminal (closed) statuses, from enums (DUP-4).
_TERMINAL_SQL = sql_list(TERMINAL)


async def neighborhood(db: AsyncSession, scope_id: uuid.UUID) -> list[dict[str, Any]]:
    """Depth-2 neighborhood of a scope's open items.

    NON-recursive version (two hops) — correct and faster than a recursive CTE.
    Returns each item once, with its smallest depth (0 = seed, 1, 2).
    """
    result = await db.execute(
        text(f"""
            WITH edges AS (
                SELECT source_id AS a, target_id AS b FROM item_relationships
                UNION ALL
                SELECT target_id AS a, source_id AS b FROM item_relationships
            ),
            seed AS (
                SELECT id FROM items
                WHERE scope_id = :scope_id AND status NOT IN ({_TERMINAL_SQL})
            ),
            hop1 AS (SELECT DISTINCT e.b AS id, 1 AS depth FROM edges e JOIN seed s ON e.a = s.id),
            hop2 AS (SELECT DISTINCT e.b AS id, 2 AS depth FROM edges e JOIN hop1 h ON e.a = h.id)
            SELECT DISTINCT ON (i.id)
                   i.id, i.title, i.status, i.scope_id, i.impact_ai, i.effort_ai, d.depth
            FROM (SELECT id, 0 AS depth FROM seed
                  UNION ALL SELECT id, depth FROM hop1
                  UNION ALL SELECT id, depth FROM hop2) d
            JOIN items i ON i.id = d.id
            WHERE i.status NOT IN ({_TERMINAL_SQL})
            ORDER BY i.id, d.depth
        """),
        {"scope_id": str(scope_id)},
    )
    return [
        {
            "id": str(r["id"]),
            "title": r["title"],
            "status": r["status"],
            "scope_id": str(r["scope_id"]),
            "impact_ai": r["impact_ai"],
            "effort_ai": r["effort_ai"],
            "depth": r["depth"],
        }
        for r in result.mappings().all()
    ]


async def blockers_of(db: AsyncSession, item_id: uuid.UUID) -> list[dict[str, Any]]:
    """Items that EFFECTIVELY block item_id: incoming `blocks` arc whose source is open."""
    result = await db.execute(
        text(f"""
            SELECT s.id, s.title, s.status
            FROM item_relationships r
            JOIN items s ON s.id = r.source_id
            WHERE r.target_id = :item_id AND r.relation = 'blocks'
              AND s.status NOT IN ({_TERMINAL_SQL})
        """),
        {"item_id": str(item_id)},
    )
    return [{"id": str(r["id"]), "title": r["title"], "status": r["status"]} for r in result.mappings().all()]


async def unblocked_by(db: AsyncSession, item_id: uuid.UUID) -> list[dict[str, Any]]:
    """Targets that item_id blocks (outgoing `blocks` arc) left WITHOUT any other open blocker.

    Called after closing item_id to report what got unblocked (blocking is derived;
    no state is written on the target, it is only reported/audited).
    """
    result = await db.execute(
        text(f"""
            SELECT t.id, t.title
            FROM item_relationships r
            JOIN items t ON t.id = r.target_id
            WHERE r.source_id = :item_id AND r.relation = 'blocks'
              AND t.status NOT IN ({_TERMINAL_SQL})
              AND NOT EXISTS (
                  SELECT 1 FROM item_relationships r2
                  JOIN items s2 ON s2.id = r2.source_id
                  WHERE r2.target_id = t.id AND r2.relation = 'blocks'
                    AND r2.source_id <> :item_id
                    AND s2.status NOT IN ({_TERMINAL_SQL})
              )
        """),
        {"item_id": str(item_id)},
    )
    return [{"id": str(r["id"]), "title": r["title"]} for r in result.mappings().all()]


async def graph_blocked_ids(db: AsyncSession, project_id: uuid.UUID | None = None) -> set[str]:
    """Ids of items effectively blocked by the graph (for badges/filtering).

    With ``project_id`` it narrows to items blocked WITHIN that project (the dashboard
    and the backlog pass it so other accounts' graphs are not counted). Without it, global.
    """
    proj, params = "", {}
    if project_id is not None:
        proj = "AND t.project_id = :pid"
        params = {"pid": str(project_id)}
    result = await db.execute(
        text(f"""
            SELECT DISTINCT r.target_id AS id
            FROM item_relationships r
            JOIN items s ON s.id = r.source_id
            JOIN items t ON t.id = r.target_id
            WHERE r.relation = 'blocks'
              AND s.status NOT IN ({_TERMINAL_SQL})
              AND t.status NOT IN ({_TERMINAL_SQL})
              {proj}
        """),
        params,
    )
    return {str(r["id"]) for r in result.mappings().all()}


async def unblocker_ids(db: AsyncSession, project_id: uuid.UUID | None = None) -> set[str]:
    """Ids of items blocking others that are still open (🔓 badge = unblocker).

    With ``project_id`` it narrows to unblockers WITHIN that project.
    """
    proj, params = "", {}
    if project_id is not None:
        proj = "AND s.project_id = :pid"
        params = {"pid": str(project_id)}
    result = await db.execute(
        text(f"""
            SELECT DISTINCT r.source_id AS id
            FROM item_relationships r
            JOIN items s ON s.id = r.source_id
            JOIN items t ON t.id = r.target_id
            WHERE r.relation = 'blocks'
              AND s.status NOT IN ({_TERMINAL_SQL})
              AND t.status NOT IN ({_TERMINAL_SQL})
              {proj}
        """),
        params,
    )
    return {str(r["id"]) for r in result.mappings().all()}


async def subgraph(
    db: AsyncSession,
    item_id: uuid.UUID,
    project_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Subgraph centered on an item: incoming and outgoing arcs (both directions)."""
    project_filter = ""
    params: dict[str, str] = {"id": str(item_id)}
    if project_id is not None:
        project_filter = "AND si.project_id = :pid AND ti.project_id = :pid"
        params["pid"] = str(project_id)
    result = await db.execute(
        text(f"""
            SELECT r.source_id, r.target_id, r.relation, r.note,
                   si.title AS source_title, si.status AS source_status,
                   ti.title AS target_title, ti.status AS target_status
            FROM item_relationships r
            JOIN items si ON si.id = r.source_id
            JOIN items ti ON ti.id = r.target_id
            WHERE (r.source_id = :id OR r.target_id = :id)
            {project_filter}
        """),
        params,
    )
    arcs = [
        {
            "source_id": str(r["source_id"]),
            "target_id": str(r["target_id"]),
            "relation": r["relation"],
            "note": r["note"],
            "source_title": r["source_title"],
            "source_status": r["source_status"],
            "target_title": r["target_title"],
            "target_status": r["target_status"],
        }
        for r in result.mappings().all()
    ]
    return {"item_id": str(item_id), "arcs": arcs}


def topological_order(
    node_ids: list[str], edges: list[tuple[str, str, str]], impact: dict[str, int] | None = None
) -> dict[str, Any]:
    """Kahn's algorithm over the precedence DAG. Degrades gracefully on cycles.

    edges: list of (source, target, relation). Normalization:
        blocks   A->B  => precedence A->B (A before B)
        requires A->B  => precedence B->A (B before A)
        others         => ignored
    Returns {order: [...], has_cycle: bool, cycle_nodes: [...]}.
    Invariant: len(order) == len(node_ids) (no item is ever lost).
    """
    impact = impact or {}
    nodes = set(node_ids)
    # Build the precedence DAG.
    adj: dict[str, set[str]] = {n: set() for n in nodes}
    indeg: dict[str, int] = {n: 0 for n in nodes}
    for source, target, relation in edges:
        if relation == "blocks":
            a, b = source, target
        elif relation == "requires":
            a, b = target, source
        else:
            continue
        if a in nodes and b in nodes and b not in adj[a]:
            adj[a].add(b)
            indeg[b] += 1

    # Stable order: by descending impact, then by id.
    def _key(n: str) -> tuple[int, str]:
        return (-(impact.get(n) or 0), n)

    ready = sorted([n for n in nodes if indeg[n] == 0], key=_key)
    order: list[str] = []
    while ready:
        n = ready.pop(0)
        order.append(n)
        newly: list[str] = []
        for m in adj[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                newly.append(m)
        if newly:
            ready = sorted(ready + newly, key=_key)

    leftover = [n for n in node_ids if n not in set(order)]
    if leftover:
        leftover.sort(key=_key)
        order = order + leftover

    return {"order": order, "has_cycle": bool(leftover), "cycle_nodes": leftover}
