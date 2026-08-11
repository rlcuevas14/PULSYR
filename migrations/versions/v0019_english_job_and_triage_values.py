"""v0019: English agent_runs.status + sentry_issues.triage.

The last Spanish enum values that reach the API surface (job status is returned by
/items/{id}/enrich and shown in /admin; triage is returned by pulsyr_incidents and
pulsyr_incident). Same policy as v0011 and v0018: data migrated in place, no
compatibility shim for old clients.

Note triage is nullable, so its CHECK keeps the `IS NULL OR` guard.
"""

from alembic import op

revision = "v0019"
down_revision = "v0018"
branch_labels = None
depends_on = None

RUN_STATUSES = [
    ("pendiente", "pending"),
    ("corriendo", "running"),
]
TRIAGE = [
    ("pendiente", "pending"),
    ("bug-real", "real-bug"),
    ("input-malo", "bad-input"),
    ("ruido", "noise"),
]
_EN_RUN = "'pending','running','ok','error'"
_ES_RUN = "'pendiente','corriendo','ok','error'"
_EN_TRIAGE = "'pending','real-bug','bad-input','3rd-party','noise'"
_ES_TRIAGE = "'pendiente','bug-real','input-malo','3rd-party','ruido'"


def _rename(pairs: list[tuple[str, str]], table: str, col: str, *, reverse: bool = False) -> None:
    for old, new in pairs:
        src, dst = (new, old) if reverse else (old, new)
        op.execute(f"UPDATE {table} SET {col} = '{dst}' WHERE {col} = '{src}'")


def upgrade() -> None:
    # Drop the checks first — renamed rows would violate them mid-flight.
    op.execute("ALTER TABLE agent_runs DROP CONSTRAINT IF EXISTS agent_runs_status_check")
    op.execute("ALTER TABLE sentry_issues DROP CONSTRAINT IF EXISTS sentry_issues_triage_check")

    _rename(RUN_STATUSES, "agent_runs", "status")
    _rename(TRIAGE, "sentry_issues", "triage")

    op.execute("ALTER TABLE agent_runs ALTER COLUMN status SET DEFAULT 'pending'")
    op.execute(
        f"ALTER TABLE agent_runs ADD CONSTRAINT agent_runs_status_check CHECK (status IN ({_EN_RUN}))"
    )
    op.execute(
        "ALTER TABLE sentry_issues ADD CONSTRAINT sentry_issues_triage_check "
        f"CHECK (triage IS NULL OR triage IN ({_EN_TRIAGE}))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE agent_runs DROP CONSTRAINT IF EXISTS agent_runs_status_check")
    op.execute("ALTER TABLE sentry_issues DROP CONSTRAINT IF EXISTS sentry_issues_triage_check")

    _rename(RUN_STATUSES, "agent_runs", "status", reverse=True)
    _rename(TRIAGE, "sentry_issues", "triage", reverse=True)

    op.execute("ALTER TABLE agent_runs ALTER COLUMN status SET DEFAULT 'pendiente'")
    op.execute(
        f"ALTER TABLE agent_runs ADD CONSTRAINT agent_runs_status_check CHECK (status IN ({_ES_RUN}))"
    )
    op.execute(
        "ALTER TABLE sentry_issues ADD CONSTRAINT sentry_issues_triage_check "
        f"CHECK (triage IS NULL OR triage IN ({_ES_TRIAGE}))"
    )
