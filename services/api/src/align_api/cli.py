from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from align_api.config import get_settings
from align_api.db import SessionFactory
from align_api.models import (
    AnalysisRun,
    AuditEvent,
    BudgetAccount,
    BudgetTopup,
    Course,
    Enrollment,
    FileAsset,
    MethodManifest,
    Operation,
    OutboxEvent,
    ParseArtifact,
    PolicyValidationReport,
    ReleaseManifest,
    ReviewVersion,
    Submission,
    SubmissionVersion,
    UsageEntry,
    UsageReconciliation,
    UsageReservation,
    User,
)
from align_api.security import hash_password, normalize_email
from align_api.storage import storage_client

RELEASE_MANIFEST_COLUMNS = {
    "parser": "parser_manifest_id",
    "parser_resource": "parser_resource_manifest_id",
    "model": "model_manifest_id",
    "rates": "rate_manifest_id",
    "retrieval": "retrieval_manifest_id",
    "rules": "rules_manifest_id",
    "prompt": "prompt_manifest_id",
    "routing": "routing_policy_manifest_id",
    "batching": "batching_policy_manifest_id",
}


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_json(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def release_core_hash(
    manifests: dict[str, MethodManifest], grounding_registry: str, lint_registry: str
) -> str:
    return sha256_json({
        "methods": {kind: manifests[kind].sha256 for kind in sorted(manifests)},
        "grounding_registry_version": grounding_registry,
        "lint_registry_version": lint_registry,
    })


def validate_policy_validation_report(
    report: dict[str, Any], release: dict[str, Any], manifests: dict[str, dict[str, Any]]
) -> None:
    required = {
        "schema_version", "status", "provider_mode", "data_mode", "data_origin",
        "candidate_core_hash", "baseline_core_hash", "dataset_sha256", "protocol_sha256",
        "codebook_sha256", "split_sha256", "sample_count", "scope", "compatibility",
        "support_by_category", "metrics_by_category", "confidence_intervals_by_category",
        "actual_cost_usd_per_bundle", "pending_liability_count", "repeats",
        "critical_invariant_violations", "ledger_refs", "allowed_data_modes", "evaluation_kind",
        "report_sha256",
    }
    if not required <= set(report) or report.get("schema_version") != 2:
        raise SystemExit("PolicyValidationReport schema is incomplete")
    if report.get("status") not in {"eligible", "ineligible", "incomplete"}:
        raise SystemExit("PolicyValidationReport status is invalid")
    if report.get("data_mode") not in {"synthetic", "institutional"}:
        raise SystemExit("PolicyValidationReport data mode is invalid")
    if report.get("provider_mode") not in {"mock", "live"}:
        raise SystemExit("PolicyValidationReport provider mode is invalid")
    if report.get("data_origin") != report.get("data_mode"):
        raise SystemExit("PolicyValidationReport data origin does not match data mode")
    if report["provider_mode"] == "mock" and report["data_mode"] != "synthetic":
        raise SystemExit("mock policy reports can only use synthetic data")
    for field in ("candidate_core_hash", "baseline_core_hash", "dataset_sha256", "protocol_sha256", "codebook_sha256", "split_sha256"):
        value = report.get(field)
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise SystemExit(f"PolicyValidationReport {field} must be a lowercase SHA-256")
    core_hash = sha256_json({
        "methods": {kind: sha256_json(value) for kind, value in sorted(manifests.items())},
        "grounding_registry_version": release.get("grounding_registry_version"),
        "lint_registry_version": release.get("lint_registry_version"),
    })
    if report["candidate_core_hash"] != core_hash:
        raise SystemExit("PolicyValidationReport candidate core hash does not match registered manifests")
    if type(report.get("sample_count")) is not int or report["sample_count"] < 0:
        raise SystemExit("PolicyValidationReport sample_count is invalid")
    scope = report.get("scope")
    if not isinstance(scope, dict) or any(
        key not in scope or not isinstance(scope[key], list) or not scope[key]
        or any(not isinstance(item, str) or not item.strip() for item in scope[key])
        or len(set(scope[key])) != len(scope[key])
        for key in ("task_families", "categories", "modalities")
    ):
        raise SystemExit("PolicyValidationReport scope is incomplete")
    expected_manifest_hashes = {
        kind: sha256_json(value) for kind, value in sorted(manifests.items())
    }
    expected_compatibility = {
        "method_manifest_sha256": expected_manifest_hashes,
        "parser_resource_sha256": manifests["parser_resource"].get("resource_manifest_sha256"),
        "grounding_registry_version": release.get("grounding_registry_version"),
        "lint_registry_version": release.get("lint_registry_version"),
    }
    if report.get("compatibility") != expected_compatibility:
        raise SystemExit("PolicyValidationReport release compatibility does not match exact manifests")
    support = report.get("support_by_category")
    metrics = report.get("metrics_by_category")
    intervals = report.get("confidence_intervals_by_category")
    categories = set(scope["categories"])
    if not isinstance(support, dict) or set(support) != categories:
        raise SystemExit("PolicyValidationReport category support does not match scope")
    if not isinstance(metrics, dict) or set(metrics) != categories:
        raise SystemExit("PolicyValidationReport category metrics do not match scope")
    if not isinstance(intervals, dict) or (report["status"] == "eligible" and set(intervals) != categories):
        raise SystemExit("PolicyValidationReport confidence intervals do not match scope")
    category_values: dict[str, dict[str, dict[str, int]]] = {}
    for category in scope["categories"]:
        category_support = support[category]
        category_metrics = metrics[category]
        if not isinstance(category_support, dict) or not isinstance(category_metrics, dict):
            raise SystemExit("PolicyValidationReport per-category evidence is invalid")
        support_fields = (
            "gold_positive_applicable_assessable", "applicable_assessable_units",
            "baseline_predicted_findings", "candidate_predicted_findings", "independent_assignments",
        )
        if any(type(category_support.get(key)) is not int or category_support[key] < 0 for key in support_fields):
            raise SystemExit("PolicyValidationReport category support counts are invalid")
        if category_support["applicable_assessable_units"] < category_support["gold_positive_applicable_assessable"]:
            raise SystemExit("PolicyValidationReport category denominator is below positive support")
        if set(category_metrics) != {"baseline", "candidate"}:
            raise SystemExit("PolicyValidationReport must compare baseline and candidate metrics")
        by_arm: dict[str, dict[str, int]] = {}
        for arm in ("baseline", "candidate"):
            values = category_metrics[arm]
            fields = (
                "tp", "fp", "fn", "evidence_supported_findings", "decisions",
                "gold_unassessable_violations",
            )
            if not isinstance(values, dict) or any(type(values.get(key)) is not int or values[key] < 0 for key in fields):
                raise SystemExit("PolicyValidationReport metric counts are invalid")
            predicted = values["tp"] + values["fp"]
            if values["tp"] + values["fn"] != category_support["gold_positive_applicable_assessable"]:
                raise SystemExit("PolicyValidationReport recall denominator does not match positive support")
            if predicted != category_support[f"{arm}_predicted_findings"] or values["evidence_supported_findings"] > predicted:
                raise SystemExit("PolicyValidationReport predicted finding counts do not match support")
            if values["decisions"] > category_support["applicable_assessable_units"]:
                raise SystemExit("PolicyValidationReport decision coverage denominator is invalid")
            by_arm[arm] = values
        category_values[category] = by_arm
        if report["status"] == "eligible":
            if (
                category_support["gold_positive_applicable_assessable"] < 20
                or category_support["baseline_predicted_findings"] < 20
                or category_support["candidate_predicted_findings"] < 20
                or category_support["independent_assignments"] < 5
            ):
                raise SystemExit("eligible report lacks the required per-category evaluation support")
            baseline, candidate = by_arm["baseline"], by_arm["candidate"]
            denominator = Decimal(category_support["applicable_assessable_units"])
            if denominator == 0 or baseline["tp"] + baseline["fp"] == 0 or candidate["tp"] + candidate["fp"] == 0:
                raise SystemExit("eligible report contains an undefined metric denominator")
            with localcontext() as decimal_context:
                decimal_context.prec = 34
                baseline_precision = Decimal(baseline["tp"]) / Decimal(baseline["tp"] + baseline["fp"])
                candidate_precision = Decimal(candidate["tp"]) / Decimal(candidate["tp"] + candidate["fp"])
                baseline_esr = Decimal(baseline["evidence_supported_findings"]) / Decimal(baseline["tp"] + baseline["fp"])
                candidate_esr = Decimal(candidate["evidence_supported_findings"]) / Decimal(candidate["tp"] + candidate["fp"])
                baseline_recall = Decimal(baseline["tp"]) / Decimal(baseline["tp"] + baseline["fn"])
                candidate_recall = Decimal(candidate["tp"]) / Decimal(candidate["tp"] + candidate["fn"])
                baseline_coverage = Decimal(baseline["decisions"]) / denominator
                candidate_coverage = Decimal(candidate["decisions"]) / denominator
            if candidate_precision < max(Decimal("0.85"), baseline_precision):
                raise SystemExit("eligible report fails per-category precision gate")
            if candidate_esr < baseline_esr:
                raise SystemExit("eligible report fails per-category evidence support gate")
            if candidate_recall < max(Decimal("0.70"), baseline_recall - Decimal("0.02")):
                raise SystemExit("eligible report fails per-category end-to-end recall gate")
            if candidate_coverage < baseline_coverage - Decimal("0.02"):
                raise SystemExit("eligible report fails per-category selective coverage gate")
            if candidate["gold_unassessable_violations"] > baseline["gold_unassessable_violations"]:
                raise SystemExit("eligible report increases gold-unassessable decision violations")
            category_intervals = intervals.get(category) if isinstance(intervals, dict) else None
            if not isinstance(category_intervals, dict) or set(category_intervals) != {"baseline", "candidate"}:
                raise SystemExit("eligible report is missing per-arm confidence intervals")
            interval_metrics = {"precision", "evidence_support_rate", "end_to_end_recall", "selective_decision_coverage"}
            point_estimates = {
                "baseline": {
                    "precision": baseline_precision,
                    "evidence_support_rate": baseline_esr,
                    "end_to_end_recall": baseline_recall,
                    "selective_decision_coverage": baseline_coverage,
                },
                "candidate": {
                    "precision": candidate_precision,
                    "evidence_support_rate": candidate_esr,
                    "end_to_end_recall": candidate_recall,
                    "selective_decision_coverage": candidate_coverage,
                },
            }
            for arm in ("baseline", "candidate"):
                arm_intervals = category_intervals[arm]
                if not isinstance(arm_intervals, dict) or set(arm_intervals) != interval_metrics:
                    raise SystemExit("eligible report confidence interval metrics are incomplete")
                for bounds in arm_intervals.values():
                    if not isinstance(bounds, list) or len(bounds) != 2:
                        raise SystemExit("eligible report confidence interval is invalid")
                    try:
                        lower, upper = Decimal(str(bounds[0])), Decimal(str(bounds[1]))
                    except InvalidOperation as exc:
                        raise SystemExit("eligible report confidence interval is invalid") from exc
                    if not lower.is_finite() or not upper.is_finite() or lower < 0 or upper > 1 or lower > upper:
                        raise SystemExit("eligible report confidence interval is out of range")
                for metric, estimate in point_estimates[arm].items():
                    lower, upper = map(Decimal, map(str, arm_intervals[metric]))
                    if not lower <= estimate <= upper:
                        raise SystemExit("eligible report confidence interval excludes its point estimate")
    if report["status"] == "eligible":
        repeats = report.get("repeats")
        if (
            report["evaluation_kind"] != "empirical"
            or report["sample_count"] < 1
            or not isinstance(repeats, dict)
            or type(repeats.get("count")) is not int
            or repeats["count"] < 3
            or repeats.get("subset_preselected") is not True
            or not isinstance(repeats.get("task_families"), list)
            or any(not isinstance(item, str) for item in repeats["task_families"])
            or len(set(repeats["task_families"])) != len(repeats["task_families"])
            or set(repeats.get("task_families", [])) != set(scope["task_families"])
            or type(repeats.get("critical_invariant_violations")) is not int
            or repeats["critical_invariant_violations"] != 0
            or report.get("critical_invariant_violations") != []
            or type(report.get("pending_liability_count")) is not int
            or report["pending_liability_count"] != 0
            or not isinstance(report.get("ledger_refs"), list)
            or not report["ledger_refs"]
            or any(
                not isinstance(value, str) or not value.startswith("sha256:")
                or len(value) != 71 or any(char not in "0123456789abcdef" for char in value[7:])
                for value in report["ledger_refs"]
            )
            or not isinstance(report.get("critical_invariant_violations"), list)
        ):
            raise SystemExit("eligible report lacks repeats, clean critical gates, or settled ledger evidence")
        costs = report.get("actual_cost_usd_per_bundle")
        if not isinstance(costs, dict) or set(costs) != {"baseline", "candidate"}:
            raise SystemExit("eligible report requires paired actual cost per bundle")
        try:
            baseline_cost = Decimal(str(costs["baseline"]))
            candidate_cost = Decimal(str(costs["candidate"]))
        except InvalidOperation as exc:
            raise SystemExit("eligible report contains invalid actual cost") from exc
        if (
            not baseline_cost.is_finite() or not candidate_cost.is_finite()
            or baseline_cost <= 0 or candidate_cost < 0
            or candidate_cost > baseline_cost * Decimal("0.90")
        ):
            raise SystemExit("eligible report fails the paired actual cost reduction gate")
        if report["data_mode"] == "institutional" and not report.get("institutional_authorization_ref"):
            raise SystemExit("institutional report requires an authorization reference")
    if release.get("data_mode") != report.get("data_mode"):
        raise SystemExit("PolicyValidationReport data mode does not match release")
    allowed_modes = report.get("allowed_data_modes")
    if (
        not isinstance(allowed_modes, list) or not allowed_modes
        or any(mode not in {"synthetic", "institutional"} for mode in allowed_modes)
        or len(set(allowed_modes)) != len(allowed_modes)
        or report["data_mode"] not in allowed_modes
        or (report["provider_mode"] == "mock" and allowed_modes != ["synthetic"])
    ):
        raise SystemExit("PolicyValidationReport allowed data modes are invalid")
    report_sha256 = report.get("report_sha256")
    if (
        not isinstance(report_sha256, str)
        or len(report_sha256) != 64
        or any(char not in "0123456789abcdef" for char in report_sha256)
        or report_sha256 != sha256_json({key: value for key, value in report.items() if key != "report_sha256"})
    ):
        raise SystemExit("PolicyValidationReport report hash does not match its immutable content")


def require_release_validation_report(
    *,
    release_payload: dict[str, Any],
    routing: dict[str, Any],
    batching: dict[str, Any],
    candidate_core_hash: str,
    report: PolicyValidationReport | None,
) -> None:
    batch_size = batching.get("maximum_elements")
    required = (
        type(batch_size) is not int
        or batch_size != 1
        or routing.get("allow_live") is True
        or release_payload.get("provider_mode") == "live"
    )
    if not required:
        return
    if report is None or report.status != "eligible":
        raise SystemExit("release requires an eligible immutable PolicyValidationReport")
    if report.candidate_core_hash != candidate_core_hash:
        raise SystemExit("validation report core hash does not match release methods")
    if report.data_mode != release_payload.get("data_mode"):
        raise SystemExit("validation report data mode does not match release")


def _operator(db: DbSession) -> User | None:
    return db.scalar(select(User).where(User.global_role == "admin", User.active.is_(True)).order_by(User.created_at))


def _audit(
    db: DbSession,
    action: str,
    object_type: str,
    object_id: UUID | None,
    actor: User | None,
) -> None:
    request_id = uuid4()
    db.add(
        AuditEvent(
            actor_user_id=actor.id if actor else None,
            action=action,
            object_type=object_type,
            object_id=object_id,
            request_id=request_id,
            occurred_at=datetime.now(UTC),
        )
    )


def ensure_budget_account(db: DbSession) -> None:
    """Create the seed balance once; reruns never refill or overwrite it."""
    if db.get_bind().dialect.name == "postgresql":
        # Serialize the empty-table case; the partial unique index is a second
        # guard against accidental creation of more than one active account.
        db.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": 1095519559})
    if db.scalar(select(BudgetAccount.id).where(BudgetAccount.active.is_(True))) is None:
        db.add(
            BudgetAccount(
                currency="USD",
                confirmed_total=Decimal("25.00000000"),
                reserve_floor=Decimal("5.00000000"),
                planned_topup=Decimal("0.00000000"),
                active=True,
            )
        )


def run_migrations() -> None:
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")
    print(json.dumps({"migration": "head", "status": "completed"}))


def bootstrap_admin(email: str, display_name: str, password_from_stdin: bool) -> None:
    if not password_from_stdin:
        raise SystemExit("bootstrap admin requires --password-stdin")
    settings = get_settings()
    password = sys.stdin.readline().rstrip("\r\n")
    if len(password.encode("utf-8")) < 20:
        raise SystemExit("bootstrap temporary password must contain at least 20 bytes")
    password_hash = hash_password(password, settings.max_password_bytes)
    normalized = normalize_email(email)
    with SessionFactory.begin() as db:
        if db.scalar(select(User.id).where(User.normalized_email == normalized)) is not None:
            raise SystemExit("account already exists; bootstrap never resets an existing password")
        user = User(
            normalized_email=normalized,
            display_name=display_name.strip(),
            password_hash=password_hash,
            global_role="admin",
            active=True,
            must_change_password=True,
        )
        db.add(user)
        db.flush()
        _audit(db, "identity.bootstrap_admin", "user", user.id, user)
    print(json.dumps({"user_created": True, "email": normalized, "must_change_password": True}))


def seed_synthetic() -> None:
    settings = get_settings()
    if settings.app_env != "development" or settings.data_mode != "synthetic" or not settings.app_demo_mode:
        raise SystemExit("synthetic seed requires development, DATA_MODE=synthetic, and APP_DEMO_MODE=true")
    if settings.align_seed_password is None:
        raise SystemExit("ALIGN_SEED_PASSWORD must be set in the local environment")
    password = settings.align_seed_password.get_secret_value()
    password_hash = hash_password(password, settings.max_password_bytes)
    account_specs = [
        ("admin@align.local", "Admin Sintetis", "admin"),
        ("dosen@align.local", "Dosen Sintetis", "member"),
        ("mahasiswa@align.local", "Mahasiswa Sintetis", "member"),
        ("dosen2@align.local", "Dosen Sintetis Dua", "member"),
        ("mahasiswa2@align.local", "Mahasiswa Sintetis Dua", "member"),
    ]
    course_specs = [("IF101", "Pengantar Informatika"), ("IF202", "Sistem Informasi")]
    created_accounts = 0
    created_courses = 0
    created_memberships = 0
    with SessionFactory.begin() as db:
        users: dict[str, User] = {}
        for email, name, role in account_specs:
            normalized = normalize_email(email)
            user = db.scalar(select(User).where(User.normalized_email == normalized))
            if user is None:
                user = User(
                    normalized_email=normalized,
                    display_name=name,
                    password_hash=password_hash,
                    global_role=role,
                    active=True,
                    must_change_password=False,
                )
                db.add(user)
                db.flush()
                created_accounts += 1
            users[email] = user

        courses: dict[str, Course] = {}
        for code, title in course_specs:
            course = db.scalar(select(Course).where(Course.code == code))
            if course is None:
                course = Course(code=code, title=title, active=True)
                db.add(course)
                db.flush()
                created_courses += 1
            courses[code] = course

        for code, email, role in [
            ("IF101", "dosen@align.local", "lecturer"),
            ("IF101", "mahasiswa@align.local", "student"),
            ("IF202", "dosen2@align.local", "lecturer"),
            ("IF202", "mahasiswa2@align.local", "student"),
        ]:
            exists = db.scalar(
                select(Enrollment.id).where(
                    Enrollment.course_id == courses[code].id,
                    Enrollment.user_id == users[email].id,
                )
            )
            if exists is None:
                db.add(Enrollment(course_id=courses[code].id, user_id=users[email].id, role=role, active=True))
                created_memberships += 1

        ensure_budget_account(db)
        _audit(db, "seed.synthetic.ensure", "development_seed", None, _operator(db))
    print(
        json.dumps(
            {
                "status": "ready",
                "mode": "synthetic_demo",
                "accounts_created": created_accounts,
                "courses_created": created_courses,
                "memberships_created": created_memberships,
                "accounts": [item[0] for item in account_specs],
                "password_source": "ALIGN_SEED_PASSWORD in local .env (not printed)",
                "provider_mode": settings.provider_mode,
            },
            ensure_ascii=False,
        )
    )


def register_release(filename: str) -> None:
    manifest_path = Path(filename).resolve()
    release = json.loads(manifest_path.read_text(encoding="utf-8"))
    if release.get("schema_version") not in {1, 2}:
        raise SystemExit("unsupported release schema")
    provider_mode = release.get("provider_mode")
    data_mode = release.get("data_mode")
    if (provider_mode == "mock" and data_mode != "synthetic") or (
        provider_mode == "live" and data_mode != "institutional"
    ) or provider_mode not in {"mock", "live"}:
        raise SystemExit("mock releases require synthetic data; live releases require institutional data")
    if release.get("schema_version") == 1 and provider_mode != "mock":
        raise SystemExit("schema-1 B0 releases are mock-only")
    manifest_payloads: dict[str, dict[str, Any]] = {}
    for kind, relative_path in release["manifests"].items():
        if kind not in RELEASE_MANIFEST_COLUMNS:
            raise SystemExit(f"unsupported method manifest kind: {kind}")
        path = (manifest_path.parent / relative_path).resolve()
        payload = json.loads(path.read_text(encoding="utf-8"))
        manifest_payloads[kind] = payload
    for required_kind in RELEASE_MANIFEST_COLUMNS:
        if required_kind not in manifest_payloads:
            raise SystemExit(f"release is missing the {required_kind} manifest")
    rate_payload = manifest_payloads["rates"]
    if rate_payload.get("schema_version") != 1 or rate_payload.get("currency") != "USD":
        raise SystemExit("rate manifest schema or currency is invalid")
    rate_verified_at: datetime | None = None
    if rate_payload.get("status") == "verified":
        verification = rate_payload.get("verification")
        rate_models = rate_payload.get("models")
        if (
            not isinstance(verification, dict)
            or not all(isinstance(verification.get(key), str) and verification[key].strip()
                       for key in ("source_ref", "reviewed_by", "effective_date"))
            or not isinstance(rate_models, dict)
            or not rate_models
        ):
            raise SystemExit("verified rate manifest requires source, reviewer, effective date and per-model rates")
        for model_rates in rate_models.values():
            if not isinstance(model_rates, dict) or not all(
                key in model_rates for key in ("input_per_million", "output_per_million", "embedding_per_million")
            ):
                raise SystemExit("verified rate manifest is missing a billable token rate")
            try:
                values = [Decimal(str(model_rates[key])) for key in ("input_per_million", "output_per_million", "embedding_per_million")]
                if any(not value.is_finite() or value < 0 for value in values):
                    raise ValueError
            except (ValueError, ArithmeticError) as exc:
                raise SystemExit("verified rate manifest has an invalid decimal rate") from exc
        rate_verified_at = datetime.now(UTC)
    elif rate_payload.get("status") != "unverified" or rate_payload.get("rates") is not None:
        raise SystemExit("rate manifest must be explicitly verified or explicitly unverified")
    resource_payload = manifest_payloads["parser_resource"]
    resource_file = manifest_path.parent / str(resource_payload.get("resource_manifest", ""))
    if not resource_file.is_file() or sha256_hex(resource_file.read_bytes()) != resource_payload.get(
        "resource_manifest_sha256"
    ):
        raise SystemExit("parser resource manifest content hash does not match the release")
    report_payload: dict[str, Any] | None = None
    model_rate_name = manifest_payloads["model"].get("rates_manifest")
    rates_path = str(release["manifests"]["rates"])
    if model_rate_name and Path(str(model_rate_name)).name != Path(rates_path).name:
        raise SystemExit("model manifest and release rate manifest references disagree")
    report_path_value = release.get("policy_validation_report")
    if report_path_value is not None:
        report_path = (manifest_path.parent / str(report_path_value)).resolve()
        report_payload = json.loads(report_path.read_text(encoding="utf-8"))
        validate_policy_validation_report(report_payload, release, manifest_payloads)
    manifest_hashes = {kind: sha256_json(payload) for kind, payload in sorted(manifest_payloads.items())}
    stored_release = {**release, "method_manifest_sha256": manifest_hashes}
    config_hash = sha256_json(stored_release)
    with SessionFactory.begin() as db:
        existing = db.scalar(select(ReleaseManifest).where(ReleaseManifest.config_hash == config_hash))
        if existing is not None:
            print(json.dumps({"release_id": str(existing.id), "state": existing.state, "config_hash": config_hash}))
            return
        method_rows: dict[str, MethodManifest] = {}
        for kind, payload in manifest_payloads.items():
            digest = sha256_json(payload)
            row = db.scalar(select(MethodManifest).where(MethodManifest.sha256 == digest))
            if row is None:
                row = MethodManifest(
                    kind=kind,
                    version_label=str(payload.get("manifest_id", payload.get("policy_id", f"{kind}-baseline-v1"))),
                    content_json=payload,
                    sha256=digest,
                    verified_at=rate_verified_at if kind == "rates" else None,
                )
                db.add(row)
                db.flush()
            elif row.kind != kind or row.content_json != payload:
                raise SystemExit("manifest hash is already registered with different immutable content")
            elif kind == "rates" and rate_payload.get("status") == "verified" and row.verified_at is None:
                raise SystemExit("an existing immutable rate manifest lacks verification metadata")
            method_rows[kind] = row
        kwargs: dict[str, UUID] = {
            column: method_rows[kind].id for kind, column in RELEASE_MANIFEST_COLUMNS.items()
        }
        report_row: PolicyValidationReport | None = None
        if report_payload is not None:
            report_hash = sha256_json(report_payload)
            report_row = db.scalar(
                select(PolicyValidationReport).where(
                    PolicyValidationReport.report_hash == report_hash
                )
            )
            if report_row is None:
                report_row = PolicyValidationReport(
                    report_hash=report_hash,
                    status=str(report_payload["status"]),
                    data_mode=str(report_payload["data_mode"]),
                    candidate_core_hash=str(report_payload["candidate_core_hash"]),
                    baseline_core_hash=str(report_payload["baseline_core_hash"]),
                    report_json=report_payload,
                )
                db.add(report_row)
                db.flush()
        release_row = ReleaseManifest(
            version_label=release["version_label"],
            **kwargs,
            policy_validation_report_id=report_row.id if report_row else None,
            grounding_registry_version=release["grounding_registry_version"],
            lint_registry_version=release["lint_registry_version"],
            manifest_payload=stored_release,
            config_hash=config_hash,
            state="draft",
        )
        db.add(release_row)
        db.flush()
        _audit(db, "release.register", "release_manifest", release_row.id, _operator(db))
        result = {"release_id": str(release_row.id), "state": release_row.state, "config_hash": config_hash}
    print(json.dumps(result, ensure_ascii=False))


def activate_release(release_id: str) -> None:
    parsed_id = UUID(release_id)
    with SessionFactory.begin() as db:
        release = db.scalar(select(ReleaseManifest).where(ReleaseManifest.id == parsed_id).with_for_update())
        if release is None:
            raise SystemExit("release not found")
        if release.state == "active":
            print(json.dumps({"release_id": release_id, "state": "active", "empirical_verification": False}))
            return
        if release.state not in {"draft", "verified"}:
            raise SystemExit("retired releases cannot be activated again")
        payload = release.manifest_payload
        linked_manifests = {
            kind: db.get(MethodManifest, getattr(release, column))
            for kind, column in RELEASE_MANIFEST_COLUMNS.items()
        }
        if any(item is None for item in linked_manifests.values()):
            raise SystemExit("release method manifest links are incomplete")
        complete_manifests = {
            kind: item for kind, item in linked_manifests.items() if item is not None
        }
        rates = linked_manifests["rates"].content_json  # type: ignore[union-attr]
        routing = linked_manifests["routing"].content_json  # type: ignore[union-attr]
        batching = linked_manifests["batching"].content_json  # type: ignore[union-attr]
        batch_size = batching.get("maximum_elements")
        report = (
            db.get(PolicyValidationReport, release.policy_validation_report_id)
            if release.policy_validation_report_id
            else None
        )
        require_release_validation_report(
            release_payload=payload,
            routing=routing,
            batching=batching,
            candidate_core_hash=release_core_hash(
                complete_manifests,
                release.grounding_registry_version,
                release.lint_registry_version,
            ),
            report=report,
        )
        if report is not None and report.status == "eligible":
            active_baseline = db.scalar(
                select(ReleaseManifest).where(ReleaseManifest.state == "active").with_for_update()
            )
            if active_baseline is None:
                raise SystemExit("candidate release requires the active baseline it was evaluated against")
            baseline_manifests = {
                kind: db.get(MethodManifest, getattr(active_baseline, column))
                for kind, column in RELEASE_MANIFEST_COLUMNS.items()
            }
            if any(item is None for item in baseline_manifests.values()):
                raise SystemExit("active baseline manifest links are incomplete")
            complete_baseline = {kind: item for kind, item in baseline_manifests.items() if item is not None}
            if report.baseline_core_hash != release_core_hash(
                complete_baseline,
                active_baseline.grounding_registry_version,
                active_baseline.lint_registry_version,
            ):
                raise SystemExit("validation report baseline core hash does not match the active baseline")
        if payload.get("provider_mode") == "live" or routing.get("allow_live") is True:
            if rates.get("status") != "verified" or report is None or report.data_mode != "institutional":
                raise SystemExit("live release requires verified rates and institutionally eligible validation")
            if not get_settings().paid_calls_enabled:
                raise SystemExit("live release requires PAID_CALLS_ENABLED=true after financial guard verification")
        if release.rate_manifest_id is None or release.parser_resource_manifest_id is None:
            raise SystemExit("release must bind immutable rates and parser-resource manifests")
        checks = payload.get("activation_checks", {})
        available = set(payload.get("capabilities_available", []))
        unavailable = set(payload.get("capabilities_unavailable", []))
        is_baseline_mock = payload.get("provider_mode") == "mock" and payload.get("data_mode") == "synthetic"
        if is_baseline_mock:
            if rates.get("status") != "unverified" or rates.get("rates") is not None:
                raise SystemExit("synthetic baseline cannot activate a claimed live rate schedule")
            if linked_manifests["model"].content_json.get("paid_calls") is not False:  # type: ignore[union-attr]
                raise SystemExit("mock baseline must declare paid calls disabled")
            if routing.get("allow_live") is not False or routing.get("semantic_batch_size") != 1:
                raise SystemExit("baseline routing must keep live calls disabled and semantic batch size one")
            if batch_size != 1 or batching.get("live_batch_optimization") is not False:
                raise SystemExit("baseline batching must stay at batch size one without empirical optimization")
        elif payload.get("provider_mode") != "live" or payload.get("data_mode") != "institutional":
            raise SystemExit("release mode and data mode are incompatible")
        if not {"manual_auth", "mock_provider_schema"}.issubset(available):
            raise SystemExit("required baseline identity/mock capabilities are not available")
        is_baseline_mock = payload.get("provider_mode") == "mock" and payload.get("data_mode") == "synthetic"
        if payload.get("schema_version") == 1:
            if any(name not in unavailable for name in ("native_parser", "live_provider", "paid_calls")):
                raise SystemExit("unavailable parser/live/paid capabilities must remain explicit")
        elif payload.get("schema_version") == 2:
            required = {
                "b5_executor", "deterministic_applicability_dag", "scoped_hybrid_retrieval",
                "typed_grounding", "semantic_batch1_validator", "real_adapter_configurable",
                "review_analysis_integration",
            }
            unavailable_required = {
                "paid_calls", "live_semantic_verification", "efficient_semantic_route",
                "batch3", "batch5", "empirical_quality",
            }
            if not required.issubset(available) or (is_baseline_mock and not unavailable_required.issubset(unavailable)):
                raise SystemExit("B5 executor capability declaration is incomplete")
        else:
            raise SystemExit("unsupported release schema version")
        if not checks.get("mock_schema_contract") or not checks.get("paid_calls_fail_closed"):
            raise SystemExit("release capability checks have not passed")
        if is_baseline_mock:
            if checks.get("baseline_batch_size") != 1:
                raise SystemExit("only baseline batch size one may be activated without an eligible report")
            if checks.get("empirical_verification") is not False:
                raise SystemExit("synthetic mock release cannot claim empirical verification")
        elif checks.get("empirical_verification") is not True:
            raise SystemExit("optimized/live release requires an empirical verification declaration")
        if payload.get("schema_version") == 2 and not all(
            checks.get(name) is True for name in (
                "deterministic_executor", "source_scope_isolation",
                "quote_is_not_semantic_support", "empty_top_k_is_not_absence",
                "human_history_immutable",
            )
        ):
            raise SystemExit("B5 software gate checks have not passed")
        actor = _operator(db)
        if actor is None:
            raise SystemExit("bootstrap a local operator account before activation")
        active_releases = db.scalars(
            select(ReleaseManifest).where(ReleaseManifest.state == "active").with_for_update()
        ).all()
        for active in active_releases:
            active.state = "retired"
        db.flush()
        if release.state == "draft":
            release.state = "verified"
            db.flush()
        release.state = "active"
        release.activated_by = actor.id
        release.activated_at = datetime.now(UTC)
        _audit(db, "release.activate", "release_manifest", release.id, actor)
    print(json.dumps({
        "release_id": release_id,
        "state": "active",
        "empirical_verification": not is_baseline_mock,
    }))


def enqueue_foundation_smoke(idempotency_key: str) -> None:
    with SessionFactory.begin() as db:
        operation = db.scalar(
            select(Operation).where(
                Operation.kind == "foundation_smoke",
                Operation.idempotency_key == idempotency_key,
            )
        )
        if operation is None:
            operation = Operation(kind="foundation_smoke", idempotency_key=idempotency_key, status="queued")
            db.add(operation)
            db.flush()
            db.add(
                OutboxEvent(
                    event_type="operation.foundation_smoke",
                    subject_id=operation.id,
                    payload_json={"operation_id": str(operation.id)},
                )
            )
        result_id = str(operation.id)
        result_status = operation.status
    print(json.dumps({"operation_id": result_id, "status": result_status, "academic_work": False}))


def get_operation(operation_id: str) -> None:
    parsed_id = UUID(operation_id)
    with SessionFactory() as db:
        operation = db.get(Operation, parsed_id)
        if operation is None:
            raise SystemExit("operation not found")
        print(
            json.dumps(
                {
                    "operation_id": str(operation.id),
                    "kind": operation.kind,
                    "status": operation.status,
                    "attempt_count": operation.attempt_count,
                    "result": operation.result_json,
                    "academic_work": False,
                }
            )
        )


def _operator_session_factory() -> tuple[Any, Any]:
    url = get_settings().operator_database_url
    if not url:
        raise SystemExit("OPERATOR_DATABASE_URL is required for budget ledger changes")
    engine = create_engine(url, pool_pre_ping=True)
    return engine, sessionmaker(bind=engine, class_=DbSession, autoflush=False, expire_on_commit=False)


def purge_submission_retention(submission_id: str, policy_ref: str, *, execute: bool) -> None:
    settings = get_settings()
    if settings.data_mode != "synthetic":
        raise SystemExit("retention purge CLI is restricted to synthetic data mode")
    try:
        parsed_id = UUID(submission_id)
    except ValueError as exc:
        raise SystemExit("submission_id must be a UUID") from exc
    normalized_policy = policy_ref.strip()
    if not normalized_policy or len(normalized_policy) > 240:
        raise SystemExit("policy_ref must be a nonblank reference of at most 240 characters")
    if not normalized_policy.startswith("synthetic:"):
        raise SystemExit("policy_ref must start with synthetic:")

    now = datetime.now(UTC)
    actor_id: UUID
    object_keys: set[str] = set()
    with SessionFactory() as db:
        actor = _operator(db)
        if actor is None:
            raise SystemExit("an active admin operator account is required")
        actor_id = actor.id
        submission = db.get(Submission, parsed_id)
        if submission is None:
            raise SystemExit("submission not found")
        assets = list(db.scalars(select(FileAsset).where(FileAsset.submission_id == parsed_id)))
        if not assets:
            raise SystemExit("submission has no source assets to purge")
        source_cutoff = now - timedelta(days=settings.retention_source_days)
        if any(_as_utc(asset.created_at) > source_cutoff for asset in assets):
            raise SystemExit("source retention period has not elapsed for every source asset")
        versions = list(db.scalars(select(SubmissionVersion).where(SubmissionVersion.submission_id == parsed_id)))
        version_ids = [item.id for item in versions]
        reviews = list(db.scalars(select(ReviewVersion).where(
            ReviewVersion.submission_version_id.in_(version_ids)
        ))) if version_ids else []
        review_cutoff = now - timedelta(days=settings.retention_review_days)
        if any(_as_utc(item.created_at) > review_cutoff for item in reviews):
            raise SystemExit("review retention period has not elapsed for every review")
        parse_artifacts = list(db.scalars(select(ParseArtifact).where(
            ParseArtifact.file_asset_id.in_([item.id for item in assets])
        )))
        if any(item.status in {"queued", "running"} for item in parse_artifacts):
            raise SystemExit("submission has queued or running parser work")
        analysis_runs = list(db.scalars(select(AnalysisRun).where(
            AnalysisRun.submission_version_id.in_(version_ids)
        ))) if version_ids else []
        if any(item.status in {"queued", "running"} for item in analysis_runs):
            raise SystemExit("submission has queued or running analysis work")
        object_keys.update(item.object_key for item in assets)
        object_keys.update(item.artifact_object_key for item in parse_artifacts if item.artifact_object_key)

    if not execute:
        print(json.dumps({"status": "eligible_for_operator_purge", "submission_id": submission_id,
                          "source_asset_count": len(assets), "parser_artifact_count": len(parse_artifacts),
                          "stored_object_count": len(object_keys), "data_mode": "synthetic",
                          "dry_run": True}))
        return

    storage = storage_client(settings)
    try:
        for key in sorted(object_keys):
            storage.delete_object(Bucket=settings.s3_bucket, Key=key)
    except (BotoCoreError, ClientError, OSError) as exc:
        raise SystemExit("storage purge failed; retry is safe because object deletes are idempotent") from exc

    engine, factory = _operator_session_factory()
    try:
        with factory.begin() as db:
            result = db.execute(text("""
                SELECT retention_purge_submission(:submission_id,:actor_id,:policy_ref,:source_days,:review_days)
            """), {"submission_id": parsed_id, "actor_id": actor_id,
                   "policy_ref": normalized_policy, "source_days": settings.retention_source_days,
                   "review_days": settings.retention_review_days}).scalar_one()
    except Exception as exc:
        raise SystemExit("database redaction failed after object deletion; rerun the same operator command") from exc
    finally:
        engine.dispose()
    print(json.dumps({"status": "purged", "submission_id": submission_id,
                      "result": result, "stored_objects_deleted": len(object_keys),
                      "data_mode": "synthetic"}))


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def confirm_budget_topup(amount: str, confirmation_ref: str, reason: str) -> None:
    value = Decimal(amount)
    if not value.is_finite() or value <= 0 or len(amount) > 32:
        raise SystemExit("top-up amount must be a positive finite decimal USD amount")
    if not confirmation_ref.strip() or len(confirmation_ref) > 240 or not reason.strip() or len(reason) > 1000:
        raise SystemExit("confirmed top-up requires a reference and short reason")
    with SessionFactory() as api_db:
        actor = _operator(api_db)
        if actor is None:
            raise SystemExit("an active admin operator account is required")
        actor_id = actor.id
    engine, factory = _operator_session_factory()
    try:
        with factory.begin() as db:
            account = db.scalar(select(BudgetAccount).where(BudgetAccount.active.is_(True)).with_for_update())
            if account is None:
                raise SystemExit("active budget account is missing")
            row = BudgetTopup(
                budget_account_id=account.id,
                amount=value,
                confirmation_ref=confirmation_ref.strip(),
                actor_user_id=actor_id,
                reason=reason.strip(),
            )
            db.add(row)
            account.confirmed_total += value
            account.revision += 1
            db.add(AuditEvent(
                actor_user_id=actor_id,
                action="budget.topup.confirmed",
                object_type="budget_account",
                object_id=account.id,
                old_version_refs_json={"confirmed_total": str(account.confirmed_total - value)},
                new_version_refs_json={"confirmed_total": str(account.confirmed_total), "confirmation_ref": confirmation_ref.strip()},
                request_id=uuid4(),
            ))
            topup_id = row.id
    finally:
        engine.dispose()
    print(json.dumps({"topup_id": str(topup_id), "status": "confirmed", "amount_usd": str(value)}))


def reconcile_usage(
    usage_entry_id: str,
    *,
    resolution: str,
    amount: str,
    evidence_ref: str,
    provider_request_id: str | None,
) -> None:
    entry_id = UUID(usage_entry_id)
    value = Decimal(amount)
    if resolution not in {"billed", "not_billed"} or not value.is_finite() or value < 0:
        raise SystemExit("usage reconciliation requires billed/not_billed and a nonnegative decimal")
    if resolution == "not_billed" and value != 0:
        raise SystemExit("not_billed reconciliation must record zero actual cost")
    if not evidence_ref.strip() or len(evidence_ref) > 1000:
        raise SystemExit("usage reconciliation requires an operator evidence reference")
    with SessionFactory() as api_db:
        actor = _operator(api_db)
        if actor is None:
            raise SystemExit("an active admin operator account is required")
        actor_id = actor.id
    engine, factory = _operator_session_factory()
    try:
        with factory.begin() as db:
            entry = db.scalar(select(UsageEntry).where(UsageEntry.id == entry_id).with_for_update())
            if entry is None or entry.status != "pending":
                raise SystemExit("only an unresolved pending usage entry can be reconciled")
            reservation = db.scalar(select(UsageReservation).where(
                UsageReservation.call_id == entry.call_id
            ).with_for_update())
            if reservation is None or reservation.status != "reconciliation_required":
                raise SystemExit("pending usage has no reconciliation-required reservation")
            if entry.provider_request_id and provider_request_id and entry.provider_request_id != provider_request_id:
                raise SystemExit("provider request ID does not match the pending attempt")
            reconciliation = UsageReconciliation(
                usage_entry_id=entry.id,
                reservation_id=reservation.id,
                actual_cost=value,
                resolution=resolution,
                provider_request_id=provider_request_id or entry.provider_request_id,
                evidence_ref=evidence_ref.strip(),
                actor_user_id=actor_id,
            )
            db.add(reconciliation)
            reservation.status = "settled" if resolution == "billed" else "released"
            if resolution == "billed" and value > reservation.amount_reserved:
                account = db.scalar(select(BudgetAccount).where(
                    BudgetAccount.id == reservation.budget_account_id
                ).with_for_update())
                if account:
                    account.spend_frozen = True
                    account.freeze_reason = "reconciled_cost_exceeded_reserved_bound"
            db.add(AuditEvent(
                actor_user_id=actor_id,
                action="budget.usage.reconciled",
                object_type="usage_entry",
                object_id=entry.id,
                new_version_refs_json={"resolution": resolution, "actual_cost": str(value), "evidence_ref": evidence_ref.strip()},
                request_id=uuid4(),
            ))
    finally:
        engine.dispose()
    print(json.dumps({"usage_entry_id": usage_entry_id, "status": "reconciled", "resolution": resolution, "actual_cost_usd": str(value)}))


def resume_budget_spending(evidence_ref: str) -> None:
    if not evidence_ref.strip() or len(evidence_ref) > 1000:
        raise SystemExit("budget resume requires a reviewed evidence reference")
    with SessionFactory() as api_db:
        actor = _operator(api_db)
        if actor is None:
            raise SystemExit("an active admin operator account is required")
        actor_id = actor.id
    engine, factory = _operator_session_factory()
    try:
        with factory.begin() as db:
            if db.scalar(select(UsageReservation.id).where(
                UsageReservation.status == "reconciliation_required"
            ).limit(1)) is not None:
                raise SystemExit("pending usage liabilities must be reconciled before budget resumes")
            account = db.scalar(select(BudgetAccount).where(BudgetAccount.active.is_(True)).with_for_update())
            if account is None:
                raise SystemExit("active budget account is missing")
            old_reason = account.freeze_reason
            account.spend_frozen = False
            account.freeze_reason = None
            account.revision += 1
            db.add(AuditEvent(
                actor_user_id=actor_id,
                action="budget.spending.resumed",
                object_type="budget_account",
                object_id=account.id,
                old_version_refs_json={"freeze_reason": old_reason},
                new_version_refs_json={"evidence_ref": evidence_ref.strip()},
                request_id=uuid4(),
            ))
    finally:
        engine.dispose()
    print(json.dumps({"status": "spending_resumed", "evidence_ref": evidence_ref.strip()}))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="align", description="ALIGN local operator and development CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    db = subparsers.add_parser("db", help="database operations")
    db.add_subparsers(dest="db_command", required=True).add_parser("upgrade", help="apply Alembic migrations")

    admin = subparsers.add_parser("admin", help="operator account operations")
    admin_sub = admin.add_subparsers(dest="admin_command", required=True)
    bootstrap = admin_sub.add_parser("bootstrap", help="create the first operator from stdin")
    bootstrap.add_argument("--email", required=True)
    bootstrap.add_argument("--display-name", required=True)
    bootstrap.add_argument("--password-stdin", action="store_true")

    subparsers.add_parser("seed", help="ensure rerunnable synthetic development users and courses")

    release = subparsers.add_parser("release", help="register and activate immutable baseline releases")
    release_sub = release.add_subparsers(dest="release_command", required=True)
    register = release_sub.add_parser("register")
    register.add_argument("--file", required=True)
    activate = release_sub.add_parser("activate")
    activate.add_argument("release_id")

    budget = subparsers.add_parser("budget", help="operator-confirmed funding and billing reconciliation")
    budget_sub = budget.add_subparsers(dest="budget_command", required=True)
    topup = budget_sub.add_parser("topup")
    topup.add_argument("amount_usd")
    topup.add_argument("--confirmation-ref", required=True)
    topup.add_argument("--reason", required=True)
    reconcile = budget_sub.add_parser("reconcile")
    reconcile.add_argument("usage_entry_id")
    reconcile.add_argument("--resolution", choices=("billed", "not_billed"), required=True)
    reconcile.add_argument("--amount-usd", required=True)
    reconcile.add_argument("--evidence-ref", required=True)
    reconcile.add_argument("--provider-request-id")
    resume_budget = budget_sub.add_parser("resume")
    resume_budget.add_argument("--evidence-ref", required=True)

    operations = subparsers.add_parser("operations", help="non-academic worker operations")
    operation_sub = operations.add_subparsers(dest="operations_command", required=True)
    smoke = operation_sub.add_parser("smoke")
    smoke.add_argument("--idempotency-key", default=None)
    status = operation_sub.add_parser("status")
    status.add_argument("operation_id")

    parser_command = subparsers.add_parser("parser", help="source parser operations")
    parser_sub = parser_command.add_subparsers(dest="parser_command", required=True)
    benchmark = parser_sub.add_parser("benchmark", help="run actual isolated parser fixtures")
    benchmark.add_argument("--profile", required=True)
    benchmark.add_argument("--fixtures", required=True, type=Path)
    benchmark.add_argument("--output", required=True, type=Path)

    retention = subparsers.add_parser("retention", help="synthetic data retention and source purge")
    retention_sub = retention.add_subparsers(dest="retention_command", required=True)
    purge = retention_sub.add_parser("purge-submission", help="purge expired synthetic submission data")
    purge.add_argument("--submission-id", required=True)
    purge.add_argument("--policy-ref", default="synthetic:configured-retention-defaults")
    purge.add_argument("--execute", action="store_true", help="delete stored objects and redact scoped records")

    backup = subparsers.add_parser("backup", help="synthetic object snapshot and restore reconciliation")
    backup_sub = backup.add_subparsers(dest="backup_command", required=True)
    export_objects = backup_sub.add_parser("objects-export", help="write a verified object-store tar archive")
    export_objects.add_argument("--output", required=True, type=Path)
    import_objects = backup_sub.add_parser("objects-import", help="restore objects into an empty bucket")
    import_objects.add_argument("--input", required=True, type=Path)
    backup_sub.add_parser("reconcile", help="check database pointers, object hashes, tombstones, and liabilities")

    evaluate = subparsers.add_parser("evaluate", help="offline robustness and protocol evaluations")
    evaluate_sub = evaluate.add_subparsers(dest="evaluate_command", required=True)
    metrics = evaluate_sub.add_parser("metrics", help="score gold labels and predictions offline")
    metrics.add_argument("--dataset-manifest", required=True, type=Path)
    metrics.add_argument("--output", required=True, type=Path)
    metrics.add_argument("--bootstrap-resamples", type=int, default=10_000)
    metrics.add_argument("--seed", type=int, default=20261018)
    policies = evaluate_sub.add_parser("policies", help="compare candidate and baseline policy evidence offline")
    policies.add_argument("--candidate", required=True)
    policies.add_argument("--baseline", required=True)
    policies.add_argument("--dataset-manifest", required=True, type=Path)
    policies.add_argument("--output", required=True, type=Path)
    policies.add_argument("--bootstrap-resamples", type=int, default=10_000)
    policies.add_argument("--seed", type=int, default=20261018)
    robustness = evaluate_sub.add_parser("robustness", help="validate and run compiler/DAG robustness checks")
    robustness.add_argument("--protocol", required=True, type=Path)
    robustness.add_argument("--dataset-manifest", required=True, type=Path)
    robustness.add_argument("--release", required=True)
    robustness.add_argument("--output", required=True, type=Path)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "db" and args.db_command == "upgrade":
        run_migrations()
    elif args.command == "admin" and args.admin_command == "bootstrap":
        bootstrap_admin(args.email, args.display_name, args.password_stdin)
    elif args.command == "seed":
        seed_synthetic()
    elif args.command == "release" and args.release_command == "register":
        register_release(args.file)
    elif args.command == "release" and args.release_command == "activate":
        activate_release(args.release_id)
    elif args.command == "budget" and args.budget_command == "topup":
        confirm_budget_topup(args.amount_usd, args.confirmation_ref, args.reason)
    elif args.command == "budget" and args.budget_command == "reconcile":
        reconcile_usage(
            args.usage_entry_id,
            resolution=args.resolution,
            amount=args.amount_usd,
            evidence_ref=args.evidence_ref,
            provider_request_id=args.provider_request_id,
        )
    elif args.command == "budget" and args.budget_command == "resume":
        resume_budget_spending(args.evidence_ref)
    elif args.command == "operations" and args.operations_command == "smoke":
        enqueue_foundation_smoke(args.idempotency_key or str(uuid4()))
    elif args.command == "operations" and args.operations_command == "status":
        get_operation(args.operation_id)
    elif args.command == "parser" and args.parser_command == "benchmark":
        from align_worker.benchmark import run_parser_benchmark

        run_parser_benchmark(args.profile, args.fixtures, args.output)
    elif args.command == "evaluate" and args.evaluate_command == "metrics":
        from align_api.evaluation import EvaluationError, evaluate_metrics_file

        try:
            report = evaluate_metrics_file(
                args.dataset_manifest,
                args.output,
                bootstrap_resamples=args.bootstrap_resamples,
                seed=args.seed,
            )
        except EvaluationError as exc:
            raise SystemExit(str(exc)) from exc
        print(json.dumps({"status": report["status"], "output": str(args.output),
                          "dataset_sha256": report["dataset_sha256"],
                          "report_sha256": report["report_sha256"]}))
    elif args.command == "evaluate" and args.evaluate_command == "policies":
        from align_api.evaluation import EvaluationError, evaluate_policy_file

        try:
            report = evaluate_policy_file(
                args.dataset_manifest,
                args.output,
                candidate_id=args.candidate,
                baseline_id=args.baseline,
                root=Path(__file__).resolve().parents[4],
                bootstrap_resamples=args.bootstrap_resamples,
                seed=args.seed,
                ledger_verified=False,
            )
        except EvaluationError as exc:
            raise SystemExit(str(exc)) from exc
        print(json.dumps({"status": report["status"], "output": str(args.output),
                          "candidate_core_hash": report["candidate_core_hash"],
                          "baseline_core_hash": report["baseline_core_hash"],
                          "report_sha256": report["report_sha256"]}))
    elif args.command == "evaluate" and args.evaluate_command == "robustness":
        from align_api.robustness import (
            ProtocolError,
            evaluate_robustness_files,
            parse_release_id,
        )

        release_id = parse_release_id(args.release)
        with SessionFactory() as db:
            release_row = db.get(ReleaseManifest, release_id)
            if release_row is None:
                raise SystemExit("release is not registered")
            manifest_hashes: dict[str, str] = {}
            for kind, column in RELEASE_MANIFEST_COLUMNS.items():
                manifest_id = getattr(release_row, column)
                manifest = db.get(MethodManifest, manifest_id) if manifest_id else None
                if manifest is None or manifest.kind != kind:
                    raise SystemExit("registered release has an incomplete method manifest binding")
                if sha256_json(manifest.content_json) != manifest.sha256:
                    raise SystemExit("registered method manifest content hash is invalid")
                manifest_hashes[kind] = manifest.sha256
            if release_row.manifest_payload.get("method_manifest_sha256") != manifest_hashes:
                raise SystemExit("registered release manifest hashes do not match linked immutable methods")
            release = {
                "id": str(release_row.id),
                "config_hash": release_row.config_hash,
                "method_manifest_sha256": manifest_hashes,
            }
            try:
                report = evaluate_robustness_files(
                    args.protocol, args.dataset_manifest, args.output, release
                )
            except ProtocolError as exc:
                raise SystemExit(str(exc)) from exc
        print(json.dumps({"status": report["status"], "method": report["method"],
                          "output": str(args.output), "report_type": report["report_type"]}))
    elif args.command == "retention" and args.retention_command == "purge-submission":
        purge_submission_retention(args.submission_id, args.policy_ref, execute=args.execute)
    elif args.command == "backup":
        from align_api.storage_backup import (
            export_storage_archive,
            import_storage_archive,
            reconcile_restored_state,
        )

        settings = get_settings()
        if settings.data_mode != "synthetic":
            raise SystemExit("this backup/restore workflow accepts synthetic data only")
        try:
            if args.backup_command == "objects-export":
                result = export_storage_archive(settings, args.output)
            elif args.backup_command == "objects-import":
                result = import_storage_archive(settings, args.input)
            else:
                result = reconcile_restored_state(settings)
        except (BotoCoreError, ClientError, OSError, ValueError) as exc:
            raise SystemExit(f"synthetic object backup operation failed: {exc}") from exc
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        if args.backup_command == "reconcile" and result["status"] != "reconciled":
            raise SystemExit("restored state is incomplete; keep service closed")
    else:
        raise SystemExit("unknown command")


if __name__ == "__main__":
    main()
