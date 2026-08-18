"""Webhook logic: HMAC signature verification + Sentry ingest + Git progress."""

import asyncio
import hashlib
import hmac
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.items import service
from app.items.models import Item, ItemEvent
from app.scopes.service import resolve_scope
from app.webhooks.connection import DEFAULT_BASE_URL, effective_base_url, outbound
from app.webhooks.models import SentryIssue, SentryIssueEvent

logger = logging.getLogger("pulsyr.webhooks")

_TAG_RE = re.compile(r"<[^>]+>")
# ponytail: accepts the legacy `pulso:` prefix too — commit history is immutable and
# those references must keep auto-closing. Drop the alternation once no live repo uses it.
_PULSYR_RE = re.compile(r"(?:closes\s+)?puls(?:yr|o):([0-9a-fA-F-]{36})")


class IncidentTransitionError(ValueError):
    """Invalid reversible incident-container transition."""


def _issue_event(
    db: AsyncSession,
    issue: SentryIssue,
    actor: str,
    action: str,
    previous_status: str | None,
    *,
    note: str | None = None,
) -> None:
    db.add(SentryIssueEvent(
        project_id=issue.project_id,
        issue_id=issue.id,
        actor=actor[:255],
        action=action,
        previous_status=previous_status,
        status=issue.status,
        note=(note or "").strip()[:2000] or None,
    ))


async def _locked_issue(db: AsyncSession, issue: SentryIssue) -> SentryIssue:
    """Serialize reversible/container transitions and refresh any stale identity-map row."""
    locked = await db.scalar(
        select(SentryIssue)
        .where(SentryIssue.id == issue.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked is None:
        raise IncidentTransitionError("Incident not found.")
    return locked


def verify_sentry_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not secret or not header:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


def verify_github_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not secret or not header:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


def _sanitize(textval: str | None, limit: int = 4000) -> str:
    if not textval:
        return ""
    return _TAG_RE.sub("", textval)[:limit]


def _parse_dt(value: Any) -> datetime | None:
    """Parse an ISO timestamp from Sentry (with or without 'Z'). None if unparseable."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def parse_sentry_payload(payload: dict) -> dict[str, Any]:
    """Normalize the three inbound shapes (issue webhook / event_alert / legacy plugin)
    into a single dict. ValueError when there is no issue id (spec 2026-07-10 §4.3)."""
    data = payload.get("data") or {}
    issue = data.get("issue") or payload.get("issue")
    event = data.get("event")
    if issue:                       # Internal Integration, resource=issue
        src, sentry_id = issue, issue.get("id")
        proj = issue.get("project")
        slug = proj.get("slug") if isinstance(proj, dict) else (str(proj) if proj else None)
        web_url = issue.get("web_url") or issue.get("permalink")
    elif event:                     # Internal Integration, resource=event_alert
        src, sentry_id = event, event.get("issue_id")
        slug = None                 # alert payloads only carry a numeric project id
        web_url = event.get("web_url")
    else:                           # legacy per-project plugin (flat, unsigned)
        src, sentry_id = payload, payload.get("id")
        proj = payload.get("project")
        slug = str(proj) if proj else None
        web_url = payload.get("url")
    if not sentry_id:
        raise ValueError("Missing Sentry issue id")
    title = _sanitize(src.get("title") or src.get("culprit") or src.get("message")
                      or "Sentry issue", 500)
    level = src.get("level", "error")
    if level not in ("error", "warning", "info"):
        level = "error"
    try:
        count = int(str(src.get("count") or 1))
    except (TypeError, ValueError):
        count = 1
    return {"sentry_id": str(sentry_id), "title": title, "level": level,
            "slug": (slug or "")[:60] or None, "web_url": web_url, "count": count,
            "first_seen": _parse_dt(src.get("firstSeen")),
            "last_seen": _parse_dt(src.get("lastSeen"))}


async def ingest_sentry(
    db: AsyncSession,
    payload: dict,
    *,
    account_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
) -> dict:
    """Idempotent upsert into sentry_issues by sentry_issue_id (UNIQUE).

    Policy: the error lands in the sentry_issues CONTAINER (not in the backlog). Promotion
    to the backlog requires analysis: the AI triage does it (classifies noise) and/or the
    owner does it manually from /incidents. Dedup: the same issue increments events_count.
    account_id/project_id (v0017) are resolved by the tokened route; the legacy route lacks them.
    """
    parsed = parse_sentry_payload(payload)
    sentry_id = parsed["sentry_id"]

    issue = (await db.execute(
        select(SentryIssue).where(SentryIssue.sentry_issue_id == sentry_id)
    )).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    # The error's REAL timestamps in Sentry (not the ingest time).
    first_seen = parsed["first_seen"] or now
    last_seen = parsed["last_seen"] or now
    if issue is None:
        issue = SentryIssue(
            sentry_issue_id=sentry_id, project=parsed["slug"] or "unknown",
            title=parsed["title"], level=parsed["level"],
            status="new", events_count=parsed["count"],
            first_seen=first_seen, last_seen=last_seen,
            account_id=account_id, project_id=project_id,
            payload={"sanitized_title": parsed["title"], "web_url": parsed["web_url"]},
        )
        db.add(issue)
        created = True
        await db.flush()
        # Enqueue AI triage (pre-classifies noise; runs when ANTHROPIC_API_KEY is set).
        from app.jobs.worker import JobQueueFull, enqueue_job

        try:
            await enqueue_job(
                db,
                kind="triage-sentry",
                ref_type="sentry_issue",
                ref_id=issue.id,
                project_id=issue.project_id,
                commit=False,
            )
            triage_queued = True
        except JobQueueFull:
            # The webhook remains fast and durable under pressure. The incident is
            # preserved for manual triage instead of growing the async queue.
            triage_queued = False
    else:
        issue.events_count += 1
        issue.last_seen = last_seen
        # Heal old rows: if we now know the routing, attach them (dedup path).
        if issue.project_id is None and project_id is not None:
            issue.project_id = project_id
        if issue.account_id is None and account_id is not None:
            issue.account_id = account_id
        created = False
        triage_queued = False
    await db.flush()

    return {"sentry_issue_id": sentry_id, "created": created,
            "events_count": issue.events_count, "triage": issue.triage,
            "triage_queued": triage_queued, "status": issue.status}


async def promote_issue(
    db: AsyncSession, issue: SentryIssue, priority: str = "p1", actor: str = "manual"
) -> str:
    """Promote an issue from the container to the backlog as a bug item (an analyzed
    decision: AI triage or the owner). Idempotent: if already linked, returns the existing item."""
    issue = await _locked_issue(db, issue)
    if issue.item_id is not None:
        return str(issue.item_id)
    if priority not in ("p0", "p1", "p2", "p3"):
        raise IncidentTransitionError("priority must be one of: p0, p1, p2, p3.")
    old = issue.status
    scope = await resolve_scope(
        db, issue.project, create=True, source_repo="sentry", project_id=issue.project_id
    )
    item = Item(
        scope_id=scope.id, project_id=issue.project_id,
        title=issue.title[:300], type="bug", status="backlog",
        summary_md=_sanitize(str(issue.payload), 4000), origen="sentry",
        priority=priority, priority_declared=priority, stale_risk=True,
        source_refs={"sentry_issue_id": issue.sentry_issue_id},
    )
    db.add(item)
    await db.flush()
    issue.item_id = item.id
    issue.status = "linked"
    db.add(ItemEvent(item_id=item.id, actor=f"sentry:{issue.sentry_issue_id}",
                     action="created", payload={"from": "sentry", "priority": priority, "by": actor}))
    _issue_event(db, issue, actor, "promoted", old, note=f"priority={priority}")
    await db.flush()
    return str(item.id)


async def ignore_issue(
    db: AsyncSession,
    issue: SentryIssue,
    actor: str,
    reason: str | None = None,
) -> SentryIssue:
    issue = await _locked_issue(db, issue)
    if issue.status == "ignored":
        return issue
    if issue.status != "new":
        raise IncidentTransitionError(
            f"Invalid incident transition: {issue.status} → ignored"
        )
    old = issue.status
    issue.status = "ignored"
    _issue_event(db, issue, actor, "ignored", old, note=reason)
    await db.flush()
    return issue


async def unignore_issue(
    db: AsyncSession,
    issue: SentryIssue,
    actor: str,
) -> SentryIssue:
    issue = await _locked_issue(db, issue)
    if issue.status == "new":
        return issue
    if issue.status != "ignored":
        raise IncidentTransitionError(
            f"Invalid incident transition: {issue.status} → new"
        )
    old = issue.status
    issue.status = "new"
    _issue_event(db, issue, actor, "restored", old)
    await db.flush()
    return issue


async def resolve_issue(
    db: AsyncSession,
    issue: SentryIssue,
    *,
    in_sentry: bool,
    nota: str | None,
    actor: str,
    commit_sha: str | None = None,
) -> dict[str, Any]:
    """Resolve an incident: mark it resolved in Pulsyr, optionally in Sentry, and
    close the linked backlog item (if there is one and it is still open).

    Service logic extracted from the pulsyr_incidente_resolver MCP tool (ARCH-2) so that
    UI / REST / MCP consume it without duplication. Flush only; the edge does the commit.

    Returns {"id", "status", "resuelto_en_sentry", "item_cerrado"}.
    """
    previous_status = issue.status
    issue.status = "resolved"
    sentry_done = False
    if in_sentry:
        try:
            conn = await outbound(db, issue.account_id)
            sentry_done = await resolve_in_sentry(
                issue.sentry_issue_id,
                api_token=conn.api_token if conn else None,
                org_slug=conn.org_slug if conn else None,
                base_url=effective_base_url(conn) if conn else None,
            )
        except Exception as e:  # network / Sentry API error: must not block the local close
            logger.warning(
                "resolve_issue: failed to resolve %s in Sentry: %s",
                issue.sentry_issue_id, e,
            )
            sentry_done = False

    item_cerrado = False
    if issue.item_id is not None:
        item = await service.get_item(db, issue.item_id)
        if item is not None and item.status not in ("done", "discarded"):
            try:
                await service.close_item(
                    db, item, "done", nota or "resolved from incident",
                    actor, commit_sha=commit_sha,
                )
                item_cerrado = True
            except service.TransitionError as e:
                logger.warning(
                    "resolve_issue: could not close item %s linked to incident %s: %s",
                    issue.item_id, issue.id, e,
                )

    _issue_event(db, issue, actor, "resolved", previous_status, note=nota)
    await db.flush()
    return {
        "id": str(issue.id),
        "status": "resolved",
        "resuelto_en_sentry": sentry_done,
        "item_cerrado": item_cerrado,
    }


async def process_github_push(db: AsyncSession, payload: dict) -> dict:
    """Stamp last_touched_at per scope and auto-complete items referenced by pulsyr:UUID."""
    commits = payload.get("commits", [])
    touched_scopes: set[str] = set()
    completed: list[str] = []

    for commit in commits:
        msg = commit.get("message", "")
        sha = commit.get("id", "")
        # conventional-commit scope: fix(auth): -> auth
        m = re.match(r"^\w+\(([\w-]+)\)", msg)
        if m:
            touched_scopes.add(m.group(1))
        # pulsyr:UUID -> complete (idempotent, validated)
        for match in _PULSYR_RE.finditer(msg):
            item_id = match.group(1)
            done = await _complete_by_ref(db, item_id, sha)
            if done:
                completed.append(item_id)

    for scope_name in touched_scopes:
        await db.execute(text("""
            UPDATE items SET last_touched_at = now()
            WHERE scope_id = (SELECT id FROM scopes WHERE name = :name)
              AND status NOT IN ('done','discarded')
        """), {"name": scope_name})

    await db.flush()
    return {"touched_scopes": sorted(touched_scopes), "completed": completed}


async def fetch_sentry_issues(
    token: str, org: str, project: str, query: str = "is:unresolved", limit: int = 100,
    base_url: str = DEFAULT_BASE_URL,
) -> list[dict[str, Any]]:
    """Fetch the project's issues from the Sentry API (for historical backfill)."""
    url = f"{base_url}/api/0/projects/{org}/{project}/issues/"
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(
            url, params={"query": query, "limit": min(limit, 100)},
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        data = resp.json()
    return data if isinstance(data, list) else []


async def backfill_issues(
    db: AsyncSession, issues: list[dict], project: str, *,
    account_id: uuid.UUID | None = None, project_id: uuid.UUID | None = None,
) -> dict:
    """Ingest into the container every issue fetched from the Sentry API (dedup by id)."""
    created = 0
    updated = 0
    ignored = 0
    failures: list[dict[str, str]] = []
    for iss in issues:
        payload = {"data": {"issue": {
            "id": iss.get("id"),
            "title": iss.get("title") or iss.get("culprit") or "Sentry issue",
            "project": project,
            "level": iss.get("level", "error"),
            "web_url": iss.get("permalink"),
            "count": iss.get("count"),
            "firstSeen": iss.get("firstSeen"),
            "lastSeen": iss.get("lastSeen"),
        }}}
        try:
            result = await ingest_sentry(
                db, payload, account_id=account_id, project_id=project_id
            )
            if result["created"]:
                created += 1
            else:
                updated += 1
        except ValueError as exc:
            ignored += 1
            failures.append({"code": "invalid_issue", "message": str(exc)[:160]})
    return {
        "ingested": created + updated,
        "total": len(issues),
        "fetched": len(issues),
        "created": created,
        "updated": updated,
        "ignored": ignored,
        "failures": failures,
    }


def _format_stacktrace(event: dict, max_frames: int = 12) -> str:
    """Extract a readable summary (exception + in-app frames) from the Sentry event."""
    if not event:
        return "(no event)"
    lines: list[str] = []
    culprit = event.get("culprit")
    if culprit:
        lines.append(f"culprit: {culprit}")
    for entry in event.get("entries", []):
        if entry.get("type") != "exception":
            continue
        for val in entry.get("data", {}).get("values", []):
            etype = val.get("type", "")
            evalue = val.get("value", "")
            lines.append(f"\n{etype}: {evalue}")
            frames = (val.get("stacktrace") or {}).get("frames") or []
            # prioritize frames from our own code (in_app)
            in_app = [f for f in frames if f.get("inApp")] or frames
            for f in in_app[-max_frames:]:
                fn = f.get("filename") or f.get("module") or "?"
                ln = f.get("lineNo")
                func = f.get("function") or "?"
                lines.append(f"  {fn}:{ln} in {func}")
                ctx = f.get("context") or []
                for _, code in ctx:
                    if isinstance(code, str) and code.strip():
                        lines.append(f"      {code.strip()[:160]}")
    return "\n".join(lines)[:6000] if lines else "(no stack trace)"


async def fetch_issue_detail(
    issue_id: str, *, api_token: str | None = None, base_url: str | None = None
) -> dict[str, Any]:
    """Fetch metadata + stack trace of a Sentry issue's latest event (for the MCP).
    Without an explicit api_token it falls back to the legacy env-based mode (deprecated)."""
    token = api_token or settings.sentry_api_token
    if not token:
        raise RuntimeError("SENTRY_API_TOKEN not configured")
    base = base_url or DEFAULT_BASE_URL
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=60) as client:
        meta_r = await client.get(f"{base}/api/0/issues/{issue_id}/", headers=headers)
        meta_r.raise_for_status()
        meta = meta_r.json()
        ev_r = await client.get(
            f"{base}/api/0/issues/{issue_id}/events/latest/", headers=headers
        )
        event = ev_r.json() if ev_r.status_code == 200 else {}
    return {
        "title": meta.get("title"),
        "culprit": meta.get("culprit"),
        "level": meta.get("level"),
        "count": meta.get("count"),
        "first_seen": meta.get("firstSeen"),
        "last_seen": meta.get("lastSeen"),
        "permalink": meta.get("permalink"),
        "stacktrace": _format_stacktrace(event),
    }


async def resolve_in_sentry(
    issue_id: str, *, api_token: str | None = None,
    org_slug: str | None = None, base_url: str | None = None,
) -> bool:
    """Mark the issue as resolved in Sentry (requires a token with Issue&Event: Write).
    Uses the org-scoped endpoint (the documented one) when org_slug is set; without an
    org it falls back to the legacy /api/0/issues/{id}/. Without an explicit api_token
    it uses the global env (deprecated). Retries once on 429, honoring Retry-After (5s cap)."""
    token = api_token or settings.sentry_api_token
    if not token:
        return False
    base = base_url or DEFAULT_BASE_URL
    org = org_slug or settings.sentry_org
    url = (f"{base}/api/0/organizations/{org}/issues/{issue_id}/" if org
           else f"{base}/api/0/issues/{issue_id}/")
    headers = {"Authorization": f"Bearer {token}", "content-type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.put(url, headers=headers, json={"status": "resolved"})
        if r.status_code == 429:
            try:
                delay = min(float(r.headers.get("Retry-After", "1")), 5.0)
            except ValueError:
                delay = 1.0
            await asyncio.sleep(delay)
            r = await client.put(url, headers=headers, json={"status": "resolved"})
        return r.status_code in (200, 202)


async def _complete_by_ref(db: AsyncSession, item_id: str, sha: str) -> bool:
    try:
        item = await service.get_item(db, uuid.UUID(item_id))
    except (ValueError, AttributeError):
        logger.warning("_complete_by_ref: invalid item_id in commit %s: %r", sha[:12], item_id)
        return False
    if item is None:
        return False
    if item.status in ("done", "discarded"):
        return False  # idempotent: already closed
    try:
        await service.close_item(db, item, "done", f"closed by commit {sha[:12]}",
                                 f"github:{sha[:12]}", commit_sha=sha)
    except service.TransitionError as e:
        logger.warning("_complete_by_ref: invalid transition closing %s: %s", item_id, e)
        return False
    return True
