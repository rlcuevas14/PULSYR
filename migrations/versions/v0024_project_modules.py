"""v0024: configurable, auditable project modules."""

from alembic import op

revision = "v0024"
down_revision = "v0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE project_modules (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id  uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            module      VARCHAR(30) NOT NULL
                        CHECK (module IN ('threads','incidents','management')),
            enabled     BOOLEAN NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT project_modules_project_module_uniq UNIQUE(project_id, module)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE project_module_events (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id       uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            module           VARCHAR(30) NOT NULL
                             CHECK (module IN ('threads','incidents','management')),
            actor            VARCHAR(255) NOT NULL,
            previous_enabled BOOLEAN NOT NULL,
            enabled          BOOLEAN NOT NULL,
            source           VARCHAR(20) NOT NULL
                             CHECK (source IN ('onboarding','preset','manual','migration')),
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        INSERT INTO project_modules (project_id, module, enabled)
        SELECT p.id, m.module, true
        FROM projects p
        CROSS JOIN (VALUES ('threads'), ('incidents'), ('management')) AS m(module)
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT p.id
                FROM projects p
                LEFT JOIN project_modules pm ON pm.project_id = p.id
                GROUP BY p.id
                HAVING count(pm.id) <> 3 OR count(DISTINCT pm.module) <> 3
            ) THEN
                RAISE EXCEPTION 'project_modules backfill invariant failed';
            END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS project_module_events")
    op.execute("DROP TABLE IF EXISTS project_modules")
