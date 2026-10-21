"""Grant the B5 worker read-only access to confirmed feedback sources.

Revision ID: 0014_b5_feedback_worker_reads
Revises: 0013_b5_feedback_worker
"""

from alembic import op

revision = "0014_b5_feedback_worker_reads"
down_revision = "0013_b5_feedback_worker"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    GRANT SELECT ON review_versions,review_findings,evidence_links,
      review_annotation_revisions,review_annotations,manual_evidence,evidence_items,
      criteria,analysis_runs,release_manifests,method_manifests,feedback_drafts TO align_worker;
    """)


def downgrade() -> None:
    op.execute("""
    REVOKE SELECT ON review_versions,review_findings,evidence_links,
      review_annotation_revisions,review_annotations,manual_evidence FROM align_worker;
    """)
