"""v0020: users.password_hash becomes NULLABLE.

Signing in with GitHub or Google means the provider holds the credential and this
side stores none, so an OAuth-created user has no hash to put in the column. The
NOT NULL was the only thing standing in the way; nothing else about the row
changes, and existing password users keep their hash.

authenticate() refuses a NULL hash outright rather than letting it reach bcrypt,
so relaxing the constraint does not open a passwordless login on the form.
"""

from alembic import op

revision = "v0020"
down_revision = "v0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL")


def downgrade() -> None:
    # Re-adding NOT NULL means deciding what happens to OAuth users, and there is no
    # good automatic answer: inventing a hash creates a credential nobody knows, and
    # deleting the row takes their account and its data with it. Fail loudly and let
    # a human choose, rather than destroy accounts inside a migration.
    op.execute(
        """
        DO $$
        DECLARE n integer;
        BEGIN
            SELECT count(*) INTO n FROM users WHERE password_hash IS NULL;
            IF n > 0 THEN
                RAISE EXCEPTION
                    'v0020 downgrade refused: % OAuth user(s) have no password. '
                    'Delete or re-credential them by hand first.', n;
            END IF;
        END $$;
        """
    )
    op.execute("ALTER TABLE users ALTER COLUMN password_hash SET NOT NULL")
