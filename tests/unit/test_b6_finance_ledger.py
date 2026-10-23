from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from align_api import cli
from align_api.cli import ensure_budget_account
from align_api.models import (
    AuditEvent,
    Base,
    BudgetAccount,
    Job,
    MethodManifest,
    PaidCallRun,
    ReleaseManifest,
    UsageEntry,
    UsageReconciliation,
    UsageReservation,
    User,
)
from align_domain.finance import FinanceGuardError
from align_domain.providers import PaidCallBlocked, ProviderContext
from align_worker import finance
from align_worker.finance import (
    CallBounds,
    mark_unknown,
    reconcile_stale_reservations,
    reserve_call,
    settle_call,
)


def _ledger(tmp_path, monkeypatch, *, with_run: bool = True, run_cap: Decimal = Decimal("1")):
    engine = create_engine(f"sqlite:///{tmp_path / 'b6-ledger.sqlite'}")
    tables = [
        BudgetAccount.__table__,
        User.__table__,
        AuditEvent.__table__,
        Job.__table__,
        MethodManifest.__table__,
        ReleaseManifest.__table__,
        PaidCallRun.__table__,
        UsageReservation.__table__,
        UsageEntry.__table__,
        UsageReconciliation.__table__,
    ]
    Base.metadata.create_all(engine, tables=tables)
    factory = sessionmaker(bind=engine, class_=Session, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(finance, "SessionFactory", factory)
    rate_manifest_id = uuid4()
    job_id = uuid4()
    release_id: UUID | None = None
    model_manifest_id: UUID | None = None
    paid_call_run_id: UUID | None = None
    with factory.begin() as db:
        account = BudgetAccount(
            id=uuid4(), currency="USD", confirmed_total=Decimal("5.002"),
            reserve_floor=Decimal("5"), planned_topup=Decimal("0"), active=True,
        )
        db.add(account)
        db.add(Job(
            id=job_id, type="analysis", subject_id=uuid4(), scope_json={},
            input_fingerprint="a" * 64, status="queued", job_limits_json={
                "max_calls": 3, "max_vision_calls": 1, "max_text_input_tokens": 5000,
                "max_text_output_tokens": 1000, "max_embedding_tokens": 1000,
                "max_active_seconds": 600, "budget_usd": "0.01",
            },
        ))
        rates_row = MethodManifest(
            id=rate_manifest_id, kind="rates", version_label="test-rates-v1",
            content_json={"schema_version": 1, "status": "verified", "currency": "USD", "models": {
                "m-test": {"input_per_million": "1", "output_per_million": "5", "embedding_per_million": "0.1"},
                "text-embedding-3-small": {"input_per_million": "1", "output_per_million": "0", "embedding_per_million": "0.1"},
            }},
            sha256="b" * 64, verified_at=datetime.now(UTC),
        )
        db.add(rates_row)
        if with_run:
            rows: dict[str, MethodManifest] = {"rates": rates_row}
            for kind in ("parser", "parser_resource", "model", "retrieval", "rules", "prompt", "routing", "batching"):
                content = (
                    {
                        "provider_mode": "live", "paid_calls": True, "capability_smoke": True,
                        "embedding_dimensions": 1536,
                        "live_model_ids": {
                            "efficient": "m-test", "complex": "m-test", "vision": "m-test",
                            "embedding": "text-embedding-3-small",
                        },
                    }
                    if kind == "model"
                    else {"fixture_kind": kind, "nonce": str(uuid4()), "allow_live": kind == "routing", "maximum_elements": 1 if kind == "batching" else None}
                )
                row = MethodManifest(
                    id=uuid4(), kind=kind, version_label=f"test-{kind}", content_json=content,
                    sha256=uuid4().hex * 2,
                )
                db.add(row)
                db.flush()
                rows[kind] = row
            release = ReleaseManifest(
                id=uuid4(), version_label="unit-live-release", parser_manifest_id=rows["parser"].id,
                parser_resource_manifest_id=rows["parser_resource"].id,
                model_manifest_id=rows["model"].id, rate_manifest_id=rates_row.id,
                retrieval_manifest_id=rows["retrieval"].id, rules_manifest_id=rows["rules"].id,
                prompt_manifest_id=rows["prompt"].id, routing_policy_manifest_id=rows["routing"].id,
                batching_policy_manifest_id=rows["batching"].id,
                grounding_registry_version="unit-test", lint_registry_version="unit-test",
                manifest_payload={"provider_mode": "live", "data_mode": "institutional"},
                config_hash=uuid4().hex * 2, state="active",
            )
            db.add(release)
            db.flush()
            actor = User(
                id=uuid4(), normalized_email=f"finance-{uuid4().hex}@example.test",
                display_name="Synthetic Finance Operator", password_hash="synthetic-test-only",
                global_role="admin", active=True,
            )
            db.add(actor)
            db.flush()
            spend_run = PaidCallRun(
                id=uuid4(), budget_account_id=account.id, release_manifest_id=release.id,
                cap_usd=run_cap, authorization_ref="unit-test-authorization",
                created_by=actor.id, state="active",
            )
            db.add(spend_run)
            db.flush()
            release_id = release.id
            model_manifest_id = rows["model"].id
            paid_call_run_id = spend_run.id
    return engine, factory, job_id, rate_manifest_id, release_id, model_manifest_id, paid_call_run_id


def test_worker_rechecks_call_model_manifest_before_reservation(tmp_path, monkeypatch, request) -> None:
    engine, factory, job_id, rate_manifest_id, _release_id, _model_id, _run_id = _ledger(
        tmp_path, monkeypatch, with_run=False
    )
    for name in ("MODEL_EFFICIENT", "MODEL_COMPLEX", "MODEL_VISION"):
        monkeypatch.setenv(name, "m-test")
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-3-small")
    from align_api.config import get_settings

    get_settings.cache_clear()
    request.addfinalizer(get_settings.cache_clear)
    now = datetime.now(UTC)
    with factory.begin() as db:
        manifests: dict[str, MethodManifest] = {}
        contents = {
            "parser": {}, "parser_resource": {}, "retrieval": {}, "rules": {},
            "prompt": {}, "routing": {"allow_live": True},
            "batching": {"maximum_elements": 1},
            "model": {
                "provider_mode": "live", "paid_calls": True,
                "live_model_ids": {
                    "efficient": "m-test", "complex": "m-test",
                    "vision": "m-test", "embedding": "text-embedding-3-small",
                },
            },
        }
        for kind, content in contents.items():
            row = MethodManifest(
                kind=kind, version_label=f"test-{kind}", content_json=content,
                sha256=uuid4().hex * 2,
                verified_at=now if kind == "rates" else None,
            )
            db.add(row)
            db.flush()
            manifests[kind] = row
        mismatched_call_model = MethodManifest(
            kind="model", version_label="call-model-mismatch",
            content_json={"model": "another-model"}, sha256="a" * 64,
        )
        db.add(mismatched_call_model)
        db.flush()
        release = ReleaseManifest(
            version_label="live-test-release",
            parser_manifest_id=manifests["parser"].id,
            parser_resource_manifest_id=manifests["parser_resource"].id,
            model_manifest_id=manifests["model"].id,
            rate_manifest_id=rate_manifest_id,
            retrieval_manifest_id=manifests["retrieval"].id,
            rules_manifest_id=manifests["rules"].id,
            prompt_manifest_id=manifests["prompt"].id,
            routing_policy_manifest_id=manifests["routing"].id,
            batching_policy_manifest_id=manifests["batching"].id,
            grounding_registry_version="grounding-test-v1",
            lint_registry_version="lint-test-v1",
            manifest_payload={"provider_mode": "live", "data_mode": "institutional"},
            config_hash="c" * 64,
            state="active",
        )
        db.add(release)
        db.flush()
        release_id = release.id
        call_manifest_id = mismatched_call_model.id

    call_id, policy_id = uuid4(), manifests["routing"].id
    context = ProviderContext(
        call_id=call_id,
        job_id=job_id,
        manifest_id=call_manifest_id,
        policy_id=policy_id,
        scope_ids=[uuid4()],
        max_input_tokens=100,
        max_output_tokens=10,
        paid_release_manifest_id=release_id,
        rate_manifest_id=rate_manifest_id,
        paid_calls_enabled=True,
        billing_bounds={"input_tokens": 100, "output_tokens": 10, "active_ms": 1000},
    )
    with pytest.raises(PaidCallBlocked, match="paid_call_model_manifest_mismatch"):
        finance.WorkerPaidCallLedger(enabled=True).reserve(
            context, "m-test", {"input_tokens": 100, "output_tokens": 10, "active_ms": 1000},
        )
    with factory() as db:
        assert db.query(UsageReservation).filter_by(call_id=call_id).first() is None
    engine.dispose()


def test_run_cap_counts_held_and_settled_liability(tmp_path, monkeypatch) -> None:
    engine, factory, job_id, rate_manifest_id, release_id, model_id, run_id = _ledger(
        tmp_path, monkeypatch, run_cap=Decimal("0.0025")
    )
    assert release_id is not None and model_id is not None and run_id is not None
    with factory.begin() as db:
        account = db.scalar(select(BudgetAccount).where(BudgetAccount.active.is_(True)))
        assert account is not None
        account.confirmed_total = Decimal("10")
    first_call = uuid4()
    reserve_call(
        job_id=job_id, call_id=first_call, logical_call_id=uuid4(), attempt_no=1,
        release_manifest_id=release_id, model_manifest_id=model_id,
        rate_manifest_id=rate_manifest_id, model_id="m-test",
        bounds=CallBounds(input_tokens=1000, output_tokens=100, active_ms=1000),
    )
    with pytest.raises(FinanceGuardError, match="paid_call_run_cap_exceeded"):
        reserve_call(
            job_id=job_id, call_id=uuid4(), logical_call_id=uuid4(), attempt_no=1,
            release_manifest_id=release_id, model_manifest_id=model_id,
            rate_manifest_id=rate_manifest_id, model_id="m-test",
            bounds=CallBounds(input_tokens=1000, output_tokens=100, active_ms=1000),
        )
    release_unstarted_call(first_call, proof="unit fake never starts provider I/O")
    settled_call = uuid4()
    reserve_call(
        job_id=job_id, call_id=settled_call, logical_call_id=uuid4(), attempt_no=1,
        release_manifest_id=release_id, model_manifest_id=model_id,
        rate_manifest_id=rate_manifest_id, model_id="m-test",
        bounds=CallBounds(input_tokens=1000, output_tokens=100, active_ms=1000),
    )
    settle_call(
        settled_call, provider_request_id="unit-settled-request",
        usage={"input_tokens": 1000, "output_tokens": 100, "active_ms": 1000},
    )
    with pytest.raises(FinanceGuardError, match="paid_call_run_cap_exceeded"):
        reserve_call(
            job_id=job_id, call_id=uuid4(), logical_call_id=uuid4(), attempt_no=1,
            release_manifest_id=release_id, model_manifest_id=model_id,
            rate_manifest_id=rate_manifest_id, model_id="m-test",
            bounds=CallBounds(input_tokens=1000, output_tokens=100, active_ms=1000),
        )
    engine.dispose()


def test_pending_liability_is_reserved_once_and_retry_needs_new_capacity(tmp_path, monkeypatch) -> None:
    engine, factory, job_id, rate_manifest_id, release_id, model_id, _run_id = _ledger(tmp_path, monkeypatch)
    assert release_id is not None and model_id is not None
    call_id = uuid4()
    reserve_call(
        job_id=job_id, call_id=call_id, logical_call_id=uuid4(), attempt_no=1,
        release_manifest_id=release_id, model_manifest_id=model_id,
        rate_manifest_id=rate_manifest_id, model_id="m-test",
        bounds=CallBounds(input_tokens=1000, output_tokens=100, active_ms=1000),
    )
    mark_unknown(call_id, provider_request_id="req-unknown", reason="transport_lost_after_request")
    with factory.begin() as db:
        ensure_budget_account(db)
    with factory() as db:
        reservation = db.scalar(select(UsageReservation).where(UsageReservation.call_id == call_id))
        entry = db.scalar(select(UsageEntry).where(UsageEntry.call_id == call_id))
        assert reservation is not None and reservation.status == "reconciliation_required"
        assert entry is not None and entry.status == "pending" and entry.actual_cost is None
        assert entry.provider_request_id == "req-unknown"
        assert reservation.amount_reserved == Decimal("0.00150000")
        assert db.scalar(select(BudgetAccount.confirmed_total)) == Decimal("5.00200000")
    with pytest.raises(FinanceGuardError, match="budget_reconciliation_pending"):
        reserve_call(
            job_id=job_id, call_id=uuid4(), logical_call_id=uuid4(), attempt_no=1,
            release_manifest_id=release_id, model_manifest_id=model_id,
            rate_manifest_id=rate_manifest_id, model_id="m-test",
            bounds=CallBounds(input_tokens=1000, output_tokens=100, active_ms=1000),
        )
    engine.dispose()


def test_operator_reconciliation_records_not_billed_and_allows_a_new_attempt(tmp_path, monkeypatch) -> None:
    engine, factory, job_id, rate_manifest_id, release_id, model_id, _run_id = _ledger(tmp_path, monkeypatch)
    assert release_id is not None and model_id is not None
    call_id, logical_call_id = uuid4(), uuid4()
    reserve_call(
        job_id=job_id, call_id=call_id, logical_call_id=logical_call_id, attempt_no=1,
        release_manifest_id=release_id, model_manifest_id=model_id,
        rate_manifest_id=rate_manifest_id, model_id="m-test",
        bounds=CallBounds(input_tokens=1000, output_tokens=100, active_ms=1000),
    )
    mark_unknown(call_id, provider_request_id="req-reconcile", reason="transport_lost_after_submit")
    with factory.begin() as db:
        actor = User(
            normalized_email="finance-operator@example.test", display_name="Synthetic Operator",
            password_hash="synthetic-test-only",  # noqa: S106 -- synthetic test fixture only
            global_role="admin", active=True,
        )
        db.add(actor)
        db.flush()
        entry = db.scalar(select(UsageEntry).where(UsageEntry.call_id == call_id))
        assert entry is not None
        entry_id = entry.id
    monkeypatch.setattr(cli, "SessionFactory", factory)
    monkeypatch.setattr(cli, "_operator_session_factory", lambda: (engine, factory))

    with pytest.raises(SystemExit, match="not_billed reconciliation must record zero"):
        cli.reconcile_usage(
            str(entry_id), resolution="not_billed", amount="0.01",
            evidence_ref="synthetic billing check", provider_request_id="req-reconcile",
        )
    cli.reconcile_usage(
        str(entry_id), resolution="not_billed", amount="0",
        evidence_ref="synthetic billing check", provider_request_id="req-reconcile",
    )
    with factory() as db:
        reconciliation = db.scalar(select(UsageReconciliation).where(UsageReconciliation.usage_entry_id == entry_id))
        reservation = db.scalar(select(UsageReservation).where(UsageReservation.call_id == call_id))
        entry = db.get(UsageEntry, entry_id)
        account = db.scalar(select(BudgetAccount).where(BudgetAccount.active.is_(True)))
        assert reconciliation is not None and reconciliation.resolution == "not_billed"
        assert reconciliation.actual_cost == Decimal("0E-8")
        assert reconciliation.evidence_ref == "synthetic billing check"
        assert reservation is not None and reservation.status == "released"
        assert entry is not None and entry.status == "pending"
        assert account is not None and account.confirmed_total == Decimal("5.00200000")
    reserve_call(
        job_id=job_id, call_id=uuid4(), logical_call_id=logical_call_id, attempt_no=2,
        release_manifest_id=release_id, model_manifest_id=model_id,
        rate_manifest_id=rate_manifest_id, model_id="m-test",
        bounds=CallBounds(input_tokens=1000, output_tokens=100, active_ms=1000),
    )
    engine.dispose()


def test_provider_actual_over_bound_is_ledgered_and_freezes_new_calls(tmp_path, monkeypatch) -> None:
    engine, factory, job_id, rate_manifest_id, release_id, model_id, _run_id = _ledger(tmp_path, monkeypatch)
    assert release_id is not None and model_id is not None
    call_id = uuid4()
    reserve_call(
        job_id=job_id, call_id=call_id, logical_call_id=uuid4(), attempt_no=1,
        release_manifest_id=release_id, model_manifest_id=model_id,
        rate_manifest_id=rate_manifest_id, model_id="m-test",
        bounds=CallBounds(input_tokens=1000, output_tokens=100, active_ms=1000),
    )
    settle_call(
        call_id,
        provider_request_id="req-actual",
        usage={"input_tokens": 1000, "output_tokens": 200, "active_ms": 1200},
    )
    with factory() as db:
        entry = db.scalar(select(UsageEntry).where(UsageEntry.call_id == call_id))
        account = db.scalar(select(BudgetAccount))
        reservation = db.scalar(select(UsageReservation).where(UsageReservation.call_id == call_id))
        assert entry is not None and entry.status == "known"
        assert entry.actual_cost == Decimal("0.00200000")
        assert reservation is not None and reservation.status == "settled"
        assert account is not None and account.spend_frozen is True
        assert account.freeze_reason == "provider_usage_exceeded_reserved_bound"
    with pytest.raises(FinanceGuardError, match="budget_spending_frozen"):
        reserve_call(
            job_id=job_id, call_id=uuid4(), logical_call_id=uuid4(), attempt_no=1,
            release_manifest_id=release_id, model_manifest_id=model_id,
            rate_manifest_id=rate_manifest_id, model_id="m-test",
            bounds=CallBounds(input_tokens=1, active_ms=1),
        )
    engine.dispose()


def test_expired_held_reservation_becomes_reconcilable_pending_liability(tmp_path, monkeypatch) -> None:
    engine, factory, job_id, rate_manifest_id, release_id, model_id, _run_id = _ledger(tmp_path, monkeypatch)
    assert release_id is not None and model_id is not None
    call_id = uuid4()
    reserve_call(
        job_id=job_id, call_id=call_id, logical_call_id=uuid4(), attempt_no=1,
        release_manifest_id=release_id, model_manifest_id=model_id,
        rate_manifest_id=rate_manifest_id, model_id="m-test",
        bounds=CallBounds(input_tokens=1000, output_tokens=100, active_ms=1000),
    )
    with factory.begin() as db:
        reservation = db.scalar(select(UsageReservation).where(UsageReservation.call_id == call_id))
        assert reservation is not None
        reservation.created_at = datetime.now(UTC) - timedelta(minutes=10)
    assert reconcile_stale_reservations(minimum_age_seconds=60) == 1
    with factory() as db:
        reservation = db.scalar(select(UsageReservation).where(UsageReservation.call_id == call_id))
        entry = db.scalar(select(UsageEntry).where(UsageEntry.call_id == call_id))
        assert reservation is not None and reservation.status == "reconciliation_required"
        assert entry is not None and entry.status == "pending"
        assert entry.usage_json["reason_code"] == "worker_crash_or_lease_expired"
    engine.dispose()


def test_not_billed_started_attempt_still_consumes_job_call_limit(tmp_path, monkeypatch) -> None:
    engine, factory, job_id, rate_manifest_id, release_id, model_id, _run_id = _ledger(tmp_path, monkeypatch)
    assert release_id is not None and model_id is not None
    call_id = uuid4()
    logical_call_id = uuid4()
    with factory.begin() as db:
        job = db.get(Job, job_id)
        assert job is not None
        job.job_limits_json = {**job.job_limits_json, "max_calls": 1}
    reserve_call(
        job_id=job_id, call_id=call_id, logical_call_id=logical_call_id, attempt_no=1,
        release_manifest_id=release_id, model_manifest_id=model_id,
        rate_manifest_id=rate_manifest_id, model_id="m-test",
        bounds=CallBounds(input_tokens=1000, output_tokens=100, active_ms=1000),
    )
    mark_unknown(call_id, provider_request_id="req-not-billed", reason="timeout_after_submit")
    with factory.begin() as db:
        reservation = db.scalar(select(UsageReservation).where(UsageReservation.call_id == call_id))
        entry = db.scalar(select(UsageEntry).where(UsageEntry.call_id == call_id))
        assert reservation is not None and entry is not None
        reservation.status = "released"
        db.add(UsageReconciliation(
            usage_entry_id=entry.id,
            reservation_id=reservation.id,
            actual_cost=Decimal("0"),
            resolution="not_billed",
            provider_request_id="req-not-billed",
            evidence_ref="test-evidence:provider-no-charge",
            actor_user_id=uuid4(),
        ))
    with pytest.raises(FinanceGuardError, match="job_limit_exceeded:max_calls"):
        reserve_call(
            job_id=job_id, call_id=uuid4(), logical_call_id=logical_call_id, attempt_no=2,
            release_manifest_id=release_id, model_manifest_id=model_id,
            rate_manifest_id=rate_manifest_id, model_id="m-test",
            bounds=CallBounds(input_tokens=1, active_ms=1),
        )
    engine.dispose()
