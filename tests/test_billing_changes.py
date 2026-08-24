"""Plan changes: what gets charged, who may ask, and what the screen may claim."""

from app.billing import paddle, service


def _price(plan_code, billing_period, price_id="pri_x"):
    return paddle.PlanPrice(price_id, plan_code, billing_period, "800", "USD")


def test_tier_upgrade_charges_the_difference_now():
    """More capacity now means paying for it now."""
    current = _price("solo", "monthly")
    target = _price("studio", "monthly")
    assert service.proration_for(current, target) == paddle.PRORATION_UPGRADE


def test_tier_downgrade_credits_at_renewal():
    """Never refund mid-period for a downgrade: credit it at renewal."""
    assert service.proration_for(
        _price("studio", "monthly"), _price("solo", "monthly")
    ) == paddle.PRORATION_DOWNGRADE


def test_monthly_to_yearly_charges_now():
    """A year is a much larger payment; charging now is the honest moment."""
    assert service.proration_for(
        _price("solo", "monthly"), _price("solo", "yearly")
    ) == paddle.PRORATION_UPGRADE


def test_yearly_to_monthly_waits_for_renewal():
    """Let the paid year run out rather than unwinding it."""
    assert service.proration_for(
        _price("solo", "yearly"), _price("solo", "monthly")
    ) == paddle.PRORATION_DOWNGRADE


def test_tier_change_wins_over_term_change():
    """Studio yearly to Solo monthly is a downgrade even though the term also
    shortens: the smaller of the two must not be charged immediately."""
    assert service.proration_for(
        _price("studio", "yearly"), _price("solo", "monthly")
    ) == paddle.PRORATION_DOWNGRADE


def test_tier_upgrade_overrides_term_downgrade():
    """Solo yearly to Studio monthly is an upgrade: tier wins even when the
    term shortens. This proves tier is checked before term in the decision."""
    assert service.proration_for(
        _price("solo", "yearly"), _price("studio", "monthly")
    ) == paddle.PRORATION_UPGRADE


def test_unknown_plan_code_credits_at_renewal():
    """When Paddle omits items from the subscription response, plan_code is
    empty. Never charge on a guess; credit at renewal instead."""
    current = paddle.SubscriptionView(
        status="active",
        price_id="pri_current",
        plan_code="",
        billing_period="monthly",
        next_billed_at=None,
        scheduled_action=None,
        scheduled_at=None,
        update_payment_method_url=None,
        cancel_url=None,
    )
    target = _price("studio", "monthly")
    assert service.proration_for(current, target) == paddle.PRORATION_DOWNGRADE
