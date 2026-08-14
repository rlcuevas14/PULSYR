"""v0023: hot-path indexes and idempotent active jobs."""

from alembic import op

revision = "v0023"
down_revision = "v0022"
branch_labels = None
depends_on = None


_INDEXES: tuple[tuple[str, str], ...] = (
    (
        "items_project_created_id_idx",
        "CREATE INDEX items_project_created_id_idx "
        "ON items (project_id, created_at DESC, id)",
    ),
    (
        "items_project_status_created_id_idx",
        "CREATE INDEX items_project_status_created_id_idx "
        "ON items (project_id, status, created_at DESC, id)",
    ),
    (
        "items_project_impact_id_idx",
        "CREATE INDEX items_project_impact_id_idx "
        "ON items (project_id, impact_ai DESC NULLS LAST, "
        "effort_ai ASC NULLS LAST, id)",
    ),
    (
        "items_project_priority_impact_id_idx",
        "CREATE INDEX items_project_priority_impact_id_idx "
        "ON items (project_id, priority ASC NULLS LAST, "
        "impact_ai DESC NULLS LAST, id)",
    ),
    (
        "threads_project_updated_id_idx",
        "CREATE INDEX threads_project_updated_id_idx "
        "ON threads (project_id, updated_at DESC, id)",
    ),
    (
        "scopes_project_order_name_id_idx",
        "CREATE INDEX scopes_project_order_name_id_idx "
        "ON scopes (project_id, display_order, name, id)",
    ),
    (
        "agent_runs_pending_created_id_idx",
        "CREATE INDEX agent_runs_pending_created_id_idx "
        "ON agent_runs (created_at, id) WHERE status = 'pending'",
    ),
    (
        "agent_runs_active_ref_idx",
        "CREATE INDEX agent_runs_active_ref_idx "
        "ON agent_runs (project_id, kind, ref_type, ref_id) "
        "WHERE ref_id IS NOT NULL AND status IN ('pending','running')",
    ),
    (
        "agent_runs_project_active_idx",
        "CREATE INDEX agent_runs_project_active_idx ON agent_runs (project_id, status) "
        "WHERE status IN ('pending','running')",
    ),
)


def upgrade() -> None:
    for _name, ddl in _INDEXES:
        op.execute(ddl)


def downgrade() -> None:
    for name, _ddl in reversed(_INDEXES):
        op.execute(f"DROP INDEX IF EXISTS {name}")
