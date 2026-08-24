"""v0026: paid plans and the Paddle identifiers that back them.

The webhook is the only writer of these columns. `paddle_event_at` exists so a
retried or out-of-order delivery cannot overwrite newer subscription state with
older state, which would silently move a paying account to the wrong plan.
"""

from alembic import op

revision = "v0026"
down_revision = "v0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE account_subscriptions ADD COLUMN paddle_customer_id TEXT")
    op.execute("ALTER TABLE account_subscriptions ADD COLUMN paddle_subscription_id TEXT")
    op.execute("ALTER TABLE account_subscriptions ADD COLUMN paddle_event_at TIMESTAMPTZ")
    op.execute(
        """
        ALTER TABLE account_subscriptions
        ADD CONSTRAINT account_subscriptions_paddle_sub_uniq UNIQUE(paddle_subscription_id)
        """
    )
    op.execute(
        "ALTER TABLE account_subscriptions DROP CONSTRAINT account_subscriptions_plan_check"
    )
    op.execute(
        """
        ALTER TABLE account_subscriptions
        ADD CONSTRAINT account_subscriptions_plan_check
        CHECK (plan_code IN ('free','self_hosted','solo','studio'))
        """
    )


def downgrade() -> None:
    # A paid account cannot be represented by the older constraint. Refuse rather
    # than silently rewrite someone's plan to free.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM account_subscriptions WHERE plan_code IN ('solo','studio')
            ) THEN
                RAISE EXCEPTION 'v0026 downgrade refused: paid subscriptions exist';
            END IF;
        END $$
        """
    )
    op.execute(
        "ALTER TABLE account_subscriptions DROP CONSTRAINT account_subscriptions_plan_check"
    )
    op.execute(
        """
        ALTER TABLE account_subscriptions
        ADD CONSTRAINT account_subscriptions_plan_check
        CHECK (plan_code IN ('free','self_hosted'))
        """
    )
    op.execute(
        "ALTER TABLE account_subscriptions DROP CONSTRAINT account_subscriptions_paddle_sub_uniq"
    )
    op.execute("ALTER TABLE account_subscriptions DROP COLUMN paddle_event_at")
    op.execute("ALTER TABLE account_subscriptions DROP COLUMN paddle_subscription_id")
    op.execute("ALTER TABLE account_subscriptions DROP COLUMN paddle_customer_id")
