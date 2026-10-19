"""B0 identity, course metadata and operational foundations.

Revision ID: 0001_b0_foundation
Revises:
"""

from alembic import op

revision = "0001_b0_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE version_text text;
        BEGIN
          SELECT extversion INTO version_text FROM pg_extension WHERE extname = 'vector';
          IF version_text IS NULL THEN
            RAISE EXCEPTION 'pgvector extension must be installed by the database bootstrap';
          END IF;
          IF split_part(version_text, '.', 1)::integer = 0
             AND split_part(version_text, '.', 2)::integer < 8 THEN
            RAISE EXCEPTION 'pgvector 0.8 or newer required, found %', version_text;
          END IF;
        END $$;

        CREATE TABLE users (
          id uuid PRIMARY KEY,
          normalized_email varchar(320) NOT NULL UNIQUE,
          display_name varchar(160) NOT NULL,
          password_hash varchar(512) NOT NULL,
          global_role varchar(16) NOT NULL CONSTRAINT ck_users_global_role CHECK (global_role IN ('admin','member')),
          active boolean NOT NULL DEFAULT true,
          must_change_password boolean NOT NULL DEFAULT false,
          revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE sessions (
          id uuid PRIMARY KEY,
          user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          token_hash varchar(64) NOT NULL UNIQUE,
          csrf_hash varchar(64) NOT NULL,
          last_seen_at timestamptz NOT NULL,
          expires_at timestamptz NOT NULL,
          absolute_expires_at timestamptz NOT NULL,
          revoked_at timestamptz,
          revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_sessions_user_active ON sessions(user_id, revoked_at);

        CREATE TABLE courses (
          id uuid PRIMARY KEY,
          code varchar(64) NOT NULL UNIQUE,
          title varchar(240) NOT NULL,
          active boolean NOT NULL DEFAULT true,
          approved_context_version_id uuid,
          revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE enrollments (
          id uuid PRIMARY KEY,
          course_id uuid NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
          user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          role varchar(16) NOT NULL CHECK (role IN ('lecturer','student')),
          active boolean NOT NULL DEFAULT true,
          revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_enrollments_course_user UNIQUE(course_id, user_id)
        );
        CREATE INDEX ix_enrollments_user_active ON enrollments(user_id, active);

        CREATE TABLE method_manifests (
          id uuid PRIMARY KEY,
          kind varchar(32) NOT NULL CHECK (kind IN ('parser','parser_resource','model','retrieval','rules','prompt','routing','batching')),
          version_label varchar(120) NOT NULL,
          content_json jsonb NOT NULL,
          sha256 varchar(64) NOT NULL UNIQUE CHECK (sha256 ~ '^[a-f0-9]{64}$'),
          verified_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE release_manifests (
          id uuid PRIMARY KEY,
          version_label varchar(120) NOT NULL,
          parser_manifest_id uuid NOT NULL REFERENCES method_manifests(id),
          model_manifest_id uuid NOT NULL REFERENCES method_manifests(id),
          retrieval_manifest_id uuid NOT NULL REFERENCES method_manifests(id),
          rules_manifest_id uuid NOT NULL REFERENCES method_manifests(id),
          prompt_manifest_id uuid NOT NULL REFERENCES method_manifests(id),
          routing_policy_manifest_id uuid NOT NULL REFERENCES method_manifests(id),
          batching_policy_manifest_id uuid NOT NULL REFERENCES method_manifests(id),
          grounding_registry_version varchar(80) NOT NULL,
          lint_registry_version varchar(80) NOT NULL,
          manifest_payload jsonb NOT NULL,
          config_hash varchar(64) NOT NULL UNIQUE CHECK (config_hash ~ '^[a-f0-9]{64}$'),
          state varchar(16) NOT NULL CHECK (state IN ('draft','verified','active','retired')),
          activated_by uuid REFERENCES users(id),
          activated_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE jobs (
          id uuid PRIMARY KEY,
          type varchar(64) NOT NULL,
          subject_id uuid NOT NULL,
          scope_json jsonb NOT NULL,
          input_fingerprint varchar(64) NOT NULL CHECK (input_fingerprint ~ '^[a-f0-9]{64}$'),
          status varchar(24) NOT NULL CHECK (status IN ('queued','running','ready_for_review','blocked','failed')),
          stage varchar(64),
          lease_until timestamptz,
          lease_fence integer NOT NULL DEFAULT 0 CHECK (lease_fence >= 0),
          completion_kind varchar(32),
          last_error_code varchar(120),
          job_limits_json jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_jobs_type_input_fingerprint UNIQUE(type, input_fingerprint)
        );
        CREATE INDEX ix_jobs_status_created ON jobs(status, created_at);

        CREATE TABLE job_stages (
          id uuid PRIMARY KEY,
          job_id uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
          stage_name varchar(64) NOT NULL,
          input_hash varchar(64) NOT NULL CHECK (input_hash ~ '^[a-f0-9]{64}$'),
          status varchar(16) NOT NULL CHECK (status IN ('queued','running','succeeded','blocked','failed')),
          output_refs_json jsonb NOT NULL DEFAULT '{}'::jsonb,
          attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
          started_at timestamptz,
          finished_at timestamptz,
          failure_code varchar(120),
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_job_stages_fingerprint UNIQUE(job_id, stage_name, input_hash)
        );

        CREATE TABLE outbox_events (
          id uuid PRIMARY KEY,
          event_type varchar(80) NOT NULL,
          subject_id uuid NOT NULL,
          payload_json jsonb NOT NULL,
          delivered_at timestamptz,
          next_attempt_at timestamptz,
          attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
          created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_outbox_pending ON outbox_events(delivered_at, created_at);

        CREATE TABLE budget_accounts (
          id uuid PRIMARY KEY,
          currency varchar(3) NOT NULL DEFAULT 'USD' CHECK (currency = 'USD'),
          confirmed_total numeric(18,8) NOT NULL CHECK (confirmed_total >= 0),
          reserve_floor numeric(18,8) NOT NULL CHECK (reserve_floor >= 0),
          planned_topup numeric(18,8) NOT NULL DEFAULT 0 CHECK (planned_topup >= 0),
          active boolean NOT NULL DEFAULT true,
          revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE usage_reservations (
          id uuid PRIMARY KEY,
          budget_account_id uuid NOT NULL REFERENCES budget_accounts(id),
          job_id uuid NOT NULL REFERENCES jobs(id),
          call_id uuid NOT NULL UNIQUE,
          amount_reserved numeric(18,8) NOT NULL CHECK (amount_reserved >= 0),
          status varchar(32) NOT NULL CHECK (status IN ('held','settled','released','reconciliation_required')),
          created_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE usage_entries (
          id uuid PRIMARY KEY,
          call_id uuid NOT NULL UNIQUE,
          logical_call_id uuid NOT NULL,
          attempt_no integer NOT NULL CHECK (attempt_no >= 1),
          provider_request_id varchar(240),
          manifest_id uuid NOT NULL REFERENCES method_manifests(id),
          usage_json jsonb NOT NULL,
          rates_snapshot_json jsonb NOT NULL,
          actual_cost numeric(18,8) CHECK (actual_cost IS NULL OR actual_cost >= 0),
          status varchar(16) NOT NULL CHECK (status IN ('known','pending')),
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_usage_entries_logical_attempt UNIQUE(logical_call_id, attempt_no)
        );

        CREATE TABLE audit_events (
          id uuid PRIMARY KEY,
          actor_user_id uuid REFERENCES users(id),
          worker_id varchar(120),
          action varchar(120) NOT NULL,
          object_type varchar(120) NOT NULL,
          object_id uuid,
          old_version_refs_json jsonb,
          new_version_refs_json jsonb,
          request_id uuid NOT NULL,
          occurred_at timestamptz NOT NULL DEFAULT now(),
          created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_audit_events_object ON audit_events(object_type, object_id, occurred_at);

        CREATE TABLE idempotency_records (
          id uuid PRIMARY KEY,
          actor_id uuid NOT NULL,
          method varchar(10) NOT NULL,
          route varchar(240) NOT NULL,
          key varchar(36) NOT NULL,
          body_hash varchar(64) NOT NULL CHECK (body_hash ~ '^[a-f0-9]{64}$'),
          response_status integer NOT NULL,
          response_json jsonb,
          expires_at timestamptz NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_idempotency_actor_method_route_key UNIQUE(actor_id, method, route, key)
        );
        CREATE INDEX ix_idempotency_expires ON idempotency_records(expires_at);

        CREATE TABLE operations (
          id uuid PRIMARY KEY,
          kind varchar(80) NOT NULL,
          idempotency_key varchar(120) NOT NULL,
          status varchar(16) NOT NULL CHECK (status IN ('queued','running','succeeded','failed')),
          attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
          lease_until timestamptz,
          result_json jsonb,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_operations_kind_idempotency UNIQUE(kind, idempotency_key)
        );

        CREATE FUNCTION reject_immutable_row_change() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'IMMUTABLE_ROW';
        END $$;
        CREATE TRIGGER method_manifests_are_immutable
          BEFORE UPDATE OR DELETE ON method_manifests
          FOR EACH ROW EXECUTE FUNCTION reject_immutable_row_change();
        CREATE TRIGGER audit_events_are_append_only
          BEFORE UPDATE OR DELETE ON audit_events
          FOR EACH ROW EXECUTE FUNCTION reject_immutable_row_change();
        CREATE TRIGGER usage_entries_are_immutable
          BEFORE UPDATE OR DELETE ON usage_entries
          FOR EACH ROW EXECUTE FUNCTION reject_immutable_row_change();

        CREATE FUNCTION protect_release_manifest_payload() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF (NEW.version_label, NEW.parser_manifest_id, NEW.model_manifest_id,
              NEW.retrieval_manifest_id, NEW.rules_manifest_id, NEW.prompt_manifest_id,
              NEW.routing_policy_manifest_id, NEW.batching_policy_manifest_id,
              NEW.grounding_registry_version, NEW.lint_registry_version,
              NEW.manifest_payload, NEW.config_hash, NEW.created_at)
             IS DISTINCT FROM
             (OLD.version_label, OLD.parser_manifest_id, OLD.model_manifest_id,
              OLD.retrieval_manifest_id, OLD.rules_manifest_id, OLD.prompt_manifest_id,
              OLD.routing_policy_manifest_id, OLD.batching_policy_manifest_id,
              OLD.grounding_registry_version, OLD.lint_registry_version,
              OLD.manifest_payload, OLD.config_hash, OLD.created_at) THEN
            RAISE EXCEPTION 'RELEASE_PAYLOAD_IMMUTABLE';
          END IF;
          IF NOT (
            (OLD.state = 'draft' AND NEW.state = 'verified'
              AND NEW.activated_by IS NOT DISTINCT FROM OLD.activated_by
              AND NEW.activated_at IS NOT DISTINCT FROM OLD.activated_at)
            OR (OLD.state = 'verified' AND NEW.state = 'active'
              AND NEW.activated_by IS NOT NULL AND NEW.activated_at IS NOT NULL)
            OR (OLD.state = 'active' AND NEW.state = 'retired'
              AND NEW.activated_by IS NOT DISTINCT FROM OLD.activated_by
              AND NEW.activated_at IS NOT DISTINCT FROM OLD.activated_at)
          ) THEN
            RAISE EXCEPTION 'RELEASE_STATE_TRANSITION_INVALID';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER release_manifest_payload_is_immutable
          BEFORE UPDATE ON release_manifests
          FOR EACH ROW EXECUTE FUNCTION protect_release_manifest_payload();
        CREATE TRIGGER release_manifests_are_retained
          BEFORE DELETE ON release_manifests
          FOR EACH ROW EXECUTE FUNCTION reject_immutable_row_change();
        CREATE UNIQUE INDEX uq_release_single_active ON release_manifests ((state)) WHERE state = 'active';

        CREATE FUNCTION protect_outbox_payload() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF (NEW.event_type, NEW.subject_id, NEW.payload_json, NEW.created_at)
             IS DISTINCT FROM (OLD.event_type, OLD.subject_id, OLD.payload_json, OLD.created_at) THEN
            RAISE EXCEPTION 'OUTBOX_PAYLOAD_IMMUTABLE';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER outbox_payload_is_immutable
          BEFORE UPDATE ON outbox_events
          FOR EACH ROW EXECUTE FUNCTION protect_outbox_payload();
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO align_api")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO align_api")
    op.execute("REVOKE UPDATE, DELETE ON audit_events, method_manifests, usage_entries FROM align_api")
    op.execute("REVOKE UPDATE, DELETE ON budget_accounts FROM align_api")
    op.execute("REVOKE DELETE ON users, sessions, courses, enrollments, release_manifests, outbox_events, usage_reservations FROM align_api")
    op.execute("GRANT SELECT ON method_manifests, release_manifests, budget_accounts, usage_reservations, usage_entries TO align_operator")
    op.execute("GRANT SELECT ON method_manifests, release_manifests, jobs TO align_worker")
    op.execute("GRANT SELECT, UPDATE ON jobs, operations, usage_reservations TO align_worker")
    op.execute("GRANT SELECT, INSERT, UPDATE ON job_stages, outbox_events TO align_worker")
    op.execute("GRANT SELECT, INSERT ON usage_entries, audit_events TO align_worker")
    op.execute("REVOKE UPDATE, DELETE ON audit_events, usage_entries FROM align_worker")


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS operations;
        DROP TABLE IF EXISTS idempotency_records;
        DROP TABLE IF EXISTS audit_events;
        DROP TABLE IF EXISTS usage_entries;
        DROP TABLE IF EXISTS usage_reservations;
        DROP TABLE IF EXISTS budget_accounts;
        DROP TABLE IF EXISTS outbox_events;
        DROP TABLE IF EXISTS job_stages;
        DROP TABLE IF EXISTS jobs;
        DROP TABLE IF EXISTS release_manifests;
        DROP TABLE IF EXISTS method_manifests;
        DROP TABLE IF EXISTS enrollments;
        DROP TABLE IF EXISTS courses;
        DROP TABLE IF EXISTS sessions;
        DROP TABLE IF EXISTS users;
        DROP FUNCTION IF EXISTS protect_outbox_payload();
        DROP FUNCTION IF EXISTS protect_release_manifest_payload();
        DROP FUNCTION IF EXISTS reject_immutable_row_change();
        """
    )
