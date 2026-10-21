"""Add immutable student submission versions and their parser namespace.

Revision ID: 0008_b3_submissions
Revises: 0007_b2_parser_namespace_lock
"""

from alembic import op

revision = "0008_b3_submissions"
down_revision = "0007_b2_parser_namespace_lock"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE submissions (
      id uuid PRIMARY KEY,
      assignment_id uuid NOT NULL REFERENCES assignments(id) ON DELETE RESTRICT,
      individual_user_id uuid REFERENCES users(id) ON DELETE RESTRICT,
      group_id uuid REFERENCES groups(id) ON DELETE RESTRICT,
      active_final_version_id uuid,
      draft_assignment_version_id uuid NOT NULL REFERENCES assignment_versions(id) ON DELETE RESTRICT,
      draft_revision integer NOT NULL DEFAULT 1 CHECK (draft_revision >= 1),
      reopen_until timestamptz,
      revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
      created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT ck_submissions_one_owner CHECK
        ((individual_user_id IS NOT NULL)::integer + (group_id IS NOT NULL)::integer = 1)
    );
    CREATE UNIQUE INDEX uq_submissions_assignment_individual
      ON submissions(assignment_id,individual_user_id) WHERE individual_user_id IS NOT NULL;
    CREATE UNIQUE INDEX uq_submissions_assignment_group
      ON submissions(assignment_id,group_id) WHERE group_id IS NOT NULL;

    CREATE TABLE submission_versions (
      id uuid PRIMARY KEY,
      submission_id uuid NOT NULL REFERENCES submissions(id) ON DELETE RESTRICT,
      version_no integer NOT NULL CHECK (version_no >= 1),
      assignment_version_id uuid NOT NULL REFERENCES assignment_versions(id) ON DELETE RESTRICT,
      manifest_hash varchar(64) NOT NULL,
      manifest_json jsonb NOT NULL,
      finalized_by uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
      finalized_at timestamptz NOT NULL,
      is_late boolean NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_submission_versions_number UNIQUE(submission_id,version_no),
      CONSTRAINT uq_submission_versions_id_submission UNIQUE(id,submission_id)
    );
    ALTER TABLE submissions ADD CONSTRAINT fk_submissions_active_final_version
      FOREIGN KEY(active_final_version_id,id) REFERENCES submission_versions(id,submission_id) ON DELETE RESTRICT;
    ALTER TABLE file_assets ADD CONSTRAINT fk_file_assets_submission
      FOREIGN KEY(submission_id) REFERENCES submissions(id) ON DELETE RESTRICT;
    ALTER TABLE file_assets ADD CONSTRAINT uq_file_assets_id_submission UNIQUE(id,submission_id);
    ALTER TABLE file_assets DROP CONSTRAINT ck_file_assets_scope;
    ALTER TABLE file_assets ADD CONSTRAINT ck_file_assets_scope CHECK (
      (purpose='context' AND course_id IS NOT NULL AND assignment_id IS NULL AND submission_id IS NULL)
      OR (purpose='rubric' AND course_id IS NOT NULL AND assignment_id IS NOT NULL AND submission_id IS NULL)
      OR (purpose='submission' AND course_id IS NOT NULL AND assignment_id IS NULL AND submission_id IS NOT NULL)
    );

    CREATE TABLE submission_version_files (
      submission_version_id uuid NOT NULL,
      submission_id uuid NOT NULL REFERENCES submissions(id) ON DELETE RESTRICT,
      file_asset_id uuid NOT NULL,
      display_order integer NOT NULL CHECK(display_order >= 0),
      created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(submission_version_id,file_asset_id),
      CONSTRAINT uq_submission_version_file_order UNIQUE(submission_version_id,display_order),
      CONSTRAINT fk_submission_version_files_version_scope FOREIGN KEY(submission_version_id,submission_id)
        REFERENCES submission_versions(id,submission_id) ON DELETE RESTRICT,
      CONSTRAINT fk_submission_version_files_asset_scope FOREIGN KEY(file_asset_id,submission_id)
        REFERENCES file_assets(id,submission_id) ON DELETE RESTRICT
    );
    CREATE TABLE submission_version_members (
      submission_version_id uuid NOT NULL REFERENCES submission_versions(id) ON DELETE RESTRICT,
      user_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
      display_name_snapshot varchar(240) NOT NULL,
      email_snapshot varchar(320) NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(submission_version_id,user_id)
    );
    ALTER TABLE parse_artifacts ADD CONSTRAINT fk_parse_artifacts_submission_version
      FOREIGN KEY(submission_version_id) REFERENCES submission_versions(id) ON DELETE RESTRICT;
    ALTER TABLE evidence_items ADD CONSTRAINT fk_evidence_items_submission_version
      FOREIGN KEY(submission_version_id) REFERENCES submission_versions(id) ON DELETE RESTRICT;
    ALTER TABLE evidence_items DROP CONSTRAINT evidence_items_type_check;
    ALTER TABLE evidence_items ADD CONSTRAINT ck_evidence_items_type CHECK
      (type IN ('paragraph','table','diagram','image_text','spreadsheet_cell','spreadsheet_range','numeric_observation'));

    CREATE FUNCTION guard_submission_rows() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE assignment_course uuid; assignment_mode varchar(16); group_assignment uuid;
      submission_owner uuid; submission_group uuid; prior_version_no integer; next_version_no integer;
    BEGIN
      IF TG_TABLE_NAME='submissions' THEN
        IF TG_OP='DELETE' THEN RAISE EXCEPTION 'SUBMISSION_THREAD_IMMUTABLE'; END IF;
        IF TG_OP='UPDATE' AND (NEW.assignment_id,NEW.individual_user_id,NEW.group_id)
          IS DISTINCT FROM (OLD.assignment_id,OLD.individual_user_id,OLD.group_id)
          THEN RAISE EXCEPTION 'SUBMISSION_OWNER_IMMUTABLE'; END IF;
        IF TG_OP='UPDATE' AND NEW.active_final_version_id IS DISTINCT FROM OLD.active_final_version_id
          AND OLD.active_final_version_id IS NOT NULL THEN
          SELECT version_no INTO prior_version_no FROM submission_versions WHERE id=OLD.active_final_version_id;
          SELECT version_no INTO next_version_no FROM submission_versions WHERE id=NEW.active_final_version_id
            AND submission_id=NEW.id;
          IF next_version_no IS NULL OR next_version_no <= prior_version_no
            THEN RAISE EXCEPTION 'SUBMISSION_FINAL_POINTER_MUST_ADVANCE'; END IF;
        END IF;
        SELECT course_id INTO assignment_course FROM assignments WHERE id=NEW.assignment_id;
        IF NEW.group_id IS NOT NULL THEN
          SELECT assignment_id INTO group_assignment FROM groups WHERE id=NEW.group_id AND active;
          IF group_assignment IS DISTINCT FROM NEW.assignment_id THEN RAISE EXCEPTION 'SUBMISSION_GROUP_SCOPE_MISMATCH'; END IF;
        END IF;
        IF NEW.draft_assignment_version_id IS NOT NULL THEN
          SELECT mode INTO assignment_mode FROM assignment_versions
           WHERE id=NEW.draft_assignment_version_id AND assignment_id=NEW.assignment_id AND state='published';
          IF assignment_mode IS NULL OR (assignment_mode='individual') <> (NEW.individual_user_id IS NOT NULL)
             OR (assignment_mode='group') <> (NEW.group_id IS NOT NULL)
            THEN RAISE EXCEPTION 'SUBMISSION_ASSIGNMENT_MODE_MISMATCH'; END IF;
        END IF;
        RETURN NEW;
      ELSIF TG_TABLE_NAME='submission_versions' THEN
        IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'SUBMISSION_VERSION_IMMUTABLE'; END IF;
        IF NOT EXISTS (SELECT 1 FROM submissions s JOIN assignment_versions av ON av.id=NEW.assignment_version_id
          WHERE s.id=NEW.submission_id AND s.assignment_id=av.assignment_id)
          THEN RAISE EXCEPTION 'SUBMISSION_VERSION_ASSIGNMENT_MISMATCH'; END IF;
        RETURN NEW;
      ELSIF TG_TABLE_NAME='submission_version_files' THEN
        IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'SUBMISSION_VERSION_FILE_IMMUTABLE'; END IF;
        IF NOT EXISTS (SELECT 1 FROM file_assets f WHERE f.id=NEW.file_asset_id AND f.submission_id=NEW.submission_id
          AND f.purpose='submission' AND f.validation_state='validated')
          THEN RAISE EXCEPTION 'SUBMISSION_FILE_SCOPE_MISMATCH'; END IF;
        RETURN NEW;
      ELSIF TG_TABLE_NAME='submission_version_members' THEN
        IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'SUBMISSION_ROSTER_IMMUTABLE'; END IF;
        SELECT s.individual_user_id,s.group_id INTO submission_owner,submission_group
          FROM submission_versions sv JOIN submissions s ON s.id=sv.submission_id
          WHERE sv.id=NEW.submission_version_id;
        IF submission_owner IS NOT NULL THEN
          IF submission_owner<>NEW.user_id THEN RAISE EXCEPTION 'SUBMISSION_ROSTER_OWNER_MISMATCH'; END IF;
        ELSIF NOT EXISTS (
          SELECT 1 FROM submission_versions sv JOIN submissions s ON s.id=sv.submission_id
          JOIN group_memberships gm ON gm.group_id=s.group_id AND gm.assignment_id=s.assignment_id
          JOIN assignments a ON a.id=s.assignment_id
          JOIN enrollments e ON e.course_id=a.course_id AND e.user_id=gm.user_id
          WHERE sv.id=NEW.submission_version_id AND gm.user_id=NEW.user_id
            AND gm.active AND e.active AND e.role='student'
        ) THEN RAISE EXCEPTION 'SUBMISSION_ROSTER_MEMBER_MISMATCH'; END IF;
        RETURN NEW;
      ELSIF TG_TABLE_NAME='file_assets' THEN
        IF TG_OP='UPDATE' AND OLD.purpose='submission'
          THEN RAISE EXCEPTION 'SUBMISSION_FILE_ASSET_IMMUTABLE'; END IF;
        IF TG_OP='UPDATE' AND (NEW.id,NEW.submission_id,NEW.course_id,NEW.assignment_id,NEW.purpose,
          NEW.uploaded_by,NEW.original_name,NEW.size_bytes,NEW.mime,NEW.sha256,NEW.object_key)
          IS DISTINCT FROM (OLD.id,OLD.submission_id,OLD.course_id,OLD.assignment_id,OLD.purpose,
          OLD.uploaded_by,OLD.original_name,OLD.size_bytes,OLD.mime,OLD.sha256,OLD.object_key)
          THEN RAISE EXCEPTION 'FILE_ASSET_IDENTITY_IMMUTABLE'; END IF;
        IF TG_OP='DELETE' AND (OLD.purpose<>'submission' OR EXISTS(
          SELECT 1 FROM submission_version_files WHERE file_asset_id=OLD.id))
          THEN RAISE EXCEPTION 'FINAL_SUBMISSION_ASSET_IMMUTABLE'; END IF;
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
      END IF;
      RETURN COALESCE(NEW,OLD);
    END $$;
    CREATE TRIGGER submission_thread_guard BEFORE INSERT OR UPDATE OR DELETE ON submissions
      FOR EACH ROW EXECUTE FUNCTION guard_submission_rows();
    CREATE TRIGGER submission_version_guard BEFORE INSERT OR UPDATE OR DELETE ON submission_versions
      FOR EACH ROW EXECUTE FUNCTION guard_submission_rows();
    CREATE TRIGGER submission_version_files_guard BEFORE INSERT OR UPDATE OR DELETE ON submission_version_files
      FOR EACH ROW EXECUTE FUNCTION guard_submission_rows();
    CREATE TRIGGER submission_version_members_guard BEFORE INSERT OR UPDATE OR DELETE ON submission_version_members
      FOR EACH ROW EXECUTE FUNCTION guard_submission_rows();
    CREATE TRIGGER submission_asset_guard BEFORE UPDATE OR DELETE ON file_assets
      FOR EACH ROW EXECUTE FUNCTION guard_submission_rows();

    GRANT SELECT,INSERT,UPDATE,DELETE ON submissions,submission_versions,submission_version_files,submission_version_members TO align_api;
    GRANT SELECT ON submissions,submission_versions,submission_version_files,file_assets TO align_worker;
    GRANT DELETE ON file_assets TO align_api;
    REVOKE UPDATE,DELETE ON submission_versions,submission_version_files,submission_version_members FROM align_api,align_worker;
    REVOKE INSERT,UPDATE,DELETE ON submissions,submission_versions,submission_version_files,submission_version_members FROM align_worker;
    """)


def downgrade() -> None:
    op.execute("""
    REVOKE DELETE ON file_assets FROM align_api;
    DROP TRIGGER IF EXISTS submission_asset_guard ON file_assets;
    DROP TRIGGER IF EXISTS submission_version_members_guard ON submission_version_members;
    DROP TRIGGER IF EXISTS submission_version_files_guard ON submission_version_files;
    DROP TRIGGER IF EXISTS submission_version_guard ON submission_versions;
    DROP TRIGGER IF EXISTS submission_thread_guard ON submissions;
    DROP FUNCTION IF EXISTS guard_submission_rows();
    ALTER TABLE evidence_items DROP CONSTRAINT IF EXISTS ck_evidence_items_type;
    ALTER TABLE evidence_items ADD CONSTRAINT ck_evidence_items_type CHECK
      (type IN ('paragraph','table','diagram','image_text','numeric_observation'));
    ALTER TABLE evidence_items DROP CONSTRAINT IF EXISTS fk_evidence_items_submission_version;
    ALTER TABLE parse_artifacts DROP CONSTRAINT IF EXISTS fk_parse_artifacts_submission_version;
    DROP TABLE IF EXISTS submission_version_members;
    DROP TABLE IF EXISTS submission_version_files;
    ALTER TABLE submissions DROP CONSTRAINT IF EXISTS fk_submissions_active_final_version;
    DROP TABLE IF EXISTS submission_versions;
    ALTER TABLE file_assets DROP CONSTRAINT IF EXISTS uq_file_assets_id_submission;
    ALTER TABLE file_assets DROP CONSTRAINT IF EXISTS fk_file_assets_submission;
    ALTER TABLE file_assets DROP CONSTRAINT IF EXISTS ck_file_assets_scope;
    ALTER TABLE file_assets ADD CONSTRAINT ck_file_assets_scope CHECK (
      (purpose='context' AND assignment_id IS NULL AND submission_id IS NULL)
      OR (purpose='rubric' AND assignment_id IS NOT NULL AND submission_id IS NULL)
      OR (purpose='submission' AND submission_id IS NOT NULL)
    );
    DROP TABLE IF EXISTS submissions;
    """)
