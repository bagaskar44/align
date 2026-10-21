"""B5 executor integration against a real fixture source and isolated pgvector DB."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from pypdf import PdfReader
from sqlalchemy import select, text

from align_api.db import SessionFactory
from align_api.models import (
    AnalysisRun,
    Assignment,
    AssignmentGroup,
    CoverageInspection,
    CoverageRecord,
    EvidenceItem,
    FindingCandidate,
    Job,
    MethodManifest,
    OutboxEvent,
    ParseArtifact,
    ProviderCallRecord,
    ReleaseManifest,
    ReviewVersion,
    RoutingDecision,
    SourceUnit,
    Submission,
    SubmissionVersion,
)
from align_domain.assignment_contracts import canonical_hash
from align_worker import dispatcher
from align_worker.celery_app import celery_app
from align_worker.database import SessionFactory as WorkerSessionFactory
from align_worker.executor import _synthetic_embedding, _vector_rank
from align_worker.tasks import execute_analysis, execute_job, reap_expired_leases

pytestmark = pytest.mark.skipif(
    os.environ.get("ALIGN_RUN_INTEGRATION") != "true",
    reason="requires isolated Compose PostgreSQL, Redis and object storage",
)


def _headers(csrf: str) -> dict[str, str]:
    return {
        "Origin": "http://testserver",
        "X-CSRF-Token": csrf,
        "Idempotency-Key": str(uuid4()),
    }


def _register_verified_b5_release(actor_id: UUID) -> ReleaseManifest:
    root = Path(__file__).parents[2]
    manifest_path = root / "config" / "release.baseline.json"
    release_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifests: dict[str, dict[str, object]] = {}
    for kind, relative in release_payload["manifests"].items():
        manifests[kind] = json.loads(
            (manifest_path.parent / relative).read_text(encoding="utf-8")
        )
    now = datetime.now(UTC)
    with SessionFactory.begin() as db:
        methods: dict[str, MethodManifest] = {}
        for kind, payload in manifests.items():
            digest = canonical_hash(payload)
            method = db.scalar(select(MethodManifest).where(MethodManifest.sha256 == digest))
            if method is None:
                method = MethodManifest(
                    kind=kind,
                    version_label=str(payload.get("manifest_id", f"{kind}-b5-test")),
                    content_json=payload,
                    sha256=digest,
                    verified_at=now,
                )
                db.add(method)
                db.flush()
            methods[kind] = method
        release = ReleaseManifest(
            version_label=f"B5-test-{uuid4().hex[:8]}",
            parser_manifest_id=methods["parser"].id,
            model_manifest_id=methods["model"].id,
            retrieval_manifest_id=methods["retrieval"].id,
            rules_manifest_id=methods["rules"].id,
            prompt_manifest_id=methods["prompt"].id,
            routing_policy_manifest_id=methods["routing"].id,
            batching_policy_manifest_id=methods["batching"].id,
            grounding_registry_version=str(release_payload["grounding_registry_version"]),
            lint_registry_version=str(release_payload["lint_registry_version"]),
            manifest_payload=release_payload,
            config_hash=canonical_hash({"payload": release_payload, "test_actor": str(actor_id)}),
            state="verified",
            activated_by=actor_id,
            activated_at=now,
        )
        db.add(release)
        db.flush()
        db.expunge(release)
        return release


def test_mock_executor_keeps_inventory_scope_and_human_review_history(
    b4_scenario: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario = b4_scenario
    source_bytes = Path("tests/fixtures/b1/native-two-pages.pdf").read_bytes()
    actual_pages = [
        page.extract_text() or "" for page in PdfReader(BytesIO(source_bytes)).pages
    ]
    actual_text = "\n".join(actual_pages)
    assert "PDF digital sintetis B1" in actual_text
    with SessionFactory.begin() as db:
        source = db.get(SubmissionVersion, scenario["source"].id)
        artifact = db.get(ParseArtifact, scenario["artifact"].id)
        assert source and artifact
        for index, page_text in enumerate(actual_pages, start=1):
            actual_unit = SourceUnit(
                parse_artifact_id=artifact.id,
                locator_key=canonical_hash(f"b5-real-pdf-page-{artifact.id}-{index}"),
                unit_type="pdf_page",
                source_order=10 + index,
                anchor_json={"format": "pdf", "page": index},
                parse_status="parsed",
                limitations_json=[],
            )
            db.add(actual_unit)
            db.flush()
            db.add(EvidenceItem(
                source_unit_id=actual_unit.id,
                submission_version_id=source.id,
                context_version_id=None,
                assignment_version_id=None,
                file_asset_id=scenario["asset"].id,
                source_sha256=scenario["asset"].sha256,
                type="paragraph",
                anchor_json={"format": "pdf", "page": index},
                content_original_json={"text": page_text},
                content_normalized_json={"text": page_text},
                parse_status="parsed",
                limitations_json=[],
            ))
        unreadable = SourceUnit(
            parse_artifact_id=artifact.id,
            locator_key=canonical_hash(f"b5-unreadable-diagram-{artifact.id}"),
            unit_type="pdf_page",
            source_order=3,
            anchor_json={"format": "pdf", "page": 3, "region": "figure"},
            parse_status="unreadable",
            limitations_json=[{"code": "visual_region_unreadable"}],
        )
        db.add(unreadable)
        db.flush()
        unreadable_id = unreadable.id

    release = _register_verified_b5_release(scenario["lecturer"].id)
    lecturer = scenario["clients"]["lecturer"]
    csrf = scenario["csrf"]["lecturer"]

    old_review_response = lecturer.post(
        f"/api/v1/submission-versions/{scenario['source'].id}/reviews",
        headers=_headers(csrf),
        json={"analysis_run_id": None},
    )
    assert old_review_response.status_code == 201, old_review_response.text
    old_review = old_review_response.json()
    notes_response = lecturer.put(
        f"/api/v1/reviews/{old_review['id']}/notes",
        headers=_headers(csrf),
        json={
            "lecturer_notes": "Catatan manusia sebelum analisis B5.",
            "limitations": ["Periksa contoh pada halaman dua."],
            "expected_revision": old_review["revision"],
        },
    )
    assert notes_response.status_code == 200, notes_response.text
    human_revision = notes_response.json()["revision"]

    start = lecturer.post(
        f"/api/v1/submission-versions/{scenario['source'].id}/analysis-runs",
        headers=_headers(csrf),
        json={"release_manifest_id": str(release.id)},
    )
    assert start.status_code == 202, start.text
    run_id = start.json()["analysis_run_id"]

    # Kill a fresh executor process after it has started indexing. Its open
    # transaction must roll back, after which the ordinary Celery task replays
    # from the committed queued state.
    crash_script = r"""
import os, sys
from align_worker import executor
original = executor._stage
def crash_at_index(db, run_id, stage_name, *args, **kwargs):
    original(db, run_id, stage_name, *args, **kwargs)
    if stage_name == "index":
        os._exit(86)
executor._stage = crash_at_index
executor.execute_analysis_run(sys.argv[1])
os._exit(0)
"""
    crashed = subprocess.run(  # noqa: S603 -- controlled Python executable with a static test script
        [sys.executable, "-c", crash_script, run_id], check=False, timeout=120
    )
    assert crashed.returncode == 86
    with SessionFactory() as db:
        crashed_run = db.get(AnalysisRun, run_id)
        assert crashed_run is not None and crashed_run.status == "queued"
        crashed_job = db.get(Job, crashed_run.job_id)
        assert crashed_job is not None and crashed_job.status == "queued"
        assert db.scalar(text(
            "SELECT count(*) FROM analysis_run_stages WHERE analysis_run_id=:run"
        ), {"run": run_id}) == 0
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
    assert celery_app.conf.broker_transport_options["visibility_timeout"] > int(
        os.environ.get("JOB_MAX_ACTIVE_SECONDS", "600")
    )
    replayed = execute_analysis.apply(kwargs={"analysis_run_id": run_id}, throw=True).get()
    assert replayed["status"] in {"partial", "completed"}, replayed

    with WorkerSessionFactory() as db:
        current_role = db.scalar(text("SELECT current_user"))
        assert current_role == "align_worker"
        assert db.scalar(text("SELECT has_table_privilege(current_user,'requirements','SELECT')"))
        assert db.scalar(text("SELECT has_table_privilege(current_user,'criterion_set_versions','SELECT')"))
        assert db.scalar(text("SELECT has_table_privilege(current_user,'criterion_requirement_links','SELECT')"))
        assert not db.scalar(text("SELECT has_table_privilege(current_user,'review_versions','INSERT')"))
        assert not db.scalar(text("SELECT has_table_privilege(current_user,'review_finding_decisions','UPDATE')"))
        assert not db.scalar(text("SELECT has_table_privilege(current_user,'publications','INSERT')"))
        assert db.scalar(text("SELECT has_column_privilege(current_user,'feedback_drafts','generation_status','UPDATE')"))
        assert not db.scalar(text("SELECT has_column_privilege(current_user,'feedback_drafts','state','UPDATE')"))

    analysis_response = lecturer.get(f"/api/v1/analysis-runs/{run_id}")
    assert analysis_response.status_code == 200, analysis_response.text
    analysis = analysis_response.json()
    assert analysis["provider_mode"] == "mock"
    assert analysis["data_origin"] == "synthetic"
    assert analysis["label"] == "SIMULASI • data sintetis"
    assert len(analysis["elements"]) == 1
    assert analysis["elements"][0]["check_status"] == "unassessable"
    assert "human_judgment_required" in analysis["elements"][0]["reason_codes"]

    with SessionFactory() as db:
        coverage = db.scalar(
            select(CoverageRecord).where(
                CoverageRecord.analysis_run_id == run_id,
                CoverageRecord.check_element_id == scenario["element"].id,
            )
        )
        assert coverage is not None
        inspections = db.scalars(
            select(CoverageInspection).where(CoverageInspection.coverage_record_id == coverage.id)
        ).all()
        inventory_ids = {item["source_unit_id"] for item in coverage.inventory_json["units"]}
        assert str(unreadable_id) in inventory_ids
        unreadable_inspection = next(item for item in inspections if item.source_unit_id == unreadable_id)
        assert unreadable_inspection.state == "unresolved"
        assert coverage.exhaustive_for_negative is False
        assert db.scalar(select(FindingCandidate.id).where(FindingCandidate.analysis_run_id == run_id)) is None

        owner_submission = db.get(Submission, scenario["submission"].id)
        assignment = db.get(Assignment, scenario["assignment"].id)
        assert owner_submission and assignment
        foreign_group = AssignmentGroup(
            assignment_id=assignment.id,
            name=f"B5 vector scope {uuid4().hex[:8]}",
            active=True,
        )
        db.add(foreign_group)
        db.flush()
        foreign_submission = Submission(
            assignment_id=assignment.id,
            individual_user_id=None,
            group_id=foreign_group.id,
            active_final_version_id=None,
            draft_assignment_version_id=scenario["version"].id,
            draft_revision=1,
            revision=1,
        )
        db.add(foreign_submission)
        db.flush()
        row = db.execute(
            text("SELECT id::text FROM retrieval_chunks WHERE submission_version_id=:version LIMIT 1"),
            {"version": str(scenario["source"].id)},
        ).first()
        assert row is not None
        foreign_chunk_id = uuid4()
        db.execute(
            text("""
                INSERT INTO retrieval_chunks
                  (id,submission_version_id,submission_id,course_id,assignment_id,evidence_item_id,
                   source_sha256,chunk_no,tokenizer_manifest,token_start,token_end,chunk_text,chunk_hash,
                   anchor_json,source_order,structural_metadata_json)
                SELECT :id,c.submission_version_id,:owner,c.course_id,c.assignment_id,c.evidence_item_id,
                       c.source_sha256,99,c.tokenizer_manifest,c.token_start,c.token_end,c.chunk_text,
                       c.chunk_hash,c.anchor_json,999,c.structural_metadata_json
                FROM retrieval_chunks c WHERE c.id=:chunk
            """),
            {"id": str(foreign_chunk_id), "owner": str(foreign_submission.id), "chunk": row.id},
        )
        db.execute(
            text("""
                INSERT INTO evidence_embeddings
                  (chunk_id,submission_version_id,course_id,assignment_id,owner_submission_id,
                   embedding_manifest,dimension,content_hash,embedding)
                SELECT :chunk,submission_version_id,course_id,assignment_id,:owner,
                       'synthetic-hash-embedding-1536-v1',1536,repeat('1',64),embedding
                FROM evidence_embeddings WHERE chunk_id=:existing
                ORDER BY embedding_manifest LIMIT 1
            """),
            {"chunk": str(foreign_chunk_id), "owner": str(owner_submission.id), "existing": row.id},
        )
        db.commit()
    with WorkerSessionFactory() as worker_db:
        assert str(foreign_chunk_id) not in _vector_rank(
            worker_db,
            scope={
                "submission_version_id": scenario["source"].id,
                "course_id": scenario["course"].id,
                "assignment_id": scenario["assignment"].id,
                "submission_id": scenario["submission"].id,
            },
            query_vector=_synthetic_embedding(actual_text[:100]),
            embedding_manifest="synthetic-hash-embedding-1536-v1",
        )

    new_review_response = lecturer.post(
        f"/api/v1/submission-versions/{scenario['source'].id}/reviews",
        headers=_headers(csrf),
        json={"analysis_run_id": run_id},
    )
    assert new_review_response.status_code == 201, new_review_response.text
    assert new_review_response.json()["id"] != old_review["id"]
    assert new_review_response.json()["analysis_run"]["id"] == run_id
    new_review = new_review_response.json()
    manual = lecturer.post(
        f"/api/v1/reviews/{new_review['id']}/manual-evidence",
        headers=_headers(csrf),
        json={
            "file_id": str(scenario["asset"].id),
            "source_sha256": scenario["asset"].sha256,
            "anchor": {
                "format": "file",
                "source_sha256": scenario["asset"].sha256,
                "locator_note": "Dosen memeriksa sumber PDF sintetis untuk draft feedback.",
            },
            "transcription": None,
            "original_evidence_id": None,
            "reason": None,
            "element_ids": [str(scenario["element"].element_id)],
            "review_finding_id": None,
            "relationship_code": None,
            "role": None,
            "expected_revision": new_review["revision"],
        },
    )
    assert manual.status_code == 201, manual.text
    revision = manual.json()["revision"]
    decision = lecturer.put(
        f"/api/v1/reviews/{new_review['id']}/element-decisions/{scenario['element'].element_id}",
        headers=_headers(csrf),
        json={
            "review_status": "reviewed",
            "resolution": "confirmed_issue",
            "applicability_override": None,
            "reason": None,
            "evidence_ids": [manual.json()["evidence_id"]],
            "expected_revision": revision,
        },
    )
    assert decision.status_code == 200, decision.text
    finding = lecturer.post(
        f"/api/v1/reviews/{new_review['id']}/manual-findings",
        headers=_headers(csrf),
        json={
            "action": "accept",
            "primary_category": "missing_requirement",
            "primary_criterion_id": str(scenario["criterion"].id),
            "claim": "Klaim terkonfirmasi untuk uji draft feedback B5.",
            "element_ids": [str(scenario["element"].element_id)],
            "requirement_ids": [str(scenario["requirement"].id)],
            "criterion_ids": [],
            "evidence_ids": [manual.json()["evidence_id"]],
            "coverage_attestation": None,
            "numeric": None,
            "reason": None,
            "expected_revision": decision.json()["revision"],
        },
    )
    assert finding.status_code == 201, finding.text
    finding_id = finding.json()["finding"]["id"]
    complete = lecturer.post(
        f"/api/v1/reviews/{new_review['id']}/complete",
        headers=_headers(csrf),
        json={"expected_revision": finding.json()["revision"]},
    )
    assert complete.status_code == 200, complete.text
    feedback_body = {
        "mode": "model",
        "body_markdown": None,
        "blocks": [],
        "included_review_finding_ids": [finding_id],
        "exclusion_reasons": {},
        "expected_revision": complete.json()["revision"],
    }
    feedback_headers = _headers(csrf)
    feedback = lecturer.post(
        f"/api/v1/reviews/{new_review['id']}/feedback-drafts",
        headers=feedback_headers,
        json=feedback_body,
    )
    assert feedback.status_code == 202, feedback.text
    feedback_json = feedback.json()
    assert feedback_json["generation_mode"] == "model"
    assert feedback_json["generation_status"] == "queued"
    assert feedback_json["generation_job_id"]
    replay = lecturer.post(
        f"/api/v1/reviews/{new_review['id']}/feedback-drafts",
        headers=feedback_headers,
        json=feedback_body,
    )
    assert replay.status_code == 202 and replay.json()["id"] == feedback_json["id"]
    pending_edit = lecturer.put(
        f"/api/v1/feedback-drafts/{feedback_json['id']}",
        headers=_headers(csrf),
        json={
            "body_markdown": feedback_json["body_markdown"],
            "blocks": feedback_json["blocks"],
            "included_review_finding_ids": [finding_id],
            "exclusion_reasons": {},
            "expected_revision": feedback_json["revision"],
        },
    )
    assert pending_edit.status_code == 409
    pending_approval = lecturer.post(
        f"/api/v1/feedback-drafts/{feedback_json['id']}/approve",
        headers=_headers(csrf),
        json={
            "expected_revision": feedback_json["revision"],
            "expected_review_revision": complete.json()["revision"],
        },
    )
    assert pending_approval.status_code == 409
    with SessionFactory() as db:
        queued_job = db.get(Job, feedback_json["generation_job_id"])
        outbox = db.scalar(select(OutboxEvent).where(
            OutboxEvent.event_type == "job.execute",
            OutboxEvent.subject_id == feedback_json["generation_job_id"],
        ))
        assert queued_job is not None and queued_job.status == "queued"
        assert outbox is not None and outbox.payload_json["job_id"] == feedback_json["generation_job_id"]
    assert execute_job.apply(args=[feedback_json["generation_job_id"]], throw=True).get()["status"] == "ready_for_review"
    feedback_json = lecturer.get(f"/api/v1/feedback-drafts/{feedback_json['id']}").json()
    assert feedback_json["generation_status"] == "completed"
    assert "SIMULASI • data sintetis" in feedback_json["body_markdown"]
    assert feedback_json["included_review_finding_ids"] == [finding_id]
    with SessionFactory() as db:
        generation_job = db.get(Job, feedback_json["generation_job_id"])
        call = db.scalar(
            select(ProviderCallRecord).where(
                ProviderCallRecord.analysis_run_id == run_id,
                ProviderCallRecord.task == "draft_feedback",
            )
        )
        assert generation_job is not None and generation_job.status == "ready_for_review"
        assert generation_job.job_limits_json["max_calls"] == 2
        assert generation_job.job_limits_json["max_text_input_tokens"] == 12_000
        assert generation_job.job_limits_json["max_text_output_tokens"] == 3_000
        assert generation_job.job_limits_json["budget_usd"] == "0.03"
        assert call is not None and call.status == "completed" and call.provider_mode == "mock"
        route = db.get(RoutingDecision, call.routing_decision_id)
        assert route is not None and route.task == "draft_feedback"
        assert route.selected_role == "efficient"
        assert call.response_manifest_json["simulated"] is True
        assert call.response_manifest_json["fixture_id"] == "mock:draft_feedback:v1"
        assert call.usage_json["input_tokens"] <= 12_000
        assert call.usage_json["output_tokens"] <= 3_000
    with SessionFactory() as db:
        old = db.get(ReviewVersion, old_review["id"])
        assert old is not None
        assert old.lecturer_notes == "Catatan manusia sebelum analisis B5."
        assert old.revision == human_revision
        assert old.analysis_run_id is None

    # Simulate a worker dying after its lease was committed. The reaper must
    # requeue the exact analysis ID; the outbox must retry a broker outage and
    # then publish the expected Celery task/argument to the test Redis broker.
    second_start = lecturer.post(
        f"/api/v1/submission-versions/{scenario['source'].id}/analysis-runs",
        headers=_headers(csrf),
        json={"release_manifest_id": str(release.id)},
    )
    assert second_start.status_code == 202, second_start.text
    recovered_run_id = second_start.json()["analysis_run_id"]
    expired = datetime.now(UTC) - timedelta(minutes=5)
    with SessionFactory.begin() as db:
        recovered_run = db.get(AnalysisRun, recovered_run_id)
        assert recovered_run is not None and recovered_run.job_id is not None
        recovered_run.status = "running"
        recovered_job = db.get(Job, recovered_run.job_id)
        assert recovered_job is not None
        recovered_job.status = "running"
        recovered_job.lease_until = expired
    recovery = reap_expired_leases()
    assert recovery["jobs"] >= 1
    recovery_event_id: UUID
    with SessionFactory.begin() as db:
        recovery_event = db.scalar(
            select(OutboxEvent)
            .where(
                OutboxEvent.event_type == "job.analysis_run",
                OutboxEvent.subject_id == UUID(recovered_run_id),
            )
            .order_by(OutboxEvent.created_at.desc(), OutboxEvent.id.desc())
            .with_for_update()
        )
        assert recovery_event is not None
        for pending in db.scalars(
            select(OutboxEvent).where(OutboxEvent.delivered_at.is_(None)).with_for_update()
        ).all():
            pending.next_attempt_at = datetime.now(UTC) + timedelta(hours=1)
        recovery_event.next_attempt_at = datetime.now(UTC)
        recovery_event_id = recovery_event.id

    def broker_down(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("synthetic broker outage")

    with monkeypatch.context() as patcher:
        patcher.setattr(celery_app, "send_task", broker_down)
        assert dispatcher.dispatch_once() == 0
    with SessionFactory.begin() as db:
        recovery_event = db.get(OutboxEvent, recovery_event_id)
        assert recovery_event is not None and recovery_event.delivered_at is None
        assert recovery_event.attempt_count >= 1
        recovery_event.next_attempt_at = datetime.now(UTC)
    assert dispatcher.dispatch_once() == 1
    with SessionFactory() as db:
        delivered = db.get(OutboxEvent, recovery_event_id)
        assert delivered is not None and delivered.delivered_at is not None
        assert delivered.payload_json == {"analysis_run_id": recovered_run_id}
    replayed_recovery = execute_analysis.apply(
        kwargs={"analysis_run_id": recovered_run_id}, throw=True
    ).get()
    assert replayed_recovery["status"] in {"partial", "completed"}, replayed_recovery

