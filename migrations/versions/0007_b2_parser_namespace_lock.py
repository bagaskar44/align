"""Allow the parser worker to lock, but not edit, assignment namespaces.

Revision ID: 0007_b2_parser_namespace_lock
Revises: 0006_b2_parser_worker_scope
"""

from alembic import op

revision = "0007_b2_parser_namespace_lock"
down_revision = "0006_b2_parser_worker_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT UPDATE (updated_at) ON assignment_versions TO align_worker")


def downgrade() -> None:
    op.execute("REVOKE UPDATE (updated_at) ON assignment_versions FROM align_worker")
