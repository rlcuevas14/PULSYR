"""Thread service — CRUD, stage advancement, AI elaboration."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.items.models import Item
from app.scopes.models import Scope
from app.threads.models import THREAD_STAGES, Thread, ThreadArtifact, next_stage, prev_stage

_STAGE_KIND = {
    "research": "research",
    "stories": "stories",
    "spec": "spec",
    "in-development": "notes",
    "review": "notes",
}


class ThreadError(ValueError):
    pass


async def get_thread(db: AsyncSession, thread_id: uuid.UUID) -> Thread | None:
    return (await db.execute(select(Thread).where(Thread.id == thread_id))).scalar_one_or_none()


async def create_thread(
    db: AsyncSession,
    scope_name: str,
    title: str,
    summary: str | None,
    project_id: uuid.UUID | None = None,
) -> Thread:
    q = select(Scope).where(func.lower(Scope.name) == scope_name.lower())
    if project_id is not None:
        q = q.where(Scope.project_id == project_id)
    scope = (await db.execute(q)).scalar_one_or_none()
    if scope is None:
        scope = Scope(name=scope_name, source_repo="thread", project_id=project_id)
        db.add(scope)
        await db.flush()
    thread = Thread(
        scope_id=scope.id, title=title, summary_md=summary, stage="idea",
        project_id=project_id,
    )
    db.add(thread)
    await db.flush()
    return thread


async def add_artifact(
    db: AsyncSession, thread: Thread, kind: str, content: str,
    user_id: uuid.UUID | None = None, stage: str | None = None,
) -> ThreadArtifact:
    art = ThreadArtifact(
        thread_id=thread.id, stage=stage or thread.stage, kind=kind,
        content_md=content, created_by_user_id=user_id,
    )
    db.add(art)
    await db.flush()
    return art


async def _open_linked_items(db: AsyncSession, thread: Thread) -> int:
    n = await db.scalar(
        select(func.count()).select_from(Item).where(
            Item.thread_id == thread.id, Item.status.not_in(["done", "discarded"])
        )
    )
    return int(n or 0)


async def advance_stage(
    db: AsyncSession, thread: Thread, artifact_content: str | None = None,
    user_id: uuid.UUID | None = None,
) -> Thread:
    nxt = next_stage(thread.stage)
    if nxt is None:
        raise ThreadError(f"Stage '{thread.stage}' has no next stage.")
    if nxt == "done" and await _open_linked_items(db, thread) > 0:
        raise ThreadError("There are still open linked items — close them before marking the thread done.")
    if artifact_content:
        kind = _STAGE_KIND.get(thread.stage, "notes")
        await add_artifact(db, thread, kind, artifact_content, user_id)
    thread.stage = nxt
    await db.flush()
    return thread


async def set_stage(db: AsyncSession, thread: Thread, stage: str) -> Thread:
    if stage not in THREAD_STAGES:
        raise ThreadError(f"Invalid stage: {stage}")
    if stage == "done" and await _open_linked_items(db, thread) > 0:
        raise ThreadError("There are still open linked items — close them before marking the thread done.")
    thread.stage = stage
    await db.flush()
    return thread


def back_stage_value(stage: str) -> str | None:
    return prev_stage(stage)


async def list_threads(
    db: AsyncSession,
    stage: str | None = None,
    scope_name: str | None = None,
    project_id: uuid.UUID | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Thread]:
    q = select(Thread)
    if project_id is not None:
        q = q.where(Thread.project_id == project_id)
    if stage:
        q = q.where(Thread.stage == stage)
    if scope_name:
        scope_query = select(Scope).where(func.lower(Scope.name) == scope_name.lower())
        if project_id is not None:
            scope_query = scope_query.where(Scope.project_id == project_id)
        scope = (await db.execute(scope_query)).scalar_one_or_none()
        if scope:
            q = q.where(Thread.scope_id == scope.id)
    q = q.order_by(Thread.updated_at.desc(), Thread.id).offset(offset).limit(limit)
    return list((await db.execute(q)).scalars().all())


async def elaborate_next_stage(db: AsyncSession, thread: Thread) -> dict:
    from app.ai import llm

    nxt = next_stage(thread.stage)
    if nxt is None or nxt == "done":
        raise ThreadError("No next stage to elaborate.")
    arts = (await db.execute(
        select(ThreadArtifact).where(ThreadArtifact.thread_id == thread.id)
        .order_by(ThreadArtifact.created_at)
    )).scalars().all()
    arts_text = "\n\n".join(f"## {a.stage} ({a.kind})\n{a.content_md}" for a in arts)
    try:
        result = await llm.generate_stage(nxt, thread.title, thread.summary_md, arts_text)
    except llm.LLMUnavailable as e:
        raise ThreadError("AI unavailable (no ANTHROPIC_API_KEY).") from e
    return {"stage": nxt, "content": result["content"], "model": result["model"]}
