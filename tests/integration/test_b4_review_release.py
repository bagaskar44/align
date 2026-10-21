"""B4 provider-off review, append-only evidence and released student views."""

from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text

from align_api.config import get_settings
from align_api.db import SessionFactory
from align_api.main import create_app
from align_api.models import (
    AnalysisRun,
    Assignment,
    AssignmentGroup,
    AssignmentVersion,
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
    NumericPolicyVersion,
    ParseArtifact,
    Publication,
    Requirement,
    ReviewVersion,
    SourceUnit,
    SpecificationVersion,
    Submission,
    SubmissionVersion,
    SubmissionVersionFile,
    SubmissionVersionMember,
    User,
)
from align_api.security import hash_password
from align_api.storage import storage_client
from align_domain.assignment_contracts import canonical_hash

pytestmark = pytest.mark.skipif(
    os.environ.get("ALIGN_RUN_INTEGRATION") != "true",
    reason="requires isolated Compose PostgreSQL, Redis and object storage",
)
PASSWORD = "Synthetic-B4-review-integration"  # noqa: S105 -- synthetic fixture only


def _headers(csrf: str, key: str | None = None) -> dict[str, str]:
    return {"Origin": "http://testserver", "X-CSRF-Token": csrf, "Idempotency-Key": key or str(uuid4())}


@pytest.fixture
def _scenario():
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
            "required_context_fields": ["metric", "period", "unit"], "number_locale": "id-ID",
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
            validation_state="validated", preflight_json={"parser_profile": "native_pdf_v1", "pages": 2})
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


def test_provider_off_manual_review_annotation_feedback_release_and_withdrawal(_scenario) -> None:
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
    context = {"metric": "massa", "period": "2026", "unit": "kg"}
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
