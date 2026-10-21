"""Grant parser worker read-only access to the B2 namespace fence.

Revision ID: 0006_b2_parser_worker_scope
Revises: 0005_b2_rubric_parser_namespace
"""

from alembic import op

revision = "0006_b2_parser_worker_scope"
down_revision = "0005_b2_rubric_parser_namespace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT SELECT ON assignment_versions TO align_worker")


def downgrade() -> None:
    op.execute("REVOKE SELECT ON assignment_versions FROM align_worker")
