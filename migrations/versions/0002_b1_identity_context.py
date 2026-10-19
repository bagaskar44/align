"""B1 account provisioning, context versions, source assets and parser provenance.

Revision ID: 0002_b1_identity_context
Revises: 0001_b0_foundation
"""

from alembic import op

revision = "0002_b1_identity_context"
down_revision = "0001_b0_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE context_versions (
          id uuid PRIMARY KEY,
          course_id uuid NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
          version_no integer NOT NULL CHECK (version_no >= 1),
          state varchar(16) NOT NULL CHECK (state IN ('draft','approved')),
          fields_json jsonb NOT NULL,
          instruction_sources_json jsonb NOT NULL DEFAULT '[]'::jsonb,
          content_hash varchar(64),
          approved_by uuid REFERENCES users(id),
          approved_at timestamptz,
          revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_context_versions_course_version UNIQUE(course_id, version_no)
        );
        ALTER TABLE courses ADD CONSTRAINT fk_courses_approved_context_version
          FOREIGN KEY (approved_context_version_id) REFERENCES context_versions(id) ON DELETE SET NULL;
        CREATE INDEX ix_context_versions_course_state ON context_versions(course_id, state, created_at);

        CREATE TABLE context_conflicts (
          id uuid PRIMARY KEY,
          context_version_id uuid NOT NULL REFERENCES context_versions(id) ON DELETE CASCADE,
          field_path varchar(80) NOT NULL CHECK (field_path IN ('course_code','course_title','context_description','language','number_locale')),
          candidate_values_json jsonb NOT NULL,
          resolution_json jsonb,
          resolution_history_json jsonb NOT NULL DEFAULT '[]'::jsonb,
          resolved_by uuid REFERENCES users(id),
          resolved_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_context_conflicts_version_field UNIQUE(context_version_id, field_path)
        );

        CREATE TABLE learning_outcomes (
          id uuid PRIMARY KEY,
          context_version_id uuid NOT NULL REFERENCES context_versions(id) ON DELETE CASCADE,
          code varchar(80) NOT NULL,
          description varchar(4000) NOT NULL,
          origin varchar(16) NOT NULL CHECK (origin IN ('manual','extracted','suggested')),
          source_refs_json jsonb NOT NULL DEFAULT '[]'::jsonb,
          resolution_status varchar(24) NOT NULL CHECK (resolution_status IN ('proposed','confirmed','conflicting','not_provided')),
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_learning_outcomes_version_code UNIQUE(context_version_id, code)
        );

        CREATE TABLE file_assets (
          id uuid PRIMARY KEY,
          course_id uuid NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
          submission_id uuid,
          assignment_id uuid,
          purpose varchar(16) NOT NULL CHECK (purpose IN ('context','rubric','submission')),
          uploaded_by uuid NOT NULL REFERENCES users(id),
          original_name varchar(255) NOT NULL,
          size_bytes integer NOT NULL CHECK (size_bytes > 0),
          mime varchar(120) NOT NULL,
          sha256 varchar(64) NOT NULL,
          object_key varchar(240) NOT NULL UNIQUE,
          validation_state varchar(20) NOT NULL CHECK (validation_state IN ('validated','rejected','quarantined')),
          preflight_json jsonb NOT NULL,
          quarantined_at timestamptz,
          revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_file_assets_scope CHECK (
            (purpose = 'context' AND assignment_id IS NULL AND submission_id IS NULL)
            OR (purpose = 'rubric' AND assignment_id IS NOT NULL AND submission_id IS NULL)
            OR (purpose = 'submission' AND submission_id IS NOT NULL)
          )
        );
        CREATE INDEX ix_file_assets_course_purpose ON file_assets(course_id, purpose, created_at);

        CREATE TABLE normative_file_links (
          id uuid PRIMARY KEY,
          file_asset_id uuid NOT NULL REFERENCES file_assets(id) ON DELETE RESTRICT,
          context_version_id uuid REFERENCES context_versions(id) ON DELETE RESTRICT,
          assignment_version_id uuid,
          source_role varchar(16) NOT NULL CHECK (source_role IN ('kk','rpp','rubric')),
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_normative_file_links_one_version CHECK (
            (context_version_id IS NOT NULL)::integer + (assignment_version_id IS NOT NULL)::integer = 1
          ),
          CONSTRAINT uq_normative_context_file_role UNIQUE(file_asset_id, context_version_id, source_role)
        );
        CREATE INDEX ix_normative_file_links_context ON normative_file_links(context_version_id);

        CREATE TABLE parse_artifacts (
          id uuid PRIMARY KEY,
          file_asset_id uuid NOT NULL REFERENCES file_assets(id) ON DELETE RESTRICT,
          submission_version_id uuid,
          context_version_id uuid REFERENCES context_versions(id) ON DELETE RESTRICT,
          assignment_version_id uuid,
          parser_manifest_id uuid REFERENCES method_manifests(id),
          parser_resource_manifest_id uuid REFERENCES method_manifests(id),
          fingerprint varchar(64) NOT NULL,
          owner_scope_hash varchar(64) NOT NULL,
          status varchar(24) NOT NULL CHECK (status IN ('queued','running','parsed','partially_parsed','unreadable','unsupported','failed','blocked')),
          limitations_json jsonb NOT NULL DEFAULT '[]'::jsonb,
          artifact_object_key varchar(240),
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_parse_artifacts_one_namespace CHECK (
            (submission_version_id IS NOT NULL)::integer + (context_version_id IS NOT NULL)::integer + (assignment_version_id IS NOT NULL)::integer = 1
          ),
          CONSTRAINT uq_parse_artifacts_scoped_fingerprint UNIQUE(owner_scope_hash, fingerprint)
        );

        CREATE TABLE parser_checkpoints (
          id uuid PRIMARY KEY,
          job_id uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
          parse_artifact_id uuid NOT NULL REFERENCES parse_artifacts(id) ON DELETE CASCADE,
          task_key varchar(64) NOT NULL,
          file_hash varchar(64) NOT NULL,
          namespace_version_id uuid NOT NULL,
          canonical_unit_locator_json jsonb NOT NULL,
          stage varchar(64) NOT NULL,
          parser_resource_manifest_id uuid REFERENCES method_manifests(id),
          status varchar(16) NOT NULL CHECK (status IN ('queued','running','succeeded','blocked','failed')),
          output_refs_json jsonb NOT NULL DEFAULT '{}'::jsonb,
          covered_unit_refs_json jsonb NOT NULL DEFAULT '[]'::jsonb,
          limitations_json jsonb NOT NULL DEFAULT '[]'::jsonb,
          started_at timestamptz,
          finished_at timestamptz,
          admission_node varchar(120),
          admission_lease varchar(120),
          admission_fence integer,
          actual_active_ms integer,
          peak_memory_bytes bigint,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_parser_checkpoints_task UNIQUE(parse_artifact_id, task_key)
        );

        CREATE TABLE processing_time_reservations (
          id uuid PRIMARY KEY,
          job_id uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
          task_key varchar(64) NOT NULL,
          attempt_no integer NOT NULL CHECK (attempt_no >= 1),
          reserved_active_ms integer NOT NULL CHECK (reserved_active_ms >= 0),
          actual_active_ms integer CHECK (actual_active_ms IS NULL OR actual_active_ms >= 0),
          state varchar(16) NOT NULL CHECK (state IN ('held','settled','released')),
          started_at timestamptz,
          finished_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_processing_time_reservation_attempt UNIQUE(job_id, task_key, attempt_no)
        );

        CREATE TABLE source_units (
          id uuid PRIMARY KEY,
          parse_artifact_id uuid NOT NULL REFERENCES parse_artifacts(id) ON DELETE CASCADE,
          locator_key varchar(64) NOT NULL,
          parent_id uuid REFERENCES source_units(id) ON DELETE CASCADE,
          unit_type varchar(24) NOT NULL,
          source_order integer NOT NULL CHECK (source_order >= 0),
          anchor_json jsonb NOT NULL,
          parse_status varchar(24) NOT NULL CHECK (parse_status IN ('parsed','partially_parsed','unreadable','unsupported','failed')),
          limitations_json jsonb NOT NULL DEFAULT '[]'::jsonb,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_source_units_artifact_locator UNIQUE(parse_artifact_id, locator_key)
        );
        CREATE INDEX ix_source_units_artifact_order ON source_units(parse_artifact_id, source_order);

        CREATE TABLE evidence_items (
          id uuid PRIMARY KEY,
          source_unit_id uuid NOT NULL REFERENCES source_units(id) ON DELETE CASCADE,
          submission_version_id uuid,
          context_version_id uuid REFERENCES context_versions(id) ON DELETE RESTRICT,
          assignment_version_id uuid,
          file_asset_id uuid NOT NULL REFERENCES file_assets(id) ON DELETE RESTRICT,
          source_sha256 varchar(64) NOT NULL,
          type varchar(32) NOT NULL CHECK (type IN ('paragraph','table','diagram','image_text','numeric_observation')),
          anchor_json jsonb NOT NULL,
          content_original_json jsonb NOT NULL,
          content_normalized_json jsonb NOT NULL,
          parse_status varchar(24) NOT NULL,
          limitations_json jsonb NOT NULL DEFAULT '[]'::jsonb,
          parser_manifest_id uuid REFERENCES method_manifests(id),
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_evidence_items_source_type UNIQUE(source_unit_id, file_asset_id, type),
          CONSTRAINT ck_evidence_items_one_namespace CHECK (
            (submission_version_id IS NOT NULL)::integer + (context_version_id IS NOT NULL)::integer + (assignment_version_id IS NOT NULL)::integer = 1
          )
        );

        CREATE FUNCTION protect_b1_approved_context() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.state = 'approved' THEN RAISE EXCEPTION 'APPROVED_CONTEXT_IMMUTABLE'; END IF;
          IF NEW.state = 'approved' AND current_user = 'align_worker' THEN
            RAISE EXCEPTION 'WORKER_CANNOT_APPROVE_CONTEXT';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER context_versions_immutable_after_approval
          BEFORE UPDATE OR DELETE ON context_versions
          FOR EACH ROW EXECUTE FUNCTION protect_b1_approved_context();
        CREATE FUNCTION protect_b1_context_child() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          target_context_id uuid;
          target_state varchar(16);
        BEGIN
          IF TG_OP = 'DELETE' OR TG_OP = 'UPDATE' THEN
            target_context_id := OLD.context_version_id;
            SELECT state INTO target_state FROM context_versions WHERE id = target_context_id;
            IF target_state = 'approved' THEN RAISE EXCEPTION 'APPROVED_CONTEXT_IMMUTABLE'; END IF;
          END IF;
          IF TG_OP = 'INSERT' OR TG_OP = 'UPDATE' THEN
            target_context_id := NEW.context_version_id;
            SELECT state INTO target_state FROM context_versions WHERE id = target_context_id;
            IF target_state = 'approved' THEN RAISE EXCEPTION 'APPROVED_CONTEXT_IMMUTABLE'; END IF;
          END IF;
          IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER context_conflicts_draft_only
          BEFORE INSERT OR UPDATE OR DELETE ON context_conflicts
          FOR EACH ROW EXECUTE FUNCTION protect_b1_context_child();
        CREATE TRIGGER learning_outcomes_draft_only
          BEFORE INSERT OR UPDATE OR DELETE ON learning_outcomes
          FOR EACH ROW EXECUTE FUNCTION protect_b1_context_child();
        CREATE FUNCTION protect_b1_context_file_link_scope() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          context_course uuid;
          file_course uuid;
          file_purpose varchar(16);
        BEGIN
          IF NEW.context_version_id IS NOT NULL THEN
            SELECT course_id INTO context_course FROM context_versions WHERE id = NEW.context_version_id;
            SELECT course_id, purpose INTO file_course, file_purpose FROM file_assets WHERE id = NEW.file_asset_id;
            IF context_course IS NULL OR file_course IS DISTINCT FROM context_course OR file_purpose <> 'context' THEN
              RAISE EXCEPTION 'CONTEXT_FILE_SCOPE_MISMATCH';
            END IF;
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER normative_context_file_scope
          BEFORE INSERT OR UPDATE ON normative_file_links
          FOR EACH ROW EXECUTE FUNCTION protect_b1_context_file_link_scope();
        CREATE FUNCTION protect_b1_context_artifact_scope() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          context_course uuid;
          context_state varchar(16);
          file_course uuid;
          file_purpose varchar(16);
        BEGIN
          IF NEW.context_version_id IS NOT NULL THEN
            SELECT course_id, state INTO context_course, context_state FROM context_versions WHERE id = NEW.context_version_id;
            SELECT course_id, purpose INTO file_course, file_purpose FROM file_assets WHERE id = NEW.file_asset_id;
            IF context_course IS NULL OR file_course IS DISTINCT FROM context_course OR file_purpose <> 'context' OR context_state <> 'draft' THEN
              RAISE EXCEPTION 'CONTEXT_PARSE_SCOPE_MISMATCH';
            END IF;
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER parse_artifact_context_scope
          BEFORE INSERT OR UPDATE ON parse_artifacts
          FOR EACH ROW EXECUTE FUNCTION protect_b1_context_artifact_scope();
        CREATE FUNCTION protect_b1_context_evidence_scope() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          unit_file uuid;
          unit_context uuid;
          file_course uuid;
          context_course uuid;
        BEGIN
          IF NEW.context_version_id IS NOT NULL THEN
            SELECT artifact.file_asset_id, artifact.context_version_id
              INTO unit_file, unit_context
              FROM source_units AS unit
              JOIN parse_artifacts AS artifact ON artifact.id = unit.parse_artifact_id
              WHERE unit.id = NEW.source_unit_id;
            SELECT course_id INTO file_course FROM file_assets WHERE id = NEW.file_asset_id;
            SELECT course_id INTO context_course FROM context_versions WHERE id = NEW.context_version_id;
            IF unit_file IS DISTINCT FROM NEW.file_asset_id OR unit_context IS DISTINCT FROM NEW.context_version_id OR file_course IS DISTINCT FROM context_course THEN
              RAISE EXCEPTION 'CONTEXT_EVIDENCE_SCOPE_MISMATCH';
            END IF;
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER evidence_context_scope
          BEFORE INSERT ON evidence_items
          FOR EACH ROW EXECUTE FUNCTION protect_b1_context_evidence_scope();
        CREATE FUNCTION protect_b1_approved_course_pointer() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          context_course uuid;
          context_state varchar(16);
        BEGIN
          IF NEW.approved_context_version_id IS NOT NULL THEN
            SELECT course_id, state INTO context_course, context_state
              FROM context_versions WHERE id = NEW.approved_context_version_id;
            IF context_course IS DISTINCT FROM NEW.id OR context_state <> 'approved' THEN
              RAISE EXCEPTION 'APPROVED_CONTEXT_POINTER_INVALID';
            END IF;
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER course_approved_context_scope
          BEFORE INSERT OR UPDATE OF approved_context_version_id ON courses
          FOR EACH ROW EXECUTE FUNCTION protect_b1_approved_course_pointer();
        CREATE FUNCTION reject_b1_evidence_change() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'EVIDENCE_IMMUTABLE'; END $$;
        CREATE TRIGGER evidence_items_are_immutable
          BEFORE UPDATE OR DELETE ON evidence_items
          FOR EACH ROW EXECUTE FUNCTION reject_b1_evidence_change();
        CREATE TRIGGER file_assets_are_retained
          BEFORE UPDATE OR DELETE ON file_assets
          FOR EACH ROW EXECUTE FUNCTION reject_b1_evidence_change();
        CREATE TRIGGER normative_file_links_are_immutable
          BEFORE UPDATE OR DELETE ON normative_file_links
          FOR EACH ROW EXECUTE FUNCTION reject_b1_evidence_change();

        GRANT SELECT, INSERT, UPDATE, DELETE ON context_versions, context_conflicts, learning_outcomes, file_assets, normative_file_links, parse_artifacts, parser_checkpoints, processing_time_reservations, source_units, evidence_items TO align_api;
        GRANT SELECT, INSERT, UPDATE ON context_versions, context_conflicts, learning_outcomes, parse_artifacts, parser_checkpoints, processing_time_reservations, source_units, evidence_items TO align_worker;
        GRANT SELECT ON file_assets, normative_file_links TO align_worker;
        REVOKE DELETE ON context_versions, context_conflicts, learning_outcomes, file_assets, normative_file_links, parse_artifacts, parser_checkpoints, processing_time_reservations, source_units, evidence_items FROM align_api, align_worker;
        REVOKE UPDATE, DELETE ON evidence_items FROM align_api, align_worker
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS file_assets_are_retained ON file_assets;
        DROP TRIGGER IF EXISTS normative_file_links_are_immutable ON normative_file_links;
        DROP TRIGGER IF EXISTS evidence_items_are_immutable ON evidence_items;
        DROP TRIGGER IF EXISTS context_versions_immutable_after_approval ON context_versions;
        DROP TRIGGER IF EXISTS context_conflicts_draft_only ON context_conflicts;
        DROP TRIGGER IF EXISTS learning_outcomes_draft_only ON learning_outcomes;
        DROP TRIGGER IF EXISTS normative_context_file_scope ON normative_file_links;
        DROP TRIGGER IF EXISTS parse_artifact_context_scope ON parse_artifacts;
        DROP TRIGGER IF EXISTS evidence_context_scope ON evidence_items;
        DROP TRIGGER IF EXISTS course_approved_context_scope ON courses;
        DROP FUNCTION IF EXISTS protect_b1_context_child();
        DROP FUNCTION IF EXISTS protect_b1_context_file_link_scope();
        DROP FUNCTION IF EXISTS protect_b1_context_artifact_scope();
        DROP FUNCTION IF EXISTS protect_b1_context_evidence_scope();
        DROP FUNCTION IF EXISTS protect_b1_approved_course_pointer();
        DROP FUNCTION IF EXISTS reject_b1_evidence_change();
        DROP FUNCTION IF EXISTS protect_b1_approved_context();
        DROP TABLE IF EXISTS evidence_items;
        DROP TABLE IF EXISTS source_units;
        DROP TABLE IF EXISTS processing_time_reservations;
        DROP TABLE IF EXISTS parser_checkpoints;
        DROP TABLE IF EXISTS parse_artifacts;
        DROP TABLE IF EXISTS normative_file_links;
        DROP TABLE IF EXISTS file_assets;
        DROP TABLE IF EXISTS learning_outcomes;
        DROP TABLE IF EXISTS context_conflicts;
        ALTER TABLE courses DROP CONSTRAINT IF EXISTS fk_courses_approved_context_version;
        DROP TABLE IF EXISTS context_versions;
        """
    )
