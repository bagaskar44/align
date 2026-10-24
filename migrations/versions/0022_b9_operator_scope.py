"""Allow the B9 operator CLI to inspect actors and course scope.

Revision ID: 0022_b9_operator_scope
Revises: 0021_b9_moodle_sandbox
"""

from alembic import op

revision = "0022_b9_operator_scope"
down_revision = "0021_b9_moodle_sandbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT SELECT ON users, courses TO align_operator")


def downgrade() -> None:
    op.execute("REVOKE SELECT ON users, courses FROM align_operator")
