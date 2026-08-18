"""End-to-end parity for the additive P0/P1 MCP surface."""

import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select


async def _setup(client, preset="hybrid", scope="write"):
    from app.accounts.service import create_account
    from app.auth.service import create_api_token
    from app.database import get_db
    from app.projects.service import create_project

    suffix = uuid.uuid4().hex[:8]
    async for db in client.app.dependency_overrides[get_db]():
        account, owner = await create_account(
            db, f"p{suffix}", f"p{suffix}@test.cl", "P", "password"
        )
        project = await create_project(
            db, name=f"p-{suffix}", account_id=account.id, preset=preset
        )
        token, raw = await create_api_token(db, f"p-{suffix}", scope, owner.id)
        token.project_id = project.id
        await db.commit()
        return raw, project.id, account.id
    raise AssertionError("database dependency did not yield")


async def _call(client, raw, name, arguments):
    response = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers={"Authorization": f"Bearer {raw}"},
    )
    return response.json()["result"]


def _data(result):
    return json.loads(result["content"][0]["text"])


@pytest.mark.asyncio
async def test_core_item_update_comment_unlink_archive_and_reopen(client):
    raw, _pid, _account_id = await _setup(client)
    first = _data(await _call(client, raw, "pulsyr_create", {
        "title": "Parity item", "type": "feature", "area_name": "core",
        "impact_ai": 5, "effort_ai": "S",
    }))
    second = _data(await _call(client, raw, "pulsyr_create", {
        "title": "Related item", "type": "bug", "area_name": "core",
    }))
    updated = _data(await _call(client, raw, "pulsyr_item_update", {
        "item_id": first["id"], "title": "Parity item updated",
        "summary": "shared service", "priority": "p0", "impact_ai": None,
        "agent_ready": True,
    }))
    assert updated["title"] == "Parity item updated"
    assert updated["priority"] == "p0" and updated["impact_ai"] is None
    comment = _data(await _call(client, raw, "pulsyr_comment_add", {
        "item_id": first["id"], "body_md": "Decision receipt", "kind": "decision",
    }))
    assert comment["kind"] == "decision"
    detail = _data(await _call(client, raw, "pulsyr_item_get", {
        "item_id": first["id"], "include_graph": True,
    }))
    assert detail["comments"][0]["body_md"] == "Decision receipt"
    assert any(event["action"] == "item_updated" for event in detail["events"])

    await _call(client, raw, "pulsyr_link", {
        "source_id": first["id"], "target_id": second["id"], "relation": "related",
    })
    removed = _data(await _call(client, raw, "pulsyr_unlink", {
        "source_id": first["id"], "target_id": second["id"], "relation": "related",
    }))
    assert removed["deleted"] is True
    repeated = _data(await _call(client, raw, "pulsyr_unlink", {
        "source_id": first["id"], "target_id": second["id"], "relation": "related",
    }))
    assert repeated["deleted"] is False
    detail = _data(await _call(client, raw, "pulsyr_item_get", {"item_id": first["id"]}))
    assert any(event["action"] == "relationship_removed" for event in detail["events"])

    discarded = _data(await _call(client, raw, "pulsyr_discard", {
        "item_id": first["id"], "reason": "superseded",
    }))
    assert discarded["status"] == "discarded"
    week = datetime.now(timezone.utc).strftime("%G-W%V")
    archive = _data(await _call(client, raw, "pulsyr_archive_list", {
        "week": week, "status": "discarded", "limit": 10,
    }))
    archived = next(item for item in archive["items"] if item["id"] == first["id"])
    assert archived["reason"] == "superseded"
    reopened = _data(await _call(client, raw, "pulsyr_reopen", {"item_id": first["id"]}))
    assert reopened["status"] == "backlog"
    priority = _data(await _call(client, raw, "pulsyr_priority_view", {}))
    assert any(item["id"] == first["id"] for item in priority["unestimated"])


@pytest.mark.asyncio
async def test_thread_artifact_and_done_guard(client):
    raw, _pid, _account_id = await _setup(client, preset="product")
    thread = _data(await _call(client, raw, "pulsyr_thread_create", {
        "title": "Guarded thread", "area_name": "threads",
    }))
    artifact = _data(await _call(client, raw, "pulsyr_thread_artifact_add", {
        "thread_id": thread["id"], "kind": "research", "content": "Evidence",
    }))
    assert artifact["stage"] == "idea"
    item = _data(await _call(client, raw, "pulsyr_create", {
        "title": "Open linked", "type": "feature", "area_name": "threads",
        "thread_id": thread["id"],
    }))
    blocked = await _call(client, raw, "pulsyr_thread_set_stage", {
        "thread_id": thread["id"], "stage": "done",
    })
    assert blocked["isError"] is True
    assert _data(blocked)["error"]["code"] == "conflict"
    await _call(client, raw, "pulsyr_complete", {"item_id": item["id"], "note": "done"})
    done = _data(await _call(client, raw, "pulsyr_thread_set_stage", {
        "thread_id": thread["id"], "stage": "done",
    }))
    assert done["stage"] == "done"


@pytest.mark.asyncio
async def test_incident_transitions_promote_and_audit(client):
    from app.database import get_db
    from app.webhooks.models import SentryIssue, SentryIssueEvent

    raw, project_id, account_id = await _setup(client, preset="product")
    async for db in client.app.dependency_overrides[get_db]():
        issue = SentryIssue(
            sentry_issue_id=f"p{uuid.uuid4().hex[:8]}", project="api", title="Boom",
            level="error", status="new", events_count=1, payload={},
            project_id=project_id, account_id=account_id,
        )
        db.add(issue)
        await db.commit()
        await db.refresh(issue)
        issue_id = str(issue.id)
        break
    assert _data(await _call(client, raw, "pulsyr_incident_ignore", {
        "id": issue_id, "reason": "known noise",
    }))["status"] == "ignored"
    assert _data(await _call(client, raw, "pulsyr_incident_unignore", {
        "id": issue_id,
    }))["status"] == "new"
    promoted = _data(await _call(client, raw, "pulsyr_incident_promote", {
        "id": issue_id, "priority": "p0",
    }))
    repeated = _data(await _call(client, raw, "pulsyr_incident_promote", {
        "id": issue_id, "priority": "p0",
    }))
    assert promoted["item_id"] == repeated["item_id"]
    async for db in client.app.dependency_overrides[get_db]():
        events = list((await db.scalars(
            select(SentryIssueEvent)
            .where(SentryIssueEvent.issue_id == uuid.UUID(issue_id))
            .order_by(SentryIssueEvent.created_at)
        )).all())
        assert [event.action for event in events] == ["ignored", "restored", "promoted"]
        break


@pytest.mark.asyncio
async def test_management_new_tools_and_metadata_version_invariant(client):
    raw, _pid, _account_id = await _setup(client, preset="client")
    compartment = _data(await _call(client, raw, "pulsyr_compartment_upsert", {
        "name": "Contracts", "description": "Signed and draft contracts",
    }))
    document = _data(await _call(client, raw, "pulsyr_doc_put", {
        "compartment": "Contracts", "name": "MSA", "doc_type": "md", "content": "v1",
    }))
    await _call(client, raw, "pulsyr_doc_put", {
        "compartment": "Contracts", "name": "MSA", "doc_type": "md", "content": "v2",
    })
    updated = _data(await _call(client, raw, "pulsyr_doc_update", {
        "deliverable_id": document["id"], "summary_md": "metadata only", "status": "review",
        "compartment_id": compartment["id"],
    }))
    assert updated["current_version"] == 2 and updated["status"] == "review"
    rolled = _data(await _call(client, raw, "pulsyr_doc_rollback", {
        "deliverable_id": document["id"], "version_no": 1,
    }))
    assert rolled["current_version"] == 3 and rolled["rollback_of"] == 1
    pending = _data(await _call(client, raw, "pulsyr_pending_upsert", {"title": "Call client"}))
    deleted = _data(await _call(client, raw, "pulsyr_pending_delete", {
        "pending_id": pending["id"],
    }))
    repeated = _data(await _call(client, raw, "pulsyr_pending_delete", {
        "pending_id": pending["id"],
    }))
    assert deleted["deleted"] is True and repeated["deleted"] is False


@pytest.mark.asyncio
async def test_backfill_is_bounded_and_uses_server_side_connection(client, monkeypatch):
    from app.database import get_db
    from app.projects.models import Project
    from app.webhooks import service as webhooks
    from app.webhooks.models import SentryConnection

    raw, project_id, account_id = await _setup(client, preset="product")
    backfill_id = f"BF-{uuid.uuid4().hex[:8]}"
    async for db in client.app.dependency_overrides[get_db]():
        project = await db.get(Project, project_id)
        project.sentry_project_slug = "api"
        db.add(SentryConnection(
            account_id=account_id, webhook_token=uuid.uuid4().hex,
            api_token="server-secret", org_slug="org",
        ))
        await db.commit()
        break
    seen = {}

    async def fake_fetch(token, org, project, query, limit, base_url):
        seen.update({"token": token, "org": org, "project": project, "limit": limit})
        return [{"id": backfill_id, "title": "Backfilled"}, {"title": "missing id"}]

    monkeypatch.setattr(webhooks, "fetch_sentry_issues", fake_fetch)
    result = _data(await _call(client, raw, "pulsyr_incident_backfill", {
        "query": "is:unresolved", "limit": 1000,
    }))
    assert seen == {"token": "server-secret", "org": "org", "project": "api", "limit": 100}
    assert result["fetched"] == 2 and result["created"] == 1 and result["ignored"] == 1
    assert "server-secret" not in json.dumps(result)


@pytest.mark.asyncio
async def test_cross_project_lookup_and_safety_net_do_not_leak(client, monkeypatch):
    from app.database import get_db
    from app.items.models import Item, ItemRelationship
    from app.mcp.server import TOOLS

    raw_a, _pid_a, _ = await _setup(client)
    raw_b, _pid_b, _ = await _setup(client)
    item = _data(await _call(client, raw_a, "pulsyr_create", {
        "title": "Private title", "type": "feature", "area_name": "private",
    }))
    foreign = _data(await _call(client, raw_b, "pulsyr_create", {
        "title": "Foreign secret", "type": "feature", "area_name": "private",
    }))
    # Even a legacy/corrupt cross-project edge must not leak its other endpoint.
    async for db in client.app.dependency_overrides[get_db]():
        db.add(ItemRelationship(
            source_id=uuid.UUID(item["id"]),
            target_id=uuid.UUID(foreign["id"]),
            relation="related",
        ))
        await db.commit()
        break
    graph_detail = _data(await _call(client, raw_a, "pulsyr_item_get", {
        "item_id": item["id"], "include_graph": True,
    }))
    assert graph_detail["graph"]["arcs"] == []
    assert "Foreign secret" not in json.dumps(graph_detail)

    denied = await _call(client, raw_b, "pulsyr_item_get", {"item_id": item["id"]})
    assert denied["isError"] is True
    assert _data(denied)["error"]["code"] == "not_found"
    assert "Private title" not in denied["content"][0]["text"]

    original = TOOLS["pulsyr_item_update"].handler

    async def mutate_then_fail(db, token, args):
        row = await db.get(Item, uuid.UUID(args["item_id"]))
        row.title = "must rollback"
        await db.flush()
        raise RuntimeError("SQL SELECT secret-value")

    monkeypatch.setattr(TOOLS["pulsyr_item_update"], "handler", mutate_then_fail)
    failed = await _call(client, raw_a, "pulsyr_item_update", {"item_id": item["id"]})
    monkeypatch.setattr(TOOLS["pulsyr_item_update"], "handler", original)
    text = failed["content"][0]["text"]
    assert _data(failed)["error"]["code"] == "internal_error"
    assert "RuntimeError" not in text and "secret-value" not in text
    detail = _data(await _call(client, raw_a, "pulsyr_item_get", {"item_id": item["id"]}))
    assert detail["title"] == "Private title"
