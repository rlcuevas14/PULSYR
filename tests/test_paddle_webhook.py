"""Paddle billing webhook: signature, plan mapping, and out-of-order safety.

The signature is the only thing standing between the public internet and a free
paid plan, so it gets the same attention as the money path itself.
"""

import hashlib
import hmac
import json
import time
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.accounts.models import AccountSubscription
from app.accounts.plans import FREE
from app.accounts.service import create_account
from app.config import settings

SECRET = "pdl_ntfset_test_secret"


def _signed(body: bytes, secret: str = SECRET, ts: int | None = None) -> dict[str, str]:
    stamp = str(int(time.time()) if ts is None else ts)
    digest = hmac.new(secret.encode(), stamp.encode() + b":" + body, hashlib.sha256)
    return {
        "paddle-signature": f"ts={stamp};h1={digest.hexdigest()}",
        "content-type": "application/json",
    }


def _event(
    account_id: uuid.UUID,
    *,
    plan_code: str = "solo",
    status: str = "active",
    occurred_at: str = "2026-08-23T12:00:00.000000Z",
    event_type: str = "subscription.created",
    subscription_id: str = "",
) -> bytes:
    subscription_id = subscription_id or f"sub_{uuid.uuid4().hex[:12]}"
    return json.dumps(
        {
            "event_id": "evt_01test",
            "event_type": event_type,
            "occurred_at": occurred_at,
            "data": {
                "id": subscription_id,
                "status": status,
                "customer_id": "ctm_01test",
                "custom_data": {"account_id": str(account_id)},
                "items": [
                    {"price": {"id": "pri_01test", "custom_data": {"plan_code": plan_code}}}
                ],
            },
        }
    ).encode()


async def _account(db):
    suffix = uuid.uuid4().hex[:8]
    account, _owner = await create_account(
        db, f"Paddle {suffix}", f"paddle-{suffix}@test.cl", "Owner", None, plan_code=FREE
    )
    await db.commit()
    return account


async def _subscription(db, account_id) -> AccountSubscription:
    db.expire_all()
    row = await db.scalar(
        select(AccountSubscription).where(AccountSubscription.account_id == account_id)
    )
    assert row is not None
    return row


@pytest.mark.asyncio
async def test_no_secret_configured_answers_503(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "paddle_webhook_secret", "")
    r = await client.post("/webhooks/paddle", json={"event_type": "subscription.created"})
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_wrong_secret_is_rejected(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "paddle_webhook_secret", SECRET)
    body = _event(uuid.uuid4())
    r = await client.post("/webhooks/paddle", content=body, headers=_signed(body, "other"))
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_tampered_body_is_rejected(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "paddle_webhook_secret", SECRET)
    body = _event(uuid.uuid4())
    headers = _signed(body)
    r = await client.post("/webhooks/paddle", content=body + b" ", headers=headers)
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_stale_timestamp_is_rejected(client: AsyncClient, monkeypatch):
    """Replay protection: a correct signature over an old timestamp still fails."""
    monkeypatch.setattr(settings, "paddle_webhook_secret", SECRET)
    body = _event(uuid.uuid4())
    headers = _signed(body, ts=int(time.time()) - 3600)
    r = await client.post("/webhooks/paddle", content=body, headers=headers)
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_unhandled_event_type_is_acknowledged(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "paddle_webhook_secret", SECRET)
    body = json.dumps({"event_type": "transaction.completed", "data": {}}).encode()
    r = await client.post("/webhooks/paddle", content=body, headers=_signed(body))
    assert r.status_code == 200
    assert r.json()["status"] == "ignored"


@pytest.mark.asyncio
async def test_unknown_plan_code_fails_loudly(client: AsyncClient, db, monkeypatch):
    """A paid subscription we cannot map must not be swallowed with a 2xx."""
    monkeypatch.setattr(settings, "paddle_webhook_secret", SECRET)
    account = await _account(db)
    body = _event(account.id, plan_code="enterprise")
    r = await client.post("/webhooks/paddle", content=body, headers=_signed(body))
    assert r.status_code == 422
    assert (await _subscription(db, account.id)).plan_code == FREE


@pytest.mark.asyncio
async def test_missing_account_id_fails_loudly(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "paddle_webhook_secret", SECRET)
    payload = json.loads(_event(uuid.uuid4()))
    payload["data"]["custom_data"] = {}
    body = json.dumps(payload).encode()
    r = await client.post("/webhooks/paddle", content=body, headers=_signed(body))
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_active_subscription_grants_the_paid_plan(client: AsyncClient, db, monkeypatch):
    monkeypatch.setattr(settings, "paddle_webhook_secret", SECRET)
    account = await _account(db)
    body = _event(account.id, plan_code="studio", subscription_id="sub_grant")
    r = await client.post("/webhooks/paddle", content=body, headers=_signed(body))
    assert r.status_code == 200

    row = await _subscription(db, account.id)
    assert row.plan_code == "studio"
    assert row.status == "active"
    assert row.paddle_subscription_id == "sub_grant"
    assert row.paddle_customer_id == "ctm_01test"


@pytest.mark.asyncio
async def test_past_due_keeps_access_during_dunning(client: AsyncClient, db, monkeypatch):
    monkeypatch.setattr(settings, "paddle_webhook_secret", SECRET)
    account = await _account(db)
    body = _event(account.id, status="past_due", event_type="subscription.updated")
    r = await client.post("/webhooks/paddle", content=body, headers=_signed(body))
    assert r.status_code == 200

    row = await _subscription(db, account.id)
    assert (row.plan_code, row.status) == ("solo", "active")


@pytest.mark.asyncio
async def test_status_specific_event_types_are_handled(client: AsyncClient, db, monkeypatch):
    """Paddle signals some states with their own event name rather than
    subscription.updated. Those must land without anyone ticking a box for them."""
    monkeypatch.setattr(settings, "paddle_webhook_secret", SECRET)
    account = await _account(db)
    body = _event(account.id, status="paused", event_type="subscription.paused")
    r = await client.post("/webhooks/paddle", content=body, headers=_signed(body))
    assert r.status_code == 200

    row = await _subscription(db, account.id)
    assert (row.plan_code, row.status) == ("solo", "suspended")


@pytest.mark.asyncio
async def test_cancellation_drops_to_free_instead_of_locking_out(
    client: AsyncClient, db, monkeypatch
):
    """The Terms promise we never delete data over a quota, so a canceled account
    lands on Free rather than on a status that blocks every write."""
    monkeypatch.setattr(settings, "paddle_webhook_secret", SECRET)
    account = await _account(db)
    sub = "sub_cancel"
    active = _event(account.id, occurred_at="2026-08-23T12:00:00.000000Z", subscription_id=sub)
    await client.post("/webhooks/paddle", content=active, headers=_signed(active))

    canceled = _event(
        account.id,
        status="canceled",
        event_type="subscription.canceled",
        occurred_at="2026-09-23T12:00:00.000000Z",
        subscription_id=sub,
    )
    r = await client.post("/webhooks/paddle", content=canceled, headers=_signed(canceled))
    assert r.status_code == 200

    row = await _subscription(db, account.id)
    assert (row.plan_code, row.status) == (FREE, "active")


@pytest.mark.asyncio
async def test_cancellation_clears_the_dead_subscription_id(
    client: AsyncClient, db, monkeypatch
):
    """Dropping to Free must not leave the row pointing at a subscription that no
    longer exists. It did, and every billing route resolves through that column:
    the free account was offered "Switch to this plan" and the confirm route then
    called Paddle with a dead id."""
    monkeypatch.setattr(settings, "paddle_webhook_secret", SECRET)
    account = await _account(db)
    # Captured before the first _subscription() call below expires it: reading an
    # expired attribute as a bare argument crashes with MissingGreenlet, the same
    # pattern the tests in tests/test_billing_changes.py already follow.
    account_id = account.id
    sub = "sub_dead"
    active = _event(account_id, occurred_at="2026-08-23T12:00:00.000000Z", subscription_id=sub)
    await client.post("/webhooks/paddle", content=active, headers=_signed(active))
    assert (await _subscription(db, account_id)).paddle_subscription_id == sub

    canceled = _event(
        account_id,
        status="canceled",
        event_type="subscription.canceled",
        occurred_at="2026-09-23T12:00:00.000000Z",
        subscription_id=sub,
    )
    r = await client.post("/webhooks/paddle", content=canceled, headers=_signed(canceled))
    assert r.status_code == 200

    row = await _subscription(db, account_id)
    assert (row.plan_code, row.status) == (FREE, "active")
    assert row.paddle_subscription_id is None
    # The customer still exists at Paddle, and a future purchase reuses it.
    assert row.paddle_customer_id == "ctm_01test"


@pytest.mark.asyncio
async def test_cancellation_for_a_superseded_subscription_keeps_the_live_pointer(
    client: AsyncClient, db, monkeypatch
):
    """A cancellation event names the subscription it is about. When that id no
    longer matches the one we are mirroring, e.g. an upgrade already replaced it
    with a new subscription, clearing the pointer would erase the id of the
    subscription that is actually live. The account still resolves from
    custom_data, so the plan still drops to Free; only the id is protected."""
    monkeypatch.setattr(settings, "paddle_webhook_secret", SECRET)
    account = await _account(db)
    # Captured before the first _subscription() call below expires it, same
    # pattern as test_cancellation_clears_the_dead_subscription_id above.
    account_id = account.id
    live_sub = "sub_live"
    active = _event(
        account_id, occurred_at="2026-08-23T12:00:00.000000Z", subscription_id=live_sub
    )
    await client.post("/webhooks/paddle", content=active, headers=_signed(active))
    assert (await _subscription(db, account_id)).paddle_subscription_id == live_sub

    canceled = _event(
        account_id,
        status="canceled",
        event_type="subscription.canceled",
        occurred_at="2026-09-23T12:00:00.000000Z",
        subscription_id="sub_superseded",
    )
    r = await client.post("/webhooks/paddle", content=canceled, headers=_signed(canceled))
    assert r.status_code == 200

    row = await _subscription(db, account_id)
    assert (row.plan_code, row.status) == (FREE, "active")
    assert row.paddle_subscription_id == live_sub


@pytest.mark.asyncio
async def test_out_of_order_delivery_cannot_undo_newer_state(
    client: AsyncClient, db, monkeypatch
):
    """A retry of an older event arriving after a newer one must not downgrade."""
    monkeypatch.setattr(settings, "paddle_webhook_secret", SECRET)
    account = await _account(db)
    sub = "sub_order"
    newer = _event(
        account.id,
        plan_code="studio",
        occurred_at="2026-09-23T12:00:00.000000Z",
        subscription_id=sub,
    )
    await client.post("/webhooks/paddle", content=newer, headers=_signed(newer))

    older = _event(
        account.id,
        plan_code="solo",
        event_type="subscription.updated",
        occurred_at="2026-08-23T12:00:00.000000Z",
        subscription_id=sub,
    )
    r = await client.post("/webhooks/paddle", content=older, headers=_signed(older))
    assert r.status_code == 200
    assert r.json()["status"] == "ignored:stale_event"
    assert (await _subscription(db, account.id)).plan_code == "studio"
