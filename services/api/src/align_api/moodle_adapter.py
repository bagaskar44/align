from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

import httpx
from pydantic import SecretStr

from align_api.config import Settings

READ_FUNCTIONS = frozenset(
    {
        "core_webservice_get_site_info",
        "mod_assign_get_assignments",
        "mod_assign_get_submissions",
        "core_enrol_get_enrolled_users",
        "core_group_get_course_groups",
        "core_group_get_group_members",
    }
)


class MoodleAdapterError(Exception):
    """Safe Moodle adapter failure; message is a non-secret status code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def configured_moodle_token(settings: Settings) -> str:
    if settings.moodle_ws_token_file is not None:
        try:
            token = settings.moodle_ws_token_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise MoodleAdapterError("moodle_web_service_not_configured") from exc
        if token:
            return token
    if isinstance(settings.moodle_ws_token, SecretStr):
        token = settings.moodle_ws_token.get_secret_value()
        if token:
            return token
    raise MoodleAdapterError("moodle_web_service_not_configured")


def validate_file_url(file_url: str, moodle_origin: str) -> str:
    parsed = urlsplit(file_url)
    origin = urlsplit(moodle_origin)
    try:
        decoded_path = unquote(parsed.path, errors="strict")
    except UnicodeDecodeError as exc:
        raise MoodleAdapterError("unsupported_moodle_file_url") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.hostname.lower() != (origin.hostname or "").lower()
        or parsed.port != origin.port
        or not decoded_path.startswith("/webservice/pluginfile.php/")
        or "/assignsubmission_file/submission_files/" not in decoded_path
        or re.search(r"%25", parsed.path, re.IGNORECASE) is not None
        or re.search(r"%(?:2f|5c)", parsed.path, re.IGNORECASE) is not None
        or "\\" in decoded_path
        or "\x00" in decoded_path
    ):
        raise MoodleAdapterError("unsupported_moodle_file_url")
    segments = decoded_path.split("/")
    if any(segment in {".", ".."} for segment in segments):
        raise MoodleAdapterError("unsupported_moodle_file_url")
    query = (
        parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True) if parsed.query else []
    )
    if any(key != "forcedownload" or value not in {"0", "1"} for key, value in query):
        raise MoodleAdapterError("unsupported_moodle_file_url")
    if len({key for key, _value in query}) != len(query):
        raise MoodleAdapterError("unsupported_moodle_file_url")
    return file_url


class MoodleWebService:
    def __init__(
        self,
        *,
        origin: str,
        token: str,
        timeout_seconds: int = 120,
        max_response_bytes: int = 1_048_576,
    ) -> None:
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "https"
            or parsed.hostname is None
            or parsed.username
            or parsed.password
        ):
            raise MoodleAdapterError("invalid_moodle_origin")
        self.origin = f"https://{parsed.hostname.lower()}" + (
            f":{parsed.port}" if parsed.port else ""
        )
        self.token = token
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.endpoint = self.origin + "/webservice/rest/server.php"
        self.enabled_functions: frozenset[str] | None = None
        self.deadline = time.monotonic() + timeout_seconds

    def _request_timeout(self, requested: float | None = None) -> httpx.Timeout:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise MoodleAdapterError("moodle_operation_timeout")
        total = min(remaining, float(self.timeout_seconds))
        if requested is not None:
            total = min(total, requested)
        return httpx.Timeout(total, connect=min(5.0, total))

    async def _read_limited(self, response: httpx.Response, limit: int) -> bytes:
        if response.status_code != 200:
            raise MoodleAdapterError("moodle_service_unavailable")
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > limit:
                raise MoodleAdapterError("moodle_response_too_large")
        return bytes(body)

    async def call(self, function: str, **parameters: Any) -> Any:
        if function not in READ_FUNCTIONS:
            raise MoodleAdapterError("moodle_function_not_allowlisted")
        if self.enabled_functions is not None and function not in self.enabled_functions:
            raise MoodleAdapterError("moodle_function_not_enabled")
        payload: dict[str, str] = {
            "wstoken": self.token,
            "wsfunction": function,
            "moodlewsrestformat": "json",
        }
        for key, value in parameters.items():
            if isinstance(value, list):
                for index, item in enumerate(value):
                    payload[f"{key}[{index}]"] = str(item)
            elif value is not None:
                payload[key] = str(value)
        try:
            async with asyncio.timeout_at(self.deadline):
                timeout = self._request_timeout()
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                    async with client.stream(
                        "POST",
                        self.endpoint,
                        data=payload,
                        headers={"Accept": "application/json"},
                    ) as response:
                        body = await self._read_limited(response, self.max_response_bytes)
            value = json.loads(body)
        except MoodleAdapterError:
            raise
        except TimeoutError as exc:
            raise MoodleAdapterError("moodle_operation_timeout") from exc
        except (httpx.HTTPError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise MoodleAdapterError("moodle_service_unavailable") from exc
        if isinstance(value, dict) and ("exception" in value or "errorcode" in value):
            raise MoodleAdapterError("moodle_service_rejected")
        return value

    async def verify_functions(self, required: set[str]) -> frozenset[str]:
        if not required <= READ_FUNCTIONS:
            raise MoodleAdapterError("moodle_function_not_allowlisted")
        response = await self.call("core_webservice_get_site_info")
        functions = response.get("functions") if isinstance(response, dict) else None
        if not isinstance(functions, list):
            raise MoodleAdapterError("moodle_service_functions_unavailable")
        enabled = frozenset(
            item["name"]
            for item in functions
            if isinstance(item, dict)
            and isinstance(item.get("name"), str)
            and item["name"] in READ_FUNCTIONS
        )
        self.enabled_functions = enabled
        if not required <= enabled:
            raise MoodleAdapterError("moodle_function_not_enabled")
        return enabled

    async def download_to_path(
        self, file_url: str, path: str | Path, *, max_bytes: int, timeout_seconds: float
    ) -> tuple[int, str]:
        trusted_url = validate_file_url(file_url, self.origin)
        digest = hashlib.sha256()
        size = 0
        try:
            operation_deadline = min(self.deadline, time.monotonic() + timeout_seconds)
            async with asyncio.timeout_at(operation_deadline):
                timeout = self._request_timeout(timeout_seconds)
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                    async with client.stream(
                        "POST",
                        trusted_url,
                        data={"token": self.token},
                        headers={"Accept": "application/octet-stream"},
                    ) as response:
                        if response.status_code != 200:
                            raise MoodleAdapterError("moodle_file_download_failed")
                        content_type = (
                            response.headers.get("content-type", "").split(";", 1)[0].lower()
                        )
                        if content_type in {
                            "application/json",
                            "text/html",
                            "application/xhtml+xml",
                        }:
                            raise MoodleAdapterError("moodle_file_download_rejected")
                        with Path(path).open("wb") as target:
                            async for chunk in response.aiter_bytes():
                                size += len(chunk)
                                if size > max_bytes:
                                    raise MoodleAdapterError("moodle_file_too_large")
                                digest.update(chunk)
                                target.write(chunk)
            if size == 0:
                raise MoodleAdapterError("moodle_file_empty")
            return size, digest.hexdigest()
        except MoodleAdapterError:
            raise
        except TimeoutError as exc:
            raise MoodleAdapterError("moodle_operation_timeout") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise MoodleAdapterError("moodle_file_download_failed") from exc


def assignment_attempts(payload: Any, assignment_id: int) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("assignments"), list):
        raise MoodleAdapterError("moodle_submission_response_invalid")
    found: list[dict[str, Any]] = []
    for assignment in payload["assignments"]:
        if not isinstance(assignment, dict) or assignment.get("assignmentid") != assignment_id:
            continue
        submissions = assignment.get("submissions", [])
        if not isinstance(submissions, list):
            raise MoodleAdapterError("moodle_submission_response_invalid")
        for attempt in submissions:
            if not isinstance(attempt, dict):
                raise MoodleAdapterError("moodle_submission_response_invalid")
            if attempt.get("status") != "submitted":
                continue
            remote_id = attempt.get("id")
            attempt_no = attempt.get("attemptnumber")
            if type(remote_id) is not int or type(attempt_no) is not int or attempt_no < 0:
                raise MoodleAdapterError("moodle_submission_response_invalid")
            found.append({**attempt, "attemptid": f"{remote_id}:{attempt_no}"})
    return found


def submission_files(attempt: dict[str, Any]) -> list[dict[str, Any]]:
    plugins = attempt.get("plugins")
    if not isinstance(plugins, list):
        raise MoodleAdapterError("unsupported_moodle_submission_source")
    files: list[dict[str, Any]] = []
    for plugin in plugins:
        if not isinstance(plugin, dict):
            continue
        plugin_type = plugin.get("type")
        if plugin_type == "onlinetext":
            content = plugin.get("editorfields") or plugin.get("text") or plugin.get("content")
            if content:
                raise MoodleAdapterError("unsupported_moodle_submission_source")
            continue
        if plugin_type != "file":
            continue
        areas = plugin.get("fileareas", [])
        if not isinstance(areas, list):
            continue
        for area in areas:
            if not isinstance(area, dict) or area.get("area") != "submission_files":
                continue
            entries = area.get("files", [])
            if not isinstance(entries, list):
                raise MoodleAdapterError("moodle_submission_response_invalid")
            for item in entries:
                if not isinstance(item, dict):
                    raise MoodleAdapterError("moodle_submission_response_invalid")
                name = item.get("filename")
                url = item.get("fileurl")
                size = item.get("filesize")
                if (
                    not isinstance(name, str)
                    or not name
                    or not isinstance(url, str)
                    or type(size) is not int
                    or size <= 0
                ):
                    raise MoodleAdapterError("moodle_submission_response_invalid")
                files.append(item)
    if not files:
        raise MoodleAdapterError("unsupported_moodle_submission_source")
    return files
