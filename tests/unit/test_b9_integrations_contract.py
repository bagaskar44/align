from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session as DbSession

from align_api.idempotency import canonical_hash
from align_api.integrations import (
    GroupMappingCreate,
    ImportRequest,
    MappingCreate,
    MappingPatch,
    _cached_import_response,
    _external_roster,
    _verify_moodle_assignment,
)
from align_api.models import IdempotencyRecord
from align_api.moodle_adapter import MoodleWebService


class StubIdempotencySession:
    def __init__(self, record: IdempotencyRecord | None) -> None:
        self.record = record
        self.reads = 0

    def scalar(self, _statement: object) -> IdempotencyRecord | None:
        self.reads += 1
        return self.record


def _import_request() -> ImportRequest:
    return ImportRequest(
        mapping_id=uuid4(),
        expected_mapping_revision=2,
        attempt_id="1:5",
        confirmed_owner_user_id=uuid4(),
        confirmed_roster_user_ids=[],
    )


def _idempotency_record(
    actor_id: UUID, key: str, body: ImportRequest, *, expires_at: datetime | None = None,
    response: dict[str, object] | None = None, response_status: int = 201,
) -> IdempotencyRecord:
    return IdempotencyRecord(
        actor_id=actor_id,
        method="POST",
        route="/api/v1/integrations/moodle/imports",
        key=key,
        body_hash=canonical_hash(body.model_dump(mode="json")),
        response_status=response_status,
        response_json=response,
        expires_at=expires_at or datetime.now(UTC) + timedelta(hours=1),
    )


def test_moodle_import_replays_completed_receipt_without_writing_or_network_state() -> None:
    actor_id = uuid4()
    key = str(uuid4())
    body = _import_request()
    receipt = {"status": "imported", "event_id": str(uuid4())}
    db = StubIdempotencySession(_idempotency_record(actor_id, key, body, response=receipt))

    assert _cached_import_response(cast(DbSession, db), actor_id, key, body) == receipt
    assert db.reads == 1


def test_moodle_import_rejects_reused_key_with_different_body_before_network() -> None:
    actor_id = uuid4()
    key = str(uuid4())
    first_body = _import_request()
    second_body = _import_request()
    db = StubIdempotencySession(
        _idempotency_record(actor_id, key, first_body, response={"status": "imported"})
    )

    with pytest.raises(HTTPException) as raised:
        _cached_import_response(cast(DbSession, db), actor_id, key, second_body)

    assert raised.value.status_code == 409
    assert db.reads == 1


def test_moodle_import_expired_receipt_allows_a_new_attempt() -> None:
    actor_id = uuid4()
    key = str(uuid4())
    body = _import_request()
    db = StubIdempotencySession(
        _idempotency_record(
            actor_id, key, body, expires_at=datetime.now(UTC) - timedelta(seconds=1),
            response={"status": "imported"},
        )
    )

    assert _cached_import_response(cast(DbSession, db), actor_id, key, body) is None


def test_moodle_import_in_progress_key_and_malformed_key_fail_closed() -> None:
    actor_id = uuid4()
    key = str(uuid4())
    body = _import_request()
    db = StubIdempotencySession(_idempotency_record(actor_id, key, body, response_status=0))

    with pytest.raises(HTTPException) as in_progress:
        _cached_import_response(cast(DbSession, db), actor_id, key, body)
    with pytest.raises(HTTPException) as malformed:
        _cached_import_response(cast(DbSession, db), actor_id, "not-a-uuid", body)

    assert in_progress.value.status_code == 409
    assert malformed.value.status_code == 422
    assert db.reads == 1


def test_moodle_mutation_schemas_parse_browser_uuid_strings() -> None:
    platform_id = uuid4()
    course_id = uuid4()
    assignment_version_id = uuid4()
    owner_id = uuid4()
    member_id = uuid4()
    mapping_id = uuid4()
    group_id = uuid4()

    mapping = MappingCreate.model_validate(
        {
            "platform_id": str(platform_id),
            "lti_context_id": "2",
            "lti_resource_link_id": "1",
            "moodle_course_id": 2,
            "moodle_assignment_id": 1,
            "local_course_id": str(course_id),
            "assignment_version_id": str(assignment_version_id),
        }
    )
    patch = MappingPatch.model_validate(
        {
            "expected_revision": 1,
            "active": True,
            "local_course_id": str(course_id),
            "assignment_version_id": str(assignment_version_id),
        }
    )
    group = GroupMappingCreate.model_validate(
        {"moodle_group_id": 1, "local_group_id": str(group_id)}
    )
    request = ImportRequest.model_validate(
        {
            "mapping_id": str(mapping_id),
            "expected_mapping_revision": 1,
            "attempt_id": "1:5",
            "confirmed_owner_user_id": str(owner_id),
            "confirmed_roster_user_ids": [str(owner_id), str(member_id)],
        }
    )

    assert mapping.platform_id == platform_id
    assert mapping.local_course_id == course_id
    assert mapping.assignment_version_id == assignment_version_id
    assert patch.local_course_id == course_id
    assert patch.assignment_version_id == assignment_version_id
    assert group.local_group_id == group_id
    assert request.mapping_id == mapping_id
    assert request.confirmed_owner_user_id == owner_id
    assert request.confirmed_roster_user_ids == [owner_id, member_id]
    assert isinstance(request.mapping_id, UUID)


def test_moodle_assignment_lookup_uses_supported_course_filter() -> None:
    class StubMoodleClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        async def verify_functions(self, required: set[str]) -> frozenset[str]:
            return frozenset(required)

        async def call(self, function: str, **parameters: object) -> dict[str, object]:
            self.calls.append((function, parameters))
            return {"courses": [{"id": 2, "assignments": [{"id": 1}]}]}

    client = StubMoodleClient()
    mapping = SimpleNamespace(moodle_course_id=2, moodle_assignment_id=1)

    asyncio.run(_verify_moodle_assignment(cast(MoodleWebService, client), mapping))

    assert client.calls == [("mod_assign_get_assignments", {"courseids": [2]})]


@pytest.mark.parametrize(
    "members_payload",
    [
        [{"groupid": 7, "userids": [10, 11]}],
        [{"id": 7, "users": [{"id": 10}, {"id": 11}]}],
    ],
)
def test_moodle_group_roster_accepts_live_and_legacy_webservice_shapes(
    members_payload: list[dict[str, object]],
) -> None:
    class StubMoodleClient:
        async def verify_functions(self, _required: set[str]) -> frozenset[str]:
            return frozenset({"core_group_get_course_groups", "core_group_get_group_members"})

        async def call(self, function: str, **_parameters: object) -> object:
            if function == "core_enrol_get_enrolled_users":
                return [{"id": 10, "suspended": False}, {"id": 11, "suspended": False}]
            if function == "core_group_get_course_groups":
                return [{"id": 7}]
            if function == "core_group_get_group_members":
                return members_payload
            raise AssertionError(f"unexpected Moodle function {function}")

    group_id, member_ids = asyncio.run(
        _external_roster(
            cast(MoodleWebService, StubMoodleClient()),
            SimpleNamespace(moodle_course_id=3),
            {"groupid": 7, "userid": 10},
        )
    )

    assert group_id == 7
    assert member_ids == [10, 11]
