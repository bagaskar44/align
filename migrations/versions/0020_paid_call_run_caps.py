"""Atomically cap provider spend across all calls in one operator run.

Revision ID: 0020_paid_call_run_caps
Revises: 0019_b8_task_facts
"""

from alembic import op

revision = "0020_paid_call_run_caps"
down_revision = "0019_b8_task_facts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE paid_call_runs (
      id uuid PRIMARY KEY,
      budget_account_id uuid NOT NULL REFERENCES budget_accounts(id) ON DELETE RESTRICT,
      release_manifest_id uuid NOT NULL REFERENCES release_manifests(id) ON DELETE RESTRICT,
      cap_usd numeric(18,8) NOT NULL CHECK(cap_usd > 0),
      authorization_ref varchar(240) NOT NULL,
      created_by uuid NOT NULL REFERENCES users(id),
      state varchar(16) NOT NULL DEFAULT 'active' CHECK(state IN ('active','closed')),
      closed_at timestamptz,
      close_reason varchar(1000),
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE UNIQUE INDEX uq_paid_call_runs_one_active_per_account
      ON paid_call_runs (budget_account_id) WHERE state = 'active';
    ALTER TABLE usage_reservations
      ADD COLUMN paid_call_run_id uuid REFERENCES paid_call_runs(id) ON DELETE RESTRICT;
    CREATE INDEX ix_usage_reservations_paid_run_status
      ON usage_reservations (paid_call_run_id, status);
    """)
    op.execute("""
    CREATE FUNCTION protect_paid_call_run() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
      PERFORM pg_advisory_xact_lock(1095519559);
      IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'PAID_CALL_RUN_IMMUTABLE'; END IF;
      IF TG_OP = 'INSERT' THEN
        IF NEW.state <> 'active'
          OR NOT EXISTS (SELECT 1 FROM budget_accounts WHERE id = NEW.budget_account_id AND active IS TRUE)
          OR NOT EXISTS (SELECT 1 FROM release_manifests WHERE id = NEW.release_manifest_id AND state = 'active')
        THEN RAISE EXCEPTION 'PAID_CALL_RUN_BINDING_INVALID'; END IF;
        RETURN NEW;
      END IF;
      IF (NEW.id, NEW.budget_account_id, NEW.release_manifest_id, NEW.cap_usd,
          NEW.authorization_ref, NEW.created_by, NEW.created_at)
         IS DISTINCT FROM
         (OLD.id, OLD.budget_account_id, OLD.release_manifest_id, OLD.cap_usd,
          OLD.authorization_ref, OLD.created_by, OLD.created_at) THEN
        RAISE EXCEPTION 'PAID_CALL_RUN_IMMUTABLE';
      END IF;
      IF NOT (OLD.state = 'active' AND NEW.state = 'closed'
          AND NEW.closed_at IS NOT NULL AND NEW.close_reason IS NOT NULL)
      THEN RAISE EXCEPTION 'PAID_CALL_RUN_STATE_TRANSITION_INVALID'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER paid_call_runs_are_immutable
      BEFORE INSERT OR UPDATE OR DELETE ON paid_call_runs
      FOR EACH ROW EXECUTE FUNCTION protect_paid_call_run();

    CREATE FUNCTION guard_paid_call_reservation_run() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE
      run_account_id uuid;
      run_state varchar(16);
      run_release_id uuid;
      active_release_id uuid;
      run_cap numeric(18,8);
      run_liability numeric(18,8);
    BEGIN
      PERFORM pg_advisory_xact_lock(1095519559);
      IF TG_OP = 'INSERT' THEN
        IF NEW.paid_call_run_id IS NULL THEN
          RAISE EXCEPTION 'PAID_CALL_RUN_REQUIRED';
        END IF;
        SELECT budget_account_id, state, release_manifest_id, cap_usd
          INTO run_account_id, run_state, run_release_id, run_cap
          FROM paid_call_runs WHERE id = NEW.paid_call_run_id;
        IF run_account_id IS NULL OR run_account_id <> NEW.budget_account_id THEN
          RAISE EXCEPTION 'PAID_CALL_RUN_ACCOUNT_MISMATCH';
        END IF;
        IF run_state <> 'active' THEN
          RAISE EXCEPTION 'PAID_CALL_RUN_NOT_ACTIVE';
        END IF;
        SELECT id INTO active_release_id FROM release_manifests WHERE state = 'active';
        IF active_release_id IS NULL OR active_release_id <> run_release_id THEN
          RAISE EXCEPTION 'PAID_CALL_RUN_RELEASE_NOT_ACTIVE';
        END IF;
        SELECT COALESCE(SUM(
          CASE
            WHEN entry.status = 'known' AND entry.actual_cost IS NOT NULL THEN entry.actual_cost
            WHEN reservation.status IN ('held','reconciliation_required') THEN reservation.amount_reserved
            WHEN entry.status = 'pending' THEN COALESCE(reconciliation.actual_cost, 0)
            ELSE 0
          END
        ), 0)
          INTO run_liability
          FROM usage_reservations AS reservation
          LEFT JOIN usage_entries AS entry ON entry.call_id = reservation.call_id
          LEFT JOIN usage_reconciliations AS reconciliation ON reconciliation.usage_entry_id = entry.id
          WHERE reservation.paid_call_run_id = NEW.paid_call_run_id;
        IF run_liability + NEW.amount_reserved > run_cap THEN
          RAISE EXCEPTION 'PAID_CALL_RUN_CAP_EXCEEDED';
        END IF;
        RETURN NEW;
      END IF;
      IF (NEW.id, NEW.budget_account_id, NEW.paid_call_run_id, NEW.job_id,
          NEW.call_id, NEW.logical_call_id, NEW.attempt_no, NEW.amount_reserved,
          NEW.bounds_json, NEW.created_at)
         IS DISTINCT FROM
         (OLD.id, OLD.budget_account_id, OLD.paid_call_run_id, OLD.job_id,
          OLD.call_id, OLD.logical_call_id, OLD.attempt_no, OLD.amount_reserved,
          OLD.bounds_json, OLD.created_at) THEN
        RAISE EXCEPTION 'PAID_CALL_RESERVATION_IMMUTABLE';
      END IF;
      IF NOT (
        (OLD.status = 'held' AND NEW.status IN ('settled','released','reconciliation_required'))
        OR (OLD.status = 'reconciliation_required' AND NEW.status IN ('settled','released'))
      ) THEN
        RAISE EXCEPTION 'PAID_CALL_RESERVATION_STATE_TRANSITION_INVALID';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER usage_reservations_require_active_paid_run
      BEFORE INSERT OR UPDATE ON usage_reservations
      FOR EACH ROW EXECUTE FUNCTION guard_paid_call_reservation_run();
    """)
    # Reservation workers can only read the run cap and insert its foreign key.
    # Their existing table-level INSERT and column-scoped reservation status
    # rights remain unchanged.
    op.execute("GRANT SELECT ON paid_call_runs TO align_api, align_operator, align_worker")
    op.execute("GRANT INSERT ON paid_call_runs TO align_operator")
    op.execute("GRANT UPDATE (state, closed_at, close_reason) ON paid_call_runs TO align_operator")
    op.execute("REVOKE UPDATE ON paid_call_runs FROM align_api, align_worker")
    op.execute("GRANT SELECT ON usage_reservations TO align_operator")


def downgrade() -> None:
    op.execute("REVOKE ALL ON paid_call_runs FROM align_api, align_operator, align_worker")
    op.execute("DROP TRIGGER IF EXISTS usage_reservations_require_active_paid_run ON usage_reservations")
    op.execute("DROP FUNCTION IF EXISTS guard_paid_call_reservation_run()")
    op.execute("DROP TRIGGER IF EXISTS paid_call_runs_are_immutable ON paid_call_runs")
    op.execute("DROP FUNCTION IF EXISTS protect_paid_call_run()")
    op.execute("DROP INDEX IF EXISTS ix_usage_reservations_paid_run_status")
    op.execute("ALTER TABLE usage_reservations DROP COLUMN paid_call_run_id")
    op.execute("DROP TABLE paid_call_runs")
