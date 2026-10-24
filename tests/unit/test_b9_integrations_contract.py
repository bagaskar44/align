from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

from align_api.integrations import (
    GroupMappingCreate,
    ImportRequest,
    MappingCreate,
    MappingPatch,
    _verify_moodle_assignment,
)
from align_api.moodle_adapter import MoodleWebService


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
