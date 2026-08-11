"""Worker job handlers. Sprint 1: real enrich (Haiku + embeddings)."""

import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import llm
from app.items.models import AiEnrichment, Item


async def handle_enrich(db: AsyncSession, ref_id: uuid.UUID | None) -> dict:
    """Enrich an item with impact/effort (Haiku) and an embedding (Gemini).

    Degrades gracefully: without ANTHROPIC_API_KEY it doesn't estimate; without
    GEMINI_API_KEY or pgvector it doesn't embed. Never breaks the worker.
    """
    if ref_id is None:
        return {"status": "no-ref"}

    item = (await db.execute(select(Item).where(Item.id == ref_id))).scalar_one_or_none()
    if item is None:
        return {"status": "item-not-found"}

    out: dict = {"item_id": str(ref_id)}

    # 1) Impact/effort estimation with Haiku.
    try:
        result = await llm.enrich_item(item.title, item.summary_md, item.type)
        item.impact_ai = result["impact"]
        item.effort_ai = result["effort"]
        item.impact_rationale = result["rationale"]
        db.add(AiEnrichment(
            item_id=item.id,
            model=result["model"],
            prompt_version="v1",
            effort=result["effort"],
            impact=result["impact"],
            rationale=result["rationale"],
            tokens_in=result.get("tokens_in"),
            tokens_out=result.get("tokens_out"),
        ))
        out["enriched"] = True
    except llm.LLMUnavailable:
        out["enriched"] = False
        out["note"] = "sin ANTHROPIC_API_KEY"

    # 2) Embedding (best-effort: requires GEMINI_API_KEY + pgvector).
    try:
        vec = await llm.embed_text(f"{item.title}\n{item.summary_md or ''}")
        if vec:
            await db.execute(
                text("UPDATE items SET embedding = CAST(:vec AS vector) WHERE id = :id"),
                {"vec": str(vec), "id": str(item.id)},
            )
            out["embedded"] = True
    except Exception as exc:  # pgvector missing or network error — don't break the job
        out["embedded"] = False
        out["embed_error"] = str(exc)[:120]

    await db.flush()
    return out


async def handle_triage_sentry(db: AsyncSession, ref_id: uuid.UUID | None) -> dict:
    """Pre-classify a Sentry issue with Haiku (bug-real / input-malo / 3rd-party / ruido).
    Noise auto-hides itself (status=ignored). Does NOT promote to the backlog — that decision
    belongs to the owner from /incidents (informed by the triage). Degrades without an API key
    (the issue stays untriaged)."""
    from app.webhooks.models import SentryIssue

    if ref_id is None:
        return {"status": "no-ref"}
    issue = (await db.execute(select(SentryIssue).where(SentryIssue.id == ref_id))).scalar_one_or_none()
    if issue is None:
        return {"status": "issue-not-found"}

    try:
        verdict = await llm.triage_sentry(issue.title, str(issue.payload or ""))
    except llm.LLMUnavailable:
        return {"status": "no-api-key", "note": "queda sin triage hasta tener ANTHROPIC_API_KEY"}

    issue.triage = verdict["triage"]
    # The triage classifies; it never writes to the backlog. Hiding noise is reversible
    # (the issue stays in the container, just filtered out); promoting is not. Promotion
    # is the owner's call from /incidents, or the agent's via pulsyr_incidents.
    if verdict["triage"] == "noise" and issue.item_id is None:
        issue.status = "ignored"  # auto-hide the noise from the container
    await db.flush()
    return {"issue_id": str(ref_id), "triage": verdict["triage"], "status": issue.status}


HANDLERS = {
    "enrich": handle_enrich,
    "triage-sentry": handle_triage_sentry,
}
