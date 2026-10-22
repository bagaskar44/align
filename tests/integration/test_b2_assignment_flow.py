"""B2 assignment/package/specification flow against isolated PostgreSQL and Redis."""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from align_api.config import get_settings
from align_api.db import SessionFactory
from align_api.main import create_app
from align_api.models import (
    ContextVersion,
    Course,
    Enrollment,
    EvidenceItem,
    FileAsset,
    NormativeFileLink,
    ParseArtifact,
    SourceUnit,
    SpecificationVersion,
    User,
)
from align_api.security import hash_password
from align_domain.assignment_contracts import TaskFact, canonical_hash
from align_domain.executor import resolve_applicability, verified_task_fact_values

pytestmark = pytest.mark.skipif(
    os.environ.get("ALIGN_RUN_INTEGRATION") != "true",
    reason="requires isolated Compose PostgreSQL and Redis",
)
PASSWORD = "Synthetic-B2-integration-password-2026"  # noqa: S105 -- synthetic fixture only


@pytest.fixture
def b2_scenario():
    settings = get_settings().model_copy(
        update={
            "app_origin": "http://testserver",
            "redis_url": "redis://redis:6379/0",
            "s3_endpoint": "http://object-storage:9090",
            "redis_queue_prefix": f"b2-test-{uuid4().hex}",
        }
    )
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    suffix = uuid4().hex
    lecturer, lecturer_b, student_a, student_b, outsider = [
        User(
            normalized_email=f"b2-{role}-{suffix}@example.test",
            display_name=f"B2 {role}",
            password_hash=hash_password(PASSWORD),
            global_role="member",
            active=True,
            must_change_password=False,
        )
        for role in ("lecturer", "lecturer-b", "student-a", "student-b", "outsider")
    ]
    course = Course(code=f"B2-{suffix[:12]}", title="Synthetic B2 course", active=True)
    outsider_course = Course(
        code=f"B2-X-{suffix[:10]}", title="Synthetic outside course", active=True
    )
    with SessionFactory.begin() as db:
        db.add_all([lecturer, lecturer_b, student_a, student_b, outsider, course, outsider_course])
        db.flush()
        now = datetime.now(UTC)
        context = ContextVersion(
            course_id=course.id,
            version_no=1,
            state="approved",
            fields_json={
                "context_description": {"value": "Context sintetis untuk B2."},
                "outcomes_status": "not_provided",
                "learning_outcomes": [],
            },
            instruction_sources_json=[],
            content_hash="c" * 64,
            approved_by=lecturer.id,
            approved_at=now,
            revision=1,
        )
        db.add(context)
        db.flush()
        course.approved_context_version_id = context.id
        db.add_all(
            [
                Enrollment(course_id=course.id, user_id=lecturer.id, role="lecturer", active=True),
                Enrollment(course_id=course.id, user_id=lecturer_b.id, role="lecturer", active=True),
                Enrollment(course_id=course.id, user_id=student_a.id, role="student", active=True),
                Enrollment(course_id=course.id, user_id=student_b.id, role="student", active=True),
                Enrollment(
                    course_id=outsider_course.id,
                    user_id=outsider.id,
                    role="student",
                    active=True,
                ),
            ]
        )
    with ExitStack() as stack:
        clients = {
            "lecturer": stack.enter_context(TestClient(app)),
            "lecturer_b": stack.enter_context(TestClient(app)),
            "student_a": stack.enter_context(TestClient(app)),
            "student_b": stack.enter_context(TestClient(app)),
            "outsider": stack.enter_context(TestClient(app)),
        }
        tokens = {}
        for role, user in zip(
            ("lecturer", "lecturer_b", "student_a", "student_b", "outsider"),
            (lecturer, lecturer_b, student_a, student_b, outsider),
            strict=True,
        ):
            response = clients[role].post(
                "/api/v1/auth/login",
                headers={"Origin": settings.app_origin},
                json={"email": user.normalized_email, "password": PASSWORD},
            )
            assert response.status_code == 200, response.text
            tokens[role] = response.json()["csrf_token"]
        yield {
            "app": app,
            "clients": clients,
            "tokens": tokens,
            "course": course,
            "context": context,
            "users": {"lecturer": lecturer, "lecturer_b": lecturer_b, "student_a": student_a, "student_b": student_b},
            "settings": settings,
        }
    with SessionFactory.begin() as db:
        for user in (lecturer, lecturer_b, student_a, student_b, outsider):
            current = db.get(User, user.id)
            if current is not None:
                current.active = False
        for item in (course, outsider_course):
            current_course = db.get(Course, item.id)
            if current_course is not None:
                current_course.active = False


def headers(token: str, key: str | None = None) -> dict[str, str]:
    return {
        "Origin": "http://testserver",
        "X-CSRF-Token": token,
        "Idempotency-Key": key or str(uuid4()),
    }


def create_assignment_draft(client: TestClient, token: str, course: Course, context: ContextVersion, title: str, instruction: str) -> tuple[dict[str, object], dict[str, object]]:
    created = client.post(
        "/api/v1/assignments",
        headers=headers(token),
        json={"course_id": str(course.id), "title": title, "context_version_id": str(context.id)},
    )
    assert created.status_code == 201, created.text
    assignment = created.json()
    version = assignment["version"]
    now = datetime.now(UTC)
    updated = client.put(
        f"/api/v1/assignment-versions/{version['id']}",
        headers=headers(token),
        json={
            "expected_revision": version["revision"],
            "instruction_original": instruction,
            "instruction_summary": None,
            "mode": "individual",
            "opens_at": (now - timedelta(hours=1)).isoformat(),
            "due_at": (now + timedelta(days=7)).isoformat(),
            "late_policy": "accept_with_label",
        },
    )
    assert updated.status_code == 200, updated.text
    return assignment, updated.json()


def specification_envelope(elements: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": 2,
        "elements": elements,
        "operator_registry_version": "operators-v1",
        "predicate_registry_version": "predicates-v1",
        "grounding_registry_version": "grounding-v1",
        "lint_registry_version": "lint-v1",
        "task_facts": [],
        "limits": {
            "draft_max_cost_usd": "0.05",
            "max_text_calls": 3,
            "max_vision_calls": 2,
            "max_input_tokens": 20000,
            "max_output_tokens": 4000,
        },
    }


def policy_payload() -> dict[str, object]:
    return {
        "metric_definition": "Besaran sintetis yang sama dalam konteks yang sama.",
        "required_context_fields": ["metric", "entity", "definition", "unit"],
        "number_locale": "id-ID",
        "canonical_unit": "ratio",
        "allowed_conversions": [],
        "comparison_mode": "pairwise",
        "absolute_tolerance": "0",
        "relative_tolerance": "0",
        "rounding": {"mode": "none", "quantum": None},
        "operand_roles": {"left": "angka kiri", "right": "angka kanan"},
    }


def element_template(
    *,
    element_id: str,
    element_code: str,
    criterion_id: str,
    obligation_key: str,
    target_kind: str,
    requirement_ids: list[str],
    derivation_refs: list[dict[str, object]],
    grounding: dict[str, object],
    operators: list[str],
    numeric_policy_id: str | None = None,
    question: str = "Apakah bukti menjawab kewajiban resmi?",
) -> dict[str, object]:
    return {
        "element_id": element_id,
        "element_code": element_code,
        "criterion_id": criterion_id,
        "requirement_ids": requirement_ids,
        "obligation_key": obligation_key,
        "target_kind": target_kind,
        "decision_question": question,
        "derivation_refs": derivation_refs,
        "expected_evidence": "Bukti relevan yang menjawab tuntutan resmi.",
        "grounding_contract": grounding,
        "accepted_evidence_types": ["paragraph", "table", "spreadsheet_cell"],
        "relationships": [],
        "applicability": {"mode": "always"},
        "source_hints": ["istilah atau label yang relevan dengan tuntutan"],
        "operators": operators,
        "dependencies": [],
        "coverage_policy": {
            "positive_strategy": "sufficient_support",
            "negative_strategy": "exhaustive_compatible_source_units",
            "inspect_embedded_visuals": True,
            "unresolved_compatible_unit_blocks_negative": True,
            "preserve_table_context": True,
        },
        "abstention_conditions": ["unreadable_relevant_region"],
        "numeric_policy_version_id": numeric_policy_id,
        "human_judgment_boundary": "Keputusan akademik di luar pemeriksaan ini tetap dilakukan dosen.",
    }


def specification_payload(
    *,
    instruction_id: str,
    criterion_id: str,
    requirement_id: str,
    obligation_key: str,
    policy_id: str,
    instruction_length: int,
    include_requirement: bool,
) -> dict[str, object]:
    numeric = element_template(
        element_id=str(uuid4()),
        element_code="EL-NUMERIC-CHECK",
        criterion_id=criterion_id,
        obligation_key="numeric.synthetic.comparison",
        target_kind="numeric",
        requirement_ids=[],
        derivation_refs=[
            {
                "source_kind": "instruction",
                "source_id": instruction_id,
                "span": {"start": 0, "end": instruction_length},
            }
        ],
        grounding={
            "schema_version": 1,
            "mode": "comparable_numeric",
            "absence_support": None,
        },
        operators=[
            "locate_evidence",
            "expand_context",
            "inspect_source_coverage",
            "establish_comparability",
            "compare_numbers",
            "request_human_review",
        ],
        numeric_policy_id=policy_id,
        question="Apakah dua observasi yang sepadan melaporkan besaran yang sama?",
    )
    elements = [numeric]
    if include_requirement:
        elements.insert(
            0,
            element_template(
                element_id=str(uuid4()),
                element_code="EL-REQ-MISSING-VALUE",
                criterion_id=criterion_id,
                obligation_key=obligation_key,
                target_kind="requirement",
                requirement_ids=[requirement_id],
                derivation_refs=[
                    {"source_kind": "requirement", "source_id": requirement_id, "span": None}
                ],
                grounding={"schema_version": 1, "mode": "human_only", "absence_support": None},
                operators=["locate_evidence", "request_human_review"],
                question="Apakah bukti menjawab requirement jika kondisi yang disebutkan berlaku?",
            ),
        )
    return {
        "schema_version": 2,
        "elements": elements,
        "operator_registry_version": "operators-v1",
        "predicate_registry_version": "predicates-v1",
        "grounding_registry_version": "grounding-v1",
        "lint_registry_version": "lint-v1",
        "task_facts": [],
        "limits": {
            "draft_max_cost_usd": "0.05",
            "max_text_calls": 3,
            "max_vision_calls": 2,
            "max_input_tokens": 20000,
            "max_output_tokens": 4000,
        },
    }


def test_assignment_spec_publish_group_and_student_visibility(b2_scenario):
    lecturer = b2_scenario["clients"]["lecturer"]
    csrf = b2_scenario["tokens"]["lecturer"]
    course = b2_scenario["course"]
    context = b2_scenario["context"]
    now = datetime.now(UTC)
    instruction = "Tuliskan metode 😀 dan jelaskan missing value."

    create_key = str(uuid4())
    create = lecturer.post(
        "/api/v1/assignments",
        headers=headers(csrf, create_key),
        json={
            "course_id": str(course.id),
            "title": "Tugas sintetis B2",
            "context_version_id": str(context.id),
        },
    )
    assert create.status_code == 201, create.text
    assignment = create.json()
    assert (
        lecturer.post(
            "/api/v1/assignments",
            headers=headers(csrf, create_key),
            json={
                "course_id": str(course.id),
                "title": "Tugas sintetis B2",
                "context_version_id": str(context.id),
            },
        ).json()["id"]
        == assignment["id"]
    )
    version = assignment["version"]
    assert version["criterion_set"] is not None
    version_id = version["id"]
    opens_at = (now - timedelta(hours=1)).isoformat()
    due_at = (now + timedelta(days=7)).isoformat()
    version_body = {
        "expected_revision": version["revision"],
        "instruction_original": instruction,
        "instruction_summary": "Ringkasan sintetis.",
        "mode": "group",
        "opens_at": opens_at,
        "due_at": due_at,
        "late_policy": "accept_with_label",
    }
    update_key = str(uuid4())
    update = lecturer.put(
        f"/api/v1/assignment-versions/{version_id}",
        headers=headers(csrf, update_key),
        json=version_body,
    )
    assert update.status_code == 200, update.text
    replay = lecturer.put(
        f"/api/v1/assignment-versions/{version_id}",
        headers=headers(csrf, update_key),
        json=version_body,
    )
    assert replay.status_code == 200 and replay.json()["revision"] == update.json()["revision"]
    generated = lecturer.post(
        f"/api/v1/assignment-versions/{version_id}/criteria-drafts",
        headers=headers(csrf),
        json={"rubric_file_ids": [], "generate_from_instruction": True},
    )
    assert generated.status_code == 201, generated.text
    generation = generated.json()["generation"]
    assert generation["status"] == "extractive_mock"
    assert generation["provider_calls"] == 0 and generation["cost_usd"] == "0"
    assert generated.json()["criteria"] == []
    assert generation["proposals"][0]["quote"] == instruction
    assert generation["proposals"][0]["mandatory"] is None
    assert generation["proposals"][0]["source_span"] == {
        "start": 0,
        "end": len(instruction),
    }
    criterion_set = update.json()["criterion_set"]
    phrase = "jelaskan missing value"
    start = instruction.index(phrase)
    save_criteria = lecturer.put(
        f"/api/v1/criterion-set-versions/{criterion_set['id']}",
        headers=headers(csrf),
        json={
            "expected_revision": criterion_set["revision"],
            "criteria": [
                {
                    "code": "C1",
                    "title": "Penjelasan missing value",
                    "descriptor": "Menjelaskan penanganan missing value secara manual.",
                    "display_order": 0,
                    "outcome_ids": [],
                    "requirements": [
                        {
                            "code": "R1",
                            "text": phrase,
                            "mandatory": True,
                            "obligation_key": "missing_values.explanation",
                            "normative_source_refs": [
                                {
                                    "source_kind": "instruction",
                                    "source_id": version_id,
                                    "span": {"start": start, "end": start + len(phrase)},
                                }
                            ],
                        }
                    ],
                }
            ],
        },
    )
    assert save_criteria.status_code == 200, save_criteria.text
    criterion = save_criteria.json()["criteria"][0]
    requirement = criterion["requirements"][0]
    assert requirement["normative_source_refs"][0]["quote"] == phrase
    assert requirement["normative_source_refs"][0]["source_hash"]

    policy = lecturer.post(
        f"/api/v1/assignments/{assignment['id']}/numeric-policy-drafts",
        headers=headers(csrf),
        json={"policy_key": "synthetic-pairwise", "payload": policy_payload()},
    )
    assert policy.status_code == 201, policy.text
    invalid_policy = policy_payload()
    invalid_policy["relative_tolerance"] = "1.5"
    assert (
        lecturer.post(
            f"/api/v1/assignments/{assignment['id']}/numeric-policy-drafts",
            headers=headers(csrf),
            json={"policy_key": "invalid-policy", "payload": invalid_policy},
        ).status_code
        == 422
    )
    policy = policy.json()
    approved_policy = lecturer.post(
        f"/api/v1/numeric-policy-versions/{policy['id']}/approve",
        headers=headers(csrf),
        json={"expected_revision": policy["revision"]},
    )
    assert approved_policy.status_code == 200, approved_policy.text

    current_version = lecturer.get(f"/api/v1/assignment-versions/{version_id}").json()
    package_approval = lecturer.post(
        f"/api/v1/assignment-versions/{version_id}/approve-package",
        headers=headers(csrf),
        json={
            "criterion_set_version_id": criterion_set["id"],
            "expected_revision": current_version["revision"],
        },
    )
    assert package_approval.status_code == 200, package_approval.text
    spec_draft = lecturer.post(
        f"/api/v1/assignment-versions/{version_id}/specification-drafts",
        headers=headers(csrf),
        json={"criterion_set_version_id": criterion_set["id"]},
    )
    assert spec_draft.status_code == 201, spec_draft.text
    specification = spec_draft.json()
    assert specification["state"] == "draft"
    assert specification["content_hash"]
    common = {
        "instruction_id": version_id,
        "criterion_id": criterion["id"],
        "requirement_id": requirement["id"],
        "obligation_key": requirement["obligation_key"],
        "policy_id": policy["id"],
        "instruction_length": len(instruction),
    }
    invalid = specification_payload(**common, include_requirement=True)
    invalid["elements"][0]["operators"] = ["run_shell"]
    invalid_response = lecturer.put(
        f"/api/v1/specification-versions/{specification['id']}",
        headers=headers(csrf),
        json={"expected_revision": specification["revision"], "specification": invalid},
    )
    assert invalid_response.status_code == 422
    assert (
        lecturer.get(f"/api/v1/specification-versions/{specification['id']}").json()["revision"]
        == 1
    )

    incomplete = specification_payload(**common, include_requirement=False)
    first_report = lecturer.put(
        f"/api/v1/specification-versions/{specification['id']}",
        headers=headers(csrf),
        json={"expected_revision": 1, "specification": incomplete},
    )
    assert first_report.status_code == 200, first_report.text
    assert first_report.json()["effective_status"] == "failed"
    blocking = lecturer.post(
        f"/api/v1/spec-lint-reports/{first_report.json()['lint_report_id']}/resolutions",
        headers=headers(csrf),
        json={
            "issue_code": f"unsupported_requirement:{requirement['id']}",
            "resolution": "acknowledged_faithful",
            "reason": "Tidak boleh menimpa requirement yang belum didukung.",
            "expected_spec_revision": 2,
            "expected_report_revision": 1,
        },
    )
    assert blocking.status_code == 422

    complete = specification_payload(**common, include_requirement=True)
    complete["task_facts"] = [
        {"key": "submission_variant", "value_type": "string", "allowed_values": ["original", "alternate"]}
    ]
    complete["elements"][0]["applicability"] = {
        "mode": "fact_rule",
        "rule": {"op": "eq", "fact": "task.submission_variant", "value": "original"},
    }
    second_report = lecturer.put(
        f"/api/v1/specification-versions/{specification['id']}",
        headers=headers(csrf),
        json={"expected_revision": 2, "specification": complete},
    )
    assert second_report.status_code == 200, second_report.text
    assert second_report.json()["effective_status"] == "needs_review"
    stale_resolution = lecturer.post(
        f"/api/v1/spec-lint-reports/{first_report.json()['lint_report_id']}/resolutions",
        headers=headers(csrf),
        json={
            "issue_code": "EL-REQ-MISSING-VALUE:condition_fidelity_review",
            "resolution": "acknowledged_human_boundary",
            "reason": "Resolution pada report lama.",
            "expected_spec_revision": 3,
            "expected_report_revision": 1,
        },
    )
    assert stale_resolution.status_code == 409

    current_report_id = second_report.json()["lint_report_id"]
    issue_code = "EL-REQ-MISSING-VALUE:condition_fidelity_review"
    resolved = lecturer.post(
        f"/api/v1/spec-lint-reports/{current_report_id}/resolutions",
        headers=headers(csrf),
        json={
            "issue_code": issue_code,
            "resolution": "acknowledged_human_boundary",
            "reason": "Kondisi sumber harus ditetapkan dan ditinjau dosen.",
            "expected_spec_revision": 3,
            "expected_report_revision": 1,
        },
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["resolution_revision"] == 2
    duplicate = lecturer.post(
        f"/api/v1/spec-lint-reports/{current_report_id}/resolutions",
        headers=headers(csrf),
        json={
            "issue_code": issue_code,
            "resolution": "acknowledged_human_boundary",
            "reason": "Tidak boleh menggandakan resolusi.",
            "expected_spec_revision": 3,
            "expected_report_revision": 2,
        },
    )
    assert duplicate.status_code == 409
    approved = lecturer.post(
        f"/api/v1/specification-versions/{specification['id']}/approve",
        headers=headers(csrf),
        json={
            "expected_revision": 3,
            "lint_report_id": current_report_id,
            "expected_report_revision": 2,
            "fidelity_confirmation": True,
        },
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["fidelity_confirmation"] is True
    assert approved.json()["state"] == "approved"

    fact_rule_element = SimpleNamespace(
        applicability=SimpleNamespace(
            mode="fact_rule",
            rule={"op": "eq", "fact": "task.submission_variant", "value": "original"},
        )
    )
    fact_registry = {
        "submission_variant": TaskFact(
            key="submission_variant", value_type="string", allowed_values=["original", "alternate"]
        )
    }
    task_source_hash = uuid4().hex * 2
    task_artifact_fingerprint = uuid4().hex * 2
    task_artifact_scope_hash = uuid4().hex * 2
    with SessionFactory.begin() as db:
        rubric_asset = FileAsset(
            course_id=course.id,
            assignment_id=UUID(assignment["id"]),
            purpose="rubric",
            uploaded_by=b2_scenario["users"]["lecturer"].id,
            original_name="task-fact-source.txt",
            size_bytes=1,
            mime="text/plain",
            sha256=task_source_hash,
            object_key=f"synthetic/b2-task-fact/{uuid4()}",
            validation_state="validated",
            preflight_json={},
        )
        db.add(rubric_asset)
        db.flush()
        db.add(NormativeFileLink(
            file_asset_id=rubric_asset.id,
            assignment_version_id=UUID(version_id),
            source_role="rubric",
        ))
        task_artifact = ParseArtifact(
            file_asset_id=rubric_asset.id,
            assignment_version_id=UUID(version_id),
            fingerprint=task_artifact_fingerprint,
            owner_scope_hash=task_artifact_scope_hash,
            status="parsed",
            limitations_json=[],
        )
        db.add(task_artifact)
        db.flush()
        task_unit = SourceUnit(
            parse_artifact_id=task_artifact.id,
            locator_key="synthetic-paragraph-1",
            unit_type="paragraph",
            source_order=0,
            anchor_json={"paragraph": 1},
            parse_status="parsed",
            limitations_json=[],
        )
        db.add(task_unit)
        db.flush()
        task_evidence = EvidenceItem(
            source_unit_id=task_unit.id,
            assignment_version_id=UUID(version_id),
            file_asset_id=rubric_asset.id,
            source_sha256=task_source_hash,
            type="paragraph",
            anchor_json={"paragraph": 1},
            content_original_json={"text": "Varian asli untuk tugas sintetis."},
            content_normalized_json={"text": "Varian asli untuk tugas sintetis."},
            parse_status="parsed",
            limitations_json=[],
        )
        db.add(task_evidence)
        db.flush()
        task_evidence_id = task_evidence.id
    fact_route = f"/api/v1/assignment-versions/{version_id}/task-facts"
    initial_revision = lecturer.get(f"/api/v1/assignment-versions/{version_id}").json()["revision"]
    empty_snapshot = {
        "schema_version": 1,
        "assignment_version_id": version_id,
        "specification_version_id": specification["id"],
        "specification_hash": approved.json()["content_hash"],
        "facts": {},
        "content_hash": canonical_hash({}),
    }
    empty_values, _ = verified_task_fact_values(
        empty_snapshot,
        assignment_version_id=version_id,
        specification_version_id=specification["id"],
        specification_hash=approved.json()["content_hash"],
        definitions=fact_registry,
    )
    assert resolve_applicability(fact_rule_element, empty_values) == (
        "unresolved", "applicability_fact_unverified"
    )
    fact_body = {
        "expected_revision": initial_revision,
        "lecturer_confirmation": True,
        "facts": [{
            "key": "submission_variant",
            "value": "original",
            "origin": "Dosen mengonfirmasi ketentuan varian pada instruksi sintetis.",
            "evidence_ids": [str(task_evidence_id)],
        }],
    }
    assert b2_scenario["clients"]["student_a"].put(
        fact_route,
        headers=headers(b2_scenario["tokens"]["student_a"]),
        json=fact_body,
    ).status_code == 404
    assert b2_scenario["clients"]["outsider"].put(
        fact_route,
        headers=headers(b2_scenario["tokens"]["outsider"]),
        json=fact_body,
    ).status_code == 404
    true_response = lecturer.put(fact_route, headers=headers(csrf), json=fact_body)
    assert true_response.status_code == 200, true_response.text
    true_record = true_response.json()["task_facts"]["facts"]["submission_variant"]
    assert true_record["verification"] == "lecturer_confirmed"
    assert true_record["verified_by"] == str(b2_scenario["users"]["lecturer"].id)
    assert true_record["origin"] == fact_body["facts"][0]["origin"]
    assert true_record["evidence_ids"] == [str(task_evidence_id)]
    true_values, provenance = verified_task_fact_values(
        true_response.json()["task_facts"],
        assignment_version_id=version_id,
        specification_version_id=specification["id"],
        specification_hash=approved.json()["content_hash"],
        definitions=fact_registry,
    )
    assert resolve_applicability(fact_rule_element, true_values) == ("applicable", None)
    assert provenance["submission_variant"]["verified_by"] == str(b2_scenario["users"]["lecturer"].id)
    assert provenance["submission_variant"]["evidence_ids"] == [str(task_evidence_id)]
    assert lecturer.put(fact_route, headers=headers(csrf), json=fact_body).status_code == 409
    invalid_value = {
        **fact_body,
        "expected_revision": true_response.json()["revision"],
        "facts": [{**fact_body["facts"][0], "value": "unregistered"}],
    }
    assert lecturer.put(fact_route, headers=headers(csrf), json=invalid_value).status_code == 422
    invalid_evidence = {
        **fact_body,
        "expected_revision": true_response.json()["revision"],
        "facts": [{**fact_body["facts"][0], "evidence_ids": [str(uuid4())]}],
    }
    assert lecturer.put(fact_route, headers=headers(csrf), json=invalid_evidence).status_code == 404

    false_body = {
        **fact_body,
        "expected_revision": true_response.json()["revision"],
        "facts": [{**fact_body["facts"][0], "value": "alternate"}],
    }
    false_response = lecturer.put(fact_route, headers=headers(csrf), json=false_body)
    assert false_response.status_code == 200, false_response.text
    false_values, _ = verified_task_fact_values(
        false_response.json()["task_facts"],
        assignment_version_id=version_id,
        specification_version_id=specification["id"],
        specification_hash=approved.json()["content_hash"],
        definitions=fact_registry,
    )
    assert resolve_applicability(fact_rule_element, false_values) == (
        "not_applicable", "verified_fact_rule_false"
    )
    assert false_response.json()["task_facts"]["content_hash"] != true_response.json()["task_facts"]["content_hash"]
    unknown_response = lecturer.put(
        fact_route,
        headers=headers(csrf),
        json={"expected_revision": false_response.json()["revision"], "lecturer_confirmation": True, "facts": []},
    )
    assert unknown_response.status_code == 200, unknown_response.text
    unknown_values, _ = verified_task_fact_values(
        unknown_response.json()["task_facts"],
        assignment_version_id=version_id,
        specification_version_id=specification["id"],
        specification_hash=approved.json()["content_hash"],
        definitions=fact_registry,
    )
    assert resolve_applicability(fact_rule_element, unknown_values) == (
        "unresolved", "applicability_fact_unverified"
    )
    true_again = lecturer.put(
        fact_route,
        headers=headers(csrf),
        json={
            "expected_revision": unknown_response.json()["revision"],
            "lecturer_confirmation": True,
            "facts": fact_body["facts"],
        },
    )
    assert true_again.status_code == 200, true_again.text

    replacement_spec_draft = lecturer.post(
        f"/api/v1/assignment-versions/{version_id}/specification-drafts",
        headers=headers(csrf),
        json={"criterion_set_version_id": criterion_set["id"]},
    )
    assert replacement_spec_draft.status_code == 201, replacement_spec_draft.text
    replacement_spec_id = replacement_spec_draft.json()["id"]
    cleared_facts = lecturer.get(f"/api/v1/assignment-versions/{version_id}").json()
    assert cleared_facts["task_facts"] == {}

    replacement_lint = lecturer.put(
        f"/api/v1/specification-versions/{replacement_spec_id}",
        headers=headers(csrf),
        json={"expected_revision": 1, "specification": complete},
    )
    assert replacement_lint.status_code == 200, replacement_lint.text
    assert replacement_lint.json()["effective_status"] == "needs_review"
    replacement_resolution = lecturer.post(
        f"/api/v1/spec-lint-reports/{replacement_lint.json()['lint_report_id']}/resolutions",
        headers=headers(csrf),
        json={
            "issue_code": "EL-REQ-MISSING-VALUE:condition_fidelity_review",
            "resolution": "acknowledged_human_boundary",
            "reason": "Dosen memverifikasi applicability terhadap instruksi sintetis.",
            "expected_spec_revision": 2,
            "expected_report_revision": 1,
        },
    )
    assert replacement_resolution.status_code == 200, replacement_resolution.text
    replacement_approval = lecturer.post(
        f"/api/v1/specification-versions/{replacement_spec_id}/approve",
        headers=headers(csrf),
        json={
            "expected_revision": 2,
            "lint_report_id": replacement_lint.json()["lint_report_id"],
            "expected_report_revision": 2,
            "fidelity_confirmation": True,
        },
    )
    assert replacement_approval.status_code == 200, replacement_approval.text
    approved_replacement_version = lecturer.get(
        f"/api/v1/assignment-versions/{version_id}"
    ).json()
    assert approved_replacement_version["task_facts"] == {}
    reverified_fact = lecturer.put(
        fact_route,
        headers=headers(csrf),
        json={
            "expected_revision": approved_replacement_version["revision"],
            "lecturer_confirmation": True,
            "facts": fact_body["facts"],
        },
    )
    assert reverified_fact.status_code == 200, reverified_fact.text
    rebound_record = reverified_fact.json()["task_facts"]
    assert rebound_record["specification_version_id"] == replacement_spec_id
    assert rebound_record["specification_hash"] == replacement_approval.json()["content_hash"]

    preview = lecturer.get(f"/api/v1/assignment-versions/{version_id}/publish-preview")
    assert preview.status_code == 200, preview.text
    public_preview = preview.json()
    assert public_preview["version"]["instruction_original"] == instruction
    assert public_preview["version"]["criteria"][0]["requirements"][0]["text"] == requirement["text"]
    assert "specification" not in public_preview["version"]
    assert "grounding_contract" not in json.dumps(public_preview)

    current_version = lecturer.get(f"/api/v1/assignment-versions/{version_id}").json()
    published = lecturer.post(
        f"/api/v1/assignment-versions/{version_id}/publish",
        headers=headers(csrf),
        json={"expected_revision": current_version["revision"]},
    )
    assert published.status_code == 200, published.text

    group = lecturer.post(
        f"/api/v1/assignments/{assignment['id']}/groups",
        headers=headers(csrf),
        json={
            "name": "Kelompok sintetis",
            "member_user_ids": [
                str(b2_scenario["users"]["student_a"].id),
                str(b2_scenario["users"]["student_b"].id),
            ],
            "active": True,
        },
    )
    assert group.status_code == 201, group.text
    duplicate_member = lecturer.post(
        f"/api/v1/assignments/{assignment['id']}/groups",
        headers=headers(csrf),
        json={
            "name": "Kelompok duplikat",
            "member_user_ids": [str(b2_scenario["users"]["student_a"].id)],
            "active": True,
        },
    )
    assert duplicate_member.status_code == 409
    updated_group = lecturer.put(
        f"/api/v1/groups/{group.json()['id']}",
        headers=headers(csrf),
        json={
            "expected_revision": group.json()["revision"],
            "name": "Kelompok sintetis diperbarui",
            "member_user_ids": [
                str(b2_scenario["users"]["student_a"].id),
                str(b2_scenario["users"]["student_b"].id),
            ],
            "active": True,
        },
    )
    assert updated_group.status_code == 200, updated_group.text
    assert len(updated_group.json()["members"]) == 2

    public = b2_scenario["clients"]["student_a"].get(f"/api/v1/assignments/{assignment['id']}")
    assert public.status_code == 200, public.text
    public_body = public.json()
    assert public_body["version"]["instruction_original"] == instruction
    assert (
        public_body["version"]["instruction_summary_label"] == "Ringkasan, bukan sumber kewajiban"
    )
    assert public_body["version"]["criteria"][0]["requirements"][0]["text"] == phrase
    assert "payload" not in public_body["version"]
    assert "decision_question" not in public.text
    assert "numeric_policy_version_id" not in public.text
    student_groups = b2_scenario["clients"]["student_a"].get(
        f"/api/v1/assignments/{assignment['id']}/groups"
    )
    assert student_groups.status_code == 200
    assert len(student_groups.json()["items"]) == 1
    assert len(student_groups.json()["items"][0]["members"]) == 2
    outsider = b2_scenario["clients"]["outsider"].get(f"/api/v1/assignments/{assignment['id']}")
    assert outsider.status_code == 404

    new_version = lecturer.post(
        f"/api/v1/assignments/{assignment['id']}/new-version",
        headers=headers(csrf),
        json={"base_version_id": version_id},
    )
    assert new_version.status_code == 201, new_version.text
    assert new_version.json()["state"] == "draft"
    assert new_version.json()["package_approved_at"] is None
    assert new_version.json()["criterion_set"]["state"] == "draft"
    assert new_version.json()["specification"] is None
    assert new_version.json()["task_facts"] == {}

    history_fixture = json.loads(
        (Path(__file__).parents[2] / "fixtures/synthetic/b2-schema1-history.json").read_text()
    )
    legacy_payload = history_fixture["payload"]
    legacy_element = legacy_payload["elements"][0]
    assert "grounding_contract" not in legacy_element
    assert "derivation_refs" not in legacy_element
    legacy = SpecificationVersion(
        assignment_id=UUID(assignment["id"]),
        version_no=3,
        schema_version=1,
        criterion_set_version_id=UUID(new_version.json()["criterion_set"]["id"]),
        context_version_id=context.id,
        instruction_hash=new_version.json()["instruction_hash"],
        payload_json=legacy_payload,
        content_hash=canonical_hash(legacy_payload),
        state="draft",
        revision=1,
    )
    with SessionFactory.begin() as db:
        db.add(legacy)
        db.flush()
        legacy_id = str(legacy.id)
    legacy_read = lecturer.get(f"/api/v1/specification-versions/{legacy_id}")
    assert legacy_read.status_code == 200
    assert legacy_read.json()["schema_version"] == 1
    assert legacy_read.json()["payload"] == legacy_payload
    assert legacy_read.json()["lint"] is None
    assert (
        lecturer.put(
            f"/api/v1/specification-versions/{legacy_id}",
            headers=headers(csrf),
            json={"expected_revision": 1, "specification": {"schema_version": 2}},
        ).status_code
        == 409
    )
    assert (
        lecturer.post(
            f"/api/v1/specification-versions/{legacy_id}/lint",
            headers=headers(csrf),
            json={"expected_revision": 1},
        ).status_code
        == 409
    )
    assert (
        lecturer.post(
            f"/api/v1/specification-versions/{legacy_id}/approve",
            headers=headers(csrf),
            json={
                "expected_revision": 1,
                "lint_report_id": str(uuid4()),
                "expected_report_revision": 1,
                "fidelity_confirmation": True,
            },
        ).status_code
        == 409
    )


def test_b2_rubric_uses_shared_parser_and_hash_bound_evidence(b2_scenario):
    lecturer = b2_scenario["clients"]["lecturer"]
    csrf = b2_scenario["tokens"]["lecturer"]
    assignment, version = create_assignment_draft(
        lecturer,
        csrf,
        b2_scenario["course"],
        b2_scenario["context"],
        "Rubrik parser B2",
        "Gunakan excerpt rubrik yang ditinjau dosen.",
    )
    fixture = Path(__file__).parents[2] / "apps/web/e2e/fixtures/kk_context.docx"
    upload = lecturer.post(
        f"/api/v1/assignments/{assignment['id']}/rubric-files",
        headers=headers(csrf),
        files={
            "file": (
                "rubrik-sintetis.docx",
                fixture.read_bytes(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert upload.status_code == 201, upload.text
    asset = upload.json()["asset"]
    opened = lecturer.post(
        f"/api/v1/assignment-versions/{version['id']}/criteria-drafts",
        headers=headers(csrf),
        json={"rubric_file_ids": [asset["id"]], "generate_from_instruction": False},
    )
    assert opened.status_code == 201, opened.text
    generation = opened.json()["generation"]
    assert generation["rubric_parser_status"] == "queued"
    job_id = generation["rubric_parser_job_id"]
    assert job_id

    result = None
    for _ in range(120):
        parsed = lecturer.get(f"/api/v1/assignment-versions/{version['id']}/rubric-parse")
        assert parsed.status_code == 200, parsed.text
        result = parsed.json()
        if result["status"] == "ready_for_review":
            break
        time.sleep(1)
    assert result is not None and result["status"] == "ready_for_review", result
    assert result["status"] == "ready_for_review"
    file_result = result["files"][0]
    assert file_result["parse_status"] in {"parsed", "partially_parsed"}
    evidence = next(item for item in file_result["evidence"] if item["text"])
    assert evidence["source_sha256"] == asset["sha256"]
    assert evidence["anchor"]

    with SessionFactory() as db:
        artifact = db.scalar(
            select(ParseArtifact).where(
                ParseArtifact.file_asset_id == UUID(asset["id"]),
                ParseArtifact.assignment_version_id == UUID(version["id"]),
            )
        )
        assert artifact is not None and artifact.parser_manifest_id and artifact.parser_resource_manifest_id
        assert artifact.context_version_id is None
        parsed_evidence = db.scalar(
            select(EvidenceItem).where(EvidenceItem.id == UUID(evidence["id"]))
        )
        assert parsed_evidence is not None
        assert parsed_evidence.assignment_version_id == UUID(version["id"])
        assert parsed_evidence.context_version_id is None

    text = evidence["text"]
    size = len(text)
    criterion_set = opened.json()["criterion_set_version_id"]
    criteria_saved = lecturer.put(
        f"/api/v1/criterion-set-versions/{criterion_set}",
        headers=headers(csrf),
        json={
            "expected_revision": opened.json()["revision"],
            "criteria": [
                {
                    "code": "C-RUBRIC",
                    "title": "Kriteria dari excerpt",
                    "descriptor": "Excerpt parser hanya menjadi sumber kutipan untuk keputusan dosen.",
                    "display_order": 0,
                    "outcome_ids": [],
                    "requirements": [
                        {
                            "code": "R-RUBRIC",
                            "text": text,
                            "mandatory": False,
                            "obligation_key": "rubric.reviewed_excerpt",
                            "normative_source_refs": [
                                {
                                    "source_kind": "rubric_evidence",
                                    "source_id": evidence["id"],
                                    "span": {"start": 0, "end": size},
                                }
                            ],
                        }
                    ],
                }
            ],
        },
    )
    assert criteria_saved.status_code == 200, criteria_saved.text
    projection = criteria_saved.json()["criteria"][0]["requirements"][0]["normative_source_refs"][0]
    assert projection["quote"] == text
    assert projection["source_sha256"] == asset["sha256"]
    assert projection["anchor"] == evidence["anchor"]
    approved = lecturer.post(
        f"/api/v1/assignment-versions/{version['id']}/approve-package",
        headers=headers(csrf),
        json={
            "criterion_set_version_id": criterion_set,
            "expected_revision": lecturer.get(f"/api/v1/assignment-versions/{version['id']}").json()["revision"],
        },
    )
    assert approved.status_code == 200, approved.text


def test_semantic_negative_exact_but_irrelevant_quote_requires_fidelity_review(b2_scenario):
    lecturer = b2_scenario["clients"]["lecturer"]
    csrf = b2_scenario["tokens"]["lecturer"]
    fixture = json.loads(
        (Path(__file__).parents[2] / "fixtures/synthetic/b2-semantic-negative.json").read_text()
    )
    instruction = fixture["instruction_original"]
    assignment, version = create_assignment_draft(
        lecturer,
        csrf,
        b2_scenario["course"],
        b2_scenario["context"],
        "Fixture semantic-negative B2",
        instruction,
    )
    opened = lecturer.post(
        f"/api/v1/assignment-versions/{version['id']}/criteria-drafts",
        headers=headers(csrf),
        json={"rubric_file_ids": [], "generate_from_instruction": False},
    )
    assert opened.status_code == 201, opened.text
    criterion_set = opened.json()["criterion_set_version_id"]
    official = fixture["official_requirement"]
    official_start = instruction.index(official)
    criteria_saved = lecturer.put(
        f"/api/v1/criterion-set-versions/{criterion_set}",
        headers=headers(csrf),
        json={
            "expected_revision": opened.json()["revision"],
            "criteria": [
                {
                    "code": "C-SEMANTIC-NEGATIVE",
                    "title": "Kewajiban bersyarat",
                    "descriptor": "Makna dan cakupan harus ditelaah manusia.",
                    "display_order": 0,
                    "outcome_ids": [],
                    "requirements": [
                        {
                            "code": "R-SEMANTIC-NEGATIVE",
                            "text": official,
                            "mandatory": True,
                            "obligation_key": "inflation.operational_cost.conditional",
                            "normative_source_refs": [
                                {
                                    "source_kind": "instruction",
                                    "source_id": version["id"],
                                    "span": {"start": official_start, "end": official_start + len(official)},
                                }
                            ],
                        }
                    ],
                }
            ],
        },
    )
    assert criteria_saved.status_code == 200, criteria_saved.text
    criterion = criteria_saved.json()["criteria"][0]
    requirement = criterion["requirements"][0]
    package = lecturer.post(
        f"/api/v1/assignment-versions/{version['id']}/approve-package",
        headers=headers(csrf),
        json={"criterion_set_version_id": criterion_set, "expected_revision": lecturer.get(f"/api/v1/assignment-versions/{version['id']}").json()["revision"]},
    )
    assert package.status_code == 200, package.text
    draft = lecturer.post(
        f"/api/v1/assignment-versions/{version['id']}/specification-drafts",
        headers=headers(csrf),
        json={"criterion_set_version_id": criterion_set},
    )
    assert draft.status_code == 201, draft.text
    irrelevant_quote = fixture["mechanically_exact_but_irrelevant_quote"]
    span = fixture["quote_codepoint_span"]
    assert instruction[span["start"]:span["end"]] == irrelevant_quote
    element = element_template(
        element_id=str(uuid4()),
        element_code="EL-SEMANTIC-NEGATIVE",
        criterion_id=criterion["id"],
        obligation_key=requirement["obligation_key"],
        target_kind="requirement",
        requirement_ids=[requirement["id"]],
        derivation_refs=[
            {"source_kind": "requirement", "source_id": requirement["id"], "span": None},
            {"source_kind": "instruction", "source_id": version["id"], "span": span},
        ],
        grounding={"schema_version": 1, "mode": "human_only", "absence_support": None},
        operators=["locate_evidence", "expand_context", "inspect_source_coverage", "request_human_review"],
        question=fixture["decision_question"],
    )
    updated = lecturer.put(
        f"/api/v1/specification-versions/{draft.json()['id']}",
        headers=headers(csrf),
        json={"expected_revision": draft.json()["revision"], "specification": specification_envelope([element])},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["effective_status"] == "needs_review"
    report = lecturer.get(f"/api/v1/spec-lint-reports/{updated.json()['lint_report_id']}")
    assert report.status_code == 200, report.text
    issue = next(item for item in report.json()["issues"] if item["rule_code"] == "condition_fidelity_review")
    assert issue["severity"] == "review"
    assert issue["source_refs"][-1]["span"] == span
    unreviewed = lecturer.post(
        f"/api/v1/specification-versions/{draft.json()['id']}/approve",
        headers=headers(csrf),
        json={
            "expected_revision": updated.json()["revision"],
            "lint_report_id": updated.json()["lint_report_id"],
            "expected_report_revision": updated.json()["resolution_revision"],
            "fidelity_confirmation": True,
        },
    )
    assert unreviewed.status_code == 422


def test_two_lecturers_race_package_specification_and_publish_approvals(b2_scenario):
    first = b2_scenario["clients"]["lecturer"]
    second = b2_scenario["clients"]["lecturer_b"]
    first_token = b2_scenario["tokens"]["lecturer"]
    second_token = b2_scenario["tokens"]["lecturer_b"]
    assignment, version = create_assignment_draft(
        first,
        first_token,
        b2_scenario["course"],
        b2_scenario["context"],
        "Race approval B2",
        "Jelaskan alasan pemilihan metode.",
    )
    opened = first.post(
        f"/api/v1/assignment-versions/{version['id']}/criteria-drafts",
        headers=headers(first_token),
        json={"rubric_file_ids": [], "generate_from_instruction": False},
    )
    assert opened.status_code == 201, opened.text
    criterion_set = opened.json()["criterion_set_version_id"]
    criterion_body = {
        "expected_revision": opened.json()["revision"],
        "criteria": [
            {
                "code": "C-RACE",
                "title": "Penjelasan metode",
                "descriptor": "Jelaskan alasan pemilihan metode.",
                "display_order": 0,
                "outcome_ids": [],
                "requirements": [
                    {
                        "code": "R-RACE",
                        "text": "Jelaskan alasan pemilihan metode.",
                        "mandatory": True,
                        "obligation_key": "method.selection.rationale",
                        "normative_source_refs": [
                            {"source_kind": "instruction", "source_id": version["id"], "span": {"start": 0, "end": len(version["instruction_original"])}},
                        ],
                    }
                ],
            }
        ],
    }
    saved = first.put(f"/api/v1/criterion-set-versions/{criterion_set}", headers=headers(first_token), json=criterion_body)
    assert saved.status_code == 200, saved.text
    criterion = saved.json()["criteria"][0]
    requirement = criterion["requirements"][0]

    def race_post(path: str, body: dict[str, object]) -> list[int]:
        barrier = Barrier(2)

        def call(client: TestClient, token: str) -> int:
            barrier.wait(timeout=10)
            return client.post(path, headers=headers(token), json=body).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(call, first, first_token),
                pool.submit(call, second, second_token),
            ]
            return sorted(future.result(timeout=20) for future in futures)

    current = first.get(f"/api/v1/assignment-versions/{version['id']}").json()
    package_body = {"criterion_set_version_id": criterion_set, "expected_revision": current["revision"]}
    assert race_post(f"/api/v1/assignment-versions/{version['id']}/approve-package", package_body) == [200, 409]

    specification = first.post(
        f"/api/v1/assignment-versions/{version['id']}/specification-drafts",
        headers=headers(first_token),
        json={"criterion_set_version_id": criterion_set},
    )
    assert specification.status_code == 201, specification.text
    element = element_template(
        element_id=str(uuid4()),
        element_code="EL-METHOD-RATIONALE",
        criterion_id=criterion["id"],
        obligation_key=requirement["obligation_key"],
        target_kind="requirement",
        requirement_ids=[requirement["id"]],
        derivation_refs=[{"source_kind": "requirement", "source_id": requirement["id"], "span": None}],
        grounding={"schema_version": 1, "mode": "human_only", "absence_support": None},
        operators=["locate_evidence", "expand_context", "inspect_source_coverage", "request_human_review"],
    )
    saved_spec = first.put(
        f"/api/v1/specification-versions/{specification.json()['id']}",
        headers=headers(first_token),
        json={"expected_revision": 1, "specification": specification_envelope([element])},
    )
    assert saved_spec.status_code == 200, saved_spec.text
    assert saved_spec.json()["effective_status"] == "passed"
    spec_body = {
        "expected_revision": saved_spec.json()["revision"],
        "lint_report_id": saved_spec.json()["lint_report_id"],
        "expected_report_revision": saved_spec.json()["resolution_revision"],
        "fidelity_confirmation": True,
    }
    assert race_post(f"/api/v1/specification-versions/{specification.json()['id']}/approve", spec_body) == [200, 409]

    latest = first.get(f"/api/v1/assignment-versions/{version['id']}").json()
    assert race_post(
        f"/api/v1/assignment-versions/{version['id']}/publish",
        {"expected_revision": latest["revision"]},
    ) == [200, 409]
    published = first.get(f"/api/v1/assignment-versions/{version['id']}")
    assert published.status_code == 200 and published.json()["state"] == "published"
