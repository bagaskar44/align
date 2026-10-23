"""B4 provider-off review, append-only evidence and released student views."""

from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from align_api.cli import ensure_budget_account
from align_api.config import get_settings
from align_api.db import SessionFactory
from align_api.finance_lock import lock_paid_finance_scope
from align_api.main import create_app
from align_api.models import (
    AnalysisRun,
    Assignment,
    AssignmentGroup,
    AssignmentVersion,
    BudgetAccount,
    CheckElement,
    ContextVersion,
    Course,
    Criterion,
    CriterionRequirementLink,
    CriterionSetVersion,
    ElementRequirementLink,
    Enrollment,
    EvidenceItem,
    FileAsset,
    FindingCandidate,
    FindingElementLink,
    FindingEvidenceLink,
    FindingRequirementLink,
    GroupMembership,
    Job,
    MethodManifest,
    NumericPolicyVersion,
    PaidCallRun,
    ParseArtifact,
    ParserCheckpoint,
    Publication,
    ReleaseManifest,
    Requirement,
    RetentionTombstone,
    ReviewVersion,
    SourceUnit,
    SpecificationVersion,
    Submission,
    SubmissionVersion,
    SubmissionVersionFile,
    SubmissionVersionMember,
    UsageReservation,
    User,
)
from align_api.security import hash_password
from align_api.storage import storage_client
from align_domain.assignment_contracts import canonical_hash
from align_domain.finance import BudgetState
from align_worker.database import SessionFactory as WorkerSessionFactory
from align_worker.finance import CallBounds, release_unstarted_call, reserve_call

pytestmark = pytest.mark.skipif(
    os.environ.get("ALIGN_RUN_INTEGRATION") != "true",
    reason="requires isolated Compose PostgreSQL, Redis and object storage",
)
PASSWORD = "Synthetic-B4-review-integration"  # noqa: S105 -- synthetic fixture only


def _headers(csrf: str, key: str | None = None) -> dict[str, str]:
    return {"Origin": "http://testserver", "X-CSRF-Token": csrf, "Idempotency-Key": key or str(uuid4())}


@pytest.fixture
def _scenario(request):
    suffix = uuid4().hex
    source_bytes = Path("tests/fixtures/b1/native-two-pages.pdf").read_bytes()
    source_digest = hashlib.sha256(source_bytes).hexdigest()
    lecturer, lecturer_b, student, classmate, outsider = [
        User(normalized_email=f"b4-{role}-{suffix}@example.test", display_name=f"B4 {role}",
             password_hash=hash_password(PASSWORD), global_role="admin" if role == "outsider" else "member", active=True,
             must_change_password=False)
        for role in ("lecturer", "lecturer-b", "student", "classmate", "outsider")
    ]
    course = Course(code=f"B4-{suffix[:12]}", title="Synthetic B4 course", active=True)
    with SessionFactory.begin() as db:
        db.add_all([lecturer, lecturer_b, student, classmate, outsider, course])
        db.flush()
        now = datetime.now(UTC)
        context = ContextVersion(course_id=course.id, version_no=1, state="approved",
            fields_json={"context_description": {"value": "Context sintetis B4."},
                         "outcomes_status": "not_provided", "learning_outcomes": []},
            instruction_sources_json=[], content_hash=canonical_hash("B4 context"),
            approved_by=lecturer.id, approved_at=now, revision=1)
        db.add(context)
        db.flush()
        course.approved_context_version_id = context.id
        assignment = Assignment(course_id=course.id, title=f"B4 manual review {suffix}",
                                administrative_state="open")
        db.add(assignment)
        db.flush()
        group = AssignmentGroup(assignment_id=assignment.id, name="B4 synthetic group", active=True)
        db.add(group)
        db.flush()
        numeric_policy_payload = {
            "metric_definition": "Besaran sintetis yang sama dalam konteks yang sama.",
            "required_context_fields": ["metric", "entity", "period", "definition", "unit"], "number_locale": "id-ID",
            "canonical_unit": "kg", "allowed_conversions": [], "comparison_mode": "pairwise",
            "absolute_tolerance": "0", "relative_tolerance": "0",
            "rounding": {"mode": "none", "quantum": None},
        }
        numeric_policy = NumericPolicyVersion(assignment_id=assignment.id, policy_key="b4-synthetic-pairwise",
            version_no=1, state="approved", payload_json=numeric_policy_payload,
            content_hash=canonical_hash(numeric_policy_payload), approved_by=lecturer.id,
            approved_at=now, revision=1)
        db.add(numeric_policy)
        db.flush()
        criterion_set = CriterionSetVersion(assignment_id=assignment.id, context_version_id=context.id,
            version_no=1, instruction_hash=canonical_hash("B4 instruction"), state="draft",
            content_hash=canonical_hash("B4 criterion set"), approved_by=None,
            approved_at=None, revision=1)
        db.add(criterion_set)
        db.flush()
        criterion = Criterion(criterion_set_version_id=criterion_set.id, code="C1", title="Bukti",
            descriptor="Tunjukkan bukti sumber yang relevan.", display_order=0)
        db.add(criterion)
        db.flush()
        requirement = Requirement(criterion_set_version_id=criterion_set.id, code="R1",
            text="Setiap klaim harus didukung sumber.", mandatory=True,
            obligation_key="evidence.support", normative_source_refs_json=[])
        db.add(requirement)
        db.flush()
        db.add(CriterionRequirementLink(criterion_id=criterion.id, requirement_id=requirement.id,
            criterion_set_version_id=criterion_set.id))
        db.flush()
        criterion_set.state, criterion_set.approved_by, criterion_set.approved_at = "approved", lecturer.id, now
        db.flush()
        spec = SpecificationVersion(assignment_id=assignment.id, version_no=1, schema_version=2,
            state="draft", criterion_set_version_id=criterion_set.id, context_version_id=context.id,
            instruction_hash=canonical_hash("B4 instruction"), payload_json={"elements": []},
            content_hash=canonical_hash("B4 spec"), fidelity_confirmed_by=None,
            fidelity_confirmed_at=None, approved_by=None, approved_at=None, revision=1)
        db.add(spec)
        db.flush()
        element = CheckElement(specification_version_id=spec.id, element_id=uuid4(), element_code="E1",
            criterion_id=criterion.id, obligation_key="evidence.support", target_kind="requirement",
            decision_question="Apakah klaim memakai bukti yang dapat diperiksa?",
            grounding_mode="human_only", payload_json={"relationships": []})
        db.add(element)
        db.flush()
        db.add(ElementRequirementLink(check_element_id=element.id, requirement_id=requirement.id))
        spec.payload_json = {
            "schema_version": 2,
            "elements": [{
                "element_id": str(element.element_id), "element_code": "E1", "criterion_id": str(criterion.id),
                "requirement_ids": [str(requirement.id)], "obligation_key": "evidence.support",
                "target_kind": "requirement", "decision_question": element.decision_question,
                "derivation_refs": [{"source_kind": "requirement", "source_id": str(requirement.id), "span": None}],
                "expected_evidence": "Bukti relevan yang menjawab tuntutan resmi.",
                "grounding_contract": {"schema_version": 1, "mode": "human_only", "absence_support": None},
                "accepted_evidence_types": ["paragraph"], "relationships": [],
                "applicability": {"mode": "always"}, "source_hints": ["klaim dan bukti"],
                "operators": ["locate_evidence", "request_human_review"], "dependencies": [],
                "coverage_policy": {"positive_strategy": "sufficient_support",
                    "negative_strategy": "exhaustive_compatible_source_units", "inspect_embedded_visuals": True,
                    "unresolved_compatible_unit_blocks_negative": True, "preserve_table_context": True},
                "abstention_conditions": ["unreadable_relevant_region"], "numeric_policy_version_id": None,
                "human_judgment_boundary": "Keputusan akademik tetap dilakukan dosen.",
            }],
            "operator_registry_version": "operators-v1", "predicate_registry_version": "predicates-v1",
            "grounding_registry_version": "grounding-v1", "lint_registry_version": "lint-v1",
            "task_facts": [], "limits": {"draft_max_cost_usd": "0.05", "max_text_calls": 3,
                "max_vision_calls": 2, "max_input_tokens": 20000, "max_output_tokens": 4000},
        }
        db.flush()
        spec.state, spec.fidelity_confirmed_by, spec.fidelity_confirmed_at = "approved", lecturer.id, now
        spec.approved_by, spec.approved_at = lecturer.id, now
        db.flush()
        version = AssignmentVersion(assignment_id=assignment.id, version_no=1, state="published",
            context_version_id=context.id, instruction_original="Jelaskan klaim dan buktinya.",
            instruction_hash=criterion_set.instruction_hash, instruction_summary=None, mode="group",
            opens_at=now - timedelta(days=2), due_at=now + timedelta(days=7),
            late_policy="accept_with_label", limits_json={}, criterion_set_version_id=criterion_set.id,
            specification_version_id=spec.id, package_approved_by=lecturer.id,
            package_approved_at=now, published_by=lecturer.id, published_at=now, revision=1)
        db.add(version)
        db.flush()
        assignment.active_version_id = version.id
        enrollments = [
            Enrollment(course_id=course.id, user_id=lecturer.id, role="lecturer", active=True),
            Enrollment(course_id=course.id, user_id=lecturer_b.id, role="lecturer", active=True),
            Enrollment(course_id=course.id, user_id=student.id, role="student", active=True),
            Enrollment(course_id=course.id, user_id=classmate.id, role="student", active=True),
        ]
        db.add_all(enrollments)
        db.add_all([
            GroupMembership(group_id=group.id, assignment_id=assignment.id, user_id=student.id, active=True),
            GroupMembership(group_id=group.id, assignment_id=assignment.id, user_id=classmate.id, active=True),
        ])
        submission = Submission(assignment_id=assignment.id, individual_user_id=None,
            group_id=group.id, active_final_version_id=None, draft_assignment_version_id=version.id,
            draft_revision=1, revision=1)
        db.add(submission)
        db.flush()
        digest = source_digest
        asset = FileAsset(course_id=course.id, submission_id=submission.id, assignment_id=None,
            purpose="submission", uploaded_by=student.id, original_name="b4-source.pdf", size_bytes=len(source_bytes),
            mime="application/pdf", sha256=digest, object_key=f"b4/{suffix}/source.pdf",
            validation_state="validated", preflight_json={"parser_profile": "native_pdf_v1", "pages": 2},
            created_at=(datetime.now(UTC) - timedelta(days=400) if getattr(request, "param", None) == "b7-retention"
                        else datetime.now(UTC)))
        db.add(asset)
        db.flush()
        source_manifest_files = [{"file_asset_id": str(asset.id), "sha256": digest}]
        source = SubmissionVersion(submission_id=submission.id, version_no=1,
            assignment_version_id=version.id, manifest_hash=canonical_hash(source_manifest_files),
            manifest_json={"files": source_manifest_files},
            finalized_by=student.id, finalized_at=now, is_late=False)
        db.add(source)
        db.flush()
        submission.active_final_version_id = source.id
        db.add_all([
            SubmissionVersionMember(submission_version_id=source.id, user_id=member.id,
                display_name_snapshot=member.display_name, email_snapshot=member.normalized_email)
            for member in (student, classmate)
        ])
        db.add(SubmissionVersionFile(submission_version_id=source.id, submission_id=submission.id,
            file_asset_id=asset.id, display_order=0))
        artifact = ParseArtifact(file_asset_id=asset.id, submission_version_id=source.id,
            fingerprint=canonical_hash("B4 parser fixture"), owner_scope_hash=canonical_hash(str(source.id)),
            status="parsed", limitations_json=[])
        db.add(artifact)
        db.flush()
        anchor = {"format": "pdf", "page": 1}
        unit = SourceUnit(parse_artifact_id=artifact.id, locator_key=canonical_hash("B4 unit"),
            unit_type="pdf_page", source_order=1, anchor_json=anchor, parse_status="parsed",
            limitations_json=[])
        db.add(unit)
        db.flush()
        if getattr(request, "param", None) == "b7-retention":
            parse_job = Job(type="b7_retention_parser_fixture", subject_id=asset.id,
                scope_json={"synthetic": True}, input_fingerprint=canonical_hash({
                    "fixture": "B7 retention parser fixture", "submission_version": str(source.id)}),
                status="ready_for_review", job_limits_json={})
            db.add(parse_job)
            db.flush()
            db.add(ParserCheckpoint(job_id=parse_job.id, parse_artifact_id=artifact.id,
                task_key="retention-test", file_hash=digest, namespace_version_id=source.id,
                canonical_unit_locator_json={"source_path": "private synthetic source locator"},
                stage="parse", status="succeeded", output_refs_json={"text": "private text"},
                covered_unit_refs_json=[{"source_unit": "private source unit"}], limitations_json=[]))
        evidence = EvidenceItem(source_unit_id=unit.id, submission_version_id=source.id,
            context_version_id=None, assignment_version_id=None, file_asset_id=asset.id,
            source_sha256=digest, type="paragraph", anchor_json=anchor,
            content_original_json={"text": "Teks bukti sintetis pada halaman satu."},
            content_normalized_json={"text": "Teks bukti sintetis pada halaman satu."},
            parse_status="parsed", limitations_json=[])
        db.add(evidence)
        db.flush()

    settings = get_settings().model_copy(update={
        "app_origin": "http://testserver", "provider_mode": "off", "redis_url": "redis://redis:6379/0",
        "s3_endpoint": "http://object-storage:9090", "redis_queue_prefix": f"b4-test-{suffix}",
    })
    assert settings.provider_mode == "off"
    storage_client(settings).put_object(Bucket=settings.s3_bucket, Key=asset.object_key,
        Body=source_bytes, ContentType=asset.mime)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    with ExitStack() as stack:
        clients = {key: stack.enter_context(TestClient(app)) for key in ("lecturer", "lecturer_b", "student", "classmate", "outsider")}
        csrf = {}
        for key, user in (("lecturer", lecturer), ("lecturer_b", lecturer_b), ("student", student),
                          ("classmate", classmate), ("outsider", outsider)):
            response = clients[key].post("/api/v1/auth/login", headers={"Origin": settings.app_origin},
                json={"email": user.normalized_email, "password": PASSWORD})
            assert response.status_code == 200, response.text
            csrf[key] = response.json()["csrf_token"]
        yield {"clients": clients, "csrf": csrf, "settings": settings,
               "course": course, "assignment": assignment, "version": version,
               "criterion": criterion, "element": element, "requirement": requirement,
               "submission": submission, "source": source, "asset": asset, "evidence": evidence,
               "artifact": artifact, "unit": unit,
               "lecturer": lecturer, "lecturer_b": lecturer_b, "student": student,
               "classmate": classmate, "outsider": outsider, "numeric_policy": numeric_policy}


@pytest.fixture
def _budget_exhausted():
    """Use the full USD 20 spendable pool, leaving provider-off manual review intact."""
    nonce = uuid4().hex
    rate_content = {
        "schema_version": 1,
        "status": "verified",
        "currency": "USD",
        "verification": {"source_ref": f"test:{nonce}", "reviewed_by": "synthetic-test", "effective_date": "2026-10-22"},
        "models": {
            f"b6-exhausted-{nonce[:8]}": {
                "input_per_million": "20", "output_per_million": "0", "embedding_per_million": "0"
            },
            "text-embedding-3-small": {
                "input_per_million": "0.02", "output_per_million": "0", "embedding_per_million": "0.02"
            },
        },
    }
    model_id = f"b6-exhausted-{nonce[:8]}"
    call_id, logical_call_id = uuid4(), uuid4()
    run_id = uuid4()
    with SessionFactory.begin() as db:
        ensure_budget_account(db)
        db.flush()
        account = db.scalar(select(BudgetAccount).where(BudgetAccount.active.is_(True)))
        assert account is not None and account.spend_frozen is False
        rate = MethodManifest(
            kind="rates", version_label=f"b6-exhausted-{nonce[:8]}",
            content_json=rate_content, sha256=canonical_hash(rate_content), verified_at=datetime.now(UTC),
        )
        manifests: dict[str, MethodManifest] = {"rates": rate}
        for kind in ("parser", "parser_resource", "model", "retrieval", "rules", "prompt", "routing", "batching"):
            content = (
                {
                    "provider_mode": "live", "paid_calls": True, "capability_smoke": True,
                    "embedding_dimensions": 1536,
                    "live_model_ids": {
                        "efficient": model_id, "complex": model_id, "vision": model_id,
                        "embedding": "text-embedding-3-small",
                    },
                }
                if kind == "model"
                else {
                    "fixture_kind": kind, "nonce": nonce, "allow_live": kind == "routing",
                    "maximum_elements": 1 if kind == "batching" else None,
                }
            )
            manifest = MethodManifest(
                kind=kind, version_label=f"b4-budget-{kind}-{nonce[:8]}",
                content_json=content, sha256=canonical_hash(content),
            )
            db.add(manifest)
            db.flush()
            manifests[kind] = manifest
        job = Job(
            type="b6_budget_exhaustion_fixture", subject_id=uuid4(), scope_json={"fixture": nonce},
            input_fingerprint=canonical_hash({"fixture": nonce}), status="queued",
            job_limits_json={
                "max_calls": 1, "max_vision_calls": 0, "max_text_input_tokens": 1_000_000,
                "max_text_output_tokens": 0, "max_embedding_tokens": 0,
                "max_active_seconds": 600, "budget_usd": "20",
            },
        )
        db.add_all([rate, job])
        db.flush()
        actor = db.scalar(select(User).where(User.global_role == "admin", User.active.is_(True)))
        if actor is None:
            actor = User(
                normalized_email=f"b4-budget-{nonce[:8]}@example.test",
                display_name="Synthetic Budget Operator", password_hash=hash_password(PASSWORD),
                global_role="admin", active=True,
            )
            db.add(actor)
            db.flush()
        previous_release = db.scalar(
            select(ReleaseManifest).where(ReleaseManifest.state == "active").with_for_update()
        )
        previous_release_snapshot = None
        if previous_release is not None:
            previous_release_snapshot = {
                "version_label": previous_release.version_label,
                "parser_manifest_id": previous_release.parser_manifest_id,
                "parser_resource_manifest_id": previous_release.parser_resource_manifest_id,
                "model_manifest_id": previous_release.model_manifest_id,
                "rate_manifest_id": previous_release.rate_manifest_id,
                "retrieval_manifest_id": previous_release.retrieval_manifest_id,
                "rules_manifest_id": previous_release.rules_manifest_id,
                "prompt_manifest_id": previous_release.prompt_manifest_id,
                "routing_policy_manifest_id": previous_release.routing_policy_manifest_id,
                "batching_policy_manifest_id": previous_release.batching_policy_manifest_id,
                "policy_validation_report_id": previous_release.policy_validation_report_id,
                "grounding_registry_version": previous_release.grounding_registry_version,
                "lint_registry_version": previous_release.lint_registry_version,
                "manifest_payload": previous_release.manifest_payload,
                "config_hash": previous_release.config_hash,
                "activated_by": previous_release.activated_by,
            }
            previous_release.state = "retired"
        release_id = uuid4()
        db.add(ReleaseManifest(
            id=release_id, version_label=f"b4-budget-release-{nonce[:8]}",
            parser_manifest_id=manifests["parser"].id,
            parser_resource_manifest_id=manifests["parser_resource"].id,
            model_manifest_id=manifests["model"].id,
            rate_manifest_id=manifests["rates"].id,
            retrieval_manifest_id=manifests["retrieval"].id,
            rules_manifest_id=manifests["rules"].id,
            prompt_manifest_id=manifests["prompt"].id,
            routing_policy_manifest_id=manifests["routing"].id,
            batching_policy_manifest_id=manifests["batching"].id,
            grounding_registry_version="b4-finance-fixture", lint_registry_version="b4-finance-fixture",
            manifest_payload={"provider_mode": "live", "data_mode": "synthetic"},
            config_hash=canonical_hash({"b4_budget_release": nonce}),
            state="active", activated_by=actor.id, activated_at=datetime.now(UTC),
        ))
        job_id, rate_id, model_manifest_id, account_id, actor_id = (
            job.id, rate.id, manifests["model"].id, account.id, actor.id
        )

    operator_engine = create_engine(get_settings().operator_database_url, pool_pre_ping=True)
    operator_factory = sessionmaker(bind=operator_engine, class_=DbSession, autoflush=False, expire_on_commit=False)
    try:
        with operator_factory.begin() as db:
            lock_paid_finance_scope(db)
            for active_run in db.scalars(
                select(PaidCallRun).where(PaidCallRun.state == "active").with_for_update()
            ).all():
                active_run.state = "closed"
                active_run.closed_at = datetime.now(UTC)
                active_run.close_reason = "isolated B4 budget-exhaustion fixture setup"
            db.add(PaidCallRun(
                id=run_id, budget_account_id=account_id, release_manifest_id=release_id,
                cap_usd=Decimal("20"), authorization_ref="synthetic-b4-test-authorization",
                created_by=actor_id, state="active",
            ))
    finally:
        operator_engine.dispose()
    reserve_call(
        job_id=job_id, call_id=call_id, logical_call_id=logical_call_id, attempt_no=1,
        release_manifest_id=release_id, model_manifest_id=model_manifest_id,
        rate_manifest_id=rate_id, model_id=model_id,
        bounds=CallBounds(input_tokens=1_000_000, active_ms=1),
    )
    with WorkerSessionFactory() as db:
        reservation = db.scalar(select(UsageReservation).where(UsageReservation.call_id == call_id))
        account = db.scalar(select(BudgetAccount).where(BudgetAccount.active.is_(True)))
        assert reservation is not None and reservation.status == "held"
        assert reservation.amount_reserved == Decimal("20.00000000")
        assert account is not None
        assert BudgetState(
            confirmed_total=account.confirmed_total,
            reserve_floor=account.reserve_floor,
            known_spend=Decimal("0"),
            held_reservations=reservation.amount_reserved,
            planned_topup=account.planned_topup,
        ).spendable == Decimal("0E-8")
    try:
        yield
    finally:
        release_unstarted_call(call_id, proof="provider-off manual-review fixture made no provider request")
        operator_engine = create_engine(get_settings().operator_database_url, pool_pre_ping=True)
        operator_factory = sessionmaker(bind=operator_engine, class_=DbSession, autoflush=False, expire_on_commit=False)
        try:
            with operator_factory.begin() as db:
                lock_paid_finance_scope(db)
                run = db.scalar(select(PaidCallRun).where(PaidCallRun.id == run_id).with_for_update())
                if run is not None and run.state == "active":
                    run.state = "closed"
                    run.closed_at = datetime.now(UTC)
                    run.close_reason = "isolated B4 budget-exhaustion fixture complete"
        finally:
            operator_engine.dispose()
        with SessionFactory.begin() as db:
            lock_paid_finance_scope(db)
            active = db.scalar(select(ReleaseManifest).where(ReleaseManifest.id == release_id).with_for_update())
            if active is not None and active.state == "active":
                active.state = "retired"
            if previous_release_snapshot is not None:
                restored_payload = dict(previous_release_snapshot["manifest_payload"])
                restored_release = ReleaseManifest(
                    version_label=f"{previous_release_snapshot['version_label']}-restored-{nonce[:8]}",
                    parser_manifest_id=previous_release_snapshot["parser_manifest_id"],
                    parser_resource_manifest_id=previous_release_snapshot["parser_resource_manifest_id"],
                    model_manifest_id=previous_release_snapshot["model_manifest_id"],
                    rate_manifest_id=previous_release_snapshot["rate_manifest_id"],
                    retrieval_manifest_id=previous_release_snapshot["retrieval_manifest_id"],
                    rules_manifest_id=previous_release_snapshot["rules_manifest_id"],
                    prompt_manifest_id=previous_release_snapshot["prompt_manifest_id"],
                    routing_policy_manifest_id=previous_release_snapshot["routing_policy_manifest_id"],
                    batching_policy_manifest_id=previous_release_snapshot["batching_policy_manifest_id"],
                    policy_validation_report_id=previous_release_snapshot["policy_validation_report_id"],
                    grounding_registry_version=previous_release_snapshot["grounding_registry_version"],
                    lint_registry_version=previous_release_snapshot["lint_registry_version"],
                    manifest_payload=restored_payload,
                    config_hash=canonical_hash({"restored_config": previous_release_snapshot["config_hash"], "nonce": nonce}),
                    state="active", activated_by=previous_release_snapshot["activated_by"] or actor_id,
                    activated_at=datetime.now(UTC),
                )
                db.add(restored_release)


def test_provider_off_manual_review_annotation_feedback_release_and_withdrawal(_scenario, _budget_exhausted) -> None:
    scenario = _scenario
    clients, csrf = scenario["clients"], scenario["csrf"]
    source, element, criterion, evidence = scenario["source"], scenario["element"], scenario["criterion"], scenario["evidence"]
    route = f"/api/v1/submission-versions/{source.id}/reviews"
    created = clients["lecturer"].post(route, headers=_headers(csrf["lecturer"]), json={"analysis_run_id": None})
    assert created.status_code == 201, created.text
    review = created.json()
    assert review["analysis_run"] is None
    assert len(review["elements"]) == 1 and review["elements"][0]["decision"]["review_status"] == "pending"
    review_id = UUID(review["id"])
    assert clients["student"].get(f"/api/v1/reviews/{review_id}").status_code == 404
    assert clients["outsider"].get(f"/api/v1/reviews/{review_id}").status_code == 404

    incomplete = clients["lecturer"].post(f"/api/v1/reviews/{review_id}/complete",
        headers=_headers(csrf["lecturer"]), json={"expected_revision": review["revision"]})
    assert incomplete.status_code == 422 and incomplete.json()["error"]["code"] == "VALIDATION_ERROR"

    barrier = Barrier(2)
    def concurrent_decision(label: str):
        barrier.wait(timeout=10)
        return clients[label].put(f"/api/v1/reviews/{review_id}/element-decisions/{element.element_id}",
            headers=_headers(csrf[label]), json={"review_status": "reviewed", "resolution": "observed_no_issue",
                "applicability_override": None, "reason": None, "evidence_ids": [str(evidence.id)],
                "expected_revision": 1})

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(concurrent_decision, ("lecturer", "lecturer_b")))
    assert sorted(response.status_code for response in responses) == [200, 409]
    revision = next(response.json()["revision"] for response in responses if response.status_code == 200)

    manual = clients["lecturer"].post(f"/api/v1/reviews/{review_id}/manual-evidence",
        headers=_headers(csrf["lecturer"]), json={"file_id": str(scenario["asset"].id),
            "source_sha256": scenario["asset"].sha256,
            "anchor": {"format": "file", "source_sha256": scenario["asset"].sha256,
                       "locator_note": "Dosen memeriksa seluruh berkas sintetis."},
            "transcription": None, "original_evidence_id": None, "reason": None,
            "element_ids": [str(element.element_id)], "review_finding_id": None,
            "relationship_code": None, "role": None, "expected_revision": revision})
    assert manual.status_code == 201, manual.text
    manual_evidence_id = manual.json()["evidence_id"]
    revision = manual.json()["revision"]

    decision = clients["lecturer"].put(f"/api/v1/reviews/{review_id}/element-decisions/{element.element_id}",
        headers=_headers(csrf["lecturer"]), json={"review_status": "reviewed", "resolution": "confirmed_issue",
            "applicability_override": None, "reason": None, "evidence_ids": [manual_evidence_id],
            "expected_revision": revision})
    assert decision.status_code == 200, decision.text
    revision = decision.json()["revision"]
    finding = clients["lecturer"].post(f"/api/v1/reviews/{review_id}/manual-findings",
        headers=_headers(csrf["lecturer"]), json={"action": "accept", "primary_category": "missing_requirement",
            "primary_criterion_id": str(criterion.id), "claim": "Klaim pada tugas belum disertai bukti yang diminta.",
            "element_ids": [str(element.element_id)], "requirement_ids": [str(scenario["requirement"].id)],
            "criterion_ids": [], "evidence_ids": [manual_evidence_id], "coverage_attestation": None,
            "numeric": None, "reason": None, "expected_revision": revision})
    assert finding.status_code == 201, finding.text
    finding_id = finding.json()["finding"]["id"]
    revision = finding.json()["revision"]
    completed = clients["lecturer"].post(f"/api/v1/reviews/{review_id}/complete",
        headers=_headers(csrf["lecturer"]), json={"expected_revision": revision})
    assert completed.status_code == 200, completed.text
    complete_revision = completed.json()["revision"]

    draft = clients["lecturer"].post(f"/api/v1/reviews/{review_id}/feedback-drafts",
        headers=_headers(csrf["lecturer"]), json={"mode": "template", "body_markdown": None, "blocks": [],
            "included_review_finding_ids": [finding_id], "exclusion_reasons": {},
            "expected_revision": complete_revision})
    assert draft.status_code == 201, draft.text
    draft_id, draft_revision = draft.json()["id"], draft.json()["revision"]
    approved = clients["lecturer"].post(f"/api/v1/feedback-drafts/{draft_id}/approve",
        headers=_headers(csrf["lecturer"]), json={"expected_revision": draft_revision,
            "expected_review_revision": complete_revision})
    assert approved.status_code == 200 and approved.json()["state"] == "approved"

    annotation_id = manual.json()["annotation_id"]
    removed = clients["lecturer"].post(f"/api/v1/reviews/{review_id}/annotations/{annotation_id}/revisions",
        headers=_headers(csrf["lecturer"]), json={"kind": "span_selection", "operation": "remove",
            "expected_revision": complete_revision, "reason": "Lokasi ini tidak lagi dipakai untuk mendukung temuan.",
            "element_ids": [str(element.element_id)]})
    assert removed.status_code == 200, removed.text
    revision = removed.json()["review_revision"]
    stale_annotation = clients["lecturer"].post(
        f"/api/v1/reviews/{review_id}/annotations/{annotation_id}/revisions",
        headers=_headers(csrf["lecturer"]), json={"kind": "span_selection", "operation": "remove",
            "expected_revision": complete_revision, "reason": "Mutasi dengan revisi lama harus ditolak.",
            "element_ids": [str(element.element_id)]})
    assert stale_annotation.status_code == 409, stale_annotation.text
    draft_status = clients["lecturer"].get(f"/api/v1/feedback-drafts/{draft_id}")
    assert draft_status.status_code == 200 and draft_status.json()["stale"] is True
    blocked_complete = clients["lecturer"].post(f"/api/v1/reviews/{review_id}/complete",
        headers=_headers(csrf["lecturer"]), json={"expected_revision": revision})
    assert blocked_complete.status_code == 422
    with SessionFactory() as db:
        assert db.get(ReviewVersion, review_id).state == "draft"

    original_content = scenario["evidence"].content_original_json
    transcription = clients["lecturer"].post(f"/api/v1/reviews/{review_id}/manual-evidence",
        headers=_headers(csrf["lecturer"]), json={"file_id": str(scenario["asset"].id),
            "source_sha256": scenario["asset"].sha256,
            "anchor": evidence.anchor_json,
            "transcription": "Transkripsi koreksi dosen dari sumber original.",
            "original_evidence_id": str(evidence.id), "reason": "Membetulkan pembacaan teks pada halaman satu.",
            "element_ids": [str(element.element_id)], "review_finding_id": finding_id,
            "relationship_code": None, "role": None, "expected_revision": revision})
    assert transcription.status_code == 201, transcription.text
    transcription_evidence_id = transcription.json()["evidence_id"]
    revision = transcription.json()["revision"]
    with SessionFactory() as db:
        original_after = db.get(EvidenceItem, evidence.id)
        corrected_evidence = db.get(EvidenceItem, UUID(transcription_evidence_id))
        assert original_after.content_original_json == original_content
        assert corrected_evidence.type == "lecturer_transcription"
        assert corrected_evidence.content_original_json["text"] == "Transkripsi koreksi dosen dari sumber original."

    corrected = clients["lecturer"].patch(f"/api/v1/reviews/{review_id}/manual-findings/{finding_id}",
        headers=_headers(csrf["lecturer"]), json={"action": "edit", "primary_category": "missing_requirement",
            "primary_criterion_id": str(criterion.id), "claim": "Klaim belum didukung oleh sumber yang ditunjuk.",
            "element_ids": [str(element.element_id)], "requirement_ids": [str(scenario["requirement"].id)],
            "criterion_ids": [], "evidence_ids": [transcription_evidence_id], "coverage_attestation": None,
            "numeric": None, "reason": "Bukti manual sebelumnya dinonaktifkan.", "expected_revision": revision})
    assert corrected.status_code == 200, corrected.text
    revision = corrected.json()["revision"]
    completed_again = clients["lecturer"].post(f"/api/v1/reviews/{review_id}/complete",
        headers=_headers(csrf["lecturer"]), json={"expected_revision": revision})
    assert completed_again.status_code == 200, completed_again.text
    revision = completed_again.json()["revision"]
    stale_publish = clients["lecturer"].post(f"/api/v1/reviews/{review_id}/publish",
        headers=_headers(csrf["lecturer"]), json={"feedback_draft_id": draft_id,
            "expected_revision": revision, "expected_feedback_revision": approved.json()["revision"]})
    assert stale_publish.status_code == 409

    binding = clients["lecturer"].post(f"/api/v1/reviews/{review_id}/annotations",
        headers=_headers(csrf["lecturer"]), json={"kind": "evidence_binding", "evidence_item_id": transcription_evidence_id,
            "element_ids": [str(element.element_id)], "review_finding_id": finding_id,
            "relationship_code": None, "role": None, "reason": "Binding evidence parser untuk snapshot sintetis.",
            "expected_revision": revision})
    assert binding.status_code == 201, binding.text
    annotation_id = binding.json()["annotation_id"]
    completed_with_binding = clients["lecturer"].post(f"/api/v1/reviews/{review_id}/complete",
        headers=_headers(csrf["lecturer"]), json={"expected_revision": binding.json()["review_revision"]})
    assert completed_with_binding.status_code == 200, completed_with_binding.text
    revision = completed_with_binding.json()["revision"]

    new_draft = clients["lecturer"].post(f"/api/v1/reviews/{review_id}/feedback-drafts",
        headers=_headers(csrf["lecturer"]), json={"mode": "template", "body_markdown": None, "blocks": [],
            "included_review_finding_ids": [finding_id], "exclusion_reasons": {},
            "expected_revision": revision})
    assert new_draft.status_code == 201, new_draft.text
    new_approval = clients["lecturer"].post(f"/api/v1/feedback-drafts/{new_draft.json()['id']}/approve",
        headers=_headers(csrf["lecturer"]), json={"expected_revision": new_draft.json()["revision"],
            "expected_review_revision": revision})
    assert new_approval.status_code == 200, new_approval.text
    publish_key = str(uuid4())
    publish_body = {"feedback_draft_id": new_draft.json()["id"],
        "expected_revision": revision, "expected_feedback_revision": new_approval.json()["revision"]}
    published = clients["lecturer"].post(f"/api/v1/reviews/{review_id}/publish",
        headers=_headers(csrf["lecturer"], publish_key), json=publish_body)
    assert published.status_code == 201, published.text
    publication_id = published.json()["id"]
    replayed_publish = clients["lecturer"].post(f"/api/v1/reviews/{review_id}/publish",
        headers=_headers(csrf["lecturer"], publish_key), json=publish_body)
    assert replayed_publish.status_code == 201 and replayed_publish.json() == published.json()

    result = clients["student"].get(f"/api/v1/submissions/{scenario['submission'].id}/published-result")
    assert result.status_code == 200 and result.json()["status"] == "released"
    assert "manual" in result.json()["feedback_markdown"].lower() or "temuan" in result.json()["feedback_markdown"].lower()
    assert result.json()["confirmed_findings"][0]["claim"] == "Klaim belum didukung oleh sumber yang ditunjuk."
    assert "feedback_blocks" in result.json()
    assert "candidate_id" not in result.text and "prompt" not in result.text.lower()
    classmate_result = clients["classmate"].get(
        f"/api/v1/submissions/{scenario['submission'].id}/published-result")
    assert classmate_result.status_code == 200 and classmate_result.json() == result.json()
    assert "roster" not in classmate_result.text and "individual_user_id" not in classmate_result.text
    assert clients["outsider"].get(f"/api/v1/publications/{publication_id}").status_code == 404
    exported = clients["student"].get(f"/api/v1/publications/{publication_id}/export?format=json")
    assert exported.status_code == 200 and exported.headers["cache-control"] == "private, no-store"
    assert "prompt" not in exported.text.lower() and "cached_value" not in exported.text
    original_snapshot = exported.json()
    assert any(
        item["annotation"]["kind"] == "evidence_binding"
        for item in original_snapshot["confirmed_findings"][0]["evidence"]
        if item["annotation"] is not None
    )
    annotation_removed_after_publish = clients["lecturer"].post(
        f"/api/v1/reviews/{review_id}/annotations/{annotation_id}/revisions",
        headers=_headers(csrf["lecturer"]), json={"kind": "evidence_binding", "operation": "remove",
            "expected_revision": revision, "reason": "Binding berubah setelah publikasi; snapshot historis tetap dibekukan."})
    assert annotation_removed_after_publish.status_code == 200, annotation_removed_after_publish.text
    historical_export = clients["student"].get(f"/api/v1/publications/{publication_id}/export?format=json")
    assert historical_export.status_code == 200
    assert any(
        item["annotation"]["kind"] == "evidence_binding"
        for item in historical_export.json()["confirmed_findings"][0]["evidence"]
        if item["annotation"] is not None
    )
    assert historical_export.json() == original_snapshot
    markdown = clients["student"].get(f"/api/v1/publications/{publication_id}/export?format=md")
    assert markdown.status_code == 200 and "Klaim belum didukung" in markdown.text

    new_source = SubmissionVersion(submission_id=scenario["submission"].id, version_no=2,
        assignment_version_id=scenario["version"].id,
        manifest_hash=scenario["source"].manifest_hash, manifest_json=scenario["source"].manifest_json,
        finalized_by=scenario["student"].id, finalized_at=datetime.now(UTC), is_late=False)
    with SessionFactory.begin() as db:
        db.add(new_source)
        db.flush()
        db.add(SubmissionVersionFile(submission_version_id=new_source.id,
            submission_id=scenario["submission"].id, file_asset_id=scenario["asset"].id, display_order=0))
        db.add_all([
            SubmissionVersionMember(submission_version_id=new_source.id, user_id=member.id,
                display_name_snapshot=member.display_name, email_snapshot=member.normalized_email)
            for member in (scenario["student"], scenario["classmate"])
        ])
        db.get(Submission, scenario["submission"].id).active_final_version_id = new_source.id
    old_result_on_new_final = clients["student"].get(
        f"/api/v1/submissions/{scenario['submission'].id}/published-result")
    assert old_result_on_new_final.status_code == 200
    assert old_result_on_new_final.json()["status"] == "released"
    assert old_result_on_new_final.json()["source"]["submission_version"] == 1

    withdrawn = clients["lecturer"].post(f"/api/v1/publications/{publication_id}/withdraw",
        headers=_headers(csrf["lecturer"]), json={"reason": "Koreksi sumber sebelum hasil pengganti."})
    assert withdrawn.status_code == 200
    withdrawn_result = clients["student"].get(f"/api/v1/submissions/{scenario['submission'].id}/published-result")
    assert withdrawn_result.status_code == 200 and withdrawn_result.json()["status"] == "withdrawn"
    withdrawn_export = clients["student"].get(f"/api/v1/publications/{publication_id}/export?format=json")
    assert withdrawn_export.status_code == 409
    with SessionFactory() as db:
        assert db.get(Submission, scenario["submission"].id).latest_published_id == UUID(publication_id)
        assert db.get(Publication, UUID(publication_id)).snapshot_hash == published.json()["snapshot_hash"]

    replacement = clients["lecturer"].post(f"/api/v1/submission-versions/{new_source.id}/reviews",
        headers=_headers(csrf["lecturer"]), json={"analysis_run_id": None})
    assert replacement.status_code == 201, replacement.text
    replacement_review = replacement.json()
    replacement_decision = clients["lecturer"].put(
        f"/api/v1/reviews/{replacement_review['id']}/element-decisions/{element.element_id}",
        headers=_headers(csrf["lecturer"]), json={"review_status": "reviewed",
            "resolution": "observed_no_issue", "applicability_override": None, "reason": None,
            "evidence_ids": [], "expected_revision": replacement_review["revision"]})
    assert replacement_decision.status_code == 200, replacement_decision.text
    replacement_complete = clients["lecturer"].post(
        f"/api/v1/reviews/{replacement_review['id']}/complete", headers=_headers(csrf["lecturer"]),
        json={"expected_revision": replacement_decision.json()["revision"]})
    assert replacement_complete.status_code == 200, replacement_complete.text
    replacement_draft = clients["lecturer"].post(
        f"/api/v1/reviews/{replacement_review['id']}/feedback-drafts", headers=_headers(csrf["lecturer"]),
        json={"mode": "template", "body_markdown": None, "blocks": [],
            "included_review_finding_ids": [], "exclusion_reasons": {},
            "expected_revision": replacement_complete.json()["revision"]})
    assert replacement_draft.status_code == 201, replacement_draft.text
    replacement_approval = clients["lecturer"].post(
        f"/api/v1/feedback-drafts/{replacement_draft.json()['id']}/approve",
        headers=_headers(csrf["lecturer"]), json={"expected_revision": replacement_draft.json()["revision"],
            "expected_review_revision": replacement_complete.json()["revision"]})
    assert replacement_approval.status_code == 200, replacement_approval.text
    replacement_publish = clients["lecturer"].post(
        f"/api/v1/reviews/{replacement_review['id']}/publish", headers=_headers(csrf["lecturer"]),
        json={"feedback_draft_id": replacement_draft.json()["id"],
            "expected_revision": replacement_complete.json()["revision"],
            "expected_feedback_revision": replacement_approval.json()["revision"]})
    assert replacement_publish.status_code == 201, replacement_publish.text
    assert replacement_publish.json()["sequence_no"] == 2
    current_result = clients["student"].get(
        f"/api/v1/submissions/{scenario['submission'].id}/published-result")
    assert current_result.status_code == 200 and current_result.json()["status"] == "released"
    assert current_result.json()["source"]["submission_version"] == 2

    stale_review_response = clients["lecturer"].post(
        f"/api/v1/submission-versions/{new_source.id}/reviews", headers=_headers(csrf["lecturer"]),
        json={"analysis_run_id": None})
    assert stale_review_response.status_code == 201, stale_review_response.text
    stale_review = stale_review_response.json()
    stale_decision = clients["lecturer"].put(
        f"/api/v1/reviews/{stale_review['id']}/element-decisions/{element.element_id}",
        headers=_headers(csrf["lecturer"]), json={"review_status": "reviewed",
            "resolution": "observed_no_issue", "applicability_override": None, "reason": None,
            "evidence_ids": [], "expected_revision": stale_review["revision"]})
    assert stale_decision.status_code == 200, stale_decision.text
    stale_complete = clients["lecturer"].post(f"/api/v1/reviews/{stale_review['id']}/complete",
        headers=_headers(csrf["lecturer"]), json={"expected_revision": stale_decision.json()["revision"]})
    assert stale_complete.status_code == 200, stale_complete.text
    stale_draft = clients["lecturer"].post(f"/api/v1/reviews/{stale_review['id']}/feedback-drafts",
        headers=_headers(csrf["lecturer"]), json={"mode": "template", "body_markdown": None,
            "blocks": [], "included_review_finding_ids": [], "exclusion_reasons": {},
            "expected_revision": stale_complete.json()["revision"]})
    assert stale_draft.status_code == 201, stale_draft.text
    stale_approval = clients["lecturer"].post(
        f"/api/v1/feedback-drafts/{stale_draft.json()['id']}/approve",
        headers=_headers(csrf["lecturer"]), json={"expected_revision": stale_draft.json()["revision"],
            "expected_review_revision": stale_complete.json()["revision"]})
    assert stale_approval.status_code == 200, stale_approval.text
    future_source = SubmissionVersion(submission_id=scenario["submission"].id, version_no=3,
        assignment_version_id=scenario["version"].id,
        manifest_hash=scenario["source"].manifest_hash, manifest_json=scenario["source"].manifest_json,
        finalized_by=scenario["student"].id, finalized_at=datetime.now(UTC), is_late=False)
    with SessionFactory.begin() as db:
        db.add(future_source)
        db.flush()
        db.add(SubmissionVersionFile(submission_version_id=future_source.id,
            submission_id=scenario["submission"].id, file_asset_id=scenario["asset"].id, display_order=0))
        db.add_all([
            SubmissionVersionMember(submission_version_id=future_source.id, user_id=member.id,
                display_name_snapshot=member.display_name, email_snapshot=member.normalized_email)
            for member in (scenario["student"], scenario["classmate"])
        ])
        db.get(Submission, scenario["submission"].id).active_final_version_id = future_source.id
    stale_publish = clients["lecturer"].post(f"/api/v1/reviews/{stale_review['id']}/publish",
        headers=_headers(csrf["lecturer"]), json={"feedback_draft_id": stale_draft.json()["id"],
            "expected_revision": stale_complete.json()["revision"],
            "expected_feedback_revision": stale_approval.json()["revision"]})
    assert stale_publish.status_code == 409 and stale_publish.json()["error"]["code"] == "VERSION_CONFLICT"
    with SessionFactory() as db:
        assert db.scalar(select(Publication.id).where(
            Publication.review_version_id == UUID(stale_review["id"]))) is None
        assert db.get(Submission, scenario["submission"].id).latest_published_id == UUID(replacement_publish.json()["id"])
    current_result_after_future_final = clients["student"].get(
        f"/api/v1/submissions/{scenario['submission'].id}/published-result")
    assert current_result_after_future_final.status_code == 200
    assert current_result_after_future_final.json()["source"]["submission_version"] == 2

    with SessionFactory.begin() as db:
        assert db.get(Submission, scenario["submission"].id).latest_published_id == UUID(replacement_publish.json()["id"])
        revoked = db.scalar(select(Enrollment).where(Enrollment.course_id == scenario["course"].id,
            Enrollment.user_id == scenario["classmate"].id, Enrollment.role == "student"))
        assert revoked is not None
        revoked.active = False
    assert clients["classmate"].get(
        f"/api/v1/submissions/{scenario['submission'].id}/published-result").status_code == 404


def test_mock_candidate_snapshot_is_labelled_and_defer_is_a_terminal_limitation(_scenario) -> None:
    scenario = _scenario
    with SessionFactory.begin() as db:
        run = AnalysisRun(submission_version_id=scenario["source"].id,
            specification_version_id=scenario["version"].specification_version_id,
            release_manifest_id=None, config_fingerprint=canonical_hash("synthetic mock run"),
            status="completed", provider_mode="mock", data_origin="synthetic",
            limitations_json=[{"code": "synthetic_fixture", "message": "Kandidat hanya fixture."}])
        db.add(run)
        db.flush()
        candidate = FindingCandidate(analysis_run_id=run.id, primary_category="missing_requirement",
            primary_criterion_id=scenario["criterion"].id, obligation_key="evidence.support",
            issue_signature="synthetic-issue", dedup_key="synthetic-dedup",
            claim="Fixture sintetis: sumber terlihat tidak lengkap.", technical_validation="valid",
            semantic_support="insufficient", limitations_json=[])
        db.add(candidate)
        db.flush()
        db.add_all([FindingElementLink(finding_candidate_id=candidate.id, check_element_id=scenario["element"].id),
            FindingEvidenceLink(finding_candidate_id=candidate.id, evidence_item_id=scenario["evidence"].id)])
        candidate_id, run_id = candidate.id, run.id
    client, csrf = scenario["clients"]["lecturer"], scenario["csrf"]["lecturer"]
    created = client.post(f"/api/v1/submission-versions/{scenario['source'].id}/reviews",
        headers=_headers(csrf), json={"analysis_run_id": str(run_id)})
    assert created.status_code == 201, created.text
    review = created.json()
    assert review["analysis_run"]["provider_mode"] == "mock"
    assert review["analysis_run"]["data_origin"] == "synthetic"
    assert "SIMULASI SINTETIS" in review["analysis_run"]["label"]
    finding = review["findings"][0]
    assert finding["candidate_id"] == str(candidate_id) and finding["action"] == "pending"
    deferred = client.put(f"/api/v1/reviews/{review['id']}/candidate-decisions/{candidate_id}",
        headers=_headers(csrf), json={"action": "defer", "reason": "Kualitas sumber belum cukup untuk menyimpulkan.",
            "expected_revision": review["revision"]})
    assert deferred.status_code == 200 and deferred.json()["finding"]["action"] == "defer"
    revision = deferred.json()["revision"]
    unresolved = client.put(f"/api/v1/reviews/{review['id']}/element-decisions/{scenario['element'].element_id}",
        headers=_headers(csrf), json={"review_status": "reviewed", "resolution": "unable_to_conclude",
            "applicability_override": None, "reason": "Sumber belum cukup terbaca.", "evidence_ids": [],
            "expected_revision": revision})
    assert unresolved.status_code == 200, unresolved.text
    complete = client.post(f"/api/v1/reviews/{review['id']}/complete", headers=_headers(csrf),
        json={"expected_revision": unresolved.json()["revision"]})
    assert complete.status_code == 200, complete.text
    stored = client.get(f"/api/v1/reviews/{review['id']}")
    assert stored.json()["limitations"]


def test_candidate_accept_edit_reject_and_feedback_excludes_rejected_deferred_claims(_scenario) -> None:
    scenario = _scenario
    with SessionFactory.begin() as db:
        run = AnalysisRun(submission_version_id=scenario["source"].id,
            specification_version_id=scenario["version"].specification_version_id,
            release_manifest_id=None, config_fingerprint=canonical_hash("synthetic candidate consumer"),
            status="completed", provider_mode="mock", data_origin="synthetic",
            limitations_json=[{"code": "synthetic_fixture", "message": "Kandidat hanya fixture."}])
        db.add(run)
        db.flush()
        candidates = []
        for key, claim in (
            ("accepted", "Klaim kandidat yang dikonfirmasi dosen."),
            ("edited", "Klaim kandidat yang diedit lalu ditolak."),
            ("deferred", "Klaim kandidat yang ditunda."),
        ):
            candidate = FindingCandidate(analysis_run_id=run.id, primary_category="missing_requirement",
                primary_criterion_id=scenario["criterion"].id, obligation_key="evidence.support",
                issue_signature=f"synthetic-{key}", dedup_key=f"synthetic-{key}", claim=claim,
                technical_validation="valid", semantic_support="supported", limitations_json=[])
            db.add(candidate)
            db.flush()
            db.add_all([
                FindingElementLink(finding_candidate_id=candidate.id,
                    check_element_id=scenario["element"].id),
                FindingRequirementLink(finding_candidate_id=candidate.id,
                    requirement_id=scenario["requirement"].id),
                FindingEvidenceLink(finding_candidate_id=candidate.id,
                    evidence_item_id=scenario["evidence"].id),
            ])
            candidates.append((candidate.id, claim))
        run_id = run.id

    client, csrf = scenario["clients"]["lecturer"], scenario["csrf"]["lecturer"]
    created = client.post(f"/api/v1/submission-versions/{scenario['source'].id}/reviews",
        headers=_headers(csrf), json={"analysis_run_id": str(run_id)})
    assert created.status_code == 201, created.text
    review = created.json()
    accepted_id, accepted_claim = candidates[0]
    edited_id, edited_claim = candidates[1]
    deferred_id, deferred_claim = candidates[2]

    accepted = client.put(f"/api/v1/reviews/{review['id']}/candidate-decisions/{accepted_id}",
        headers=_headers(csrf), json={"action": "accept", "expected_revision": review["revision"]})
    assert accepted.status_code == 200 and accepted.json()["finding"]["action"] == "accept", accepted.text
    revision = accepted.json()["revision"]

    edited = client.put(f"/api/v1/reviews/{review['id']}/candidate-decisions/{edited_id}",
        headers=_headers(csrf), json={"action": "edit", "primary_category": "missing_requirement",
            "primary_criterion_id": str(scenario["criterion"].id),
            "claim": edited_claim, "element_ids": [str(scenario["element"].element_id)],
            "requirement_ids": [str(scenario["requirement"].id)], "criterion_ids": [],
            "evidence_ids": [str(scenario["evidence"].id)], "expected_revision": revision})
    assert edited.status_code == 200 and edited.json()["finding"]["action"] == "edit", edited.text
    revision = edited.json()["revision"]

    rejected = client.put(f"/api/v1/reviews/{review['id']}/candidate-decisions/{edited_id}",
        headers=_headers(csrf), json={"action": "reject", "reason": "Dosen menolak kandidat setelah verifikasi.",
            "expected_revision": revision})
    assert rejected.status_code == 200 and rejected.json()["finding"]["action"] == "reject", rejected.text
    revision = rejected.json()["revision"]

    deferred = client.put(f"/api/v1/reviews/{review['id']}/candidate-decisions/{deferred_id}",
        headers=_headers(csrf), json={"action": "defer", "reason": "Sumber terkait belum cukup terbaca.",
            "expected_revision": revision})
    assert deferred.status_code == 200 and deferred.json()["finding"]["action"] == "defer", deferred.text
    revision = deferred.json()["revision"]

    decision = client.put(f"/api/v1/reviews/{review['id']}/element-decisions/{scenario['element'].element_id}",
        headers=_headers(csrf), json={"review_status": "reviewed", "resolution": "confirmed_issue",
            "applicability_override": None, "reason": None,
            "evidence_ids": [str(scenario["evidence"].id)], "expected_revision": revision})
    assert decision.status_code == 200, decision.text
    completed = client.post(f"/api/v1/reviews/{review['id']}/complete", headers=_headers(csrf),
        json={"expected_revision": decision.json()["revision"]})
    assert completed.status_code == 200, completed.text

    accepted_finding_id = accepted.json()["finding"]["id"]
    draft = client.post(f"/api/v1/reviews/{review['id']}/feedback-drafts", headers=_headers(csrf),
        json={"mode": "template", "body_markdown": None, "blocks": [],
            "included_review_finding_ids": [accepted_finding_id], "exclusion_reasons": {},
            "expected_revision": completed.json()["revision"]})
    assert draft.status_code == 201, draft.text
    assert accepted_claim in draft.json()["body_markdown"]
    assert edited_claim not in draft.json()["body_markdown"]
    assert deferred_claim not in draft.json()["body_markdown"]


def test_manual_numeric_finding_uses_decimal_policy_and_rejects_unverified_literals(_scenario) -> None:
    scenario = _scenario
    client, csrf = scenario["clients"]["lecturer"], scenario["csrf"]["lecturer"]
    source, element, criterion, evidence = scenario["source"], scenario["element"], scenario["criterion"], scenario["evidence"]
    created = client.post(f"/api/v1/submission-versions/{source.id}/reviews", headers=_headers(csrf),
        json={"analysis_run_id": None})
    assert created.status_code == 201, created.text
    review_id, revision = created.json()["id"], created.json()["revision"]
    context = {"metric": "massa", "entity": "produk-A", "period": "2026",
        "definition": "massa bersih", "unit": "kg"}
    numeric = {"numeric_policy_version_id": str(scenario["numeric_policy"].id),
        "verification_note": "Dosen membandingkan angka pada original secara manual.",
        "operands": [
            {"evidence_id": str(evidence.id), "role": "left", "literal": "10,5",
             "source_quality": "lecturer_confirmed", "context": context},
            {"evidence_id": str(evidence.id), "role": "right", "literal": "12,0",
             "source_quality": "lecturer_confirmed", "context": context},
        ]}
    finding = client.post(f"/api/v1/reviews/{review_id}/manual-findings", headers=_headers(csrf), json={
        "action": "accept", "primary_category": "numerical_inconsistency",
        "primary_criterion_id": str(criterion.id), "claim": "Dua besaran terverifikasi berbeda di luar tolerance.",
        "element_ids": [str(element.element_id)], "requirement_ids": [], "criterion_ids": [],
        "evidence_ids": [str(evidence.id)], "coverage_attestation": None, "numeric": numeric,
        "reason": None, "expected_revision": revision})
    assert finding.status_code == 201, finding.text
    assert finding.json()["finding"]["numeric_check_id"]
    stored = client.get(f"/api/v1/reviews/{review_id}").json()["findings"][0]["numeric_check"]
    assert stored["comparability"] == "comparable" and stored["inconsistent"] is True
    assert stored["difference"] == "1.5" and stored["allowed_difference"] == "0"

    unverified_numeric = {**numeric, "operands": [
        {**numeric["operands"][0], "literal": "10", "source_quality": "native_literal"},
        {**numeric["operands"][1], "literal": "12", "source_quality": "native_literal"},
    ]}
    rejected = client.post(f"/api/v1/reviews/{review_id}/manual-findings", headers=_headers(csrf), json={
        "action": "accept", "primary_category": "numerical_inconsistency",
        "primary_criterion_id": str(criterion.id), "claim": "Tidak boleh memakai angka yang tidak ada di evidence.",
        "element_ids": [str(element.element_id)], "requirement_ids": [], "criterion_ids": [],
        "evidence_ids": [str(evidence.id)], "coverage_attestation": None, "numeric": unverified_numeric,
        "reason": None, "expected_revision": finding.json()["revision"]})
    assert rejected.status_code == 422 and rejected.json()["error"]["code"] == "VALIDATION_ERROR"


def test_resource_blocked_source_stays_available_for_manual_review(_scenario) -> None:
    scenario = _scenario
    with SessionFactory.begin() as db:
        artifact = db.get(ParseArtifact, scenario["artifact"].id)
        assert artifact is not None
        artifact.status = "blocked"
    client = scenario["clients"]["lecturer"]
    rendered = client.get(f"/api/v1/source-units/{scenario['unit'].id}/render")
    assert rendered.status_code == 200, rendered.text
    assert rendered.json()["file"]["sha256"] == scenario["asset"].sha256
    original = client.get(rendered.json()["file"]["content_url"])
    assert original.status_code == 200
    assert hashlib.sha256(original.content).hexdigest() == scenario["asset"].sha256


def test_worker_can_write_analysis_candidates_but_not_lecturer_decisions_or_publications() -> None:
    settings = get_settings()
    engine = create_engine(settings.worker_database_url)
    try:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT current_user")).scalar_one() == "align_worker"
            for table, privilege in (("analysis_runs", "INSERT"), ("finding_candidates", "INSERT"),
                                     ("review_versions", "INSERT"), ("review_element_decisions", "INSERT"),
                                     ("review_finding_decisions", "INSERT"), ("feedback_drafts", "INSERT"),
                                     ("publications", "INSERT"), ("publication_events", "INSERT")):
                expected = table in {"analysis_runs", "finding_candidates"}
                actual = connection.execute(text(
                    "SELECT has_table_privilege(current_user, :table_name, :privilege)"),
                    {"table_name": table, "privilege": privilege}).scalar_one()
                assert actual is expected, f"unexpected align_worker {privilege} privilege on {table}"
    finally:
        engine.dispose()


@pytest.mark.parametrize("_scenario", ["b7-retention"], indirect=True)
def test_b7_synthetic_retention_scrubs_source_and_preserves_authorized_tombstones(_scenario) -> None:
    from align_api.cli import purge_submission_retention
    from align_api.models import (
        FeedbackDraft,
        ManualEvidence,
        Publication,
        ReviewAnnotationRevision,
    )

    scenario = _scenario
    lecturer = scenario["clients"]["lecturer"]
    student = scenario["clients"]["student"]
    outsider = scenario["clients"]["outsider"]
    submission_id = scenario["submission"].id
    source = scenario["source"]
    asset = scenario["asset"]
    evidence = scenario["evidence"]
    element = scenario["element"]
    headers = _headers(scenario["csrf"]["lecturer"])

    # Synthetic vector rows prove purge removes derived embeddings as well as source text.
    chunk_id = uuid4()
    vector_literal = "[" + ",".join(["0"] * 1536) + "]"
    with SessionFactory.begin() as db:
        db.execute(text("""
            INSERT INTO retrieval_chunks (
              id,submission_version_id,submission_id,course_id,assignment_id,evidence_item_id,
              source_sha256,chunk_no,tokenizer_manifest,token_start,token_end,chunk_text,
              chunk_hash,anchor_json,source_order,structural_metadata_json
            ) VALUES (
              :id,:version_id,:submission_id,:course_id,:assignment_id,:evidence_id,
              :sha,0,'b7-retention-test',0,5,'private synthetic vector source text',
              :chunk_hash,CAST(:anchor AS jsonb),1,'{}'::jsonb
            )
        """), {"id": chunk_id, "version_id": source.id, "submission_id": submission_id,
            "course_id": scenario["course"].id, "assignment_id": scenario["assignment"].id,
            "evidence_id": evidence.id, "sha": asset.sha256,
            "chunk_hash": canonical_hash("private synthetic vector source text"),
            "anchor": json.dumps(evidence.anchor_json)})
        db.execute(text("""
            INSERT INTO evidence_embeddings (
              chunk_id,submission_version_id,course_id,assignment_id,owner_submission_id,
              embedding_manifest,dimension,content_hash,embedding
            ) VALUES (
              :chunk_id,:version_id,:course_id,:assignment_id,:submission_id,
              'b7-retention-test',1536,:content_hash,CAST(:embedding AS vector)
            )
        """), {"chunk_id": chunk_id, "version_id": source.id, "course_id": scenario["course"].id,
            "assignment_id": scenario["assignment"].id, "submission_id": submission_id,
            "content_hash": canonical_hash("private synthetic vector source text"),
            "embedding": vector_literal})

    created = lecturer.post(f"/api/v1/submission-versions/{source.id}/reviews", headers=headers,
        json={"analysis_run_id": None})
    assert created.status_code == 201, created.text
    review_id, review_revision = created.json()["id"], created.json()["revision"]
    annotation = lecturer.post(f"/api/v1/reviews/{review_id}/manual-evidence", headers=_headers(scenario["csrf"]["lecturer"]),
        json={"file_id": str(asset.id), "source_sha256": asset.sha256, "anchor": evidence.anchor_json,
            "transcription": "Private synthetic correction text.", "original_evidence_id": str(evidence.id),
            "reason": "Private synthetic annotation reason.", "element_ids": [str(element.element_id)],
            "review_finding_id": None, "relationship_code": None, "role": None,
            "expected_revision": review_revision})
    assert annotation.status_code == 201, annotation.text
    decision = lecturer.put(f"/api/v1/reviews/{review_id}/element-decisions/{element.element_id}",
        headers=_headers(scenario["csrf"]["lecturer"]), json={"review_status": "reviewed",
                "resolution": "confirmed_issue", "applicability_override": None, "reason": None,
            "evidence_ids": [annotation.json()["evidence_id"]], "expected_revision": annotation.json()["revision"]})
    assert decision.status_code == 200, decision.text
    finding = lecturer.post(f"/api/v1/reviews/{review_id}/manual-findings",
        headers=_headers(scenario["csrf"]["lecturer"]), json={"action": "accept",
            "primary_category": "missing_requirement", "primary_criterion_id": str(scenario["criterion"].id),
            "claim": "Private synthetic finding claim.", "element_ids": [str(element.element_id)],
            "requirement_ids": [str(scenario["requirement"].id)], "criterion_ids": [],
            "evidence_ids": [annotation.json()["evidence_id"]], "coverage_attestation": None,
            "numeric": None, "reason": None, "expected_revision": decision.json()["revision"]})
    assert finding.status_code == 201, finding.text
    completed = lecturer.post(f"/api/v1/reviews/{review_id}/complete",
        headers=_headers(scenario["csrf"]["lecturer"]), json={"expected_revision": finding.json()["revision"]})
    assert completed.status_code == 200, completed.text
    draft = lecturer.post(f"/api/v1/reviews/{review_id}/feedback-drafts",
        headers=_headers(scenario["csrf"]["lecturer"]), json={"mode": "manual",
            "body_markdown": "Private synthetic feedback text.",
            "blocks": [{"criterion_id": str(scenario["criterion"].id),
                "review_finding_ids": [finding.json()["finding"]["id"]],
                "evidence_ids": [annotation.json()["evidence_id"]],
                "message": "Private synthetic feedback text.", "next_action": "Periksa sumber asli.",
                "limitation": None}],
            "included_review_finding_ids": [finding.json()["finding"]["id"]], "exclusion_reasons": {},
            "expected_revision": completed.json()["revision"]})
    assert draft.status_code == 201, draft.text
    approved = lecturer.post(f"/api/v1/feedback-drafts/{draft.json()['id']}/approve",
        headers=_headers(scenario["csrf"]["lecturer"]), json={"expected_revision": draft.json()["revision"],
            "expected_review_revision": completed.json()["revision"]})
    assert approved.status_code == 200, approved.text
    published = lecturer.post(f"/api/v1/reviews/{review_id}/publish",
        headers=_headers(scenario["csrf"]["lecturer"]), json={"feedback_draft_id": draft.json()["id"],
            "expected_revision": completed.json()["revision"],
            "expected_feedback_revision": approved.json()["revision"]})
    assert published.status_code == 201, published.text

    # The default 365-day review retention is respected; the fixture itself is synthetic.
    with SessionFactory.begin() as db:
        review_row = db.get(ReviewVersion, UUID(review_id))
        assert review_row is not None
        review_row.created_at = datetime.now(UTC) - timedelta(days=400)

    try:
        purge_submission_retention(str(submission_id), "synthetic:b7-test", execute=True)
    except SystemExit as exc:
        database_error = getattr(exc.__cause__, "orig", exc.__cause__)
        raise AssertionError(str(database_error)) from exc
    with SessionFactory() as db:
        tombstone = db.scalar(select(RetentionTombstone).where(RetentionTombstone.file_asset_id == asset.id))
        stored_asset = db.get(FileAsset, asset.id)
        stored_evidence = db.get(EvidenceItem, evidence.id)
        stored_checkpoint = db.scalar(select(ParserCheckpoint).where(ParserCheckpoint.parse_artifact_id == scenario["artifact"].id))
        stored_draft = db.get(FeedbackDraft, UUID(draft.json()["id"]))
        stored_publication = db.get(Publication, UUID(published.json()["id"]))
        stored_manual = db.scalar(select(ManualEvidence).where(ManualEvidence.review_version_id == UUID(review_id)))
        stored_annotation = db.scalar(select(ReviewAnnotationRevision).where(
            ReviewAnnotationRevision.annotation_id == UUID(annotation.json()["annotation_id"])))
        remaining_embeddings = db.scalar(text("SELECT count(*) FROM evidence_embeddings WHERE submission_version_id=:id"),
            {"id": source.id})
        stored_chunk_text = db.scalar(text("SELECT chunk_text FROM retrieval_chunks WHERE id=:id"), {"id": chunk_id})
        assert tombstone is not None and stored_asset is not None and stored_asset.validation_state == "quarantined"
        assert stored_asset.original_name == "SOURCE_PURGED" and stored_asset.object_key == f"purged/{asset.id}"
        assert stored_evidence is not None and stored_evidence.content_original_json == {}
        assert stored_checkpoint is not None and stored_checkpoint.canonical_unit_locator_json == {"purged": True}
        assert stored_draft is not None and stored_draft.body_markdown == "SOURCE_PURGED"
        assert stored_draft.included_review_finding_ids_json == [] and stored_draft.exclusion_reasons_json == {}
        assert stored_publication is not None and stored_publication.snapshot_json == {"status": "purged", "source_purged": True}
        assert stored_manual is not None and stored_manual.transcription is None and stored_manual.source_anchor_json == {}
        assert stored_annotation is not None and stored_annotation.transcription is None and stored_annotation.anchor_json is None
        assert remaining_embeddings == 0 and stored_chunk_text == ""

    assert lecturer.get(f"/api/v1/files/{asset.id}/source-units").status_code == 410
    assert lecturer.get(f"/api/v1/files/{asset.id}/content").status_code == 410
    assert student.get(f"/api/v1/files/{asset.id}/content").status_code == 410
    assert student.get(f"/api/v1/submissions/{submission_id}/published-result").status_code == 410
    assert outsider.get(f"/api/v1/files/{asset.id}/source-units").status_code == 404
    assert outsider.get(f"/api/v1/files/{asset.id}/content").status_code == 404
    assert outsider.get(f"/api/v1/submissions/{submission_id}/published-result").status_code == 404


def test_review_queue_resume_filters_and_editable_neutral_template(_scenario) -> None:
    """Staff projections and resume are authorised; no provider or methodology shortcut."""
    scenario = _scenario
    client, csrf = scenario["clients"]["lecturer"], scenario["csrf"]["lecturer"]
    assignment_id, submission_id = scenario["assignment"].id, scenario["submission"].id
    queue_path = f"/api/v1/assignments/{assignment_id}/submissions"
    before = client.get(queue_path, params={"review_status": "not_started"})
    assert before.status_code == 200, before.text
    assert str(submission_id) in [item["id"] for item in before.json()["items"]]
    review = client.post(f"/api/v1/submission-versions/{scenario['source'].id}/reviews",
                        headers=_headers(csrf), json={"analysis_run_id": None})
    assert review.status_code == 201, review.text
    review = review.json()
    review_id = review["id"]
    drafts_path = f"/api/v1/reviews/{review_id}/feedback-drafts"
    assert client.get(drafts_path).json() == {"items": [], "next_cursor": None}
    for name in ("student", "classmate", "outsider"):
        assert scenario["clients"][name].get(drafts_path).status_code == 404
    student_status = scenario["clients"]["student"].get(f"/api/v1/submissions/{submission_id}/status")
    assert student_status.status_code == 200
    assert "staff_status" not in student_status.json()
    assert "course_id" not in student_status.json()
    active = client.get(queue_path, params={"review_status": "draft", "limit": 1})
    assert active.status_code == 200, active.text
    assert active.json()["items"][0]["staff_status"]["review_id"] == review_id
    assert active.json()["items"][0]["course_id"] == str(scenario["course"].id)
    assert client.get(queue_path, params={"q": "no-such-student"}).json()["items"] == []
    assert client.get(queue_path, params={"q": "b4 STUDENT"}).json()["items"]
    assert client.get(queue_path, params={"review_status": "complete"}).json()["items"] == []
    assert client.get(queue_path, params={"publication_status": "released"}).json()["items"] == []
    assert client.get(queue_path, params={"review_status": "invalid"}).status_code == 422
    decision = client.put(f"/api/v1/reviews/{review_id}/element-decisions/{scenario['element'].element_id}",
        headers=_headers(csrf), json={"review_status": "reviewed", "resolution": "observed_no_issue",
            "applicability_override": None, "reason": None, "evidence_ids": [str(scenario["evidence"].id)],
            "expected_revision": review["revision"]})
    assert decision.status_code == 200, decision.text
    completed = client.post(f"/api/v1/reviews/{review_id}/complete", headers=_headers(csrf),
                            json={"expected_revision": decision.json()["revision"]})
    assert completed.status_code == 200, completed.text
    version = completed.json()["revision"]
    for _ in range(2):
        created = client.post(drafts_path, headers=_headers(csrf), json={
            "mode": "template", "blocks": [], "included_review_finding_ids": [],
            "exclusion_reasons": {}, "expected_revision": version})
        assert created.status_code == 201, created.text
    page = client.get(drafts_path, params={"limit": 1}).json()
    draft = page["items"][0]
    assert page["next_cursor"] == 1
    older = client.get(drafts_path, params={"cursor": 1, "limit": 1}).json()
    assert len(older["items"]) == 1 and older["items"][0]["sequence_no"] < draft["sequence_no"]
    update = {"body_markdown": draft["body_markdown"], "blocks": [],
              "included_review_finding_ids": [], "exclusion_reasons": {}, "expected_revision": draft["revision"]}
    saved = client.put(f"/api/v1/feedback-drafts/{draft['id']}", headers=_headers(csrf), json=update)
    assert saved.status_code == 200, saved.text
    assert client.put(f"/api/v1/feedback-drafts/{draft['id']}", headers=_headers(csrf), json=update).status_code == 409
    invalid = {**update, "body_markdown": "Kesimpulan akademik tanpa temuan resmi.", "expected_revision": saved.json()["revision"]}
    assert client.put(f"/api/v1/feedback-drafts/{draft['id']}", headers=_headers(csrf), json=invalid).status_code == 422
    approved = client.post(f"/api/v1/feedback-drafts/{draft['id']}/approve", headers=_headers(csrf),
                           json={"expected_revision": saved.json()["revision"], "expected_review_revision": version})
    assert approved.status_code == 200, approved.text
    publication = client.post(f"/api/v1/reviews/{review_id}/publish", headers=_headers(csrf), json={
        "feedback_draft_id": draft["id"], "expected_revision": version,
        "expected_feedback_revision": approved.json()["revision"]})
    assert publication.status_code == 201, publication.text
    released = client.get(queue_path, params={"publication_status": "released", "review_status": "complete"})
    assert released.status_code == 200 and released.json()["items"]
    withdrawn = client.post(f"/api/v1/publications/{publication.json()['id']}/withdraw", headers=_headers(csrf),
                            json={"reason": "Koreksi sintetis untuk pengujian riwayat."})
    assert withdrawn.status_code == 200, withdrawn.text
    filtered = client.get(queue_path, params={"publication_status": "withdrawn"})
    assert filtered.status_code == 200 and filtered.json()["items"]
    assert client.get(queue_path, params={"publication_status": "released"}).json()["items"] == []
