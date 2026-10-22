"""Version-bound, lecturer-verified task facts for applicability.

Revision ID: 0019_b8_task_facts
Revises: 0018_b7_retention
"""

from alembic import op

revision = "0019_b8_task_facts"
down_revision = "0018_b7_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE assignment_versions
      ADD COLUMN task_facts_json jsonb NOT NULL DEFAULT '{}'::jsonb,
      ADD CONSTRAINT ck_assignment_task_facts_object
        CHECK (jsonb_typeof(task_facts_json) = 'object');
    """)


def downgrade() -> None:
    op.execute("""
    ALTER TABLE assignment_versions
      DROP CONSTRAINT ck_assignment_task_facts_object,
      DROP COLUMN task_facts_json;
    """)
