"""Allow the B5 worker to maintain feedback draft update timestamps.

Revision ID: 0015_b5_worker_ts
Revises: 0014_b5_feedback_worker_reads
"""

from alembic import op

revision = "0015_b5_worker_ts"
down_revision = "0014_b5_feedback_worker_reads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT UPDATE (updated_at) ON feedback_drafts TO align_worker")


def downgrade() -> None:
    op.execute("REVOKE UPDATE (updated_at) ON feedback_drafts FROM align_worker")
