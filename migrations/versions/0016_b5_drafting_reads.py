"""Grant the B5 drafting worker read-only access to approved rubric source rows.

Revision ID: 0016_b5_drafting_reads
Revises: 0015_b5_worker_ts
"""

from alembic import op

revision = "0016_b5_drafting_reads"
down_revision = "0015_b5_worker_ts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "GRANT SELECT ON criterion_set_versions,criterion_requirement_links,"
        "criterion_outcome_links TO align_worker"
    )


def downgrade() -> None:
    op.execute(
        "REVOKE SELECT ON criterion_set_versions,criterion_requirement_links,"
        "criterion_outcome_links FROM align_worker"
    )
