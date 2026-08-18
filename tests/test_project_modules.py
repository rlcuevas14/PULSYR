import json
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, func, select

from app.database import get_db
from app.projects.models import Project, ProjectMember, ProjectModule, ProjectModuleEvent
from app.projects.modules import (
    PRESETS,
    ModuleConfigurationError,
    apply_preset,
    enabled_modules,
    infer_preset,
    module_states,
    set_module_enabled,
    states_for_preset,
)


async def _owner_and_project(db, *, preset: str = "solo"):
    from app.accounts.service import create_account
    from app.projects.service import create_project

    suffix = uuid.uuid4().hex[:8]
    account, owner = await create_account(
        db,
        f"modules-{suffix}",
        f"modules-{suffix}@test.dev",
        "Module Owner",
        "password123",
    )
    project = await create_project(
        db,
        name=f"modules-{suffix}",
        account_id=account.id,
        preset=preset,
        actor=owner.email,
    )
    await db.commit()
    return owner, project


async def _login_solo(client: AsyncClient):
    from app.auth.service import create_user

    suffix = uuid.uuid4().hex[:8]
    email = f"modules-ui-{suffix}@test.dev"
    async for db in client.app.dependency_overrides[get_db]():
        user = await create_user(db, email, "Module Owner", "password123", "admin")
        project = await db.scalar(select(Project).where(Project.account_id == user.account_id))
        user_id, project_id, slug = user.id, project.id, project.slug
        break
    response = await client.post(
        "/login",
        data={"email": email, "password": "password123"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return user_id, project_id, slug, email


def test_presets_and_inference_are_closed_and_reproducible():
    assert PRESETS == ("solo", "product", "client", "hybrid")
    assert states_for_preset("solo") == {
        "threads": False,
        "incidents": False,
        "management": False,
    }
    assert infer_preset(states_for_preset("product")) == "product"
    assert infer_preset(states_for_preset("client")) == "client"
    assert infer_preset(states_for_preset("hybrid")) == "hybrid"
    assert infer_preset({"threads": True, "incidents": False, "management": False}) == "custom"
    with pytest.raises(ValueError):
        states_for_preset("agency")


@pytest.mark.asyncio
async def test_new_project_has_three_rows_and_core_is_always_effective(db):
    _owner, project = await _owner_and_project(db)

    states = await module_states(db, project.id)
    assert states == states_for_preset("solo")
    assert await enabled_modules(db, project.id) == frozenset({"core"})
    assert await db.scalar(
        select(func.count()).select_from(ProjectModule).where(
            ProjectModule.project_id == project.id
        )
    ) == 3


@pytest.mark.asyncio
async def test_changes_are_audited_and_idempotent(db):
    owner, project = await _owner_and_project(db)

    assert await set_module_enabled(
        db, project.id, "threads", True, owner.email
    ) is True
    assert await set_module_enabled(
        db, project.id, "threads", True, owner.email
    ) is False
    assert await db.scalar(
        select(func.count()).select_from(ProjectModuleEvent).where(
            ProjectModuleEvent.project_id == project.id
        )
    ) == 1

    await apply_preset(db, project.id, "hybrid", owner.email)
    await db.commit()
    events = list((await db.scalars(
        select(ProjectModuleEvent)
        .where(ProjectModuleEvent.project_id == project.id)
        .order_by(ProjectModuleEvent.created_at, ProjectModuleEvent.module)
    )).all())
    assert len(events) == 3
    assert events[0].previous_enabled is False and events[0].enabled is True
    assert {event.source for event in events} == {"manual", "preset"}
    assert await enabled_modules(db, project.id) == frozenset(
        {"core", "threads", "incidents", "management"}
    )


@pytest.mark.asyncio
async def test_incomplete_configuration_fails_explicitly(db):
    _owner, project = await _owner_and_project(db, preset="hybrid")
    await db.execute(delete(ProjectModule).where(
        ProjectModule.project_id == project.id,
        ProjectModule.module == "management",
    ))
    await db.flush()

    with pytest.raises(ModuleConfigurationError):
        await module_states(db, project.id)


@pytest.mark.asyncio
async def test_solo_navigation_and_every_transport_guard(client: AsyncClient):
    _uid, _pid, slug, _email = await _login_solo(client)

    home = await client.get("/", headers={"Accept": "text/html"})
    assert home.status_code == 200
    assert 'href="/threads"' not in home.text
    assert 'href="/incidents"' not in home.text
    assert 'href="/management"' not in home.text

    page = await client.get("/threads", headers={"Accept": "text/html"})
    assert page.status_code == 403
    assert f"/projects/{slug}/settings" in page.text
    rest = await client.get("/api/v1/threads")
    assert rest.status_code == 403
    assert rest.json()["code"] == "module_disabled"
    action = await client.post(f"/ui/incidents/{uuid.uuid4()}/promote")
    assert action.status_code == 403
    download = await client.get(
        f"/management/documentos/{uuid.uuid4()}/download",
        headers={"Accept": "text/html"},
    )
    assert download.status_code == 403


@pytest.mark.asyncio
async def test_owner_applies_preset_and_data_survives_disable_reenable(client: AsyncClient):
    _uid, project_id, slug, _email = await _login_solo(client)
    applied = await client.post(
        f"/projects/{slug}/settings/modules/preset",
        data={"preset": "product"},
        follow_redirects=False,
    )
    assert applied.status_code == 303

    created = await client.post(
        "/api/v1/threads",
        json={"scope_name": "General", "title": "Preserved thread"},
    )
    assert created.status_code == 201
    thread_id = uuid.UUID(created.json()["id"])
    disabled = await client.post(
        f"/projects/{slug}/settings/modules/threads",
        data={"enabled": "false"},
        follow_redirects=False,
    )
    assert disabled.status_code == 303
    assert (await client.get("/api/v1/threads")).status_code == 403

    from app.threads.models import Thread

    async for db in client.app.dependency_overrides[get_db]():
        row = await db.get(Thread, thread_id)
        assert row is not None and row.project_id == project_id
        break

    enabled = await client.post(
        f"/projects/{slug}/settings/modules/threads",
        data={"enabled": "true"},
        follow_redirects=False,
    )
    assert enabled.status_code == 303
    restored = await client.get("/api/v1/threads")
    assert restored.status_code == 200
    assert any(row["id"] == str(thread_id) for row in restored.json())


@pytest.mark.asyncio
async def test_member_cannot_change_modules(client: AsyncClient):
    from app.auth.models import User
    from app.auth.service import create_user

    owner_id, project_id, slug, _owner_email = await _login_solo(client)
    async for db in client.app.dependency_overrides[get_db]():
        owner = await db.get(User, owner_id)
        member = await create_user(
            db,
            f"member-{uuid.uuid4().hex[:8]}@test.dev",
            "Member",
            "password123",
            account_id=owner.account_id,
            account_role="member",
            is_superadmin=False,
        )
        db.add(ProjectMember(user_id=member.id, project_id=project_id, role="editor"))
        await db.commit()
        member_email = member.email
        break
    await client.post("/logout", follow_redirects=False)
    login = await client.post(
        "/login",
        data={"email": member_email, "password": "password123"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    response = await client.post(
        f"/projects/{slug}/settings/modules/threads",
        data={"enabled": "true"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_disabled_incidents_skip_queued_job(db, monkeypatch):
    from app.jobs.handlers import handle_triage_sentry
    from app.webhooks.models import SentryIssue

    _owner, project = await _owner_and_project(db)
    issue = SentryIssue(
        sentry_issue_id=f"module-{uuid.uuid4().hex}",
        project="demo",
        title="Do not triage",
        level="error",
        status="new",
        events_count=1,
        project_id=project.id,
        account_id=project.account_id,
        payload={},
    )
    db.add(issue)
    await db.flush()

    async def unexpected(*_args, **_kwargs):
        raise AssertionError("LLM must not run for a disabled incident module")

    monkeypatch.setattr("app.ai.llm.triage_sentry", unexpected)
    result = await handle_triage_sentry(db, issue.id)
    assert result == {"status": "skipped", "reason": "module_disabled"}
    assert issue.status == "new"


@pytest.mark.asyncio
async def test_tokened_sentry_webhook_acknowledges_without_ingesting_when_disabled(
    client: AsyncClient,
):
    from app.auth.service import create_user
    from app.webhooks import connection
    from app.webhooks.models import SentryIssue

    suffix = uuid.uuid4().hex[:8]
    async for db in client.app.dependency_overrides[get_db]():
        user = await create_user(
            db, f"sentry-module-{suffix}@test.dev", "Owner", "password123", "admin"
        )
        project = await db.scalar(select(Project).where(Project.account_id == user.account_id))
        project.sentry_project_slug = "disabled-project"
        conn = await connection.get_or_create(db, user.account_id)
        token = conn.webhook_token
        await db.commit()
        break
    sentry_id = f"disabled-{suffix}"
    payload = json.dumps({
        "data": {
            "issue": {
                "id": sentry_id,
                "title": "Ignored safely",
                "project": {"slug": "disabled-project"},
            }
        }
    }).encode()
    response = await client.post(f"/webhooks/sentry/{token}", content=payload)
    assert response.status_code == 200
    assert response.json()["reason"] == "module_disabled"
    async for db in client.app.dependency_overrides[get_db]():
        assert await db.scalar(select(SentryIssue).where(
            SentryIssue.sentry_issue_id == sentry_id
        )) is None
        break
