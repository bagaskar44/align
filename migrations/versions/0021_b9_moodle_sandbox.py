"""Registered LTI platforms and explicit Moodle mapping/import provenance.

Revision ID: 0021_b9_moodle_sandbox
Revises: 0020_paid_call_run_caps
"""

from alembic import op

revision = "0021_b9_moodle_sandbox"
down_revision = "0020_paid_call_run_caps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE lti_platforms (
      id uuid PRIMARY KEY,
      issuer varchar(500) NOT NULL,
      client_id varchar(240) NOT NULL,
      deployment_id varchar(240) NOT NULL,
      authorization_url varchar(1000) NOT NULL,
      token_url varchar(1000) NOT NULL,
      jwks_url varchar(1000) NOT NULL,
      moodle_origin varchar(500) NOT NULL,
      active boolean NOT NULL DEFAULT true,
      created_by uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
      revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_lti_platform_registration UNIQUE (issuer, client_id, deployment_id)
    );

    CREATE TABLE lti_login_flows (
      id uuid PRIMARY KEY,
      platform_id uuid NOT NULL REFERENCES lti_platforms(id) ON DELETE CASCADE,
      state_hash varchar(64) NOT NULL UNIQUE,
      nonce_hash varchar(64) NOT NULL,
      correlation_hash varchar(64) NOT NULL,
      target_link_uri varchar(1000) NOT NULL,
      expires_at timestamptz NOT NULL,
      consumed_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT ck_lti_login_flow_expiry CHECK (expires_at > created_at)
    );
    CREATE INDEX ix_lti_login_flows_expiry ON lti_login_flows (expires_at, consumed_at);

    CREATE TABLE lti_identity_bindings (
      id uuid PRIMARY KEY,
      platform_id uuid NOT NULL REFERENCES lti_platforms(id) ON DELETE CASCADE,
      subject varchar(500) NOT NULL,
      moodle_user_id integer NOT NULL CHECK (moodle_user_id > 0),
      user_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
      active boolean NOT NULL DEFAULT true,
      created_by uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
      revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_lti_binding_subject UNIQUE (platform_id, subject),
      CONSTRAINT uq_lti_binding_moodle_user UNIQUE (platform_id, moodle_user_id),
      CONSTRAINT uq_lti_binding_platform_id UNIQUE (platform_id, id)
    );

    CREATE TABLE lti_course_permissions (
      id uuid PRIMARY KEY,
      platform_id uuid NOT NULL REFERENCES lti_platforms(id) ON DELETE CASCADE,
      course_id uuid NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
      active boolean NOT NULL DEFAULT true,
      changed_by uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
      created_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_lti_course_permission UNIQUE (platform_id, course_id)
    );

    CREATE TABLE lms_course_assignment_mappings (
      id uuid PRIMARY KEY,
      platform_id uuid NOT NULL REFERENCES lti_platforms(id) ON DELETE CASCADE,
      lti_context_id varchar(500) NOT NULL,
      lti_resource_link_id varchar(500) NOT NULL,
      moodle_course_id integer NOT NULL CHECK (moodle_course_id > 0),
      moodle_assignment_id integer NOT NULL CHECK (moodle_assignment_id > 0),
      local_course_id uuid NOT NULL REFERENCES courses(id) ON DELETE RESTRICT,
      assignment_version_id uuid NOT NULL REFERENCES assignment_versions(id) ON DELETE RESTRICT,
      active boolean NOT NULL DEFAULT true,
      changed_by uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
      revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_lms_mapping_platform_id UNIQUE (platform_id, id)
    );
    CREATE UNIQUE INDEX uq_lms_mapping_active_external
      ON lms_course_assignment_mappings (platform_id, lti_context_id, lti_resource_link_id)
      WHERE active IS TRUE;
    CREATE INDEX ix_lms_mapping_local_course
      ON lms_course_assignment_mappings (local_course_id, active);

    CREATE TABLE lms_group_mappings (
      id uuid PRIMARY KEY,
      mapping_id uuid NOT NULL REFERENCES lms_course_assignment_mappings(id) ON DELETE CASCADE,
      moodle_group_id integer NOT NULL CHECK (moodle_group_id > 0),
      local_group_id uuid NOT NULL REFERENCES groups(id) ON DELETE RESTRICT,
      active boolean NOT NULL DEFAULT true,
      changed_by uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
      revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_lms_group_moodle_id UNIQUE (mapping_id, moodle_group_id),
      CONSTRAINT uq_lms_group_local_id UNIQUE (mapping_id, local_group_id)
    );

    CREATE TABLE lti_session_bindings (
      session_id uuid PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
      platform_id uuid NOT NULL REFERENCES lti_platforms(id) ON DELETE RESTRICT,
      identity_binding_id uuid NOT NULL REFERENCES lti_identity_bindings(id) ON DELETE RESTRICT,
      mapping_id uuid NOT NULL REFERENCES lms_course_assignment_mappings(id) ON DELETE RESTRICT,
      mapping_revision integer NOT NULL CHECK (mapping_revision >= 1),
      CONSTRAINT fk_lti_session_binding_platform_identity
        FOREIGN KEY (platform_id, identity_binding_id)
        REFERENCES lti_identity_bindings(platform_id, id) ON DELETE RESTRICT,
      CONSTRAINT fk_lti_session_binding_platform_mapping
        FOREIGN KEY (platform_id, mapping_id)
        REFERENCES lms_course_assignment_mappings(platform_id, id) ON DELETE RESTRICT,
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX ix_lti_session_bindings_platform ON lti_session_bindings (platform_id);
    CREATE INDEX ix_lti_session_bindings_identity ON lti_session_bindings (identity_binding_id);

    CREATE TABLE lms_import_events (
      id uuid PRIMARY KEY,
      platform_id uuid NOT NULL REFERENCES lti_platforms(id) ON DELETE RESTRICT,
      mapping_id uuid NOT NULL REFERENCES lms_course_assignment_mappings(id) ON DELETE RESTRICT,
      mapping_revision integer NOT NULL CHECK (mapping_revision >= 1),
      moodle_attempt_id varchar(240) NOT NULL,
      moodle_user_id integer CHECK (moodle_user_id > 0),
      moodle_group_id integer CHECK (moodle_group_id > 0),
      moodle_submitted_at timestamptz,
      source_fingerprint varchar(64) NOT NULL CHECK (source_fingerprint ~ '^[a-f0-9]{64}$'),
      fingerprint varchar(64) NOT NULL UNIQUE CHECK (fingerprint ~ '^[a-f0-9]{64}$'),
      local_submission_id uuid REFERENCES submissions(id) ON DELETE RESTRICT,
      submission_version_id uuid REFERENCES submission_versions(id) ON DELETE RESTRICT,
      job_id uuid REFERENCES jobs(id) ON DELETE RESTRICT,
      imported_by uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
      status varchar(16) NOT NULL CHECK (status IN ('imported','blocked')),
      safe_detail varchar(240),
      CONSTRAINT fk_lms_import_platform_mapping
        FOREIGN KEY (platform_id, mapping_id)
        REFERENCES lms_course_assignment_mappings(platform_id, id) ON DELETE RESTRICT,
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX ix_lms_import_mapping_created ON lms_import_events (mapping_id, created_at);

    CREATE FUNCTION protect_lms_import_event() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
      RAISE EXCEPTION 'LMS_IMPORT_EVENT_IMMUTABLE';
    END $$;
    CREATE TRIGGER lms_import_events_are_immutable
      BEFORE UPDATE OR DELETE ON lms_import_events
      FOR EACH ROW EXECUTE FUNCTION protect_lms_import_event();

    -- API owns all LMS writes. Operator CLI can administer registration and
    -- identity/course permissions but cannot create academic imports.
    GRANT SELECT, INSERT, UPDATE, DELETE ON lti_platforms, lti_login_flows,
      lti_identity_bindings, lti_course_permissions, lms_course_assignment_mappings,
      lms_group_mappings, lti_session_bindings, lms_import_events TO align_api;
    GRANT SELECT, INSERT, UPDATE ON lti_platforms, lti_identity_bindings,
      lti_course_permissions TO align_operator;
    GRANT SELECT ON lms_course_assignment_mappings, lms_group_mappings, lms_import_events TO align_operator;
    REVOKE ALL ON lti_platforms, lti_login_flows, lti_identity_bindings,
      lti_course_permissions, lms_course_assignment_mappings, lti_session_bindings,
      lms_group_mappings, lms_import_events FROM align_worker;
    """)


def downgrade() -> None:
    op.execute("""
    DROP TRIGGER IF EXISTS lms_import_events_are_immutable ON lms_import_events;
    DROP FUNCTION IF EXISTS protect_lms_import_event();
    DROP TABLE IF EXISTS lms_import_events;
    DROP TABLE IF EXISTS lti_session_bindings;
    DROP TABLE IF EXISTS lms_group_mappings;
    DROP TABLE IF EXISTS lms_course_assignment_mappings;
    DROP TABLE IF EXISTS lti_course_permissions;
    DROP TABLE IF EXISTS lti_identity_bindings;
    DROP TABLE IF EXISTS lti_login_flows;
    DROP TABLE IF EXISTS lti_platforms;
    """)
