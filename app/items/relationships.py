"""Graph arc management: creation with text-based resolution + guards, plus deletion.

Shared by the REST API, the UI and the MCP. Resolves text queries to items via
full-text search (through app.items.search); aborts on ambiguity (rank tie) instead of
guessing, and flags low confidence when the margin between top-1 and top-2 is narrow.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import RELATIONS
from app.items.models import ItemEvent, ItemRelationship
from app.items.search import search_items

_SYMMETRIC = ("conflicts", "related")
# Minimum relative margin between top-1 and top-2 to consider the resolution "reliable".
# If (rank0 - rank1) / rank0 < this threshold, the match is low-confidence.
_LOW_CONFIDENCE_MARGIN = 0.15


class RelationshipError(ValueError):
    """Business error when creating/deleting an arc (not found, ambiguous, self-loop, duplicate)."""


async def resolve_query_verbose(
    db: AsyncSession, query: str, project_id: uuid.UUID | None = None
) -> dict:
    """Resolve a text query to an item via full-text search, with confidence metadata.

    Returns {"id": uuid, "title": str, "low_confidence": bool, "margin": float}.
    Aborts (RelationshipError) if there are no results or if the top-2 ties exactly.
    `low_confidence=True` when the relative margin between top-1 and top-2 is narrow
    (the match is plausible but not clear-cut) — the caller may warn the user.
    """
    rows = await search_items(db, query, limit=2, project_id=project_id)
    if not rows:
        raise RelationshipError(f"No item found for «{query}».")

    rank0 = rows[0]["rank"]
    low_confidence = False
    margin = 1.0
    if len(rows) == 2:
        rank1 = rows[1]["rank"]
        if rank0 == rank1:
            raise RelationshipError(
                f"«{query}» is ambiguous (multiple items with the same rank). Name the exact item."
            )
        margin = (rank0 - rank1) / rank0 if rank0 else 0.0
        low_confidence = margin < _LOW_CONFIDENCE_MARGIN

    return {
        "id": uuid.UUID(rows[0]["id"]),
        "title": rows[0]["title"],
        "low_confidence": low_confidence,
        "margin": margin,
    }


async def resolve_query(
    db: AsyncSession, query: str, project_id: uuid.UUID | None = None
) -> uuid.UUID:
    """Resolve a text query to an item_id via full-text search. Aborts on a rank tie in the top-2.

    Compatibility: returns only the id (as before). To warn about low confidence,
    use `resolve_query_verbose`.
    """
    return (await resolve_query_verbose(db, query, project_id=project_id))["id"]


async def create_relationship(
    db: AsyncSession,
    source_id: uuid.UUID,
    target_id: uuid.UUID,
    relation: str,
    note: str | None = None,
) -> ItemRelationship:
    if relation not in RELATIONS:
        raise RelationshipError(
            f"Invalid relation '{relation}'. Use one of: {', '.join(RELATIONS)}."
        )
    if source_id == target_id:
        raise RelationshipError("An item cannot relate to itself.")

    # For symmetric arcs, normalize the order so (A,B) and (B,A) don't get duplicated.
    s, t = source_id, target_id
    if relation in _SYMMETRIC and str(source_id) > str(target_id):
        s, t = target_id, source_id

    # Idempotency: if it already exists, return the existing one (no 500 from a duplicate PK).
    existing = await db.get(ItemRelationship, {"source_id": s, "target_id": t, "relation": relation})
    if existing is not None:
        return existing

    rel = ItemRelationship(source_id=s, target_id=t, relation=relation, note=note)
    db.add(rel)
    await db.flush()
    return rel


async def delete_relationship(
    db: AsyncSession,
    source_id: uuid.UUID,
    target_id: uuid.UUID,
    relation: str,
    actor: str | None = None,
) -> bool:
    rel = await db.get(
        ItemRelationship, {"source_id": source_id, "target_id": target_id, "relation": relation}
    )
    if rel is None:
        # For symmetric relations, try the reversed order.
        if relation in _SYMMETRIC:
            rel = await db.get(
                ItemRelationship,
                {"source_id": target_id, "target_id": source_id, "relation": relation},
            )
    if rel is None:
        return False
    if actor:
        db.add(ItemEvent(
            item_id=source_id,
            actor=actor,
            action="relationship_removed",
            payload={"target_id": str(target_id), "relation": rel.relation},
        ))
    await db.delete(rel)
    await db.flush()
    return True
