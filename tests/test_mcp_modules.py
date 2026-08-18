"""Contract tests for module-aware MCP discovery and direct-call enforcement."""

import json
import uuid

import pytest

LEGACY_TOOL_MODULES = {
    "pulsyr_context": "core",
    "pulsyr_search": "core",
    "pulsyr_list": "core",
    "pulsyr_areas": "core",
    "pulsyr_move_area": "core",
    "pulsyr_create": "core",
    "pulsyr_advance": "core",
    "pulsyr_complete": "core",
    "pulsyr_link": "core",
    "pulsyr_thread_create": "threads",
    "pulsyr_thread_advance": "threads",
    "pulsyr_thread_list": "threads",
    "pulsyr_thread": "threads",
    "pulsyr_thread_link": "threads",
    "pulsyr_incidents": "incidents",
    "pulsyr_incident": "incidents",
    "pulsyr_incident_resolve": "incidents",
    "pulsyr_doc_list": "management",
    "pulsyr_doc_get": "management",
    "pulsyr_doc_put": "management",
    "pulsyr_pending_list": "management",
    "pulsyr_pending_upsert": "management",
    "pulsyr_pending_complete": "management",
    "pulsyr_gantt_get": "management",
    "pulsyr_gantt_task_upsert": "management",
    "pulsyr_gantt_task_remove": "management",
}


async def _setup(client, enabled=(), scope="write"):
    from app.accounts.service import create_account
    from app.auth.service import create_api_token
    from app.database import get_db
    from app.projects.modules import set_module_enabled
    from app.projects.service import create_project

    suffix = uuid.uuid4().hex[:8]
    async for db in client.app.dependency_overrides[get_db]():
        account, owner = await create_account(
            db, f"m{suffix}", f"m{suffix}@test.cl", "M", "password"
        )
        project = await create_project(
            db, name=f"m-{suffix}", account_id=account.id, preset="solo"
        )
        for module in enabled:
            await set_module_enabled(db, project.id, module, True, owner.email)
        token, raw = await create_api_token(db, f"m-{suffix}", scope, owner.id)
        token.project_id = project.id
        await db.commit()
        return raw, project.id
    raise AssertionError("database dependency did not yield")


async def _rpc(client, raw, method, params=None):
    return await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        headers={"Authorization": f"Bearer {raw}"},
    )


def _tool_payload(response):
    return json.loads(response.json()["result"]["content"][0]["text"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "enabled",
    [
        (),
        ("threads",),
        ("incidents",),
        ("management",),
        ("threads", "incidents"),
        ("threads", "management"),
        ("incidents", "management"),
        ("threads", "incidents", "management"),
    ],
)
async def test_discovery_matches_all_module_combinations(client, enabled):
    from app.mcp.server import TOOLS

    raw, _ = await _setup(client, enabled)
    response = await _rpc(client, raw, "tools/list")
    descriptors = response.json()["result"]["tools"]
    names = {descriptor["name"] for descriptor in descriptors}
    expected_modules = {"core", *enabled}
    assert names == {
        name for name, tool in TOOLS.items() if tool.module in expected_modules
    }
    assert "pulsyr_capabilities" in names
    for descriptor in descriptors:
        assert set(descriptor["annotations"]) == {
            "readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"
        }

    capabilities = _tool_payload(await _rpc(
        client,
        raw,
        "tools/call",
        {"name": "pulsyr_capabilities", "arguments": {}},
    ))
    effective = {
        module for module, state in capabilities["modules"].items() if state["effective"]
    }
    assert effective == expected_modules

    resources = (await _rpc(client, raw, "resources/list")).json()["result"]["resources"]
    resource_uris = {entry["uri"] for entry in resources}
    assert "pulsyr://capabilities" in resource_uris
    assert ("pulsyr://incidents/open" in resource_uris) == ("incidents" in enabled)
    assert ("pulsyr://management/status" in resource_uris) == ("management" in enabled)
    templates = (
        await _rpc(client, raw, "resources/templates/list")
    ).json()["result"]["resourceTemplates"]
    template_uris = {entry["uriTemplate"] for entry in templates}
    assert ("pulsyr://threads/{thread_id}" in template_uris) == ("threads" in enabled)
    prompts = (await _rpc(client, raw, "prompts/list")).json()["result"]["prompts"]
    assert {prompt["name"] for prompt in prompts} == {"briefing", "decision"}


def test_legacy_catalog_keeps_all_26_names_and_module_ownership():
    from app.mcp.server import TOOLS

    assert len(LEGACY_TOOL_MODULES) == 26
    assert {name: TOOLS[name].module for name in LEGACY_TOOL_MODULES} == LEGACY_TOOL_MODULES


def test_annotations_do_not_claim_create_style_upserts_are_idempotent():
    from app.mcp.server import TOOLS

    assert TOOLS["pulsyr_create"].annotations["idempotentHint"] is False
    assert TOOLS["pulsyr_pending_upsert"].annotations["idempotentHint"] is False
    assert TOOLS["pulsyr_pending_complete"].annotations["idempotentHint"] is False
    assert TOOLS["pulsyr_gantt_task_upsert"].annotations["idempotentHint"] is False
    assert TOOLS["pulsyr_unlink"].annotations == {
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    }


@pytest.mark.asyncio
async def test_disabled_direct_call_precedes_scope_and_never_calls_handler(client, monkeypatch):
    from app.mcp.server import TOOLS

    raw, _ = await _setup(client, enabled=(), scope="read")
    called = False

    async def forbidden_handler(*_args):
        nonlocal called
        called = True
        raise AssertionError("disabled handler ran")

    monkeypatch.setattr(TOOLS["pulsyr_thread_create"], "handler", forbidden_handler)
    response = await _rpc(client, raw, "tools/call", {
        "name": "pulsyr_thread_create",
        "arguments": {"title": "hidden", "area_name": "x"},
    })
    result = response.json()["result"]
    payload = json.loads(result["content"][0]["text"])
    assert result["isError"] is True
    assert payload["error"]["code"] == "module_disabled"
    assert called is False


@pytest.mark.asyncio
async def test_same_token_sees_module_toggle_without_regeneration(client):
    from app.database import get_db
    from app.projects.modules import set_module_enabled

    raw, project_id = await _setup(client, enabled=())
    before = await _rpc(client, raw, "tools/list")
    assert "pulsyr_thread_list" not in {
        tool["name"] for tool in before.json()["result"]["tools"]
    }
    async for db in client.app.dependency_overrides[get_db]():
        await set_module_enabled(db, project_id, "threads", True, "test@pulsyr.local")
        await db.commit()
        break
    after = await _rpc(client, raw, "tools/list")
    assert "pulsyr_thread_list" in {
        tool["name"] for tool in after.json()["result"]["tools"]
    }


@pytest.mark.asyncio
async def test_context_and_capabilities_resource_are_module_aware(client):
    raw, _ = await _setup(client, enabled=())
    context = _tool_payload(await _rpc(client, raw, "tools/call", {
        "name": "pulsyr_context", "arguments": {},
    }))
    assert context["modules"] == ["core"]
    assert "active_threads" not in context["local"]
    assert "sentry_unlinked" not in context["local"]

    resource = await _rpc(
        client, raw, "resources/read", {"uri": "pulsyr://capabilities"}
    )
    payload = json.loads(resource.json()["result"]["contents"][0]["text"])
    assert payload["modules"]["core"]["effective"] is True
    assert payload["modules"]["threads"]["effective"] is False

    disabled = await _rpc(
        client, raw, "resources/read", {"uri": "pulsyr://incidents/open"}
    )
    assert disabled.json()["error"]["code"] == -32003
    assert "module_disabled" in disabled.json()["error"]["message"]
