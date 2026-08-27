"""Rules about what a plan change means, kept apart from the HTTP layer so they
can be read and tested as rules."""

from app.billing import paddle

# Ordered by capacity, not by price. A move up this list is an upgrade.
PLAN_RANK: dict[str, int] = {"free": 0, "solo": 1, "studio": 2}
_TERM_RANK: dict[str, int] = {"monthly": 0, "yearly": 1}


def is_downgrade(
    current: paddle.PlanPrice | paddle.SubscriptionView, target: paddle.PlanPrice
) -> bool:
    """Whether the customer is getting less, which decides what we warn them about.

    It no longer decides what we tell Paddle: both directions bill the same way
    (see paddle.PRORATION). What it still decides is the sentence the customer
    reads before confirming, and getting that backwards would be worse than a
    wrong charge, because the charge is visible and the sentence is trusted.

    Tier decides first: dropping a tier is a downgrade even when the term also
    changes. Term is only the tiebreaker within one tier, where moving to a year
    is the larger commitment.
    """
    if current.plan_code not in PLAN_RANK:
        # We could not identify what they are on, which happens when Paddle omits
        # items from a subscription response. Warn rather than stay silent: an
        # unnecessary caution costs nothing, a missing one costs their capacity.
        return True

    current_tier = PLAN_RANK[current.plan_code]
    target_tier = PLAN_RANK.get(target.plan_code, 0)
    if target_tier != current_tier:
        return target_tier < current_tier

    current_term = _TERM_RANK.get(current.billing_period, 0)
    target_term = _TERM_RANK.get(target.billing_period, 0)
    return target_term <= current_term
