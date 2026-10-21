"""B2 assignment packages, schema-v2 specifications, lint and groups.

Revision ID: 0004_b2_assignments_specs_groups
Revises: 0003_b1_provenance_guards
"""

from alembic import op

revision = "0004_b2_assignments_specs_groups"
down_revision = "0003_b1_provenance_guards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE assignments (
      id uuid PRIMARY KEY, course_id uuid NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
      title varchar(240) NOT NULL, active_version_id uuid, administrative_state varchar(16) NOT NULL DEFAULT 'open'
        CHECK (administrative_state IN ('open','closed','archived')),
      revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1), created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE criterion_set_versions (
      id uuid PRIMARY KEY, assignment_id uuid NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
      context_version_id uuid NOT NULL REFERENCES context_versions(id) ON DELETE RESTRICT,
      version_no integer NOT NULL CHECK (version_no >= 1), instruction_hash varchar(64) NOT NULL,
      state varchar(16) NOT NULL CHECK (state IN ('draft','approved')), content_hash varchar(64),
      approved_by uuid REFERENCES users(id), approved_at timestamptz,
      revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1), created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(assignment_id,version_no)
    );
    CREATE TABLE numeric_policy_versions (
      id uuid PRIMARY KEY, assignment_id uuid NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
      policy_key varchar(80) NOT NULL, version_no integer NOT NULL CHECK (version_no >= 1),
      state varchar(16) NOT NULL CHECK (state IN ('draft','approved')), payload_json jsonb NOT NULL,
      content_hash varchar(64) NOT NULL, approved_by uuid REFERENCES users(id), approved_at timestamptz,
      revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1), created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(assignment_id,policy_key,version_no)
    );
    CREATE TABLE specification_versions (
      id uuid PRIMARY KEY, assignment_id uuid NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
      version_no integer NOT NULL CHECK (version_no >= 1), schema_version integer NOT NULL CHECK (schema_version IN (1,2)),
      criterion_set_version_id uuid NOT NULL REFERENCES criterion_set_versions(id) ON DELETE RESTRICT,
      context_version_id uuid NOT NULL REFERENCES context_versions(id) ON DELETE RESTRICT,
      instruction_hash varchar(64) NOT NULL, payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
      content_hash varchar(64) NOT NULL, approved_lint_report_id uuid,
      approved_lint_resolution_revision integer CHECK (approved_lint_resolution_revision >= 1),
      fidelity_confirmed_by uuid REFERENCES users(id), fidelity_confirmed_at timestamptz,
      approved_by uuid REFERENCES users(id), approved_at timestamptz,
      state varchar(16) NOT NULL CHECK (state IN ('draft','approved')),
      revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1), created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(assignment_id,version_no)
    );
    CREATE TABLE assignment_versions (
      id uuid PRIMARY KEY, assignment_id uuid NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
      version_no integer NOT NULL CHECK (version_no >= 1), state varchar(16) NOT NULL CHECK (state IN ('draft','published')),
      context_version_id uuid NOT NULL REFERENCES context_versions(id) ON DELETE RESTRICT,
      instruction_original varchar(30000) NOT NULL CHECK (length(instruction_original) <= 30000),
      instruction_hash varchar(64) NOT NULL, instruction_summary varchar(2000),
      mode varchar(16) NOT NULL CHECK (mode IN ('individual','group')),
      opens_at timestamptz NOT NULL, due_at timestamptz NOT NULL CHECK (due_at > opens_at),
      late_policy varchar(32) NOT NULL CHECK (late_policy IN ('accept_with_label','reject_after_due')),
      limits_json jsonb NOT NULL DEFAULT '{}'::jsonb, criterion_set_version_id uuid REFERENCES criterion_set_versions(id),
      specification_version_id uuid REFERENCES specification_versions(id),
      package_approved_by uuid REFERENCES users(id), package_approved_at timestamptz,
      published_by uuid REFERENCES users(id), published_at timestamptz,
      revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1), created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(assignment_id,version_no)
    );
    ALTER TABLE assignments ADD CONSTRAINT fk_assignments_active_version
      FOREIGN KEY(active_version_id) REFERENCES assignment_versions(id) ON DELETE SET NULL;
    ALTER TABLE file_assets ADD CONSTRAINT fk_file_assets_assignment
      FOREIGN KEY(assignment_id) REFERENCES assignments(id) ON DELETE CASCADE;
    ALTER TABLE normative_file_links ADD CONSTRAINT fk_normative_links_assignment_version
      FOREIGN KEY(assignment_version_id) REFERENCES assignment_versions(id) ON DELETE RESTRICT;
    ALTER TABLE normative_file_links ADD CONSTRAINT uq_normative_assignment_file_role
      UNIQUE(file_asset_id,assignment_version_id,source_role);
    CREATE INDEX ix_assignments_course_active ON assignments(course_id,active_version_id);

    CREATE TABLE criteria (
      id uuid PRIMARY KEY, criterion_set_version_id uuid NOT NULL REFERENCES criterion_set_versions(id) ON DELETE CASCADE,
      code varchar(80) NOT NULL, title varchar(240) NOT NULL, descriptor varchar(4000) NOT NULL,
      display_order integer NOT NULL DEFAULT 0 CHECK (display_order >= 0), created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(criterion_set_version_id,code), UNIQUE(id,criterion_set_version_id)
    );
    CREATE TABLE requirements (
      id uuid PRIMARY KEY, criterion_set_version_id uuid NOT NULL REFERENCES criterion_set_versions(id) ON DELETE CASCADE,
      code varchar(80) NOT NULL, text varchar(2000) NOT NULL, mandatory boolean NOT NULL DEFAULT true, obligation_key varchar(240) NOT NULL,
      normative_source_refs_json jsonb NOT NULL DEFAULT '[]'::jsonb, created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(criterion_set_version_id,code), UNIQUE(id,criterion_set_version_id)
    );
    CREATE TABLE criterion_requirement_links (
      criterion_id uuid NOT NULL, requirement_id uuid NOT NULL, criterion_set_version_id uuid NOT NULL,
      PRIMARY KEY(criterion_id,requirement_id),
      CONSTRAINT fk_criterion_requirement_criterion_scope FOREIGN KEY(criterion_id,criterion_set_version_id)
        REFERENCES criteria(id,criterion_set_version_id) ON DELETE CASCADE,
      CONSTRAINT fk_criterion_requirement_requirement_scope FOREIGN KEY(requirement_id,criterion_set_version_id)
        REFERENCES requirements(id,criterion_set_version_id) ON DELETE CASCADE
    );
    CREATE TABLE criterion_outcome_links (
      criterion_id uuid NOT NULL REFERENCES criteria(id) ON DELETE CASCADE,
      learning_outcome_id uuid NOT NULL REFERENCES learning_outcomes(id) ON DELETE CASCADE,
      PRIMARY KEY(criterion_id,learning_outcome_id)
    );
    CREATE TABLE check_elements (
      id uuid PRIMARY KEY, specification_version_id uuid NOT NULL REFERENCES specification_versions(id) ON DELETE CASCADE,
      element_id uuid NOT NULL, element_code varchar(80) NOT NULL, criterion_id uuid NOT NULL REFERENCES criteria(id) ON DELETE RESTRICT,
      obligation_key varchar(240) NOT NULL, target_kind varchar(16) NOT NULL CHECK (target_kind IN ('requirement','descriptor','numeric')),
      decision_question varchar(2000) NOT NULL, grounding_mode varchar(32) NOT NULL
        CHECK (grounding_mode IN ('anchored_content','linked_relationship','comparable_numeric','human_only')),
      payload_json jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(specification_version_id,element_code)
    );
    CREATE TABLE element_requirement_links (
      check_element_id uuid NOT NULL REFERENCES check_elements(id) ON DELETE CASCADE,
      requirement_id uuid NOT NULL REFERENCES requirements(id) ON DELETE RESTRICT,
      PRIMARY KEY(check_element_id,requirement_id)
    );
    CREATE TABLE spec_lint_reports (
      id uuid PRIMARY KEY, specification_version_id uuid NOT NULL REFERENCES specification_versions(id) ON DELETE CASCADE,
      source_spec_revision integer NOT NULL CHECK (source_spec_revision >= 1), source_content_hash varchar(64) NOT NULL,
      source_package_hashes_json jsonb NOT NULL, registry_versions_json jsonb NOT NULL,
      initial_status varchar(16) NOT NULL CHECK (initial_status IN ('failed','needs_review','passed')),
      issues_json jsonb NOT NULL, resolution_revision integer NOT NULL DEFAULT 1 CHECK (resolution_revision >= 1),
      created_at timestamptz NOT NULL DEFAULT now()
    );
    ALTER TABLE specification_versions ADD CONSTRAINT fk_spec_versions_approved_lint
      FOREIGN KEY(approved_lint_report_id) REFERENCES spec_lint_reports(id) ON DELETE RESTRICT;
    CREATE TABLE spec_lint_resolutions (
      id uuid PRIMARY KEY, lint_report_id uuid NOT NULL REFERENCES spec_lint_reports(id) ON DELETE CASCADE,
      issue_code varchar(240) NOT NULL, resolution varchar(40) NOT NULL
        CHECK (resolution IN ('acknowledged_faithful','acknowledged_human_boundary')),
      reason varchar(2000) NOT NULL CHECK (length(btrim(reason)) > 0), actor_id uuid NOT NULL REFERENCES users(id),
      created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(lint_report_id,issue_code)
    );
    CREATE TABLE groups (
      id uuid PRIMARY KEY, assignment_id uuid NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
      name varchar(160) NOT NULL, active boolean NOT NULL DEFAULT true, revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
      created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(assignment_id,name), UNIQUE(id,assignment_id)
    );
    CREATE TABLE group_memberships (
      id uuid PRIMARY KEY, group_id uuid NOT NULL, assignment_id uuid NOT NULL, user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      active boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT fk_group_memberships_group_scope FOREIGN KEY(group_id,assignment_id) REFERENCES groups(id,assignment_id) ON DELETE CASCADE,
      UNIQUE(group_id,user_id)
    );
    CREATE UNIQUE INDEX uq_group_memberships_active_assignment_user ON group_memberships(assignment_id,user_id) WHERE active;

    CREATE FUNCTION protect_b2_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE state_value varchar(16);
    BEGIN
      IF TG_TABLE_NAME='assignment_versions' THEN state_value := OLD.state;
      ELSIF TG_TABLE_NAME='criterion_set_versions' OR TG_TABLE_NAME='specification_versions' OR TG_TABLE_NAME='numeric_policy_versions'
        THEN state_value := OLD.state;
      END IF;
      IF TG_OP IN ('UPDATE','DELETE') AND state_value IN ('published','approved') THEN
        RAISE EXCEPTION 'B2_APPROVED_SNAPSHOT_IMMUTABLE';
      END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER assignment_versions_snapshot_guard BEFORE UPDATE OR DELETE ON assignment_versions
      FOR EACH ROW EXECUTE FUNCTION protect_b2_snapshot();
    CREATE TRIGGER criterion_sets_snapshot_guard BEFORE UPDATE OR DELETE ON criterion_set_versions
      FOR EACH ROW EXECUTE FUNCTION protect_b2_snapshot();
    CREATE TRIGGER specification_versions_snapshot_guard BEFORE UPDATE OR DELETE ON specification_versions
      FOR EACH ROW EXECUTE FUNCTION protect_b2_snapshot();
    CREATE TRIGGER numeric_policy_snapshot_guard BEFORE UPDATE OR DELETE ON numeric_policy_versions
      FOR EACH ROW EXECUTE FUNCTION protect_b2_snapshot();

    CREATE FUNCTION protect_b2_children() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE target_state varchar(16); target_spec uuid; target_assignment uuid; group_assignment uuid; target_context uuid;
    BEGIN
      IF TG_TABLE_NAME='criteria' THEN
        SELECT state INTO target_state FROM criterion_set_versions WHERE id=COALESCE(NEW.criterion_set_version_id,OLD.criterion_set_version_id) FOR UPDATE;
      ELSIF TG_TABLE_NAME='requirements' THEN
        SELECT state INTO target_state FROM criterion_set_versions WHERE id=COALESCE(NEW.criterion_set_version_id,OLD.criterion_set_version_id) FOR UPDATE;
      ELSIF TG_TABLE_NAME='criterion_requirement_links' THEN
        SELECT state INTO target_state FROM criterion_set_versions WHERE id=COALESCE(NEW.criterion_set_version_id,OLD.criterion_set_version_id) FOR UPDATE;
      ELSIF TG_TABLE_NAME='criterion_outcome_links' THEN
        SELECT set_version.state INTO target_state FROM criterion_set_versions set_version
          JOIN criteria c ON c.criterion_set_version_id=set_version.id
          WHERE c.id=COALESCE(NEW.criterion_id,OLD.criterion_id) FOR UPDATE OF set_version;
      ELSIF TG_TABLE_NAME='check_elements' THEN
        SELECT state INTO target_state FROM specification_versions WHERE id=COALESCE(NEW.specification_version_id,OLD.specification_version_id) FOR UPDATE;
        IF TG_OP IN ('INSERT','UPDATE') THEN
          SELECT criterion_set_version_id INTO target_spec FROM specification_versions WHERE id=NEW.specification_version_id;
          SELECT criterion_set_version_id INTO target_assignment FROM criteria WHERE id=NEW.criterion_id;
          IF target_spec IS DISTINCT FROM target_assignment THEN RAISE EXCEPTION 'CHECK_ELEMENT_CRITERION_SCOPE_MISMATCH'; END IF;
        END IF;
      ELSIF TG_TABLE_NAME='element_requirement_links' THEN
        SELECT s.state INTO target_state FROM specification_versions s JOIN check_elements e ON e.specification_version_id=s.id
          WHERE e.id=COALESCE(NEW.check_element_id,OLD.check_element_id) FOR UPDATE OF s;
        SELECT s.criterion_set_version_id,e.criterion_id INTO target_spec,target_assignment
          FROM specification_versions s JOIN check_elements e ON e.specification_version_id=s.id
          WHERE e.id=COALESCE(NEW.check_element_id,OLD.check_element_id);
        IF TG_OP IN ('INSERT','UPDATE') AND (
          NOT EXISTS(SELECT 1 FROM requirements r WHERE r.id=NEW.requirement_id AND r.criterion_set_version_id=target_spec)
          OR NOT EXISTS(SELECT 1 FROM criterion_requirement_links l WHERE l.criterion_id=target_assignment AND l.requirement_id=NEW.requirement_id)
        ) THEN RAISE EXCEPTION 'ELEMENT_REQUIREMENT_SCOPE_MISMATCH'; END IF;
      END IF;
      IF target_state IN ('approved','published') THEN RAISE EXCEPTION 'B2_APPROVED_CHILD_IMMUTABLE'; END IF;
      IF TG_TABLE_NAME='criterion_outcome_links' AND TG_OP IN ('INSERT','UPDATE') THEN
        SELECT csv.context_version_id INTO target_context FROM criteria c
          JOIN criterion_set_versions csv ON csv.id=c.criterion_set_version_id
          WHERE c.id=NEW.criterion_id;
        IF NOT EXISTS(SELECT 1 FROM learning_outcomes lo WHERE lo.id=NEW.learning_outcome_id AND lo.context_version_id=target_context)
          THEN RAISE EXCEPTION 'CRITERION_OUTCOME_CONTEXT_MISMATCH'; END IF;
      END IF;
      IF TG_TABLE_NAME='group_memberships' AND TG_OP IN ('INSERT','UPDATE') THEN
        SELECT assignment_id INTO group_assignment FROM groups WHERE id=NEW.group_id;
        IF group_assignment IS DISTINCT FROM NEW.assignment_id THEN RAISE EXCEPTION 'GROUP_ASSIGNMENT_SCOPE_MISMATCH'; END IF;
      ELSIF TG_TABLE_NAME='normative_file_links' THEN
        IF TG_OP IN ('UPDATE','DELETE') THEN
          IF OLD.assignment_version_id IS NOT NULL THEN
            SELECT state INTO target_state FROM assignment_versions WHERE id=OLD.assignment_version_id FOR UPDATE;
          ELSIF OLD.context_version_id IS NOT NULL THEN
            SELECT state INTO target_state FROM context_versions WHERE id=OLD.context_version_id FOR UPDATE;
          END IF;
        END IF;
        IF TG_OP IN ('INSERT','UPDATE') THEN
          IF NEW.assignment_version_id IS NOT NULL THEN
            SELECT state,assignment_id INTO target_state,target_assignment FROM assignment_versions WHERE id=NEW.assignment_version_id FOR UPDATE;
            IF NEW.source_role <> 'rubric' OR NOT EXISTS(
              SELECT 1 FROM file_assets WHERE id=NEW.file_asset_id AND assignment_id=target_assignment AND purpose='rubric'
            ) THEN RAISE EXCEPTION 'NORMATIVE_RUBRIC_SCOPE_MISMATCH'; END IF;
          ELSIF NEW.context_version_id IS NOT NULL THEN
            SELECT state INTO target_state FROM context_versions WHERE id=NEW.context_version_id FOR UPDATE;
          END IF;
        END IF;
      END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER criteria_draft_guard BEFORE INSERT OR UPDATE OR DELETE ON criteria FOR EACH ROW EXECUTE FUNCTION protect_b2_children();
    CREATE TRIGGER requirements_draft_guard BEFORE INSERT OR UPDATE OR DELETE ON requirements FOR EACH ROW EXECUTE FUNCTION protect_b2_children();
    CREATE TRIGGER criterion_requirement_draft_guard BEFORE INSERT OR UPDATE OR DELETE ON criterion_requirement_links FOR EACH ROW EXECUTE FUNCTION protect_b2_children();
    CREATE TRIGGER criterion_outcome_draft_guard BEFORE INSERT OR UPDATE OR DELETE ON criterion_outcome_links FOR EACH ROW EXECUTE FUNCTION protect_b2_children();
    CREATE TRIGGER check_elements_draft_guard BEFORE INSERT OR UPDATE OR DELETE ON check_elements FOR EACH ROW EXECUTE FUNCTION protect_b2_children();
    CREATE TRIGGER element_requirement_draft_guard BEFORE INSERT OR UPDATE OR DELETE ON element_requirement_links FOR EACH ROW EXECUTE FUNCTION protect_b2_children();
    CREATE TRIGGER group_membership_scope_guard BEFORE INSERT OR UPDATE ON group_memberships FOR EACH ROW EXECUTE FUNCTION protect_b2_children();
    CREATE TRIGGER normative_file_link_scope_guard BEFORE INSERT OR UPDATE OR DELETE ON normative_file_links FOR EACH ROW EXECUTE FUNCTION protect_b2_children();

    CREATE FUNCTION protect_b2_lint_report() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'B2_LINT_REPORT_IMMUTABLE'; END IF;
      IF NEW.id IS DISTINCT FROM OLD.id OR NEW.specification_version_id IS DISTINCT FROM OLD.specification_version_id
        OR NEW.source_spec_revision IS DISTINCT FROM OLD.source_spec_revision OR NEW.source_content_hash IS DISTINCT FROM OLD.source_content_hash
        OR NEW.source_package_hashes_json IS DISTINCT FROM OLD.source_package_hashes_json OR NEW.registry_versions_json IS DISTINCT FROM OLD.registry_versions_json
        OR NEW.initial_status IS DISTINCT FROM OLD.initial_status OR NEW.issues_json IS DISTINCT FROM OLD.issues_json
        OR NEW.created_at IS DISTINCT FROM OLD.created_at OR NEW.resolution_revision <> OLD.resolution_revision + 1 THEN
        RAISE EXCEPTION 'B2_LINT_REPORT_IMMUTABLE';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER lint_report_update_guard BEFORE UPDATE OR DELETE ON spec_lint_reports FOR EACH ROW EXECUTE FUNCTION protect_b2_lint_report();
    CREATE FUNCTION protect_b2_resolution() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RAISE EXCEPTION 'B2_LINT_RESOLUTION_APPEND_ONLY'; END $$;
    CREATE TRIGGER lint_resolution_append_only BEFORE UPDATE OR DELETE ON spec_lint_resolutions FOR EACH ROW EXECUTE FUNCTION protect_b2_resolution();
    """)


def downgrade() -> None:
    op.execute("""
    DROP TRIGGER lint_resolution_append_only ON spec_lint_resolutions;
    DROP FUNCTION protect_b2_resolution();
    DROP TRIGGER lint_report_update_guard ON spec_lint_reports;
    DROP FUNCTION protect_b2_lint_report();
    DROP TRIGGER group_membership_scope_guard ON group_memberships;
    DROP TRIGGER normative_file_link_scope_guard ON normative_file_links;
    DROP TRIGGER element_requirement_draft_guard ON element_requirement_links;
    DROP TRIGGER check_elements_draft_guard ON check_elements;
    DROP TRIGGER criterion_outcome_draft_guard ON criterion_outcome_links;
    DROP TRIGGER criterion_requirement_draft_guard ON criterion_requirement_links;
    DROP TRIGGER requirements_draft_guard ON requirements;
    DROP TRIGGER criteria_draft_guard ON criteria;
    DROP FUNCTION protect_b2_children();
    DROP TRIGGER numeric_policy_snapshot_guard ON numeric_policy_versions;
    DROP TRIGGER specification_versions_snapshot_guard ON specification_versions;
    DROP TRIGGER criterion_sets_snapshot_guard ON criterion_set_versions;
    DROP TRIGGER assignment_versions_snapshot_guard ON assignment_versions;
    DROP FUNCTION protect_b2_snapshot();
    ALTER TABLE normative_file_links DROP CONSTRAINT fk_normative_links_assignment_version;
    ALTER TABLE normative_file_links DROP CONSTRAINT uq_normative_assignment_file_role;
    ALTER TABLE file_assets DROP CONSTRAINT fk_file_assets_assignment;
    ALTER TABLE specification_versions DROP CONSTRAINT fk_spec_versions_approved_lint;
    ALTER TABLE assignments DROP CONSTRAINT fk_assignments_active_version;
    DROP TABLE group_memberships, groups, spec_lint_resolutions, spec_lint_reports, element_requirement_links,
      check_elements, criterion_outcome_links, criterion_requirement_links, requirements, criteria, assignment_versions, specification_versions,
      numeric_policy_versions, criterion_set_versions, assignments;
    """)
