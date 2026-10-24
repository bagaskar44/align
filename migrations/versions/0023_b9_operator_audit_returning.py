"""Return only audit timestamps to the LMS operator CLI.

Revision ID: 0023_b9_operator_audit_returning
Revises: 0022_b9_operator_scope
"""

from alembic import op

revision = "0023_b9_operator_audit_returning"
down_revision = "0022_b9_operator_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT SELECT (created_at) ON audit_events TO align_operator")


def downgrade() -> None:
    op.execute("REVOKE SELECT (created_at) ON audit_events FROM align_operator")
