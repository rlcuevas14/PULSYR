"""v0025: append-only audit events for incident transitions."""

from alembic import op

revision = "v0025"
down_revision = "v0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE sentry_issue_events (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id      uuid REFERENCES projects(id) ON DELETE CASCADE,
            issue_id        uuid NOT NULL REFERENCES sentry_issues(id) ON DELETE CASCADE,
            actor           VARCHAR(255) NOT NULL,
            action          VARCHAR(60) NOT NULL,
            previous_status VARCHAR(20),
            status          VARCHAR(20),
            note            TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX sentry_issue_events_issue_created_idx "
        "ON sentry_issue_events(issue_id, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sentry_issue_events")
