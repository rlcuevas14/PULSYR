"""The owner-facing billing screen.

Nothing here writes a plan. Entitlements are read from the local mirror and
billing detail is read live from Paddle, because a pending cancellation copied
into our database is a pending cancellation that can go stale.
"""

import logging
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException

from app.accounts import plans
from app.auth.deps import require_owner, require_owner_ui
from app.auth.models import User
from app.billing import paddle
from app.billing import service as billing_service
from app.config import settings
from app.database import get_db
from app.i18n import resolve_lang
from app.i18n import t as _t
from app.templates_config import templates
from app.ui.flash import flash_success

logger = logging.getLogger("pulsyr.billing")

router = APIRouter(tags=["billing"])

_TXN_RE = re.compile(r"^txn_[a-z0-9]{20,32}$")


@router.get("/billing", response_class=HTMLResponse)
async def billing_screen(
    request: Request,
    user: User = Depends(require_owner_ui),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    subscription = await plans.subscription_for(db, user.account_id)
    plan_code = subscription.plan_code if subscription else plans.SELF_HOSTED
    if plan_code == plans.SELF_HOSTED:
        raise HTTPException(status_code=404)

    limits = plans.limits_for(plan_code)
    usage = await plans.usage_for(db, user.account_id)

    prices: list[paddle.PlanPrice] = []
    if paddle.configured() and settings.paddle_client_token:
        try:
            prices = await paddle.list_plan_prices()
        except paddle.PaddleError:
            logger.warning("plan catalog unavailable for account %s", user.account_id)

    # Whether this account already pays us is a fact in the local mirror, which is
    # always readable, never an inference from whether the live read succeeded. A
    # Paddle outage that left `detail` None used to reopen the buy buttons for a
    # paying account, and a second checkout there is a second live subscription.
    has_subscription = bool(subscription and subscription.paddle_subscription_id)

    detail = None
    detail_failed = False
    if paddle.configured() and subscription and subscription.paddle_subscription_id:
        try:
            detail = await paddle.get_subscription(subscription.paddle_subscription_id)
        except paddle.PaddleError:
            detail_failed = True
            logger.warning("billing detail unavailable for account %s", user.account_id)

    # Formatted here rather than in the template so a card and the confirmation
    # screen state a price the same way, through the one helper that knows how
    # Paddle sends amounts.
    price_labels = {
        price.price_id: _money(price.amount, price.currency_code) for price in prices
    }

    # One entry per tier holding both of its terms, so the screen can show three
    # plans with a period switch rather than one card per price. Four loose cards
    # is not how anyone reads a pricing table.
    tiers: dict[str, dict[str, paddle.PlanPrice]] = {}
    for price in prices:
        tiers.setdefault(price.plan_code, {})[price.billing_period] = price
    # Ordered by the same PLAN_RANK that decides upgrade from downgrade, so the
    # columns read in capacity order and there is only one place that knows it.
    tiers = dict(sorted(tiers.items(), key=lambda kv: billing_service.PLAN_RANK.get(kv[0], 0)))

    # Derived from the catalog rather than written as copy: if the yearly price
    # ever changes, the badge changes with it instead of quietly lying.
    months_free: dict[str, int] = {}
    for code, terms in tiers.items():
        monthly, yearly = terms.get("monthly"), terms.get("yearly")
        if not monthly or not yearly:
            continue
        try:
            saved = int(monthly.amount) * 12 - int(yearly.amount)
            free = round(saved / int(monthly.amount))
        except (ValueError, ZeroDivisionError):
            continue
        if free > 0:
            months_free[code] = free

    intent = request.session.pop("billing_intent", None) or {}
    preselected_price_id = next(
        (
            price.price_id
            for price in prices
            if price.plan_code == intent.get("plan")
            and price.billing_period == intent.get("cycle")
        ),
        None,
    )

    return templates.TemplateResponse(request, "billing.html", {
        "user": user,
        "plan_code": plan_code,
        "status": subscription.status if subscription else "active",
        "limits": limits,
        "usage": usage,
        "detail": detail,
        "detail_failed": detail_failed,
        "has_subscription": has_subscription,
        "prices": prices,
        "tiers": tiers,
        "months_free": months_free,
        "price_labels": price_labels,
        "preselected_price_id": preselected_price_id,
        "account_id": str(user.account_id),
        "user_email": user.email,
        "paddle_token": settings.paddle_client_token,
        "paddle_environment": settings.paddle_environment,
        "transaction_id": "",
    })


@router.get("/billing/checkout", response_class=HTMLResponse)
async def billing_checkout(
    request: Request, _ptxn: str = Query(default=""),
) -> HTMLResponse:
    """Paddle's default payment link target. Deliberately session-free: this is
    where a payment-recovery email lands, and the transaction id is the
    capability. The page renders nothing belonging to the account."""
    # No client token means nobody sells anything from this install. Serving the
    # page anyway would have a self-hosted deployment fetch a third-party script
    # on a public URL for a checkout that cannot exist.
    if not settings.paddle_client_token:
        raise HTTPException(status_code=404)
    if _ptxn and not _TXN_RE.match(_ptxn):
        raise HTTPException(status_code=400, detail="invalid transaction id")
    return templates.TemplateResponse(request, "billing_checkout.html", {
        "transaction_id": _ptxn,
        "paddle_token": settings.paddle_client_token,
        "paddle_environment": settings.paddle_environment,
    })


def _money(amount: str | None, currency: str) -> str | None:
    """Paddle sends the lowest denomination as a string. Two decimals covers
    every currency the catalog uses; a zero-decimal currency such as CLP or JPY
    would need its own case here.

    Anything that is not an integer string returns None, the same as a missing
    amount: the caller's template already has a branch for "no figure to show",
    and falling into it beats a 500 on a page about money.
    """
    if amount is None:
        return None
    try:
        return f"{currency} {int(amount) / 100:.2f}"
    except ValueError:
        return None


async def _resolve_target(price_id: str) -> paddle.PlanPrice:
    for price in await paddle.list_plan_prices():
        if price.price_id == price_id:
            return price
    raise HTTPException(status_code=400, detail="unknown price")


@contextmanager
def _paddle_outage_is_502(action: str, account_id: uuid.UUID) -> Iterator[None]:
    """One posture for one dependency: every billing action answers a Paddle
    outage the same way, wherever in the interaction it happens.

    HTTPException is deliberately not caught. `_resolve_target` raises 400 for a
    price that is not in the catalog, and an unknown price is a bad request, not
    a provider outage; reporting it as 502 would tell the owner to come back
    later for something that will never work.
    """
    try:
        yield
    except paddle.PaddleError as exc:
        logger.warning("%s failed for account %s: %s", action, account_id, exc)
        raise HTTPException(status_code=502, detail="billing_provider_error") from exc


@router.get("/ui/billing/confirm", response_class=HTMLResponse)
async def billing_confirm(
    request: Request,
    price_id: str = Query(...),
    user: User = Depends(require_owner_ui),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    subscription = await plans.subscription_for(db, user.account_id)
    if subscription is None or not subscription.paddle_subscription_id:
        raise HTTPException(status_code=400, detail="no subscription to change")

    with _paddle_outage_is_502("plan preview", user.account_id):
        target = await _resolve_target(price_id)
        current = await paddle.get_subscription(subscription.paddle_subscription_id)
        proration = billing_service.proration_for(current, target)
        preview = await paddle.preview_change(
            subscription.paddle_subscription_id, target.price_id, proration
        )

    recurring = _money(preview.recurring_amount, preview.currency_code)
    if recurring is None:
        # A confirmation screen that cannot state the recurring price must not
        # render at all: a customer confirming a charge should never see the
        # literal string "None" where a price belongs.
        logger.warning(
            "plan preview for account %s returned an unparseable recurring amount",
            user.account_id,
        )
        raise HTTPException(status_code=502, detail="billing_provider_error")

    return templates.TemplateResponse(request, "billing_confirm.html", {
        "user": user,
        "target": target,
        "immediate": _money(preview.immediate_amount, preview.currency_code),
        "recurring": recurring,
        "next_billed_at": preview.next_billed_at,
        "is_downgrade": proration == paddle.PRORATION_DOWNGRADE,
    })


@router.post("/ui/billing/change")
async def billing_change(
    request: Request,
    price_id: str = Form(...),
    user: User = Depends(require_owner),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Ask Paddle to change the plan, then say so honestly.

    Nothing about the plan is written here. The subscription id comes from the
    owner's own row rather than the request, so no owner can act on another
    tenant's subscription by guessing an id.
    """
    subscription = await plans.subscription_for(db, user.account_id)
    if subscription is None or not subscription.paddle_subscription_id:
        raise HTTPException(status_code=400, detail="no subscription to change")

    with _paddle_outage_is_502("plan change", user.account_id):
        target = await _resolve_target(price_id)
        current = await paddle.get_subscription(subscription.paddle_subscription_id)
        proration = billing_service.proration_for(current, target)
        await paddle.change_plan(
            subscription.paddle_subscription_id, target.price_id, proration
        )

    # Deliberately not the new plan name: the webhook has not landed yet and
    # painting it now would contradict itself if the change did not stick.
    flash_success(request, message=_t("billing.change_submitted", resolve_lang(request)))
    return Response(status_code=204, headers={"HX-Refresh": "true"})


@router.post("/ui/billing/cancel")
async def billing_cancel(
    request: Request,
    user: User = Depends(require_owner),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Schedule a cancellation for the end of the paid period.

    Paddle does not flip the subscription to canceled immediately; it keeps it
    active and records a scheduled change, and only the webhook writes the
    eventual status. Nothing here touches the local mirror.
    """
    subscription = await plans.subscription_for(db, user.account_id)
    if subscription is None or not subscription.paddle_subscription_id:
        raise HTTPException(status_code=400, detail="no subscription to cancel")
    with _paddle_outage_is_502("cancellation", user.account_id):
        await paddle.cancel_subscription(subscription.paddle_subscription_id)

    flash_success(request, message=_t("billing.cancel_submitted", resolve_lang(request)))
    return Response(status_code=204, headers={"HX-Refresh": "true"})
