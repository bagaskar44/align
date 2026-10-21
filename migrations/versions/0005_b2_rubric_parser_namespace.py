"""Bind B2 rubric parser provenance to its immutable assignment version.

Revision ID: 0005_b2_rubric_parser_namespace
Revises: 0004_b2_assignments_specs_groups
"""

from alembic import op

revision = "0005_b2_rubric_parser_namespace"
down_revision = "0004_b2_assignments_specs_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE parse_artifacts ADD CONSTRAINT fk_parse_artifacts_assignment_version
      FOREIGN KEY(assignment_version_id) REFERENCES assignment_versions(id) ON DELETE RESTRICT;
    ALTER TABLE evidence_items ADD CONSTRAINT fk_evidence_items_assignment_version
      FOREIGN KEY(assignment_version_id) REFERENCES assignment_versions(id) ON DELETE RESTRICT;
    """)


def downgrade() -> None:
    op.execute("""
    ALTER TABLE evidence_items DROP CONSTRAINT fk_evidence_items_assignment_version;
    ALTER TABLE parse_artifacts DROP CONSTRAINT fk_parse_artifacts_assignment_version;
    """)
