"""UI screen + HTMX action coverage. Logs in a real session (cookie persists on the client
jar), seeds data into the user's project, exercises every screen and /ui/* action, asserting
status + content + DB effect."""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.database import get_db


async def _login(client: AsyncClient, role: str = "admin"):
    """Create a user (auto-account + 'Default' project), log in (cookie lands in the client
    jar so later requests are authenticated), return (user_id, project_id)."""
    from app.auth.service import create_user
    from app.projects.models import Project

    suffix = uuid.uuid4().hex[:8]
    email = f"ui{suffix}@t.cl"
    async for db in client.app.dependency_overrides[get_db]():
        user = await create_user(db, email, "UI User", "password", role)
        proj = (await db.execute(
            select(Project).where(Project.account_id == user.account_id)
        )).scalars().first()
        uid, pid = user.id, proj.id
        break
    resp = await client.post(
        "/login", data={"email": email, "password": "password"}, follow_redirects=False
    )
    assert resp.status_code == 303
    return uid, pid


async def _seed_item(client, pid, *, title="Task", status="backlog", type="feature",
                     impact_ai=None, effort_ai=None, priority=None, agent_ready=False):
    from app.items.models import Item
    from app.scopes.models import Scope

    async for db in client.app.dependency_overrides[get_db]():
        scope = (await db.execute(select(Scope).where(Scope.project_id == pid))).scalars().first()
        if scope is None:
            scope = Scope(name=f"area-{uuid.uuid4().hex[:6]}", project_id=pid)
            db.add(scope)
            await db.flush()
        item = Item(
            scope_id=scope.id, project_id=pid, title=title, type=type, status=status,
            origen="human", impact_ai=impact_ai, effort_ai=effort_ai, priority=priority,
            agent_ready=agent_ready,
        )
        db.add(item)
        await db.commit()
        await db.refresh(item)
        return item.id, scope.id


@pytest.mark.asyncio
async def test_dashboard_and_screens_render(client: AsyncClient):
    _uid, pid = await _login(client)
    await _seed_item(client, pid, title="On the board", impact_ai=5, effort_ai="XS")
    r = await client.get("/")
    assert r.status_code == 200 and 'id="home-cards"' in r.text  # the dashboard, not the login page
    for path in ("/backlog", "/priority", "/threads", "/incidents", "/projects"):
        rr = await client.get(path)
        assert rr.status_code == 200, path


@pytest.mark.asyncio
async def test_backlog_filters_and_hx(client: AsyncClient):
    _uid, pid = await _login(client)
    await _seed_item(client, pid, title="Bug one here", type="bug", status="backlog")
    r = await client.get("/backlog?status=backlog&item_type=bug&order=impact")
    assert r.status_code == 200 and "Bug one here" in r.text
    r2 = await client.get("/backlog", headers={"HX-Request": "true"})
    assert r2.status_code == 200


@pytest.mark.asyncio
async def test_item_detail_and_404(client: AsyncClient):
    _uid, pid = await _login(client)
    item_id, _ = await _seed_item(client, pid, title="Detail me now")
    r = await client.get(f"/items/{item_id}")
    assert r.status_code == 200 and "Detail me now" in r.text
    r404 = await client.get(f"/items/{uuid.uuid4()}")
    assert r404.status_code == 404


@pytest.mark.asyncio
async def test_item_transition_close_reopen(client: AsyncClient):
    _uid, pid = await _login(client)
    item_id, _ = await _seed_item(client, pid, status="backlog")
    r = await client.post(f"/ui/items/{item_id}/transition", data={"status": "in-progress"})
    assert r.status_code == 204
    rc = await client.post(
        f"/ui/items/{item_id}/close", data={"status": "done", "reason": "shipped"}
    )
    assert rc.status_code == 204
    rr = await client.post(f"/ui/items/{item_id}/reopen")
    assert rr.status_code == 204


@pytest.mark.asyncio
async def test_item_transition_invalid_422(client: AsyncClient):
    _uid, pid = await _login(client)
    item_id, _ = await _seed_item(client, pid, status="backlog")
    r = await client.post(f"/ui/items/{item_id}/transition", data={"status": "done"})
    assert r.status_code == 422  # terminal must go through /close


@pytest.mark.asyncio
async def test_item_set_field(client: AsyncClient):
    _uid, pid = await _login(client)
    item_id, _ = await _seed_item(client, pid)
    for field, value in (("priority", "p0"), ("impact_ai", "4"), ("effort_ai", "M")):
        r = await client.post(f"/ui/items/{item_id}/field", data={"field": field, "value": value})
        assert r.status_code == 204
    bad = await client.post(f"/ui/items/{item_id}/field", data={"field": "nope", "value": "x"})
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_item_create_via_ui(client: AsyncClient):
    _uid, pid = await _login(client)
    _i, scope_id = await _seed_item(client, pid)
    r = await client.post(
        "/ui/items/create",
        data={"title": "Created via UI", "scope_id": str(scope_id), "type": "feature"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    bad = await client.post(
        "/ui/items/create",
        data={"title": "X", "scope_id": str(uuid.uuid4()), "type": "feature"},
    )
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_relationships_create_and_delete(client: AsyncClient):
    _uid, pid = await _login(client)
    uniq = uuid.uuid4().hex[:8]
    a, _ = await _seed_item(client, pid, title=f"Alpha {uniq}")
    b, _ = await _seed_item(client, pid, title=f"Betaunique {uniq}")
    r = await client.post(
        f"/ui/items/{a}/relationships",
        data={"relation": "blocks", "target_query": f"Betaunique {uniq}"},
    )
    assert r.status_code == 200
    d = await client.request(
        "DELETE", f"/ui/items/{a}/relationships",
        params={"source": str(a), "target": str(b), "relation": "blocks"},
    )
    assert d.status_code == 200


@pytest.mark.asyncio
async def test_thread_create_advance_detail(client: AsyncClient):
    _uid, pid = await _login(client)
    _i, scope_id = await _seed_item(client, pid)
    from app.scopes.models import Scope

    async for db in client.app.dependency_overrides[get_db]():
        sname = (await db.get(Scope, scope_id)).name
        break
    r = await client.post(
        "/ui/threads/create", data={"title": "Big feature x", "scope_name": sname},
        follow_redirects=False,
    )
    assert r.status_code == 303
    tid = r.headers["location"].split("/")[-1]
    detail = await client.get(f"/threads/{tid}")
    assert detail.status_code == 200 and "Big feature x" in detail.text
    adv = await client.post(f"/ui/threads/{tid}/advance", data={"artifact_content": "notes"})
    assert adv.status_code == 204
    stage = await client.post(f"/ui/threads/{tid}/stage", data={"stage": "spec"})
    assert stage.status_code == 204


@pytest.mark.asyncio
async def test_switch_project_and_admin_screens(client: AsyncClient):
    _uid, pid = await _login(client)
    sw = await client.post(
        "/ui/project/switch", data={"project_id": str(pid)}, follow_redirects=False
    )
    assert sw.status_code == 303
    for path in ("/admin", "/admin/accounts", "/account/members"):
        r = await client.get(path)
        assert r.status_code == 200, path


@pytest.mark.asyncio
async def test_unauthenticated_redirects_to_login(client: AsyncClient):
    r = await client.get("/", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert "/login" in r.headers.get("location", "")


@pytest.mark.asyncio
async def test_incident_promote_and_ignore(client: AsyncClient):
    _uid, pid = await _login(client)
    from app.webhooks.models import SentryIssue

    ids = []
    async for db in client.app.dependency_overrides[get_db]():
        for _ in range(2):
            iss = SentryIssue(sentry_issue_id=f"u{uuid.uuid4().hex[:8]}", project="x", title="Err",
                              level="error", status="new", events_count=1, payload={}, project_id=pid)
            db.add(iss)
            await db.flush()
            ids.append(str(iss.id))
        await db.commit()
        break
    pr = await client.post(f"/ui/incidents/{ids[0]}/promote", data={"priority": "p0"})
    assert pr.status_code == 204
    ig = await client.post(f"/ui/incidents/{ids[1]}/ignore")
    assert ig.status_code == 204
    nf = await client.post(f"/ui/incidents/{uuid.uuid4()}/ignore")
    assert nf.status_code == 404


@pytest.mark.asyncio
async def test_hilo_elaborate_degraded(client: AsyncClient, monkeypatch):
    monkeypatch.setattr("app.config.settings.anthropic_api_key", "")
    _uid, pid = await _login(client)
    _i, scope_id = await _seed_item(client, pid)
    from app.scopes.models import Scope

    async for db in client.app.dependency_overrides[get_db]():
        sname = (await db.get(Scope, scope_id)).name
        break
    cr = await client.post("/ui/threads/create", data={"title": "Elab", "scope_name": sname},
                           follow_redirects=False)
    tid = cr.headers["location"].split("/")[-1]
    # without an API key, elaborate degrades to a 200 error fragment (never 500)
    r = await client.post(f"/ui/threads/{tid}/elaborate")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_static_brand_assets_served(client: AsyncClient):
    r = await client.get("/static/brand/pulsyr-favicon-16.svg")
    assert r.status_code == 200 and "svg" in r.headers["content-type"]
    r = await client.get("/static/manifest.webmanifest")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_list_items_topological_order_fixed(client: AsyncClient):
    """service.list_items(order='topological') must order blocker before blocked item."""
    from app.items.models import ItemRelationship
    from app.items.service import list_items

    _uid, pid = await _login(client)
    a_id, _ = await _seed_item(client, pid, title="Topo-A depends on nothing")
    b_id, _ = await _seed_item(client, pid, title="Topo-B blocked by A")

    async for db in client.app.dependency_overrides[get_db]():
        rel = ItemRelationship(source_id=a_id, target_id=b_id, relation="blocks")
        db.add(rel)
        await db.commit()

        items = await list_items(db, project_id=pid, order="topological")
        ids_in_order = [str(i.id) for i in items]
        assert ids_in_order.index(str(a_id)) < ids_in_order.index(str(b_id)), (
            "topological order: blocker (a) must appear before its target (b)"
        )
        break


@pytest.mark.asyncio
async def test_home_cards_stats(client: AsyncClient):
    _uid, pid = await _login(client)
    await _seed_item(client, pid, title="Open A", status="backlog")
    await _seed_item(client, pid, title="Quick win", status="backlog", impact_ai=5, effort_ai="XS")
    await _seed_item(client, pid, title="An idea", status="idea")
    r = await client.get("/")
    assert r.status_code == 200
    assert 'id="home-cards"' in r.text
    assert "Backlog" in r.text


# ---------- Backlog redesign tests ----------

@pytest.mark.asyncio
async def test_backlog_show_param(client: AsyncClient):

    from app.items.models import Item
    from app.scopes.models import Scope

    _uid, pid = await _login(client)
    await _seed_item(client, pid, title="Open item", status="backlog")
    async for db in client.app.dependency_overrides[get_db]():
        scope = (await db.execute(select(Scope).where(Scope.project_id == pid))).scalars().first()
        import datetime as _dt
        done = Item(scope_id=scope.id, project_id=pid, title="Done item", type="feature",
                    status="done", origen="human",
                    closed_at=_dt.datetime.now(_dt.timezone.utc))
        db.add(done)
        await db.commit()
        break

    # Default (show=open) excludes done
    r = await client.get("/backlog")
    assert r.status_code == 200
    assert "Open item" in r.text
    assert "Done item" not in r.text

    # show=closed shows only closed
    r2 = await client.get("/backlog?show=closed")
    assert r2.status_code == 200
    assert "Done item" in r2.text
    assert "Open item" not in r2.text

    # show=all shows both
    r3 = await client.get("/backlog?show=all")
    assert r3.status_code == 200
    assert "Open item" in r3.text
    assert "Done item" in r3.text

    # Explicit status= overrides show=
    r4 = await client.get("/backlog?status=backlog&show=closed")
    assert "Open item" in r4.text
    assert "Done item" not in r4.text


@pytest.mark.asyncio
async def test_backlog_search_q(client: AsyncClient):
    _uid, pid = await _login(client)
    await _seed_item(client, pid, title="xyzunique backlog feature")
    await _seed_item(client, pid, title="completely different title")
    r = await client.get("/backlog?q=xyzunique")
    assert r.status_code == 200
    assert "xyzunique backlog feature" in r.text
    assert "completely different title" not in r.text


@pytest.mark.asyncio
async def test_backlog_filter_priority_effort(client: AsyncClient):
    _uid, pid = await _login(client)
    await _seed_item(client, pid, title="P0 item", priority="p0", effort_ai="M")
    await _seed_item(client, pid, title="P2 item", priority="p2", effort_ai="XL")
    r = await client.get("/backlog?priority=p0")
    assert r.status_code == 200
    assert "P0 item" in r.text
    assert "P2 item" not in r.text
    r2 = await client.get("/backlog?effort=M")
    assert r2.status_code == 200
    assert "P0 item" in r2.text


@pytest.mark.asyncio
async def test_backlog_chips_quickwins_urgent_agent_ready(client: AsyncClient):
    from app.items.models import Item
    from app.scopes.models import Scope

    _uid, pid = await _login(client)
    async for db in client.app.dependency_overrides[get_db]():
        scope = (await db.execute(select(Scope).where(Scope.project_id == pid))).scalars().first()
        if scope is None:
            scope = Scope(name="area-chips", project_id=pid)
            db.add(scope)
            await db.flush()
        qw = Item(scope_id=scope.id, project_id=pid, title="Quick win item", type="feature",
                  status="backlog", origen="human", impact_ai=5, effort_ai="XS")
        ar = Item(scope_id=scope.id, project_id=pid, title="Agent ready item", type="feature",
                  status="backlog", origen="human", agent_ready=True)
        slow = Item(scope_id=scope.id, project_id=pid, title="Slow heavy item", type="feature",
                    status="backlog", origen="human", impact_ai=1, effort_ai="XL")
        db.add_all([qw, ar, slow])
        await db.commit()
        break

    r = await client.get("/backlog?quickwins=true")
    assert r.status_code == 200
    assert "Quick win item" in r.text
    assert "Slow heavy item" not in r.text

    r2 = await client.get("/backlog?agent_ready=true")
    assert r2.status_code == 200
    assert "Agent ready item" in r2.text
    assert "Quick win item" not in r2.text

    r3 = await client.get("/backlog?urgent=true")
    assert r3.status_code == 200  # at least doesn't crash


@pytest.mark.asyncio
async def test_backlog_view_board(client: AsyncClient):
    _uid, pid = await _login(client)
    await _seed_item(client, pid, title="Board item backlog", status="backlog")
    await _seed_item(client, pid, title="Board item in-progress", status="in-progress")
    r = await client.get("/backlog?view=board")
    assert r.status_code == 200
    r2 = await client.get("/backlog?view=board", headers={"HX-Request": "true"})
    assert r2.status_code == 200


@pytest.mark.asyncio
async def test_backlog_group_by(client: AsyncClient):
    _uid, pid = await _login(client)
    await _seed_item(client, pid, title="Group test item")
    r = await client.get("/backlog?group=type")
    assert r.status_code == 200
    assert "Group test item" in r.text


@pytest.mark.asyncio
async def test_board_dnd_valid_move(client: AsyncClient):
    """Drag&drop válido: aplica la transición y devuelve el tablero re-renderizado."""
    from app.items.models import Item

    _uid, pid = await _login(client)
    item_id, _ = await _seed_item(client, pid, title="DnD movable", status="backlog")
    r = await client.post(f"/ui/items/{item_id}/board-move",
                          data={"status": "in-progress"},
                          headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "board-root" in r.text and "DnD movable" in r.text  # board partial
    assert "HX-Trigger" not in r.headers  # move was valid → no error toast
    async for db in client.app.dependency_overrides[get_db]():
        it = (await db.execute(select(Item).where(Item.id == item_id))).scalar_one()
        assert it.status == "in-progress"
        break


@pytest.mark.asyncio
async def test_board_dnd_invalid_move_snaps_back(client: AsyncClient):
    """Movimiento inválido por lifecycle: tablero sin cambios + toast vía HX-Trigger, no 422."""
    from app.items.models import Item

    _uid, pid = await _login(client)
    # idea → in-review no es transición válida (matriz de lifecycle)
    item_id, _ = await _seed_item(client, pid, title="DnD stuck", status="idea")
    r = await client.post(f"/ui/items/{item_id}/board-move",
                          data={"status": "in-review"},
                          headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "board-root" in r.text
    assert "pulsyr:toast" in r.headers.get("HX-Trigger", "")
    async for db in client.app.dependency_overrides[get_db]():
        it = (await db.execute(select(Item).where(Item.id == item_id))).scalar_one()
        assert it.status == "idea"  # sin cambios
        break


@pytest.mark.asyncio
async def test_board_grip_gated_by_write(client: AsyncClient):
    """El grip de arrastre y can_write solo aparecen para quien puede escribir."""
    _uid, pid = await _login(client)
    await _seed_item(client, pid, title="Grip item", status="backlog")
    r = await client.get("/backlog?view=board")
    assert r.status_code == 200
    assert 'data-can-write="1"' in r.text  # admin/owner puede escribir
    assert "data-drag-handle" in r.text


@pytest.mark.asyncio
async def test_close_modal_endpoint(client: AsyncClient):
    _uid, pid = await _login(client)
    item_id, _ = await _seed_item(client, pid, status="backlog")

    r = await client.get(f"/ui/items/{item_id}/close-modal")
    assert r.status_code == 200
    assert "done" in r.text
    assert "discarded" in r.text

    r404 = await client.get(f"/ui/items/{uuid.uuid4()}/close-modal")
    assert r404.status_code == 404


@pytest.mark.asyncio
async def test_registro_groups_by_week(client: AsyncClient):
    import datetime as _dt

    from app.items.models import Item
    from app.scopes.models import Scope

    _uid, pid = await _login(client)
    async for db in client.app.dependency_overrides[get_db]():
        scope = (await db.execute(select(Scope).where(Scope.project_id == pid))).scalars().first()
        if scope is None:
            scope = Scope(name="area-reg", project_id=pid)
            db.add(scope)
            await db.flush()
        closed1 = Item(scope_id=scope.id, project_id=pid, title="Closed this week",
                       type="feature", status="done", origen="human",
                       closed_at=_dt.datetime.now(_dt.timezone.utc))
        closed2 = Item(scope_id=scope.id, project_id=pid, title="Old closed item",
                       type="bug", status="discarded", origen="human",
                       closed_at=_dt.datetime(2020, 1, 6, tzinfo=_dt.timezone.utc))
        no_date = Item(scope_id=scope.id, project_id=pid, title="No date closed",
                       type="docs", status="done", origen="human", closed_at=None)
        db.add_all([closed1, closed2, no_date])
        await db.commit()
        break

    r = await client.get("/archive")
    assert r.status_code == 200
    assert "Closed this week" in r.text
    assert "Old closed item" in r.text
    assert "No date" in r.text  # default EN; es="Sin fecha"

    r2 = await client.get("/archive", headers={"HX-Request": "true"})
    assert r2.status_code == 200
    assert "Closed this week" in r2.text


@pytest.mark.asyncio
async def test_registro_close_event_reason(client: AsyncClient):
    import datetime as _dt

    from app.items.models import Item, ItemEvent
    from app.scopes.models import Scope

    _uid, pid = await _login(client)
    async for db in client.app.dependency_overrides[get_db]():
        scope = (await db.execute(select(Scope).where(Scope.project_id == pid))).scalars().first()
        if scope is None:
            scope = Scope(name="area-reason", project_id=pid)
            db.add(scope)
            await db.flush()
        item = Item(scope_id=scope.id, project_id=pid, title="With reason item",
                    type="feature", status="done", origen="human",
                    closed_at=_dt.datetime.now(_dt.timezone.utc))
        db.add(item)
        await db.flush()
        ev = ItemEvent(item_id=item.id, actor="user@test", action="closed",
                       payload={"status": "done", "reason": "shipped v2", "commit_sha": "abc123"})
        db.add(ev)
        await db.commit()
        break

    r = await client.get("/archive")
    assert r.status_code == 200
    assert "shipped v2" in r.text
    assert "abc123" in r.text


@pytest.mark.asyncio
async def test_registro_filters_and_load_more(client: AsyncClient):
    import datetime as _dt

    from app.items.models import Item
    from app.scopes.models import Scope

    _uid, pid = await _login(client)
    async for db in client.app.dependency_overrides[get_db]():
        scope = (await db.execute(select(Scope).where(Scope.project_id == pid))).scalars().first()
        if scope is None:
            scope = Scope(name="area-filters", project_id=pid)
            db.add(scope)
            await db.flush()
        db.add(Item(scope_id=scope.id, project_id=pid, title="Bug closed item",
                    type="bug", status="done", origen="human",
                    closed_at=_dt.datetime.now(_dt.timezone.utc)))
        db.add(Item(scope_id=scope.id, project_id=pid, title="Feature closed item",
                    type="feature", status="done", origen="human",
                    closed_at=_dt.datetime.now(_dt.timezone.utc)))
        await db.commit()
        break

    r = await client.get("/archive?item_type=bug")
    assert r.status_code == 200
    assert "Bug closed item" in r.text
    assert "Feature closed item" not in r.text

    r2 = await client.get("/archive?before=2020-01-01")
    assert r2.status_code == 200


@pytest.mark.asyncio
async def test_registro_summary_no_api_key(client: AsyncClient, monkeypatch):
    monkeypatch.setattr("app.config.settings.anthropic_api_key", "")
    _uid, pid = await _login(client)
    r = await client.get("/ui/archive/summary?week=2026-W27")
    assert r.status_code == 200
    assert "unavailable" in r.text.lower()  # default EN


@pytest.mark.asyncio
async def test_registro_in_nav_and_dashboard_homecard(client: AsyncClient):
    _uid, pid = await _login(client)
    r = await client.get("/")
    assert r.status_code == 200
    assert "/archive" in r.text
    r2 = await client.get("/archive")
    assert r2.status_code == 200


# ---------- i18n ----------


@pytest.mark.asyncio
async def test_lang_default_english_and_switch(client: AsyncClient):
    from app.auth.service import create_user

    # Sin usuarios /login redirige a /setup — crea uno para ver el login real.
    async for db in client.app.dependency_overrides[get_db]():
        await create_user(db, f"lang{uuid.uuid4().hex[:6]}@t.cl", "Lang User", "password")
        break

    r = await client.get("/login")
    assert r.status_code == 200
    assert 'lang="en"' in r.text and "Sign in" in r.text

    r_es = await client.get("/ui/lang/es?next=/login", follow_redirects=True)
    assert 'lang="es"' in r_es.text and "Entrar" in r_es.text

    r_fr = await client.get("/ui/lang/fr?next=/login", follow_redirects=True)
    assert 'lang="fr"' in r_fr.text and "Se connecter" in r_fr.text


@pytest.mark.asyncio
async def test_lang_switch_guards(client: AsyncClient):
    assert (await client.get("/ui/lang/xx?next=/")).status_code == 404
    r = await client.get("/ui/lang/en?next=//evil.com", follow_redirects=False)
    assert r.headers["location"] == "/"


@pytest.mark.asyncio
async def test_lang_selector_in_navbar(client: AsyncClient):
    _uid, pid = await _login(client)
    r = await client.get("/")
    assert r.status_code == 200
    for code in ("en", "es", "fr"):
        assert f"/ui/lang/{code}?next=" in r.text


@pytest.mark.asyncio
async def test_all_screens_render_in_all_languages(client: AsyncClient):
    """Smoke: cada pantalla renderiza 200 en en/es/fr (errores de runtime de i18n)."""
    _uid, pid = await _login(client)
    item_id, _ = await _seed_item(client, pid, title="Lang smoke item")
    screens = ("/", "/backlog", "/backlog?view=board", "/backlog?group=status",
               "/archive", "/priority", "/threads", "/incidents",
               "/projects", f"/items/{item_id}", "/admin", "/account/members")
    probes = {"es": ("Actividad reciente", "/"), "fr": ("Activité récente", "/")}
    for code in ("en", "es", "fr"):
        await client.get(f"/ui/lang/{code}?next=/")
        for path in screens:
            r = await client.get(path)
            assert r.status_code == 200, f"{code} {path} -> {r.status_code}"
        if code in probes:
            text, path = probes[code]
            r = await client.get(path)
            assert text in r.text, f"{code}: {text!r} not found in {path}"


@pytest.mark.asyncio
async def test_legacy_spanish_routes_redirect(client: AsyncClient):
    """Pre-v0018 Spanish slugs 301 to the English routes, query string preserved."""
    await _login(client)
    for old, new in [("/prioridad", "/priority"), ("/hilos", "/threads"),
                     ("/incidentes", "/incidents"), ("/registro", "/archive")]:
        r = await client.get(old, follow_redirects=False)
        assert r.status_code == 301 and r.headers["location"] == new, old
    r = await client.get("/registro?item_type=bug", follow_redirects=False)
    assert r.headers["location"] == "/archive?item_type=bug"
    tid = uuid.uuid4()
    r = await client.get(f"/hilos/{tid}", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == f"/threads/{tid}"


# ---------- UX quick wins (2026-07-16 audit) ----------

@pytest.mark.asyncio
async def test_new_project_seeds_default_scope(client: AsyncClient):
    """A fresh project must be usable by a human: the new-item modal needs at
    least one scope option, so create_project seeds a 'General' scope."""
    _uid, _pid = await _login(client)
    r = await client.get("/")
    assert r.status_code == 200
    assert ">General<" in r.text


@pytest.mark.asyncio
async def test_markdown_rendered_and_sanitized(client: AsyncClient):
    """M1: summary/comments render markdown; raw HTML is escaped (XSS-safe)."""
    _uid, pid = await _login(client)
    item_id, _ = await _seed_item(client, pid, title="MD item")
    await client.post(f"/ui/items/{item_id}/field", data={
        "field": "summary_md", "value": "**bold move** <script>alert(1)</script>",
    })
    detail = await client.get(f"/items/{item_id}")
    assert "<strong>bold move</strong>" in detail.text
    assert "<script>alert(1)</script>" not in detail.text


# ---------- UX audit cluster 1: behavior endpoints (2026-07-16) ----------

@pytest.mark.asyncio
async def test_login_seeds_active_project_in_session(client: AsyncClient):
    """A3: right after login the header shows the resolved project, not 'Select project'."""
    await _login(client)
    r = await client.get("/")
    assert r.status_code == 200
    assert "Select project" not in r.text


@pytest.mark.asyncio
async def test_item_title_and_summary_editable_from_ui(client: AsyncClient):
    """A4: title and summary_md are editable through /ui/items/{id}/field."""
    _uid, pid = await _login(client)
    item_id, _ = await _seed_item(client, pid, title="Old title")
    r = await client.post(f"/ui/items/{item_id}/field", data={"field": "title", "value": "New title"})
    assert r.status_code == 204
    r2 = await client.post(
        f"/ui/items/{item_id}/field", data={"field": "summary_md", "value": "Fresh summary"}
    )
    assert r2.status_code == 204
    detail = await client.get(f"/items/{item_id}")
    assert "New title" in detail.text and "Fresh summary" in detail.text
    bad = await client.post(f"/ui/items/{item_id}/field", data={"field": "title", "value": "   "})
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_incident_unignore_restores_to_new(client: AsyncClient):
    """A5: an ignored incident can be restored from the UI (ignore is no longer one-way)."""
    _uid, pid = await _login(client)
    from app.webhooks.models import SentryIssue

    async for db in client.app.dependency_overrides[get_db]():
        iss = SentryIssue(sentry_issue_id=f"u{uuid.uuid4().hex[:8]}", project="x", title="Restorable",
                          level="error", status="ignored", events_count=1, payload={}, project_id=pid)
        db.add(iss)
        await db.commit()
        await db.refresh(iss)
        iid = str(iss.id)
        break
    r = await client.post(f"/ui/incidents/{iid}/unignore")
    assert r.status_code == 204
    page = await client.get("/incidents")  # default view hides ignored — restored must show up
    assert "Restorable" in page.text


@pytest.mark.asyncio
async def test_settings_sentry_slug_conflict_rerenders_form(client: AsyncClient):
    """A7: a 422 on project settings re-renders the page with the error, not a plain-text dead end."""
    await _login(client)
    second = await client.post("/projects/new", data={"name": "Second"}, follow_redirects=False)
    assert second.status_code == 303
    ok = await client.post("/projects/default/settings", data={
        "name": "Default", "description": "", "color": "", "repo_url": "",
        "github_webhook_secret": "", "sentry_project_slug": "dup-slug",
    }, follow_redirects=False)
    assert ok.status_code == 303
    clash = await client.post("/projects/second/settings", data={
        "name": "Second", "description": "", "color": "", "repo_url": "",
        "github_webhook_secret": "", "sentry_project_slug": "dup-slug",
    })
    assert clash.status_code == 422
    assert "API Tokens" in clash.text  # the full settings page, form intact


@pytest.mark.asyncio
async def test_change_password_flow(client: AsyncClient):
    """A8: users can change their own password; wrong current or short new are rejected."""
    from app.auth.service import create_user

    suffix = uuid.uuid4().hex[:8]
    email = f"pw{suffix}@t.cl"
    async for db in client.app.dependency_overrides[get_db]():
        await create_user(db, email, "PW User", "password", "admin")
        break
    await client.post("/login", data={"email": email, "password": "password"})
    page = await client.get("/password")
    assert page.status_code == 200
    bad = await client.post("/password", data={
        "current_password": "wrong", "new_password": "newpassword1", "confirm_password": "newpassword1",
    })
    assert bad.status_code == 422
    short = await client.post("/password", data={
        "current_password": "password", "new_password": "short", "confirm_password": "short",
    })
    assert short.status_code == 422
    ok = await client.post("/password", data={
        "current_password": "password", "new_password": "newpassword1", "confirm_password": "newpassword1",
    }, follow_redirects=False)
    assert ok.status_code == 303
    await client.post("/logout")
    relog = await client.post(
        "/login", data={"email": email, "password": "newpassword1"}, follow_redirects=False
    )
    assert relog.status_code == 303


@pytest.mark.asyncio
async def test_admin_token_result_warns_about_mcp(client: AsyncClient):
    """A2: /admin tokens have no project — the result must not offer the MCP connect snippet."""
    await _login(client)
    r = await client.post("/ui/admin/tokens", data={"name": "svc", "scopes": "write"})
    assert r.status_code == 200
    assert "claude mcp add" not in r.text


@pytest.mark.asyncio
async def test_backlog_counter_shows_cap(client: AsyncClient):
    """B8: when the 300-item fetch cap is hit, the counter says 300+ instead of lying."""
    _uid, pid = await _login(client)
    from app.items.models import Item
    from app.scopes.models import Scope

    async for db in client.app.dependency_overrides[get_db]():
        scope = Scope(name=f"cap-{uuid.uuid4().hex[:6]}", project_id=pid)
        db.add(scope)
        await db.flush()
        db.add_all([
            Item(scope_id=scope.id, project_id=pid, title=f"cap item {n}", type="feature",
                 status="backlog", origen="human")
            for n in range(305)
        ])
        await db.commit()
        break
    r = await client.get("/backlog")
    assert "300+" in r.text


@pytest.mark.asyncio
async def test_ui_add_comment_form_encoded(client: AsyncClient):
    """The item-detail comment form posts form-urlencoded; the UI endpoint must
    accept it (the REST endpoint only takes JSON — audit finding C1)."""
    _uid, pid = await _login(client)
    item_id, _ = await _seed_item(client, pid, title="Commentable")
    r = await client.post(f"/ui/items/{item_id}/comments", data={"body_md": "hello from the UI"})
    assert r.status_code == 204
    assert r.headers.get("HX-Refresh") == "true"
    detail = await client.get(f"/items/{item_id}")
    assert "hello from the UI" in detail.text


# ---------- Audit regressions (spec 2026-07-06) ----------


@pytest.mark.asyncio
async def test_close_modal_lifecycle_targets(client: AsyncClient):
    """Spec §1.6: radios según targets terminales — desde 'spec' solo se puede descartar."""
    _uid, pid = await _login(client)
    spec_id, _ = await _seed_item(client, pid, title="Spec-state item", status="spec")
    r = await client.get(f"/ui/items/{spec_id}/close-modal")
    assert r.status_code == 200
    assert 'value="discarded"' in r.text
    assert 'value="done"' not in r.text

    bl_id, _ = await _seed_item(client, pid, title="Backlog-state item", status="backlog")
    r2 = await client.get(f"/ui/items/{bl_id}/close-modal")
    assert 'value="done"' in r2.text and 'value="discarded"' in r2.text


@pytest.mark.asyncio
async def test_transition_out_of_terminal_clears_closed_at(client: AsyncClient):
    """done→backlog vía apply_transition (alcanzable por MCP pulsyr_advance) limpia closed_at."""
    import datetime as _dt

    from app.items import service
    from app.items.models import Item

    _uid, pid = await _login(client)
    item_id, _ = await _seed_item(client, pid, status="backlog")
    async for db in client.app.dependency_overrides[get_db]():
        item = (await db.execute(select(Item).where(Item.id == item_id))).scalar_one()
        item.status = "done"
        item.closed_at = _dt.datetime.now(_dt.timezone.utc)
        await db.flush()
        await service.apply_transition(db, item, "backlog", "test@t.cl")
        await db.commit()
        await db.refresh(item)
        assert item.status == "backlog"
        assert item.closed_at is None
        break


@pytest.mark.asyncio
async def test_registro_search_q(client: AsyncClient):
    """Regresión: /archive?q= pasaba dicts de search_items a Item.id.in_()."""
    import datetime as _dt

    from app.items.models import Item
    from app.scopes.models import Scope

    _uid, pid = await _login(client)
    async for db in client.app.dependency_overrides[get_db]():
        scope = (await db.execute(select(Scope).where(Scope.project_id == pid))).scalars().first()
        if scope is None:
            scope = Scope(name="area-regq", project_id=pid)
            db.add(scope)
            await db.flush()
        db.add(Item(scope_id=scope.id, project_id=pid, title="Facturacion electronica lista",
                    type="feature", status="done", origen="human",
                    closed_at=_dt.datetime.now(_dt.timezone.utc)))
        db.add(Item(scope_id=scope.id, project_id=pid, title="Otro cierre irrelevante",
                    type="bug", status="done", origen="human",
                    closed_at=_dt.datetime.now(_dt.timezone.utc)))
        await db.commit()
        break

    r = await client.get("/archive?q=facturacion")
    assert r.status_code == 200
    assert "Facturacion electronica lista" in r.text
    assert "Otro cierre irrelevante" not in r.text


@pytest.mark.asyncio
async def test_registro_commit_links_to_repo_url(client: AsyncClient):
    """Spec §2.1: sha corto linkeado a {repo_url}/commit/{sha} cuando repo_url está definido."""
    import datetime as _dt

    from app.items.models import Item
    from app.projects.models import Project
    from app.scopes.models import Scope

    _uid, pid = await _login(client)
    async for db in client.app.dependency_overrides[get_db]():
        proj = (await db.execute(select(Project).where(Project.id == pid))).scalar_one()
        proj.repo_url = "https://github.com/acme/repo"
        scope = (await db.execute(select(Scope).where(Scope.project_id == pid))).scalars().first()
        if scope is None:
            scope = Scope(name="area-sha", project_id=pid)
            db.add(scope)
            await db.flush()
        db.add(Item(scope_id=scope.id, project_id=pid, title="Item with commit",
                    type="feature", status="done", origen="human",
                    closed_at=_dt.datetime.now(_dt.timezone.utc),
                    source_refs={"commit_sha": "deadbeef1234567"}))
        await db.commit()
        break

    r = await client.get("/archive")
    assert r.status_code == 200
    assert 'https://github.com/acme/repo/commit/deadbeef1234567' in r.text
    assert "deadbee" in r.text


@pytest.mark.asyncio
async def test_backlog_status_overrides_show(client: AsyncClient):
    """Spec §1.1: un status concreto manda sobre show (default open)."""
    import datetime as _dt

    from app.items.models import Item
    from app.scopes.models import Scope

    _uid, pid = await _login(client)
    await _seed_item(client, pid, title="Abierto normal", status="backlog")
    async for db in client.app.dependency_overrides[get_db]():
        scope = (await db.execute(select(Scope).where(Scope.project_id == pid))).scalars().first()
        db.add(Item(scope_id=scope.id, project_id=pid, title="Cerrado explicito",
                    type="feature", status="done", origen="human",
                    closed_at=_dt.datetime.now(_dt.timezone.utc)))
        await db.commit()
        break

    # show default = open, pero status=done es más específico y gana
    r = await client.get("/backlog?status=done")
    assert r.status_code == 200
    assert "Cerrado explicito" in r.text
    assert "Abierto normal" not in r.text


@pytest.mark.asyncio
async def test_backlog_closed_rows_have_no_actions(client: AsyncClient):
    """Spec §1.6: el botón ✓ (y mover) solo en estados no terminales."""
    import datetime as _dt

    from app.items.models import Item
    from app.scopes.models import Scope

    _uid, pid = await _login(client)
    async for db in client.app.dependency_overrides[get_db]():
        scope = (await db.execute(select(Scope).where(Scope.project_id == pid))).scalars().first()
        if scope is None:
            scope = Scope(name="area-closed", project_id=pid)
            db.add(scope)
            await db.flush()
        db.add(Item(scope_id=scope.id, project_id=pid, title="Ya cerrado item",
                    type="feature", status="done", origen="human",
                    closed_at=_dt.datetime.now(_dt.timezone.utc)))
        await db.commit()
        break

    r = await client.get("/backlog?show=closed")
    assert r.status_code == 200
    assert "Ya cerrado item" in r.text
    assert "/close-modal" not in r.text
    assert "→ move…" not in r.text  # default EN
