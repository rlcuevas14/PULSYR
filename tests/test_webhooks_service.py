"""Coverage for app/webhooks/service.py + the signed webhook router endpoints."""
import hashlib
import hmac
import json
import uuid

import pytest

from app.webhooks import service as ws


def test_verify_sentry_signature():
    body = b'{"a":1}'
    sig = hmac.new(b"sec", body, hashlib.sha256).hexdigest()
    assert ws.verify_sentry_signature("sec", body, sig) is True
    assert ws.verify_sentry_signature("sec", body, "bad") is False
    assert ws.verify_sentry_signature("", body, sig) is False
    assert ws.verify_sentry_signature("sec", body, None) is False


def test_verify_github_signature():
    body = b'{"a":1}'
    sig = "sha256=" + hmac.new(b"sec", body, hashlib.sha256).hexdigest()
    assert ws.verify_github_signature("sec", body, sig) is True
    assert ws.verify_github_signature("sec", body, "sha256=bad") is False
    assert ws.verify_github_signature("", body, sig) is False


def test_sanitize_strips_tags_and_limits():
    assert ws._sanitize("<b>hi</b>") == "hi"
    assert ws._sanitize(None) == ""
    assert len(ws._sanitize("x" * 99, limit=10)) == 10


def test_parse_dt():
    assert ws._parse_dt("2026-01-01T00:00:00Z") is not None
    assert ws._parse_dt("not-a-date") is None
    assert ws._parse_dt(None) is None


def test_format_stacktrace():
    assert ws._format_stacktrace({}) == "(no event)"
    event = {
        "culprit": "app.x",
        "entries": [{"type": "exception", "data": {"values": [{
            "type": "ValueError", "value": "boom",
            "stacktrace": {"frames": [{"filename": "a.py", "lineNo": 3, "function": "f",
                                       "inApp": True, "context": [[3, "raise ValueError()"]]}]},
        }]}}],
    }
    out = ws._format_stacktrace(event)
    assert "ValueError: boom" in out and "a.py:3 in f" in out


@pytest.mark.asyncio
async def test_ingest_sentry_create_and_dedup(db):
    payload = {"data": {"issue": {"id": "S1", "title": "Boom", "project": "web",
                                  "level": "error", "count": 3}}}
    r1 = await ws.ingest_sentry(db, payload)
    assert r1["created"] is True and r1["events_count"] == 3
    r2 = await ws.ingest_sentry(db, payload)
    assert r2["created"] is False and r2["events_count"] == 4
    with pytest.raises(ValueError):
        await ws.ingest_sentry(db, {"data": {"issue": {"title": "no id"}}})


def test_parse_sentry_payload_shapes():
    # issue webhook (primario): slug desde data.issue.project.slug
    p1 = ws.parse_sentry_payload({"data": {"issue": {
        "id": 42, "title": "Boom", "level": "warning",
        "project": {"slug": "web", "id": 7}, "permalink": "https://s/x"}}})
    assert p1["sentry_id"] == "42" and p1["slug"] == "web" and p1["level"] == "warning"
    assert p1["web_url"] == "https://s/x"
    # event_alert: sin slug, con web_url
    p2 = ws.parse_sentry_payload({"data": {"event": {
        "issue_id": "77", "title": "Alert", "level": "error",
        "project": 7, "web_url": "https://s/y"}}})
    assert p2["sentry_id"] == "77" and p2["slug"] is None and p2["web_url"] == "https://s/y"
    # plugin legacy: plano
    p3 = ws.parse_sentry_payload({"id": "9", "project": "api", "level": "info", "url": "u",
                                  "message": "m"})
    assert p3["sentry_id"] == "9" and p3["slug"] == "api" and p3["level"] == "info"
    with pytest.raises(ValueError):
        ws.parse_sentry_payload({"data": {"issue": {"title": "no id"}}})


@pytest.mark.asyncio
async def test_ingest_stamps_account_and_project(db):
    from sqlalchemy import select

    from app.accounts.models import Account
    from app.projects.models import Project
    from app.webhooks.models import SentryIssue
    a = Account(name="x", slug=f"x-{uuid.uuid4().hex[:6]}")
    db.add(a)
    await db.flush()
    p = Project(name="w", slug=f"w-{uuid.uuid4().hex[:6]}", account_id=a.id,
                sentry_project_slug="web")
    db.add(p)
    await db.flush()
    payload = {"data": {"issue": {"id": "ST1", "title": "T", "project": {"slug": "web"}}}}
    await ws.ingest_sentry(db, payload, account_id=a.id, project_id=p.id)
    row = (await db.execute(select(SentryIssue).where(
        SentryIssue.sentry_issue_id == "ST1"))).scalar_one()
    assert row.account_id == a.id and row.project_id == p.id and row.project == "web"
    # el dedup sana una fila sin proyecto cuando el ruteo ya se conoce
    db.add(SentryIssue(sentry_issue_id="ST2", project="web", title="t"))
    await db.flush()
    await ws.ingest_sentry(db, {"data": {"issue": {"id": "ST2", "title": "t",
                            "project": {"slug": "web"}}}}, account_id=a.id, project_id=p.id)
    row2 = (await db.execute(select(SentryIssue).where(
        SentryIssue.sentry_issue_id == "ST2"))).scalar_one()
    assert row2.project_id == p.id and row2.account_id == a.id


@pytest.mark.asyncio
async def test_promote_issue_creates_item_idempotent(db):
    await ws.ingest_sentry(db, {"data": {"issue": {"id": "S2", "title": "Crash", "project": "api"}}})
    from sqlalchemy import select

    from app.webhooks.models import SentryIssue
    issue = (await db.execute(select(SentryIssue).where(SentryIssue.sentry_issue_id == "S2"))).scalar_one()
    item_id = await ws.promote_issue(db, issue, priority="p0")
    assert item_id
    assert issue.status == "linked"
    assert await ws.promote_issue(db, issue) == item_id  # idempotent


@pytest.mark.asyncio
async def test_resolve_issue_closes_linked_item(db):
    await ws.ingest_sentry(db, {"data": {"issue": {"id": "S3", "title": "Err", "project": "api"}}})
    from sqlalchemy import select

    from app.items.models import Item
    from app.webhooks.models import SentryIssue
    issue = (await db.execute(select(SentryIssue).where(SentryIssue.sentry_issue_id == "S3"))).scalar_one()
    item_id = await ws.promote_issue(db, issue)
    res = await ws.resolve_issue(db, issue, in_sentry=False, nota="fixed", actor="me")
    assert res["status"] == "resolved" and res["item_cerrado"] is True
    item = await db.get(Item, uuid.UUID(item_id))
    assert item.status == "done"


@pytest.mark.asyncio
async def test_process_github_push_autocompletes(db):
    from app.items.models import Item
    from app.scopes.models import Scope
    scope = Scope(name=f"auth-{uuid.uuid4().hex[:6]}")
    db.add(scope)
    await db.flush()
    item = Item(scope_id=scope.id, title="Close me", type="feature", status="in-progress", origen="human")
    db.add(item)
    await db.flush()
    payload = {"commits": [{"id": "abc123", "message": f"fix(auth): done pulsyr:{item.id}"}]}
    res = await ws.process_github_push(db, payload)
    assert str(item.id) in res["completed"]
    await db.refresh(item)
    assert item.status == "done"


@pytest.mark.asyncio
async def test_backfill_issues(db):
    issues = [{"id": "B1", "title": "one", "count": 1}, {"title": "no id"}]
    res = await ws.backfill_issues(db, issues, "web")
    assert res == {"ingested": 1, "total": 2}


@pytest.mark.asyncio
async def test_sentry_webhook_endpoint(client, monkeypatch):
    monkeypatch.setattr("app.config.settings.sentry_client_secret", "whsecret")
    payload = {"data": {"issue": {"id": "W1", "title": "Hook", "project": "web"}}}
    body = json.dumps(payload).encode()
    sig = hmac.new(b"whsecret", body, hashlib.sha256).hexdigest()
    r = await client.post("/webhooks/sentry", content=body, headers={"sentry-hook-signature": sig})
    assert r.status_code == 200 and r.json()["created"] is True
    bad = await client.post("/webhooks/sentry", content=body, headers={"sentry-hook-signature": "no"})
    assert bad.status_code == 401


@pytest.mark.asyncio
async def test_github_webhook_endpoint(client, monkeypatch):
    monkeypatch.setattr("app.config.settings.github_webhook_secret", "ghsecret")
    body = json.dumps({"commits": []}).encode()
    sig = "sha256=" + hmac.new(b"ghsecret", body, hashlib.sha256).hexdigest()
    r = await client.post(
        "/webhooks/github", content=body,
        headers={"x-hub-signature-256": sig, "x-github-event": "push"},
    )
    assert r.status_code == 200
    ignored = await client.post(
        "/webhooks/github", content=body,
        headers={"x-hub-signature-256": sig, "x-github-event": "ping"},
    )
    assert ignored.json() == {"ignored": "ping"}


@pytest.mark.asyncio
async def test_sentry_api_calls_mocked(monkeypatch):
    import httpx

    monkeypatch.setattr("app.config.settings.sentry_api_token", "tok")

    class _R:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"title": "T", "culprit": "c", "level": "error", "count": 1,
                    "firstSeen": None, "lastSeen": None, "permalink": "u", "entries": []}

    async def fake_get(self, url, **kw):
        return _R()

    async def fake_put(self, url, **kw):
        return _R()

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    monkeypatch.setattr(httpx.AsyncClient, "put", fake_put)

    detail = await ws.fetch_issue_detail("123", api_token="tok")
    assert detail["title"] == "T"
    assert await ws.resolve_in_sentry("123", api_token="tok", org_slug="org") is True
    # y el modo legacy por env sigue funcionando (sin kwargs)
    detail_legacy = await ws.fetch_issue_detail("123")
    assert detail_legacy["title"] == "T"
    assert isinstance(await ws.fetch_sentry_issues("tok", "org", "proj"), list)


@pytest.mark.asyncio
async def test_resolve_in_sentry_no_token(monkeypatch):
    monkeypatch.setattr("app.config.settings.sentry_api_token", "")
    assert await ws.resolve_in_sentry("1") is False


@pytest.mark.asyncio
async def test_resolve_in_sentry_429_retries(monkeypatch):
    import httpx
    calls = []

    class _R429:
        status_code = 429
        headers = {"Retry-After": "0"}

    class _ROK:
        status_code = 200
        headers = {}

    async def fake_put(self, url, **kw):
        calls.append(url)
        return _R429() if len(calls) == 1 else _ROK()

    monkeypatch.setattr(httpx.AsyncClient, "put", fake_put)
    assert await ws.resolve_in_sentry("9", api_token="t", org_slug="o",
                                      base_url="https://sh.example.com") is True
    assert len(calls) == 2
    assert calls[0].startswith("https://sh.example.com/api/0/organizations/o/")
