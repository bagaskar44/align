"""Queue B5 feedback generation through the fenced worker.

Revision ID: 0013_b5_feedback_worker
Revises: 0012_b5_feedback_generation
"""

from alembic import op

revision = "0013_b5_feedback_worker"
down_revision = "0012_b5_feedback_generation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE feedback_drafts
      DROP CONSTRAINT IF EXISTS feedback_drafts_generation_status_check,
      DROP CONSTRAINT IF EXISTS ck_feedback_generation_status;
    ALTER TABLE feedback_drafts ADD CONSTRAINT ck_feedback_generation_status
      CHECK(generation_status IN ('not_requested','queued','running','completed','fallback'));

    GRANT SELECT ON review_versions,review_findings,evidence_links,criteria,analysis_runs,
      release_manifests,method_manifests,feedback_drafts TO align_worker;
    GRANT UPDATE (generation_mode,generation_status,generation_failure_code,body_markdown,
      blocks_json,content_hash) ON feedback_drafts TO align_worker;
    """)


def downgrade() -> None:
    op.execute("""
    REVOKE UPDATE (generation_mode,generation_status,generation_failure_code,body_markdown,
      blocks_json,content_hash) ON feedback_drafts FROM align_worker;
    ALTER TABLE feedback_drafts DROP CONSTRAINT IF EXISTS ck_feedback_generation_status;
    ALTER TABLE feedback_drafts ADD CONSTRAINT ck_feedback_generation_status
      CHECK(generation_status IN ('not_requested','completed','fallback'));
    """)
