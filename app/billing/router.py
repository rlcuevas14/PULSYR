"""The owner-facing billing screen.

Nothing here writes a plan. Entitlements are read from the local mirror and
billing detail is read live from Paddle, because a pending cancellation copied
into our database is a pending cancellation that can go stale.
"""

import logging
import re

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException

from app.accounts import plans
from app.auth.deps import require_owner
from app.auth.models import User
from app.billing import paddle
from app.config import settings
from app.database import get_db
from app.templates_config import templates

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
