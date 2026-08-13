"""v0021: hosted account plans and durable OAuth identities.

Existing accounts are private/self-hosted and stay unlimited. Only future public
signup explicitly chooses the Free tier in application code.
"""

from alembic import op

revision = "v0021"
down_revision = "v0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE account_subscriptions (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            account_id  uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
            plan_code   TEXT NOT NULL CHECK (plan_code IN ('free','self_hosted')),
            status      TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active','suspended','canceled')),
            started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            onboarding_completed_at TIMESTAMPTZ,
            CONSTRAINT account_subscriptions_account_uniq UNIQUE(account_id)
        )
        """
    )
    op.execute(
        """
        INSERT INTO account_subscriptions (account_id, plan_code, status)
        SELECT id, 'self_hosted', 'active' FROM accounts
        """
    )
    op.execute("ALTER TABLE users ADD COLUMN terms_accepted_at TIMESTAMPTZ")
    op.execute("ALTER TABLE users ADD COLUMN terms_version TEXT")
    op.execute(
        """
        CREATE TABLE oauth_identities (
            id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id        uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            provider       TEXT NOT NULL,
            subject        TEXT NOT NULL,
            email_at_link  TEXT NOT NULL,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_login_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT oauth_identities_provider_subject_uniq UNIQUE(provider, subject),
            CONSTRAINT oauth_identities_user_provider_uniq UNIQUE(user_id, provider)
        )
        """
    )
    # Authentication treats email case-insensitively. Refuse a pathological legacy
    # duplicate rather than keep a race window that can produce two identities.
    op.execute("CREATE UNIQUE INDEX users_email_lower_uniq ON users (lower(email))")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS users_email_lower_uniq")
    op.execute("DROP TABLE IF EXISTS oauth_identities")
    op.execute("DROP TABLE IF EXISTS account_subscriptions")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS terms_version")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS terms_accepted_at")
