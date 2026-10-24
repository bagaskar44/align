from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

from align_api import integrations, moodle_adapter
from align_api.config import Settings
from align_api.moodle_adapter import (
    MoodleAdapterError,
    MoodleWebService,
    assignment_attempts,
    submission_files,
    validate_file_url,
)


def _install_transport(monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx.Request]:
    requests: list[httpx.Request] = []
    original_client = httpx.AsyncClient

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    monkeypatch.setattr(
        moodle_adapter.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    return requests


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


def test_invalid_moodle_webservice_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"exception": "moodle_exception", "errorcode": "invalidtoken"},
        )

    requests = _install_transport(monkeypatch, reject)
    client = MoodleWebService(
        origin="https://moodle-b9.localhost:8443",
        token="synthetic-invalid-token",  # noqa: S106 -- deliberately invalid test token
    )

    with pytest.raises(MoodleAdapterError, match="moodle_service_rejected"):
        asyncio.run(client.call("core_webservice_get_site_info"))

    assert len(requests) == 1
    assert requests[0].url.host == "moodle-b9.localhost"


def test_moodle_service_disabled_function_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def site_info(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"functions": [{"name": "core_webservice_get_site_info"}]},
        )

    requests = _install_transport(monkeypatch, site_info)
    client = MoodleWebService(
        origin="https://moodle-b9.localhost:8443",
        token="synthetic-test-token",  # noqa: S106 -- synthetic test token
    )

    with pytest.raises(MoodleAdapterError, match="moodle_function_not_enabled"):
        asyncio.run(
            client.verify_functions(
                {"core_webservice_get_site_info", "mod_assign_get_submissions"}
            )
        )
    with pytest.raises(MoodleAdapterError, match="moodle_function_not_enabled"):
        asyncio.run(client.call("mod_assign_get_submissions", assignmentids=[1]))

    assert len(requests) == 1


@pytest.mark.parametrize(
    ("status", "headers", "body", "max_bytes", "error_code"),
    [
        (302, {"location": "https://foreign.example/file.pdf"}, b"", 1024, "moodle_file_download_failed"),
        (200, {"content-type": "text/html"}, b"<html>login</html>", 1024, "moodle_file_download_rejected"),
        (200, {"content-type": "application/octet-stream"}, b"too large", 4, "moodle_file_too_large"),
    ],
)
def test_file_download_rejects_redirect_mime_and_oversize(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    status: int,
    headers: dict[str, str],
    body: bytes,
    max_bytes: int,
    error_code: str,
) -> None:
    requests = _install_transport(
        monkeypatch,
        lambda _request: httpx.Response(status, headers=headers, content=body),
    )
    client = MoodleWebService(
        origin="https://moodle-b9.localhost:8443",
        token="synthetic-test-token",  # noqa: S106 -- synthetic test token
    )
    destination = tmp_path / "staged.pdf"

    with pytest.raises(MoodleAdapterError, match=error_code):
        asyncio.run(
            client.download_to_path(
                "https://moodle-b9.localhost:8443/webservice/pluginfile.php/9/assignsubmission_file/submission_files/4/final.pdf",
                destination,
                max_bytes=max_bytes,
                timeout_seconds=5,
            )
        )

    assert len(requests) == 1
    assert requests[0].url.host == "moodle-b9.localhost"
    assert requests[0].extensions.get("timeout") is not None


@pytest.mark.parametrize(
    ("metadata_size", "reported_mime", "expected_status"),
    [
        (None, "application/pdf", 409),
        ("match", "text/plain", 415),
    ],
)
def test_downloaded_source_size_and_mime_must_match_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    metadata_size: str | None,
    reported_mime: str,
    expected_status: int,
) -> None:
    payload = Path("fixtures/b9/moodle-final.pdf").read_bytes()
    expected_size = len(payload) if metadata_size == "match" else len(payload) + 1
    attempt = {
        "plugins": [
            {
                "type": "file",
                "fileareas": [
                    {
                        "area": "submission_files",
                        "files": [
                            {
                                "filename": "final.pdf",
                                "fileurl": "https://moodle-b9.localhost:8443/webservice/pluginfile.php/9/assignsubmission_file/submission_files/4/final.pdf",
                                "filesize": expected_size,
                                "mimetype": reported_mime,
                            }
                        ],
                    }
                ],
            }
        ]
    }

    class StubMoodleClient:
        async def download_to_path(
            self, _url: str, path: str, *, max_bytes: int, timeout_seconds: float
        ) -> tuple[int, str]:
            assert max_bytes >= len(payload)
            assert timeout_seconds > 0
            Path(path).write_bytes(payload)
            return len(payload), hashlib.sha256(payload).hexdigest()

    monkeypatch.setattr(integrations.tempfile, "tempdir", str(tmp_path))

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            integrations._download_validated_files(
                StubMoodleClient(),
                type("Platform", (), {"moodle_origin": "https://moodle-b9.localhost:8443"})(),
                attempt,
                Settings(),
            )
        )

    assert error.value.status_code == expected_status
    assert list(tmp_path.iterdir()) == []
