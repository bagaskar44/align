"""B6 append-only finance records, release bindings and report eligibility.

Revision ID: 0017_b6_finance_numeric
Revises: 0016_b5_drafting_reads
"""

from alembic import op

revision = "0017_b6_finance_numeric"
down_revision = "0016_b5_drafting_reads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The original constraint was emitted inline without a name, so PostgreSQL
    # assigned the standard ``<table>_<column>_check`` identifier.
    op.execute("ALTER TABLE method_manifests DROP CONSTRAINT method_manifests_kind_check")
    op.execute("ALTER TABLE method_manifests ADD CONSTRAINT ck_method_manifests_kind CHECK(kind IN ('parser','parser_resource','model','rates','retrieval','rules','prompt','routing','batching'))")
    op.execute("""
    CREATE TABLE policy_validation_reports (
      id uuid PRIMARY KEY,
      report_hash varchar(64) NOT NULL UNIQUE,
      status varchar(16) NOT NULL CHECK(status IN ('eligible','ineligible','incomplete')),
      data_mode varchar(16) NOT NULL CHECK(data_mode IN ('synthetic','institutional')),
      candidate_core_hash varchar(64) NOT NULL,
      baseline_core_hash varchar(64) NOT NULL,
      report_json jsonb NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TRIGGER policy_validation_reports_are_immutable
      BEFORE UPDATE OR DELETE ON policy_validation_reports
      FOR EACH ROW EXECUTE FUNCTION reject_immutable_row_change();
    """)
    op.execute("ALTER TABLE release_manifests ADD COLUMN parser_resource_manifest_id uuid REFERENCES method_manifests(id)")
    op.execute("ALTER TABLE release_manifests ADD COLUMN rate_manifest_id uuid REFERENCES method_manifests(id)")
    op.execute("ALTER TABLE release_manifests ADD COLUMN policy_validation_report_id uuid REFERENCES policy_validation_reports(id) ON DELETE RESTRICT")
    op.execute("ALTER TABLE budget_accounts ADD COLUMN spend_frozen boolean NOT NULL DEFAULT false")
    op.execute("ALTER TABLE budget_accounts ADD COLUMN freeze_reason varchar(240)")
    op.execute("CREATE UNIQUE INDEX uq_budget_accounts_single_active ON budget_accounts (active) WHERE active IS TRUE")
    op.execute("ALTER TABLE usage_reservations ADD COLUMN bounds_json jsonb NOT NULL DEFAULT '{}'::jsonb")
    op.execute("ALTER TABLE usage_reservations ADD COLUMN logical_call_id uuid")
    op.execute("ALTER TABLE usage_reservations ADD COLUMN attempt_no integer NOT NULL DEFAULT 1")
    op.execute("UPDATE usage_reservations SET logical_call_id = call_id WHERE logical_call_id IS NULL")
    op.execute("ALTER TABLE usage_reservations ALTER COLUMN logical_call_id SET NOT NULL")
    op.execute("ALTER TABLE usage_reservations ADD CONSTRAINT uq_usage_reservations_logical_attempt UNIQUE(logical_call_id,attempt_no)")
    op.execute("ALTER TABLE usage_reservations ADD CONSTRAINT ck_usage_reservations_attempt_no CHECK(attempt_no >= 1)")
    op.execute("""
    CREATE TABLE usage_reconciliations (
      id uuid PRIMARY KEY,
      usage_entry_id uuid NOT NULL UNIQUE REFERENCES usage_entries(id) ON DELETE RESTRICT,
      reservation_id uuid NOT NULL REFERENCES usage_reservations(id) ON DELETE RESTRICT,
      actual_cost numeric(18,8) NOT NULL CHECK(actual_cost >= 0),
      resolution varchar(16) NOT NULL CHECK(resolution IN ('billed','not_billed')),
      provider_request_id varchar(240),
      evidence_ref varchar(1000) NOT NULL,
      actor_user_id uuid NOT NULL REFERENCES users(id),
      created_at timestamptz NOT NULL DEFAULT now(),
      CHECK((resolution = 'not_billed' AND actual_cost = 0) OR resolution = 'billed')
    );
    CREATE TRIGGER usage_reconciliations_are_immutable
      BEFORE UPDATE OR DELETE ON usage_reconciliations
      FOR EACH ROW EXECUTE FUNCTION reject_immutable_row_change();

    CREATE TABLE budget_topups (
      id uuid PRIMARY KEY,
      budget_account_id uuid NOT NULL REFERENCES budget_accounts(id) ON DELETE RESTRICT,
      amount numeric(18,8) NOT NULL CHECK(amount > 0),
      confirmation_ref varchar(240) NOT NULL UNIQUE,
      actor_user_id uuid NOT NULL REFERENCES users(id),
      reason varchar(1000) NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TRIGGER budget_topups_are_immutable
      BEFORE UPDATE OR DELETE ON budget_topups
      FOR EACH ROW EXECUTE FUNCTION reject_immutable_row_change();
    """)

    op.execute("DROP TRIGGER release_manifest_payload_is_immutable ON release_manifests")
    op.execute("DROP FUNCTION protect_release_manifest_payload()")
    op.execute("""
    CREATE FUNCTION protect_release_manifest_payload() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
      IF (NEW.version_label, NEW.parser_manifest_id, NEW.model_manifest_id,
          NEW.retrieval_manifest_id, NEW.rules_manifest_id, NEW.prompt_manifest_id,
          NEW.routing_policy_manifest_id, NEW.batching_policy_manifest_id,
          NEW.parser_resource_manifest_id, NEW.rate_manifest_id,
          NEW.policy_validation_report_id, NEW.grounding_registry_version,
          NEW.lint_registry_version, NEW.manifest_payload, NEW.config_hash, NEW.created_at)
         IS DISTINCT FROM
         (OLD.version_label, OLD.parser_manifest_id, OLD.model_manifest_id,
          OLD.retrieval_manifest_id, OLD.rules_manifest_id, OLD.prompt_manifest_id,
          OLD.routing_policy_manifest_id, OLD.batching_policy_manifest_id,
          OLD.parser_resource_manifest_id, OLD.rate_manifest_id,
          OLD.policy_validation_report_id, OLD.grounding_registry_version,
          OLD.lint_registry_version, OLD.manifest_payload, OLD.config_hash, OLD.created_at) THEN
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
      ) THEN RAISE EXCEPTION 'RELEASE_STATE_TRANSITION_INVALID'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER release_manifest_payload_is_immutable
      BEFORE UPDATE ON release_manifests FOR EACH ROW
      EXECUTE FUNCTION protect_release_manifest_payload();
    """)
    op.execute("GRANT SELECT ON budget_accounts, jobs, usage_entries, usage_reconciliations TO align_worker")
    op.execute("GRANT INSERT ON usage_reservations TO align_worker")
    op.execute("GRANT SELECT ON usage_reservations TO align_worker")
    op.execute("REVOKE UPDATE ON usage_reservations FROM align_worker")
    op.execute("GRANT UPDATE (status) ON usage_reservations TO align_worker")
    op.execute("GRANT INSERT ON usage_entries TO align_worker")
    op.execute("GRANT UPDATE (spend_frozen, freeze_reason, updated_at) ON budget_accounts TO align_worker")
    op.execute("GRANT SELECT ON policy_validation_reports, usage_reconciliations, budget_topups TO align_api, align_operator, align_worker")
    op.execute("GRANT INSERT ON policy_validation_reports TO align_api")
    op.execute("GRANT SELECT ON budget_accounts TO align_operator")
    op.execute("GRANT UPDATE (status) ON usage_reservations TO align_operator")
    op.execute("GRANT UPDATE (spend_frozen, freeze_reason, confirmed_total, revision, updated_at) ON budget_accounts TO align_operator")
    op.execute("GRANT INSERT ON usage_reconciliations, budget_topups, audit_events TO align_operator")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO align_operator")


def downgrade() -> None:
    op.execute("REVOKE ALL ON usage_reconciliations, budget_topups, policy_validation_reports FROM align_operator, align_worker, align_api")
    op.execute("DROP TRIGGER release_manifest_payload_is_immutable ON release_manifests")
    op.execute("DROP FUNCTION protect_release_manifest_payload()")
    op.execute("DROP TRIGGER IF EXISTS policy_validation_reports_are_immutable ON policy_validation_reports")
    op.execute("DROP TRIGGER IF EXISTS usage_reconciliations_are_immutable ON usage_reconciliations")
    op.execute("DROP TRIGGER IF EXISTS budget_topups_are_immutable ON budget_topups")
    op.execute("""
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
      IF NOT ((OLD.state = 'draft' AND NEW.state = 'verified'
          AND NEW.activated_by IS NOT DISTINCT FROM OLD.activated_by
          AND NEW.activated_at IS NOT DISTINCT FROM OLD.activated_at)
        OR (OLD.state = 'verified' AND NEW.state = 'active'
          AND NEW.activated_by IS NOT NULL AND NEW.activated_at IS NOT NULL)
        OR (OLD.state = 'active' AND NEW.state = 'retired'
          AND NEW.activated_by IS NOT DISTINCT FROM OLD.activated_by
          AND NEW.activated_at IS NOT DISTINCT FROM OLD.activated_at))
      THEN RAISE EXCEPTION 'RELEASE_STATE_TRANSITION_INVALID'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER release_manifest_payload_is_immutable
      BEFORE UPDATE ON release_manifests FOR EACH ROW
      EXECUTE FUNCTION protect_release_manifest_payload();
    """)
    op.execute("ALTER TABLE release_manifests DROP COLUMN policy_validation_report_id")
    op.execute("ALTER TABLE release_manifests DROP COLUMN rate_manifest_id")
    op.execute("ALTER TABLE release_manifests DROP COLUMN parser_resource_manifest_id")
    op.execute("DROP INDEX uq_budget_accounts_single_active")
    op.execute("ALTER TABLE usage_reservations DROP COLUMN bounds_json")
    op.execute("ALTER TABLE usage_reservations DROP CONSTRAINT ck_usage_reservations_attempt_no")
    op.execute("ALTER TABLE usage_reservations DROP CONSTRAINT uq_usage_reservations_logical_attempt")
    op.execute("ALTER TABLE usage_reservations DROP COLUMN attempt_no")
    op.execute("ALTER TABLE usage_reservations DROP COLUMN logical_call_id")
    op.execute("ALTER TABLE budget_accounts DROP COLUMN freeze_reason")
    op.execute("ALTER TABLE budget_accounts DROP COLUMN spend_frozen")
    op.execute("ALTER TABLE method_manifests DROP CONSTRAINT ck_method_manifests_kind")
    op.execute("ALTER TABLE method_manifests ADD CONSTRAINT method_manifests_kind_check CHECK(kind IN ('parser','parser_resource','model','retrieval','rules','prompt','routing','batching'))")
    op.execute("DROP TABLE IF EXISTS usage_reconciliations, budget_topups, policy_validation_reports")
