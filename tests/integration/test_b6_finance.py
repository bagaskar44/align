from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError

from align_api.config import get_settings
from align_api.db import SessionFactory
from align_api.models import (
    BudgetAccount,
    Job,
    MethodManifest,
    ReleaseManifest,
    UsageEntry,
    UsageReservation,
)
from align_domain.assignment_contracts import canonical_hash
from align_domain.finance import FinanceGuardError
from align_domain.providers import OpenAIResponsesProvider, ProviderContext
from align_worker.database import SessionFactory as WorkerSessionFactory
from align_worker.finance import (
    CallBounds,
    WorkerPaidCallLedger,
    mark_unknown,
    release_unstarted_call,
    reserve_call,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("ALIGN_RUN_INTEGRATION") != "true",
    reason="requires the isolated PostgreSQL integration profile",
)


def test_worker_reservation_privilege_is_limited_to_status_and_timestamp() -> None:
    engine = create_engine(get_settings().worker_database_url)
    try:
        with engine.connect() as db:
            assert db.execute(text(
                "SELECT has_column_privilege(current_user, 'usage_reservations', 'status', 'UPDATE')"
            )).scalar_one() is True
            for column in ("amount_reserved", "call_id", "job_id", "bounds_json"):
                assert db.execute(text(
                    "SELECT has_column_privilege(current_user, 'usage_reservations', :column, 'UPDATE')"
                ), {"column": column}).scalar_one() is False
            assert db.execute(text(
                "SELECT has_column_privilege(current_user, 'budget_accounts', 'spend_frozen', 'UPDATE')"
            )).scalar_one() is True
            assert db.execute(text(
                "SELECT has_column_privilege(current_user, 'budget_accounts', 'confirmed_total', 'UPDATE')"
            )).scalar_one() is False
    finally:
        engine.dispose()


def _rows() -> tuple[BudgetAccount, dict[str, UUID], UUID]:
    with SessionFactory.begin() as db:
        account = db.scalar(select(BudgetAccount).where(BudgetAccount.active.is_(True)).order_by(BudgetAccount.id))
        if account is None:
            account = BudgetAccount(
                currency="USD", confirmed_total=Decimal("25"), reserve_floor=Decimal("5"),
                planned_topup=Decimal("0"), active=True,
            )
            db.add(account)
            db.flush()
        standard_rates = {
            "schema_version": 1,
            "status": "verified",
            "currency": "USD",
            "models": {
                "m8": {"input_per_million": "8", "output_per_million": "0", "embedding_per_million": "0"},
                "m19": {"input_per_million": "19.9995", "output_per_million": "0", "embedding_per_million": "0"},
                "mcheap": {"input_per_million": "0.000001", "output_per_million": "0", "embedding_per_million": "0"},
            },
        }
        rate_hash = canonical_hash(standard_rates)
        rates = db.scalar(select(MethodManifest).where(MethodManifest.sha256 == rate_hash))
        if rates is None:
            rates = MethodManifest(
                kind="rates", version_label="b6-pg-test-rates", content_json=standard_rates,
                sha256=rate_hash, verified_at=datetime.now(UTC),
            )
            db.add(rates)
            db.flush()
        jobs: dict[str, UUID] = {}
        for key in ("c1", "c2", "c3", "pending"):
            job = Job(
                type="b6_finance_test", subject_id=uuid4(), scope_json={"test": key},
                input_fingerprint=canonical_hash({"test": key, "nonce": str(uuid4())}),
                status="queued", job_limits_json={
                    "max_calls": 2, "max_vision_calls": 0, "max_text_input_tokens": 1_000_000,
                    "max_text_output_tokens": 0, "max_embedding_tokens": 0,
                    "max_active_seconds": 600, "budget_usd": "20",
                },
            )
            db.add(job)
            db.flush()
            jobs[key] = job.id
        return account, jobs, rates.id


def test_live_adapter_commits_reservation_then_settles_postgres_before_return(monkeypatch: pytest.MonkeyPatch) -> None:
    account, jobs, existing_rates_id = _rows()
    fixture_nonce = str(uuid4())
    with SessionFactory.begin() as db:
        manifest_rows: dict[str, MethodManifest] = {}
        contents = {
            "parser": {"schema_version": 1, "test_fixture": fixture_nonce},
            "model": {"provider_mode": "live", "paid_calls": True, "test_fixture": fixture_nonce},
            "retrieval": {"schema_version": 1, "test_fixture": fixture_nonce},
            "rules": {"schema_version": 1, "test_fixture": fixture_nonce},
            "prompt": {"schema_version": 1, "test_fixture": fixture_nonce},
            "routing": {"allow_live": True, "test_fixture": fixture_nonce},
            "batching": {"maximum_elements": 1, "test_fixture": fixture_nonce},
            "parser_resource": {"resource": "test-only", "test_fixture": fixture_nonce},
            "rates": {
                "schema_version": 1,
                "status": "verified",
                "currency": "USD",
                "verification": {"source_ref": "test-fixture", "reviewed_by": "test", "effective_date": "2026-10-22", "fixture": fixture_nonce},
                "models": {
                    "mcheap": {
                        "input_per_million": "0.000001",
                        "output_per_million": "0",
                        "embedding_per_million": "0",
                    }
                },
            },
        }
        for manifest_kind, manifest_content in contents.items():
            manifest_content["test_kind"] = manifest_kind
        for kind, content in contents.items():
            digest = canonical_hash(content)
            row = db.scalar(select(MethodManifest).where(MethodManifest.sha256 == digest))
            if row is None:
                row = MethodManifest(
                    kind=kind,
                    version_label=f"b6-ledger-{kind}-{fixture_nonce[:8]}",
                    content_json=content,
                    sha256=digest,
                    verified_at=datetime.now(UTC) if kind == "rates" else None,
                )
                db.add(row)
                db.flush()
            elif row.kind != kind or row.content_json != content:
                raise AssertionError("test manifest hash collision across immutable contents")
            manifest_rows[kind] = row
        # This integration suite may run after B4/B5 tests that activated the
        # synthetic baseline. The disposable DB permits one active release.
        for active_release in db.scalars(
            select(ReleaseManifest).where(ReleaseManifest.state == "active").with_for_update()
        ).all():
            active_release.state = "retired"
        db.flush()
        release_id = uuid4()
        db.add(ReleaseManifest(
            id=release_id,
            version_label="b6-ledger-live-test-only",
            parser_manifest_id=manifest_rows["parser"].id,
            model_manifest_id=manifest_rows["model"].id,
            retrieval_manifest_id=manifest_rows["retrieval"].id,
            rules_manifest_id=manifest_rows["rules"].id,
            prompt_manifest_id=manifest_rows["prompt"].id,
            routing_policy_manifest_id=manifest_rows["routing"].id,
            batching_policy_manifest_id=manifest_rows["batching"].id,
            parser_resource_manifest_id=manifest_rows["parser_resource"].id,
            rate_manifest_id=manifest_rows["rates"].id,
            grounding_registry_version="test-only",
            lint_registry_version="test-only",
            manifest_payload={"provider_mode": "live", "data_mode": "institutional"},
            config_hash=canonical_hash({"release_id": str(release_id)}),
            state="active",
        ))
        db.flush()
        job_id = jobs["c1"]
        job = db.get(Job, job_id)
        assert job is not None
        job.job_limits_json = {
            **job.job_limits_json,
            "max_calls": 4,
            "max_text_input_tokens": 10_000,
            "max_text_output_tokens": 500,
            "max_embedding_tokens": 0,
            "max_active_seconds": 60,
            "budget_usd": "10",
        }

    call = ProviderContext(
        call_id=uuid4(), job_id=job_id, manifest_id=manifest_rows["model"].id,
        policy_id=manifest_rows["routing"].id, scope_ids=[uuid4()],
        max_input_tokens=1000, max_output_tokens=100,
        paid_release_manifest_id=release_id, rate_manifest_id=manifest_rows["rates"].id,
        logical_call_id=uuid4(), paid_calls_enabled=True,
        billing_bounds={"input_tokens": 1000, "output_tokens": 100, "embedding_tokens": 0,
            "vision_calls": 0, "image_count": 0, "active_ms": 30_000},
    )

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"id":"req_b6_test","usage":{"input_tokens":10,"output_tokens":3}}'

    def fake_urlopen(*_args: object, **_kwargs: object) -> FakeResponse:
        with WorkerSessionFactory() as db:
            reservation = db.scalar(select(UsageReservation).where(UsageReservation.call_id == call.call_id))
            assert reservation is not None and reservation.status == "held"
            assert reservation.amount_reserved == Decimal("0.00000001")
        return FakeResponse()

    monkeypatch.setattr("align_domain.providers.urllib.request.urlopen", fake_urlopen)
    provider = OpenAIResponsesProvider(
        "synthetic-test-key", {"complex": "mcheap"},
        paid_call_ledger=WorkerPaidCallLedger(enabled=True),
    )
    response = provider._post(call, "responses", {"model": "mcheap", "input": "synthetic"})
    assert response["id"] == "req_b6_test"
    with WorkerSessionFactory() as db:
        reservation = db.scalar(select(UsageReservation).where(UsageReservation.call_id == call.call_id))
        entry = db.scalar(select(UsageEntry).where(UsageEntry.call_id == call.call_id))
        assert reservation is not None and reservation.status == "settled"
        assert entry is not None and entry.status == "known" and entry.actual_cost == Decimal("0.00000001")
    with pytest.raises(DBAPIError, match="RELEASE_PAYLOAD_IMMUTABLE"):
        with SessionFactory.begin() as db:
            release = db.get(ReleaseManifest, release_id)
            assert release is not None
            release.rate_manifest_id = existing_rates_id
            db.flush()


def _reserve(job_id: UUID, rates_id: UUID, model: str, call_id: UUID | None = None) -> UUID:
    physical_call_id = call_id or uuid4()
    reserve_call(
        job_id=job_id,
        call_id=physical_call_id,
        logical_call_id=uuid4(),
        attempt_no=1,
        rate_manifest_id=rates_id,
        model_id=model,
        bounds=CallBounds(input_tokens=1_000_000, active_ms=1_000),
    )
    return physical_call_id


def test_concurrent_global_reservations_unknown_billing_and_new_attempt_liability() -> None:
    _account, jobs, rates_id = _rows()
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(_reserve, jobs[key], rates_id, "m8") for key in ("c1", "c2", "c3")]
    outcomes: list[UUID | Exception] = []
    for future in futures:
        try:
            outcomes.append(future.result())
        except Exception as exc:  # the expected loser receives a closed finance guard
            outcomes.append(exc)
    successful = [item for item in outcomes if isinstance(item, UUID)]
    failed = [item for item in outcomes if isinstance(item, Exception)]
    assert len(successful) == 2, f"reservation outcomes: {[str(item) for item in outcomes]}"
    assert len(failed) == 1 and isinstance(failed[0], FinanceGuardError), [str(item) for item in outcomes]
    assert "global_budget_exhausted" in str(failed[0])
    with WorkerSessionFactory() as db:
        held = db.scalars(select(UsageReservation).where(
            UsageReservation.call_id.in_(successful), UsageReservation.status == "held"
        )).all()
        assert len(held) == 2
        assert sum((row.amount_reserved for row in held), Decimal("0")) == Decimal("16.00000000")
    for call_id in successful:
        release_unstarted_call(call_id, proof="integration fake never starts provider I/O")

    pending_call = _reserve(jobs["pending"], rates_id, "m19")
    mark_unknown(pending_call, provider_request_id="request-timeout", reason="timeout_after_submit")
    with WorkerSessionFactory() as db:
        reservation = db.scalar(select(UsageReservation).where(UsageReservation.call_id == pending_call))
        entry = db.scalar(select(UsageEntry).where(UsageEntry.call_id == pending_call))
        assert reservation is not None and reservation.status == "reconciliation_required"
        assert entry is not None and entry.status == "pending" and entry.provider_request_id == "request-timeout"
    with pytest.raises(FinanceGuardError, match="global_budget_exhausted"):
        reserve_call(
            job_id=jobs["pending"], call_id=uuid4(), logical_call_id=entry.logical_call_id,
            attempt_no=2, rate_manifest_id=rates_id, model_id="m19",
            bounds=CallBounds(input_tokens=1_000_000, active_ms=1_000),
        )
    with WorkerSessionFactory() as db:
        account = db.get(BudgetAccount, _account.id)
        assert account is not None and account.confirmed_total == Decimal("25.00000000")
