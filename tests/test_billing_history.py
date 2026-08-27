"""Billing history, resuming a cancellation, and invoice access.

The history is the only screen that reads both sides of the money, and the
invoice route is the only one that takes a Paddle id from the URL, so both get
their own tests rather than riding along with the plan-change ones.
"""

import json
import uuid
from datetime import datetime, timezone

import httpx
import pytest
from httpx import AsyncClient

from app.accounts.plans import FREE, apply_paddle_subscription
from app.accounts.service import create_account
from app.billing import paddle
from app.config import settings

_KEY = "pdl_sdbx_apikey_x"


async def _paid_owner(db, plan_code="solo"):
    suffix = uuid.uuid4().hex[:8]
    account, owner = await create_account(
        db, f"Hist {suffix}", f"hist-{suffix}@test.cl", "Owner", "secret-password",
        plan_code=FREE,
    )
    await apply_paddle_subscription(
        db, account_id=account.id, plan_code=plan_code, paddle_status="active",
        subscription_id=f"sub_{suffix}", customer_id="ctm_x",
        occurred_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
    )
    await db.commit()
    return account, owner


async def _login(client: AsyncClient, email: str) -> None:
    await client.post("/login", data={"email": email, "password": "secret-password"})


def _subscription(**overrides) -> paddle.SubscriptionView:
    fields = {
        "status": "active", "price_id": "pri_x", "plan_code": "solo",
        "billing_period": "monthly", "next_billed_at": None,
        "scheduled_action": None, "scheduled_at": None,
        "update_payment_method_url": None, "cancel_url": None,
    }
    fields.update(overrides)
    return paddle.SubscriptionView(**fields)


# --------------------------------------------------------------------------
# The client: what Paddle's two money resources become
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_history_merges_charges_and_credits_newest_first(monkeypatch):
    """Paddle splits history across /transactions and /adjustments, and a
    downgrade writes to both: a zero-value transaction and the credit that
    carries the actual money. Reading only transactions would show the customer
    a $0.00 line where their money moved."""
    monkeypatch.setattr(settings, "paddle_api_key", _KEY)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/transactions":
            return httpx.Response(200, json={"data": [
                {"id": "txn_a", "status": "completed", "origin": "web",
                 "billed_at": "2026-08-01T10:00:00Z", "currency_code": "USD",
                 "invoice_number": "1-0001",
                 "details": {"totals": {"grand_total": "800"}}},
                {"id": "txn_b", "status": "completed", "origin": "subscription_update",
                 "billed_at": "2026-08-20T10:00:00Z", "currency_code": "USD",
                 "invoice_number": "1-0002",
                 "details": {"totals": {"grand_total": "18010"}}},
            ]})
        return httpx.Response(200, json={"data": [
            {"id": "adj_a", "action": "credit", "status": "approved",
             "created_at": "2026-08-10T10:00:00Z", "currency_code": "USD",
             "credit_note_number": "1-0001-C",
             "totals": {"total": "19198"}},
        ]})

    monkeypatch.setattr(paddle, "_transport", httpx.MockTransport(handler))
    movements = await paddle.list_movements("sub_x")

    assert [(m.kind, m.label) for m in movements] == [
        ("charge", "change"),   # 20 Aug
        ("credit", "credit"),   # 10 Aug
        ("charge", "start"),    #  1 Aug
    ]
    # The credit is the reason the merge exists: it is the biggest number here
    # and it lives in the resource a transactions-only history never reads.
    assert movements[1].amount == "19198"


@pytest.mark.asyncio
async def test_an_unknown_origin_falls_back_to_a_label_that_exists(monkeypatch):
    """t() returns the key itself when a key is missing, so an origin Paddle
    adds later would render the literal "billing.movement.whatever" on a
    customer's billing screen. Normalising in Python is what stops that."""
    monkeypatch.setattr(settings, "paddle_api_key", _KEY)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/transactions":
            return httpx.Response(200, json={"data": [
                {"id": "txn_a", "status": "completed", "origin": "something_new_at_paddle",
                 "billed_at": "2026-08-01T10:00:00Z", "currency_code": "USD",
                 "details": {"totals": {"grand_total": "800"}}},
            ]})
        return httpx.Response(200, json={"data": []})

    monkeypatch.setattr(paddle, "_transport", httpx.MockTransport(handler))
    movements = await paddle.list_movements("sub_x")

    assert movements[0].label == "charge"


@pytest.mark.asyncio
async def test_a_movement_without_a_date_sorts_last_instead_of_crashing(monkeypatch):
    """Every real timestamp here is timezone-aware, and Python refuses to order
    an aware datetime against a naive one. A row with no date at all must sink
    to the bottom, not raise TypeError on the whole screen."""
    monkeypatch.setattr(settings, "paddle_api_key", _KEY)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/transactions":
            return httpx.Response(200, json={"data": [
                {"id": "txn_undated", "status": "completed", "origin": "web",
                 "currency_code": "USD", "invoice_number": "1-0002",
                 "details": {"totals": {"grand_total": "800"}}},
                {"id": "txn_dated", "status": "completed", "origin": "web",
                 "billed_at": "2026-08-01T10:00:00Z", "currency_code": "USD",
                 "invoice_number": "1-0001",
                 "details": {"totals": {"grand_total": "800"}}},
            ]})
        return httpx.Response(200, json={"data": []})

    monkeypatch.setattr(paddle, "_transport", httpx.MockTransport(handler))
    movements = await paddle.list_movements("sub_x")

    assert [m.transaction_id for m in movements] == ["txn_dated", "txn_undated"]


@pytest.mark.asyncio
async def test_resume_sends_an_explicit_null_not_an_omitted_field(monkeypatch):
    """Paddle clears a scheduled change on an explicit null. Omitting the field
    leaves the cancellation exactly where it was, and the screen would then
    report success for something that did not happen."""
    monkeypatch.setattr(settings, "paddle_api_key", _KEY)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": {}})

    monkeypatch.setattr(paddle, "_transport", httpx.MockTransport(handler))
    await paddle.resume_plan("sub_x")

    assert seen["method"] == "PATCH"
    assert seen["body"] == {"scheduled_change": None}


@pytest.mark.asyncio
async def test_invoice_url_refuses_another_subscriptions_transaction(monkeypatch):
    """The transaction id arrives in a URL, so it is attacker-controlled. Paddle
    is the only authority on who owns it, and without this check one owner's
    invoice is one guessed id away from another owner's browser."""
    monkeypatch.setattr(settings, "paddle_api_key", _KEY)
    minted = {"invoice": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/invoice"):
            minted["invoice"] = True
            return httpx.Response(200, json={"data": {"url": "https://paddle.example/pdf"}})
        return httpx.Response(200, json={"data": {
            "id": "txn_someone_else", "subscription_id": "sub_other",
        }})

    monkeypatch.setattr(paddle, "_transport", httpx.MockTransport(handler))
    url = await paddle.invoice_url("txn_someone_else", "sub_mine")

    assert url is None
    # Not merely "returned None": the link must never be minted at all.
    assert minted["invoice"] is False


@pytest.mark.asyncio
async def test_invoice_url_mints_a_link_for_its_own_transaction(monkeypatch):
    monkeypatch.setattr(settings, "paddle_api_key", _KEY)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/invoice"):
            return httpx.Response(200, json={"data": {"url": "https://paddle.example/pdf"}})
        return httpx.Response(200, json={"data": {
            "id": "txn_mine", "subscription_id": "sub_mine",
        }})

    monkeypatch.setattr(paddle, "_transport", httpx.MockTransport(handler))
    assert await paddle.invoice_url("txn_mine", "sub_mine") == "https://paddle.example/pdf"


# --------------------------------------------------------------------------
# The screen and the routes
# --------------------------------------------------------------------------


def _movement(**overrides) -> paddle.Movement:
    fields = {
        "kind": "charge", "label": "start",
        "occurred_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
        "amount": "800", "currency_code": "USD", "state": "settled",
        "reference": "1-0001", "transaction_id": "txn_" + "a" * 22,
    }
    fields.update(overrides)
    return paddle.Movement(**fields)


@pytest.mark.asyncio
async def test_history_renders_a_credit_as_money_returned(client: AsyncClient, db, monkeypatch):
    """A credit and a charge of the same size must not read alike: without the
    sign, a refund on the screen looks exactly like another payment taken."""
    monkeypatch.setattr(settings, "paddle_api_key", _KEY)
    _account, owner = await _paid_owner(db)
    await _login(client, owner.email)

    async def detail(_subscription_id):
        return _subscription()

    async def movements(_subscription_id):
        return [_movement(kind="credit", label="credit", amount="19198", transaction_id=None)]

    monkeypatch.setattr(paddle, "get_subscription", detail)
    monkeypatch.setattr(paddle, "list_movements", movements)

    r = await client.get("/billing")
    assert r.status_code == 200
    assert "Billing history" in r.text
    assert "-USD 191.98" in r.text


@pytest.mark.asyncio
async def test_history_outage_leaves_the_rest_of_the_screen_standing(
    client: AsyncClient, db, monkeypatch,
):
    """History is the least important thing on this page. Its own try block is
    what stops an outage in it from taking the current plan down with it."""
    monkeypatch.setattr(settings, "paddle_api_key", _KEY)
    _account, owner = await _paid_owner(db)
    await _login(client, owner.email)

    async def detail(_subscription_id):
        return _subscription(next_billed_at=datetime(2026, 9, 1, tzinfo=timezone.utc))

    async def boom(_subscription_id):
        raise paddle.PaddleError("down")

    monkeypatch.setattr(paddle, "get_subscription", detail)
    monkeypatch.setattr(paddle, "list_movements", boom)

    r = await client.get("/billing")
    assert r.status_code == 200
    assert "Next billed on" in r.text  # the plan card survived
    assert "No movements yet." in r.text


@pytest.mark.asyncio
async def test_cancelling_asks_before_it_fires(client: AsyncClient, db, monkeypatch):
    """Cancelling was a single click straight to Paddle. It now carries the
    app's confirm dialog, and the sentence names the date access ends: people
    cancel expecting to lose access today, and are owed the news that they
    do not."""
    monkeypatch.setattr(settings, "paddle_api_key", _KEY)
    _account, owner = await _paid_owner(db)
    await _login(client, owner.email)

    async def detail(_subscription_id):
        return _subscription(next_billed_at=datetime(2026, 9, 1, tzinfo=timezone.utc))

    async def movements(_subscription_id):
        return []

    monkeypatch.setattr(paddle, "get_subscription", detail)
    monkeypatch.setattr(paddle, "list_movements", movements)

    r = await client.get("/billing")
    assert "data-confirm=" in r.text
    assert "You keep full access until" in r.text


@pytest.mark.asyncio
async def test_resume_is_offered_only_once_a_cancellation_is_scheduled(
    client: AsyncClient, db, monkeypatch,
):
    """Offering "keep my plan" to someone who never cancelled is noise; not
    offering it to someone who did is the dead end this replaces."""
    monkeypatch.setattr(settings, "paddle_api_key", _KEY)
    _account, owner = await _paid_owner(db)
    await _login(client, owner.email)

    scheduled: dict[str, str | None] = {"action": None}

    async def detail(_subscription_id):
        return _subscription(
            next_billed_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
            scheduled_action=scheduled["action"],
            scheduled_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )

    async def movements(_subscription_id):
        return []

    monkeypatch.setattr(paddle, "get_subscription", detail)
    monkeypatch.setattr(paddle, "list_movements", movements)

    assert "Keep my plan" not in (await client.get("/billing")).text

    scheduled["action"] = "cancel"
    body = (await client.get("/billing")).text
    assert "Keep my plan" in body
    # The cancel control is gone: there is nothing left to cancel.
    assert "/ui/billing/cancel" not in body


@pytest.mark.asyncio
async def test_resume_calls_paddle_and_writes_nothing_locally(
    client: AsyncClient, db, monkeypatch,
):
    """Same contract as every other action here: Paddle is asked, the webhook
    owns the mirror."""
    from app.accounts.plans import subscription_for

    monkeypatch.setattr(settings, "paddle_api_key", _KEY)
    account, owner = await _paid_owner(db)
    account_id = account.id
    await _login(client, owner.email)
    called = {}

    async def resume(subscription_id):
        called["id"] = subscription_id

    monkeypatch.setattr(paddle, "resume_plan", resume)
    r = await client.post("/ui/billing/resume")

    assert r.status_code in (200, 204)
    assert called["id"].startswith("sub_")

    db.expire_all()
    row = await subscription_for(db, account_id)
    assert (row.plan_code, row.status) == ("solo", "active")


@pytest.mark.asyncio
async def test_resume_reports_a_paddle_outage_as_a_provider_error(
    client: AsyncClient, db, monkeypatch,
):
    monkeypatch.setattr(settings, "paddle_api_key", _KEY)
    _account, owner = await _paid_owner(db)
    await _login(client, owner.email)

    async def boom(_subscription_id):
        raise paddle.PaddleError("down")

    monkeypatch.setattr(paddle, "resume_plan", boom)
    r = await client.post("/ui/billing/resume")

    assert r.status_code == 502


@pytest.mark.asyncio
async def test_invoice_route_refuses_a_malformed_id_without_calling_paddle(
    client: AsyncClient, db, monkeypatch,
):
    """The shape is checked before anything leaves the process, so a hand-typed
    id never becomes an outbound request. The unmocked-call guard in conftest is
    what proves no request was made."""
    monkeypatch.setattr(settings, "paddle_api_key", _KEY)
    _account, owner = await _paid_owner(db)
    await _login(client, owner.email)

    r = await client.get("/ui/billing/invoice/not-a-transaction")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_invoice_route_404s_on_a_transaction_that_is_not_ours(
    client: AsyncClient, db, monkeypatch,
):
    """A foreign transaction is a 404, not a 502: nothing is wrong with Paddle,
    and the answer must not distinguish "exists elsewhere" from "does not
    exist"."""
    monkeypatch.setattr(settings, "paddle_api_key", _KEY)
    _account, owner = await _paid_owner(db)
    await _login(client, owner.email)

    async def refuse(_transaction_id, _subscription_id):
        return None

    monkeypatch.setattr(paddle, "invoice_url", refuse)
    r = await client.get("/ui/billing/invoice/txn_" + "a" * 22)

    assert r.status_code == 404


@pytest.mark.asyncio
async def test_invoice_route_redirects_to_the_signed_link(
    client: AsyncClient, db, monkeypatch,
):
    monkeypatch.setattr(settings, "paddle_api_key", _KEY)
    _account, owner = await _paid_owner(db)
    await _login(client, owner.email)

    async def mint(_transaction_id, _subscription_id):
        return "https://paddle.example/invoice.pdf"

    monkeypatch.setattr(paddle, "invoice_url", mint)
    r = await client.get("/ui/billing/invoice/txn_" + "a" * 22, follow_redirects=False)

    assert r.status_code == 303
    assert r.headers["location"] == "https://paddle.example/invoice.pdf"


@pytest.mark.asyncio
async def test_a_member_cannot_reach_the_invoice_route(client: AsyncClient, db, monkeypatch):
    """Invoices are the owner's, not every collaborator's."""
    monkeypatch.setattr(settings, "paddle_api_key", _KEY)
    account, _owner = await _paid_owner(db)
    from app.auth.service import create_user

    suffix = uuid.uuid4().hex[:8]
    member = await create_user(
        db, f"member-{suffix}@test.cl", "Member", "secret-password",
        account_id=account.id, account_role="member",
    )
    await db.commit()
    await _login(client, member.email)

    r = await client.get(
        "/ui/billing/invoice/txn_" + "a" * 22, follow_redirects=False,
    )
    assert r.status_code in (302, 303, 403, 404)


@pytest.mark.asyncio
async def test_success_lands_as_a_banner_not_only_a_corner_toast(
    client: AsyncClient, db, monkeypatch,
):
    """Every action here answers 204 + HX-Refresh, so the customer returns to a
    page that still shows the old plan while the webhook is in flight. A small
    toast in the bottom-right corner was the only sign anything had happened.
    Popping the flash in the router is also what stops base.html rendering a
    second copy of the same message."""
    monkeypatch.setattr(settings, "paddle_api_key", _KEY)
    _account, owner = await _paid_owner(db)
    await _login(client, owner.email)

    async def resume(_subscription_id):
        return None

    async def detail(_subscription_id):
        return _subscription()

    async def movements(_subscription_id):
        return []

    monkeypatch.setattr(paddle, "resume_plan", resume)
    monkeypatch.setattr(paddle, "get_subscription", detail)
    monkeypatch.setattr(paddle, "list_movements", movements)

    await client.post("/ui/billing/resume")
    body = (await client.get("/billing")).text

    assert "Cancellation removed. Your plan continues as before." in body
    assert 'role="status"' in body
    # The corner toast must not also fire: one event, one message.
    assert "data-initial-message" not in body


@pytest.mark.asyncio
async def test_billing_dates_carry_no_meaningless_time(
    client: AsyncClient, db, monkeypatch,
):
    """Renewal and invoice dates are date-level facts. The shared filter appends
    "00:00" to every one of them, which reads as precision this data does not
    have, on the one screen where precision is read as a claim."""
    monkeypatch.setattr(settings, "paddle_api_key", _KEY)
    _account, owner = await _paid_owner(db)
    await _login(client, owner.email)

    async def detail(_subscription_id):
        return _subscription(next_billed_at=datetime(2027, 8, 26, tzinfo=timezone.utc))

    async def movements(_subscription_id):
        return [_movement()]

    monkeypatch.setattr(paddle, "get_subscription", detail)
    monkeypatch.setattr(paddle, "list_movements", movements)

    body = (await client.get("/billing")).text
    assert "2027-08-26" in body
    assert "00:00" not in body


@pytest.mark.asyncio
async def test_a_downgrade_credit_is_read_off_the_transaction(monkeypatch):
    """The bug this test exists for: a proration credit is NOT an adjustment.

    Paddle's proration engine parks it on the customer's balance at change time
    and writes no adjustment record. Verified against a real sandbox downgrade,
    which produced exactly this shape and an EMPTY /adjustments list. Reading
    only grand_total rendered the change as "USD 0.00" and silently dropped the
    191.95 the customer got back, on the screen whose whole job is to say where
    their money went."""
    monkeypatch.setattr(settings, "paddle_api_key", _KEY)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/transactions":
            return httpx.Response(200, json={"data": [
                {"id": "txn_down", "status": "completed", "origin": "subscription_update",
                 "billed_at": "2026-08-27T03:12:07Z", "currency_code": "USD",
                 "invoice_number": "1-0004",
                 "details": {"totals": {
                     "subtotal": "-16131", "tax": "-3064", "total": "-19195",
                     "grand_total": "0", "credit": "0", "credit_to_balance": "19195",
                 }}},
            ]})
        return httpx.Response(200, json={"data": []})

    monkeypatch.setattr(paddle, "_transport", httpx.MockTransport(handler))
    movements = await paddle.list_movements("sub_x")

    assert len(movements) == 1
    assert movements[0].kind == "credit"
    assert movements[0].amount == "19195"
    # The origin label survives, so the row says WHY the money moved rather
    # than a bare "Credit".
    assert movements[0].label == "change"


@pytest.mark.asyncio
async def test_a_card_verification_transaction_is_not_a_movement(monkeypatch):
    """Changing a payment method writes a transaction that moves nothing on
    either side and carries no billed_at. Listed, it reads as "USD 0.00,
    pending" with a blank date, on a screen where every line is money."""
    monkeypatch.setattr(settings, "paddle_api_key", _KEY)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/transactions":
            return httpx.Response(200, json={"data": [
                {"id": "txn_card", "status": "ready",
                 "origin": "subscription_payment_method_change",
                 "billed_at": None, "currency_code": "USD",
                 "details": {"totals": {"grand_total": "0", "credit_to_balance": "0"}}},
                {"id": "txn_real", "status": "completed", "origin": "web",
                 "billed_at": "2026-08-26T21:25:30Z", "currency_code": "USD",
                 "invoice_number": "1-0001",
                 "details": {"totals": {"grand_total": "800"}}},
            ]})
        return httpx.Response(200, json={"data": []})

    monkeypatch.setattr(paddle, "_transport", httpx.MockTransport(handler))
    movements = await paddle.list_movements("sub_x")

    assert [m.transaction_id for m in movements] == ["txn_real"]


@pytest.mark.asyncio
async def test_no_invoice_link_when_paddle_issued_no_invoice(monkeypatch):
    """The invoice route can only fail for a transaction with no invoice, so the
    row must not offer a link to it."""
    monkeypatch.setattr(settings, "paddle_api_key", _KEY)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/transactions":
            return httpx.Response(200, json={"data": [
                {"id": "txn_no_invoice", "status": "completed", "origin": "web",
                 "billed_at": "2026-08-26T21:25:30Z", "currency_code": "USD",
                 "details": {"totals": {"grand_total": "800"}}},
            ]})
        return httpx.Response(200, json={"data": []})

    monkeypatch.setattr(paddle, "_transport", httpx.MockTransport(handler))
    movements = await paddle.list_movements("sub_x")

    assert len(movements) == 1
    assert movements[0].transaction_id is None
