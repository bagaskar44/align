"""B5 bounded model feedback generation provenance.

Revision ID: 0012_b5_feedback_generation
Revises: 0011_b5_executor
"""

from alembic import op

revision = "0012_b5_feedback_generation"
down_revision = "0011_b5_executor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE feedback_drafts
      ADD COLUMN generation_job_id uuid REFERENCES jobs(id) ON DELETE RESTRICT,
      ADD COLUMN generation_status varchar(16) NOT NULL DEFAULT 'not_requested'
        CHECK(generation_status IN ('not_requested','completed','fallback')),
      ADD COLUMN generation_failure_code varchar(80);
    CREATE UNIQUE INDEX uq_feedback_drafts_generation_job
      ON feedback_drafts(generation_job_id) WHERE generation_job_id IS NOT NULL;
    """)


def downgrade() -> None:
    op.execute("""
    DROP INDEX IF EXISTS uq_feedback_drafts_generation_job;
    ALTER TABLE feedback_drafts
      DROP COLUMN IF EXISTS generation_failure_code,
      DROP COLUMN IF EXISTS generation_status,
      DROP COLUMN IF EXISTS generation_job_id;
    """)
