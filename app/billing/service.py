"""Rules that decide what a plan change costs, kept apart from the HTTP layer
so they can be read and tested as rules."""

from app.billing import paddle

# Ordered by capacity, not by price. A move up this list is an upgrade.
PLAN_RANK: dict[str, int] = {"free": 0, "solo": 1, "studio": 2}
_TERM_RANK: dict[str, int] = {"monthly": 0, "yearly": 1}


def proration_for(
    current: paddle.PlanPrice | paddle.SubscriptionView, target: paddle.PlanPrice
) -> str:
    """Charge immediately only when the customer is getting more.

    Tier decides first: dropping a tier is a downgrade even when the term also
    changes, because charging immediately for a smaller plan would be indefensible.
    Term is the tiebreaker within the same tier, where moving to a year is the
    larger payment and belongs today.
    """
    current_tier = PLAN_RANK.get(current.plan_code, 0)
    target_tier = PLAN_RANK.get(target.plan_code, 0)
    if target_tier != current_tier:
        return paddle.PRORATION_UPGRADE if target_tier > current_tier else paddle.PRORATION_DOWNGRADE

    current_term = _TERM_RANK.get(current.billing_period, 0)
    target_term = _TERM_RANK.get(target.billing_period, 0)
    return paddle.PRORATION_UPGRADE if target_term > current_term else paddle.PRORATION_DOWNGRADE
