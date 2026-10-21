"""Permit narrowly scoped cleanup of expired draft submission assets.

Revision ID: 0009_b3_upload_retention
Revises: 0008_b3_submissions
"""

from alembic import op

revision = "0009_b3_upload_retention"
down_revision = "0008_b3_submissions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    DROP TRIGGER IF EXISTS file_assets_are_retained ON file_assets;
    DROP TRIGGER IF EXISTS submission_asset_guard ON file_assets;

    CREATE FUNCTION guard_submission_assets() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='UPDATE' THEN
        IF OLD.purpose<>'submission' THEN RAISE EXCEPTION 'FILE_ASSET_IMMUTABLE'; END IF;
        IF (NEW.id,NEW.submission_id,NEW.course_id,NEW.assignment_id,NEW.purpose,NEW.uploaded_by,
            NEW.original_name,NEW.size_bytes,NEW.mime,NEW.sha256,NEW.object_key,NEW.preflight_json,NEW.created_at)
           IS DISTINCT FROM
           (OLD.id,OLD.submission_id,OLD.course_id,OLD.assignment_id,OLD.purpose,OLD.uploaded_by,
            OLD.original_name,OLD.size_bytes,OLD.mime,OLD.sha256,OLD.object_key,OLD.preflight_json,OLD.created_at)
          THEN RAISE EXCEPTION 'FILE_ASSET_IDENTITY_IMMUTABLE'; END IF;
        IF NEW.validation_state<>'quarantined' OR NEW.quarantined_at IS NULL
          THEN RAISE EXCEPTION 'SUBMISSION_ASSET_LIFECYCLE_INVALID'; END IF;
        RETURN NEW;
      END IF;
      IF TG_OP='DELETE' THEN
        IF OLD.purpose<>'submission' OR EXISTS(
          SELECT 1 FROM submission_version_files WHERE file_asset_id=OLD.id)
          THEN RAISE EXCEPTION 'FINAL_SUBMISSION_ASSET_IMMUTABLE'; END IF;
        RETURN OLD;
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER submission_asset_guard BEFORE UPDATE OR DELETE ON file_assets
      FOR EACH ROW EXECUTE FUNCTION guard_submission_assets();

    GRANT UPDATE (validation_state,quarantined_at,updated_at) ON file_assets TO align_worker;
    GRANT DELETE ON file_assets TO align_worker;
    GRANT UPDATE (draft_revision,revision,updated_at) ON submissions TO align_worker;
    GRANT SELECT (id,course_id) ON assignments TO align_worker;
    GRANT SELECT (id,assignment_id,active) ON groups TO align_worker;
    """)


def downgrade() -> None:
    op.execute("""
    REVOKE UPDATE (validation_state,quarantined_at,updated_at) ON file_assets FROM align_worker;
    REVOKE DELETE ON file_assets FROM align_worker;
    REVOKE UPDATE (draft_revision,revision,updated_at) ON submissions FROM align_worker;
    REVOKE SELECT (id,course_id) ON assignments FROM align_worker;
    REVOKE SELECT (id,assignment_id,active) ON groups FROM align_worker;
    DROP TRIGGER IF EXISTS submission_asset_guard ON file_assets;
    DROP FUNCTION IF EXISTS guard_submission_assets();
    CREATE TRIGGER file_assets_are_retained BEFORE UPDATE OR DELETE ON file_assets
      FOR EACH ROW EXECUTE FUNCTION reject_b1_evidence_change();
    CREATE TRIGGER submission_asset_guard BEFORE UPDATE OR DELETE ON file_assets
      FOR EACH ROW EXECUTE FUNCTION guard_submission_rows();
    """)
