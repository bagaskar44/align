from __future__ import annotations

import pytest

from align_api.moodle_adapter import (
    MoodleAdapterError,
    MoodleWebService,
    assignment_attempts,
    submission_files,
    validate_file_url,
)


def test_file_download_url_is_pinned_to_moodle_submission_files() -> None:
    url = "https://moodle-b9.localhost:8443/webservice/pluginfile.php/9/assignsubmission_file/submission_files/4/final.pdf?forcedownload=1"
    assert validate_file_url(url, "https://moodle-b9.localhost:8443") == url

    rejected = [
        "https://foreign.example/webservice/pluginfile.php/9/assignsubmission_file/submission_files/4/final.pdf",
        "http://moodle-b9.localhost:8443/webservice/pluginfile.php/9/assignsubmission_file/submission_files/4/final.pdf",
        "https://moodle-b9.localhost:8443/pluginfile.php/9/assignsubmission_file/submission_files/4/final.pdf",
        "https://moodle-b9.localhost:8443/webservice/pluginfile.php/9/assignsubmission_file/submission_files/4/final.pdf?token=secret",
        "https://moodle-b9.localhost:8443/webservice/pluginfile.php/9/assignsubmission_file/submission_files/../secret.pdf",
        "https://moodle-b9.localhost:8443/webservice/pluginfile.php/9/assignsubmission_file/submission_files/%2e%2e/secret.pdf",
        "https://moodle-b9.localhost:8443/webservice/pluginfile.php/9/assignsubmission_file/submission_files/%252e%252e/secret.pdf",
    ]
    for candidate in rejected:
        with pytest.raises(MoodleAdapterError):
            validate_file_url(candidate, "https://moodle-b9.localhost:8443")


def test_moodle_webservice_accepts_only_the_read_allowlist() -> None:
    client = MoodleWebService(
        origin="https://moodle-b9.localhost:8443",
        token="test-token-not-a-credential",  # noqa: S106
    )
    with pytest.raises(MoodleAdapterError, match="moodle_function_not_allowlisted"):
        import asyncio

        asyncio.run(client.call("mod_assign_save_grade"))


def test_assignment_attempt_parser_keeps_only_submitted_attempts() -> None:
    payload = {
        "assignments": [
            {
                "assignmentid": 12,
                "submissions": [
                    {"id": 90, "attemptnumber": 1, "status": "submitted", "userid": 8},
                    {"id": 91, "attemptnumber": 2, "status": "draft", "userid": 8},
                ],
            },
            {
                "assignmentid": 99,
                "submissions": [{"id": 92, "attemptnumber": 1, "status": "submitted"}],
            },
        ]
    }
    attempts = assignment_attempts(payload, 12)
    assert [item["attemptid"] for item in attempts] == ["90:1"]


def test_online_text_and_non_file_sources_are_blocked() -> None:
    with pytest.raises(MoodleAdapterError, match="unsupported_moodle_submission_source"):
        submission_files(
            {"plugins": [{"type": "onlinetext", "editorfields": [{"text": "answer"}]}]}
        )
    with pytest.raises(MoodleAdapterError, match="unsupported_moodle_submission_source"):
        submission_files({"plugins": [{"type": "onlinetext", "editorfields": []}]})
