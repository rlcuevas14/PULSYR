"""v0022: shared privacy-preserving rate-limit buckets."""

from alembic import op

revision = "v0022"
down_revision = "v0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE rate_limit_buckets (
            bucket       TEXT NOT NULL,
            key_hash     VARCHAR(64) NOT NULL,
            window_start TIMESTAMPTZ NOT NULL,
            attempts     INTEGER NOT NULL DEFAULT 1 CHECK (attempts > 0),
            PRIMARY KEY (bucket, key_hash, window_start)
        )
        """
    )
    op.execute(
        "CREATE INDEX rate_limit_buckets_window_idx "
        "ON rate_limit_buckets (window_start)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS rate_limit_buckets")
