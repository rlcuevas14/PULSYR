"""The owner-facing billing screen.

Nothing here writes a plan. Entitlements are read from the local mirror and
billing detail is read live from Paddle, because a pending cancellation copied
into our database is a pending cancellation that can go stale.
"""

import logging
import re

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException

from app.accounts import plans
from app.auth.deps import require_owner
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
    user: User = Depends(require_owner),
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

    detail = None
    detail_failed = False
    if paddle.configured() and subscription and subscription.paddle_subscription_id:
        try:
            detail = await paddle.get_subscription(subscription.paddle_subscription_id)
        except paddle.PaddleError:
            detail_failed = True
            logger.warning("billing detail unavailable for account %s", user.account_id)

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
        "actions_available": paddle.configured(),
        "prices": prices,
        "preselected_price_id": preselected_price_id,
        "account_id": str(user.account_id),
        "user_email": user.email,
        "paddle_token": settings.paddle_client_token,
        "paddle_environment": settings.paddle_environment,
        "transaction_id": "",
    })


@router.get("/billing/intent")
async def billing_intent(request: Request) -> dict[str, str | None]:
    """What the visitor picked on the public pricing page, if anything. Read by
    the billing screen to preselect a plan, and by the test suite."""
    intent = request.session.get("billing_intent") or {}
    return {"plan": intent.get("plan"), "cycle": intent.get("cycle")}


@router.get("/billing/checkout", response_class=HTMLResponse)
async def billing_checkout(
    request: Request, _ptxn: str = Query(default=""),
) -> HTMLResponse:
    """Paddle's default payment link target. Deliberately session-free: this is
    where a payment-recovery email lands, and the transaction id is the
    capability. The page renders nothing belonging to the account."""
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
    would need its own case here."""
    if amount is None:
        return None
    return f"{currency} {int(amount) / 100:.2f}"


async def _resolve_target(price_id: str) -> paddle.PlanPrice:
    for price in await paddle.list_plan_prices():
        if price.price_id == price_id:
            return price
    raise HTTPException(status_code=400, detail="unknown price")


@router.get("/ui/billing/confirm", response_class=HTMLResponse)
async def billing_confirm(
    request: Request,
    price_id: str = Query(...),
    user: User = Depends(require_owner),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    subscription = await plans.subscription_for(db, user.account_id)
    if subscription is None or not subscription.paddle_subscription_id:
        raise HTTPException(status_code=400, detail="no subscription to change")

    target = await _resolve_target(price_id)
    current = await paddle.get_subscription(subscription.paddle_subscription_id)
    proration = billing_service.proration_for(current, target)
    preview = await paddle.preview_change(
        subscription.paddle_subscription_id, target.price_id, proration
    )

    return templates.TemplateResponse(request, "billing_confirm.html", {
        "user": user,
        "target": target,
        "immediate": _money(preview.immediate_amount, preview.currency_code),
        "recurring": _money(preview.recurring_amount, preview.currency_code),
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

    target = await _resolve_target(price_id)
    current = await paddle.get_subscription(subscription.paddle_subscription_id)
    proration = billing_service.proration_for(current, target)
    try:
        await paddle.change_plan(
            subscription.paddle_subscription_id, target.price_id, proration
        )
    except paddle.PaddleError as exc:
        logger.warning("plan change failed for account %s: %s", user.account_id, exc)
        raise HTTPException(status_code=502, detail="billing_provider_error") from exc

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
    try:
        await paddle.cancel_subscription(subscription.paddle_subscription_id)
    except paddle.PaddleError as exc:
        logger.warning("cancellation failed for account %s: %s", user.account_id, exc)
        raise HTTPException(status_code=502, detail="billing_provider_error") from exc

    flash_success(request, message=_t("billing.cancel_submitted", resolve_lang(request)))
    return Response(status_code=204, headers={"HX-Refresh": "true"})
